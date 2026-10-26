"""The map tile: a lawn that gets mowed.

Drawn in the look of the "Tuin" design: a dark-green lawn, the mowed lanes in two alternating light
greens (so it looks like a striped lawn), the charging dock (a dark block with a green LED) and the
mower as a small robot, pulsing while it works.

The image is only the tile: no card, no title, no legend, no text. Everything outside its rounded
corners is transparent, so it takes the background of whatever it is put on.

Without a background picture the dark green is just the tile surface. With one (see maps.py, for
instance a screenshot of the Navimow app) the picture replaces the lawn and the lanes and the robot are
drawn on top of it, at the right place thanks to the picture's calibration. The tile can then show the
whole picture, or zoom in on a window of `span` metres around the robot.
"""
from __future__ import annotations

import html
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from .i18n import current, t
from .maps import to_image

STALE_SECONDS = 120     # a position older than this is drawn grey and does not pulse

LIGHT = ("--lawn:#6fa85a;--lane-a:#a9d58e;--lane-b:#9bcb80;--dock:#16211b;--robot:#f2f2ee;--robot-line:#16211b")
DARK = ("--lawn:#3f6f3a;--lane-a:#86bf6f;--lane-b:#78b062;--dock:#e8ece8;--robot:#f2f2ee;--robot-line:#16211b")

STYLE = """
 .lawn{fill:var(--lawn)}
 .lane-a{fill:none;stroke:var(--lane-a);stroke-linecap:round;stroke-linejoin:round}
 .lane-b{fill:none;stroke:var(--lane-b);stroke-linecap:round;stroke-linejoin:round}
 .dock{fill:var(--dock)} .led{fill:#4fd17a}
 .body{fill:var(--robot);stroke:var(--robot-line);stroke-width:2} .wheel{fill:var(--robot-line)}
 .pulse{transform-box:fill-box;transform-origin:center;animation:pulse 1.6s ease-out infinite}
 @keyframes pulse{from{transform:scale(1);opacity:.55}to{transform:scale(2.6);opacity:0}}
 @media (prefers-reduced-motion:reduce){.pulse{animation:none}}
"""

LED = {"paused": "#e8a33a", "error": "#e04b3f"}                         # every other state: green
PULSE = {"mowing": "#ffffff", "returning": "#2f7fc1", "error": "#e04b3f"}

Point = Tuple[float, float]


def _transform(x: float, y: float, rotate: int, flip: Optional[str]) -> Tuple[float, float]:
    """Rotate counter-clockwise in steps of 90 degrees, then optionally mirror."""
    for _ in range((rotate // 90) % 4):
        x, y = -y, x
    if flip == "x":
        x = -x
    elif flip == "y":
        y = -y
    return x, y


def _fmt(v: float) -> str:
    return ("%.1f" % v).rstrip("0").rstrip(".")


def tile_size(width: int = 640) -> Tuple[int, int]:
    """(width, height) of the tile for a requested width: the proportions of the design's map."""
    w = max(240, min(int(width), 1600))
    return w, round(w * 520 / 800)


def lanes(points: List[Point]) -> List[List[Point]]:
    """Split the path into lanes: a new lane starts whenever the mower turns around (roughly 180
    degrees). Mowing goes up and down in lanes, and alternate lanes get a different shade."""
    out: List[List[Point]] = []
    cur: List[Point] = []
    ref: Optional[Tuple[float, float]] = None      # direction of the lane being built
    for p in points:
        if not cur:
            cur = [p]
            continue
        dx, dy = p[0] - cur[-1][0], p[1] - cur[-1][1]
        if dx == 0 and dy == 0:
            continue
        if ref is None:
            ref = (dx, dy)
        elif dx * ref[0] + dy * ref[1] < 0:        # turned around: close the lane, start the next one
            out.append(cur)
            cur = [cur[-1], p]
            ref = (dx, dy)
            continue
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _layout_plain(local_pts: List[Point], cur: Optional[Point], width: int, rotate: int,
                  flip: Optional[str]) -> Tuple[int, int, Callable[[Point], Point], float, str]:
    """Fit the path, the dock and the mower into the tile: (W, H, project, pixels per metre, extra svg)."""
    rotate = rotate if rotate in (0, 90, 180, 270) else 0
    flip = flip if flip in ("x", "y") else None
    W, H = tile_size(width)
    pts = [_transform(x, y, rotate, flip) for x, y in local_pts]
    c = _transform(cur[0], cur[1], rotate, flip) if cur else None
    xs = [0.0] + [p[0] for p in pts] + ([c[0]] if c else [])
    ys = [0.0] + [p[1] for p in pts] + ([c[1]] if c else [])
    minx, maxx, miny, maxy = min(xs) - 1.5, max(xs) + 1.5, min(ys) - 1.5, max(ys) + 1.5
    if maxx - minx < 8:
        m = (maxx + minx) / 2
        minx, maxx = m - 4, m + 4
    if maxy - miny < 8:
        m = (maxy + miny) / 2
        miny, maxy = m - 4, m + 4
    inner = 28
    s = min((W - 2 * inner) / (maxx - minx), (H - 2 * inner) / (maxy - miny))
    ox = (W - s * (maxx - minx)) / 2
    oy = (H - s * (maxy - miny)) / 2

    def project(p: Point) -> Point:
        x, y = _transform(p[0], p[1], rotate, flip)
        return ox + (x - minx) * s, oy + (maxy - y) * s              # y grows upwards on the map

    return W, H, project, s, '<rect class="lawn" x="0" y="0" width="%d" height="%d"/>' % (W, H)


def _layout_background(bg: Dict[str, Any], cur: Optional[Point], width: int, span: float,
                       follow: str, fixed_size: bool = False) -> Tuple[int, int, Callable[[Point], Point], float, str]:
    """Place the calibrated picture in the tile: the whole picture, or `span` metres around the mower.

    The whole picture normally gets the picture's own proportions. With `fixed_size` it is fitted inside
    the tile of the zoomed view instead, so the image keeps the same size when the zoom changes."""
    bw, bh, scale = bg["w"], bg["h"], bg["scale"]
    if span and span > 0:
        W, H = tile_size(width)
        k = W / (span * scale)                                     # tile pixels per picture pixel
        base = None
        if follow != "dock" and cur is not None:
            base = to_image(bg, cur[0], cur[1])
        if base is None:
            base = to_image(bg, 0.0, 0.0)
        hw, hh = W / (2 * k), H / (2 * k)                          # half the window, in picture pixels
        cu = bw / 2 if 2 * hw >= bw else _clamp(base[0], hw, bw - hw)
        cv = bh / 2 if 2 * hh >= bh else _clamp(base[1], hh, bh - hh)
    else:
        if fixed_size:
            W, H = tile_size(width)
        else:
            W = max(240, min(int(width), 1600))
            H = max(150, min(round(W * bh / bw), 1600))
        k = min(W / bw, H / bh)
        cu, cv = bw / 2, bh / 2
    tx, ty = W / 2 - k * cu, H / 2 - k * cv

    def project(p: Point) -> Point:
        u, v = to_image(bg, p[0], p[1])
        return (u - cu) * k + W / 2, (v - cv) * k + H / 2

    image = ('<image href="%s" x="0" y="0" width="%d" height="%d" preserveAspectRatio="none" '
             'transform="translate(%s %s) scale(%s)"/>'
             % (html.escape(bg["uri"]), bw, bh, _fmt(tx), _fmt(ty), ("%.5f" % k).rstrip("0").rstrip(".")))
    return W, H, project, scale * k, image


def render_tile(data: Optional[Dict[str, Any]], rotate: int = 0, flip: Optional[str] = None,
                theme: Optional[str] = None, width: int = 640, swath_m: float = 0.25,
                lang: Optional[str] = None, background: Optional[Dict[str, Any]] = None,
                span: float = 0.0, follow: str = "robot", fixed_size: bool = False) -> str:
    """`background` is a calibrated picture (MapStore.background); `span` (metres, 0 = the whole picture)
    and `follow` ("robot" or "dock") choose the window; `fixed_size` keeps the image the same size whether it
    shows the whole picture or a window. `rotate` and `flip` only apply without a picture: its calibration
    already knows how it is oriented."""
    data = data or {}
    local_pts: List[Point] = [(float(x), float(y)) for x, y in data.get("points", [])]
    cur: Optional[Point] = None
    if data.get("x") is not None and data.get("y") is not None:
        cur = (float(data["x"]), float(data["y"]))

    if background:
        W, H, project, ppm, under = _layout_background(background, cur, width, span, follow, fixed_size)
    else:
        W, H, project, ppm, under = _layout_plain(local_pts, cur, width, rotate, flip)

    css_vars = LIGHT if theme == "light" else DARK if theme == "dark" else None
    style = STYLE + (" svg{%s}" % css_vars if css_vars else
                     " svg{%s} @media (prefers-color-scheme:dark){svg{%s}}" % (LIGHT, DARK))
    out: List[str] = []
    out.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
               'aria-label="%s">' % (W, H, W, H, html.escape(t("map.aria", lang))))
    out.append("<style>%s</style>" % style)
    # the rounded corners: everything is clipped to them, so the corners stay transparent
    out.append('<defs><clipPath id="tile"><rect x="0" y="0" width="%d" height="%d" rx="18"/></clipPath></defs>'
               % (W, H))
    out.append('<g clip-path="url(#tile)">')
    out.append(under)

    # the mowed lanes: alternate shades make the stripes of a mowed lawn
    width_px = max(4.0 if background else 6.0, swath_m * ppm)
    for i, lane in enumerate(lanes(local_pts)):
        cls = "lane-a" if i % 2 == 0 else "lane-b"
        if len(lane) >= 2:
            out.append('<polyline class="%s" stroke-width="%s" points="%s"/>'
                       % (cls, _fmt(width_px), " ".join("%s,%s" % (_fmt(a), _fmt(b)) for a, b in map(project, lane))))
        else:
            a, b = project(lane[0])
            out.append('<circle class="%s" fill="currentColor" stroke="none" cx="%s" cy="%s" r="%s"/>'
                       % (cls, _fmt(a), _fmt(b), _fmt(width_px / 2)))

    # charging dock: a dark rounded block with a green LED
    dx, dy = project((0.0, 0.0))
    out.append('<rect class="dock" x="%s" y="%s" width="32" height="20" rx="4"/>' % (_fmt(dx - 16), _fmt(dy - 10)))
    out.append('<rect class="led" x="%s" y="%s" width="10" height="4" rx="1"/>' % (_fmt(dx - 12), _fmt(dy - 6)))

    # the mower: a small robot, rotated like the design (clockwise from "up")
    if cur:
        cx, cy = project(cur)
        state = data.get("state")
        stale = data.get("position_age_s") is not None and data["position_age_s"] > STALE_SECONDS
        angle = 0.0
        if data.get("course_deg") is not None:
            a = math.radians(float(data["course_deg"]))
            tip = project((cur[0] + math.cos(a), cur[1] + math.sin(a)))
            angle = math.degrees(math.atan2(tip[0] - cx, -(tip[1] - cy)))
        out.append('<g transform="translate(%s %s)"%s>' % (_fmt(cx), _fmt(cy), ' opacity=".55"' if stale else ""))
        if state in PULSE and not stale:
            out.append('<circle class="pulse" cx="0" cy="0" r="14" fill="%s"/>' % PULSE[state])
        out.append('<g transform="rotate(%s)">' % _fmt(angle))
        out.append('<rect class="body" x="-11" y="-15" width="22" height="30" rx="7"/>')
        out.append('<rect x="-7" y="-12" width="14" height="6" rx="2" fill="%s"/>' % LED.get(state, "#4fd17a"))
        out.append('<circle class="wheel" cx="0" cy="5" r="4"/>')
        out.append("</g></g>")
    out.append("</g></svg>")
    return "".join(out)


def tile_page(svg: str, refresh: int = 5, lang: Optional[str] = None) -> str:
    """The tile in a page with a transparent background, so it can be embedded anywhere."""
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="%d"><title>%s</title>'
            '<style>html,body{margin:0;background:transparent}svg{display:block;width:100%%;height:auto;'
            'max-width:1000px}</style></head><body>%s</body></html>'
            % (lang or current(), refresh, html.escape(t("map.title", lang)), svg))
