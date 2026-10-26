"""Weekly mowing statistics: number of sessions and mowing time.

State lives in one small JSON file so a restart does not lose the running session or the
week totals. It cannot grow: per mower it keeps one running session, the last session, a
grand total and at most `keep_weeks` weekly totals. openHAB (or anything else) can keep
the long history itself by persisting the `session` messages the bridge publishes.

A *session* runs from "docked/idle/charging -> mowing" until the mower is back in
"docked/idle/charging". Time is split per state: mowing_s counts only the time in
`mowing`; paused and returning are kept apart. Sessions with less than `min_session_s`
of mowing are reported but not counted. A session that was already running when the
bridge started (its start unknown) or that had a gap longer than GAP_LIMIT is flagged
`partial`. A session is counted in the week (ISO, Monday first, in the configured time
zone) in which it ended.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional, Tuple

from .models import AT_DOCK, DRIVING

log = logging.getLogger(__name__)

GAP_LIMIT = 600.0    # seconds without any status after which the gap is not counted
SAVE_EVERY = 60.0    # while a session runs, write at most this often (state changes always)
BUCKETS = {"mowing": "mowing_s", "paused": "paused_s", "returning": "returning_s"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _zero() -> Dict[str, float]:
    return {"sessions": 0, "mowing_s": 0, "duration_s": 0}


class StatsTracker:
    def __init__(self, path: str, tz: tzinfo = timezone.utc, keep_weeks: int = 12,
                 min_session_s: float = 60.0):
        self.path = path
        self.tz = tz
        self.keep_weeks = max(2, keep_weeks)
        self.min_session_s = min_session_s
        self.data: Dict[str, Any] = {"version": 1, "devices": {}}
        self._published_week: Dict[str, str] = {}
        self._last_save = 0.0
        self._load()

    # -- storage -------------------------------------------------------------
    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("devices"), dict):
                self.data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s (%s); starting with empty statistics", self.path, exc)

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as exc:      # a full disk must not take the bridge down
            log.warning("cannot write %s: %s", self.path, exc)

    # -- weeks ---------------------------------------------------------------
    def week_key(self, ts: float) -> str:
        iso = datetime.fromtimestamp(ts, self.tz).isocalendar()
        return "%d-W%02d" % (iso[0], iso[1])

    def week_start(self, ts: float) -> str:
        local = datetime.fromtimestamp(ts, self.tz)
        return (local - timedelta(days=local.weekday())).date().isoformat()

    def _dev(self, name: str, now: float) -> Dict[str, Any]:
        d = self.data["devices"].get(name)
        if d is None:
            d = {"since": _iso(now), "weeks": {}, "total": _zero(), "active": None,
                 "last_state": None, "last_session": None}
            self.data["devices"][name] = d
        return d

    # -- events --------------------------------------------------------------
    def on_state(self, name: str, state: str, battery: Optional[int], now: float,
                 progress: Optional[float] = None, zone: Any = None,
                 points: Optional[int] = None) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Feed every status update. Returns (finished session or None, running-flag changed)."""
        d = self._dev(name, now)
        act = d["active"]
        last = d["last_state"]
        session = None
        changed = False

        if act is None:
            if state in DRIVING and (last is None or last in AT_DOCK):
                d["active"] = act = {
                    "started": now, "state": state, "last_seen": now, "battery_start": battery,
                    "partial": not (state == "mowing" and last in AT_DOCK),
                    "mowing_s": 0.0, "paused_s": 0.0, "returning_s": 0.0, "other_s": 0.0}
                changed = True
        else:
            gap = now - act["last_seen"]
            broken = gap > GAP_LIMIT
            if broken:
                act["partial"] = True          # the time we did not see is not counted
            else:
                act[BUCKETS.get(act["state"], "other_s")] += gap
            if state in AT_DOCK:
                ended_at = act["last_seen"] if broken else now
                session = self._finish(name, d, act, ended_at, state, battery, progress, zone, points)
                changed = True
            else:
                act["state"] = state
                act["last_seen"] = now
        d["last_state"] = state

        # state changes and finished sessions are saved at once; plain "still running" updates
        # only once a minute
        if session is not None or changed or (d["active"] and now - self._last_save >= SAVE_EVERY) \
                or last != state:
            self._last_save = now
            self.save()
        return session, changed

    def _finish(self, name: str, d: Dict[str, Any], act: Dict[str, Any], ended_at: float, state: str,
                battery: Optional[int], progress, zone, points) -> Dict[str, Any]:
        duration = max(0.0, ended_at - act["started"])
        counted = act["mowing_s"] >= self.min_session_s
        week = self.week_key(ended_at)
        session = {
            "started": _iso(act["started"]), "ended": _iso(ended_at), "week": week,
            "duration_s": round(duration), "mowing_s": round(act["mowing_s"]),
            "paused_s": round(act["paused_s"]), "returning_s": round(act["returning_s"]),
            "battery_start": act.get("battery_start"), "battery_end": battery, "end_state": state,
            "progress_pct": progress, "zone": zone, "points": points,
            "partial": bool(act["partial"]), "counted": counted}
        if counted:
            w = d["weeks"].setdefault(week, _zero())
            for bucket in (w, d["total"]):
                bucket["sessions"] += 1
                bucket["mowing_s"] += session["mowing_s"]
                bucket["duration_s"] += session["duration_s"]
            for old in sorted(d["weeks"])[:-self.keep_weeks]:
                del d["weeks"][old]
        d["last_session"] = session
        d["active"] = None
        return session

    # -- reading -------------------------------------------------------------
    def snapshot(self, name: str, now: float) -> Dict[str, Any]:
        d = self._dev(name, now)
        key = self.week_key(now)
        cur = d["weeks"].get(key, _zero())
        prev_key = self.week_key(now - 7 * 86400)
        out: Dict[str, Any] = {
            "week": key, "week_start": self.week_start(now),
            "sessions": cur["sessions"], "mowing_s": cur["mowing_s"],
            "mowing_h": round(cur["mowing_s"] / 3600.0, 2), "duration_s": cur["duration_s"],
            "active": d["active"] is not None,
            "total_sessions": d["total"]["sessions"], "total_mowing_s": d["total"]["mowing_s"],
            "total_mowing_h": round(d["total"]["mowing_s"] / 3600.0, 2), "since": d["since"],
            "weeks": [{"week": k, "sessions": v["sessions"], "mowing_h": round(v["mowing_s"] / 3600.0, 2)}
                      for k, v in sorted(d["weeks"].items(), reverse=True)],
            "last_update": _iso(now),
        }
        if prev_key in d["weeks"]:
            p = d["weeks"][prev_key]
            out["previous_week"] = {"week": prev_key, "sessions": p["sessions"],
                                    "mowing_s": p["mowing_s"], "mowing_h": round(p["mowing_s"] / 3600.0, 2)}
        if d["last_session"]:
            out["last_session_ended"] = d["last_session"]["ended"]
        self._published_week[name] = key
        return out

    def week_changed(self, name: str, now: float) -> bool:
        """True once after the week rolled over since the last snapshot (so it can be republished)."""
        return name in self._published_week and self._published_week[name] != self.week_key(now)

    def names(self):
        return list(self.data["devices"])
