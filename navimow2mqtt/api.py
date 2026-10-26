"""REST client for the Navimow open API (endpoints as used by navimow-sdk)."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from . import datalog
from .auth import AuthRequired, TokenManager
from .config import NavimowConfig
from .models import Device

log = logging.getLogger(__name__)

# command name -> (cloud command, params)
COMMANDS: Dict[str, tuple] = {
    "start": ("action.devices.commands.StartStop", {"on": True}),
    "stop": ("action.devices.commands.StartStop", {"on": False}),
    "pause": ("action.devices.commands.PauseUnpause", {"on": False}),
    "resume": ("action.devices.commands.PauseUnpause", {"on": True}),
    "dock": ("action.devices.commands.Dock", None),
}


class ApiError(Exception):
    pass


class NavimowApi:
    def __init__(self, cfg: NavimowConfig, tokens: TokenManager,
                 session: requests.Session | None = None):
        self.cfg = cfg
        self.tokens = tokens
        self.session = session or requests.Session()

    def _request(self, method: str, endpoint: str, body: Optional[dict] = None) -> Dict[str, Any]:
        url = "%s/%s" % (self.cfg.api_url, endpoint.lstrip("/"))
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer %s" % self.tokens.access_token(force_refresh=attempt == 1),
                       "requestId": str(uuid.uuid4())}
            try:
                resp = self.session.request(method, url, json=body, headers=headers, timeout=20)
            except requests.RequestException as exc:
                datalog.log("api_error", endpoint=endpoint, type=type(exc).__name__, error=str(exc)[:200])
                raise ApiError("request failed: %s" % exc) from exc
            if resp.status_code in (401, 403):
                datalog.log("api_auth_rejected", endpoint=endpoint, status=resp.status_code, attempt=attempt)
                if attempt == 0:
                    continue  # token may have been revoked early: refresh once and retry
                raise AuthRequired("API rejected the access token (HTTP %d)" % resp.status_code)
            if resp.status_code >= 400:
                datalog.log("api_error", endpoint=endpoint, status=resp.status_code, body=resp.text[:200])
                raise ApiError("HTTP %d: %s" % (resp.status_code, resp.text[:200]))
            try:
                data = resp.json()
            except ValueError as exc:
                raise ApiError("response is not JSON") from exc
            if data.get("code") != 1:
                datalog.log("api_error", endpoint=endpoint, code=data.get("code"), desc=data.get("desc"))
                raise ApiError("API error: %s" % data.get("desc"))
            return data.get("data") or {}
        raise ApiError("unreachable")  # pragma: no cover

    def devices(self) -> List[Device]:
        data = self._request("GET", "/openapi/smarthome/authList")
        return [Device.from_dict(d) for d in (data.get("payload") or {}).get("devices", [])]

    def statuses(self, device_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not device_ids:
            return {}
        data = self._request("POST", "/openapi/smarthome/getVehicleStatus",
                             {"devices": [{"id": i} for i in device_ids]})
        out = {}
        for d in (data.get("payload") or {}).get("devices", []):
            if d.get("id"):
                out[str(d["id"])] = d
        return out

    def mqtt_info(self) -> Dict[str, Any]:
        return self._request("GET", "/openapi/mqtt/userInfo/get/v2")

    def send_command(self, device_id: str, command: str) -> None:
        if command not in COMMANDS:
            raise ApiError("unknown command %r (choose from %s)" % (command, ", ".join(COMMANDS)))
        name, params = COMMANDS[command]
        execution: Dict[str, Any] = {"command": name}
        if params is not None:
            execution["params"] = params
        data = self._request("POST", "/openapi/smarthome/sendCommands", {
            "commands": [{"devices": [{"id": device_id}], "execution": execution}]})
        for res in (data.get("payload") or {}).get("commands", []):
            if res.get("status") == "ERROR":
                code = res.get("errorCode") or "COMMAND_FAILED"
                if code == "alreadyInState":
                    continue
                raise ApiError("command %s failed: %s" % (command, code))


def mqtt_connection(info: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the userInfo response into websocket connection parameters.

    Same rules as the official integration: port 443, TLS, path from mqttUrl.
    """
    host = info.get("mqttHost") or ""
    url = info.get("mqttUrl") or ""
    port = 443
    path = url or "/"
    parsed = urlparse(url)
    if parsed.scheme in ("ws", "wss") and parsed.hostname:
        host = host or parsed.hostname
        port = parsed.port or port
        path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    host = urlparse(host).hostname or host
    return {"host": host, "port": port, "path": path,
            "username": info.get("userName"), "password": info.get("pwdInfo")}
