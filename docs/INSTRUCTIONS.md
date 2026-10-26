# Brief: your own dashboard (design) and backend for navimow2mqtt

*[Nederlandse versie: INSTRUCTIONS.nl.md](INSTRUCTIONS.nl.md)*

> **Do you use openHAB (or Home Assistant)? Then you do not need this document.** Read [OPENHAB.md](OPENHAB.md) instead.
> This document is only for anyone who wants to build **their own web dashboard with their own backend**, for example
> to draw the path of a mowing session or to keep a history.

This document is a brief for building a **dashboard** (design) and a **backend** on top of `navimow2mqtt`. The bridge
itself does not change: backend and dashboard only talk to the MQTT broker, never to the Navimow cloud.

Everything under "Measured" was observed on a **Navimow i208 AWD** (zone "achtertuin", about 300 m², many corners). What was
not measured is listed under "Unknown". Do not assume anything that is not written here.

---

## 1. Goal

1. See what the mower is doing now: status, battery, progress, position, path.
2. Keep a history of mowing sessions and replay them.
3. See when signing in to Navimow again is needed, with a link to the sign-in page.

Non-goals: controlling the mower with more than start, pause, resume and dock; drawing the garden map (boundary, zones)
(it is not available, see 6); doing anything with Navimow account data.

## 2. Data contract (MQTT)

Base topic `navimow` (configurable, `MQTT_BASE_TOPIC`), `<name>` is the mower's name from the app, lower case (`mower`).
All JSON is UTF-8. **Retained** means you get the last message immediately after subscribing.

| Topic | Retained | Content |
|---|---|---|
| `navimow/bridge/status` | yes | `online` / `offline` (the bridge's Last Will) |
| `navimow/bridge/auth` | yes | `ok` / `required` |
| `navimow/bridge/login_url` | yes | link to sign in at Navimow; empty unless `auth` = `required` |
| `navimow/bridge/web_url` | yes | address of the bridge's sign-in page (only if `PUBLIC_URL` is set) |
| `navimow/bridge/login` | – | **write**: address from the address bar (or just the code) after the Navimow sign-in |
| `navimow/bridge/login_result` | no | outcome of that attempt |
| `navimow/<name>/availability` | yes | `online` / `offline` |
| `navimow/<name>/info` | yes | `{"id","name","model","firmware","serial","raw":{...}}` |
| `navimow/<name>/state` | yes | see 2.1 |
| `navimow/<name>/position` | yes | see 2.2 |
| `navimow/<name>/path` | yes | see 2.3 |
| `navimow/<name>/location` | yes | raw array from the mower; for debugging only |
| `navimow/<name>/event` | no | events from the mower and `{"event":"command_failed",...}` |
| `navimow/<name>/command` | – | **write**: `start`, `pause`, `resume`, `dock`, `stop` |

(The complete reference, including `session` and `stats`, is in [MQTT.md](MQTT.md).)

### 2.1 `state`

```json
{"state": "mowing", "activity": "mowing", "battery": 59, "metrics": {"raw_state": "isRunning"},
 "last_update": "2026-10-13T20:15:59+00:00"}
```

- `state`: `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown`.
- `activity`: the same, reduced to `docked`, `mowing`, `paused`, `returning`, `error`.
- `battery`: 0 to 100 (may be missing). `signal_strength`, `position`, `error` only come along if the mower sends them.
- `metrics.raw_state` is the raw value (`isDocked`, `isRunning`, ...). Never show it without `state`.
- `last_update` is the moment the bridge published (UTC), not the mower's measurement time.

### 2.2 `position`

```json
{"x": 2.25, "y": 2.75, "distance_m": 3.55, "heading_rad": 0.403, "heading_deg": 23.1, "course_deg": 25.0,
 "vehicle_state": 4, "time": 1791922952261, "zone": 6, "target_zone": [6], "progress_pct": 4.1,
 "last_update": "2026-10-13T20:22:32+00:00"}
```

Only fields that were ever received are present. A field that is missing is "unknown", not zero.

| Field | Meaning | Reliability |
|---|---|---|
| `x`, `y` | metres in a local grid, the **dock is roughly at (0, 0)**. Not GPS. | **Rounded to 0.25 m.** |
| `distance_m` | distance to (0, 0) | derived from x, y |
| `heading_deg` | `postureTheta` in degrees (0 to 360), counter-clockwise from +x | **Unreliable while turning**; sometimes 180° off the direction of travel. Prefer `course_deg`. |
| `course_deg` | direction in which the mower moved, from the last two different positions, same convention | Coarse for short steps (0.25 m). Missing after the start of a session until it moves. |
| `time` | time of the measurement, **epoch in milliseconds (UTC)** | measured: matches the clock |
| `zone` | internal zone id the mower is driving in | **Not the same as the number in the app.** Id 6 = "achtertuin" (measured). Linking a name is configuration, not data. |
| `target_zone` | list of commanded zone ids | |
| `progress_pct` | progress 0 to 100 | measured: matches the app (4.1 versus 4 %) |
| `vehicle_state` | raw number; 4 was seen while mowing and driving by hand | meaning unknown, do not show |

Arrival rate: 2 to 5 s while mowing, 8 to 20 s while driving by hand. In the dock the mower sends an empty array; the
bridge then leaves the last `position` in place. **So check the age** (`time`) before showing a position as current.

### 2.3 `path`

```json
{"points": [[-0.25, 1.5], [0.25, 1.75], [0.75, 2.0]], "count": 3,
 "started": "2026-10-13T20:20:28+00:00", "last_update": "2026-10-13T20:21:14+00:00"}
```

The path of the **running** mowing session, which the bridge keeps (up to `PATH_MAX_POINTS`, default 8000 points); on MQTT
there is a thinned copy of at most 500 points with the real number in `total`. It starts over as soon as the state goes
from `docked`, `idle` or `charging` to `mowing`; pausing and resuming clears nothing. After a restart of the bridge it
starts empty, even in the middle of a session: **so the backend is the only place where a complete path is kept** (see 3.3).

## 3. Backend

### 3.1 Task

A small service that:

1. subscribes to `navimow/#`, validates and stores the messages;
2. derives mowing sessions and stores their complete path;
3. provides an HTTP API with a live stream to the dashboard;
4. does not replace the bridge: it only writes to `navimow/<name>/command` and `navimow/bridge/login`.

Choose a stack that runs on a Docker server with little memory. Preferred: **Python (FastAPI) or Node (Fastify) +
SQLite**, in one container, in the same `docker-compose` as the bridge. No separate database server.

### 3.2 Data model (SQLite)

- `devices(name pk, device_id, model, firmware, serial)`
- `sessions(id pk, device, started_at, ended_at, start_battery, end_battery, end_state, last_progress_pct, zone_ids json, point_count)`
- `points(session_id, t_ms, x, y, course_deg, heading_deg, zone, progress_pct)` with an index on `(session_id, t_ms)`
- `states(device, ts, state, battery)`, only on a change of `state` or of `battery` by 5 points
- `events(ts, device, kind, payload json)` for `event`, `command_failed` and `auth` changes

Retention configurable (default: `points` 365 days, `states` 90 days). Store times in UTC.

### 3.3 Deriving sessions

Use the same rules as the bridge:

- **Start:** `state` goes from `docked`, `idle` or `charging` to `mowing`. (Not at the very first status received: then a
  session is already running and there is no known start; create a session with `started_at` = now and mark
  `partial = true`.)
- **End:** `state` goes from `mowing`, `paused` or `returning` to `docked`, `idle` or `charging`.
- **Points:** add a point from `position` if the state is `mowing`, `paused` or `returning` and `(x, y)` differs from the
  previous point. Use `time` (ms) as the time, not the receive time.
- A `position` message can come again (retained on subscribing): ignore messages whose `time` is not newer than the last point.
- Set a session that gets no points for more than 24 hours and has not ended to `ended_at = last point` and mark
  `aborted = true`.

### 3.4 HTTP API

JSON, `Content-Type: application/json`, times as ISO-8601 UTC.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/devices` | mowers with `info` |
| GET | `/api/devices/{name}/live` | last `state`, `position`, `path` of the running session, `availability` |
| GET | `/api/sessions?device=&from=&to=&limit=` | list, newest first |
| GET | `/api/sessions/{id}` | session with `points` (parameter `max_points` thins out when there are many points) |
| GET | `/api/bridge` | `{"status","auth","login_url","web_url","mqtt_connected"}` |
| GET | `/api/stream` | Server-Sent Events: `state`, `position`, `path`, `bridge`; a full snapshot first |
| POST | `/api/devices/{name}/command` | body `{"command":"start"|"pause"|"resume"|"dock"|"stop"}`; writes to MQTT |
| POST | `/api/bridge/login` | body `{"text": "<address or code>"}`; writes to `navimow/bridge/login` |
| GET | `/healthz` | liveness |

Rules:

- Only allow commands if `availability` = `online` and `bridge/status` = `online`; otherwise 409 with a clear message. A
  command confirms nothing: the dashboard waits for the new `state` through the stream.
- Never return more than is in the MQTT messages. There are no tokens, passwords or broker credentials of Navimow in this
  system, and none may end up in it.
- Dropping and re-establishing the stream connection must go smoothly (`Last-Event-ID` is not needed; a full snapshot on
  every new connection is enough).

### 3.5 Configuration (environment variables)

`MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_BASE_TOPIC` (default `navimow`), `DB_PATH` (default
`/data/dashboard.db`), `HTTP_PORT` (default 8080), `RETENTION_POINTS_DAYS`, `RETENTION_STATES_DAYS`, `ADMIN_TOKEN`
(optional; see 3.6), `ZONE_NAMES` (e.g. `6=back garden,4=front garden`).

### 3.6 Security

The API is meant for the local network. Commands and sign-in must not be open to everyone:

- If `ADMIN_TOKEN` is set, `POST` calls require `Authorization: Bearer <token>`. Without a token only `GET`, unless
  `ALLOW_COMMANDS_WITHOUT_TOKEN=true` is set explicitly.
- Do not put values from MQTT into HTML unescaped; the dashboard must render all text as text.
- CORS closed by default (same origin); if the dashboard is served by the same service that is enough.

### 3.7 Reliability

- Loss of the broker: reconnect with backoff (1 to 30 s), show as `mqtt_connected=false`.
- The bridge has a **Last Will**: `navimow/bridge/status` becomes `offline`. Show that as "bridge offline", not as
  "mower offline".
- Messages can arrive twice or again (retained); write idempotently (see 3.3).
- An invalid message is logged and skipped; never crash.

### 3.8 Tests

At least: session derivation from a series of messages (including pause, a restart in the middle of a session, duplicate
messages); de-duplication of points; API contracts; refusing commands when `offline`. Add the messages from section 2 as
test data and **record real captures** from the bridge's `/data/logs/events-*.jsonl` (every cloud message is in there with
a timestamp; see the README, "Log files").

## 4. Dashboard (design)

### 4.1 Principles

- One page, **mobile first** (360 px wide), also works on a wall-mounted tablet and on a desktop.
- Light and dark theme following the system (`prefers-color-scheme`), with a manual choice.
- Dates and times in the browser's time zone; the interface language is your choice.
- Calm and readable: one colour for "active" (mowing), one for "attention" (error, sign-in needed), the rest neutral.
  Colour is never the only carrier of information (always text or an icon too). Contrast at least WCAG AA.
- No external sources (no CDN, no fonts from outside): the dashboard must work without internet.

### 4.2 Screens

**A. Live (start screen)**

```
┌───────────────────────────────┐
│ Mower             ● Mowing    │  name, status (icon + text)
│ 59 %  ▓▓▓▓▓░░░░   4 %  progress│  battery, progress
│ Zone: back garden   ⏱ 3 min   │  zone, duration of the session
│ ┌───────────────────────────┐ │
│ │        map area           │ │  path, dock, mower with heading
│ │   ·  ╱╲_╱                 │ │
│ │  [D]  ➤                   │ │
│ └───────────────────────────┘ │
│ [Pause]  [Dock]   [Start]     │  only the sensible buttons
│ ⚠ Sign-in to Navimow needed   │  only when auth = required
└───────────────────────────────┘
```

- **Map area** (canvas or SVG): the path as a line, the dock at (0, 0) as a fixed symbol, the mower as an arrow in the
  direction of `course_deg` (fall back to no arrow, not to `heading_deg`, if `course_deg` is missing). Zoom (pinch,
  scroll), pan and "back to the mower". Grid and scale in metres; the 0.25 m resolution makes the line angular, do not
  try to smooth that with invented points.
- **Orientation** is not known (see 6). Default: +x to the right, +y up. Provide settings for **rotating (0/90/180/270°)
  and mirroring**, kept in the browser, so the user can match it to the app map.
- **Buttons**: only show what fits the `state` (e.g. Start when `docked`, Pause when `mowing`, Resume when `paused`, Dock
  when `mowing`, `paused` or `returning`). After a click: disable the button with a small "sent" note until a new `state`
  arrives, or after 15 s a clear time-out message.
- **Stale data**: if `last_update` of `state` is older than 5 minutes or the mower is `offline`, grey it out and show
  "last seen 14:32". A position older than 2 minutes during `mowing` gets a warning.
- **"Sign-in needed" banner**: at `auth = required` with a button to `web_url` (or `login_url`) and an input field to
  paste the address from the address bar (`POST /api/bridge/login`). After success the banner disappears by itself as soon
  as `auth` is `ok` again.

**B. History**

- List of mowing sessions: date, duration, battery start/end, progress, zone, end state; aborted and partial sessions
  clearly marked.
- Detail: the same map area with the whole path and a **playback bar** (play, pause, speed ×1/×4/×16, scrubbing). Colour
  the path by time or by zone.
- Export: `GET /api/sessions/{id}` as JSON and as GPX-like CSV (`t,x,y`). No coordinate conversion: it is a local grid.

**C. Settings**

Theme, map orientation, units (m), zone names (shown locally only, from `ZONE_NAMES` or entered), connection status of the
bridge and MQTT, version.

### 4.3 States that must be designed

Loading, no data ever received, mower in the dock (show the position and path of the previous session as "previous
session"), bridge offline, MQTT connection lost, sign-in needed, command failed (`command_failed`), `error` state with
readable text, a very long path (thin out to ~2000 points before drawing), several mowers (selector at the top).

### 4.4 Technique

A static single-page app without a build step or with a small, pinned build process; no framework is required but none is
forbidden. Served by the backend. Updated through the SSE stream, with automatic reconnecting. A development version must
work without a broker, with a small **demo mode** that replays recorded messages (from `events-*.jsonl`).

## 5. Acceptance criteria

1. With an empty database and a running bridge, the dashboard shows the status, the battery and (if a session is running)
   the path so far within 5 s.
2. A complete mowing session yields exactly one session with a path without duplicate points; a restart of the backend or of
   the bridge in the middle of a session loses no points the backend already had.
3. At `navimow/bridge/auth = required` the banner appears within 5 s; after a successful sign-in it disappears.
4. When the broker, the bridge or the network drops, the dashboard shows the right, different message and recovers by
   itself without reloading.
5. All `GET` screens work without internet. `POST` actions without a valid token are refused (3.6).
6. No API response or log line contains a token, password or sign-in link of Navimow, except the `login_url` that the
   bridge itself publishes and only while `auth = required`.

## 6. Unknown or not available

- **The garden map** (boundary, zones, no-go areas) is not in the official API. Do not plan a map background. (The app's
  private cloud does have it; that is a separate, unofficial integration and outside this brief. The bridge's own
  calibrated background pictures, see [OPENHAB.md](OPENHAB.md), are a workaround.)
- **Orientation of the grid** relative to the compass or the app map. It can only be matched with the settings in 4.2. A
  hint from the app: up-and-right of the dock is +x, +y, but that is not proven.
- **Zone id to name** is only known by starting each zone once and seeing which id appears.
- **The meaning of `vehicle_state`** and of types 3 and 4 (commanded zone, delay) have been seen only to a limited extent.
- **Behaviour over days** (token refresh, MQTT connections, messages that go missing) is being investigated with the
  bridge's log files. Keep in mind that the channel can go silent: show the age, do not rely on "no news is good news".
- Only measured on one model (i208 AWD). Other models may lack fields (e.g. LiDAR mowers send positions much more often).
