# Op een server zetten met Portainer

*[English version: PORTAINER.md](PORTAINER.md)*

Dit zijn de stappen om navimow2mqtt op je servertje te draaien, met een stack in Portainer.

## Waarom niet gewoon `docker-compose.yml`?

Dat bestand bouwt de image uit de projectmap (`build: .`). Die map staat niet op je server, en Portainer kan hem daar niet
bouwen. Daarom bouw je de image **eenmaal op je pc** en zet je hem als bestand over. De stack gebruikt dan alleen
`docker-compose.portainer.yml`.

## Stap 0: welke processor heeft je server?

Open op de server een terminal (SSH, of in Portainer: Containers, de Portainer-container, Console) en voer uit:

```bash
uname -m
```

- `x86_64`: een gewone pc of NUC. Gebruik de standaardbouw (stap 1a).
- `aarch64` of `arm64`: bijvoorbeeld een Raspberry Pi 4 of 5. Gebruik stap 1b.
- `armv7l`: een oude 32-bit Pi. Zeg het me, dan kijk ik wat er nodig is.

## Stap 1: de image bouwen en opslaan (op je pc)

In de projectmap, PowerShell:

**1a. Server is x86_64** (de image die je nu lokaal hebt is hiervoor geschikt):

```bash
docker build -t navimow2mqtt:latest .
docker save navimow2mqtt:latest -o navimow2mqtt.tar
```

**1b. Server is ARM64**:

```bash
docker buildx build --platform linux/arm64 -t navimow2mqtt:latest --load .
docker save navimow2mqtt:latest -o navimow2mqtt.tar
```

Het bestand `navimow2mqtt.tar` is ongeveer 120 MB.

## Stap 2: de image in Portainer zetten

1. Portainer, **Images**, **Import**.
2. Kies `navimow2mqtt.tar`, **Upload**.
3. Daarna staat `navimow2mqtt:latest` in de lijst met images.

(Heb je een eigen registry, dan kun je de image daar naartoe pushen en in de stack de volledige naam gebruiken.)

## Stap 3: de stack maken

1. Portainer, **Stacks**, **Add stack**.
2. Naam: `navimow2mqtt`.
3. Kies **Web editor** en plak de inhoud van [`docker-compose.portainer.yml`](../docker-compose.portainer.yml).
4. Onder **Environment variables** voeg je toe (knop "Add environment variable", of "Advanced mode" en plakken):

   | Naam | Waarde | Verplicht |
   |---|---|---|
   | `MQTT_HOST` | het IP van je MQTT-broker, bijv. `192.168.1.2` | ja |
   | `MQTT_USERNAME`, `MQTT_PASSWORD` | als je broker inloggegevens vraagt | nee |
   | `PUBLIC_URL` | het adres van **deze server** voor de loginpagina, bijv. `http://192.168.1.5:8099` | aanbevolen |
   | `TIMEZONE` | `Europe/Amsterdam` (staat al als standaard) | nee |
   | `LANGUAGE` | `nl` voor een Nederlandse loginpagina (standaard `en`) | nee |

   Het wachtwoord zet je hier in en niet in het compose-bestand.
5. **Deploy the stack**.

## Stap 4: eenmalig inloggen

Het token dat je nu op je pc hebt, staat in een Docker-volume op je pc en gaat niet mee. Op de server log je dus één keer in:

1. Kijk bij Containers, `navimow2mqtt`, **Logs**: er staat `login required`.
2. Open `http://<server>:8099` in een browser.
3. Klik op *Sign in to Navimow* en log in. Je komt vanzelf op de pagina terug en bent ingelogd (de pagina toont dan
   *Signed in*). Lukt dat niet, klap dan het grijze regeltje *Trouble signing in?* onderaan open en plak het adres uit de adresbalk (zie de
   README).
4. Binnen enkele seconden staat in de log `found 1 mower(s)` en `cloud MQTT connected`.

Daarna blijft het token in het volume `navimow-data`, ook als je de stack bijwerkt of de container opnieuw maakt.

## Stap 5: de lokale kopie op je pc stoppen

**Draai nooit twee kopieën tegelijk.** Ze gebruiken dezelfde MQTT-client-id en dezelfde Navimow-login: ze gooien elkaar van de
broker en kunnen elkaars token ongeldig maken. Stop dus de versie op je pc:

```bash
docker compose down
```

## Wat controleren

In een MQTT-client op je server (of MQTT Explorer):

- `navimow/bridge/status` is `online`, `navimow/bridge/auth` is `ok`
- `navimow/mower/state` toont de status en batterij
- als `PUBLIC_URL` is gezet: `navimow/bridge/web_url` bevat het adres van de loginpagina

## Bijwerken naar een nieuwe versie

1. Op je pc opnieuw bouwen en `docker save` (stap 1).
2. Portainer: **Images**, **Import**, het nieuwe bestand uploaden (de oude `latest` krijgt dan de nieuwe versie).
3. Stack, **Update the stack** met "Re-pull image" uit en **Deploy**, of ga naar de container en kies **Recreate**.

Token, statistieken en logs blijven bewaard (volume `navimow-data`).

## Logbestanden ophalen voor analyse

De bridge schrijft naar `/data/logs` (zie de README). Ophalen kan zo:

- **Via SSH op de server:**
  ```bash
  docker cp navimow2mqtt:/data/logs ./navimow-logs
  ```
- **Via Portainer:** Containers, `navimow2mqtt`, Console (Connect), dan bijvoorbeeld `ls -l /data/logs`. Bestanden uit het volume
  downloaden kan in recente Portainer-versies via **Volumes**, `navimow-data`, **Browse**.

## Problemen

| Probleem | Oorzaak |
|---|---|
| `exec format error` in de log | De image is voor de verkeerde processor gebouwd (stap 0 en 1b) |
| De stack start niet: *image niet gevonden* | De import (stap 2) is niet gelukt, of de naam is anders dan `navimow2mqtt:latest` |
| `connected to MQTT broker` ontbreekt | `MQTT_HOST` klopt niet of is vanuit de container niet bereikbaar. Draait de broker op dezelfde server in een andere container, gebruik dan het IP van de server, niet `localhost` |
| De loginpagina laadt niet | Poort 8099 wordt al gebruikt, of een firewall blokkeert hem. Pas links in de `ports`-regel aan, bijv. `"8100:8099"`, en zet `PUBLIC_URL` erop af |
| Opnieuw inloggen nodig | Zie de README: de bridge toont `auth = required`, ook op `navimow/bridge/auth` |

## Als je Home Assistant-sensoren niet wilt

`MQTT_DISCOVERY` staat in dit bestand standaard op `false`, omdat openHAB het niet gebruikt. Heb je eerder discovery aan
gehad, dan staan er nog retained configuratieberichten onder `homeassistant/.../navimow2mqtt_...` op je broker. Die
kun je met een MQTT-client wissen (een leeg retained bericht naar hetzelfde topic).
