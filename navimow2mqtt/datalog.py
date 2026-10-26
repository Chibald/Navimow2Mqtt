"""Structured event log (JSON lines, one file per UTC day) for later analysis.

Records raw cloud messages and the connection / token / watchdog events around them.
Never pass secrets in here: no tokens, passwords, MQTT credentials, login links or codes.

    {"ts": "2026-10-13T20:21:14.123+00:00", "kind": "cloud_msg", "device": "...", ...}

Module-level `log(kind, **fields)` is a no-op until `init()` has been called, so the rest
of the code (and the tests) can call it unconditionally.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

log_ = logging.getLogger(__name__)

PREFIX = "events-"
SUFFIX = ".jsonl"


class DataLog:
    def __init__(self, directory: str, keep_days: int = 14, max_bytes_per_day: int = 50 * 1024 * 1024):
        self.directory = directory
        self.keep_days = keep_days
        self.max_bytes = max_bytes_per_day
        self._lock = threading.Lock()
        self._day: Optional[str] = None
        self._fh = None
        self._size = 0
        self._capped = False
        os.makedirs(directory, exist_ok=True)

    def _path(self, day: str) -> str:
        return os.path.join(self.directory, "%s%s%s" % (PREFIX, day, SUFFIX))

    def _roll(self, day: str) -> None:
        if self._fh:
            self._fh.close()
        self._day = day
        path = self._path(day)
        self._fh = open(path, "a", encoding="utf-8")
        self._size = os.path.getsize(path)
        self._capped = False
        self._cleanup(day)

    def _cleanup(self, today: str) -> None:
        cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=self.keep_days)).strftime("%Y-%m-%d")
        for path in glob.glob(os.path.join(self.directory, PREFIX + "*" + SUFFIX)):
            day = os.path.basename(path)[len(PREFIX):-len(SUFFIX)]
            if day < cutoff:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def write(self, kind: str, **fields) -> None:
        now = datetime.now(timezone.utc)
        rec = {"ts": now.isoformat(timespec="milliseconds"), "kind": kind}
        rec.update(fields)
        try:
            line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        except (TypeError, ValueError):
            return
        day = now.strftime("%Y-%m-%d")
        with self._lock:
            try:
                if day != self._day:
                    self._roll(day)
                if self._capped:
                    return
                if self._size + len(line) > self.max_bytes:
                    self._capped = True
                    line = json.dumps({"ts": rec["ts"], "kind": "log_capped",
                                       "max_bytes": self.max_bytes}) + "\n"
                self._fh.write(line)
                self._fh.flush()
                self._size += len(line)
            except OSError as exc:  # a full disk must never take the bridge down
                log_.warning("event log write failed: %s", exc)

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


_instance: Optional[DataLog] = None


def init(directory: str, keep_days: int = 14, max_mb: int = 50) -> DataLog:
    global _instance
    _instance = DataLog(directory, keep_days, max_mb * 1024 * 1024)
    return _instance


def log(kind: str, **fields) -> None:
    if _instance is not None:
        _instance.write(kind, **fields)


def close() -> None:
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None
