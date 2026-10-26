"""The pages and endpoints for the map pictures (see maps.py).

    GET  /maps                      the pictures, how to use them, and the upload form
    GET  /maps/<name>               the calibration page for one picture
    GET  /maps/<name>/image         the picture itself (for that page)
    GET  /maps/<name>/path.json     the mowed path in metres, for matching it with the picture
    POST /maps/upload?name=<name>   the picture as the request body (image/png or image/jpeg)
    POST /maps/<name>/calibrate     JSON: {"dock": [u, v], "scale": .., "rotation": .., "flip": .., "zones": [..]}
    POST /maps/<name>/delete

Writes only accept content types that a web page on another site cannot send without our consent
(application/json, image/png, image/jpeg), so they cannot be triggered from elsewhere in the browser.
There is no password: like the rest of these pages this is meant for your own network.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote

from . import rebuild, ui
from .i18n import current, t
from .maps import MAX_IMAGE_BYTES, MapError, MapStore

NAME = r"[a-z0-9][a-z0-9_-]{0,39}"
IMAGE_TYPES = ("image/png", "image/jpeg")

# The strings the calibration script needs; {x} placeholders are filled in by the script.
JS_KEYS = ("cal.saved", "cal.error", "cal.fit.ok", "cal.fit.nopath", "cal.fit.noarea", "cal.dock.unset",
           "cal.scale.est", "cal.mask.fail", "cal.path.some", "cal.path.none", "cal.loadpath.ok",
           "cal.loadpath.fail")


def _json(h, obj: Any, status: int = 200) -> None:
    h._send(json.dumps(obj), status, "application/json")


def _err(h, message: str, status: int) -> bool:
    """Reply with a JSON error; always True so a route can `return _err(...)`."""
    _json(h, {"error": message}, status)
    return True


def _script_json(obj: Any) -> str:
    """JSON that is safe inside a <script> element."""
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------------------- pages
def list_page(store: MapStore, lang: Optional[str] = None) -> str:
    esc = html.escape
    rows = []
    for name in store.names():
        v = store.get(name)
        ready = v["dock"] is not None and v["scale"] is not None
        zones = ", ".join(str(z) for z in v["zones"]) or t("maps.overview", lang)
        rows.append(
            "<tr><td><b>%s</b></td><td>%d × %d</td><td>%s</td><td>%s</td>"
            '<td class="row"><a class="btn" href="/maps/%s">%s</a>'
            '<button class="btn" type="button" data-delete="%s">%s</button></td></tr>'
            % (esc(name), v["w"], v["h"],
               esc(t("maps.calibrated" if ready else "maps.not_calibrated", lang)), esc(zones),
               quote(name), esc(t("maps.btn.calibrate", lang)), esc(name), esc(t("maps.btn.delete", lang))))
    table = ('<table><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th></th></tr></thead><tbody>%s</tbody>'
             "</table>" % (esc(t("maps.col.name", lang)), esc(t("maps.col.size", lang)),
                           esc(t("maps.col.status", lang)), esc(t("maps.col.zones", lang)), "".join(rows))
             if rows else '<p class="sub">%s</p>' % esc(t("maps.empty", lang)))
    seen = store.zones_seen()
    zones_line = (t("maps.zones_seen", lang, ids=", ".join(str(z) for z in seen)) if seen
                  else t("maps.zones_none", lang))
    example = "/tile.svg?view=%s&span=12" % (store.names()[0] if store.names() else "NAME")
    cfg = {"fail": t("maps.upload.fail", lang), "done": t("maps.upload.done", lang),
           "confirm": t("maps.confirm_delete", lang)}
    body = """<main class="card wide">
<header><div><h1>Navimow2Mqtt</h1><p class="sub">%(title)s</p></div><a class="btn" href="/">%(back)s</a></header>
<p class="sub" style="margin-top:1rem">%(intro)s</p>
%(table)s
<p class="sub">%(zones)s</p>
<h2 style="font-size:1.1rem;margin-top:1.6rem">%(up_title)s</h2>
<form id="up"><div class="cal" style="margin-top:0">
<div><label for="n">%(up_name)s</label><input id="n" type="text" maxlength="40" required pattern="[a-z0-9][a-z0-9_-]*"></div>
<div><label for="f">%(up_file)s</label><input id="f" type="file" accept="image/png,image/jpeg" required></div>
</div><button class="btn primary" type="submit" style="margin-top:.8rem">%(up_btn)s</button>
<p id="s" class="status"></p></form>
<h2 style="font-size:1.1rem;margin-top:1.6rem">%(use_title)s</h2>
<p class="sub">%(use)s</p>
</main>
<script type="application/json" id="cfg">%(cfg)s</script>
<script>
(function(){
var C=JSON.parse(document.getElementById('cfg').textContent), st=document.getElementById('s');
function say(m,bad){st.textContent=m;st.className='status '+(bad?'bad':'ok');}
document.getElementById('up').addEventListener('submit',function(e){
 e.preventDefault();
 var name=document.getElementById('n').value, file=document.getElementById('f').files[0];
 if(!file)return;
 fetch('/maps/upload?name='+encodeURIComponent(name),{method:'POST',headers:{'Content-Type':file.type},body:file})
  .then(function(r){return r.text().then(function(t){var j;try{j=JSON.parse(t);}catch(e){j={error:(t||r.statusText||String(r.status)).slice(0,200)};}return {ok:r.ok,j:j};});})
  .then(function(x){
   if(x.ok){say(C.done);location.href='/maps/'+encodeURIComponent(name);}
   else say(C.fail.replace('{err}',x.j.error||'?'),true);})
  .catch(function(err){say(C.fail.replace('{err}',String(err)),true);});
});
document.querySelectorAll('[data-delete]').forEach(function(b){b.addEventListener('click',function(){
 if(!confirm(C.confirm))return;
 fetch('/maps/'+encodeURIComponent(b.getAttribute('data-delete'))+'/delete',{method:'POST',
  headers:{'Content-Type':'application/json'},body:'{}'}).then(function(){location.reload();});
});});
})();
</script>""" % {
        "title": esc(t("maps.title", lang)), "back": esc(t("maps.back", lang)), "intro": esc(t("maps.intro", lang)),
        "table": table, "zones": esc(zones_line), "up_title": esc(t("maps.upload.title", lang)),
        "up_name": esc(t("maps.upload.name", lang)), "up_file": esc(t("maps.upload.file", lang)),
        "up_btn": esc(t("maps.upload.btn", lang)), "use_title": esc(t("maps.usage.title", lang)),
        "use": esc(t("maps.usage.text", lang, example="\x00")).replace("\x00", "<code>%s</code>" % esc(example)),
        "cfg": _script_json(cfg)}
    return ui.page(body, t("maps.title", lang), lang or current(), ui.CSS_MAPS)


CAL_SCRIPT = r"""
(function(){
'use strict';
var C = JSON.parse(document.getElementById('cfg').textContent), T = C.t;
function fmt(s, o){ return s.replace(/\{(\w+)\}/g, function(m, k){ return k in o ? o[k] : m; }); }
// a reply that is not JSON (a plain "not found", a proxy page) becomes an error message, not a SyntaxError
function reply(r){ return r.text().then(function(t){ var j; try { j = JSON.parse(t); } catch (e) { j = { error: (t || r.statusText || String(r.status)).slice(0, 200) }; } return { ok: r.ok, j: j }; }); }
var svg = document.getElementById('stage'), ov = document.getElementById('ov');
var el = function(id){ return document.getElementById(id); };
var st = { dock: null, scale: 12, rot: 0, flip: false };
if (C.view && C.view.dock) { st.dock = C.view.dock.slice(); st.scale = C.view.scale; st.rot = C.view.rotation; st.flip = !!C.view.flip; }
else { st.dock = [C.w / 2, C.h / 2]; st.docked = false; }
var path = { points: [], x: null, y: null };
var mask = null;

function toImage(x, y){
  if (st.flip) x = -x;
  var th = st.rot * Math.PI / 180, c = Math.cos(th), s = Math.sin(th), vx = x, vy = -y;
  return [st.dock[0] + st.scale * (vx * c - vy * s), st.dock[1] + st.scale * (vx * s + vy * c)];
}
function mk(tag, attrs){ var n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }

function draw(){
  while (ov.firstChild) ov.removeChild(ov.firstChild);
  var u = unit();                       // picture pixels per screen pixel: markers keep their size on screen when zoomed
  if (path.points.length > 1){
    var pts = path.points.map(function(p){ var q = toImage(p[0], p[1]); return q[0].toFixed(1) + ',' + q[1].toFixed(1); }).join(' ');
    ov.appendChild(mk('polyline', { points: pts, fill: 'none', stroke: '#1f9d4d', 'stroke-opacity': '.85',
      'stroke-width': Math.max(0.25 * st.scale, 2 * u).toFixed(2), 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  ov.appendChild(mk('circle', { cx: st.dock[0], cy: st.dock[1], r: 9 * u, fill: '#16211b', stroke: '#fff', 'stroke-width': 2.5 * u }));
  ov.appendChild(mk('circle', { cx: st.dock[0], cy: st.dock[1], r: 3 * u, fill: '#4fd17a' }));
  if (path.x !== null){ var q = toImage(path.x, path.y);
    ov.appendChild(mk('circle', { cx: q[0], cy: q[1], r: 6 * u, fill: '#f2f2ee', stroke: '#16211b', 'stroke-width': 2 * u })); }
  el('dock').value = Math.round(st.dock[0]) + ', ' + Math.round(st.dock[1]);
  el('scaleN').value = (+st.scale).toFixed(2); el('scaleR').value = st.scale;
  el('rotN').value = (+st.rot).toFixed(1); el('rotR').value = st.rot;
  el('flip').checked = st.flip;
}
function say(msg, kind){ var s = el('status'); s.textContent = msg; s.className = 'status' + (kind ? ' ' + kind : ''); }

// --- zooming and panning: the viewBox of the stage (picture pixels; the picture never leaves the stage)
var view = { x: 0, y: 0, w: C.w, h: C.h }, MAXZOOM = 25;
function unit(){ return view.w / (svg.getBoundingClientRect().width || C.w); }      // picture pixels per screen pixel
function setView(x, y, w){
  w = Math.max(C.w / MAXZOOM, Math.min(C.w, w)); var h = w * C.h / C.w;
  view.w = w; view.h = h; view.x = Math.max(0, Math.min(C.w - w, x)); view.y = Math.max(0, Math.min(C.h - h, y));
  svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h); draw();
}
function toSvg(cx, cy){ var pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy; var p = pt.matrixTransform(svg.getScreenCTM().inverse()); return [p.x, p.y]; }
function zoomAt(cx, cy, factor){                // the point under (cx, cy) stays where it is
  var p = toSvg(cx, cy), w = Math.max(C.w / MAXZOOM, Math.min(C.w, view.w / factor)), r = w / view.w;
  setView(p[0] - (p[0] - view.x) * r, p[1] - (p[1] - view.y) * r, w);
}
function zoomCentre(f){ var r = svg.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, f); }
el('zoomIn').addEventListener('click', function(){ zoomCentre(1.6); });
el('zoomOut').addEventListener('click', function(){ zoomCentre(1 / 1.6); });
el('zoomFit').addEventListener('click', function(){ setView(0, 0, C.w); });
var panMode = false;
el('pan').addEventListener('click', function(){
  panMode = !panMode; this.setAttribute('aria-pressed', panMode ? 'true' : 'false'); this.classList.toggle('primary', panMode); });
svg.addEventListener('wheel', function(e){ e.preventDefault(); zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.25 : 1 / 1.25); }, { passive: false });
window.addEventListener('resize', function(){ draw(); });

// --- pointers: one finger or the mouse places the dock (or pans: in pan mode, with Shift, or with the middle/right
// button); two fingers pinch to zoom and drag to pan
var pointers = {}, mode = null, last = null, pinch = null;
function count(){ return Object.keys(pointers).length; }
function placeDock(e){ st.dock = toSvg(e.clientX, e.clientY); st.docked = true; draw(); }
function panBy(dx, dy){ var k = unit(); setView(view.x - dx * k, view.y - dy * k, view.w); }
function twoFingers(){ var a = Object.keys(pointers).map(function(k){ return pointers[k]; });
  return { d: Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]), mx: (a[0][0] + a[1][0]) / 2, my: (a[0][1] + a[1][1]) / 2 }; }
svg.addEventListener('pointerdown', function(e){
  try { svg.setPointerCapture(e.pointerId); } catch (err) {}
  pointers[e.pointerId] = [e.clientX, e.clientY];
  if (count() === 2){ pinch = twoFingers(); mode = 'pinch'; return; }
  last = [e.clientX, e.clientY];
  mode = (panMode || e.shiftKey || e.button === 1 || e.button === 2) ? 'pan' : 'dock';
  if (mode === 'dock') placeDock(e);
});
svg.addEventListener('pointermove', function(e){
  if (!(e.pointerId in pointers)) return;
  pointers[e.pointerId] = [e.clientX, e.clientY];
  if (mode === 'pinch' && count() === 2){
    var n = twoFingers();
    if (pinch.d > 0 && n.d > 0) zoomAt(n.mx, n.my, n.d / pinch.d);
    panBy(n.mx - pinch.mx, n.my - pinch.my);
    pinch = n;
  } else if (mode === 'pan'){ panBy(e.clientX - last[0], e.clientY - last[1]); last = [e.clientX, e.clientY]; }
  else if (mode === 'dock'){ placeDock(e); }
});
function release(e){ delete pointers[e.pointerId]; if (count() < 2) mode = null; }
svg.addEventListener('pointerup', release); svg.addEventListener('pointercancel', release);
svg.addEventListener('contextmenu', function(e){ e.preventDefault(); });

function num(v, d){ var x = parseFloat(v); return isFinite(x) ? x : d; }
el('scaleR').addEventListener('input', function(){ st.scale = num(this.value, st.scale); draw(); });
el('scaleN').addEventListener('change', function(){ st.scale = Math.max(0.5, num(this.value, st.scale)); draw(); });
el('rotR').addEventListener('input', function(){ st.rot = num(this.value, st.rot); draw(); });
el('rotN').addEventListener('change', function(){ st.rot = num(this.value, st.rot); draw(); });
el('flip').addEventListener('change', function(){ st.flip = this.checked; draw(); });

// --- reading the picture: which pixels belong to the garden? (everything darker than the background)
function buildMask(img){
  var f = Math.min(1, 400 / Math.max(img.naturalWidth, img.naturalHeight));
  var w = Math.max(1, Math.round(img.naturalWidth * f)), h = Math.max(1, Math.round(img.naturalHeight * f));
  var cv = document.createElement('canvas'); cv.width = w; cv.height = h;
  var cx = cv.getContext('2d'); cx.drawImage(img, 0, 0, w, h);
  var d = cx.getImageData(0, 0, w, h).data, L = new Uint8Array(w * h), hist = new Array(256).fill(0), i;
  for (i = 0; i < w * h; i++){
    var a = d[4 * i + 3];
    L[i] = a < 128 ? 255 : Math.round(0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2]);
    if (L[i] >= 215) hist[L[i]]++;
  }
  var bg = 255, best = 0; for (i = 215; i < 256; i++) if (hist[i] > best){ best = hist[i]; bg = i; }
  var thr = bg - 18, data = new Uint8Array(w * h), count = 0;
  for (i = 0; i < w * h; i++){ if (L[i] < thr){ data[i] = 1; count++; } }
  return { w: w, h: h, f: f, data: data, areaPx: count / (f * f) };
}
function inside(u, v){
  if (!mask) return false;
  var x = Math.floor(u * mask.f), y = Math.floor(v * mask.f);
  return x >= 0 && y >= 0 && x < mask.w && y < mask.h && mask.data[y * mask.w + x] === 1;
}
function score(sample){
  var n = 0; for (var i = 0; i < sample.length; i++){ var q = toImage(sample[i][0], sample[i][1]); if (inside(q[0], q[1])) n++; }
  return n / sample.length;
}
function subsample(pts, max){ if (pts.length <= max) return pts; var out = [], step = pts.length / max;
  for (var i = 0; i < max; i++) out.push(pts[Math.floor(i * step)]); return out; }

el('estimate').addEventListener('click', function(){
  var area = num(el('area').value, 0);
  if (!mask) return say(T['cal.mask.fail'], 'bad');
  if (!(area > 0)) return say(T['cal.fit.noarea'], 'bad');
  st.scale = Math.sqrt(mask.areaPx / area); draw();
  say(fmt(T['cal.scale.est'], { s: st.scale.toFixed(1) }), 'ok');
});
el('auto').addEventListener('click', function(){
  if (!mask) return say(T['cal.mask.fail'], 'bad');
  if (path.points.length < 2) return say(T['cal.fit.nopath'], 'bad');
  if (st.docked === false) return say(T['cal.dock.unset'], 'bad');
  var sample = subsample(path.points, 250), step = 2, scores = [], r;
  for (r = -180; r < 180; r += step){ st.rot = r; scores.push(score(sample)); }
  var n = scores.length, best = Math.max.apply(null, scores), bi = scores.indexOf(best), tol = 0.01, lo = bi, hi = bi;
  // Many angles fit about equally well when the path has room in the garden: take the middle of the stretch
  // around the best one, not the first, so a small error in the dock position does not tip the result.
  while (hi - lo + 1 < n && scores[((lo - 1) % n + n) % n] >= best - tol) lo--;
  while (hi - lo + 1 < n && scores[(hi + 1) % n] >= best - tol) hi++;
  var rot = -180 + (lo + hi) / 2 * step;
  st.rot = ((rot + 180) % 360 + 360) % 360 - 180;
  var final = score(sample); draw();
  say(fmt(T['cal.fit.ok'], { pct: Math.round(final * 100) }), final > 0.7 ? 'ok' : 'bad');
});
el('save').addEventListener('click', function(){
  var zones = el('zones').value.split(',').map(function(z){ return z.trim(); }).filter(Boolean);
  fetch('/maps/' + encodeURIComponent(C.name) + '/calibrate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dock: st.dock, scale: st.scale, rotation: st.rot, flip: st.flip, zones: zones }) })
    .then(reply)
    .then(function(x){ if (x.ok) say(T['cal.saved'], 'ok'); else say(fmt(T['cal.error'], { err: x.j.error || '?' }), 'bad'); })
    .catch(function(e){ say(fmt(T['cal.error'], { err: String(e) }), 'bad'); });
});

el('zones').value = (C.view && C.view.zones || []).join(', ');
draw();
var img = new Image();
img.onload = function(){ try { mask = buildMask(img); } catch (e) { mask = null; } };
img.src = '/maps/' + encodeURIComponent(C.name) + '/image';
function loadPath(){
  return fetch('/maps/' + encodeURIComponent(C.name) + '/path.json').then(function(r){ return r.json(); }).then(function(j){
    path = { points: j.points || [], x: j.x, y: j.y };
    el('pathinfo').textContent = path.points.length > 1 ? fmt(T['cal.path.some'], { n: path.points.length }) : T['cal.path.none'];
    window.__navimowCal = { st: st, path: function(){ return path; }, mask: function(){ return mask; }, draw: draw };
    draw();
  });
}
loadPath();
// the path in memory can be cut off: let the bridge read the whole path of the last session from its log
el('loadpath').addEventListener('click', function(){
  fetch('/maps/trail/rebuild', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(reply)
    .then(function(x){
      if (!x.ok) return say(fmt(T['cal.loadpath.fail'], { err: x.j.error || '?' }), 'bad');
      return loadPath().then(function(){ say(fmt(T['cal.loadpath.ok'], { n: x.j.trail.total }), 'ok'); });
    })
    .catch(function(e){ say(fmt(T['cal.loadpath.fail'], { err: String(e) }), 'bad'); });
});
})();
"""


def calibrate_page(store: MapStore, name: str, lang: Optional[str] = None) -> Optional[str]:
    v = store.get(name)
    if not v:
        return None
    esc = html.escape
    cfg = {"name": name, "w": v["w"], "h": v["h"],
           "view": {"dock": v["dock"], "scale": v["scale"], "rotation": v["rotation"], "flip": v["flip"],
                    "zones": v["zones"]} if v["dock"] is not None else None,
           "t": {k: t(k, lang) for k in JS_KEYS}}
    seen = store.zones_seen()
    seen_line = (t("maps.zones_seen", lang, ids=", ".join(str(z) for z in seen)) if seen
                 else t("maps.zones_none", lang))
    body = """<main class="card wide">
<header><div><h1>Navimow2Mqtt</h1><p class="sub">%(title)s</p></div><a class="btn" href="/maps">%(back)s</a></header>
<div class="cal">
<div><div class="stage"><svg id="stage" viewBox="0 0 %(w)d %(h)d"><image href="/maps/%(q)s/image" x="0" y="0" width="%(w)d" height="%(h)d"/><g id="ov"></g></svg></div>
<div class="row" style="margin-top:.6rem"><button id="zoomIn" class="btn" type="button" aria-label="%(z_in)s">+</button><button id="zoomOut" class="btn" type="button" aria-label="%(z_out)s">−</button><button id="zoomFit" class="btn" type="button">%(z_fit)s</button><button id="pan" class="btn" type="button" aria-pressed="false">%(z_pan)s</button></div>
<p class="small">%(z_hint)s</p></div>
<div class="panel">
<ol class="steps"><li>%(s1)s</li><li>%(s2)s</li><li>%(s3)s</li><li>%(s4)s</li></ol>
<label for="dock">%(l_dock)s</label><input id="dock" type="text" readonly>
<label for="scaleN">%(l_scale)s</label><input id="scaleN" type="number" step="0.1" min="0.5"><input id="scaleR" type="range" min="2" max="120" step="0.1">
<label for="area">%(l_area)s</label><div class="row"><input id="area" type="number" step="1" min="1" style="flex:1"><button id="estimate" class="btn" type="button">%(b_est)s</button></div>
<label for="rotN">%(l_rot)s</label><input id="rotN" type="number" step="0.5"><input id="rotR" type="range" min="-180" max="180" step="0.5">
<button id="auto" class="btn" type="button">%(b_auto)s</button>
<label><input id="flip" type="checkbox">%(l_flip)s</label>
<label for="zones">%(l_zones)s</label><input id="zones" type="text" placeholder="6"><p class="small">%(seen)s</p>
<button id="save" class="btn primary" type="button">%(b_save)s</button>
<p id="status" class="status"></p><p id="pathinfo" class="small"></p>
<button id="loadpath" class="btn" type="button">%(b_load)s</button>
</div></div></main>
<script type="application/json" id="cfg">%(cfg)s</script><script>%(script)s</script>""" % {
        "title": esc(t("cal.title", lang, name=name)), "back": esc(t("maps.back", lang)), "w": v["w"], "h": v["h"],
        "q": quote(name), "s1": t("cal.step1", lang), "s2": t("cal.step2", lang), "s3": t("cal.step3", lang),
        "s4": t("cal.step4", lang), "l_dock": esc(t("cal.label.dock", lang)), "l_scale": esc(t("cal.label.scale", lang)),
        "l_area": esc(t("cal.label.area", lang)), "b_est": esc(t("cal.btn.estimate", lang)),
        "l_rot": esc(t("cal.label.rotation", lang)), "b_auto": esc(t("cal.btn.auto", lang)),
        "l_flip": esc(t("cal.label.flip", lang)), "l_zones": esc(t("cal.label.zones", lang)),
        "z_in": esc(t("cal.zoom.in", lang)), "z_out": esc(t("cal.zoom.out", lang)),
        "z_fit": esc(t("cal.zoom.fit", lang)), "z_pan": esc(t("cal.zoom.pan", lang)),
        "z_hint": esc(t("cal.zoom.hint", lang)),
        "seen": esc(seen_line), "b_save": esc(t("cal.btn.save", lang)), "b_load": esc(t("cal.btn.loadpath", lang)),
        "cfg": _script_json(cfg),
        "script": CAL_SCRIPT}
    return ui.page(body, t("cal.title", lang, name=name), lang or current(), ui.CSS_MAPS)


# ---------------------------------------------------------------------------- routes
def _read_body(h, limit: int) -> Optional[bytes]:
    try:
        length = int(h.headers.get("Content-Length", ""))
    except ValueError:
        return None
    if length < 0 or length > limit:
        return None
    return h.rfile.read(length)


def handle_get(h, url, store: Optional[MapStore], provider: Optional[Callable]) -> bool:
    """Serve a GET under /maps; False when the path is not ours."""
    if not url.path.startswith("/maps"):
        return False
    if store is None:
        h._send("not found", 404, "text/plain; charset=utf-8")
        return True
    if url.path in ("/maps", "/maps/"):
        h._send(list_page(store))
        return True
    m = re.fullmatch(r"/maps/(%s)(/image|/path\.json)?" % NAME, url.path)
    if not m:
        h._send("not found", 404, "text/plain; charset=utf-8")
        return True
    name, sub = m.group(1), m.group(2)
    if sub == "/image":
        got = store.image(name)
        if not got:
            h._send("not found", 404, "text/plain; charset=utf-8")
        else:
            h._send(got[0], 200, got[1], {"X-Content-Type-Options": "nosniff"})
    elif sub == "/path.json":
        data = (provider(None) if provider else None) or {}
        _json(h, {"points": data.get("points", []), "x": data.get("x"), "y": data.get("y"),
                  "zone": data.get("zone")})
    else:
        page = calibrate_page(store, name)
        if page is None:
            h._send("not found", 404, "text/plain; charset=utf-8")
        else:
            h._send(page)
    return True


def _rebuild_trail(h) -> bool:
    """POST /maps/trail/rebuild: the running bridge loads the path of the last mowing session from its log."""
    rebuilder = getattr(h.server, "trail_rebuilder", None)
    if rebuilder is None:
        h._send("not found", 404, "text/plain; charset=utf-8")
        return True
    if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
        return _err(h, "Content-Type must be application/json.", 415)
    try:
        _json(h, {"ok": True, "trail": rebuilder()})
    except rebuild.RebuildError as exc:
        return _err(h, str(exc), 400)
    return True


def handle_post(h, url, store: Optional[MapStore]) -> bool:
    """Serve a POST under /maps; False when the path is not ours."""
    if not url.path.startswith("/maps/"):
        return False
    if url.path == "/maps/trail/rebuild":
        return _rebuild_trail(h)
    if store is None:
        h._send("not found", 404, "text/plain; charset=utf-8")
        return True
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    try:
        if url.path == "/maps/upload":
            if ctype not in IMAGE_TYPES:
                return _err(h, "Content-Type must be image/png or image/jpeg.", 415)
            data = _read_body(h, MAX_IMAGE_BYTES + 1)
            if data is None:
                return _err(h, "The picture is missing or larger than %d MB." % (MAX_IMAGE_BYTES // (1024 * 1024)), 413)
            name = (parse_qs(url.query).get("name") or [""])[0]
            _json(h, {"ok": True, "view": store.put_image(name, data)})
            return True
        m = re.fullmatch(r"/maps/(%s)/(calibrate|delete)" % NAME, url.path)
        if not m:
            h._send("not found", 404, "text/plain; charset=utf-8")
            return True
        if ctype != "application/json":
            return _err(h, "Content-Type must be application/json.", 415)
        body = _read_body(h, 16384)
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return _err(h, "The request body must be a JSON object.", 400)
        name, action = m.group(1), m.group(2)
        if action == "delete":
            if not store.delete(name):
                return _err(h, "There is no picture called %r." % name, 404)
            _json(h, {"ok": True})
        else:
            _json(h, {"ok": True, "view": store.calibrate(
                name, payload.get("dock"), payload.get("scale"), payload.get("rotation"),
                payload.get("flip", False), payload.get("zones"))})
    except MapError as exc:
        _json(h, {"error": str(exc)}, 400)
    return True
