# navimow2mqtt MQTT reference

*[Nederlandse versie: MQTT.nl.md](MQTT.nl.md)*

This is the **complete list** of everything the bridge publishes and consumes on MQTT, derived from the code
(`navimow2mqtt/bridge.py`, `ha.py`, `__main__.py`). Example values were measured on a Navimow i208 AWD (mower
"Mower", zone "achtertuin"). Use this document as the contract for anything that connects to it (openHAB, Home
Assistant, Node-RED, your own dashboard).

## 1. Conventions

| Subject | Value |
|---|---|
| Base topic | `navimow` (`MQTT_BASE_TOPIC`) |
| `<name>` | the mower's name from the app, lower case; characters outside `a-z0-9_` become `_`. "Mower" becomes `mower`. If two names are equal, the second gets the last 6 characters of the device id appended. |
| Encoding | UTF-8; JSON unless stated otherwise |
| QoS when publishing | 0, except the Last Will and the sign-off of `bridge/status` (QoS 1) |
| QoS when subscribing | 0 (default) |
| Client id | `navimow2mqtt` (`MQTT_CLIENT_ID`). Before the first sign-in a one-off announcement uses `navimow2mqtt-announce`. |
| Times in JSON | ISO-8601 in UTC with seconds (`2026-10-13T20:22:32+00:00`), except `time` in `position` (epoch in milliseconds, UTC) |
| Missing fields | A field that is missing is **unknown**, not zero. |
| Never published | tokens, passwords, MQTT credentials of the Navimow cloud |

**Retained** means: the broker keeps the last message and delivers it immediately after subscribing.

## 2. Everything the bridge publishes

### 2.1 The bridge itself

| Topic | Retained | When | Content |
|---|---|---|---|
| `navimow/bridge/status` | yes | `online` on every broker connection; Last Will `offline` (QoS 1) when the connection drops; `offline` on a clean stop | `online` / `offline` |
| `navimow/bridge/auth` | yes | on every change, and after every reconnect | `ok` or `required` (signing in again is needed) |
| `navimow/bridge/login_url` | yes | as soon as `auth` goes to `required`; **empty message** as soon as it is `ok` again | Navimow sign-in link. An empty retained message clears the topic at the broker: you no longer see it. |
| `navimow/bridge/web_url` | yes | on every connection, only if `PUBLIC_URL` is set and `WEB_PORT` is not 0; also before the first sign-in | address of the sign-in page, e.g. `http://192.168.1.5:8099` |
| `navimow/bridge/login_result` | no | after every attempt via `bridge/login` | short English text, e.g. `Signed in.` or an error message. Never contains tokens. |

### 2.2 Per mower (`navimow/<name>/...`)

| Topic | Retained | When | Content |
|---|---|---|---|
| `availability` | yes | on change; again on every broker connection | `online` / `offline` (see 4) |
| `info` | yes | on every broker connection | see 3.1 |
| `state` | yes | on every status message from the cloud (push) and every HTTP poll; also again after a broker connection | see 3.2 |
| `position` | yes | on every `location` message that contains a position, zone, progress, commanded zone or delay | see 3.3 |
| `path` | yes | on every new point; and empty at the start of a new mowing session | see 3.4 |
| `location` | yes | on every `location` message from the cloud, also an empty array | raw JSON array, see 3.5 |
| `event` | no | on every `event` message from the cloud, and on a failed command | see 3.6 |
| `session` | yes | once per finished mowing session (back in `docked`, `idle` or `charging`) | see 3.7. Only if `STATS_FILE` is set (the default in the Docker image). |
| `stats` | yes | on every finished session, at the start and end of a session, on every broker connection and at the start of a new week | see 3.8. Only if `STATS_FILE` is set. |

Not published, but present in the log file (`events-*.jsonl`): the cloud channel `attributes`.

## 3. Payloads

### 3.1 `info`

```json
{"id": "2SXXX0000X0000", "name": "Mower", "model": "i208 AWD", "firmware": "0039",
 "serial": "2SXXX0000X0000", "raw": {"id": "2SXXX0000X0000", "name": "Mower", "model": "i208 AWD", "firmware": "0039"}}
```

`raw` is the unchanged device record from the Navimow API. The serial number falls back to the id.

### 3.2 `state`

```json
{"state": "mowing", "activity": "mowing", "battery": 59, "metrics": {"raw_state": "isRunning"},
 "last_update": "2026-10-13T20:15:59+00:00"}
```

| Field | Type | Meaning |
|---|---|---|
| `state` | text | `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown` |
| `activity` | text | `state` reduced to `docked`, `mowing`, `paused`, `returning`, `error` (`idle`, `charging` become `docked`; `unknown` becomes `error`) |
| `battery` | number 0 to 100 | may be missing |
| `signal_strength` | number | only if the mower sends it (not seen yet) |
| `position` | object | only if the cloud includes it in the status message (not seen yet) |
| `error` | object/text | only for an error state that the cloud includes (not seen yet) |
| `metrics.raw_state` | text | raw value, e.g. `isDocked`, `isRunning`, `isPaused`, `isDocking`, `isLifted`, `isIdel` (sic) |
| `last_update` | time | moment the bridge published, not the mower's measurement time |

Raw to `state`: `isDocked`→docked, `isIdel`/`isIdle`/`Self-Checking`→idle, `isRunning`/`isMapping`→mowing,
`isPaused`/`inSoftwareUpdate`→paused, `isDocking`→returning, `Error`/`isLifted`→error, `Offline`→unknown.

### 3.3 `position`

```json
{"x": 2.25, "y": 2.75, "distance_m": 3.55, "heading_rad": 0.403, "heading_deg": 23.1, "course_deg": 25.0,
 "vehicle_state": 4, "time": 1791922952261, "zone": 6, "target_zone": [6], "progress_pct": 4.1,
 "last_update": "2026-10-13T20:22:32+00:00"}
```

Only fields that were ever received. An empty `location` message (mower in the dock) clears nothing.

| Field | Type | Meaning |
|---|---|---|
| `x`, `y` | number | metres in a local grid, **dock roughly at (0, 0)**, not GPS; rounded to 0.25 m |
| `distance_m` | number | distance to (0, 0) |
| `heading_rad`, `heading_deg` | number | reported angle, counter-clockwise from +x; degrees 0 to 360. **Unreliable while turning** (sometimes 180° off the direction of travel) |
| `course_deg` | number | direction of the last movement (last two different positions), same convention. Missing at the start of a session |
| `vehicle_state` | number | raw number from the mower (4 seen while mowing and driving by hand); meaning unknown |
| `time` | number | measurement time, epoch **milliseconds**, UTC |
| `zone` | number | internal zone id the mower is driving in; not the same as the number in the app (id 6 = "achtertuin") |
| `target_zone` | list | commanded zone ids |
| `task_delay` | number | delay (rain/schedule); not seen yet |
| `progress_pct` | number 0 to 100 | progress (raw value 0..10000 divided by 100); matches the app |
| `last_update` | time | moment the bridge published |

### 3.4 `path`

```json
{"points": [[-0.25, 1.5], [0.25, 1.75], [0.75, 2.0]], "count": 3, "total": 3,
 "started": "2026-10-13T20:20:28+00:00", "last_update": "2026-10-13T20:21:14+00:00"}
```

The path of the running (or last) mowing session: `[x, y]` in metres. The bridge keeps up to `PATH_MAX_POINTS` (8000)
points; the message holds a **thinned copy of at most 500 points** (`count`; the first and last point stay, the rest is
spread over the whole path) and `total` is the real number. A new path starts as soon as the state goes from `docked`,
`idle` or `charging` to `mowing` (a message with `"points": [], "count": 0` comes first). Pausing and resuming clears
nothing. The bridge stores the path in `/data/trail.json`, so a restart does not lose it. Not published when
`PATH_MAX_POINTS=0`.

### 3.5 `location` (raw)

```json
[{"postureTheta": "0.435", "postureX": "2.25", "postureY": "2.0", "time": 1791922576254, "type": 1, "vehicleState": 4}]
```

A JSON array with items per `type`: 1 = position (`postureX`, `postureY` as text in metres, `postureTheta` in radians),
2 = zone/progress (`currentMowBoundary`, `currentMowProgress` 0 to 10000), 3 = commanded zone (`partitionIds`),
4 = delay (`taskDelay`). `[]` in the dock. Meant for debugging; use `position`.

### 3.6 `event`

- A message from the cloud is **passed on unchanged** (JSON object). The schema is not known; no events have been seen
  yet. The SDK lists the fields `device_id`, `timestamp`, `type`, `event`, `level`, `message`, `params`.
- If a command fails: `{"event": "command_failed", "command": "dock", "error": "..."}`.

### 3.7 `session`

One message per finished mowing session, meant to be stored and summed in openHAB (the bridge itself keeps no long
history):

```json
{"started": "2026-10-13T19:40:00+00:00", "ended": "2026-10-13T21:10:00+00:00", "week": "2026-W42",
 "duration_s": 5400, "mowing_s": 4800, "paused_s": 480, "returning_s": 120,
 "battery_start": 98, "battery_end": 41, "end_state": "docked", "progress_pct": 100.0, "zone": 6, "points": 214,
 "partial": false, "counted": true}
```

| Field | Meaning |
|---|---|
| `started`, `ended` | start and end (UTC). The end is the moment the mower was back in `docked`, `idle` or `charging`. |
| `week` | ISO week of the **end**, in the time zone of `TIMEZONE` (a week starts on Monday 00:00) |
| `duration_s` | total duration from start to end |
| `mowing_s` | time in the `mowing` state; **this is the mowing time** |
| `paused_s`, `returning_s` | time paused, time returning to the dock |
| `battery_start`, `battery_end` | battery at start and end (may be `null`) |
| `progress_pct`, `zone`, `points` | last known progress, zone id and number of path points |
| `partial` | `true` if the start or a part of the session is unknown (the bridge started in the middle of a session, or there was no status for more than 10 minutes). The unknown part is not counted. |
| `counted` | `false` if less than `STATS_MIN_SESSION_S` (60) seconds were mowed; such a session does not count in the weekly totals |

The message is retained: after subscribing you get the last session once more. In openHAB, de-duplicate on `ended`, or
do not persist on `everyUpdate`.

### 3.8 `stats`

```json
{"week": "2026-W42", "week_start": "2026-10-12", "sessions": 3, "mowing_s": 23040, "mowing_h": 6.4,
 "duration_s": 27000, "active": false, "total_sessions": 17, "total_mowing_s": 81000, "total_mowing_h": 22.5,
 "since": "2026-10-13T19:20:00+00:00",
 "weeks": [{"week": "2026-W42", "sessions": 3, "mowing_h": 6.4}, {"week": "2026-W41", "sessions": 4, "mowing_h": 8.1}],
 "previous_week": {"week": "2026-W41", "sessions": 4, "mowing_s": 29160, "mowing_h": 8.1},
 "last_session_ended": "2026-10-13T21:10:00+00:00", "last_update": "2026-10-13T21:10:01+00:00"}
```

- The "this week" numbers count only **finished** sessions; a running session is not included (`active` = `true`).
- `total_*` and `since` apply from the moment the bridge started counting. It is **not** the mower's lifetime counter;
  the official API does not provide that.
- `weeks` holds at most `STATS_WEEKS` (12) weeks, newest first. The bridge stores this in `/data/stats.json` (a few
  kilobytes, does not grow). Longer history belongs in openHAB's persistence.
- Area (m²) and blade wear are **not** available: the official API does not provide them.

## 4. Meaning of `availability`

- `offline` as soon as the bridge starts, until there is data.
- `online` on every status received (push or HTTP poll).
- `offline` if **300 s** (`OFFLINE_AFTER`) pass without any status.
- `offline` on a clean stop of the bridge.
- If the **bridge** drops, only `bridge/status` becomes `offline` (Last Will). The mower's `availability` stays at its
  last value: combine both (Home Assistant does that in the discovery).

## 5. What the bridge consumes (subscriptions)

| Topic | Content | Effect |
|---|---|---|
| `navimow/<name>/command` | `start`, `pause`, `resume`, `dock` or `stop` (case and spaces do not matter); also `start_mowing` (= `start`) and `return_to_base` (= `dock`) | Sends the command through the Navimow cloud. A `start` while the mower is `paused` becomes a `resume`. Unknown values are ignored (a log line only). There is **no confirmation**: watch `state`. If it fails, a `command_failed` appears on `event`. |
| `navimow/bridge/login` | the address from the address bar after the Navimow sign-in, or just the code | Signs the bridge in. The result is on `bridge/login_result`; afterwards `auth` becomes `ok` and `login_url` is cleared. |

Other topics are not read.

## 6. Order when connecting to the broker

1. `bridge/status` = `online`
2. per mower: subscribe to `command`, discovery (if on), `info`, `availability` (last known), `state` (if known)
3. discovery for the bridge (if on)
4. `bridge/auth` (if known)
5. `bridge/web_url` (if `PUBLIC_URL` is set)
6. subscribe to `bridge/login` and publish `bridge/login_url` again (if `required`)

**Before the very first sign-in** the bridge does not exist yet. A one-off connection (`navimow2mqtt-announce`) then sends
three retained messages: `bridge/auth` = `required`, `bridge/login_url` = the sign-in link and (if `PUBLIC_URL`)
`bridge/web_url`.

## 7. Home Assistant discovery (retained, prefix `homeassistant`)

Only with `MQTT_DISCOVERY=true` (default); the prefix is `MQTT_DISCOVERY_PREFIX`. Availability in all entities:
`bridge/status` **and** `<name>/availability` (both must be `online`). All config topics are retained.

| Config topic | unique_id | Source | Enabled by default |
|---|---|---|---|
| `homeassistant/lawn_mower/navimow2mqtt_<name>_mower/config` | `navimow2mqtt_<name>_mower` | `state` (`activity`), commands `start`, `pause`, `dock` to `<name>/command` | yes |
| `homeassistant/sensor/navimow2mqtt_<name>_battery/config` | `..._battery` | `state` `.battery`, % | yes |
| `homeassistant/sensor/navimow2mqtt_<name>_state/config` | `..._state` | `state` `.state` | yes |
| `homeassistant/sensor/navimow2mqtt_<name>_signal_strength/config` | `..._signal_strength` | `state` `.signal_strength` | no (diagnostic) |
| `homeassistant/sensor/navimow2mqtt_<name>_pos_x/config` | `..._pos_x` | `position` `.x`, m | no (diagnostic) |
| `.../navimow2mqtt_<name>_pos_y/config` | `..._pos_y` | `position` `.y`, m | no |
| `.../navimow2mqtt_<name>_pos_distance_m/config` | `..._pos_distance_m` | `position` `.distance_m`, m | no |
| `.../navimow2mqtt_<name>_pos_heading_deg/config` | `..._pos_heading_deg` | `position` `.heading_deg`, ° | no |
| `.../navimow2mqtt_<name>_pos_course_deg/config` | `..._pos_course_deg` | `position` `.course_deg`, ° | no |
| `.../navimow2mqtt_<name>_pos_progress_pct/config` | `..._pos_progress_pct` | `position` `.progress_pct`, % | no |
| `.../navimow2mqtt_<name>_pos_zone/config` | `..._pos_zone` | `position` `.zone` | no |
| `homeassistant/sensor/navimow2mqtt_<name>_stats_sessions/config` | `..._stats_sessions` | `stats` `.sessions` (only with `STATS_FILE`) | yes |
| `homeassistant/sensor/navimow2mqtt_<name>_stats_mowing_h/config` | `..._stats_mowing_h` | `stats` `.mowing_h`, hours | yes |
| `homeassistant/binary_sensor/navimow2mqtt_auth_problem/config` | `navimow2mqtt_auth_problem` | `bridge/auth` (`required` = on), device_class `problem` | yes (diagnostic) |

Device: identifier `navimow2mqtt_<name>` (manufacturer "Segway Navimow", model and firmware from `info`, serial number);
the bridge sensor belongs to the device `navimow2mqtt_bridge` ("Navimow2Mqtt"). The value templates are
`{{ value_json.<field> }}`. The path (`path`) and the raw `location` get **no** entity.

## 8. Example: see everything the bridge does

```bash
mosquitto_sub -h <broker> -t 'navimow/#' -v
mosquitto_sub -h <broker> -t 'homeassistant/+/navimow2mqtt_#' -v     # discovery
```

Sending a command:

```bash
mosquitto_pub -h <broker> -t navimow/mower/command -m dock
```
