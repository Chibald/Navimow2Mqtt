# navimow2mqtt

*[English version: README.md](README.md)*

Segway **Navimow**-robotmaaiers koppelen aan **MQTT**, met automatische **Home Assistant**-entiteiten
(maaier-entity met start / pauze / dock, batterij, status). Draait als **Docker**-container.

Het gebruikt dezelfde cloud-API als de officiële integratie
[segwaynavimow/NavimowHA](https://github.com/segwaynavimow/NavimowHA) (en de bijbehorende
[navimow-sdk](https://github.com/segwaynavimow/navimow-sdk)), maar zonder Home Assistant-custom-component:
alles gaat via je eigen MQTT-broker.

> Dit project is niet gelieerd aan Segway/Ninebot/Willand. De API is niet publiek gedocumenteerd en kan wijzigen.

## Hoe het werkt

```
 Navimow-cloud ── REST (status, commando's) ──┐
      │                                        ├─ navimow2mqtt (Docker) ── MQTT ── Home Assistant
      └── MQTT over WSS (live state-pushes) ───┘
```

1. Je logt **één keer** in met je Navimow-account (OAuth2); het token wordt in `/data/token.json` bewaard.
2. De bridge luistert op de cloud-MQTT naar live status (state, events, attributes) en valt terug op
   HTTP-polling als er 90 s niets binnenkomt.
3. Commando's (start / pauze / hervat / dock) gaan via de REST-API.
4. Alles wordt als JSON op jouw broker gezet, met Home Assistant-discovery.

## Installeren

```bash
cp .env.example .env      # pas MQTT_HOST e.d. aan
docker compose up -d --build
```

Open daarna **`http://<jouw-server>:8099`** in een browser en log in (zie hieronder). Het token wordt bewaard in
het volume `navimow-data`, dus na een herstart of rebuild hoef je niet opnieuw in te loggen.

### Inloggen

Je hoeft niet in de container of de logs te zoeken. Kies een van de drie manieren:

**1. Loginpagina (aanbevolen)** op `http://<jouw-server>:8099`
   De pagina is standaard Engelstalig; met `LANGUAGE=nl` wordt hij (en de meldingen en de kaartteksten) Nederlands.
   Zie de tabel hieronder. Hij heeft een kaartje in de stijl van een statuskaart: rechtsboven staat of je ingelogd bent,
   met twee tegels (resterende geldigheid van het token en of het automatisch ververst wordt), en twee knoppen:
   *Sign in to Navimow* (of *Sign in again* als je al bent ingelogd) en *View map*.
   - **Automatisch:** klik op *Sign in to Navimow*, log in met je Navimow-account, en je komt vanzelf op de
     loginpagina terug. Klaar. Dit werkt omdat Navimow een terugkeeradres naar je eigen server accepteert (getest met een
     eigen adres; de pagina gebruikt het adres waarop je haar hebt geopend). Je hoeft niets te plakken.
   - **Plakken** (alleen als het automatisch terugkeren niet meer lukt, bijvoorbeeld doordat Navimow zijn login
     wijzigt; het staat bewust verstopt onder het kleine grijze regeltje *Trouble signing in?* onderaan de kaart):
     klap dat open en log in via
     *Open the Navimow sign-in*. Je komt dan op een pagina van `my.home-assistant.io`; je hebt geen Home Assistant nodig. Kopieer het **volledige adres uit de adresbalk**
     (er staat `code` in) en plak het op de loginpagina.

   De pagina heeft geen wachtwoord en toont nooit tokens; zet de poort alleen open op je eigen netwerk
   (`WEB_PORT=0` zet de pagina uit).

   **Een bladwijzer zonder pagina:** een link met jouw eigen adres als terugkeerpunt werkt ook, bijvoorbeeld
   `https://navimow-h5-fra.willand.com/smartHome/login?channel=homeassistant&response_type=code&client_id=homeassistant&redirect_uri=http%3A%2F%2F<jouw-server>%3A8099%2Flogin`.
   Zo'n link heeft geen `state`; Navimow stuurt dan het woord `null` mee, en dat accepteert de bridge. De link met
   `state` van de pagina is veiliger (hij beschermt tegen een valse login-aanvraag).

**2. Via MQTT.** Zodra opnieuw inloggen nodig is, staat de link (retained) op `navimow/bridge/login_url`
   en is `navimow/bridge/auth` gelijk aan `required`. Publiceer het adres uit de adresbalk (of alleen de code) naar
   `navimow/bridge/login`; het resultaat verschijnt op `navimow/bridge/login_result`. Dit werkt zodra de bridge
   een keer met een token is gestart; de allereerste login doe je via de pagina.

**Voor openHAB, Node-RED e.d.:** zet `PUBLIC_URL` in `.env` op het adres waarop jij de pagina bereikt,
bijvoorbeeld `PUBLIC_URL=http://192.168.1.5:8099`. De container kan dat adres zelf niet weten (hij ziet alleen een
intern Docker-IP en niet welke hostnaam of poort jij gebruikt). Het wordt retained gepubliceerd op
`navimow/bridge/web_url`, ook vóór de allereerste login. Samen met `navimow/bridge/auth` en `navimow/bridge/login_url`
kan een regel in openHAB bij `required` een melding of link tonen naar de loginpagina.

**3. In de terminal:**
   ```bash
   docker compose run --rm navimow2mqtt python -m navimow2mqtt login
   ```

Het terminal-commando meldt na de login welke velden de server teruggaf (alleen de namen) en test het verversen:

- **`refresh_token` aanwezig en test OK**: de bridge ververst het token zelf, je hoeft niet opnieuw in te loggen.
- **Geen `refresh_token`, of de refresh wordt geweigerd**: dan moet je opnieuw inloggen. De bridge zet
  `navimow/bridge/auth` op `required` en in Home Assistant gaat de binary sensor **Login required** aan.
  Draai `login` opnieuw; een lopende container pakt het nieuwe token vanzelf op.

> Het access token is volgens metingen van andere integraties ongeveer **1 uur** geldig (de officiële integratie
> noemt 1 à 2 dagen). De bridge ververst het dus vaak. Of de server een `refresh_token` uitgeeft, en of de refresh
> blijft werken, verschilt volgens meldingen van gebruikers
> ([NavimowHA#86](https://github.com/segwaynavimow/NavimowHA/issues/86)) en is niet uit de code af te leiden.
> Dat blijkt pas bij de eerste login.

### Betrouwbaarheid van de live-updates

De cloud-MQTT kan stilletjes uitvallen (halfopen verbinding) terwijl de client "verbonden" meldt. Daarom:

- keepalive van 120 s, zodat een dode verbinding binnen enkele minuten opvalt;
- bij elke reconnect worden de MQTT-gegevens opnieuw opgehaald (ze zijn aan het OAuth-token gekoppeld), met minimaal
  65 s tussen twee aanvragen, want de server weigert te frequente aanvragen;
- een **watchdog**: meldt de HTTP-status iets anders dan de laatste MQTT-push, dan wordt de MQTT-verbinding
  opnieuw opgebouwd (maximaal eens per 5 minuten).

Deze aanpak is overgenomen van [NaviWatch](https://github.com/MadMorpheus/naviwatch).

Gebruik je een tweede Navimow-account waarmee de maaier gedeeld is? Dan raakt je telefoon-app niet uitgelogd.

Draai je het op een server met **Portainer**? Zie [docs/PORTAINER.md](docs/PORTAINER.nl.md) (image bouwen en importeren, stack
met `docker-compose.portainer.yml`, eenmalig inloggen).

## Configuratie

Alles via omgevingsvariabelen (`.env`):

| Variabele | Betekenis | Standaard |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT` | jouw broker | `localhost`, `1883` |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | inloggegevens | – |
| `MQTT_BASE_TOPIC` | topic-prefix | `navimow` |
| `MQTT_DISCOVERY`, `MQTT_DISCOVERY_PREFIX` | Home Assistant discovery | `true`, `homeassistant` |
| `MQTT_CLIENT_ID` | client-id | `navimow2mqtt` |
| `HTTP_POLL_INTERVAL` | minimale pauze tussen fallback-polls (s) | `60` |
| `MQTT_STALE_SECONDS` | geen cloud-push zo lang → HTTP-fallback (s) | `90` |
| `OFFLINE_AFTER` | geen data zo lang → maaier `offline` (s) | `300` |
| `LOG_LEVEL` | `INFO` of `DEBUG` | `INFO` |
| `LOG_DIR`, `LOG_KEEP_DAYS`, `LOG_MAX_MB` | logbestanden, bewaartijd en maximum per dag | `/data/logs`, `14`, `50` |
| `WEB_PORT` | poort van de loginpagina, `0` = uit | `8099` |
| `LANGUAGE` | taal van de loginpagina en de kaartteksten: `en` of `nl` | `en` |
| `MAPS_DIR`, `TRAIL_FILE` | map met achtergrondplaatjes (leeg = uit) en bestand voor het laatste pad (leeg = alleen in het geheugen) | `/data/maps`, `/data/trail.json` (in de image) |
| `TIMEZONE` | tijdzone voor het begin van een week, bijv. `Europe/Amsterdam` | `UTC` |
| `STATS_FILE`, `STATS_WEEKS`, `STATS_MIN_SESSION_S` | weekstatistieken: bestand (leeg = uit), weken bewaard, minimale maaitijd van een beurt in seconden | `/data/stats.json` (in de image), `12`, `60` |
| `PATH_MAX_POINTS` | aantal punten dat de bridge van het pad van een maaibeurt bewaart (genoeg voor een hele zone), `0` = uit | `8000` |
| `PUBLIC_URL` | adres van de loginpagina vanaf je netwerk, gepubliceerd op `bridge/web_url` | – |
| `NAVIMOW_TOKEN_FILE` | tokenbestand | `/data/token.json` |
| `NAVIMOW_REDIRECT_URI` | redirect na login | `https://my.home-assistant.io/redirect/oauth` |
| `NAVIMOW_API_URL`, `NAVIMOW_AUTHORIZE_URL`, `NAVIMOW_TOKEN_URL`, `NAVIMOW_CLIENT_ID`, `NAVIMOW_CLIENT_SECRET` | endpoints en OAuth-client | zoals de officiële integratie |

De client-id en het client-secret zijn die van de officiële Home Assistant-integratie (publiek in diens repo).
De regio is nu `fra` (Europa).

## MQTT

> De **volledige** lijst met alle topics, payloads, retained-gedrag en discovery staat in
> [docs/MQTT.md](docs/MQTT.nl.md). Onderstaande tabel is een samenvatting.

| Topic | Inhoud |
|---|---|
| `navimow/<naam>/state` | JSON: `state`, `activity`, `battery`, `signal_strength`, `position`, `error`, `last_update` (retained) |
| `navimow/<naam>/availability` | `online` / `offline` |
| `navimow/<naam>/info` | JSON met id, naam, model, firmware, serienummer |
| `navimow/<naam>/event` | events van de maaier en mislukte commando's |
| `navimow/<naam>/location` | ruwe JSON-array van het ongedocumenteerde `location`-kanaal (retained; leeg als de maaier in het dock staat) |
| `navimow/<naam>/position` | uitgepakt: `x`, `y` (m t.o.v. het dock), `distance_m`, `heading_deg`, `course_deg` (de richting waarin hij zich echt verplaatste), `vehicle_state`, `zone`, `progress_pct`, `target_zone`, `task_delay`, `last_update` (retained; alleen de velden die ooit zijn ontvangen) |
| `navimow/<naam>/path` | het pad van de huidige maaibeurt: `points` (lijst van `[x, y]`, m t.o.v. het dock), `count`, `started`, `last_update` (retained) |
| `navimow/<naam>/command` | **schrijf hierheen**: `start`, `pause`, `resume`, `dock`, `stop` |
| `navimow/bridge/status` | `online` / `offline` (Last Will) |
| `navimow/bridge/auth` | `ok` / `required` |
| `navimow/bridge/login_url` | link om in te loggen, alleen gevuld als `auth` = `required` (retained) |
| `navimow/bridge/web_url` | adres van de loginpagina, uit `PUBLIC_URL` (retained, alleen als ingesteld) |
| `navimow/bridge/login` | **schrijf hierheen** het adres uit de adresbalk (of de code) om in te loggen |
| `navimow/bridge/login_result` | uitkomst van die poging |

`<naam>` is de maaiernaam uit de app, omgezet naar `a-z0-9_`. Een `start` terwijl de maaier gepauzeerd is, wordt
automatisch een `resume`.

Statussen: `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown`. De `activity` is de
Home Assistant-variant (`docked`, `mowing`, `paused`, `returning`, `error`).

### Locatie (experimenteel)

Het `location`-kanaal staat niet in de officiële SDK; het is door [NaviWatch](https://github.com/MadMorpheus/naviwatch)
gevonden. De berichten zijn een JSON-array met items per `type`: 1 = positie (`postureX`/`postureY` in meters
t.o.v. het laadstation, **geen GPS**, plus `postureTheta`), 2 = zone en voortgang (`currentMowBoundary`,
`currentMowProgress` 0–10000), 3 = opgedragen zone, 4 = vertraging. Alleen **LiDAR-modellen** sturen dit
waarschijnlijk; zonder LiDAR (bijv. i105) blijft het stil. De eerste ontvangst staat in de log
(`first message on the location channel`); zet `LOG_LEVEL=DEBUG` om ook onbekende topics van de maaier te zien.
Getest op een **i208 AWD**: tijdens handmatig rijden komt er elke paar seconden een type-1-bericht (`postureX`,
`postureY` als tekst in meters, `postureTheta` in radialen, `vehicleState`); in het dock is de array leeg.
De bridge pakt dit uit naar `navimow/<naam>/position`. `x`/`y` zijn een lokaal raster rond het dock, geen GPS;
`heading_deg` is `postureTheta` omgezet naar graden (0–360), tegen de klok in gemeten vanaf de +x-as. Of dit de
rijrichting is, is nog niet sluitend: op een i208 AWD klopte het soms met de verplaatsing tussen punten en was het
soms ongeveer 180° anders (mogelijk een meting tijdens het keren). Het dock ligt ongeveer in
(0, 0). De positie is afgerond op 0,25 m en komt elke 8 tot 20 s; hoe de assen op kompasrichtingen of je tuin
liggen, is niet bekend. `progress_pct` is `currentMowProgress / 100` (op een i208 AWD gecontroleerd: 2,2 tegen 2 % in de app). Een leeg bericht wist de laatste positie niet. Voor Home Assistant
staan de bijbehorende sensoren standaard uit.

**Pad.** Op `navimow/<naam>/path` houdt de bridge de punten van de lopende maaibeurt bij, bruikbaar voor een lijn
in een dashboard. Er komt een punt bij als de maaier rijdt (`mowing`, `paused` of `returning`) en de positie
veranderd is. Gaat de status van `docked`, `idle` of `charging` naar `mowing`, dan begint het pad opnieuw; pauzeren
en hervatten wist het niet. Er blijven maximaal `PATH_MAX_POINTS` punten (standaard 8000) bewaard, genoeg voor een hele zone; een te lage grens laat
alleen het eind van het pad over. Het retained MQTT-bericht bevat een uitgedunde kopie van hooguit 500 punten (`count`) met
het echte aantal in `total`; het kaartje gebruikt alle punten. Het pad wordt bewaard in `/data/trail.json`, dus een herstart
verliest het niet. De positie is afgerond op 0,25 m, dus het is een grof pad.

**Het pad van een eerdere beurt terughalen.** Elk positiebericht staat in het logbestand. Is een pad afgekapt (bijvoorbeeld door
een te lage `PATH_MAX_POINTS`) of was de bridge niet aan het begin van de beurt actief, dan laad je het uit de log:

- **Met een knop (het makkelijkst):** open `http://<server>:8099/maps`, kies een plaatje en klik op *Load the full path of the
  last mowing session from the log* (in het Nederlands: *Het volledige pad van de laatste maaibeurt uit de log laden*). De
  draaiende bridge leest zijn eigen log, neemt de laatste maaibeurt met posities (een kort flitsje "maait" vlak na het opstarten
  wordt overgeslagen) en laadt het hele pad. Dat werkt alleen als `LOG_DIR` aan staat (standaard in de image) en de bridge
  tijdens die beurt draaide.
- **Met een opdracht:** stop eerst de bridge (die schrijft bij het stoppen zijn eigen pad weg), draai dan de opdracht in de
  container en start weer:

```bash
docker compose stop
docker compose run --rm --no-deps navimow2mqtt python -m navimow2mqtt rebuild-trail
docker compose start
```

Zonder opties pakt hij de laatste maaibeurt; `--name`, `--since` en `--until` (ISO-tijden) kiezen een andere. In Portainer: stop
de stack, open een console in een tijdelijke container met hetzelfde volume en draai `python -m navimow2mqtt rebuild-trail`.

`course_deg` is de richting van de laatste verplaatsing (tussen de laatste twee verschillende posities), tegen de
klok in vanaf +x. Bij veel keren, zoals in een tuin met veel hoekjes, is dit betrouwbaarder dan `heading_deg`. Door de
afronding op 0,25 m is hij grof bij korte stapjes. Een nieuwe maaibeurt begint zonder `course_deg`.

## Statistieken per week

De bridge telt per week het aantal maaibeurten en de maaitijd (tijd in de status `mowing`) en publiceert dat op
`navimow/<naam>/stats`; elke afgeronde beurt komt als apart bericht op `navimow/<naam>/session`, zodat openHAB zelf een
lange geschiedenis kan bewaren. De bridge houdt maximaal 12 weken bij in `/data/stats.json` (een paar kilobyte, groeit
niet; geen database) zodat een herstart een lopende beurt niet kwijtraakt. Stel `TIMEZONE=Europe/Amsterdam` in: een week
begint op maandag 00:00 in die tijdzone. Zie [docs/MQTT.md](docs/MQTT.nl.md) voor de velden. Oppervlak (m²), slijtage van de
messen en de levenslange teller geeft de officiële API niet.

## Logbestanden

De bridge schrijft twee soorten logs naar `/data/logs` (het volume `navimow-data`):

| Bestand | Inhoud |
|---|---|
| `events-<datum>.jsonl` | één JSON-regel per gebeurtenis, per UTC-dag. Bedoeld voor analyse |
| `navimow2mqtt.log` | de gewone leesbare log, roulerend (5 × 2 MB) |

`events-*.jsonl` bevat o.a. `cloud_msg` (elk ruw bericht van de maaier: `state`, `event`, `attributes`, `location`),
`http_status`, `state_change`, `session_start` / `session_end`, `cloud_connect` / `cloud_disconnect` (met
`up_seconds`), `cloud_force_reconnect`, `watchdog_mismatch`, `token_refresh` / `token_refresh_failed`, `login`,
`auth_state`, `api_error`, `command` en `start` / `stop`. Er worden **nooit** tokens, wachtwoorden, MQTT-gegevens van
Navimow, loginlinks of codes in geschreven; van de MQTT-verbinding alleen host en poort. De ruwe maaierberichten
bevatten wel het serienummer van de maaier en positiegegevens: deel de bestanden dus bewust.

Instellingen: `LOG_DIR` (leeg = geen bestanden), `LOG_KEEP_DAYS` (14) en `LOG_MAX_MB` (50 per dag).

Bestanden ophalen van de server:

```bash
docker compose cp navimow2mqtt:/data/logs ./logs-export
```

## Kaart

Op `http://<server>:8099/tile.svg` (en als pagina op `/tile`; `/map.svg` en `/map` zijn hetzelfde) tekent de bridge een kaartje
in de stijl van het "Tuin"-ontwerp: een donkergroen gazon waarop het gemaaide pad verschijnt als lichtgroene stroken in twee
afwisselende tinten, het laadstation en de maaier als robotje met een pulserende ring. Het is alleen het afgeronde kaartje:
geen kaartvlak, geen titel, geen tekst, en de hoeken zijn doorzichtig, dus het neemt de achtergrond van je dashboard over.
Parameters: `w`, `theme`, `swath`, `device`, `view`, `span`, `follow` (zie [docs/OPENHAB.md](docs/OPENHAB.nl.md)).

**Achtergrondplaatjes.** Op `http://<server>:8099/maps` upload je een screenshot van de Navimow-app, klik je het station aan,
geef je de oppervlakte in m² op (voor de schaal) en laat je het gemaaide pad automatisch draaien tot het in de tuin ligt.
Daarna toont `/tile.svg?view=NAAM` het kaartje op de echte vorm van je tuin, en `&span=12` zoomt in op 12 meter rond de
maaier. Met `&idle_span=0` erbij zie je in rust het hele plaatje en zoomt het kaartje alleen in zolang de maaier rijdt
(`/tile.svg?view=tuin&span=12&idle_span=0`). Met `view=auto` kiest de bridge het plaatje van de zone waarin de maaier zit. De tuin zelf geeft de officiële API niet,
vandaar de eenmalige kalibratie. De plaatjes staan in `/data/maps`, het laatste pad in `/data/trail.json` (beide in het
volume). Zie [docs/OPENHAB.md](docs/OPENHAB.nl.md) voor de stappen en de beperkingen.

## openHAB

Voor openHAB staat in [docs/OPENHAB.md](docs/OPENHAB.nl.md) per functie wat de bridge biedt en op welke MQTT-adressen
(status, bediening, positie, pad, login-bewaking) en hoe de kaart op een URL werkt. De volledige lijst staat in
[docs/MQTT.md](docs/MQTT.nl.md).

## Eigen dashboard en backend

Wil je zelf een webdashboard met geschiedenis en padweergave bouwen, dan staat een uitgewerkte opdracht in
[docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.nl.md). Voor openHAB heb je die niet nodig.

## Home Assistant

Met discovery verschijnt per maaier een apparaat met: **lawn_mower** (start, pauze, dock), **Battery**, **State**
en **Signal strength** (standaard uit). Het apparaat **Navimow2Mqtt** heeft de binary sensor **Login required**;
maak daar een automatisering van als je een melding wilt.

## Problemen oplossen

| Probleem | Oorzaak |
|---|---|
| `login required` in de log | token verlopen of nog geen `login` gedaan |
| Login mislukt (`token endpoint refused`) | de code is al gebruikt of verlopen (hij is maar kort geldig): begin opnieuw met een nieuwe link. Bij optie 1: controleer dat je de pagina opende op het adres waarop je server bereikbaar is |
| Geen live updates, wel pollen | cloud-MQTT geweigerd; zet `LOG_LEVEL=DEBUG` en kijk naar `cloud MQTT` |
| Maaier niet gevonden | andere regio dan Europa, of account zonder gedeelde maaier |

## Ontwikkelen

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m unittest discover -s tests
```

```
navimow2mqtt/
  auth.py     OAuth2: login-URL, code-uitwisseling, token verversen en opslaan
  api.py      REST: apparaten, status, MQTT-gegevens, commando's
  cloud.py    cloud-MQTT (WSS), vernieuwt de inloggegevens bij elke reconnect
  models.py   state-normalisatie
  ha.py       topics en Home Assistant discovery
  bridge.py   koppelt cloud en lokale broker
  config.py   omgevingsvariabelen
```

## Status

De code is getest met unit tests tegen nagebootste responses. Tegen de echte Navimow-cloud is hij **nog niet
getest**: de exacte velden van de OAuth-respons en de MQTT-berichten zijn afgeleid uit de officiële SDK en integratie.
Meld wat je tegenkomt, zet `LOG_LEVEL=DEBUG` voor de ruwe payloads.

## Dank

[segwaynavimow/NavimowHA](https://github.com/segwaynavimow/NavimowHA) en
[navimow-sdk](https://github.com/segwaynavimow/navimow-sdk) voor de API-kennis, en
[NaviWatch](https://github.com/MadMorpheus/naviwatch) voor de bevindingen over het bevriezen van de MQTT-verbinding.
Het project is opzet-gewijs gebaseerd op gro2mqtt.

## Licentie

[MIT](LICENSE).
