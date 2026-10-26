# navimow2mqtt voor openHAB: wat er is en waar

*[English version: OPENHAB.md](OPENHAB.md)*

Functionele beschrijving voor iemand die openHAB (MQTT-binding, Main UI) goed kent. Je bouwt Things, Items, regels en
pagina's zelf. Dit document zegt **wat de bridge biedt** en **op welke adressen**. De volledige, exacte lijst van alle
topics en payloads staat in [MQTT.md](MQTT.nl.md); hier staat de samenvatting per functie.

Aanname in alle voorbeelden: basis-topic `navimow`, maaier `mower`, bridge-server `192.168.1.5`. Pas aan naar jouw
situatie (`MQTT_BASE_TOPIC`, de maaiernaam uit de app in kleine letters, het adres van de container).

## 1. Hoe de gegevens binnenkomen

- De bridge praat met de Navimow-cloud en zet alles op **jouw eigen MQTT-broker**. openHAB hoeft alleen op die broker
  te luisteren; er is geen directe koppeling met Navimow nodig.
- Bijna alles is **retained**: na het abonneren heb je meteen de laatste waarde.
- Waarden staan als **JSON in één bericht** (`state`, `position`, `path`). Haal losse waarden eruit met
  `JSONPATH` in de transformatie van een kanaal.
- Een veld dat ontbreekt in de JSON is **onbekend** (het Item blijft `NULL`), geen nul. Dat gebeurt bijvoorbeeld met
  `zone` en `course_deg` tot de maaier ze voor het eerst stuurt.
- Beschikbaarheid: gebruik `navimow/mower/availability` (`online`/`offline`) als beschikbaarheidstopic van de Thing.
  Valt de bridge zelf weg, dan wordt `navimow/bridge/status` `offline` (Last Will); de maaier-availability blijft dan
  op de laatste waarde staan. Combineer dus beide als je dat onderscheid wilt zien.

## 2. Functies en adressen

### Status van de maaier

| Wat | Topic | Waarde (JSONPATH waar nodig) |
|---|---|---|
| Status | `navimow/mower/state` | `$.state`: `idle`, `docked`, `charging`, `mowing`, `paused`, `returning`, `error`, `unknown` |
| Activiteit (vereenvoudigd) | `navimow/mower/state` | `$.activity`: `docked`, `mowing`, `paused`, `returning`, `error` |
| Batterij, % | `navimow/mower/state` | `$.battery` (kan ontbreken) |
| Ruwe status van de maaier | `navimow/mower/state` | `$.metrics.raw_state` (bijv. `isRunning`) |
| Online/offline | `navimow/mower/availability` | `online` / `offline` |
| Apparaatgegevens | `navimow/mower/info` | JSON: `name`, `model`, `firmware`, `serial` (bruikbaar voor Thing-eigenschappen) |

### Bediening

| Wat | Topic | Waarde |
|---|---|---|
| Commando sturen | `navimow/mower/command` (schrijven) | `start`, `pause`, `resume`, `dock` of `stop` |

`start` terwijl de maaier gepauzeerd is, wordt automatisch een `resume`. Er komt **geen bevestiging**: kijk naar
`state`. Mislukt een commando, dan komt er op `navimow/mower/event` een bericht
`{"event":"command_failed","command":"dock","error":"..."}`. Neem het commando-kanaal niet op in persistentie.

### Positie, voortgang en zone

Bron: `navimow/mower/position` (JSON, retained). Alleen velden die ooit zijn ontvangen zijn aanwezig.

| Wat | JSONPATH | Opmerking |
|---|---|---|
| Positie x, y (m) | `$.x`, `$.y` | lokaal raster rond het dock, **geen GPS**, afgerond op 0,25 m. Dock ≈ (0, 0) |
| Afstand tot het dock (m) | `$.distance_m` | |
| Rijrichting (°) | `$.course_deg` | richting van de laatste verplaatsing, tegen de klok in vanaf +x. **Gebruik deze.** |
| Gemelde hoek (°) | `$.heading_deg` | onbetrouwbaar tijdens keren (soms 180° naast de rijrichting) |
| Voortgang (%) | `$.progress_pct` | komt overeen met de app |
| Zone-ID | `$.zone` | intern ID, niet het nummer uit de app (ID 6 = "achtertuin" bij deze tuin) |
| Opgedragen zone(s) | `$.target_zone` | lijst, bijv. `[6]` |
| Meettijd | `$.time` | epoch in **milliseconden**, UTC; gebruik dit om de leeftijd van een positie te bepalen |

Een positie kan oud zijn (de maaier staat in het dock en stuurt dan niets). Beoordeel de leeftijd met `$.time` voordat
je haar als actueel toont. De tijd dat de bridge publiceerde staat in `$.last_update` (ISO-8601, UTC).

### Pad van de maaibeurt

| Wat | Topic | Inhoud |
|---|---|---|
| Pad | `navimow/mower/path` | JSON: `points` (lijst van `[x, y]` in meters, hooguit 500), `count`, `total`, `started`, `last_update` |

Het pad van de lopende of laatste maaibeurt. Bij een lang pad is dit een uitgedunde kopie van hooguit 500 punten (`total` is het
echte aantal; de bridge bewaart er tot 8000 en het kaartje gebruikt ze allemaal). Een nieuwe beurt (status van `docked`, `idle` of
`charging` naar `mowing`) begint met een leeg pad. Pauzeren en hervatten wist niets. Het pad wordt bewaard in
`/data/trail.json`, dus een herstart verliest het niet. Neem `path` niet op in persistentie met
`everyChange`; het bericht is groot en verandert bij elk nieuw punt.

### Statistieken per week

| Wat | Topic | Waarde |
|---|---|---|
| Beurten deze week | `navimow/mower/stats` | `$.sessions` |
| Maaitijd deze week (uren) | `navimow/mower/stats` | `$.mowing_h` (of `$.mowing_s` in seconden) |
| Vorige week | `navimow/mower/stats` | `$.previous_week.sessions`, `$.previous_week.mowing_h` |
| Lijst van weken (max. 12) | `navimow/mower/stats` | `$.weeks` |
| Totaal sinds de bridge telt | `navimow/mower/stats` | `$.total_sessions`, `$.total_mowing_h` |
| Elke afgeronde beurt | `navimow/mower/session` | JSON met `started`, `ended`, `duration_s`, `mowing_s`, `paused_s`, `returning_s`, `battery_start`, `battery_end`, `progress_pct`, `zone`, `partial`, `counted` |

De bridge houdt zelf alleen de laatste 12 weken bij (een klein bestand in `/data`, geen database). Wil je een langere
geschiedenis, grafieken of "deze maand", bewaar dan in openHAB de waarden uit `session` (bijvoorbeeld `$.mowing_s` en
`$.duration_s`, op de tijd `$.ended`) en tel daar op. `session` is retained, dus na een herstart van openHAB komt de laatste
beurt nog een keer binnen; dedupliceer op `ended`.

Bedoeld voor een kaart zoals "Deze week: beurten, maaitijd". **Oppervlak (m²), slijtage van de messen en de levenslange
teller zijn niet beschikbaar** in de officiële API. "Maaitijd" is de tijd in de status `mowing`; pauzeren en terugrijden
tellen niet mee. Beurten korter dan 60 s tellen niet mee (`STATS_MIN_SESSION_S`). Een beurt wordt geteld in de week waarin
hij eindigde; een week begint op maandag 00:00 in de tijdzone `TIMEZONE` (stel `Europe/Amsterdam` in).

### Inloggen bij Navimow (bewaking)

De Navimow-login moet af en toe vernieuwd worden als de automatische verversing faalt. De bridge meldt dat:

| Wat | Topic | Waarde |
|---|---|---|
| Login-status | `navimow/bridge/auth` | `ok` of `required` |
| Adres van de loginpagina | `navimow/bridge/web_url` | alleen als `PUBLIC_URL` is ingesteld, bijv. `http://192.168.1.5:8099` |
| Rechtstreekse Navimow-loginlink | `navimow/bridge/login_url` | alleen gevuld bij `required`; leeg (topic gewist) bij `ok` |
| Inloggen via MQTT | `navimow/bridge/login` (schrijven) | het adres uit de adresbalk na de Navimow-login, of alleen de code |
| Uitkomst van die poging | `navimow/bridge/login_result` | korte tekst, niet retained |
| Bridge zelf | `navimow/bridge/status` | `online` / `offline` |

Bruikbaar als: regel op `auth` naar `required` die een melding stuurt met een link naar `web_url`, en een knop of een
ingesloten pagina die de loginpagina toont zolang `auth` = `required`. **Ook vóór de allereerste login** staan `auth`,
`login_url` en `web_url` al op de broker (de bridge stuurt ze eenmalig); alleen de maaier-topics ontbreken dan.

## 3. De kaart (de tile)

De bridge tekent zelf een kaartje en serveert dat op dezelfde poort als de loginpagina (standaard 8099):

| Adres | Wat |
|---|---|
| `http://192.168.1.5:8099/tile.svg` | het kaartje als afbeelding (SVG), voor een `Image`-element. `/map.svg` is hetzelfde |
| `http://192.168.1.5:8099/tile` | hetzelfde in een pagina met een doorzichtige achtergrond die zichzelf ververst. `/map` is hetzelfde |
| `http://192.168.1.5:8099/maps` | de plaatjes beheren (zie hieronder) |

**Wat je ziet:** een afgerond kaartje met een donkergroen gazon waarop het gemaaide pad verschijnt als lichtgroene stroken in
twee afwisselende tinten (zoals een gemaaid gazon), het laadstation (donker blokje met groen ledje) en de maaier als klein
robotje dat in de rijrichting wijst. Een nieuwe strook begint telkens als de maaier omkeert. Het ledje is groen, oranje bij
pauze en rood bij een storing; de ring eromheen pulseert tijdens maaien (wit), terugrijden (blauw) of een storing (rood).
Staat de positie langer dan 2 minuten stil, dan is de robot grijs en pulseert hij niet.

Het kaartje is **alleen de afbeelding**: geen kaartvlak, geen titel, geen legenda en geen enkele tekst. Alles buiten de
afgeronde hoeken is doorzichtig, dus het neemt de achtergrond van je dashboard over. Het kleurenschema volgt het systeem
(licht of donker) of wordt vastgezet met `theme`.

Zonder achtergrondplaatje is het donkergroen alleen de ondergrond van het kaartje, **geen** omtrek van je gazon: de
officiële API geeft geen tuinplattegrond.

Parameters (achter het adres, gescheiden met `&`):

| Parameter | Waarden | Betekenis |
|---|---|---|
| `device` | maaiernaam, bijv. `mower` | kies een maaier (standaard de eerste) |
| `w` | 240 tot 1600 | breedte in pixels (standaard 640) |
| `theme` | `light` of `dark` | thema; standaard volgt het de browser (`prefers-color-scheme`) |
| `swath` | meters, 0,05 tot 2 | breedte van de gemaaide strook (standaard 0,25; een schatting, de API geeft de snijbreedte niet) |
| `rotate` | `0`, `90`, `180`, `270` | het raster draaien, alleen zonder achtergrondplaatje |
| `flip` | `x` of `y` | spiegelen, alleen zonder achtergrondplaatje |
| `refresh` | seconden, minimaal 2 | alleen bij `/tile` |
| `view` | naam van een plaatje, of `auto` | gebruik dat plaatje als achtergrond (zie hieronder) |
| `span` | meters, 0 tot 500 | met een plaatje: zoom in op zoveel meter breed rond de maaier. Zonder `span` zie je het hele plaatje |
| `follow` | `robot` (standaard) of `dock` | met `span`: het venster volgt de maaier of staat op het station |
| `idle_span` | meters, `0` = het hele plaatje | het venster **als de maaier stilstaat** (`docked`, `charging`, `idle`, onbekend). Met `span=12&idle_span=0` zie je in rust het hele plaatje en zoomt het kaartje in op 12 meter zodra hij rijdt (`mowing`, `paused`, `returning`, `error`) |
| `fixed` | `0` | alleen met `idle_span`: standaard houdt het kaartje dezelfde afmetingen bij in- en uitzoomen (het hele plaatje past dan in het kaartje); met `fixed=0` krijgt het hele plaatje zijn eigen verhoudingen |

### Achtergrondplaatjes (de vorm van je tuin)

Een screenshot van de Navimow-app (of een ander plaatje van je tuin) kan als achtergrond dienen, zodat het pad en de robot
op de echte vorm van je tuin staan. De maaier geeft zelf geen plattegrond; daarom kalibreer je het plaatje eenmalig.

**Waarom kalibreren:** de bridge kent posities in meters rond het station; een plaatje is in pixels. Voor de omrekening zijn
drie getallen nodig: de plek van het station in het plaatje, de schaal (pixels per meter) en de draaiing.

1. Open `http://192.168.1.5:8099/maps`, kies een naam (bijv. `tuin`) en upload het plaatje (PNG of JPEG, maximaal 3 MB,
   maximaal 12 plaatjes). Je komt op de kalibratiepagina.
2. **Station:** klik op het laadstation in het plaatje (sleep om bij te stellen). Je kunt inzoomen (tot 25×) met het
   scrollwiel, de knoppen *+* en *−* of met twee vingers; *Hele plaatje* zoomt weer uit. Om het plaatje te verschuiven
   zet je *Plaatje verschuiven* aan (of houd Shift ingedrukt, of sleep met twee vingers). De stip en het pad houden hun
   grootte op het scherm, zodat je ook ingezoomd precies kunt plaatsen en de uitlijning kunt beoordelen.
3. **Schaal:** vul de oppervlakte in die het plaatje toont, in m² (de app toont die), en druk op *Estimate scale*. Of typ
   de schaal zelf.
4. **Draaiing:** druk op *Auto-rotate*: de bridge zoekt de hoek waarbij het gemaaide pad het best in de tuin ligt. Of draai
   zelf tot het groene pad binnen de tuin ligt. Daarvoor is een pad nodig: laat de maaier eerst een stuk van die zone
   maaien. Het pad wordt bewaard (`/data/trail.json`), ook na een herstart. Is het pad afgekapt of ontbreekt het begin, klik dan
   op *Load the full path of the last mowing session from the log*: de bridge leest zijn eigen logbestand en laadt het hele
   pad van de laatste maaibeurt.
5. **Zones:** vul de zone-nummers in die dit plaatje toont (de pagina toont de nummers die de maaier tot nu toe meldde, bij
   jou is dat 6 voor de achtertuin). Laat het leeg voor een overzicht van de hele tuin. Opslaan.

Gebruik het dan met `view=NAAM`, bijvoorbeeld `/tile.svg?view=tuin`. Met `view=auto` kiest de bridge het plaatje van de
zone waarin de maaier zit, en anders het overzicht (het plaatje zonder zones). Met `span` krijg je een uitsnede:
`/tile.svg?view=tuin&span=12` toont 12 meter breed rond de maaier, en blijft binnen het plaatje.

Goed om te weten:

- **In rust het hele plaatje, tijdens het rijden ingezoomd:** gebruik één adres met beide instellingen, bijvoorbeeld
  `/tile.svg?view=tuin&span=12&idle_span=0`. Het kaartje schakelt bij elke verversing: staat de maaier in het dock, dan zie je de
  hele tuin; rijdt hij, dan zie je 12 meter rond de robot. Er is geen vloeiende overgang: een afbeelding die om de paar seconden
  wordt ververst, springt naar de andere weergave.
- **Eén plaatje is genoeg:** een overzicht met `span` om in te zoomen. Meerdere plaatjes (bijvoorbeeld een scherpe screenshot
  per zone) zijn alleen nodig als het inzoomen op het overzicht te korrelig is; elk plaatje wordt apart gekalibreerd.
- **Scherpte:** een plaatje van een paar honderd pixels breed wordt korrelig als je ver inzoomt.
- **Automatisch draaien is een hulpmiddel:** het zoekt een hoek waarbij het pad binnen de tuin ligt. Heeft het pad veel
  ruimte in de tuin, dan passen meerdere hoeken even goed (bij een test een paar graden ernaast). Het werkt het best met een
  pad van een hele maaibeurt. Controleer het resultaat en corrigeer zo nodig met de draaiing.
- Het plaatje wordt in de afbeelding ingesloten (een SVG laadt geen externe plaatjes), dus de afbeelding is zo groot als het
  plaatje. Dat is geen probleem op je eigen netwerk.
- De pagina's hebben geen wachtwoord; zet de poort alleen open op je eigen netwerk. Uploaden, kalibreren en verwijderen
  kan alleen met speciale verzoeken, zodat een andere website in je browser dat niet kan doen.

**Inbouwen in Main UI.** De kaart is een gewone afbeelding op een URL. Volgens de openHAB-documentatie heeft de
`oh-image-card` een `url` (gebruikt als er geen Image-Item is opgegeven) en een `refreshInterval` in milliseconden;
een ververstijd van 5000 is genoeg (de maaier stuurt elke 2 tot 20 seconden een positie). De server stuurt
`Cache-Control: no-store`.

## 4. Eigenschappen en grenzen om rekening mee te houden

- **Meetfrequentie.** Posities komen elke 2 tot 5 s tijdens maaien en elke 8 tot 20 s tijdens handmatig rijden. In het
  dock stuurt de maaier niets bruikbaars; de laatste positie blijft staan.
- **Resolutie.** De positie is afgerond op 0,25 m: het pad is hoekig en grof.
- **Statusbronnen.** Status komt normaal als push uit de cloud. Is er 90 s niets, dan pollt de bridge elke 60 s via
  HTTP. Daardoor kan `state` soms enkele tientallen seconden achterlopen.
- **Beschikbaarheid.** De maaier wordt `offline` als er 300 s geen enkele status binnenkwam.
- **Alleen gemeten op een Navimow i208 AWD.** Andere modellen kunnen velden missen (LiDAR-modellen sturen waarschijnlijk
  vaker posities), en een veld kan ontbreken zonder dat dat een fout is.
- **Niet beschikbaar:** de tuingrens en zones als vlak, GPS-coördinaten, de naam van een zone (alleen een ID),
  maaihoogte en schema's. Die zitten niet in de officiële API.

## 5. Handig bij het bouwen

Alle topics van de bridge in één keer zien:

```bash
mosquitto_sub -h <broker> -t 'navimow/#' -v
```

De bridge schrijft gedetailleerde logs naar `/data/logs` (zie de README), met elk ruw bericht van de maaier. Zet
`LOG_LEVEL=DEBUG` voor de ruwe berichten in de container-log.
