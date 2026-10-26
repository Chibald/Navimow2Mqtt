"""Texts shown to people: the sign-in page, its messages and the map captions.

The language comes from LANGUAGE (en | nl), English by default. Log lines, MQTT payloads and the
Home Assistant entity names are not translated. To add a language, add a block to STRINGS with the
same keys as "en"; a missing key falls back to English.
"""
from __future__ import annotations

from typing import Dict, Optional

LANGUAGES = ("en", "nl")
_current = "en"

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "page.subtitle": "Bridge sign-in",
        "pill.in": "Signed in",
        "pill.out": "Not signed in",
        "head.in": "You’re signed in",
        "head.out": "Sign in to Navimow",
        "sub.in.auto": "The token renews itself.",
        "sub.in.manual": "Sign in again when the token expires.",
        "sub.out": "The bridge needs access to your Navimow account. You sign in on Navimow’s own site; "
                   "your password never reaches this page.",
        "tile.token": "Access token",
        "tile.refresh": "Refresh",
        "val.auto": "Automatic",
        "val.manual": "Manual",
        "val.renewing": "Renewing",
        "val.min": "{n} min",
        "val.hour_min": "{h} h {m:02d} min",
        "btn.signin": "Sign in to Navimow",
        "btn.signin_again": "Sign in again",
        "btn.map": "View map",
        "btn.submit": "Sign in",
        "note.norefresh": "The server issued no refresh token, so you will have to sign in again when the "
                          "token expires.",
        "help.summary": "Trouble signing in?",
        "help.intro": "Only needed if the button above does not bring you back to this page, for example because "
                      "Navimow changed how its sign-in works. You then sign in by hand and paste the address you "
                      "were sent to.",
        "help.link": "Open the Navimow sign-in",
        "help.step1": "{link} and log in.",
        "help.step2": "You land on a page of <code>my.home-assistant.io</code>. It may show an error: that is fine, "
                      "you do not need Home Assistant. <b>Copy the whole address</b> from the address bar; it "
                      "contains <code>code</code>.",
        "help.step3": "Paste it below.",
        "msg.ok": "Signed in.",
        "msg.ok_norefresh": " Note: no refresh token was issued.",
        "msg.no_code": "No code found in what you pasted (it pointed to: {where}). The address must contain "
                       "‘code=’. You only get such an address after signing in to Navimow: do step 1 "
                       "first and copy the address of the page you land on afterwards.",
        "msg.not_address": "not an address",
        "msg.bad_state": "This sign-in was not started by this bridge (or it is too old). "
                         "Start again with a new sign-in link.",
        "msg.rejected": "Navimow rejected the code (expired or already used?): {err}",
        "msg.unreachable": "Could not reach Navimow: {err}",
        "msg.navimow_reported": "Navimow reported: {err}",
        "msg.no_code_received": "No code received.",
        "map.title": "Navimow map",
        "map.aria": "Map of the mowing session",
        "btn.maps": "Map pictures",
        "maps.title": "Map pictures",
        "maps.intro": "Upload screenshots of the Navimow app, or any picture of your garden, and calibrate each one. "
                      "The map tile can then show the mower on it.",
        "maps.col.name": "Name",
        "maps.col.size": "Size",
        "maps.col.status": "Status",
        "maps.col.zones": "Zones",
        "maps.calibrated": "Calibrated",
        "maps.not_calibrated": "Not calibrated",
        "maps.overview": "overview",
        "maps.btn.calibrate": "Calibrate",
        "maps.btn.delete": "Delete",
        "maps.confirm_delete": "Delete this picture?",
        "maps.empty": "No pictures yet.",
        "maps.upload.title": "Add a picture",
        "maps.upload.name": "Name (a-z, 0-9, - and _)",
        "maps.upload.file": "PNG or JPEG, at most 3 MB",
        "maps.upload.btn": "Upload",
        "maps.upload.done": "Uploaded. Now calibrate it.",
        "maps.upload.fail": "Upload failed: {err}",
        "maps.zones_seen": "Zone ids seen so far: {ids}.",
        "maps.zones_none": "No zone ids seen yet: they show up once the mower has mowed.",
        "maps.usage.title": "Using a picture",
        "maps.usage.text": "Add view=NAME to the tile address, for example {example}. view=auto picks the picture of "
                           "the zone the mower is in. span=METRES zooms in on that many metres around the mower.",
        "maps.back": "Back",
        "cal.title": "Calibrate {name}",
        "cal.step1": "<b>Dock:</b> click the dock (the charging station) in the picture. Drag to fine-tune.",
        "cal.step2": "<b>Scale:</b> enter the area the picture shows, in m² (the app shows it), and press "
                     "Estimate scale. Or type the scale yourself.",
        "cal.step3": "<b>Rotation:</b> press Auto-rotate so the green path matches the garden, or turn it by hand "
                     "until the path lies inside the garden.",
        "cal.step4": "<b>Zones:</b> which zone ids does this picture show? Leave it empty for an overview of the "
                     "whole garden.",
        "cal.label.dock": "Dock (pixel)",
        "cal.label.scale": "Scale (pixels per metre)",
        "cal.label.rotation": "Rotation (degrees)",
        "cal.label.flip": "Mirror the picture",
        "cal.label.area": "Area shown (m²)",
        "cal.label.zones": "Zone ids (comma separated)",
        "cal.btn.estimate": "Estimate scale",
        "cal.btn.auto": "Auto-rotate",
        "cal.btn.save": "Save",
        "cal.path.some": "Path: {n} points from the current or last mowing session.",
        "cal.path.none": "No path recorded yet. Let the mower mow part of this zone, then reload this page.",
        "cal.saved": "Saved.",
        "cal.error": "Could not save: {err}",
        "cal.fit.ok": "Best match: {pct} % of the path lies inside the garden.",
        "cal.fit.nopath": "There is no path to match yet.",
        "cal.fit.noarea": "Enter the area first.",
        "cal.dock.unset": "Click the dock in the picture first.",
        "cal.scale.est": "Scale estimated at {s} pixels per metre.",
        "cal.mask.fail": "Could not read the picture.",
        "cal.btn.loadpath": "Load the full path of the last mowing session from the log",
        "cal.zoom.in": "Zoom in",
        "cal.zoom.out": "Zoom out",
        "cal.zoom.fit": "Whole picture",
        "cal.zoom.pan": "Move the picture",
        "cal.zoom.hint": "Zoom with the scroll wheel, the + and \u2212 buttons or two fingers. Click to place the dock; switch on Move the picture (or hold Shift, or use two fingers) to drag the picture instead.",
        "cal.loadpath.ok": "Loaded the path of the last mowing session: {n} positions.",
        "cal.loadpath.fail": "Could not load the path: {err}",
    },
    "nl": {
        "page.subtitle": "Bridge-login",
        "pill.in": "Ingelogd",
        "pill.out": "Niet ingelogd",
        "head.in": "Je bent ingelogd",
        "head.out": "Inloggen bij Navimow",
        "sub.in.auto": "Het token ververst zichzelf.",
        "sub.in.manual": "Log opnieuw in als het token verloopt.",
        "sub.out": "De bridge heeft toegang nodig tot je Navimow-account. Je logt in op de site van Navimow zelf; "
                   "je wachtwoord komt nooit op deze pagina.",
        "tile.token": "Toegangstoken",
        "tile.refresh": "Verversen",
        "val.auto": "Automatisch",
        "val.manual": "Handmatig",
        "val.renewing": "Wordt vernieuwd",
        "val.min": "{n} min",
        "val.hour_min": "{h} u {m:02d} min",
        "btn.signin": "Inloggen bij Navimow",
        "btn.signin_again": "Opnieuw inloggen",
        "btn.map": "Kaart bekijken",
        "btn.submit": "Inloggen",
        "note.norefresh": "De server gaf geen refresh-token uit, dus je moet opnieuw inloggen als het token "
                          "verloopt.",
        "help.summary": "Problemen met inloggen?",
        "help.intro": "Alleen nodig als de knop hierboven je niet terugbrengt naar deze pagina, bijvoorbeeld doordat "
                      "Navimow zijn login heeft gewijzigd. Je logt dan zelf in en plakt het adres waar je naartoe "
                      "gestuurd wordt.",
        "help.link": "Open de Navimow-login",
        "help.step1": "{link} en log in.",
        "help.step2": "Je komt op een pagina van <code>my.home-assistant.io</code>. Die toont misschien een fout: dat "
                      "is niet erg, je hebt Home Assistant niet nodig. <b>Kopieer het hele adres</b> uit de "
                      "adresbalk; het bevat het woord <code>code</code>.",
        "help.step3": "Plak het hieronder.",
        "msg.ok": "Ingelogd.",
        "msg.ok_norefresh": " Let op: er is geen refresh-token uitgegeven.",
        "msg.no_code": "Geen code gevonden in wat je plakte (het wees naar: {where}). Het adres moet ‘code=’ "
                       "bevatten. Zo’n adres krijg je pas nadat je bij Navimow bent ingelogd: doe eerst stap 1 "
                       "en kopieer daarna het adres van de pagina waar je terechtkomt.",
        "msg.not_address": "geen adres",
        "msg.bad_state": "Deze login is niet door deze bridge gestart (of is te oud). "
                         "Begin opnieuw met een nieuwe loginlink.",
        "msg.rejected": "Navimow weigerde de code (verlopen of al gebruikt?): {err}",
        "msg.unreachable": "Navimow is niet bereikbaar: {err}",
        "msg.navimow_reported": "Navimow meldde: {err}",
        "msg.no_code_received": "Geen code ontvangen.",
        "map.title": "Navimow-kaart",
        "map.aria": "Kaart van de maaibeurt",
        "btn.maps": "Kaartplaatjes",
        "maps.title": "Kaartplaatjes",
        "maps.intro": "Upload screenshots van de Navimow-app, of een ander plaatje van je tuin, en kalibreer ze. "
                      "Het kaartje kan de maaier er dan op tonen.",
        "maps.col.name": "Naam",
        "maps.col.size": "Formaat",
        "maps.col.status": "Status",
        "maps.col.zones": "Zones",
        "maps.calibrated": "Gekalibreerd",
        "maps.not_calibrated": "Niet gekalibreerd",
        "maps.overview": "overzicht",
        "maps.btn.calibrate": "Kalibreren",
        "maps.btn.delete": "Verwijderen",
        "maps.confirm_delete": "Dit plaatje verwijderen?",
        "maps.empty": "Nog geen plaatjes.",
        "maps.upload.title": "Plaatje toevoegen",
        "maps.upload.name": "Naam (a-z, 0-9, - en _)",
        "maps.upload.file": "PNG of JPEG, maximaal 3 MB",
        "maps.upload.btn": "Uploaden",
        "maps.upload.done": "Geüpload. Kalibreer het nu.",
        "maps.upload.fail": "Uploaden mislukt: {err}",
        "maps.zones_seen": "Tot nu toe geziene zone-nummers: {ids}.",
        "maps.zones_none": "Nog geen zone-nummers gezien: die verschijnen zodra de maaier heeft gemaaid.",
        "maps.usage.title": "Een plaatje gebruiken",
        "maps.usage.text": "Voeg view=NAAM toe aan het adres van het kaartje, bijvoorbeeld {example}. view=auto kiest het "
                           "plaatje van de zone waarin de maaier zit. span=METERS zoomt in op zoveel meter rond de maaier.",
        "maps.back": "Terug",
        "cal.title": "{name} kalibreren",
        "cal.step1": "<b>Station:</b> klik op het laadstation in het plaatje. Sleep om bij te stellen.",
        "cal.step2": "<b>Schaal:</b> vul de oppervlakte in die het plaatje toont, in m² (de app toont die), en druk op "
                     "Schaal schatten. Of typ de schaal zelf.",
        "cal.step3": "<b>Draaiing:</b> druk op Automatisch draaien zodat het groene pad bij de tuin past, of draai "
                     "zelf tot het pad binnen de tuin ligt.",
        "cal.step4": "<b>Zones:</b> welke zone-nummers toont dit plaatje? Laat leeg voor een overzicht van de hele tuin.",
        "cal.label.dock": "Station (pixel)",
        "cal.label.scale": "Schaal (pixels per meter)",
        "cal.label.rotation": "Draaiing (graden)",
        "cal.label.flip": "Plaatje spiegelen",
        "cal.label.area": "Getoonde oppervlakte (m²)",
        "cal.label.zones": "Zone-nummers (gescheiden met komma)",
        "cal.btn.estimate": "Schaal schatten",
        "cal.btn.auto": "Automatisch draaien",
        "cal.btn.save": "Opslaan",
        "cal.path.some": "Pad: {n} punten van de huidige of laatste maaibeurt.",
        "cal.path.none": "Nog geen pad opgenomen. Laat de maaier een stuk van deze zone maaien en laad deze pagina daarna opnieuw.",
        "cal.saved": "Opgeslagen.",
        "cal.error": "Opslaan mislukt: {err}",
        "cal.fit.ok": "Beste overeenkomst: {pct} % van het pad ligt binnen de tuin.",
        "cal.fit.nopath": "Er is nog geen pad om mee te vergelijken.",
        "cal.fit.noarea": "Vul eerst de oppervlakte in.",
        "cal.dock.unset": "Klik eerst op het station in het plaatje.",
        "cal.scale.est": "Schaal geschat op {s} pixels per meter.",
        "cal.mask.fail": "Het plaatje kon niet worden gelezen.",
        "cal.btn.loadpath": "Het volledige pad van de laatste maaibeurt uit de log laden",
        "cal.zoom.in": "Inzoomen",
        "cal.zoom.out": "Uitzoomen",
        "cal.zoom.fit": "Hele plaatje",
        "cal.zoom.pan": "Plaatje verschuiven",
        "cal.zoom.hint": "Zoom met het scrollwiel, de knoppen + en \u2212 of met twee vingers. Klik om het station te plaatsen; zet Plaatje verschuiven aan (of houd Shift ingedrukt, of gebruik twee vingers) om het plaatje te slepen.",
        "cal.loadpath.ok": "Het pad van de laatste maaibeurt is geladen: {n} posities.",
        "cal.loadpath.fail": "Het pad kon niet worden geladen: {err}",
    },
}


def normalize(code: Optional[str]) -> Optional[str]:
    """'NL', 'nl-NL', 'nl_NL' -> 'nl'; None if the language is not available."""
    if not code:
        return None
    short = code.strip().lower().replace("_", "-").split("-")[0]
    return short if short in LANGUAGES else None


def set_language(code: str) -> str:
    global _current
    lang = normalize(code)
    if lang is None:
        raise ValueError("unknown language %r (choose from %s)" % (code, ", ".join(LANGUAGES)))
    _current = lang
    return lang


def current() -> str:
    return _current


def t(key: str, lang: Optional[str] = None, **kw) -> str:
    """Text for `key` in `lang` (default: the configured language); English when missing."""
    text = STRINGS.get(lang or _current, {}).get(key)
    if text is None:
        text = STRINGS["en"][key]
    return text.format(**kw) if kw else text
