"""OAuth2 token handling for the Navimow cloud.

Flow (same as the official Home Assistant integration):
  1. `python -m navimow2mqtt login` prints the authorize URL; you sign in in a browser
     and paste the URL you are redirected to (it contains ?code=...).
  2. The code is exchanged at the token endpoint and stored in the token file.
  3. While running, the access token (valid ~1 h) is refreshed with the refresh_token
     *if the server issued one*. Without a usable refresh_token the bridge reports
     `auth required`; run `login` again.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import requests

from . import datalog
from .config import NavimowConfig
from .i18n import t

log = logging.getLogger(__name__)

REFRESH_MARGIN = 300       # refresh this many seconds before expiry
DEFAULT_LIFETIME = 3600    # assumed when the server does not say (measured live by NaviWatch: ~1 h)


class AuthRequired(Exception):
    """No usable token; the user has to run `login`."""


class AuthError(Exception):
    pass


def build_authorize_url(cfg: NavimowConfig, state: str | None = None,
                        redirect_uri: str | None = None) -> str:
    parsed = urlparse(cfg.authorize_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri or cfg.redirect_uri,
        "state": state or secrets.token_urlsafe(16),
    })
    query.setdefault("channel", "homeassistant")
    return urlunparse(parsed._replace(query=urlencode(query)))


def _find_param(text: str, name: str) -> str:
    # my.home-assistant.io nests the real query inside ?redirect=oauth%2F%3Fcode%3D...,
    # so URL-decode a few times until a plain `name=` shows up.
    candidate = text
    for _ in range(4):
        m = re.search(r"[?&#]%s=([^&#\s]+)" % re.escape(name), candidate)
        if m:
            return m.group(1)
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return ""


def extract_code(text: str) -> str:
    """Accept either the full redirect URL or just the code."""
    text = text.strip()
    code = _find_param(text, "code")
    if code:
        return code
    if text.lower().startswith(("http://", "https://")):
        return ""   # a URL without a code: do not send it to the server as one
    return text


def extract_state(text: str) -> str:
    return _find_param(text.strip(), "state")


class LoginService:
    """Hands out login URLs and completes a login from a pasted URL/code or a callback.

    Used by the web page and by the MQTT login topic. Remembers the `state` values it
    issued and refuses a code that comes with a different one (CSRF protection).
    """

    STATE_TTL = 6 * 3600.0   # a login link published on MQTT may stay on display for a while
    MAX_STATES = 20

    def __init__(self, cfg: NavimowConfig, tokens: "TokenManager"):
        self.cfg = cfg
        self.tokens = tokens
        # state -> (issued at, redirect_uri it was issued with; the token exchange must repeat it)
        self._states: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def new_url(self, redirect_uri: str | None = None) -> str:
        state = secrets.token_urlsafe(16)
        with self._lock:
            now = time.time()
            self._states = {s: v for s, v in self._states.items() if now - v[0] < self.STATE_TTL}
            while len(self._states) >= self.MAX_STATES:
                self._states.pop(min(self._states, key=lambda s: self._states[s][0]))
            self._states[state] = (now, redirect_uri)
        return build_authorize_url(self.cfg, state, redirect_uri)

    @staticmethod
    def _describe(text: str) -> str:
        """Where the pasted address points (host and path only, never the query)."""
        text = text.strip()
        if not text.lower().startswith(("http://", "https://")):
            return t("msg.not_address")
        p = urlparse(text)
        return "%s%s" % (p.netloc, p.path)

    def submit(self, text: str, state: str | None = None, redirect_uri: str | None = None) -> tuple:
        """Returns (ok, message). The message never contains token values.

        `redirect_uri` is the address the code arrived on; it is used for the exchange when the
        state does not tell which one the login was started with (e.g. a bookmarked link)."""
        code = extract_code(text)
        if not code:
            return False, t("msg.no_code", where=self._describe(text))
        state = state if state is not None else extract_state(text)
        if state.lower() in ("null", "undefined", "none"):
            state = ""      # Navimow echoes a literal "null" when the link had no state
        with self._lock:
            now = time.time()
            if state:
                issued = self._states.pop(state, None)
                if issued is None or now - issued[0] >= self.STATE_TTL:
                    return False, t("msg.bad_state")
                redirect_uri = issued[1]
        try:
            token = self.tokens.exchange_code(code, redirect_uri)
        except AuthError as exc:
            return False, t("msg.rejected", err=exc)
        except OSError as exc:
            return False, t("msg.unreachable", err=exc)
        extra = "" if token.get("refresh_token") else t("msg.ok_norefresh")
        return True, t("msg.ok") + extra


def _unwrap(body: Dict[str, Any]) -> Dict[str, Any]:
    """Token responses are either flat (OAuth standard) or wrapped in {"data": {...}}."""
    if isinstance(body.get("data"), dict) and "access_token" in body["data"]:
        return body["data"]
    return body


def _with_expiry(tok: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = time.time()
    out = dict(tok)
    if previous and "refresh_token" not in out and previous.get("refresh_token"):
        out["refresh_token"] = previous["refresh_token"]   # servers may omit it on refresh
    try:
        lifetime = float(out.get("expires_in"))
        out["expires_at"] = now + lifetime
        out["expires_assumed"] = False
    except (TypeError, ValueError):
        out["expires_at"] = now + DEFAULT_LIFETIME
        out["expires_assumed"] = True
    out["obtained_at"] = now
    return out


class TokenManager:
    """Loads/saves the token file and keeps the access token fresh. Thread-safe."""

    def __init__(self, cfg: NavimowConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._token: Optional[Dict[str, Any]] = None
        self._file_mtime = 0.0

    # -- storage -------------------------------------------------------------
    def _load(self) -> Optional[Dict[str, Any]]:
        try:
            mtime = os.path.getmtime(self.cfg.token_file)
            if self._token is not None and mtime == self._file_mtime:
                return self._token
            with open(self.cfg.token_file, "r", encoding="utf-8") as fh:
                self._token = json.load(fh)
            self._file_mtime = mtime
        except (OSError, ValueError):
            return self._token
        return self._token

    def save(self, token: Dict[str, Any]) -> None:
        path = self.cfg.token_file
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(token, fh)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        self._token = token
        try:
            self._file_mtime = os.path.getmtime(path)
        except OSError:
            pass

    # -- OAuth calls ---------------------------------------------------------
    def _post_token(self, data: Dict[str, str]) -> Dict[str, Any]:
        data = dict(data, client_id=self.cfg.client_id, client_secret=self.cfg.client_secret)
        try:
            resp = self.session.post(self.cfg.token_url, data=data, timeout=20)
        except requests.RequestException as exc:
            raise OSError("token endpoint unreachable: %s" % exc) from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code in (400, 401, 403):
            raise AuthError("token endpoint refused the request (HTTP %d)" % resp.status_code)
        if resp.status_code >= 400:
            raise OSError("token endpoint HTTP %d" % resp.status_code)
        tok = _unwrap(body if isinstance(body, dict) else {})
        if not tok.get("access_token"):
            raise AuthError("token response has no access_token (fields: %s)"
                            % ", ".join(sorted(body)) if isinstance(body, dict) else "?")
        return tok

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> Dict[str, Any]:
        tok = self._post_token({"grant_type": "authorization_code", "code": code,
                                "redirect_uri": redirect_uri or self.cfg.redirect_uri})
        token = _with_expiry(tok)
        self.save(token)
        # never the token values, only facts about them
        datalog.log("login", has_refresh_token=bool(token.get("refresh_token")),
                    expires_in=token.get("expires_in"), fields=sorted(tok))
        return token

    def _refresh(self, token: Dict[str, Any]) -> Dict[str, Any]:
        if not token.get("refresh_token"):
            datalog.log("token_refresh_failed", reason="no refresh token")
            raise AuthRequired("access token expired and the server issued no refresh token")
        age = round(time.time() - token.get("obtained_at", time.time()), 1)
        try:
            new = self._post_token({"grant_type": "refresh_token",
                                    "refresh_token": token["refresh_token"]})
        except AuthError as exc:
            datalog.log("token_refresh_failed", reason="rejected", error=str(exc)[:200],
                        token_age_seconds=age)
            raise AuthRequired("refresh rejected: %s" % exc) from exc
        except OSError as exc:
            datalog.log("token_refresh_failed", reason="network", error=str(exc)[:200],
                        token_age_seconds=age)
            raise
        refreshed = _with_expiry(new, token)
        self.save(refreshed)
        log.info("access token refreshed")
        datalog.log("token_refresh", token_age_seconds=age, expires_in=new.get("expires_in"),
                    new_refresh_token="refresh_token" in new)
        return refreshed

    # -- public --------------------------------------------------------------
    def access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            token = self._load()
            if not token or not token.get("access_token"):
                raise AuthRequired("no token yet; run: python -m navimow2mqtt login")
            if force_refresh or time.time() >= token.get("expires_at", 0) - REFRESH_MARGIN:
                try:
                    token = self._refresh(token)
                except AuthRequired:
                    # `expires_at` may be only an estimate: use the token until the API says no
                    if force_refresh or not token.get("expires_assumed"):
                        raise
            return token["access_token"]

    def status(self) -> Dict[str, Any]:
        """Token state for display; never includes token values."""
        token = self._load()
        if not token or not token.get("access_token"):
            return {"logged_in": False, "has_refresh_token": False, "expires_in": None}
        return {"logged_in": True, "has_refresh_token": bool(token.get("refresh_token")),
                "expires_in": int(token.get("expires_at", 0) - time.time())}

    def has_refresh_token(self) -> bool:
        token = self._load()
        return bool(token and token.get("refresh_token"))
