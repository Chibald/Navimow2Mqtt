"""Small login page, so you never have to open a terminal or read container logs.

    GET  /          status + two ways to sign in
    POST /login     option 2: paste the address you were sent to after signing in
    GET  /callback  option 1: the browser comes back here with ?code=&state= (Navimow accepts a
                    redirect address of our choosing; the exchange repeats the redirect address
                    the sign-in was started with)
    GET  /login     same as /callback, so a link with redirect_uri=http://<this host>/login works
    GET  /tile(.svg) the map tile: a lawn that gets mowed (see tileview.py); /map(.svg) is the same
    GET  /health    {"auth": "ok" | "required"}

There is no password on this page and it shows no token values. Publish the port on
your LAN only. Submitting a code only signs the bridge in to the account of whoever
completed the Navimow sign-in.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from urllib.parse import parse_qs, urlparse

from . import mapsweb, tileview
from .auth import LoginService, TokenManager
from .i18n import current, t
from .ui import CSS

log = logging.getLogger(__name__)

ZOOM_STATES = {"mowing", "paused", "returning", "error"}     # the mower is out of its dock: zoom in

PAGE = Template("""<!doctype html>
<html lang="$lang"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Navimow2Mqtt</title>
<style>
$css
</style></head><body>
<main class="card">
<header>
 <div><h1>Navimow2Mqtt</h1><p class="sub">$subtitle</p></div>
 <span class="pill $pill_class"><i></i>$pill_text</span>
</header>
$message
<h2>$headline</h2>
<p class="sub">$subline</p>
<div class="tiles">
 <div class="tile"><span>$tile_token_label</span><b>$tile_token</b></div>
 <div class="tile"><span>$tile_refresh_label</span><b>$tile_refresh</b></div>
</div>
<div class="actions">
 <a class="btn $primary_class" href="$sign_in_url" target="_blank" rel="noopener">$sign_in_label</a>
 <a class="btn" href="/tile">$map_label</a>
</div>
$maps_link
<details$open>
 <summary>$help_summary</summary>
 <div class="help">
 <p>$help_intro</p>
 <ol>
  <li>$help_step1</li>
  <li>$help_step2</li>
  <li>$help_step3</li>
 </ol>
 <form method="post" action="/login">
  <textarea name="text" placeholder="https://my.home-assistant.io/redirect/..." required></textarea>
  <button class="btn primary" type="submit">$submit_label</button>
 </form>
 </div>
</details>
</main></body></html>""")


def _minutes(seconds, lang=None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return t("val.renewing", lang)
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return t("val.min", lang, n=minutes)
    return t("val.hour_min", lang, h=minutes // 60, m=minutes % 60)


def render_login_page(status: dict, sign_in_url: str, manual_url: str, message: str = "",
                      ok: bool = True, lang: str | None = None, maps: bool = False) -> str:
    """The sign-in page. `status` is TokenManager.status(); `sign_in_url` is the automatic
    ("come back here") link, or the manual one when the address of this page is unknown.
    The texts follow `lang` (default: the configured language)."""
    logged_in = status["logged_in"]
    refresh = status["has_refresh_token"]
    esc = html.escape
    note = ""
    if message:
        note = '<p class="note %s">%s</p>' % ("ok" if ok else "bad", esc(message))
    elif logged_in and not refresh:
        note = '<p class="note warn">%s</p>' % esc(t("note.norefresh", lang))
    if logged_in:
        headline = t("head.in", lang)
        subline = t("sub.in.auto" if refresh else "sub.in.manual", lang)
        pill = ("ok", t("pill.in", lang))
        tiles = (_minutes(status["expires_in"], lang), t("val.auto" if refresh else "val.manual", lang))
    else:
        headline, subline = t("head.out", lang), t("sub.out", lang)
        pill = ("bad", t("pill.out", lang))
        tiles = ("—", "—")
    link = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(manual_url), esc(t("help.link", lang)))
    return PAGE.substitute(
        css=CSS, lang=lang or current(), subtitle=esc(t("page.subtitle", lang)),
        pill_class=pill[0], pill_text=esc(pill[1]), message=note, headline=esc(headline), subline=esc(subline),
        tile_token_label=esc(t("tile.token", lang)), tile_refresh_label=esc(t("tile.refresh", lang)),
        tile_token=esc(tiles[0]), tile_refresh=esc(tiles[1]),
        primary_class="" if logged_in else "primary",
        sign_in_label=esc(t("btn.signin_again" if logged_in else "btn.signin", lang)),
        sign_in_url=esc(sign_in_url), map_label=esc(t("btn.map", lang)),
        maps_link=('<p class="sub" style="text-align:center;margin-top:.9rem"><a href="/maps">%s</a></p>'
                   % esc(t("btn.maps", lang))) if maps else "",
        help_summary=esc(t("help.summary", lang)), help_intro=esc(t("help.intro", lang)),
        help_step1=t("help.step1", lang, link=link), help_step2=t("help.step2", lang),   # trusted constants
        help_step3=esc(t("help.step3", lang)), submit_label=esc(t("btn.submit", lang)),
        open=" open" if (message and not ok) else "")


def make_handler(login: LoginService, tokens: TokenManager):
    class Handler(BaseHTTPRequestHandler):
        server_version = "navimow2mqtt"

        def log_message(self, fmt, *args):  # never log the request line: /callback carries a code
            pass

        def _send(self, body, status: int = 200, ctype: str = "text/html; charset=utf-8", headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _page(self, message: str = "", ok: bool = True, status: int = 200):
            manual = login.new_url()
            # Where the browser can reach this page, for the "come back here" link. Only plain
            # host[:port] characters are accepted from the Host header.
            host = self.headers.get("Host", "")
            if re.fullmatch(r"[A-Za-z0-9.\-\[\]:]{1,100}", host):
                sign_in = login.new_url("http://%s/callback" % host)
            else:
                sign_in = manual
            self._send(render_login_page(tokens.status(), sign_in, manual, message, ok,
                                         maps=getattr(self.server, "maps", None) is not None), status)

        def _tile(self, url):
            """The map tile: the .svg routes are the bare image, for openHAB `Image`; the others are the same
            in a self-refreshing page with a transparent background.
            Query: device=<name> rotate=0|90|180|270 flip=x|y theme=light|dark w=<px> swath=<metres> refresh=<s>
            and, with a background picture: view=<name>|auto span=<metres> (0 = the whole picture) follow=robot|dock"""
            q = parse_qs(url.query)

            def one(key, default=""):
                return (q.get(key) or [default])[0]

            def num(key, default):
                try:
                    return int(one(key, str(default)))
                except ValueError:
                    return default

            try:
                swath = max(0.05, min(float(one("swath", "0.25")), 2.0))
            except ValueError:
                swath = 0.25
            provider = getattr(self.server, "map_provider", None)
            data = provider(one("device") or None) if provider else None
            background = None
            store = getattr(self.server, "maps", None)
            view = one("view")
            if store is not None and view:
                name = store.pick((data or {}).get("zone")) if view == "auto" else view
                background = store.background(name) if name else None     # unknown or uncalibrated: plain tile
            def metres(key):
                try:
                    return max(0.0, min(float(one(key)), 500.0))
                except ValueError:
                    return None
            span = metres("span") or 0.0
            # idle_span: the window while the mower is standing still; 0 = the whole picture. So
            # span=12&idle_span=0 shows everything at rest and zooms in as soon as it drives.
            idle = metres("idle_span") if "idle_span" in q else None
            driving = (data or {}).get("state") in ZOOM_STATES
            if idle is not None and not driving:
                span = idle
            fixed = idle is not None and one("fixed").lower() not in ("0", "false", "no", "off")
            svg = tileview.render_tile(data, rotate=num("rotate", 0), flip=one("flip") or None,
                                       theme=one("theme") or None, width=num("w", 640), swath_m=swath,
                                       background=background, span=span, fixed_size=fixed,
                                       follow="dock" if one("follow") == "dock" else "robot")
            if url.path.endswith(".svg"):
                self._send(svg, ctype="image/svg+xml; charset=utf-8")
            else:
                self._send(tileview.tile_page(svg, refresh=max(2, num("refresh", 5))))

        def do_GET(self):
            url = urlparse(self.path)
            if mapsweb.handle_get(self, url, getattr(self.server, "maps", None),
                                  getattr(self.server, "map_provider", None)):
                return
            if url.path == "/health":
                self._send(json.dumps({"auth": "ok" if tokens.status()["logged_in"] else "required"}),
                           ctype="application/json")
            elif url.path in ("/callback", "/login"):
                # The browser comes back here after the Navimow sign-in (Navimow accepts a redirect
                # address of our choosing; verified with http://localhost/callback).
                q = parse_qs(url.query)
                code = (q.get("code") or [""])[0]
                if not code:
                    err = (q.get("error_description") or q.get("error") or [""])[0]
                    if err:
                        return self._page(t("msg.navimow_reported", err=err[:200]), ok=False, status=400)
                    if url.path == "/login":
                        return self._page()                  # someone just browsed to /login
                    return self._page(t("msg.no_code_received"), ok=False, status=400)
                host = self.headers.get("Host", "")
                back = "http://%s%s" % (host, url.path) if re.fullmatch(r"[A-Za-z0-9.\-\[\]:]{1,100}", host) else None
                ok, msg = login.submit(code, state=(q.get("state") or [""])[0], redirect_uri=back)
                self._page(msg, ok=ok, status=200 if ok else 400)
            elif url.path in ("/tile", "/tile.svg", "/map", "/map.svg"):
                self._tile(url)
            elif url.path == "/":
                self._page()
            else:
                self._send("not found", 404, "text/plain; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            if mapsweb.handle_post(self, url, getattr(self.server, "maps", None)):
                return
            if url.path != "/login":
                return self._send("not found", 404, "text/plain; charset=utf-8")
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 8192)
            except ValueError:
                length = 0
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            ok, msg = login.submit((form.get("text") or [""])[0])
            log.info("web login: %s", "ok" if ok else "failed")
            self._page(msg, ok=ok, status=200 if ok else 400)

    return Handler


def start(port: int, login: LoginService, tokens: TokenManager, maps=None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(login, tokens))
    server.maps = maps             # the MapStore with the background pictures, or None
    server.map_provider = None     # set by main once the bridge exists: callable(device_name | None) -> dict
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    log.info("login page on port %d", port)
    return server
