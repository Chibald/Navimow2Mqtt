"""Shared look of the pages the bridge serves: the sign-in page and the map-picture pages.

A white rounded card on a light green-grey ground, grey secondary text, pills, rounded tiles and
buttons, in light and dark. No external fonts or scripts: everything works without internet.
"""
from __future__ import annotations

import html

CSS = """:root{--bg:#eef0ed;--card:#fff;--line:#e3e6e1;--text:#1b201c;--muted:#6b736c;--tile:#f3f5f2;--accent:#3d7a54;
 --on-accent:#fff;--ok-bg:#e1eee5;--ok-fg:#2b6b46;--bad-bg:#fbe6e3;--bad-fg:#a33a2f;--warn-bg:#fbf0d9;--warn-fg:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#121512;--card:#1b1f1b;--line:#2a302b;--text:#e8ece8;--muted:#8f988f;
 --tile:#232823;--accent:#5aa879;--on-accent:#0f1a13;--ok-bg:#22372a;--ok-fg:#8fd1a6;--bad-bg:#3a2320;--bad-fg:#f0a59d;
 --warn-bg:#3a3018;--warn-fg:#e8c675}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:2.5rem 1rem;
 background:var(--bg);color:var(--text);font:15px/1.45 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.card{width:100%;max-width:26rem;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:1.4rem;
 box-shadow:0 1px 2px rgba(0,0,0,.04),0 12px 28px rgba(0,0,0,.05)}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem}
h1{margin:0;font-size:1.05rem;font-weight:600;letter-spacing:-.01em}
h2{margin:1.5rem 0 .15rem;font-size:1.45rem;font-weight:700;letter-spacing:-.02em}
.sub{margin:.1rem 0 0;color:var(--muted);font-size:.85rem}
.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .7rem;border-radius:999px;font-size:.82rem;
 font-weight:600;white-space:nowrap}
.pill i{width:.5rem;height:.5rem;border-radius:50%;background:currentColor}
.pill.ok{background:var(--ok-bg);color:var(--ok-fg)}.pill.bad{background:var(--bad-bg);color:var(--bad-fg)}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:1.2rem 0}
.tile{background:var(--tile);border-radius:16px;padding:.8rem .9rem}
.tile span{display:block;color:var(--muted);font-size:.78rem}
.tile b{display:block;margin-top:.15rem;font-size:1.3rem;font-weight:700;letter-spacing:-.01em}
.note{margin:1.1rem 0 0;padding:.75rem .9rem;border-radius:14px;font-size:.88rem}
.note.ok{background:var(--ok-bg);color:var(--ok-fg)}.note.bad{background:var(--bad-bg);color:var(--bad-fg)}
.note.warn{background:var(--warn-bg);color:var(--warn-fg)}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
.btn{display:flex;align-items:center;justify-content:center;min-height:2.9rem;padding:.55rem 1rem;border-radius:14px;
 border:1px solid var(--line);background:var(--card);color:var(--text);font:inherit;font-weight:600;
 text-align:center;text-decoration:none;cursor:pointer}
.btn:hover{background:var(--tile)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.btn.primary:hover{filter:brightness(1.06);background:var(--accent)}
.btn:focus-visible,summary:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
details{margin-top:1.4rem}
summary{cursor:pointer;color:var(--muted);font-size:.78rem;text-align:center;list-style:none}
summary::-webkit-details-marker{display:none}
summary:hover{color:var(--text)}
details[open] summary{margin-bottom:.8rem}
.help{background:var(--tile);border-radius:16px;padding:.8rem .9rem}
.help p{margin:0;color:var(--muted);font-size:.84rem}
ol{margin:.8rem 0 .6rem;padding-left:1.2rem;color:var(--muted);font-size:.86rem}
ol li{margin:.3rem 0}ol b{color:var(--text)}
a{color:var(--accent)}
code{background:var(--card);padding:.05rem .3rem;border-radius:6px;font-size:.84em}
textarea{width:100%;min-height:5rem;padding:.65rem .75rem;border-radius:12px;border:1px solid var(--line);
 background:var(--card);color:var(--text);font:inherit;font-size:.88rem;resize:vertical}
form .btn{width:100%;margin-top:.6rem}
"""

# extra rules for the pages with forms, tables and the calibration stage
CSS_MAPS = """
.wide{max-width:60rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th{color:var(--muted);font-weight:600;font-size:.78rem;text-align:left;padding:.4rem .5rem}
td{padding:.55rem .5rem;border-top:1px solid var(--line);vertical-align:middle}
td .btn,.row .btn{min-height:2.2rem;padding:.2rem .8rem;font-size:.85rem;display:inline-flex;width:auto}
.row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
label{display:block;color:var(--muted);font-size:.78rem;margin:.7rem 0 .2rem}
input[type=text],input[type=number],input[type=file]{width:100%;padding:.55rem .7rem;border-radius:12px;
 border:1px solid var(--line);background:var(--card);color:var(--text);font:inherit;font-size:.9rem}
input[type=range]{width:100%;accent-color:var(--accent)}
input[type=checkbox]{accent-color:var(--accent);margin-right:.4rem}
.cal{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(16rem,1fr);gap:1.2rem;align-items:start;margin-top:1rem}
@media (max-width:760px){.cal{grid-template-columns:1fr}}
.stage{background:var(--tile);border-radius:16px;overflow:hidden;line-height:0;touch-action:none;user-select:none}
.stage svg{display:block;width:100%;height:auto;cursor:crosshair}
.panel .btn{width:100%;margin-top:.5rem}
.status{min-height:1.3rem;margin-top:.6rem;font-size:.86rem;color:var(--muted)}
.status.ok{color:var(--ok-fg)}.status.bad{color:var(--bad-fg)}
ol.steps{margin:.5rem 0 0;padding-left:1.2rem}
.small{font-size:.8rem;color:var(--muted)}
"""


def page(body: str, title: str, lang: str, css: str = "", head: str = "") -> str:
    """A full HTML page in the shared look; `body` is trusted markup."""
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
            "<style>%s%s</style>%s</head><body>%s</body></html>"
            % (html.escape(lang), html.escape(title), CSS, css, head, body))
