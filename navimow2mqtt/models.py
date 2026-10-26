"""Mower state normalisation (mirrors the cloud payload formats seen in navimow-sdk)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_RAW_STATE = {
    "isDocked": "docked",
    "isIdel": "idle",   # sic: the cloud really sends this spelling
    "isIdle": "idle",
    "isMapping": "mowing",
    "isRunning": "mowing",
    "isPaused": "paused",
    "isDocking": "returning",
    "Error": "error",
    "error": "error",
    "isLifted": "error",
    "inSoftwareUpdate": "paused",
    "Self-Checking": "idle",
    "Self-checking": "idle",
    "Offline": "unknown",
    "offline": "unknown",
}

KNOWN_STATES = {"idle", "mowing", "paused", "docked", "charging", "returning", "error", "unknown"}

# mower state -> Home Assistant lawn_mower activity
ACTIVITY = {
    "idle": "docked",
    "docked": "docked",
    "charging": "docked",
    "mowing": "mowing",
    "paused": "paused",
    "returning": "returning",
    "error": "error",
    "unknown": "error",
}


AT_DOCK = {"docked", "idle", "charging"}       # a switch from these to "mowing" starts a new session
DRIVING = {"mowing", "paused", "returning"}    # states in which a session is running


def normalize_state(raw: Any) -> str:
    if not isinstance(raw, str):
        return "unknown"
    return _RAW_STATE.get(raw, raw)


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_battery(data: Dict[str, Any]) -> Optional[int]:
    """MQTT sends `battery`; the HTTP status call sends capacityRemaining[].rawValue."""
    b = _to_int(data.get("battery"))
    if b is not None:
        return b
    cap = data.get("capacityRemaining")
    if isinstance(cap, list):
        for item in cap:
            if isinstance(item, dict) and str(item.get("unit", "")).upper() == "PERCENTAGE":
                v = _to_int(item.get("rawValue"))
                if v is not None:
                    return v
        if cap and isinstance(cap[0], dict):
            return _to_int(cap[0].get("rawValue"))
    return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Location:
    """Latest values from the (undocumented) `location` channel.

    The channel sends a JSON array of items keyed by `type` (as found by NaviWatch):
      1 pose      postureX / postureY (metres, local grid relative to the dock, NOT GPS),
                  postureTheta (radians), vehicleState
      2 progress  currentMowBoundary (zone the mower is in), currentMowProgress (0..10000)
      3 target    partitionIds (zones it was told to mow)
      4 delay     taskDelay (rain / schedule)
    Types arrive separately, so each update only changes the fields it carries.
    """

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self._last_xy: Optional[tuple] = None

    def new_session(self) -> None:
        """A new mowing session starts at the dock: forget the previous movement."""
        self._last_xy = None
        self.data.pop("course_deg", None)

    def update(self, items: Any) -> bool:
        """Merge a payload; returns True if anything was recognised."""
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return False
        changed = False
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = _to_int(it.get("type"))
            d = self.data
            if kind == 1:
                x, y, th = _float(it.get("postureX")), _float(it.get("postureY")), _float(it.get("postureTheta"))
                if x is not None and y is not None:
                    d["x"], d["y"] = x, y
                    d["distance_m"] = round(math.hypot(x, y), 2)
                    # Direction the mower actually moved (from the previous different
                    # position), counter-clockwise from +x like heading_deg. Independent of
                    # the reported heading, which is unreliable while turning.
                    if self._last_xy is not None and self._last_xy != (x, y):
                        dx, dy = x - self._last_xy[0], y - self._last_xy[1]
                        d["course_deg"] = round(math.degrees(math.atan2(dy, dx)) % 360, 1)
                    self._last_xy = (x, y)
                    changed = True
                if th is not None:
                    d["heading_rad"] = th
                    d["heading_deg"] = round(math.degrees(th) % 360, 1)
                    changed = True
                if changed:
                    if it.get("vehicleState") is not None:
                        d["vehicle_state"] = it["vehicleState"]
                    if it.get("time") is not None:
                        d["time"] = it["time"]
            elif kind == 2:
                if it.get("currentMowBoundary") is not None:
                    d["zone"] = it["currentMowBoundary"]
                prog = _float(it.get("currentMowProgress"))
                if prog is not None:
                    d["progress_pct"] = round(prog / 100.0, 1)   # raw is 0..10000
                changed = True
            elif kind == 3:
                d["target_zone"] = it.get("partitionIds")
                changed = True
            elif kind == 4:
                d["task_delay"] = it.get("taskDelay")
                changed = True
        return changed


def downsample(points: list, limit: int) -> list:
    """At most `limit` points, evenly spread over the path, always including the first and the last."""
    n = len(points)
    if limit < 2 or n <= limit:
        return list(points)
    step = (n - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


class Trail:
    """The last `max_points` positions of the current mowing session.

    A whole lawn takes thousands of points, so the limit is generous: when it is too low only the end of
    the session is left and the start of the path is missing. What is published on MQTT is a thinned-out
    copy (see to_json), not the whole path."""

    def __init__(self, max_points: int = 8000) -> None:
        self.max_points = max_points
        self.points: list = []
        self.started: Optional[str] = None

    def reset(self, started: Optional[str] = None) -> None:
        self.points = []
        self.started = started

    def add(self, x: float, y: float) -> bool:
        """Append a point unless the mower has not moved; returns True if it was added."""
        if self.max_points <= 0:
            return False
        if self.points and self.points[-1] == [x, y]:
            return False
        self.points.append([x, y])
        if len(self.points) > self.max_points:
            del self.points[:len(self.points) - self.max_points]
        return True

    def to_json(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """The path as JSON. With `limit` the points are thinned out to at most that many (first and last
        kept); `total` is always the real number of points."""
        points = downsample(self.points, limit) if limit else list(self.points)
        return {"points": points, "count": len(points), "total": len(self.points), "started": self.started}


@dataclass
class Device:
    id: str
    name: str = ""
    model: str = ""
    firmware: str = ""
    serial: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Device":
        def first(*keys: str) -> str:
            for k in keys:
                if d.get(k):
                    return str(d[k])
            return ""
        dev_id = str(d.get("id", ""))
        return cls(id=dev_id, name=first("name", "deviceName"), model=first("model", "productModel"),
                   firmware=first("firmware", "firmware_version", "firmwareVersion", "fwVersion",
                                  "softwareVersion"),
                   # the id of a Navimow looks like its serial number (e.g. 2SCAH...)
                   serial=first("serial_number", "serialNumber", "sn") or dev_id,
                   raw=dict(d))


@dataclass
class MowerState:
    state: str = "unknown"
    battery: Optional[int] = None
    signal_strength: Optional[int] = None
    position: Optional[Dict[str, Any]] = None
    error: Any = None
    timestamp: Optional[int] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, p: Dict[str, Any]) -> "MowerState":
        raw = p.get("state") or p.get("status") or p.get("vehicleState")
        metrics = p.get("metrics") if isinstance(p.get("metrics"), dict) else {}
        metrics = dict(metrics)
        state = normalize_state(raw)
        if raw is not None and state != raw:
            metrics["raw_state"] = raw
        return cls(state=state, battery=extract_battery(p),
                   signal_strength=_to_int(p.get("signal_strength")),
                   position=p.get("position") if isinstance(p.get("position"), dict) else None,
                   error=p.get("error"), timestamp=_to_int(p.get("timestamp")), metrics=metrics)

    @property
    def activity(self) -> str:
        return ACTIVITY.get(self.state, "error")

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"state": self.state, "activity": self.activity}
        if self.battery is not None:
            out["battery"] = self.battery
        if self.signal_strength is not None:
            out["signal_strength"] = self.signal_strength
        if self.position:
            out["position"] = self.position
        if self.error:
            out["error"] = self.error
        if self.metrics:
            out["metrics"] = self.metrics
        return out
