# navimow2mqtt for openHAB: what there is and where

*[Nederlandse versie: OPENHAB.nl.md](OPENHAB.nl.md)*

A functional description for someone who knows openHAB well (MQTT binding, Main UI). You build the Things, Items, rules and
pages yourself. This document says **what the bridge offers** and **at which addresses**. The complete, exact list of all
topics and payloads is in [MQTT.md](MQTT.md); this is the summary per function.

Assumption in all examples: base topic `navimow`, mower `mower`, bridge server `192.168.1.5`. Adjust to your situation
(`MQTT_BASE_TOPIC`, the mower's name from the app in lower case, the address of the container).

![openHAB Main UI card with the map tile (photo and path blurred on purpose)](img/openhab-card.png)

## 1. How the data arrives

- The bridge talks to the Navimow cloud and puts everything on **your own MQTT broker**. openHAB only has to listen to
  that broker; no direct connection to Navimow is needed.
- Almost everything is **retained**: after subscribing you have the last value immediately.
- Values are **JSON in one message** (`state`, `position`, `path`). Pick single values out with `JSONPATH` in a
  channel's transformation.
- A field that is missing in the JSON is **unknown** (the Item stays `NULL`), not zero. This happens, for example, with
  `zone` and `course_deg` until the mower sends them for the first time.
- Availability: use `navimow/mower/availability` (`online`/`offline`) as the Thing's availability topic. If the bridge
  itself drops, `navimow/bridge/status` becomes `offline` (Last Will); the mower's availability then stays at its last
  value. So combine both if you want to see that difference.

## 2. Functions and addresses

### Mower status

| What | Topic | Value (JSONPATH where needed) |
|---|---|---|
| Status | `navimow/mower/state` | `$.state`: `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown` |
| Activity (simplified) | `navimow/mower/state` | `$.activity`: `docked`, `mowing`, `paused`, `returning`, `error` |
| Battery, % | `navimow/mower/state` | `$.battery` (may be missing) |
| Raw status of the mower | `navimow/mower/state` | `$.metrics.raw_state` (e.g. `isRunning`) |
| Online/offline | `navimow/mower/availability` | `online` / `offline` |
| Device details | `navimow/mower/info` | JSON: `name`, `model`, `firmware`, `serial` (usable for Thing properties) |

### Control

| What | Topic | Value |
|---|---|---|
| Send a command | `navimow/mower/command` (write) | `start`, `pause`, `resume`, `dock` or `stop` |

A `start` while the mower is paused automatically becomes a `resume`. There is **no confirmation**: watch `state`. If a
command fails, a message `{"event":"command_failed","command":"dock","error":"..."}` appears on `navimow/mower/event`.
Do not include the command channel in persistence.

### Position, progress and zone

Source: `navimow/mower/position` (JSON, retained). Only fields that were ever received are present.

| What | JSONPATH | Note |
|---|---|---|
| Position x, y (m) | `$.x`, `$.y` | local grid around the dock, **not GPS**, rounded to 0.25 m. Dock ≈ (0, 0) |
| Distance to the dock (m) | `$.distance_m` | |
| Direction of travel (°) | `$.course_deg` | direction of the last movement, counter-clockwise from +x. **Use this one.** |
| Reported angle (°) | `$.heading_deg` | unreliable while turning (sometimes 180° off the direction of travel) |
| Progress (%) | `$.progress_pct` | matches the app |
| Zone id | `$.zone` | internal id, not the number from the app (id 6 = "achtertuin" in this garden) |
| Commanded zone(s) | `$.target_zone` | list, e.g. `[6]` |
| Measurement time | `$.time` | epoch in **milliseconds**, UTC; use it to determine the age of a position |

A position can be old (the mower sits in the dock and then sends nothing). Judge the age with `$.time` before showing it
as current. The time the bridge published is in `$.last_update` (ISO-8601, UTC).

### Path of the mowing session

| What | Topic | Content |
|---|---|---|
| Path | `navimow/mower/path` | JSON: `points` (list of `[x, y]` in metres, at most 500), `count`, `total`, `started`, `last_update` |

The path of the running or last mowing session. For a long path this is a thinned copy of at most 500 points (`total` is the
real number; the bridge keeps up to 8000 and the map tile uses them all). A new session (state from `docked`, `idle` or
`charging` to `mowing`) starts with an empty path. Pausing and resuming clears nothing. The path is stored in
`/data/trail.json`, so a restart does not lose it. Do not persist `path` with `everyChange`; the message is large and
changes with every new point.

### Weekly statistics

| What | Topic | Value |
|---|---|---|
| Sessions this week | `navimow/mower/stats` | `$.sessions` |
| Mowing time this week (hours) | `navimow/mower/stats` | `$.mowing_h` (or `$.mowing_s` in seconds) |
| Previous week | `navimow/mower/stats` | `$.previous_week.sessions`, `$.previous_week.mowing_h` |
| List of weeks (max. 12) | `navimow/mower/stats` | `$.weeks` |
| Total since the bridge started counting | `navimow/mower/stats` | `$.total_sessions`, `$.total_mowing_h` |
| Every finished session | `navimow/mower/session` | JSON with `started`, `ended`, `duration_s`, `mowing_s`, `paused_s`, `returning_s`, `battery_start`, `battery_end`, `progress_pct`, `zone`, `partial`, `counted` |

The bridge itself only keeps the last 12 weeks (a small file in `/data`, no database). If you want a longer history,
graphs or "this month", store the values from `session` in openHAB (for example `$.mowing_s` and `$.duration_s`, at the
time `$.ended`) and sum them there. `session` is retained, so after an openHAB restart the last session arrives once more;
de-duplicate on `ended`.

Meant for a card like "This week: sessions, mowing time". **Area (m²), blade wear and the lifetime counter are not
available** in the official API. "Mowing time" is the time in the `mowing` state; pausing and returning do not count.
Sessions shorter than 60 s do not count (`STATS_MIN_SESSION_S`). A session is counted in the week in which it ended; a week
starts on Monday 00:00 in the time zone `TIMEZONE` (set `Europe/Amsterdam`).

### Signing in to Navimow (monitoring)

The Navimow sign-in has to be renewed now and then if the automatic refresh fails. The bridge reports that:

| What | Topic | Value |
|---|---|---|
| Sign-in status | `navimow/bridge/auth` | `ok` or `required` |
| Address of the sign-in page | `navimow/bridge/web_url` | only if `PUBLIC_URL` is set, e.g. `http://192.168.1.5:8099` |
| Direct Navimow sign-in link | `navimow/bridge/login_url` | only filled when `required`; empty (topic cleared) when `ok` |
| Sign in via MQTT | `navimow/bridge/login` (write) | the address from the address bar after the Navimow sign-in, or just the code |
| Outcome of that attempt | `navimow/bridge/login_result` | short text, not retained |
| The bridge itself | `navimow/bridge/status` | `online` / `offline` |

Usable as: a rule on `auth` going to `required` that sends a notification with a link to `web_url`, and a button or an
embedded page that shows the sign-in page while `auth` = `required`. **Even before the very first sign-in** `auth`,
`login_url` and `web_url` are already on the broker (the bridge sends them once); only the mower topics are missing then.

## 3. The map (the tile)

The bridge draws a small map itself and serves it on the same port as the sign-in page (default 8099):

| Address | What |
|---|---|
| `http://192.168.1.5:8099/tile.svg` | the tile as an image (SVG), for an `Image` element. `/map.svg` is the same |
| `http://192.168.1.5:8099/tile` | the same in a page with a transparent background that refreshes itself. `/map` is the same |
| `http://192.168.1.5:8099/maps` | manage the pictures (see below) |

**What you see:** a rounded tile with a dark green lawn on which the mowed path appears as light green stripes in two
alternating shades (like a mowed lawn), the charging station (a dark block with a green LED) and the mower as a small
robot pointing in the direction of travel. A new stripe starts every time the mower turns around. The LED is green,
orange when paused and red on an error; the ring around it pulses while mowing (white), returning (blue) or on an error
(red). If the position has stood still for more than 2 minutes, the robot is grey and does not pulse.

The tile is **only the image**: no card, no title, no legend and no text at all. Everything outside the rounded corners is
transparent, so it takes over the background of your dashboard. The colour scheme follows the system (light or dark) or is
fixed with `theme`.

Without a background picture the dark green is only the base of the tile, **not** the outline of your lawn: the official API
does not provide a garden map.

Parameters (after the address, separated with `&`):

| Parameter | Values | Meaning |
|---|---|---|
| `device` | mower name, e.g. `mower` | choose a mower (default: the first) |
| `w` | 240 to 1600 | width in pixels (default 640); the height follows from it (ratio 800:520) |
| `theme` | `light` or `dark` | theme; by default it follows the browser (`prefers-color-scheme`) |
| `swath` | metres, 0.05 to 2 | width of the mowed stripe (default 0.25; an estimate, the API does not give the cutting width) |
| `rotate` | `0`, `90`, `180`, `270` | rotate the grid, only without a background picture |
| `flip` | `x` or `y` | mirror, only without a background picture |
| `refresh` | seconds, at least 2 | only for `/tile` |
| `view` | name of a picture, or `auto` | use that picture as the background (see below) |
| `span` | metres, 0 to 500 | with a picture: zoom to that many metres wide around the mower. Without `span` you see the whole picture |
| `follow` | `robot` (default) or `dock` | with `span`: the window follows the mower or stays on the dock |
| `idle_span` | metres, `0` = the whole picture | the window **while the mower is standing still** (`docked`, `charging`, `idle`, unknown). With `span=12&idle_span=0` you see the whole picture at rest and the tile zooms to 12 metres as soon as it drives (`mowing`, `paused`, `returning`, `error`) |
| `fixed` | `0` | only with `idle_span`: by default the tile keeps the same dimensions when zooming in and out (the whole picture fits in the tile); with `fixed=0` the whole picture gets its own proportions |

### Background pictures (the shape of your garden)

A screenshot of the Navimow app (or another picture of your garden) can serve as the background, so the path and the robot
sit on the real shape of your garden. The mower does not provide a map itself; that is why you calibrate the picture once.

**Why calibrate:** the bridge knows positions in metres around the dock; a picture is in pixels. The conversion needs three
numbers: the place of the dock in the picture, the scale (pixels per metre) and the rotation.

![The calibration page (made-up garden)](img/calibrate.png)

1. Open `http://192.168.1.5:8099/maps`, choose a name (e.g. `garden`) and upload the picture (PNG or JPEG, at most 3 MB,
   at most 12 pictures). You land on the calibration page.
2. **Dock:** click the charging station in the picture (drag to fine-tune). You can zoom in (up to 25×) with the scroll
   wheel, the *+* and *−* buttons or two fingers; *Whole picture* zooms out again. To move the picture, switch on *Move the
   picture* (or hold Shift, or drag with two fingers). The dot and the path keep their size on screen, so you can place
   precisely and judge the alignment even when zoomed in.
3. **Scale:** enter the area the picture shows, in m² (the app shows it), and press *Estimate scale*. Or type the scale
   yourself.
4. **Rotation:** press *Auto-rotate*: the bridge looks for the angle at which the mowed path lies best inside the garden.
   Or turn it yourself until the green path lies inside the garden. A path is needed for this: first let the mower mow part
   of that zone. The path is kept (`/data/trail.json`), also after a restart. If the path was cut off or the start is
   missing, click *Load the full path of the last mowing session from the log*: the bridge reads its own log file and
   loads the whole path of the last mowing session.
5. **Zones:** enter the zone numbers this picture shows (the page lists the numbers the mower has reported so far; for
   the author that is 6 for the back garden). Leave it empty for an overview of the whole garden. Save.

Then use it with `view=NAME`, for example `/tile.svg?view=garden`. With `view=auto` the bridge picks the picture of the zone
the mower is in, and otherwise the overview (the picture without zones). With `span` you get a cut-out:
`/tile.svg?view=garden&span=12` shows 12 metres wide around the mower, and stays inside the picture.

Good to know:

- **The whole picture at rest, zoomed in while driving:** use one address with both settings, for example
  `/tile.svg?view=garden&span=12&idle_span=0`. The tile switches at every refresh: if the mower is in the dock you see
  the whole garden; if it is driving you see 12 metres around the robot. There is no smooth transition: an image that is
  refreshed every few seconds jumps to the other view.
- **One picture is enough:** an overview with `span` to zoom in. Several pictures (for example a sharp screenshot per
  zone) are only needed if zooming in on the overview is too grainy; each picture is calibrated separately.
- **Sharpness:** a picture a few hundred pixels wide gets grainy when you zoom in far.
- **Auto-rotate is an aid:** it looks for an angle at which the path lies inside the garden. If the path has a lot of
  room in the garden, several angles fit equally well (in a test a few degrees off). It works best with the path of a whole
  mowing session. Check the result and correct with the rotation if needed.
- The picture is embedded in the image (an SVG does not load external pictures), so the image is as large as the picture.
  That is no problem on your own network.
- The pages have no password; only open the port on your own network. Uploading, calibrating and deleting only work with
  special requests, so another website in your browser cannot do that.

**Embedding in Main UI.** The map is an ordinary image at a URL. According to the openHAB documentation the `oh-image-card`
has a `url` (used when no Image Item is given) and a `refreshInterval` in milliseconds; a refresh time of 5000 is enough
(the mower sends a position every 2 to 20 seconds). The server sends `Cache-Control: no-store`.

A `src` expression that builds the URL from Items (and busts the cache when the position or status changes):

```
=(['NULL','UNDEF',''].includes(items.Mower_BridgeWeb.state)?'http://192.168.1.5:8099':items.Mower_BridgeWeb.state)+'/tile.svg?view=garden&span=40&idle_span=100&theme=light&w=800&_='+encodeURIComponent([items.Mower_PositieTijd.state,items.Mower_PosX.state,items.Mower_Status.state].join('-'))
```

(`_` is only a cache-buster and is ignored by the bridge; the ternary has no spaces around `?` and `:` because a `: ` in an
unquoted YAML value is read as a key.)

## 4. Properties and limits to keep in mind

- **Update rate.** Positions arrive every 2 to 5 s while mowing and every 8 to 20 s while driving by hand. In the dock the
  mower sends nothing useful; the last position stays.
- **Resolution.** The position is rounded to 0.25 m: the path is angular and coarse.
- **Status sources.** Status normally arrives as a push from the cloud. If nothing arrives for 90 s, the bridge polls every
  60 s over HTTP. As a result `state` can sometimes lag by a few tens of seconds.
- **Availability.** The mower becomes `offline` if no status at all arrived for 300 s.
- **Only measured on a Navimow i208 AWD.** Other models may lack fields (LiDAR models probably send positions more often),
  and a field can be missing without it being an error.
- **Not available:** the garden boundary and zones as an area, GPS coordinates, the name of a zone (only an id), cutting
  height and schedules. They are not in the official API.

## 5. Handy while building

See all topics of the bridge at once:

```bash
mosquitto_sub -h <broker> -t 'navimow/#' -v
```

The bridge writes detailed logs to `/data/logs` (see the README), with every raw message from the mower. Set
`LOG_LEVEL=DEBUG` for the raw messages in the container log.
