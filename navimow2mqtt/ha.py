"""Topics and Home Assistant MQTT discovery payloads."""
from __future__ import annotations

import json
from typing import Dict, Iterator, Tuple

from .models import Device


def topics(base: str, name: str) -> Dict[str, str]:
    return {
        "state": "%s/%s/state" % (base, name),
        "availability": "%s/%s/availability" % (base, name),
        "info": "%s/%s/info" % (base, name),
        "command": "%s/%s/command" % (base, name),
        "event": "%s/%s/event" % (base, name),
        "location": "%s/%s/location" % (base, name),
        "position": "%s/%s/position" % (base, name),
        "path": "%s/%s/path" % (base, name),
        "session": "%s/%s/session" % (base, name),
        "stats": "%s/%s/stats" % (base, name),
        "bridge": "%s/bridge/status" % base,
        "auth": "%s/bridge/auth" % base,
        "login": "%s/bridge/login" % base,
        "login_url": "%s/bridge/login_url" % base,
        "web_url": "%s/bridge/web_url" % base,
        "login_result": "%s/bridge/login_result" % base,
    }


def _device(name: str, dev: Device) -> dict:
    d = {"identifiers": ["navimow2mqtt_%s" % name], "name": dev.name or "Navimow %s" % name,
         "manufacturer": "Segway Navimow", "model": dev.model or "Navimow"}
    if dev.firmware:
        d["sw_version"] = dev.firmware
    if dev.serial:
        d["serial_number"] = dev.serial
    return d


def discovery_messages(prefix: str, base: str, name: str, dev: Device,
                       stats: bool = False) -> Iterator[Tuple[str, str]]:
    t = topics(base, name)
    device = _device(name, dev)
    avail = [{"topic": t["bridge"]}, {"topic": t["availability"]}]

    def uid(key: str) -> str:
        return "navimow2mqtt_%s_%s" % (name, key)

    mower = uid("mower")
    yield "%s/lawn_mower/%s/config" % (prefix, mower), json.dumps({
        "name": None, "unique_id": mower, "device": device,
        "availability": avail, "availability_mode": "all",
        "activity_state_topic": t["state"], "activity_value_template": "{{ value_json.activity }}",
        "start_mowing_command_topic": t["command"], "start_mowing_command_template": "start",
        "pause_command_topic": t["command"], "pause_command_template": "pause",
        "dock_command_topic": t["command"], "dock_command_template": "dock",
    })

    sensors = [
        ("battery", "Battery", "battery", "%", "measurement", False, True),
        ("state", "State", None, None, None, False, True),
        ("signal_strength", "Signal strength", None, None, "measurement", True, False),
    ]
    for key, label, dclass, unit, sclass, diagnostic, enabled in sensors:
        p = {"name": label, "unique_id": uid(key), "device": device,
             "state_topic": t["state"], "value_template": "{{ value_json.%s }}" % key,
             "availability": avail, "availability_mode": "all"}
        if dclass:
            p["device_class"] = dclass
        if unit:
            p["unit_of_measurement"] = unit
        if sclass:
            p["state_class"] = sclass
        if diagnostic:
            p["entity_category"] = "diagnostic"
        if not enabled:
            p["enabled_by_default"] = False
        yield "%s/sensor/%s/config" % (prefix, uid(key)), json.dumps(p)

    # From the `location` channel: metres from the dock (not GPS). Off by default because
    # not every model sends it.
    for key, label, unit in (("x", "Position X", "m"), ("y", "Position Y", "m"),
                             ("distance_m", "Distance from dock", "m"),
                             ("heading_deg", "Heading", "°"), ("course_deg", "Course", "°"),
                             ("progress_pct", "Mow progress", "%"),
                             ("zone", "Current zone", None)):
        p = {"name": label, "unique_id": uid("pos_" + key), "device": device,
             "state_topic": t["position"], "value_template": "{{ value_json.%s }}" % key,
             "availability": avail, "availability_mode": "all", "enabled_by_default": False,
             "entity_category": "diagnostic"}
        if unit:
            p["unit_of_measurement"] = unit
        yield "%s/sensor/%s/config" % (prefix, uid("pos_" + key)), json.dumps(p)

    if stats:   # weekly totals from the `stats` topic (only when STATS_FILE is set)
        for key, label, unit, dclass in (("sessions", "Sessions this week", None, None),
                                         ("mowing_h", "Mowing time this week", "h", "duration")):
            p = {"name": label, "unique_id": uid("stats_" + key), "device": device,
                 "state_topic": t["stats"], "value_template": "{{ value_json.%s }}" % key,
                 "availability": [{"topic": t["bridge"]}], "state_class": "measurement"}
            if unit:
                p["unit_of_measurement"] = unit
            if dclass:
                p["device_class"] = dclass
            yield "%s/sensor/%s/config" % (prefix, uid("stats_" + key)), json.dumps(p)


def bridge_discovery(prefix: str, base: str) -> Iterator[Tuple[str, str]]:
    """A problem sensor that turns on when the Navimow login has to be renewed."""
    t = topics(base, "bridge")
    key = "navimow2mqtt_auth_problem"
    yield "%s/binary_sensor/%s/config" % (prefix, key), json.dumps({
        "name": "Login required", "unique_id": key, "device_class": "problem",
        "state_topic": t["auth"], "payload_on": "required", "payload_off": "ok",
        "entity_category": "diagnostic",
        "availability": [{"topic": t["bridge"]}],
        "device": {"identifiers": ["navimow2mqtt_bridge"], "name": "Navimow2Mqtt",
                   "manufacturer": "navimow2mqtt"},
    })
