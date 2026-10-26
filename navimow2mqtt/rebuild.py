"""Rebuild the path of a mowing session from the event log.

The bridge keeps only a limited number of points of the current path in memory. The event log
(`events-*.jsonl`, see datalog.py) holds every position message, so the whole path of a session can be
recovered from it, for instance after the limit was too low, or to calibrate a background picture with the
path of a complete session:

    python -m navimow2mqtt rebuild-trail [--name mower] [--since ISO] [--until ISO] [--device-id ID]

Stop the bridge first: it writes its own trail file when it stops and would overwrite the result.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .models import AT_DOCK, DRIVING, downsample


class RebuildError(Exception):
    """Nothing could be rebuilt; the message says why."""


def read_events(log_dir: str) -> Iterator[Dict[str, Any]]:
    for path in sorted(glob.glob(os.path.join(log_dir, "events-*.jsonl"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict) and "ts" in ev:
                        yield ev
        except OSError:
            continue


def sessions(events: List[Dict[str, Any]], name: str) -> List[Tuple[str, Optional[str]]]:
    """(start, end) timestamps of the mowing sessions of `name`, oldest first; `end` is None while one is running.

    A session starts when the mower leaves the dock and ends when it is back. A bridge restart in the middle
    shows up as a state change from "unknown" (None); that must not start a new session."""
    found: List[Tuple[str, Optional[str]]] = []
    start: Optional[str] = None
    for ev in events:
        if ev.get("kind") != "state_change" or ev.get("device") != name:
            continue
        frm, to = ev.get("from_state"), ev.get("to_state")
        if to in DRIVING and (start is None or frm in AT_DOCK):
            if start is not None:                    # the previous one never reported its end
                found.append((start, ev["ts"]))
            start = ev["ts"]
        elif to in AT_DOCK and start is not None:
            found.append((start, ev["ts"]))
            start = None
    if start is not None:
        found.append((start, None))
    return found


def positions(events: List[Dict[str, Any]], device_id: Optional[str], since: str, until: Optional[str]) -> List[List[float]]:
    """The positions the mower reported between two times, without repeats."""
    points: List[List[float]] = []
    for ev in events:
        if ev.get("kind") != "cloud_msg" or ev.get("channel") != "location" or ev.get("device") != device_id:
            continue
        if ev["ts"] < since or (until is not None and ev["ts"] > until):
            continue
        payload = ev.get("payload")
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict) and item.get("type") == 1:
                try:
                    p = [float(item["postureX"]), float(item["postureY"])]
                except (KeyError, TypeError, ValueError):
                    continue
                if not points or points[-1] != p:
                    points.append(p)
    return points


def rebuild(log_dir: str, name: str, since: Optional[str] = None, until: Optional[str] = None,
            device_id: Optional[str] = None, max_points: int = 8000) -> Dict[str, Any]:
    """The path of a session as {"points": [[x, y], ...], "started": ts, "ended": ts | None, "total": n}."""
    events = list(read_events(log_dir))
    if not events:
        raise RebuildError("No event log found in %s (is LOG_DIR set and was the bridge running?)." % log_dir)
    ids = {e.get("device") for e in events if e.get("kind") == "cloud_msg" and e.get("channel") == "location"}
    if device_id is None:
        if len(ids) > 1:
            raise RebuildError("The log has position messages of several mowers (%s): pass --device-id."
                               % ", ".join(sorted(str(i) for i in ids)))
        device_id = next(iter(ids), None)

    if since is not None:
        window = (since, until)
        points = positions(events, device_id, since, until)
    else:
        found = sessions(events, name)
        if not found:
            raise RebuildError("No mowing session found in the event log for %r." % name)
        # the newest session that really holds a path: a brief flash of "mowing" (for instance a stale
        # status right after the bridge starts) is skipped
        window, points = found[-1], []
        for start, end in reversed(found):
            points = positions(events, device_id, start, until or end)
            if len(points) >= 2:
                window = (start, until or end)
                break
    if len(points) < 2:
        raise RebuildError("The event log holds fewer than two positions between %s and %s."
                           % (window[0], window[1] or "now"))
    return {"points": downsample(points, max_points), "started": window[0], "ended": window[1],
            "total": len(points)}


def write_trail(trail_file: str, name: str, result: Dict[str, Any]) -> None:
    """Put the rebuilt path in the trail file, keeping the paths of other mowers."""
    data: Dict[str, Any] = {"version": 1, "trails": {}}
    try:
        with open(trail_file, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        if isinstance(old, dict) and old.get("version") == 1 and isinstance(old.get("trails"), dict):
            data = old
    except (OSError, ValueError):
        pass
    data["trails"][name] = {"points": result["points"], "started": result["started"]}
    os.makedirs(os.path.dirname(trail_file) or ".", exist_ok=True)
    tmp = trail_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, trail_file)
