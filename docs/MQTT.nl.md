# MQTT-referentie navimow2mqtt

*[English version: MQTT.md](MQTT.md)*

Dit is de **volledige lijst** van alles wat de bridge op MQTT publiceert en afneemt, afgeleid van de code
(`navimow2mqtt/bridge.py`, `ha.py`, `__main__.py`). Waarden onder "Voorbeeld" zijn echt gemeten op een Navimow
i208 AWD (maaier "Mower", zone "achtertuin"). Gebruik dit document als contract voor alles wat erop aansluit
(openHAB, Home Assistant, Node-RED, een eigen dashboard).

## 1. Afspraken

| Onderwerp | Waarde |
|---|---|
| Basis-topic | `navimow` (`MQTT_BASE_TOPIC`) |
| `<naam>` | maaiernaam uit de app, kleine letters; tekens buiten `a-z0-9_` worden `_`. "Mower" wordt `mower`. Zijn twee namen gelijk, dan krijgt de tweede de laatste 6 tekens van het apparaat-id erachter. |
| Codering | UTF-8; JSON, tenzij anders vermeld |
| QoS bij publiceren | 0, behalve de Last Will en de afmelding van `bridge/status` (QoS 1) |
| QoS bij abonneren | 0 (standaard) |
| Client-id | `navimow2mqtt` (`MQTT_CLIENT_ID`). Vóór de eerste login gebruikt een eenmalige melding `navimow2mqtt-announce`. |
| Tijden in JSON | ISO-8601 in UTC met seconden (`2026-10-13T20:22:32+00:00`), behalve `time` in `position` (epoch in milliseconden, UTC) |
| Ontbrekende velden | Een veld dat ontbreekt, is **onbekend**, geen nul. |
| Wat nooit gepubliceerd wordt | tokens, wachtwoorden, MQTT-gegevens van de Navimow-cloud |

**Retained** betekent: de broker bewaart het laatste bericht en levert dat direct na het abonneren.

## 2. Alles wat de bridge publiceert

### 2.1 De bridge zelf

| Topic | Retained | Wanneer | Inhoud |
|---|---|---|---|
| `navimow/bridge/status` | ja | `online` bij elke verbinding met de broker; Last Will `offline` (QoS 1) bij wegvallen; `offline` bij een nette stop | `online` / `offline` |
| `navimow/bridge/auth` | ja | bij elke wijziging, en na elke reconnect | `ok` of `required` (opnieuw inloggen nodig) |
| `navimow/bridge/login_url` | ja | zodra `auth` naar `required` gaat; **leeg bericht** zodra het weer `ok` is | Navimow-loginlink. Een leeg retained bericht wist het topic bij de broker: je ziet het dan niet meer. |
| `navimow/bridge/web_url` | ja | bij elke verbinding, alleen als `PUBLIC_URL` is ingesteld en `WEB_PORT` niet 0 is; ook vóór de eerste login | adres van de loginpagina, bijv. `http://192.168.1.5:8099` |
| `navimow/bridge/login_result` | nee | na elke poging via `bridge/login` | korte Engelse tekst, bijv. `Signed in.` of een foutmelding. Bevat nooit tokens. |

### 2.2 Per maaier (`navimow/<naam>/...`)

| Topic | Retained | Wanneer | Inhoud |
|---|---|---|---|
| `availability` | ja | wijziging; opnieuw bij elke verbinding met de broker | `online` / `offline` (zie 4) |
| `info` | ja | bij elke verbinding met de broker | zie 3.1 |
| `state` | ja | bij elk statusbericht uit de cloud (push) en bij elke HTTP-poll; ook opnieuw na een brokerverbinding | zie 3.2 |
| `position` | ja | bij elk `location`-bericht dat een positie, zone, voortgang, opgedragen zone of vertraging bevat | zie 3.3 |
| `path` | ja | bij elk nieuw punt; en leeg bij het begin van een nieuwe maaibeurt | zie 3.4 |
| `location` | ja | bij elk `location`-bericht uit de cloud, ook een lege array | ruwe JSON-array, zie 3.5 |
| `event` | nee | bij elk `event`-bericht uit de cloud, en bij een mislukt commando | zie 3.6 |
| `session` | ja | één keer per afgeronde maaibeurt (terug in `docked`, `idle` of `charging`) | zie 3.7. Alleen als `STATS_FILE` is gezet (standaard in de Docker-image). |
| `stats` | ja | bij elke afgeronde maaibeurt, bij het begin en eind van een beurt, bij elke verbinding met de broker en bij het begin van een nieuwe week | zie 3.8. Alleen als `STATS_FILE` is gezet. |

Niet gepubliceerd, maar wel in het logbestand (`events-*.jsonl`): het cloudkanaal `attributes`.

## 3. Payloads

### 3.1 `info`

```json
{"id": "2SXXX0000X0000", "name": "Mower", "model": "i208 AWD", "firmware": "0039",
 "serial": "2SXXX0000X0000", "raw": {"id": "2SXXX0000X0000", "name": "Mower", "model": "i208 AWD", "firmware": "0039"}}
```

`raw` is het ongewijzigde apparaatrecord uit de Navimow-API. Het serienummer valt terug op het id.

### 3.2 `state`

```json
{"state": "mowing", "activity": "mowing", "battery": 59, "metrics": {"raw_state": "isRunning"},
 "last_update": "2026-10-13T20:15:59+00:00"}
```

| Veld | Type | Betekenis |
|---|---|---|
| `state` | tekst | `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown` |
| `activity` | tekst | `state` teruggebracht tot `docked`, `mowing`, `paused`, `returning`, `error` (`idle`, `charging` worden `docked`; `unknown` wordt `error`) |
| `battery` | getal 0 tot 100 | kan ontbreken |
| `signal_strength` | getal | alleen als de maaier het stuurt (nog niet gezien) |
| `position` | object | alleen als de cloud het in het statusbericht meestuurt (nog niet gezien) |
| `error` | object/tekst | alleen bij een foutstatus die de cloud meestuurt (nog niet gezien) |
| `metrics.raw_state` | tekst | ruwe waarde, bijv. `isDocked`, `isRunning`, `isPaused`, `isDocking`, `isLifted`, `isIdel` (sic) |
| `last_update` | tijd | moment waarop de bridge publiceerde, niet de meettijd van de maaier |

Vertaling ruw naar `state`: `isDocked`→docked, `isIdel`/`isIdle`/`Self-Checking`→idle, `isRunning`/`isMapping`→mowing,
`isPaused`/`inSoftwareUpdate`→paused, `isDocking`→returning, `Error`/`isLifted`→error, `Offline`→unknown.

### 3.3 `position`

```json
{"x": 2.25, "y": 2.75, "distance_m": 3.55, "heading_rad": 0.403, "heading_deg": 23.1, "course_deg": 25.0,
 "vehicle_state": 4, "time": 1791922952261, "zone": 6, "target_zone": [6], "progress_pct": 4.1,
 "last_update": "2026-10-13T20:22:32+00:00"}
```

Alleen velden die ooit zijn ontvangen. Een leeg `location`-bericht (maaier in het dock) wist niets.

| Veld | Type | Betekenis |
|---|---|---|
| `x`, `y` | getal | meters in een lokaal raster, **dock ongeveer in (0, 0)**, geen GPS; afgerond op 0,25 m |
| `distance_m` | getal | afstand tot (0, 0) |
| `heading_rad`, `heading_deg` | getal | gemelde hoek, tegen de klok in vanaf +x; graden 0 tot 360. **Onbetrouwbaar tijdens keren** (soms 180° naast de rijrichting) |
| `course_deg` | getal | richting van de laatste verplaatsing (laatste twee verschillende posities), zelfde conventie. Ontbreekt aan het begin van een beurt |
| `vehicle_state` | getal | ruw getal van de maaier (4 gezien tijdens maaien en handmatig rijden); betekenis onbekend |
| `time` | getal | meettijd, epoch **milliseconden**, UTC |
| `zone` | getal | intern zone-ID waarin de maaier rijdt; niet gelijk aan het nummer in de app (ID 6 = "achtertuin") |
| `target_zone` | lijst | opgedragen zone-ID's |
| `task_delay` | getal | vertraging (regen/schema); nog niet gezien |
| `progress_pct` | getal 0 tot 100 | voortgang (ruwe waarde 0..10000 gedeeld door 100); komt overeen met de app |
| `last_update` | tijd | publicatiemoment van de bridge |

### 3.4 `path`

```json
{"points": [[-0.25, 1.5], [0.25, 1.75], [0.75, 2.0]], "count": 3, "total": 3,
 "started": "2026-10-13T20:20:28+00:00", "last_update": "2026-10-13T20:21:14+00:00"}
```

Het pad van de lopende (of laatste) maaibeurt: `[x, y]` in meters. De bridge bewaart tot `PATH_MAX_POINTS` (8000) punten; in
het bericht staat een **uitgedunde kopie van hooguit 500 punten** (`count`, het eerste en het laatste punt blijven, de rest
verdeeld over het hele pad) en `total` is het echte aantal. Een nieuw
pad begint zodra de status van `docked`, `idle` of `charging` naar `mowing` gaat (dan komt er eerst een bericht met
`"points": [], "count": 0`). Pauzeren en hervatten wist niets. De bridge bewaart het pad in `/data/trail.json`,
dus een herstart verliest het niet. Niet gepubliceerd als `PATH_MAX_POINTS=0`.

### 3.5 `location` (ruw)

```json
[{"postureTheta": "0.435", "postureX": "2.25", "postureY": "2.0", "time": 1791922576254, "type": 1, "vehicleState": 4}]
```

Een JSON-array met items per `type`: 1 = positie (`postureX`, `postureY` als tekst in meters, `postureTheta` in
radialen), 2 = zone/voortgang (`currentMowBoundary`, `currentMowProgress` 0 tot 10000), 3 = opgedragen zone
(`partitionIds`), 4 = vertraging (`taskDelay`). `[]` in het dock. Bedoeld voor debuggen; gebruik `position`.

### 3.6 `event`

- Een bericht uit de cloud wordt **ongewijzigd doorgegeven** (JSON-object). Het schema is niet bekend; er zijn nog geen
  events gezien. De SDK noemt de velden `device_id`, `timestamp`, `type`, `event`, `level`, `message`, `params`.
- Mislukt een commando: `{"event": "command_failed", "command": "dock", "error": "..."}`.

### 3.7 `session`

Eén bericht per afgeronde maaibeurt, bedoeld om in openHAB te bewaren en op te tellen (de bridge zelf houdt geen
lange geschiedenis bij):

```json
{"started": "2026-10-13T19:40:00+00:00", "ended": "2026-10-13T21:10:00+00:00", "week": "2026-W42",
 "duration_s": 5400, "mowing_s": 4800, "paused_s": 480, "returning_s": 120,
 "battery_start": 98, "battery_end": 41, "end_state": "docked", "progress_pct": 100.0, "zone": 6, "points": 214,
 "partial": false, "counted": true}
```

| Veld | Betekenis |
|---|---|
| `started`, `ended` | begin en eind (UTC). Het eind is het moment waarop de maaier weer in `docked`, `idle` of `charging` kwam. |
| `week` | ISO-week van het **einde**, in de tijdzone van `TIMEZONE` (week begint op maandag 00:00) |
| `duration_s` | totale duur van begin tot eind |
| `mowing_s` | tijd in de status `mowing`; **dit is de maaitijd** |
| `paused_s`, `returning_s` | tijd gepauzeerd, tijd terug naar het dock |
| `battery_start`, `battery_end` | batterij bij begin en eind (kan `null` zijn) |
| `progress_pct`, `zone`, `points` | laatst bekende voortgang, zone-ID en aantal padpunten |
| `partial` | `true` als het begin of een stuk van de beurt onbekend is (de bridge startte midden in een beurt, of er was meer dan 10 minuten geen status). Het onbekende stuk is niet meegeteld. |
| `counted` | `false` als er minder dan `STATS_MIN_SESSION_S` (60) seconden gemaaid is; zo'n beurt telt niet mee in de weektotalen |

Het bericht is retained: na het abonneren krijg je de laatste beurt nog een keer. Dedupliceer in openHAB dus op
`ended`, of bewaar niet bij `everyUpdate`.

### 3.8 `stats`

```json
{"week": "2026-W42", "week_start": "2026-10-12", "sessions": 3, "mowing_s": 23040, "mowing_h": 6.4,
 "duration_s": 27000, "active": false, "total_sessions": 17, "total_mowing_s": 81000, "total_mowing_h": 22.5,
 "since": "2026-10-13T19:20:00+00:00",
 "weeks": [{"week": "2026-W42", "sessions": 3, "mowing_h": 6.4}, {"week": "2026-W41", "sessions": 4, "mowing_h": 8.1}],
 "previous_week": {"week": "2026-W41", "sessions": 4, "mowing_s": 29160, "mowing_h": 8.1},
 "last_session_ended": "2026-10-13T21:10:00+00:00", "last_update": "2026-10-13T21:10:01+00:00"}
```

- De cijfers van "deze week" tellen alleen **afgeronde** beurten; een lopende beurt zit er niet bij (`active` = `true`).
- `total_*` en `since` gelden vanaf het moment dat de bridge begon te tellen. Het is **niet** de levenslange teller van de
  maaier; die geeft de officiële API niet.
- `weeks` bevat maximaal `STATS_WEEKS` (12) weken, nieuwste eerst. De bridge bewaart dit in `/data/stats.json` (een paar
  kilobyte, groeit niet). Langere geschiedenis hoort in de persistentie van openHAB.
- Oppervlak (m²) en slijtage van de messen zijn er **niet**: die geeft de officiële API niet.

## 4. Betekenis van `availability`

- `offline` zodra de bridge start, tot er gegevens zijn.
- `online` bij elke ontvangen status (push of HTTP-poll).
- `offline` als er **300 s** (`OFFLINE_AFTER`) geen enkele status binnenkwam.
- `offline` bij een nette stop van de bridge.
- Valt de **bridge** weg, dan wordt alleen `bridge/status` `offline` (Last Will). De `availability` van de maaier
  blijft dan op de laatste waarde staan: combineer beide (Home Assistant doet dat in de discovery).

## 5. Wat de bridge afneemt (abonnementen)

| Topic | Inhoud | Effect |
|---|---|---|
| `navimow/<naam>/command` | `start`, `pause`, `resume`, `dock` of `stop` (hoofdletters en spaties maken niet uit); ook `start_mowing` (= `start`) en `return_to_base` (= `dock`) | Stuurt het commando via de Navimow-cloud. Een `start` terwijl de maaier `paused` is, wordt een `resume`. Onbekende waarden worden genegeerd (alleen een logregel). Er komt **geen bevestiging**: kijk naar `state`. Mislukt het, dan komt er een `command_failed` op `event`. |
| `navimow/bridge/login` | het adres uit de adresbalk na de Navimow-login, of alleen de code | Logt de bridge in. Het resultaat staat op `bridge/login_result`; daarna wordt `auth` `ok` en `login_url` gewist. |

Andere topics worden niet gelezen.

## 6. Volgorde bij verbinden met de broker

1. `bridge/status` = `online`
2. per maaier: abonneren op `command`, discovery (indien aan), `info`, `availability` (laatste bekende), `state` (indien bekend)
3. discovery voor de bridge (indien aan)
4. `bridge/auth` (indien bekend)
5. `bridge/web_url` (indien `PUBLIC_URL` gezet)
6. abonneren op `bridge/login` en `bridge/login_url` opnieuw publiceren (indien `required`)

**Vóór de allereerste login** bestaat de bridge nog niet. Dan stuurt een eenmalige verbinding (`navimow2mqtt-announce`)
drie retained berichten: `bridge/auth` = `required`, `bridge/login_url` = de loginlink en (indien `PUBLIC_URL`) `bridge/web_url`.

## 7. Home Assistant discovery (retained, voorvoegsel `homeassistant`)

Alleen met `MQTT_DISCOVERY=true` (standaard); het voorvoegsel is `MQTT_DISCOVERY_PREFIX`. Beschikbaarheid in
alle entiteiten: `bridge/status` **en** `<naam>/availability` (beide moeten `online` zijn). Alle config-topics zijn
retained.

| Config-topic | unique_id | Bron | Standaard aan |
|---|---|---|---|
| `homeassistant/lawn_mower/navimow2mqtt_<naam>_mower/config` | `navimow2mqtt_<naam>_mower` | `state` (`activity`), commando's `start`, `pause`, `dock` naar `<naam>/command` | ja |
| `homeassistant/sensor/navimow2mqtt_<naam>_battery/config` | `..._battery` | `state` `.battery`, % | ja |
| `homeassistant/sensor/navimow2mqtt_<naam>_state/config` | `..._state` | `state` `.state` | ja |
| `homeassistant/sensor/navimow2mqtt_<naam>_signal_strength/config` | `..._signal_strength` | `state` `.signal_strength` | nee (diagnostisch) |
| `homeassistant/sensor/navimow2mqtt_<naam>_pos_x/config` | `..._pos_x` | `position` `.x`, m | nee (diagnostisch) |
| `.../navimow2mqtt_<naam>_pos_y/config` | `..._pos_y` | `position` `.y`, m | nee |
| `.../navimow2mqtt_<naam>_pos_distance_m/config` | `..._pos_distance_m` | `position` `.distance_m`, m | nee |
| `.../navimow2mqtt_<naam>_pos_heading_deg/config` | `..._pos_heading_deg` | `position` `.heading_deg`, ° | nee |
| `.../navimow2mqtt_<naam>_pos_course_deg/config` | `..._pos_course_deg` | `position` `.course_deg`, ° | nee |
| `.../navimow2mqtt_<naam>_pos_progress_pct/config` | `..._pos_progress_pct` | `position` `.progress_pct`, % | nee |
| `.../navimow2mqtt_<naam>_pos_zone/config` | `..._pos_zone` | `position` `.zone` | nee |
| `homeassistant/sensor/navimow2mqtt_<naam>_stats_sessions/config` | `..._stats_sessions` | `stats` `.sessions` (alleen met `STATS_FILE`) | ja |
| `homeassistant/sensor/navimow2mqtt_<naam>_stats_mowing_h/config` | `..._stats_mowing_h` | `stats` `.mowing_h`, uren | ja |
| `homeassistant/binary_sensor/navimow2mqtt_auth_problem/config` | `navimow2mqtt_auth_problem` | `bridge/auth` (`required` = aan), device_class `problem` | ja (diagnostisch) |

Apparaat: identifier `navimow2mqtt_<naam>` (fabrikant "Segway Navimow", model en firmware uit `info`, serienummer);
de bridge-sensor hoort bij het apparaat `navimow2mqtt_bridge` ("Navimow2Mqtt"). De waarde-sjablonen zijn
`{{ value_json.<veld> }}`. Het pad (`path`) en de ruwe `location` krijgen **geen** entiteit.

## 8. Voorbeeld: alles zien wat de bridge doet

```bash
mosquitto_sub -h <broker> -t 'navimow/#' -v
mosquitto_sub -h <broker> -t 'homeassistant/+/navimow2mqtt_#' -v     # discovery
```

Een commando sturen:

```bash
mosquitto_pub -h <broker> -t navimow/mower/command -m dock
```
