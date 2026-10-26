"""Cloud MQTT subscription (websocket + TLS) with credential re-fetch on every reconnect.

The broker credentials are bound to the OAuth token, so after a disconnect the
client is rebuilt with fresh credentials from the API instead of reusing old ones.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Callable, Dict, List

import paho.mqtt.client as mqtt

from . import datalog
from .api import NavimowApi, mqtt_connection
from .auth import AuthRequired

log = logging.getLogger(__name__)

# `location` is not part of the official SDK; it carries position/zone/progress as a JSON
# array on LiDAR models (found by NaviWatch). Other models may never send anything on it.
CHANNELS = ("state", "event", "attributes", "location")

# The userInfo endpoint answers "Request too frequent. Please retry after 1 minute."
# when hammered, so never call it more often than this (value used by NaviWatch).
CRED_COOLDOWN = 65.0


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def parse_topic(topic: str):
    """'/downlink/vehicle/<id>/realtimeDate/<channel>' -> (device_id, channel) or None."""
    parts = topic.split("/")
    if parts and parts[0] == "":
        parts = parts[1:]
    if len(parts) != 5 or parts[0] != "downlink" or parts[1] != "vehicle" or parts[3] != "realtimeDate":
        return None
    return parts[2], parts[4]


class CloudMqtt(threading.Thread):
    def __init__(self, api: NavimowApi, device_ids: List[str], stop: threading.Event,
                 on_message: Callable[[str, str, dict], None],
                 on_auth_required: Callable[[str], None]):
        super().__init__(name="cloud-mqtt", daemon=True)
        self.api = api
        self.device_ids = device_ids
        self.stop_event = stop
        self.on_message = on_message          # (device_id, channel, payload)
        self.on_auth_required = on_auth_required
        self.connected = threading.Event()
        self._dropped = threading.Event()
        self._client = None
        self._last_cred_fetch = -CRED_COOLDOWN
        self._connected_at = None

    def force_reconnect(self) -> None:
        """Watchdog hook: drop the connection so the supervisor rebuilds it."""
        log.info("cloud MQTT: forced reconnect")
        datalog.log("cloud_force_reconnect")
        self._dropped.set()

    # -- paho callbacks ------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("cloud MQTT connect refused: %s", reason_code)
            datalog.log("cloud_connect_refused", reason=str(reason_code))
            self._dropped.set()
            return
        log.info("cloud MQTT connected")
        self._connected_at = time.monotonic()
        datalog.log("cloud_connect")
        for dev in self.device_ids:
            for ch in CHANNELS:
                client.subscribe("/downlink/vehicle/%s/realtimeDate/%s" % (dev, ch))
            # Diagnostic: shows (at DEBUG) any other topic the mower publishes. The broker
            # ACL may refuse it; that is harmless.
            client.subscribe("/downlink/vehicle/%s/#" % dev)
        self.connected.set()

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None):
        log.warning("cloud MQTT disconnected (%s)", reason_code)
        up = round(time.monotonic() - self._connected_at, 1) if self._connected_at else None
        self._connected_at = None
        datalog.log("cloud_disconnect", reason=str(reason_code), up_seconds=up)
        self.connected.clear()
        self._dropped.set()

    def _on_message(self, client, userdata, msg):
        parsed = parse_topic(msg.topic)
        if not parsed:
            log.debug("cloud: unrecognised topic %s: %s", msg.topic, msg.payload[:500])
            datalog.log("cloud_unknown_topic", topic=msg.topic,
                        payload=msg.payload[:500].decode("utf-8", errors="replace"))
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        # state/event/attributes are JSON objects, location is a JSON array
        if isinstance(payload, (dict, list)):
            log.debug("cloud %s %s: %s", parsed[1], parsed[0], payload)
            datalog.log("cloud_msg", device=parsed[0], channel=parsed[1], payload=payload)
            self.on_message(parsed[0], parsed[1], payload)

    # -- lifecycle -----------------------------------------------------------
    def _build(self, conn: Dict) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id="web_%s_%s" % (conn.get("username") or "unknown",
                                                      uuid.uuid4().hex[:10]),
                             transport="websockets")
        if conn.get("username") and conn.get("password"):
            client.username_pw_set(conn["username"], conn["password"])
        client.ws_set_options(path=conn["path"],
                              headers={"Authorization": "Bearer %s" % self.api.tokens.access_token()})
        client.tls_set()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def run(self) -> None:
        delay = 1.0
        while not self.stop_event.is_set():
            secrets_in_play: list = []   # redacted from anything we log
            try:
                wait = CRED_COOLDOWN - (time.monotonic() - self._last_cred_fetch)
                if wait > 0 and self.stop_event.wait(wait):
                    break
                self._last_cred_fetch = time.monotonic()
                conn = mqtt_connection(self.api.mqtt_info())
                secrets_in_play = [conn.get("path"), conn.get("username"), conn.get("password")]
                if not conn["host"]:
                    raise RuntimeError("API returned no MQTT host")
                # host and port only: path, user name and password are credentials
                datalog.log("cloud_creds", host=conn["host"], port=conn["port"],
                            has_user=bool(conn.get("username")))
                self._dropped.clear()
                self._client = self._build(conn)
                self._client.connect(conn["host"], conn["port"], keepalive=120)
                self._client.loop_start()
                if self.connected.wait(20):
                    delay = 1.0
                while not self.stop_event.is_set() and not self._dropped.wait(1.0):
                    pass
            except AuthRequired as exc:
                datalog.log("auth_required", source="cloud", reason=str(exc)[:200])
                self.on_auth_required(str(exc))
                delay = 60.0
            except Exception as exc:  # noqa: BLE001 - keep the supervisor alive
                log.warning("cloud MQTT error: %s", exc)
                datalog.log("cloud_error", type=type(exc).__name__,
                            error=redact(str(exc), *secrets_in_play)[:200])
            finally:
                self._teardown()
            self.stop_event.wait(delay)
            delay = min(delay * 2, 60.0)

    def _teardown(self) -> None:
        client, self._client = self._client, None
        self.connected.clear()
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
