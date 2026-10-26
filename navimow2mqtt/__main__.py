"""Entry point.

    python -m navimow2mqtt           run the bridge
    python -m navimow2mqtt login     sign in once and store the token
    python -m navimow2mqtt rebuild-trail   recover the path of the last mowing session from the event log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

from . import __version__, datalog, i18n, web
from .api import ApiError, NavimowApi
from .auth import (AuthError, AuthRequired, LoginService, TokenManager, build_authorize_url,
                   extract_code)
from .bridge import Bridge
from .maps import MapStore
from .config import ConfigError, parse


def login(config) -> int:
    tm = TokenManager(config.navimow)
    print("1. Open this URL in a browser and sign in with your Navimow account:\n")
    print("   " + build_authorize_url(config.navimow) + "\n")
    print("2. After signing in your browser is redirected to a page that may show an error or")
    print("   open Home Assistant. That is fine: copy the FULL address from the address bar")
    print("   (it contains 'code=...') and paste it here.\n")
    try:
        pasted = input("Redirect URL or code: ")
    except EOFError:
        print("No input. Run with a terminal attached (docker compose run --rm ...).")
        return 2
    code = extract_code(pasted)
    if not code:
        print("No code found.")
        return 2
    try:
        token = tm.exchange_code(code)
    except (AuthError, OSError) as exc:
        print("Login failed: %s" % exc)
        return 1
    # Field names only, never values.
    print("\nToken stored in %s" % config.navimow.token_file)
    print("Fields returned by the server: %s" % ", ".join(sorted(
        k for k in token if k not in ("expires_at", "expires_assumed", "obtained_at"))))
    if token.get("expires_assumed"):
        print("Server gave no expiry; assuming 24 h until the API says otherwise.")
    else:
        print("Token valid for about %.1f hours." % ((token["expires_at"] - token["obtained_at"]) / 3600))
    if token.get("refresh_token"):
        try:
            tm._refresh(token)
            print("Refresh test: OK. The bridge will keep the token fresh by itself.")
        except (AuthRequired, AuthError, OSError) as exc:
            print("Refresh test FAILED (%s). You will have to log in again when the token expires." % exc)
    else:
        print("NO refresh_token issued: the token cannot renew itself and will expire.")
    return 0


def announce_login_needed(config, login_service) -> None:
    """Before the first login there is no Bridge (and so no MQTT connection) yet. Send a
    one-off retained message so openHAB & co. already know that a login is needed and where."""
    from paho.mqtt import publish

    from . import ha
    t = ha.topics(config.mqtt.base_topic, "bridge")
    msgs = [{"topic": t["auth"], "payload": "required", "retain": True},
            {"topic": t["login_url"], "payload": login_service.new_url(), "retain": True}]
    if config.web_port and config.public_url:
        msgs.append({"topic": t["web_url"], "payload": config.public_url, "retain": True})
    m = config.mqtt
    try:
        publish.multiple(msgs, hostname=m.host, port=m.port, client_id=m.client_id + "-announce",
                         auth={"username": m.username, "password": m.password or ""} if m.username else None)
        logging.info("announced on MQTT that a login is needed: %s", ", ".join(x["topic"] for x in msgs))
        if not (config.web_port and config.public_url):
            logging.info("%s was not announced: PUBLIC_URL is %s", t["web_url"],
                         "not set" if not config.public_url else "set, but WEB_PORT is 0")
    except Exception as exc:  # noqa: BLE001 - the broker may simply not be reachable yet
        logging.warning("could not announce login on MQTT: %s", exc)


def rebuild_trail(config, args) -> int:
    """Recover the path of the last mowing session from the event log (stop the bridge first)."""
    from . import rebuild
    if not config.log_dir or not config.trail_file:
        print("LOG_DIR and TRAIL_FILE must both be set (they are in the Docker image).")
        return 2
    name = args.name
    if not name:
        try:
            with open(config.trail_file, "r", encoding="utf-8") as fh:
                names = list(json.load(fh).get("trails", {}))
        except (OSError, ValueError, AttributeError):
            names = []
        if len(names) == 1:
            name = names[0]
        else:
            seen = sorted({e.get("device") for e in rebuild.read_events(config.log_dir)
                           if e.get("kind") == "state_change" and e.get("device")})
            if len(seen) != 1:
                print("Several or no mowers found (%s): pass --name." % (", ".join(seen) or "none"))
                return 2
            name = seen[0]
    try:
        result = rebuild.rebuild(config.log_dir, name, args.since, args.until, args.device_id,
                                 config.path_max_points)
    except rebuild.RebuildError as exc:
        print("Could not rebuild the path: %s" % exc)
        return 1
    rebuild.write_trail(config.trail_file, name, result)
    print("Rebuilt the path of %s: %d positions (%d kept) from %s to %s." % (
        name, result["total"], len(result["points"]), result["started"], result["ended"] or "now"))
    print("Written to %s. Start the bridge again." % config.trail_file)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Navimow cloud to MQTT bridge")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "login", "rebuild-trail"))
    parser.add_argument("--name", help="rebuild-trail: the mower name (as in the MQTT topics)")
    parser.add_argument("--since", help="rebuild-trail: start of the session (ISO time, default: the last session)")
    parser.add_argument("--until", help="rebuild-trail: end of the session (ISO time)")
    parser.add_argument("--device-id", help="rebuild-trail: the mower id, when the log has several")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = parse()
    except ConfigError as exc:
        logging.error("invalid configuration: %s", exc)
        return 2
    i18n.set_language(config.language)
    if args.command == "login":
        return login(config)
    if args.command == "rebuild-trail":
        return rebuild_trail(config, args)

    if config.log_dir:
        try:
            from logging.handlers import RotatingFileHandler
            datalog.init(config.log_dir, config.log_keep_days, config.log_max_mb)
            fh = RotatingFileHandler(os.path.join(config.log_dir, "navimow2mqtt.log"),
                                     maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logging.getLogger().addHandler(fh)
        except OSError as exc:
            logging.warning("cannot write logs to %s: %s (continuing without log files)",
                            config.log_dir, exc)
    logging.info("navimow2mqtt %s starting (language %s, time zone %s)", __version__, config.language,
                 config.timezone)
    datalog.log("start", version=__version__, language=config.language, timezone=config.timezone,
                mqtt_host=config.mqtt.host, mqtt_port=config.mqtt.port,
                base_topic=config.mqtt.base_topic, http_poll_interval=config.http_poll_interval,
                mqtt_stale_seconds=config.mqtt_stale_seconds, offline_after=config.offline_after,
                path_max_points=config.path_max_points)
    tokens = TokenManager(config.navimow)
    api = NavimowApi(config.navimow, tokens)
    login_service = LoginService(config.navimow, tokens)
    map_store = None
    if config.maps_dir:
        try:
            map_store = MapStore(config.maps_dir)
        except OSError as exc:
            logging.warning("cannot use the map pictures directory %s: %s (continuing without)", config.maps_dir, exc)
    web_server = None
    if config.web_port:
        try:
            web_server = web.start(config.web_port, login_service, tokens, map_store)
        except OSError as exc:
            logging.error("cannot start the login page on port %d: %s", config.web_port, exc)

    stop_requested = False

    def _stop(*_):
        nonlocal stop_requested
        stop_requested = True
        if bridge is not None:
            bridge.stop.set()

    bridge = None
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    devices = []
    warned_login = False
    while not stop_requested and not devices:
        wait = 60
        try:
            devices = api.devices()
            if not devices:
                logging.error("no mowers found on this Navimow account; retrying in 60 s")
        except AuthRequired as exc:
            wait = 3   # no network call involved: pick up a fresh login quickly
            if not warned_login:
                warned_login = True
                if config.web_port:
                    how = "open %s in a browser" % (config.public_url or
                                                    "http://<address of this host>:%d (PUBLIC_URL is not set)"
                                                    % config.web_port)
                else:
                    how = ("run: docker compose run --rm navimow2mqtt python -m navimow2mqtt login")
                logging.error("login required (%s); %s", exc, how)
                announce_login_needed(config, login_service)
        except ApiError as exc:
            logging.error("cannot list mowers: %s; retrying in 60 s", exc)
        if not devices:
            for _ in range(wait):
                if stop_requested:
                    return 0
                time.sleep(1)
    if stop_requested:
        return 0

    bridge = Bridge(config, api, devices, login_service, map_store)
    if web_server is not None:
        web_server.map_provider = bridge.map_data     # the tile and the map pictures' calibration page
        web_server.trail_rebuilder = bridge.rebuild_trail    # the "load the path from the log" button
    logging.info("found %d mower(s): %s", len(devices),
                 ", ".join("%s (%s)" % (m.name, m.dev.id) for m in bridge.mowers.values()))
    if stop_requested:
        return 0
    bridge.run()
    datalog.log("stop")
    datalog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
