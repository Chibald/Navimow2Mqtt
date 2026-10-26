"""Background pictures for the map tile (for instance screenshots of the Navimow app).

A *view* is one picture plus its calibration: how positions in metres around the dock map onto
pixels of that picture. Three numbers describe it:

    dock      the pixel where the dock is (positions are metres from the dock, so the dock is the origin)
    scale     pixels per metre
    rotation  degrees, clockwise on screen, between the picture and the bridge's x/y axes
    flip      the picture is mirrored (normally not)

The pictures and one small JSON file live in the maps directory (a few hundred kB per picture,
12 pictures at most). Nothing here grows by itself.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import struct
import threading
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_VIEWS = 12
MIN_SIDE, MAX_SIDE = 100, 8000
SCALE_MIN, SCALE_MAX = 0.5, 500.0        # pixels per metre


class MapError(ValueError):
    """A picture or a calibration that is not acceptable; the message is meant for the user."""


def image_info(data: bytes) -> Tuple[str, str, int, int]:
    """(extension, content type, width, height) of a PNG or JPEG, read from its header. Raises MapError."""
    if len(data) > MAX_IMAGE_BYTES:
        raise MapError("The picture is larger than %d MB." % (MAX_IMAGE_BYTES // (1024 * 1024)))
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        ext, ctype = "png", "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        w = h = 0
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                break
            i += 2 + length
        ext, ctype = "jpg", "image/jpeg"
    else:
        raise MapError("Only PNG and JPEG pictures are accepted.")
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        raise MapError("The picture must be between %d and %d pixels on each side." % (MIN_SIDE, MAX_SIDE))
    return ext, ctype, w, h


def to_image(cal: Dict[str, Any], x: float, y: float) -> Tuple[float, float]:
    """Metres from the dock (x right, y up) -> pixel (u, v) of the picture (v grows downwards)."""
    if cal.get("flip"):
        x = -x
    th = math.radians(cal["rotation"])
    vx, vy = x, -y                                   # the same vector in screen orientation
    c, s = math.cos(th), math.sin(th)
    return (cal["dock"][0] + cal["scale"] * (vx * c - vy * s),
            cal["dock"][1] + cal["scale"] * (vx * s + vy * c))


def fit(pairs: List[Tuple[Tuple[float, float], Tuple[float, float]]]) -> Optional[Dict[str, Any]]:
    """Least-squares calibration from two or more ((x, y) metres, (u, v) pixels) pairs, no flip.
    Returns {"dock", "scale", "rotation"} or None when the pairs do not determine one."""
    if len(pairs) < 2:
        return None
    zs = [complex(x, -y) for (x, y), _ in pairs]
    ws = [complex(u, v) for _, (u, v) in pairs]
    zm, wm = sum(zs) / len(zs), sum(ws) / len(ws)
    den = sum(abs(z - zm) ** 2 for z in zs)
    if den < 1e-9:
        return None
    a = sum((z - zm).conjugate() * (w - wm) for z, w in zip(zs, ws)) / den
    b = wm - a * zm
    if abs(a) < 1e-9:
        return None
    return {"dock": [b.real, b.imag], "scale": abs(a),
            "rotation": math.degrees(math.atan2(a.imag, a.real))}


class MapStore:
    def __init__(self, directory: str):
        self.dir = directory
        self.path = os.path.join(directory, "maps.json")
        self._lock = threading.Lock()
        self._uri_cache: Dict[str, Tuple[float, str]] = {}
        self.data: Dict[str, Any] = {"version": 1, "views": {}, "zones_seen": []}
        os.makedirs(directory, exist_ok=True)
        self._load()

    # -- storage -------------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("views"), dict):
                data.setdefault("zones_seen", [])
                self.data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s (%s); starting without map views", self.path, exc)

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"))
        os.replace(tmp, self.path)

    def _file(self, view: Dict[str, Any]) -> str:
        return os.path.join(self.dir, view["file"])

    # -- views ---------------------------------------------------------------
    @staticmethod
    def check_name(name: str) -> str:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise MapError("The name may only contain a-z, 0-9, - and _ (at most 40 characters).")
        return name

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self.data["views"])

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            v = self.data["views"].get(name)
            return dict(v) if v else None

    def put_image(self, name: str, data: bytes) -> Dict[str, Any]:
        """Add or replace the picture of a view. A new picture of a different size resets the calibration."""
        self.check_name(name)
        ext, ctype, w, h = image_info(data)
        with self._lock:
            old = self.data["views"].get(name)
            if old is None and len(self.data["views"]) >= MAX_VIEWS:
                raise MapError("At most %d pictures can be stored." % MAX_VIEWS)
            file = "%s.%s" % (name, ext)
            tmp = os.path.join(self.dir, file + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, os.path.join(self.dir, file))
            if old and old["file"] != file:
                try:
                    os.remove(os.path.join(self.dir, old["file"]))
                except OSError:
                    pass
            keep = old and (old["w"], old["h"]) == (w, h)
            view = {"file": file, "ctype": ctype, "w": w, "h": h,
                    "dock": old["dock"] if keep else None, "scale": old["scale"] if keep else None,
                    "rotation": old["rotation"] if keep else 0.0, "flip": old["flip"] if keep else False,
                    "zones": old["zones"] if old else []}
            self.data["views"][name] = view
            self._uri_cache.pop(name, None)
            self._save()
            return dict(view)

    def calibrate(self, name: str, dock: Any, scale: Any, rotation: Any, flip: Any = False,
                  zones: Any = None) -> Dict[str, Any]:
        try:
            du, dv = float(dock[0]), float(dock[1])
            scale, rotation = float(scale), float(rotation)
        except (TypeError, ValueError, IndexError, KeyError):
            raise MapError("The calibration needs a dock position, a scale and a rotation.")
        if not all(math.isfinite(v) for v in (du, dv, scale, rotation)):
            raise MapError("The calibration contains an invalid number.")
        if not SCALE_MIN <= scale <= SCALE_MAX:
            raise MapError("The scale must be between %g and %g pixels per metre." % (SCALE_MIN, SCALE_MAX))
        zone_ids: List[int] = []
        for z in (zones or []):
            try:
                zone_ids.append(int(z))
            except (TypeError, ValueError):
                raise MapError("Zone ids must be whole numbers.")
        if len(zone_ids) > 20:
            raise MapError("Too many zone ids.")
        with self._lock:
            v = self.data["views"].get(name)
            if v is None:
                raise MapError("There is no picture called %r." % name)
            if not (-v["w"] <= du <= 2 * v["w"] and -v["h"] <= dv <= 2 * v["h"]):
                raise MapError("The dock position is far outside the picture.")
            rotation = (rotation + 180.0) % 360.0 - 180.0
            v.update({"dock": [round(du, 2), round(dv, 2)], "scale": round(scale, 4),
                      "rotation": round(rotation, 3), "flip": bool(flip), "zones": sorted(set(zone_ids))})
            self._save()
            return dict(v)

    def delete(self, name: str) -> bool:
        with self._lock:
            v = self.data["views"].pop(name, None)
            if not v:
                return False
            try:
                os.remove(self._file(v))
            except OSError:
                pass
            self._uri_cache.pop(name, None)
            self._save()
            return True

    def image(self, name: str) -> Optional[Tuple[bytes, str]]:
        v = self.get(name)
        if not v:
            return None
        try:
            with open(self._file(v), "rb") as fh:
                return fh.read(), v["ctype"]
        except OSError:
            return None

    def background(self, name: str) -> Optional[Dict[str, Any]]:
        """The view with the picture as a data: URI, ready for the tile; None if it is not calibrated."""
        v = self.get(name)
        if not v or v["dock"] is None or v["scale"] is None:
            return None
        try:
            mtime = os.path.getmtime(self._file(v))
        except OSError:
            return None
        cached = self._uri_cache.get(name)
        if cached and cached[0] == mtime:
            uri = cached[1]
        else:
            got = self.image(name)
            if not got:
                return None
            uri = "data:%s;base64,%s" % (got[1], base64.b64encode(got[0]).decode("ascii"))
            self._uri_cache[name] = (mtime, uri)
        out = dict(v)
        out["uri"] = uri
        return out

    # -- zones ---------------------------------------------------------------
    def note_zone(self, zone: Any) -> None:
        """Remember a zone id the mower reported, so the calibration page can list it."""
        try:
            zone = int(zone)
        except (TypeError, ValueError):
            return
        with self._lock:
            seen = self.data["zones_seen"]
            if zone not in seen and len(seen) < 50:
                seen.append(zone)
                seen.sort()
                self._save()

    def zones_seen(self) -> List[int]:
        with self._lock:
            return list(self.data["zones_seen"])

    def pick(self, zone: Any) -> Optional[str]:
        """The calibrated view for a zone id, else the first calibrated view without zones (the overview)."""
        try:
            zone = int(zone) if zone is not None else None
        except (TypeError, ValueError):
            zone = None
        with self._lock:
            views = self.data["views"]
            ready = {n: v for n, v in views.items() if v["dock"] is not None and v["scale"] is not None}
            if zone is not None:
                for n in sorted(ready):
                    if zone in ready[n]["zones"]:
                        return n
            for n in sorted(ready):
                if not ready[n]["zones"]:
                    return n
            return None
