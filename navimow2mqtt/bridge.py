"""Bridge: cloud (MQTT push + HTTP fallback) <-> local MQTT broker."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt

from . import datalog, ha, rebuild
from .api import COMMANDS, ApiError, NavimowApi
from .auth import AuthRequired, LoginService
from .cloud import CloudMqtt
from .config import Config
from .models import AT_DOCK, DRIVING, Device, Location, MowerState, Trail
from .stats import StatsTracker

log = logging.getLogger(__name__)

WATCHDOG_DEBOUNCE = 300.0   # seconds between forced MQTT reconnects


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def device_names(devices: List[Device]) -> Dict[str, str]:
    """device id -> unique topic-safe name."""
    out: Dict[str, str] = {}
    used = set()
    for d in devices:
        name = slug(d.name) or slug(d.id) or "mower"
        if name in used:
            name = "%s_%s" % (name, slug(d.id)[-6:])
        used.add(name)
        out[d.id] = name
    return out


TRAIL_SAVE_EVERY = 5.0      # seconds between writes of the trail file while the mower moves
PATH_PUBLISH_POINTS = 500   # at most this many points go into the retained MQTT `path` message


def load_trails(path: str) -> Dict[str, dict]:
    """The last path of every mower, so a restart does not lose it: {name: {"points": [...], "started": iso}}."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        trails = data.get("trails") if isinstance(data, dict) and data.get("version") == 1 else None
        return trails if isinstance(trails, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("cannot read %s (%s); starting without a saved path", path, exc)
        return {}


def save_trails(path: str, trails: Dict[str, Trail]) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "trails": {n: {"points": t.points, "started": t.started}
                                                for n, t in trails.items()}}, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as exc:      # a full disk must not take the bridge down
        log.warning("cannot write %s: %s", path, exc)


class MowerRuntime:
    def __init__(self, dev: Device, name: str, path_max_points: int = 8000):
        self.dev = dev
        self.name = name
        self.state: Optional[MowerState] = None
        self.last_push = 0.0       # last cloud MQTT state
        self.last_data = 0.0       # last data from any source
        self.last_http = 0.0
        self.availability: Optional[str] = None
        self.last_error: Optional[str] = None
        self.location_seen = False
        self.location = Location()
        self.trail = Trail(path_max_points)


class Bridge:
    def __init__(self, config: Config, api: NavimowApi, devices: List[Device],
                 login: Optional[LoginService] = None, maps=None):
        self.config = config
        self.maps = maps             # MapStore with the background pictures (it learns the zone ids)
        self.api = api
        self.login = login
        self.login_url: Optional[str] = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        names = device_names(devices)
        self.mowers: Dict[str, MowerRuntime] = {
            d.id: MowerRuntime(d, names[d.id], config.path_max_points) for d in devices}
        self.by_name = {m.name: m for m in self.mowers.values()}
        self._trail_saved = 0.0
        for name, rec in load_trails(config.trail_file).items():
            m = self.by_name.get(name)
            if m and isinstance(rec, dict) and config.path_max_points > 0:
                try:
                    m.trail.points = [[float(x), float(y)] for x, y in rec.get("points", [])][-config.path_max_points:]
                    m.trail.started = rec.get("started")
                except (TypeError, ValueError):
                    m.trail.points = []
        self.auth_state: Optional[str] = None
        self.last_watchdog = -WATCHDOG_DEBOUNCE
        self.stats: Optional[StatsTracker] = None
        if config.stats_file:
            from zoneinfo import ZoneInfo
            self.stats = StatsTracker(config.stats_file, ZoneInfo(config.timezone), config.stats_weeks,
                                      config.stats_min_session_s)

        cfg = config.mqtt
        self.base = cfg.base_topic
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.client_id)
        if cfg.username:
            self.client.username_pw_set(cfg.username, cfg.password)
        self.bridge_topic = ha.topics(self.base, "bridge")["bridge"]
        self.client.will_set(self.bridge_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_command
        self.cloud = CloudMqtt(api, list(self.mowers), self.stop, self.on_cloud_message,
                               self.on_auth_required)

    # -- local broker --------------------------------------------------------
    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, qos=0, retain=retain)

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None):
        log.warning("MQTT disconnected, will retry")
        datalog.log("broker_disconnect", reason=str(reason_code))

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT connect failed: %s", reason_code)
            datalog.log("broker_connect_failed", reason=str(reason_code))
            return
        log.info("connected to MQTT broker %s:%d", self.config.mqtt.host, self.config.mqtt.port)
        datalog.log("broker_connect")
        self.publish(self.bridge_topic, "online", retain=True)
        for m in self.mowers.values():
            client.subscribe(ha.topics(self.base, m.name)["command"])
            self.publish_discovery(m)
            self.publish_info(m)
            if m.availability:
                self.publish_availability(m, m.availability, force=True)
            if m.state:
                self.publish_state(m)
            self.publish_stats(m)
        if self.config.mqtt.discovery:
            for topic, payload in ha.bridge_discovery(self.config.mqtt.discovery_prefix, self.base):
                self.publish(topic, payload, retain=True)
        if self.auth_state:
            self.publish(ha.topics(self.base, "bridge")["auth"], self.auth_state, retain=True)
        if self.config.web_port and self.config.public_url:
            # Where to send a user (e.g. from openHAB) to sign in; set via PUBLIC_URL.
            self.publish(ha.topics(self.base, "bridge")["web_url"], self.config.public_url, retain=True)
        if self.login:
            client.subscribe(ha.topics(self.base, "bridge")["login"])
            if self.login_url:
                self.publish(ha.topics(self.base, "bridge")["login_url"], self.login_url, retain=True)

    def handle_login(self, text: str) -> None:
        """Publish the redirect URL (or code) to <base>/bridge/login to sign in."""
        ok, msg = self.login.submit(text)
        log.info("MQTT login: %s", "ok" if ok else "failed")
        self.publish(ha.topics(self.base, "bridge")["login_result"], msg)
        if ok:
            self.set_auth("ok")

    def _on_command(self, client, userdata, msg):
        if self.login and msg.topic == ha.topics(self.base, "bridge")["login"]:
            text = msg.payload.decode("utf-8", errors="replace").strip()
            if text:
                threading.Thread(target=self.handle_login, args=(text,), daemon=True).start()
            return
        name = msg.topic.split("/")[-2] if msg.topic.count("/") >= 2 else ""
        m = self.by_name.get(name)
        if not m:
            return
        text = msg.payload.decode("utf-8", errors="replace").strip().lower()
        threading.Thread(target=self.handle_command, args=(m, text), daemon=True).start()

    def handle_command(self, m: MowerRuntime, text: str) -> None:
        # Home Assistant's "start" doubles as "resume" while paused.
        command = {"start_mowing": "start", "return_to_base": "dock"}.get(text, text)
        if command == "start" and m.state and m.state.state == "paused":
            command = "resume"
        if command not in COMMANDS:
            log.warning("%s: ignoring unknown command %r", m.name, text)
            datalog.log("command", device=m.name, command=text[:40], ok=False, error="unknown command")
            return
        try:
            self.api.send_command(m.dev.id, command)
            log.info("%s: command %s sent", m.name, command)
            datalog.log("command", device=m.name, command=command, ok=True,
                        state=m.state.state if m.state else None)
            m.last_http = 0.0   # poll the real state soon
        except AuthRequired as exc:
            datalog.log("command", device=m.name, command=command, ok=False, error="auth required")
            self.on_auth_required(str(exc))
        except ApiError as exc:
            log.warning("%s: command %s failed: %s", m.name, command, exc)
            datalog.log("command", device=m.name, command=command, ok=False, error=str(exc)[:200])
            self.publish(ha.topics(self.base, m.name)["event"],
                         json.dumps({"event": "command_failed", "command": command, "error": str(exc)}))

    # -- publishing ----------------------------------------------------------
    def publish_discovery(self, m: MowerRuntime) -> None:
        if not self.config.mqtt.discovery:
            return
        for topic, payload in ha.discovery_messages(self.config.mqtt.discovery_prefix, self.base,
                                                    m.name, m.dev, stats=self.stats is not None):
            self.publish(topic, payload, retain=True)

    def publish_stats(self, m: MowerRuntime) -> None:
        """Weekly totals (retained). Nothing is published when STATS_FILE is not set."""
        if self.stats:
            self.publish(ha.topics(self.base, m.name)["stats"],
                         json.dumps(self.stats.snapshot(m.name, time.time())), retain=True)

    def publish_info(self, m: MowerRuntime) -> None:
        self.publish(ha.topics(self.base, m.name)["info"], json.dumps({
            "id": m.dev.id, "name": m.dev.name, "model": m.dev.model,
            "firmware": m.dev.firmware, "serial": m.dev.serial, "raw": m.dev.raw}), retain=True)

    def publish_availability(self, m: MowerRuntime, value: str, force: bool = False) -> None:
        if m.availability == value and not force:
            return
        m.availability = value
        self.publish(ha.topics(self.base, m.name)["availability"], value, retain=True)

    def rebuild_trail(self, name: Optional[str] = None, since: Optional[str] = None,
                      until: Optional[str] = None) -> dict:
        """Load the path of the last mowing session from the event log into the running bridge.

        Used when the path in memory is incomplete (it was cut off, or the bridge was not running during
        the first part). Raises rebuild.RebuildError with a message for the user when that is not possible."""
        m = self.by_name.get(name) if name else next(iter(self.mowers.values()), None)
        if m is None:
            raise rebuild.RebuildError("There is no such mower.")
        if not self.config.log_dir:
            raise rebuild.RebuildError("The event log is switched off (LOG_DIR is empty).")
        if self.config.path_max_points <= 0:
            raise rebuild.RebuildError("The path is switched off (PATH_MAX_POINTS=0).")
        res = rebuild.rebuild(self.config.log_dir, m.name, since, until, device_id=m.dev.id,
                              max_points=self.config.path_max_points)
        with self.lock:
            m.trail.points = res["points"]
            m.trail.started = res["started"]
        self.persist_trails(force=True)
        self.publish_path(m)
        datalog.log("trail_rebuilt", device=m.name, points=len(res["points"]), total=res["total"],
                    started=res["started"], ended=res["ended"])
        return {"name": m.name, "points": len(res["points"]), "total": res["total"],
                "started": res["started"], "ended": res["ended"]}

    def map_data(self, name: Optional[str] = None) -> Optional[dict]:
        """Snapshot for the map drawing (web.py); `name` selects a mower, default the first."""
        m = self.by_name.get(name) if name else next(iter(self.mowers.values()), None)
        if m is None:
            return None
        with self.lock:
            d = dict(m.location.data)
            age = None
            if d.get("time") is not None:
                age = max(0, int(time.time() - d["time"] / 1000.0))
            return {"name": m.dev.name or m.name, "state": m.state.state if m.state else None,
                    "battery": m.state.battery if m.state else None,
                    "points": list(m.trail.points), "x": d.get("x"), "y": d.get("y"),
                    "course_deg": d.get("course_deg"), "progress_pct": d.get("progress_pct"),
                    "zone": d.get("zone"), "position_age_s": age}

    def persist_trails(self, force: bool = False) -> None:
        """Write the paths to the trail file (at most every few seconds unless forced)."""
        if not self.config.trail_file or self.config.path_max_points <= 0:
            return
        now = time.monotonic()
        if force or now - self._trail_saved >= TRAIL_SAVE_EVERY:
            self._trail_saved = now
            save_trails(self.config.trail_file, {m.name: m.trail for m in self.mowers.values()})

    def publish_path(self, m: MowerRuntime) -> None:
        if self.config.path_max_points <= 0:
            return
        self.persist_trails()
        body = m.trail.to_json(PATH_PUBLISH_POINTS)     # the MQTT message stays small; the tile uses all points
        body["last_update"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.publish(ha.topics(self.base, m.name)["path"], json.dumps(body), retain=True)

    def publish_state(self, m: MowerRuntime) -> None:
        body = m.state.to_json()
        body["last_update"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.publish(ha.topics(self.base, m.name)["state"], json.dumps(body), retain=True)

    def set_auth(self, value: str) -> None:
        if value != self.auth_state:
            datalog.log("auth_state", value=value, previous=self.auth_state)
            self.auth_state = value
            t = ha.topics(self.base, "bridge")
            self.publish(t["auth"], value, retain=True)
            if self.login:
                # While a login is needed, the link to open is available on MQTT too.
                self.login_url = self.login.new_url() if value == "required" else None
                self.publish(t["login_url"], self.login_url or "", retain=True)

    def on_auth_required(self, reason: str) -> None:
        if self.auth_state != "required":
            log.error("Navimow login required (%s). Run: python -m navimow2mqtt login", reason)
        self.set_auth("required")

    # -- incoming data -------------------------------------------------------
    def check_mismatch(self, m: MowerRuntime, new: MowerState) -> None:
        """Watchdog: the cloud MQTT can go silently dead (half-open connection) while the
        client still says "connected". If HTTP disagrees with the last pushed state,
        rebuild the MQTT connection (debounced, like NaviWatch)."""
        if not m.last_push or not m.state or new.state == m.state.state:
            return
        now = time.monotonic()
        if now - self.last_watchdog < WATCHDOG_DEBOUNCE:
            return
        self.last_watchdog = now
        log.warning("%s: HTTP says %s but last MQTT push said %s; reconnecting cloud MQTT",
                    m.name, new.state, m.state.state)
        datalog.log("watchdog_mismatch", device=m.name, http_state=new.state, mqtt_state=m.state.state,
                    seconds_since_push=round(now - m.last_push, 1))
        self.cloud.force_reconnect()

    def apply(self, m: MowerRuntime, payload: dict, from_push: bool) -> None:
        with self.lock:
            new = MowerState.from_payload(payload)
            if not from_push:
                datalog.log("http_status", device=m.name, payload=payload)
                self.check_mismatch(m, new)
            if new.battery is None and m.state:
                new.battery = m.state.battery   # HTTP/MQTT payloads do not always carry it
            now = time.monotonic()
            prev = m.state.state if m.state else None
            if new.state == "mowing" and prev in AT_DOCK:
                # a new mowing session starts: begin a fresh path (pause/resume keeps it)
                m.trail.reset(datetime.now(timezone.utc).isoformat(timespec="seconds"))
                m.location.new_session()
                datalog.log("session_start", device=m.name, from_state=prev, battery=new.battery)
                self.publish_path(m)
            elif prev in DRIVING and new.state in AT_DOCK:
                self.persist_trails(force=True)
                datalog.log("session_end", device=m.name, to_state=new.state, battery=new.battery,
                            points=len(m.trail.points), progress_pct=m.location.data.get("progress_pct"),
                            zone=m.location.data.get("zone"))
            if prev != new.state:
                datalog.log("state_change", device=m.name, from_state=prev, to_state=new.state,
                            battery=new.battery, source="mqtt" if from_push else "http")
            m.state = new
            m.last_data = now
            if from_push:
                m.last_push = now
            self.publish_state(m)
            self.publish_availability(m, "online")
            self.update_stats(m, new)

    def update_stats(self, m: MowerRuntime, new: MowerState) -> None:
        if not self.stats:
            return
        session, changed = self.stats.on_state(
            m.name, new.state, new.battery, time.time(), progress=m.location.data.get("progress_pct"),
            zone=m.location.data.get("zone"), points=len(m.trail.points))
        if session:
            # one message per finished session: openHAB can persist and sum these itself
            self.publish(ha.topics(self.base, m.name)["session"], json.dumps(session), retain=True)
            datalog.log("session_summary", device=m.name, **session)
        if session or changed:
            self.publish_stats(m)

    def on_cloud_message(self, device_id: str, channel: str, payload) -> None:
        m = self.mowers.get(device_id)
        if not m:
            return
        if channel == "location":
            if not m.location_seen:
                m.location_seen = True
                log.info("%s: first message on the location channel received", m.name)
            t = ha.topics(self.base, m.name)
            # Raw dump, retained so the last known position is always visible.
            self.publish(t["location"], json.dumps(payload), retain=True)
            # Decoded; an empty array (e.g. while docked) must not wipe the last position.
            if m.location.update(payload):
                body = dict(m.location.data)
                body["last_update"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self.publish(t["position"], json.dumps(body), retain=True)
                d = m.location.data
                if self.maps is not None and d.get("zone") is not None:
                    self.maps.note_zone(d["zone"])
                if "x" in d and m.state and m.state.state in DRIVING:
                    if not m.trail.started:
                        m.trail.started = body["last_update"]
                    if m.trail.add(d["x"], d["y"]):
                        self.publish_path(m)
            return
        if not isinstance(payload, dict):
            return
        if channel == "state":
            self.apply(m, payload, from_push=True)
        elif channel == "event":
            self.publish(ha.topics(self.base, m.name)["event"], json.dumps(payload))
        # `attributes` carry no state we expose yet; visible with LOG_LEVEL=DEBUG.

    # -- HTTP fallback loop --------------------------------------------------
    def poll_once(self) -> None:
        cfg = self.config
        now = time.monotonic()
        due = [m for m in self.mowers.values()
               if now - m.last_push > cfg.mqtt_stale_seconds and now - m.last_http >= cfg.http_poll_interval]
        if due:
            try:
                statuses = self.api.statuses([m.dev.id for m in due])
                self.set_auth("ok")
                for m in due:
                    m.last_http = time.monotonic()
                    if m.dev.id in statuses:
                        self.apply(m, statuses[m.dev.id], from_push=False)
            except AuthRequired as exc:
                self.on_auth_required(str(exc))
                for m in due:
                    m.last_http = time.monotonic()
            except ApiError as exc:
                log.warning("status poll failed: %s", exc)
                datalog.log("api_error", endpoint="getVehicleStatus", error=str(exc)[:200])
                for m in due:
                    m.last_http = time.monotonic()
        for m in self.mowers.values():
            if m.last_data and now - m.last_data > cfg.offline_after:
                self.publish_availability(m, "offline")
            if self.stats and self.stats.week_changed(m.name, time.time()):
                self.publish_stats(m)       # a new week began: the "this week" figures reset

    def run(self) -> None:
        cfg = self.config.mqtt
        self.client.connect_async(cfg.host, cfg.port, keepalive=60)
        self.client.reconnect_delay_set(1, 30)
        self.client.loop_start()
        for m in self.mowers.values():
            self.publish_availability(m, "offline")
        self.cloud.start()
        while not self.stop.is_set():
            if self.cloud.connected.is_set() and self.auth_state != "ok":
                self.set_auth("ok")
            self.poll_once()
            self.stop.wait(5)
        self.shutdown()

    def shutdown(self) -> None:
        self.persist_trails(force=True)
        if self.stats:
            self.stats.save()
        for m in self.mowers.values():
            self.publish_availability(m, "offline", force=True)
        info = self.client.publish(self.bridge_topic, "offline", qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        self.client.loop_stop()
        self.client.disconnect()
