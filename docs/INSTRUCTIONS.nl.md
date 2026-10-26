# Instructie: eigen dashboard (design) en backend voor navimow2mqtt

*[English version: INSTRUCTIONS.md](INSTRUCTIONS.md)*

> **Gebruik je openHAB (of Home Assistant)? Dan heb je dit document niet nodig.** Lees dan
> [OPENHAB.md](OPENHAB.nl.md). Dit document is alleen voor wie een **eigen webdashboard met een eigen backend** wil
> bouwen, bijvoorbeeld om het pad van de maaibeurt te tekenen of een geschiedenis te bewaren.

Dit document is een opdracht voor het bouwen van een **dashboard** (design) en een **backend** bovenop
`navimow2mqtt`. De bridge zelf verandert niet: backend en dashboard praten alleen met de MQTT-broker, nooit met
de Navimow-cloud.

Alles in "Gemeten" is waargenomen op een **Navimow i208 AWD** (zone "achtertuin", about 300 m², veel hoekjes). Wat niet
gemeten is, staat onder "Onbekend". Neem niets aan dat hier niet staat.

---

## 1. Doel

1. Zien wat de maaier nu doet: status, batterij, voortgang, positie, pad.
2. Een geschiedenis van maaibeurten bewaren en terugspelen.
3. Zien wanneer opnieuw inloggen bij Navimow nodig is, met een link naar de loginpagina.

Niet-doelen: de maaier bedienen met meer dan start, pauze, hervat en dock; de tuinkaart (grens, zones) tekenen
(die is niet beschikbaar, zie 6); iets doen met Navimow-accountgegevens.

## 2. Datacontract (MQTT)

Basis-topic `navimow` (instelbaar, `MQTT_BASE_TOPIC`), `<naam>` is de maaiernaam uit de app, kleine letters
(`mower`). Alle JSON is UTF-8. **Retained** betekent: je krijgt het laatste bericht direct na het abonneren.

| Topic | Retained | Inhoud |
|---|---|---|
| `navimow/bridge/status` | ja | `online` / `offline` (Last Will van de bridge) |
| `navimow/bridge/auth` | ja | `ok` / `required` |
| `navimow/bridge/login_url` | ja | link om bij Navimow in te loggen; leeg tenzij `auth` = `required` |
| `navimow/bridge/web_url` | ja | adres van de loginpagina van de bridge (alleen als `PUBLIC_URL` is ingesteld) |
| `navimow/bridge/login` | – | **schrijven**: adres uit de adresbalk (of alleen de code) na de Navimow-login |
| `navimow/bridge/login_result` | nee | uitkomst van die poging |
| `navimow/<naam>/availability` | ja | `online` / `offline` |
| `navimow/<naam>/info` | ja | `{"id","name","model","firmware","serial","raw":{...}}` |
| `navimow/<naam>/state` | ja | zie 2.1 |
| `navimow/<naam>/position` | ja | zie 2.2 |
| `navimow/<naam>/path` | ja | zie 2.3 |
| `navimow/<naam>/location` | ja | ruwe array van de maaier; alleen voor debugging |
| `navimow/<naam>/event` | nee | events van de maaier en `{"event":"command_failed",...}` |
| `navimow/<naam>/command` | – | **schrijven**: `start`, `pause`, `resume`, `dock`, `stop` |

### 2.1 `state`

```json
{"state": "mowing", "activity": "mowing", "battery": 59, "metrics": {"raw_state": "isRunning"},
 "last_update": "2026-10-13T20:15:59+00:00"}
```

- `state`: `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown`.
- `activity`: dezelfde, teruggebracht tot `docked`, `mowing`, `paused`, `returning`, `error`.
- `battery`: 0 tot 100 (kan ontbreken). `signal_strength`, `position`, `error` komen alleen mee als de maaier ze stuurt.
- `metrics.raw_state` is de ruwe waarde (`isDocked`, `isRunning`, ...). Toon die nooit zonder `state`.
- `last_update` is het moment waarop de bridge publiceerde (UTC), niet de meetijd van de maaier.

### 2.2 `position`

```json
{"x": 2.25, "y": 2.75, "distance_m": 3.55, "heading_rad": 0.403, "heading_deg": 23.1, "course_deg": 25.0,
 "vehicle_state": 4, "time": 1791922952261, "zone": 6, "target_zone": [6], "progress_pct": 4.1,
 "last_update": "2026-10-13T20:22:32+00:00"}
```

Alleen velden die ooit zijn ontvangen zijn aanwezig. Een veld dat ontbreekt, is "onbekend", geen nul.

| Veld | Betekenis | Betrouwbaarheid |
|---|---|---|
| `x`, `y` | meters in een lokaal raster, het **dock ligt ongeveer in (0, 0)**. Geen GPS. | **Afgerond op 0,25 m.** |
| `distance_m` | afstand tot (0, 0) | afgeleid van x, y |
| `heading_deg` | `postureTheta` in graden (0 tot 360), tegen de klok in vanaf +x | **Onbetrouwbaar tijdens keren**; soms 180° naast de rijrichting. Gebruik liever `course_deg`. |
| `course_deg` | richting waarin de maaier zich verplaatste, uit de laatste twee verschillende posities, zelfde conventie | Grof bij korte stapjes (0,25 m). Ontbreekt na het begin van een beurt tot hij beweegt. |
| `time` | tijdstip van de meting, **epoch in milliseconden (UTC)** | gemeten: klopt met de klok |
| `zone` | intern zone-ID waarin de maaier rijdt | **Niet gelijk aan het nummer in de app.** ID 6 = "achtertuin" (gemeten). Naam-koppeling is configuratie, geen data. |
| `target_zone` | lijst met zone-ID's die opgedragen zijn | |
| `progress_pct` | voortgang 0 tot 100 | gemeten: komt overeen met de app (4,1 tegen 4 %) |
| `vehicle_state` | ruw getal; 4 zag je tijdens maaien en handmatig rijden | betekenis onbekend, niet tonen |

Aankomstfrequentie: 2 tot 5 s tijdens maaien, 8 tot 20 s tijdens handmatig rijden. In het dock stuurt de maaier een
lege array; de bridge laat dan de laatste `position` staan. **Controleer dus de leeftijd** (`time`) voordat je een
positie als actueel toont.

### 2.3 `path`

```json
{"points": [[-0.25, 1.5], [0.25, 1.75], [0.75, 2.0]], "count": 3,
 "started": "2026-10-13T20:20:28+00:00", "last_update": "2026-10-13T20:21:14+00:00"}
```

Het pad van de **lopende** maaibeurt, dat de bridge bewaart (tot `PATH_MAX_POINTS`, standaard 8000 punten); op MQTT staat een uitgedunde kopie van hooguit 500
punten met het echte aantal in `total`. Het
begint opnieuw zodra de status van `docked`, `idle` of `charging` naar `mowing` gaat; pauzeren en hervatten wist
niets. Na een herstart van de bridge begint het leeg, ook midden in een beurt: **de backend is dus de enige plek
waar een volledig pad bewaard blijft** (zie 3.3).

## 3. Backend

### 3.1 Taak

Een kleine service die:

1. zich abonneert op `navimow/#`, de berichten valideert en bewaart;
2. maaibeurten ("sessies") afleidt en hun volledige pad opslaat;
3. een HTTP-API met live-stream levert aan het dashboard;
4. de bridge niet vervangt: hij schrijft alleen naar `navimow/<naam>/command` en `navimow/bridge/login`.

Kies een stack die op een Docker-server met weinig geheugen draait. Voorkeur: **Python (FastAPI) of Node
(Fastify) + SQLite**, in één container, in dezelfde `docker-compose` als de bridge. Geen aparte database-server.

### 3.2 Gegevensmodel (SQLite)

- `devices(name pk, device_id, model, firmware, serial)`
- `sessions(id pk, device, started_at, ended_at, start_battery, end_battery, end_state, last_progress_pct, zone_ids json, point_count)`
- `points(session_id, t_ms, x, y, course_deg, heading_deg, zone, progress_pct)` met index op `(session_id, t_ms)`
- `states(device, ts, state, battery)`, alleen bij verandering van `state` of van `battery` met 5 punten
- `events(ts, device, kind, payload json)` voor `event`, `command_failed` en `auth`-wijzigingen

Bewaartijd instelbaar (standaard: `points` 365 dagen, `states` 90 dagen). Tijden opslaan in UTC.

### 3.3 Sessies afleiden

Gebruik dezelfde regels als de bridge:

- **Start:** `state` gaat van `docked`, `idle` of `charging` naar `mowing`. (Niet bij de allereerste ontvangen
  status: dan is een beurt al bezig en is er geen bekend begin; maak een sessie met `started_at` = nu en markeer
  `partial = true`.)
- **Einde:** `state` gaat van `mowing`, `paused` of `returning` naar `docked`, `idle` of `charging`.
- **Punten:** voeg een punt toe uit `position` als de status `mowing`, `paused` of `returning` is en `(x, y)`
  verschilt van het vorige punt. Gebruik `time` (ms) als tijd, niet de ontvangsttijd.
- Een `position`-bericht kan opnieuw komen (retained bij abonneren): negeer berichten met een `time` die niet
  nieuwer is dan het laatste punt.
- Zet een sessie die langer dan 24 uur geen punten krijgt en nog niet beëindigd is, op `ended_at = laatste punt`
  en markeer `aborted = true`.

### 3.4 HTTP-API

JSON, `Content-Type: application/json`, tijden als ISO-8601 UTC.

| Methode | Pad | Doel |
|---|---|---|
| GET | `/api/devices` | maaiers met `info` |
| GET | `/api/devices/{naam}/live` | laatste `state`, `position`, `path` van de lopende beurt, `availability` |
| GET | `/api/sessions?device=&from=&to=&limit=` | lijst, nieuwste eerst |
| GET | `/api/sessions/{id}` | sessie met `points` (parameter `max_points` ontdubbelt bij veel punten) |
| GET | `/api/bridge` | `{"status","auth","login_url","web_url","mqtt_connected"}` |
| GET | `/api/stream` | Server-Sent Events: `state`, `position`, `path`, `bridge`; eerst een volledige momentopname |
| POST | `/api/devices/{naam}/command` | body `{"command":"start"|"pause"|"resume"|"dock"|"stop"}`; schrijft naar MQTT |
| POST | `/api/bridge/login` | body `{"text": "<adres of code>"}`; schrijft naar `navimow/bridge/login` |
| GET | `/healthz` | liveness |

Regels:

- Commando's alleen toestaan als `availability` = `online` en `bridge/status` = `online`; anders 409 met een
  duidelijke melding. Een commando bevestigt niets: het dashboard wacht op de nieuwe `state` via de stream.
- Geef nooit meer terug dan in de MQTT-berichten staat. Er zijn geen tokens, wachtwoorden of broker-gegevens van
  Navimow in dit systeem en die mogen er ook niet in terechtkomen.
- Stream-verbinding verbreken en weer opzetten moet vlekkeloos gaan (`Last-Event-ID` is niet nodig; een volledige
  momentopname bij elke nieuwe verbinding is genoeg).

### 3.5 Configuratie (omgevingsvariabelen)

`MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_BASE_TOPIC` (standaard `navimow`),
`DB_PATH` (standaard `/data/dashboard.db`), `HTTP_PORT` (standaard 8080), `RETENTION_POINTS_DAYS`,
`RETENTION_STATES_DAYS`, `ADMIN_TOKEN` (optioneel; zie 3.6), `ZONE_NAMES` (bijv. `6=achtertuin,4=voortuin`).

### 3.6 Beveiliging

De API is bedoeld voor het lokale netwerk. Commando's en login mogen niet open staan voor iedereen:

- Is `ADMIN_TOKEN` gezet, dan vragen `POST`-aanroepen `Authorization: Bearer <token>`. Zonder token alleen
  `GET`, tenzij `ALLOW_COMMANDS_WITHOUT_TOKEN=true` expliciet is gezet.
- Zet geen waarden uit MQTT ongeëscaped in HTML; het dashboard moet alle tekst als tekst renderen.
- CORS standaard dicht (zelfde origin); als het dashboard door dezelfde service wordt geserveerd is dat voldoende.

### 3.7 Betrouwbaarheid

- Verlies van de broker: opnieuw verbinden met backoff (1 tot 30 s), tonen als `mqtt_connected=false`.
- De bridge heeft een **Last Will**: `navimow/bridge/status` wordt `offline`. Toon dat als "bridge offline", niet
  als "maaier offline".
- Berichten kunnen dubbel of opnieuw (retained) binnenkomen; schrijf idempotent (zie 3.3).
- Een ongeldig bericht wordt gelogd en overgeslagen; nooit crashen.

### 3.8 Tests

Minimaal: sessie-afleiding uit een reeks berichten (inclusief pauze, herstart midden in een beurt, dubbele
berichten); ontdubbelen van punten; API-contracten; weigeren van commando's bij `offline`. Voeg als testdata de
berichten uit sectie 2 toe en **leg echte opnames vast** uit `/data/logs/events-*.jsonl` van de bridge (daar staat
elk cloudbericht met tijdstempel in; zie de README, "Logbestanden").

## 4. Dashboard (design)

### 4.1 Uitgangspunten

- Eén pagina, **mobiel eerst** (360 px breed), werkt ook op een tablet aan de muur en op een desktop.
- Licht en donker thema volgens het systeem (`prefers-color-scheme`), met een handmatige keuze.
- Nederlandse interface; datums en tijden in de tijdzone van de browser.
- Rustig en leesbaar: één kleur voor "actief" (maaien), één voor "aandacht" (fout, inloggen nodig), de rest neutraal.
  Kleur is nooit de enige drager van informatie (altijd ook tekst of een icoon). Contrast minimaal WCAG AA.
- Geen externe bronnen (geen CDN, geen fonts van buiten): het dashboard moet zonder internet werken.

### 4.2 Schermen

**A. Live (startscherm)**

```
┌───────────────────────────────┐
│ Mower             ● Maait     │  naam, status (icoon + tekst)
│ 59 %  ▓▓▓▓▓░░░░   4 %  voortg.│  batterij, voortgang
│ Zone: achtertuin   ⏱ 3 min    │  zone, duur van de beurt
│ ┌───────────────────────────┐ │
│ │        kaartvlak          │ │  pad, dock, maaier met koers
│ │   ·  ╱╲_╱                 │ │
│ │  [D]  ➤                   │ │
│ └───────────────────────────┘ │
│ [Pauze]  [Dock]   [Start]     │  alleen de zinvolle knoppen
│ ⚠ Inloggen bij Navimow nodig  │  alleen bij auth = required
└───────────────────────────────┘
```

- **Kaartvlak** (canvas of SVG): het pad als lijn, het dock in (0, 0) als vast symbool, de maaier als pijl in de
  richting van `course_deg` (val terug op geen pijl, niet op `heading_deg`, als `course_deg` ontbreekt).
  Zoom (knijpen, scrollen), verschuiven en "terug naar de maaier". Raster en schaal in meters; de resolutie van
  0,25 m maakt de lijn hoekig, probeer dat niet glad te maken met verzonnen punten.
- **Oriëntatie** is niet bekend (zie 6). Standaard: +x naar rechts, +y omhoog. Geef instellingen voor
  **draaien (0/90/180/270°) en spiegelen**, bewaard in de browser, zodat de gebruiker het kan afstemmen op de
  app-kaart.
- **Knoppen**: toon alleen wat past bij `state` (bijv. Start bij `docked`, Pauze bij `mowing`, Hervat bij
  `paused`, Dock bij `mowing`, `paused` of `returning`). Na een klik: knop uitschakelen met een kleine
  "verzonden"-melding tot er een nieuwe `state` binnenkomt of na 15 s een duidelijke time-out-melding.
- **Verouderde gegevens**: staat `last_update` van `state` ouder dan 5 minuten of is de maaier `offline`, dan grijs
  maken en "laatst gezien 14:32" tonen. Een positie ouder dan 2 minuten tijdens `mowing` krijgt een waarschuwing.
- **Banner "Inloggen nodig"**: bij `auth = required` met een knop naar `web_url` (of `login_url`) en een invoerveld
  om het adres uit de adresbalk te plakken (`POST /api/bridge/login`). Na succes verdwijnt de banner vanzelf
  zodra `auth` weer `ok` is.

**B. Geschiedenis**

- Lijst van maaibeurten: datum, duur, batterij start/eind, voortgang, zone, eindstatus; afgebroken en gedeeltelijke
  beurten herkenbaar gemarkeerd.
- Detail: hetzelfde kaartvlak met het hele pad en een **afspeelregel** (afspelen, pauzeren, snelheid ×1/×4/×16,
  slepen). Kleur het pad op tijd of op zone.
- Export: `GET /api/sessions/{id}` als JSON en als GPX-achtig CSV (`t,x,y`). Geen coördinaatconversie: het is een
  lokaal raster.

**C. Instellingen**

Thema, kaartoriëntatie, eenheden (m), zone-namen (alleen lokaal weergegeven, uit `ZONE_NAMES` of ingevuld),
verbindingsstatus van bridge en MQTT, versie.

### 4.3 Toestanden die ontworpen moeten zijn

Laden, geen data ooit ontvangen, maaier in het dock (positie en pad van de vorige beurt tonen als "vorige beurt"),
bridge offline, MQTT-verbinding verbroken, inloggen nodig, commando mislukt (`command_failed`), `error`-status met
leesbare tekst, zeer lang pad (ontdubbel tot ~2000 punten voor het tekenen), meerdere maaiers (kiezer bovenaan).

### 4.4 Techniek

Een statische single-page app zonder buildstap of met een klein, vastgezet buildproces; geen framework is
toegestaan maar geen voorwaarde. Geserveerd door de backend. Bijwerken via de SSE-stream, met automatisch
herverbinden. Een ontwikkelversie moet zonder broker werken met een kleine **demo-modus** die opgenomen berichten
(uit `events-*.jsonl`) afspeelt.

## 5. Acceptatiecriteria

1. Met een lege database en een draaiende bridge toont het dashboard binnen 5 s de status, de batterij en (als
   er een beurt loopt) het pad tot nu toe.
2. Een volledige maaibeurt levert precies één sessie op met een pad zonder dubbele punten; een herstart van de
   backend of van de bridge midden in een beurt verliest geen punten die de backend al had.
3. Bij `navimow/bridge/auth = required` verschijnt binnen 5 s de banner; na een geslaagde login verdwijnt hij.
4. Bij het wegvallen van de broker, de bridge of het netwerk toont het dashboard de juiste, verschillende melding
   en herstelt zichzelf zonder herladen.
5. Alle `GET`-schermen werken zonder internet. `POST`-acties zonder geldig token worden geweigerd (3.6).
6. Geen enkele API-respons of logregel bevat een token, wachtwoord of loginlink van Navimow, behalve de
   `login_url` die de bridge zelf publiceert en alleen terwijl `auth = required`.

## 6. Onbekend of niet beschikbaar

- **De tuinkaart** (grens, zones, verboden gebieden) staat niet in de officiële API. Plan geen kaartondergrond.
  (De privé-cloud van de app heeft hem wel; dat is een aparte, onofficiële koppeling en valt buiten deze opdracht.)
- **Oriëntatie van het raster** ten opzichte van kompas of app-kaart. Dat is alleen af te stemmen met de
  instellingen uit 4.2. Een aanwijzing uit de app: rechts-omhoog van het dock is +x, +y, maar dat is niet bewezen.
- **Zone-ID naar naam** is alleen bekend door elke zone eenmalig te starten en te kijken welk ID verschijnt.
- **Betekenis van `vehicle_state`** en de types 3 en 4 (opgedragen zone, vertraging) zijn maar beperkt gezien.
- **Het gedrag over dagen** (token verversen, MQTT-verbindingen, berichten die wegvallen) wordt nu onderzocht met de
  logbestanden van de bridge. Houd er rekening mee dat het kanaal stilvalt: toon leeftijd, vertrouw niet op "geen
  nieuws is goed nieuws".
- Alleen gemeten op één model (i208 AWD). Andere modellen kunnen velden missen (bijv. LiDAR-maaiers sturen veel
  vaker posities).
