import json
import os
import tempfile
import time
import unittest
from unittest import mock

from navimow2mqtt import ha
from navimow2mqtt.api import COMMANDS, mqtt_connection
from navimow2mqtt.auth import (AuthRequired, TokenManager, build_authorize_url,
                               extract_code)
from navimow2mqtt.bridge import device_names, slug
from navimow2mqtt.cloud import parse_topic
from navimow2mqtt.config import ConfigError, NavimowConfig, parse
from navimow2mqtt.models import Device, MowerState, extract_battery, normalize_state


class Models(unittest.TestCase):
    def test_state_mapping(self):
        self.assertEqual(normalize_state("isRunning"), "mowing")
        self.assertEqual(normalize_state("isIdel"), "idle")
        self.assertEqual(normalize_state("isDocking"), "returning")
        self.assertEqual(normalize_state(None), "unknown")

    def test_activity(self):
        self.assertEqual(MowerState.from_payload({"state": "isDocked"}).activity, "docked")
        self.assertEqual(MowerState.from_payload({"vehicleState": "isLifted"}).activity, "error")
        self.assertEqual(MowerState.from_payload({"state": "charging"}).activity, "docked")

    def test_battery_formats(self):
        self.assertEqual(extract_battery({"battery": "55"}), 55)
        http = {"capacityRemaining": [{"unit": "KWH", "rawValue": 1}, {"unit": "PERCENTAGE", "rawValue": 80}]}
        self.assertEqual(extract_battery(http), 80)
        self.assertIsNone(extract_battery({}))

    def test_json_keeps_raw_state(self):
        j = MowerState.from_payload({"state": "isPaused", "battery": 40}).to_json()
        self.assertEqual(j["state"], "paused")
        self.assertEqual(j["metrics"]["raw_state"], "isPaused")


class CourseDeg(unittest.TestCase):
    def test_course_from_moves(self):
        from navimow2mqtt.models import Location
        loc = Location()
        pose = lambda x, y: [{"type": 1, "postureX": str(x), "postureY": str(y), "postureTheta": "3.0"}]
        loc.update(pose(0, 0))
        self.assertNotIn("course_deg", loc.data)            # no movement known yet
        loc.update(pose(1, 0))
        self.assertEqual(loc.data["course_deg"], 0.0)       # +x
        loc.update(pose(1, 1))
        self.assertEqual(loc.data["course_deg"], 90.0)      # +y
        loc.update(pose(0, 0))
        self.assertEqual(loc.data["course_deg"], 225.0)
        loc.update(pose(0, 0))                              # same spot: unchanged
        self.assertEqual(loc.data["course_deg"], 225.0)

    def test_new_session_forgets_course(self):
        from navimow2mqtt.models import Location
        loc = Location()
        loc.update([{"type": 1, "postureX": "0", "postureY": "0"}])
        loc.update([{"type": 1, "postureX": "1", "postureY": "0"}])
        loc.new_session()
        self.assertNotIn("course_deg", loc.data)
        loc.update([{"type": 1, "postureX": "5", "postureY": "5"}])   # first point of the new session
        self.assertNotIn("course_deg", loc.data)


class EventLog(unittest.TestCase):
    def setUp(self):
        from navimow2mqtt import datalog
        self.datalog = datalog
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "logs")

    def tearDown(self):
        self.datalog.close()
        self.tmp.cleanup()

    def _events(self):
        out = []
        for name in sorted(os.listdir(self.dir)):
            if name.startswith("events-"):
                with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                    out += [json.loads(line) for line in fh if line.strip()]
        return out

    def test_noop_until_init(self):
        self.datalog.log("x", a=1)          # must not raise
        self.assertFalse(os.path.exists(self.dir))

    def test_writes_json_lines(self):
        self.datalog.init(self.dir)
        self.datalog.log("cloud_msg", device="d1", payload=[{"type": 1}])
        ev = self._events()
        self.assertEqual((ev[0]["kind"], ev[0]["device"], ev[0]["payload"]), ("cloud_msg", "d1", [{"type": 1}]))
        self.assertIn("ts", ev[0])

    def test_retention_removes_old_days(self):
        os.makedirs(self.dir)
        for day in ("2020-01-01", "2099-01-01"):
            open(os.path.join(self.dir, "events-%s.jsonl" % day), "w").close()
        self.datalog.init(self.dir, keep_days=14)
        self.datalog.log("x")
        names = os.listdir(self.dir)
        self.assertNotIn("events-2020-01-01.jsonl", names)
        self.assertIn("events-2099-01-01.jsonl", names)

    def test_daily_cap(self):
        self.datalog.init(self.dir, max_mb=0)    # 0 MB: the first real line already exceeds it
        self.datalog.log("a")
        self.datalog.log("b")
        self.assertEqual([e["kind"] for e in self._events()], ["log_capped"])

    def test_no_secrets_in_token_events(self):
        self.datalog.init(self.dir)
        cfg = NavimowConfig(token_file=os.path.join(self.tmp.name, "t.json"))
        tm = TokenManager(cfg, session=mock.Mock())
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"access_token": "SECRET-A", "refresh_token": "SECRET-R", "expires_in": 3600}
        tm.session.post.return_value = resp
        tm.exchange_code("SECRET-CODE")
        tm._refresh(tm._load())
        with open(os.path.join(self.dir, os.listdir(self.dir)[0]), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("SECRET", text)
        kinds = [e["kind"] for e in self._events()]
        self.assertEqual(kinds, ["login", "token_refresh"])

    def test_redact(self):
        from navimow2mqtt.cloud import redact
        self.assertEqual(redact("failed wss://h/p?tok=abc as bob", "/p?tok=abc", "bob", None, ""),
                         "failed wss://h*** as ***")

    def test_bridge_events(self):
        from navimow2mqtt.bridge import Bridge
        self.datalog.init(self.dir)
        b = Bridge(parse({}), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        m = b.mowers["d1"]
        b.apply(m, {"state": "isDocked", "battery": 80}, from_push=True)
        b.apply(m, {"state": "isRunning", "battery": 79}, from_push=True)
        b.apply(m, {"state": "isRunning", "battery": 78}, from_push=True)   # no change: not logged again
        b.apply(m, {"state": "isDocked", "battery": 70}, from_push=False)
        kinds = [e["kind"] for e in self._events()]
        # the last (HTTP) answer disagrees with the last MQTT push, so the watchdog steps in
        self.assertEqual(kinds, ["state_change", "session_start", "state_change", "http_status",
                                 "watchdog_mismatch", "cloud_force_reconnect", "session_end",
                                 "state_change"])


class Stats(unittest.TestCase):
    T0 = 1791922800.0     # 2026-10-13 (a Tuesday) 19:40 UTC

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "stats.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _tracker(self, **kw):
        from zoneinfo import ZoneInfo
        from navimow2mqtt.stats import StatsTracker
        kw.setdefault("tz", ZoneInfo("Europe/Amsterdam"))
        return StatsTracker(self.path, **kw)

    def _run(self, st, t0, plan):
        """plan: list of (state, seconds); one status update per minute. Returns the last result."""
        t, res = t0, (None, False)
        for state, seconds in plan:
            for _ in range(int(seconds // 60)):
                res = st.on_state("mower", state, 50, t)
                t += 60
        return res, t

    def test_session_is_split_per_state(self):
        st = self._tracker()
        (_, _), t = self._run(st, self.T0, [("docked", 120), ("mowing", 3600), ("paused", 600),
                                            ("mowing", 1800), ("returning", 300)])
        session, changed = st.on_state("mower", "docked", 40, t)
        self.assertIsNotNone(session)
        self.assertEqual(session["end_state"], "docked")
        self.assertFalse(session["partial"])
        self.assertTrue(session["counted"])
        self.assertAlmostEqual(session["mowing_s"], 5400, delta=120)
        self.assertAlmostEqual(session["paused_s"], 600, delta=120)
        self.assertAlmostEqual(session["returning_s"], 300, delta=120)
        self.assertEqual(session["battery_start"], 50)
        self.assertEqual(session["battery_end"], 40)
        snap = st.snapshot("mower", t)
        self.assertEqual((snap["sessions"], snap["total_sessions"], snap["active"]), (1, 1, False))
        self.assertAlmostEqual(snap["mowing_h"], 1.5, delta=0.05)

    def test_short_blip_is_reported_but_not_counted(self):
        st = self._tracker()
        (_, _), t = self._run(st, self.T0, [("docked", 120)])
        st.on_state("mower", "mowing", 50, t)
        session, _ = st.on_state("mower", "docked", 50, t + 30)      # 30 s of mowing, below the 60 s minimum
        self.assertFalse(session["counted"])
        self.assertEqual(session["mowing_s"], 30)
        self.assertEqual(st.snapshot("mower", t)["sessions"], 0)
        st.on_state("mower", "mowing", 50, t + 100)
        session, _ = st.on_state("mower", "docked", 50, t + 160)     # exactly 60 s: counts
        self.assertTrue(session["counted"])

    def test_session_already_running_at_first_sight_is_partial(self):
        st = self._tracker()
        (_, _), t = self._run(st, self.T0, [("mowing", 1800)])
        session, _ = st.on_state("mower", "docked", 50, t)
        self.assertTrue(session["partial"])
        self.assertTrue(session["counted"])

    def test_running_session_survives_a_restart(self):
        st = self._tracker()
        (_, _), t = self._run(st, self.T0, [("docked", 60), ("mowing", 1200)])
        st.save()
        st2 = self._tracker()                                   # the bridge restarted
        (_, _), t = self._run(st2, t + 100, [("mowing", 600)])   # 100 s gap: still counted
        session, _ = st2.on_state("mower", "docked", 50, t)
        self.assertFalse(session["partial"])
        self.assertAlmostEqual(session["mowing_s"], 1200 + 100 + 600, delta=180)

    def test_session_that_ended_while_the_bridge_was_down(self):
        st = self._tracker()
        (_, _), t = self._run(st, self.T0, [("docked", 60), ("mowing", 1800)])
        st.save()
        st2 = self._tracker()
        session, _ = st2.on_state("mower", "docked", 50, t + 7200)    # back two hours later, docked
        self.assertTrue(session["partial"])
        self.assertLess(session["duration_s"], 2200)                  # ends at the last time it was seen

    def test_week_follows_the_time_zone(self):
        import datetime
        st = self._tracker()
        sunday_late = datetime.datetime(2026, 10, 18, 21, 30, tzinfo=datetime.timezone.utc).timestamp()  # 23:30 local
        monday_early = datetime.datetime(2026, 10, 18, 22, 30, tzinfo=datetime.timezone.utc).timestamp()  # 00:30 local
        self.assertEqual(st.week_key(sunday_late), "2026-W42")
        self.assertEqual(st.week_key(monday_early), "2026-W43")
        self.assertEqual(st.week_start(monday_early), "2026-10-19")

    def test_previous_week_and_rollover(self):
        st = self._tracker()
        week = 7 * 86400
        for i in (0, 1):
            (_, _), t = self._run(st, self.T0 + i * week, [("docked", 60), ("mowing", 3600)])
            st.on_state("mower", "docked", 50, t)
        snap = st.snapshot("mower", self.T0 + week + 7200)
        self.assertEqual(snap["sessions"], 1)
        self.assertEqual(snap["previous_week"]["sessions"], 1)
        self.assertEqual(snap["total_sessions"], 2)
        self.assertEqual([w["week"] for w in snap["weeks"]], ["2026-W43", "2026-W42"])
        self.assertFalse(st.week_changed("mower", self.T0 + week + 7200))
        self.assertTrue(st.week_changed("mower", self.T0 + 2 * week))

    def test_file_stays_small(self):
        st = self._tracker(keep_weeks=12)
        for i in range(60):        # 60 weeks of sessions
            (_, _), t = self._run(st, self.T0 + i * 7 * 86400, [("docked", 60), ("mowing", 300)])
            st.on_state("mower", "docked", 50, t)
        self.assertLessEqual(len(st.snapshot("mower", t)["weeks"]), 12)
        self.assertEqual(st.snapshot("mower", t)["total_sessions"], 60)   # the grand total is kept
        self.assertLess(os.path.getsize(self.path), 4096)

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self._tracker().names(), [])

    def test_disabled_without_path(self):
        from navimow2mqtt.stats import StatsTracker
        st = StatsTracker("")
        st.on_state("mower", "mowing", 50, self.T0)
        st.save()                                # must not raise or write anything

    def test_bridge_publishes_session_and_stats(self):
        from navimow2mqtt.bridge import Bridge
        env = {"STATS_FILE": self.path, "TIMEZONE": "Europe/Amsterdam"}
        b = Bridge(parse(env), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        clock = {"t": self.T0}
        with mock.patch("time.time", lambda: clock["t"]):
            for raw, minutes in (("isDocked", 2), ("isRunning", 30), ("isDocked", 1)):
                for _ in range(minutes):
                    b.apply(b.mowers["d1"], {"state": raw, "battery": 50}, from_push=True)
                    clock["t"] += 60
        calls = {c.args[0]: json.loads(c.args[1]) for c in b.publish.call_args_list
                 if c.args[0] in ("navimow/garden/session", "navimow/garden/stats")}
        self.assertTrue(calls["navimow/garden/session"]["counted"])
        self.assertEqual(calls["navimow/garden/stats"]["sessions"], 1)
        retained = {c.args[0]: c.kwargs.get("retain") for c in b.publish.call_args_list}
        self.assertTrue(retained["navimow/garden/session"])
        self.assertTrue(retained["navimow/garden/stats"])

    def test_no_stats_topics_when_disabled(self):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse({}), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        b.apply(b.mowers["d1"], {"state": "isRunning"}, from_push=True)
        self.assertFalse([c for c in b.publish.call_args_list if c.args[0].endswith(("/stats", "/session"))])

    def test_discovery_has_stats_sensors_only_when_enabled(self):
        dev = Device(id="x", name="G")
        off = dict(ha.discovery_messages("homeassistant", "navimow", "g", dev))
        on = dict(ha.discovery_messages("homeassistant", "navimow", "g", dev, stats=True))
        key = "homeassistant/sensor/navimow2mqtt_g_stats_mowing_h/config"
        self.assertNotIn(key, off)
        self.assertEqual(json.loads(on[key])["unit_of_measurement"], "h")

    def test_config(self):
        self.assertEqual(parse({}).stats_file, "")
        self.assertEqual(parse({}).timezone, "UTC")
        self.assertEqual(parse({"TIMEZONE": "Europe/Amsterdam", "STATS_WEEKS": "8"}).stats_weeks, 8)
        with self.assertRaises(ConfigError):
            parse({"TIMEZONE": "Mars/Olympus"})


def make_png(w, h):
    """A valid PNG of w x h pixels (plain light grey), made with the standard library."""
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xee" * (3 * w) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_jpeg_header(w, h):
    """The start of a JPEG (SOI + SOF0 with the size): enough for reading its dimensions."""
    import struct
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + sof + b"\xff\xd9"


class MapPictures(unittest.TestCase):
    def setUp(self):
        from navimow2mqtt.maps import MapStore
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "maps")
        self.store = MapStore(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_image_header_is_read(self):
        from navimow2mqtt.maps import image_info
        self.assertEqual(image_info(make_png(447, 467)), ("png", "image/png", 447, 467))
        self.assertEqual(image_info(make_jpeg_header(800, 600)), ("jpg", "image/jpeg", 800, 600))

    def test_bad_pictures_are_refused(self):
        from navimow2mqtt.maps import MAX_IMAGE_BYTES, MapError, image_info
        for bad in (b"", b"GIF89a....", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"\x89PNG\r\n\x1a\n" + b"x" * 8,
                    make_png(50, 50), make_png(9000, 120)):
            with self.assertRaises(MapError):
                image_info(bad)
        with self.assertRaises(MapError):
            image_info(make_png(200, 200) + b"\x00" * MAX_IMAGE_BYTES)

    def test_names(self):
        from navimow2mqtt.maps import MapError
        for good in ("tuin", "achtertuin", "zone-6", "a_b", "x" * 40):
            self.store.check_name(good)
        for bad in ("", "Tuin", "../x", "a b", "x" * 41, "-x", "a/b", None, 5):
            with self.assertRaises(MapError):
                self.store.check_name(bad)

    def test_the_conversion_and_a_fit_agree(self):
        from navimow2mqtt.maps import fit, to_image
        cal = {"dock": [300.0, 420.0], "scale": 14.0, "rotation": 37.0, "flip": False}
        pairs = [((x, y), to_image(cal, x, y)) for x, y in ((0, 0), (5, 2), (-3, 8), (10, -4))]
        got = fit(pairs)
        self.assertAlmostEqual(got["scale"], 14.0, places=6)
        self.assertAlmostEqual(got["rotation"], 37.0, places=6)
        self.assertAlmostEqual(got["dock"][0], 300.0, places=5)
        self.assertAlmostEqual(got["dock"][1], 420.0, places=5)
        self.assertIsNone(fit([((0, 0), (1, 1))]))                       # one pair is not enough
        self.assertIsNone(fit([((1, 1), (1, 1)), ((1, 1), (5, 5))]))     # two identical positions either

    def test_conversion_directions(self):
        """Without rotation: +x goes right, +y goes up (a smaller pixel row)."""
        from navimow2mqtt.maps import to_image
        cal = {"dock": [100.0, 200.0], "scale": 10.0, "rotation": 0.0, "flip": False}
        self.assertEqual(to_image(cal, 0, 0), (100.0, 200.0))
        self.assertEqual(to_image(cal, 2, 0), (120.0, 200.0))
        self.assertEqual(to_image(cal, 0, 3), (100.0, 170.0))
        # a clockwise quarter turn on screen: "right" now points down
        cal90 = dict(cal, rotation=90.0)
        u, v = to_image(cal90, 2, 0)
        self.assertAlmostEqual(u, 100.0, places=6)
        self.assertAlmostEqual(v, 220.0, places=6)
        # mirrored
        u, v = to_image(dict(cal, flip=True), 2, 0)
        self.assertAlmostEqual(u, 80.0, places=6)

    def test_put_calibrate_and_read_back(self):
        view = self.store.put_image("achtertuin", make_png(447, 467))
        self.assertEqual((view["w"], view["h"], view["dock"]), (447, 467, None))
        self.assertIsNone(self.store.background("achtertuin"))            # not calibrated yet
        self.store.calibrate("achtertuin", [350, 420], 15.5, 200, False, [6])
        bg = self.store.background("achtertuin")
        self.assertTrue(bg["uri"].startswith("data:image/png;base64,"))
        self.assertEqual((bg["dock"], bg["scale"], bg["rotation"], bg["zones"]), ([350.0, 420.0], 15.5, -160.0, [6]))
        # it survives a restart
        from navimow2mqtt.maps import MapStore
        again = MapStore(self.dir)
        self.assertEqual(again.get("achtertuin")["zones"], [6])
        self.assertIsNotNone(again.background("achtertuin"))

    def test_a_new_picture_of_the_same_size_keeps_the_calibration_a_different_size_resets_it(self):
        self.store.put_image("t", make_png(300, 300))
        self.store.calibrate("t", [10, 20], 12, 5)
        self.assertEqual(self.store.put_image("t", make_png(300, 300))["scale"], 12.0)
        self.assertIsNone(self.store.put_image("t", make_png(400, 300))["scale"])

    def test_calibration_is_validated(self):
        from navimow2mqtt.maps import MapError
        self.store.put_image("t", make_png(300, 300))
        for args in (([10, 20], 0.01, 0), ([10, 20], 9999, 0), ([10, 20], "x", 0), ([10], 10, 0), (None, 10, 0),
                     ([10, 20], float("nan"), 0), ([5000, 20], 10, 0)):
            with self.assertRaises(MapError, msg=str(args)):
                self.store.calibrate("t", *args)
        with self.assertRaises(MapError):
            self.store.calibrate("t", [10, 20], 10, 0, False, ["x"])
        with self.assertRaises(MapError):
            self.store.calibrate("nope", [10, 20], 10, 0)

    def test_replace_delete_and_limits(self):
        from navimow2mqtt.maps import MAX_VIEWS, MapError
        self.store.put_image("a", make_png(200, 200))
        self.store.put_image("a", make_jpeg_header(200, 200))               # png -> jpg: the old file goes
        self.assertEqual(sorted(f for f in os.listdir(self.dir) if f.startswith("a.")), ["a.jpg"])
        self.assertTrue(self.store.delete("a"))
        self.assertFalse(self.store.delete("a"))
        self.assertEqual([f for f in os.listdir(self.dir) if f != "maps.json"], [])
        for i in range(MAX_VIEWS):
            self.store.put_image("v%d" % i, make_png(200, 200))
        with self.assertRaises(MapError):
            self.store.put_image("one-too-many", make_png(200, 200))
        self.store.put_image("v0", make_png(200, 200))                       # replacing is always allowed

    def test_zones_seen_and_picking_a_view(self):
        self.store.put_image("overview", make_png(400, 400))
        self.store.put_image("achter", make_png(400, 400))
        self.store.put_image("voor", make_png(400, 400))
        self.assertIsNone(self.store.pick(6))                                 # nothing calibrated yet
        self.store.calibrate("overview", [200, 200], 10, 0, False, [])
        self.store.calibrate("achter", [200, 200], 20, 0, False, [6])
        self.assertEqual(self.store.pick(6), "achter")                        # the zone's own view
        self.assertEqual(self.store.pick(4), "overview")                      # unknown zone: the overview
        self.assertEqual(self.store.pick(None), "overview")
        self.assertEqual(self.store.pick("junk"), "overview")
        self.store.note_zone(6)
        self.store.note_zone("6")
        self.store.note_zone(4)
        self.store.note_zone("x")
        self.assertEqual(self.store.zones_seen(), [4, 6])

    def test_corrupt_file_is_ignored(self):
        from navimow2mqtt.maps import MapStore
        with open(os.path.join(self.dir, "maps.json"), "w") as fh:
            fh.write("{broken")
        self.assertEqual(MapStore(self.dir).names(), [])


class TileView(unittest.TestCase):
    """The map tile: a lawn that gets mowed. Image only, no text, transparent corners."""
    ZIGZAG = [[0, 0.5], [0, 3], [0.25, 3], [0.5, 3], [0.5, 0.5], [0.75, 0.5], [1.0, 0.5], [1.0, 3]]   # up, down, up
    DATA = {"name": "Mower", "state": "mowing", "battery": 59, "progress_pct": 4.1,
            "points": [[-0.25, 1.5], [0.25, 1.75], [0.75, 2.0], [1.5, 2.25]],
            "x": 1.5, "y": 2.25, "course_deg": 25.0, "position_age_s": 3}

    def _root(self, svg):
        import xml.etree.ElementTree as ET
        return ET.fromstring(svg)

    def test_the_image_is_only_the_tile_with_transparent_corners(self):
        from navimow2mqtt import tileview
        W, H = tileview.tile_size(640)
        svg = tileview.render_tile(self.DATA, width=640)
        root = self._root(svg)
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual((root.get("width"), root.get("height")), (str(W), str(H)))
        clip = root.find(".//%sclipPath/%srect" % (ns, ns))
        self.assertEqual((clip.get("x"), clip.get("y"), clip.get("width"), clip.get("height"), clip.get("rx")),
                         ("0", "0", str(W), str(H), "18"))
        # everything that is drawn sits inside that clip, so the corners stay transparent
        self.assertEqual([c.tag for c in root if c.tag in (ns + "rect", ns + "polyline", ns + "g")],
                         [ns + "g"])
        self.assertEqual(root.find(ns + "g").get("clip-path"), "url(#tile)")

    def test_no_text_at_all(self):
        from navimow2mqtt import tileview
        for data in (self.DATA, None, {}):
            root = self._root(tileview.render_tile(data))
            self.assertEqual(list(root.iter("{http://www.w3.org/2000/svg}text")), [])
        svg = tileview.render_tile(self.DATA)
        for word in ("Mower", "mowing", "dock", "card", "legend"):
            self.assertNotIn(">%s<" % word, svg)

    def test_the_lawn_is_dark_green_and_the_mowed_lanes_are_lighter(self):
        from navimow2mqtt import tileview
        svg = tileview.render_tile(self.DATA, theme="light")
        self.assertIn("--lawn:#6fa85a", svg)               # the design's lawn
        self.assertIn("--lane-a:#a9d58e", svg)             # the design's mowed greens
        self.assertIn("--lane-b:#9bcb80", svg)
        self.assertIn('class="lawn"', svg)

    def test_lanes_split_where_the_mower_turns_around(self):
        from navimow2mqtt.tileview import lanes
        self.assertEqual(len(lanes([(0, 0), (1, 0), (2, 0), (3, 0.2)])), 1)          # one straight run
        self.assertEqual(len(lanes([(0, 0), (2, 0), (0, 0.25)])), 2)                  # out and back
        self.assertEqual(len(lanes([tuple(p) for p in self.ZIGZAG])), 3)              # up, down, up
        self.assertEqual(len(lanes([(0, 0), (0, 0), (1, 0), (1, 0)])), 1)             # standing still is ignored
        self.assertEqual(lanes([(1, 1)]), [[(1, 1)]])
        self.assertEqual(lanes([]), [])
        # a lane starts where the previous one ended: the swath has no gap
        two = lanes([(0, 0), (2, 0), (0, 0.25)])
        self.assertEqual(two[1][0], two[0][-1])

    def test_alternating_lanes_get_alternating_shades(self):
        import re
        from navimow2mqtt import tileview
        svg = tileview.render_tile({"points": self.ZIGZAG, "x": 1, "y": 3, "state": "mowing"})
        classes = re.findall(r'<polyline class="(lane-[ab])"', svg)
        self.assertEqual(classes, ["lane-a", "lane-b", "lane-a"])

    def test_dock_and_robot(self):
        from navimow2mqtt import tileview
        svg = tileview.render_tile(self.DATA)
        for needle in ('class="dock"', 'class="led"', 'class="body"', 'class="wheel"'):
            self.assertIn(needle, svg)
        empty = tileview.render_tile(None)
        self.assertIn('class="dock"', empty)
        self.assertNotIn('class="body"', empty)
        self.assertNotIn("<polyline", empty)

    def test_led_and_pulse_follow_the_state(self):
        from navimow2mqtt import tileview
        for state, led, pulse in (("mowing", "#4fd17a", "#ffffff"), ("paused", "#e8a33a", None),
                                  ("error", "#e04b3f", "#e04b3f"), ("returning", "#4fd17a", "#2f7fc1"),
                                  ("docked", "#4fd17a", None)):
            svg = tileview.render_tile(dict(self.DATA, state=state))
            self.assertIn('fill="%s"/>' % led, svg, state)
            if pulse:
                self.assertIn('class="pulse" cx="0" cy="0" r="14" fill="%s"' % pulse, svg, state)
            else:
                self.assertNotIn('class="pulse"', svg, state)

    def test_stale_position_is_grey_and_does_not_pulse(self):
        from navimow2mqtt import tileview
        svg = tileview.render_tile(dict(self.DATA, position_age_s=999))
        self.assertNotIn('class="pulse"', svg)
        self.assertIn('opacity=".55"', svg)

    def test_robot_points_along_the_course(self):
        """0 deg = right, 90 = up, 180 = left, 270 = down; the robot is drawn facing 'up' at rotate(0)."""
        import re
        from navimow2mqtt import tileview
        for course, expected in ((0.0, 90.0), (90.0, 0.0), (180.0, -90.0), (270.0, 180.0)):
            svg = tileview.render_tile(dict(self.DATA, course_deg=course))
            angle = float(re.search(r'<g transform="rotate\(([-0-9.]+)\)">', svg).group(1))
            self.assertAlmostEqual(abs(angle), abs(expected), delta=0.5, msg="course %s" % course)
        svg = tileview.render_tile(dict(self.DATA, course_deg=0.0), rotate=90)   # a map turned 90 deg: +x points up
        self.assertAlmostEqual(float(re.search(r'<g transform="rotate\(([-0-9.]+)\)">', svg).group(1)), 0.0, delta=0.5)

    def test_up_is_up_and_x_is_right(self):
        import re
        from navimow2mqtt import tileview
        svg = tileview.render_tile({"points": [[0, 0], [2, 0], [2, 2]], "x": 2, "y": 2})
        lane = re.search(r'<polyline class="lane-a" stroke-width="[0-9.]+" points="([^"]+)"', svg).group(1).split()
        (x0, y0), (x1, y1) = [tuple(map(float, p.split(","))) for p in lane[:2]]
        self.assertGreater(x1, x0)                          # +x is to the right
        later = [tuple(map(float, p.split(","))) for p in lane][-1]
        self.assertLess(later[1], y1)                       # +y is up on screen (smaller pixel y)

    def test_rotate_and_flip_move_the_path(self):
        from navimow2mqtt import tileview
        base = tileview.render_tile(self.DATA)
        self.assertNotEqual(base, tileview.render_tile(self.DATA, rotate=90))
        self.assertNotEqual(base, tileview.render_tile(self.DATA, flip="x"))
        self.assertEqual(base, tileview.render_tile(self.DATA, rotate=360))      # not a valid step: ignored

    def test_the_swath_has_a_minimum_width(self):
        import re
        from navimow2mqtt import tileview
        svg = tileview.render_tile(self.DATA, swath_m=0.05)
        widths = [float(w) for w in re.findall(r'<polyline class="lane-[ab]" stroke-width="([0-9.]+)"', svg)]
        self.assertTrue(widths and min(widths) >= 6.0)

    def test_garbage_parameters_are_ignored(self):
        from navimow2mqtt import tileview
        self._root(tileview.render_tile(self.DATA, rotate=45, flip="z", theme="x", width=5))
        self.assertEqual(tileview.tile_size(5)[0], 240)
        self.assertEqual(tileview.tile_size(99999)[0], 1600)

    def test_theme_and_system_default(self):
        from navimow2mqtt import tileview
        self.assertIn("--lawn:#3f6f3a", tileview.render_tile(self.DATA, theme="dark"))
        self.assertIn("prefers-color-scheme:dark", tileview.render_tile(self.DATA))

    def test_page_has_a_transparent_background(self):
        from navimow2mqtt import tileview
        page = tileview.tile_page("<svg/>", refresh=7)
        self.assertIn("background:transparent", page)
        self.assertIn('content="7"', page)

    def test_aria_label_follows_the_language(self):
        from navimow2mqtt import tileview
        self.assertIn('aria-label="Map of the mowing session"', tileview.render_tile(self.DATA, lang="en"))
        self.assertIn('aria-label="Kaart van de maaibeurt"', tileview.render_tile(self.DATA, lang="nl"))


class TileBackground(unittest.TestCase):
    """A calibrated picture under the lanes and the robot: the whole picture, or a window around the mower."""
    BG = {"uri": "data:image/png;base64,AAAA", "w": 400, "h": 500, "dock": [100.0, 400.0], "scale": 10.0,
          "rotation": 0.0, "flip": False, "zones": []}

    def _svg(self, data=None, **kw):
        from navimow2mqtt import tileview
        return tileview.render_tile(data, background=kw.pop("background", self.BG), **kw)

    def test_the_whole_picture_is_shown_in_its_own_proportions(self):
        svg = self._svg({"points": []})
        self.assertIn('width="640" height="800"', svg)                       # 400 x 500 picture
        self.assertIn('<image href="data:image/png;base64,AAAA"', svg)
        self.assertIn('transform="translate(0 0) scale(1.6)"', svg)
        self.assertNotIn('class="lawn"', svg)                                # the picture replaces the lawn
        self.assertNotIn("<text", svg)
        self.assertIn('clip-path="url(#tile)"', svg)                         # the corners stay transparent

    def test_positions_land_on_the_right_pixels(self):
        # dock at picture pixel (100, 400), 10 px per metre, tile scale 1.6
        svg = self._svg({"points": [[0, 0], [2, 3]], "x": 2, "y": 3, "state": "mowing"})
        self.assertIn('x="144" y="630"', svg)                                # the dock: (100, 400) * 1.6 - (16, 10)
        self.assertIn("160,640 192,592", svg)                                # (0,0) and (2,3) -> (100,400), (120,370)
        self.assertIn('<g transform="translate(192 592)">', svg)             # the robot sits at the last position

    def test_the_calibrated_rotation_is_applied(self):
        # rotated a quarter turn clockwise: "right" (2, 0) now points down the picture
        svg = self._svg({"points": [[0, 0], [2, 0]]}, background=dict(self.BG, rotation=90.0))
        self.assertIn("160,640 160,672", svg)

    def test_a_window_of_span_metres_around_the_mower(self):
        svg = self._svg({"points": [], "x": 1, "y": 2}, span=20, width=640)
        self.assertIn('width="640" height="416"', svg)                       # the design's proportions
        self.assertIn('transform="translate(-32 -1008) scale(3.2)"', svg)    # centred on the mower
        self.assertIn('<g transform="translate(320 208)">', svg)             # which is the middle of the tile

    def test_the_window_stays_inside_the_picture(self):
        svg = self._svg({"points": [], "x": -8, "y": 0}, span=20, width=640)   # near the left edge
        self.assertIn('transform="translate(0 ', svg)
        self.assertIn('<g transform="translate(64 ', svg)                     # the robot is left of the middle

    def test_the_whole_picture_can_keep_the_size_of_the_zoomed_tile(self):
        """400 x 500 picture in the 640 x 416 tile: it fits by its height and is centred."""
        svg = self._svg({"points": []}, span=0, width=640, fixed_size=True)
        self.assertIn('width="640" height="416"', svg)
        self.assertIn('transform="translate(153.6 0) scale(0.832)"', svg)
        self.assertIn('width="640" height="800"', self._svg({"points": []}, span=0, width=640))     # default: own proportions

    def test_follow_the_dock(self):
        svg = self._svg({"points": [], "x": 5, "y": 5}, span=20, follow="dock")
        self.assertIn("translate(0 -1072)", svg)                              # centred on (100, 400), clamped

    def test_without_a_position_the_window_centres_on_the_dock(self):
        self.assertIn("translate(0 -1072)", self._svg({"points": []}, span=20))
        self.assertIn("translate(0 -1072)", self._svg(None, span=20))

    def test_a_window_larger_than_the_picture_centres_it(self):
        svg = self._svg({"points": []}, span=100)
        self.assertIn('transform="translate(192 48) scale(0.64)"', svg)

    def test_rotate_and_flip_are_ignored_with_a_picture(self):
        data = {"points": [[0, 0], [2, 3]], "x": 2, "y": 3}
        base = self._svg(data)
        self.assertEqual(base, self._svg(data, rotate=90, flip="x"))

    def test_the_swath_follows_the_scale(self):
        import re
        svg = self._svg({"points": [[0, 0], [2, 3]]}, swath_m=1.0)           # 1 m = 16 tile px
        self.assertIn('stroke-width="16"', svg)
        thin = self._svg({"points": [[0, 0], [2, 3]]}, swath_m=0.05)
        self.assertEqual(re.search(r'<polyline class="lane-a" stroke-width="([0-9.]+)"', thin).group(1), "4")

    def test_the_picture_address_is_escaped(self):
        svg = self._svg({"points": []}, background=dict(self.BG, uri='data:x"onload="alert(1)'))
        self.assertNotIn('"onload="', svg)

    def test_without_a_picture_nothing_changes(self):
        from navimow2mqtt import tileview
        self.assertIn('class="lawn"', tileview.render_tile({"points": []}))
        self.assertNotIn("<image", tileview.render_tile({"points": []}))


class LongPath(unittest.TestCase):
    """A whole lawn is thousands of points: the start of the path must not be lost."""

    def _bridge(self, **env):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse(env), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        b.mowers["d1"].state = MowerState(state="mowing")
        return b

    def _mow(self, b, n):
        for i in range(1, n + 1):             # a lane up and down, a new position each time
            b.on_cloud_message("d1", "location", [{"type": 1, "postureX": str(i * 0.25), "postureY": str(i % 7)}])

    def test_the_default_keeps_a_whole_session(self):
        self.assertEqual(parse({}).path_max_points, 8000)
        b = self._bridge()
        self._mow(b, 3000)
        trail = b.mowers["d1"].trail
        self.assertEqual(len(trail.points), 3000)
        self.assertEqual(trail.points[0], [0.25, 1.0])          # the very first point is still there
        self.assertEqual(len(b.map_data()["points"]), 3000)       # and the tile gets all of them

    def test_the_mqtt_message_is_thinned_out_but_keeps_first_and_last(self):
        b = self._bridge()
        self._mow(b, 3000)
        msg = [json.loads(c.args[1]) for c in b.publish.call_args_list if c.args[0] == "navimow/garden/path"][-1]
        self.assertEqual((msg["count"], msg["total"]), (500, 3000))
        self.assertEqual(msg["points"][0], [0.25, 1.0])
        self.assertEqual(msg["points"][-1], [750.0, 3000 % 7])
        xs = [p[0] for p in msg["points"]]
        self.assertEqual(xs, sorted(xs))                         # in order, spread over the whole path
        self.assertLess(len(b.publish.call_args_list[-1].args[1]), 20000)   # a small retained message

    def test_a_short_path_is_published_as_it_is(self):
        b = self._bridge()
        self._mow(b, 40)
        msg = [json.loads(c.args[1]) for c in b.publish.call_args_list if c.args[0] == "navimow/garden/path"][-1]
        self.assertEqual((msg["count"], msg["total"]), (40, 40))

    def test_an_explicit_limit_still_applies(self):
        b = self._bridge(PATH_MAX_POINTS="100")
        self._mow(b, 300)
        self.assertEqual(len(b.mowers["d1"].trail.points), 100)

    def test_downsample(self):
        from navimow2mqtt.models import downsample
        pts = [[i, 0] for i in range(10)]
        self.assertEqual(downsample(pts, 10), pts)
        self.assertEqual(downsample(pts, 100), pts)
        self.assertEqual(downsample(pts, 4), [[0, 0], [3, 0], [6, 0], [9, 0]])
        self.assertEqual(downsample(pts, 1), pts)                  # a limit below 2 is ignored
        self.assertEqual(downsample([], 5), [])


class RebuildTrail(unittest.TestCase):
    """Recover the whole path of a session from the event log."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "logs")
        os.makedirs(self.dir)
        self.trail = os.path.join(self.tmp.name, "trail.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self, *events, day="2026-10-14"):
        with open(os.path.join(self.dir, "events-%s.jsonl" % day), "a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")

    @staticmethod
    def _state(ts, frm, to, device="mower"):
        return {"ts": ts, "kind": "state_change", "device": device, "from_state": frm, "to_state": to}

    @staticmethod
    def _pos(ts, x, y, device="2SCAH"):
        return {"ts": ts, "kind": "cloud_msg", "device": device, "channel": "location",
                "payload": [{"type": 1, "postureX": str(x), "postureY": str(y), "postureTheta": "0.1"}]}

    def test_the_last_session_is_rebuilt_in_full(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T08:00:00.000+00:00", None, "docked"),
                  self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1),
                  self._pos("2026-10-14T09:00:30.000+00:00", 3, 1),
                  self._state("2026-10-14T10:00:00.000+00:00", "mowing", "docked"),
                  self._pos("2026-10-14T10:00:10.000+00:00", 9, 9),             # in the dock afterwards: not part of it
                  self._state("2026-10-14T13:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T13:00:10.000+00:00", 5, 5), self._pos("2026-10-14T13:00:20.000+00:00", 6, 5),
                  self._pos("2026-10-14T13:00:30.000+00:00", 6, 6), self._pos("2026-10-14T13:00:40.000+00:00", 6, 6),
                  self._state("2026-10-14T14:00:00.000+00:00", "mowing", "docked"))
        res = rebuild.rebuild(self.dir, "mower")
        self.assertEqual(res["points"], [[5.0, 5.0], [6.0, 5.0], [6.0, 6.0]])     # the last one, no duplicates
        self.assertEqual((res["started"], res["ended"]), ("2026-10-14T13:00:00.000+00:00", "2026-10-14T14:00:00.000+00:00"))

    def test_a_brief_flash_of_mowing_after_the_session_is_skipped(self):
        """Right after the bridge starts a stale status can say "mowing" for a split second."""
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1),
                  self._pos("2026-10-14T09:00:30.000+00:00", 3, 1),
                  self._state("2026-10-14T10:00:00.000+00:00", "mowing", "docked"),
                  self._state("2026-10-14T22:29:40.799+00:00", None, "mowing"),       # a stale status ...
                  self._state("2026-10-14T22:29:40.914+00:00", "mowing", "docked"))   # ... corrected at once
        res = rebuild.rebuild(self.dir, "mower")
        self.assertEqual(res["points"], [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]])
        self.assertEqual(res["started"], "2026-10-14T09:00:00.000+00:00")

    def test_a_session_that_never_reported_its_end(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1),
                  self._state("2026-10-14T12:00:00.000+00:00", "docked", "mowing"),   # no "docked" in between (log gap)
                  self._pos("2026-10-14T12:00:10.000+00:00", 5, 5), self._pos("2026-10-14T12:00:20.000+00:00", 6, 5))
        self.assertEqual(rebuild.rebuild(self.dir, "mower")["points"], [[5.0, 5.0], [6.0, 5.0]])

    def test_a_restart_in_the_middle_does_not_start_a_new_session(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1),
                  self._state("2026-10-14T09:30:00.000+00:00", None, "mowing"),       # the bridge was restarted
                  self._pos("2026-10-14T09:30:10.000+00:00", 3, 1), self._pos("2026-10-14T09:30:20.000+00:00", 4, 1))
        res = rebuild.rebuild(self.dir, "mower")
        self.assertEqual(res["points"], [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0]])
        self.assertIsNone(res["ended"])                                           # still running

    def test_a_session_across_midnight_and_explicit_times(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-13T23:50:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-13T23:55:00.000+00:00", 1, 1), day="2026-10-13")
        self._log(self._pos("2026-10-14T00:05:00.000+00:00", 2, 1), self._pos("2026-10-14T00:06:00.000+00:00", 3, 1),
                  self._state("2026-10-14T00:10:00.000+00:00", "mowing", "docked"))
        self.assertEqual(len(rebuild.rebuild(self.dir, "mower")["points"]), 3)
        res = rebuild.rebuild(self.dir, "mower", since="2026-10-14T00:00:00.000+00:00")
        self.assertEqual(res["points"], [[2.0, 1.0], [3.0, 1.0]])

    def test_it_says_what_is_wrong(self):
        from navimow2mqtt import rebuild
        with self.assertRaises(rebuild.RebuildError):                              # no log at all
            rebuild.rebuild(self.dir, "mower")
        self._log(self._state("2026-10-14T09:00:00.000+00:00", None, "docked"))
        with self.assertRaises(rebuild.RebuildError):                              # no session in it
            rebuild.rebuild(self.dir, "mower")
        self._log(self._state("2026-10-14T10:00:00.000+00:00", "docked", "mowing"))
        with self.assertRaises(rebuild.RebuildError):                              # a session but no positions
            rebuild.rebuild(self.dir, "mower")

    def test_several_mowers_need_a_device_id(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1, "A"), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1, "A"),
                  self._pos("2026-10-14T09:00:15.000+00:00", 7, 7, "B"), self._pos("2026-10-14T09:00:25.000+00:00", 8, 7, "B"))
        with self.assertRaises(rebuild.RebuildError):
            rebuild.rebuild(self.dir, "mower")
        self.assertEqual(rebuild.rebuild(self.dir, "mower", device_id="B")["points"], [[7.0, 7.0], [8.0, 7.0]])

    def test_a_long_path_is_thinned_to_the_limit(self):
        from navimow2mqtt import rebuild
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  *[self._pos("2026-10-14T09:%02d:%02d.000+00:00" % (i // 60, i % 60), i, 0) for i in range(1, 600)])
        res = rebuild.rebuild(self.dir, "mower", max_points=100)
        self.assertEqual((len(res["points"]), res["total"]), (100, 599))
        self.assertEqual((res["points"][0], res["points"][-1]), ([1.0, 0.0], [599.0, 0.0]))

    def test_the_trail_file_keeps_other_mowers_and_bad_lines_in_the_log_are_skipped(self):
        from navimow2mqtt import rebuild
        with open(self.trail, "w") as fh:
            json.dump({"version": 1, "trails": {"other": {"points": [[1, 1]], "started": None}}}, fh)
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1))
        with open(os.path.join(self.dir, "events-2026-10-14.jsonl"), "a") as fh:
            fh.write("{broken\n\n[1,2]\n")
        res = rebuild.rebuild(self.dir, "mower")
        rebuild.write_trail(self.trail, "mower", res)
        data = json.load(open(self.trail))
        self.assertEqual(data["trails"]["other"]["points"], [[1, 1]])
        self.assertEqual(data["trails"]["mower"]["points"], [[1.0, 1.0], [2.0, 1.0]])

    def test_the_bridge_loads_the_rebuilt_path(self):
        from navimow2mqtt import rebuild
        from navimow2mqtt.bridge import Bridge
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1))
        rebuild.write_trail(self.trail, "mower", rebuild.rebuild(self.dir, "mower"))
        b = Bridge(parse({"TRAIL_FILE": self.trail}), mock.Mock(), [Device(id="d1", name="Mower")])
        self.assertEqual(b.mowers["d1"].trail.points, [[1.0, 1.0], [2.0, 1.0]])

    def _bridge(self, **env):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse(dict({"LOG_DIR": self.dir, "TRAIL_FILE": self.trail}, **env)), mock.Mock(),
                   [Device(id="2SCAH", name="Mower")])
        b.publish = mock.Mock()
        return b

    def _session(self):
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  *[self._pos("2026-10-14T09:%02d:%02d.000+00:00" % (i // 60, i % 60), i * 0.25, i % 5)
                    for i in range(1, 301)],
                  self._state("2026-10-14T10:00:00.000+00:00", "mowing", "docked"))

    def test_the_running_bridge_loads_the_full_path_from_its_log(self):
        """The button: the bridge only remembered the end of the session, the log has all of it."""
        self._session()
        b = self._bridge(PATH_MAX_POINTS="50")
        m = b.mowers["2SCAH"]
        m.trail.points = [[1.0, 1.0], [2.0, 1.0]]                    # what was left in memory
        info = b.rebuild_trail()
        self.assertEqual((info["name"], info["total"], info["points"]), ("mower", 300, 50))   # thinned to the limit
        self.assertEqual(len(m.trail.points), 50)
        self.assertEqual(m.trail.points[0], [0.25, 1.0])
        self.assertEqual(m.trail.started, "2026-10-14T09:00:00.000+00:00")
        self.assertEqual(len(json.load(open(self.trail))["trails"]["mower"]["points"]), 50)    # kept across a restart
        published = [json.loads(c.args[1]) for c in b.publish.call_args_list if c.args[0] == "navimow/mower/path"]
        self.assertEqual(published[-1]["total"], 50)
        self.assertEqual(len(b.map_data()["points"]), 50)

    def test_the_default_limit_keeps_every_position(self):
        self._session()
        b = self._bridge()
        self.assertEqual(b.rebuild_trail()["points"], 300)

    def test_it_explains_when_it_cannot(self):
        from navimow2mqtt import rebuild
        b = self._bridge()
        with self.assertRaises(rebuild.RebuildError):                        # empty log
            b.rebuild_trail()
        self._session()
        for env, text in (({"LOG_DIR": ""}, "switched off"), ({"PATH_MAX_POINTS": "0"}, "switched off")):
            with self.assertRaises(rebuild.RebuildError) as cm:
                self._bridge(**env).rebuild_trail()
            self.assertIn(text, str(cm.exception))
        with self.assertRaises(rebuild.RebuildError):
            b.rebuild_trail(name="nobody")

    def test_the_command_line(self):
        import io
        from contextlib import redirect_stdout
        from navimow2mqtt import __main__ as cli
        self._log(self._state("2026-10-14T09:00:00.000+00:00", "docked", "mowing"),
                  self._pos("2026-10-14T09:00:10.000+00:00", 1, 1), self._pos("2026-10-14T09:00:20.000+00:00", 2, 1))
        env = {"LOG_DIR": self.dir, "TRAIL_FILE": self.trail}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(out):
            self.assertEqual(cli.main(["rebuild-trail"]), 0)                       # one mower in the log: no --name needed
        self.assertIn("2 positions", out.getvalue())
        self.assertEqual(json.load(open(self.trail))["trails"]["mower"]["points"], [[1.0, 1.0], [2.0, 1.0]])
        with mock.patch.dict(os.environ, {"LOG_DIR": self.dir, "TRAIL_FILE": ""}), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["rebuild-trail"]), 2)                       # needs both settings


class PathTrail(unittest.TestCase):
    def _bridge(self, env=None):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse(env or {}), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        return b, b.mowers["d1"]

    @staticmethod
    def _pose(x, y):
        return [{"type": 1, "postureX": str(x), "postureY": str(y), "postureTheta": "0.1"}]

    def _paths(self, b):
        return [json.loads(c.args[1]) for c in b.publish.call_args_list if c.args[0] == "navimow/garden/path"]

    def test_trail_unit(self):
        from navimow2mqtt.models import Trail
        t = Trail(3)
        self.assertTrue(t.add(1.0, 1.0))
        self.assertFalse(t.add(1.0, 1.0))          # not moved
        for i in range(2, 6):
            t.add(float(i), 0.0)
        self.assertEqual(len(t.points), 3)          # capped, oldest dropped
        self.assertEqual(t.points[-1], [5.0, 0.0])
        self.assertFalse(Trail(0).add(1.0, 1.0))    # disabled

    def test_points_only_added_while_driving(self):
        b, m = self._bridge()
        m.state = MowerState(state="docked")
        b.on_cloud_message("d1", "location", self._pose(1, 1))
        self.assertEqual(self._paths(b), [])
        m.state = MowerState(state="mowing")
        b.on_cloud_message("d1", "location", self._pose(1.25, 1))
        b.on_cloud_message("d1", "location", self._pose(1.25, 1))   # same spot: no new publish
        b.on_cloud_message("d1", "location", self._pose(1.5, 1))
        paths = self._paths(b)
        self.assertEqual([p["count"] for p in paths], [1, 2])
        self.assertEqual(paths[-1]["points"], [[1.25, 1.0], [1.5, 1.0]])

    def test_new_session_resets_but_pause_does_not(self):
        b, m = self._bridge()
        m.state = MowerState(state="mowing")
        b.on_cloud_message("d1", "location", self._pose(1, 1))
        b.apply(m, {"state": "isPaused"}, from_push=True)
        b.apply(m, {"state": "isRunning"}, from_push=True)          # resume: keep the path
        self.assertEqual(len(m.trail.points), 1)
        b.apply(m, {"state": "isDocked"}, from_push=True)
        b.apply(m, {"state": "isRunning"}, from_push=True)          # new session: fresh path
        self.assertEqual(m.trail.points, [])
        self.assertEqual(self._paths(b)[-1]["count"], 0)

    def test_disabled(self):
        b, m = self._bridge({"PATH_MAX_POINTS": "0"})
        m.state = MowerState(state="mowing")
        b.on_cloud_message("d1", "location", self._pose(1, 1))
        self.assertEqual(self._paths(b), [])


class LocationDecode(unittest.TestCase):
    def test_real_pose_message(self):
        from navimow2mqtt.models import Location
        loc = Location()
        # captured from an i208 AWD while driving it manually
        self.assertTrue(loc.update([{"postureTheta": "0.435", "postureX": "2.25", "postureY": "2.0",
                                     "time": 1791922576254, "type": 1, "vehicleState": 4}]))
        d = loc.data
        self.assertEqual((d["x"], d["y"]), (2.25, 2.0))
        self.assertEqual(d["distance_m"], 3.01)
        self.assertEqual(d["heading_deg"], 24.9)
        self.assertEqual(d["vehicle_state"], 4)

    def test_types_merge_and_empty_is_ignored(self):
        from navimow2mqtt.models import Location
        loc = Location()
        loc.update([{"type": 1, "postureX": "1", "postureY": "1"}])
        self.assertFalse(loc.update([]))
        loc.update([{"type": 2, "currentMowBoundary": 9, "currentMowProgress": 2550}])
        loc.update([{"type": 3, "partitionIds": [9]}, {"type": 4, "taskDelay": 0}])
        self.assertEqual((loc.data["x"], loc.data["zone"], loc.data["progress_pct"]), (1.0, 9, 25.5))
        self.assertEqual(loc.data["target_zone"], [9])

    def test_garbage_is_ignored(self):
        from navimow2mqtt.models import Location
        loc = Location()
        self.assertFalse(loc.update("x"))
        self.assertFalse(loc.update([1, {"type": 99}]))
        self.assertFalse(loc.update([{"type": 1, "postureX": "abc", "postureY": "1"}]))
        self.assertEqual(loc.data, {})


class Config(unittest.TestCase):
    def test_defaults(self):
        c = parse({})
        self.assertEqual(c.mqtt.base_topic, "navimow")
        self.assertEqual(c.http_poll_interval, 60.0)

    def test_overrides(self):
        c = parse({"MQTT_HOST": "h", "MQTT_PORT": "1884", "MQTT_BASE_TOPIC": "/x/", "MQTT_DISCOVERY": "no"})
        self.assertEqual((c.mqtt.host, c.mqtt.port, c.mqtt.base_topic, c.mqtt.discovery),
                         ("h", 1884, "x", False))

    def test_bad_number(self):
        with self.assertRaises(ConfigError):
            parse({"MQTT_PORT": "abc"})


class Cloud(unittest.TestCase):
    def test_parse_topic(self):
        self.assertEqual(parse_topic("/downlink/vehicle/abc/realtimeDate/state"), ("abc", "state"))
        self.assertIsNone(parse_topic("/downlink/vehicle/abc/other/state"))
        self.assertIsNone(parse_topic("navimow/abc/state"))

    def test_mqtt_connection(self):
        c = mqtt_connection({"mqttHost": "https://mq.example.com", "mqttUrl": "wss://mq.example.com/mqtt?x=1",
                             "userName": "u", "pwdInfo": "p"})
        self.assertEqual((c["host"], c["port"], c["path"]), ("mq.example.com", 443, "/mqtt?x=1"))
        self.assertEqual((c["username"], c["password"]), ("u", "p"))

    def test_commands(self):
        self.assertEqual(set(COMMANDS), {"start", "stop", "pause", "resume", "dock"})


class Discovery(unittest.TestCase):
    def test_messages(self):
        dev = Device(id="X1", name="Garden", model="i105")
        msgs = dict(ha.discovery_messages("homeassistant", "navimow", "garden", dev))
        mower = json.loads(msgs["homeassistant/lawn_mower/navimow2mqtt_garden_mower/config"])
        self.assertEqual(mower["dock_command_topic"], "navimow/garden/command")
        self.assertEqual(mower["activity_state_topic"], "navimow/garden/state")
        self.assertIn("homeassistant/sensor/navimow2mqtt_garden_battery/config", msgs)

    def test_device_fallbacks(self):
        d = Device.from_dict({"id": "2SCAH1", "name": "Mower", "firmwareVersion": "1.2.3"})
        self.assertEqual((d.serial, d.firmware), ("2SCAH1", "1.2.3"))
        self.assertEqual(d.raw["name"], "Mower")
        self.assertEqual(Device.from_dict({"id": "x", "serialNumber": "S9"}).serial, "S9")
        # the real authList record of an i208 AWD
        real = Device.from_dict({"id": "2SXXX0000X0000", "name": "Mower", "model": "i208 AWD",
                                 "firmware": "0039"})
        self.assertEqual((real.firmware, real.serial, real.model), ("0039", "2SXXX0000X0000", "i208 AWD"))

    def test_names(self):
        devs = [Device(id="a1", name="Garden Mower"), Device(id="b2", name="Garden Mower")]
        names = device_names(devs)
        self.assertEqual(names["a1"], "garden_mower")
        self.assertNotEqual(names["a1"], names["b2"])
        self.assertEqual(slug("Voor tuin!"), "voor_tuin")


class Watchdog(unittest.TestCase):
    def _bridge(self):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse({}), mock.Mock(), [Device(id="d1", name="Garden")])
        b.cloud = mock.Mock()
        return b, b.mowers["d1"]

    def test_mismatch_forces_reconnect_once(self):
        b, m = self._bridge()
        m.state = MowerState(state="mowing")
        m.last_push = time.monotonic()
        b.check_mismatch(m, MowerState(state="docked"))
        b.check_mismatch(m, MowerState(state="docked"))   # debounced
        self.assertEqual(b.cloud.force_reconnect.call_count, 1)

    def test_same_state_or_no_push_is_ignored(self):
        b, m = self._bridge()
        m.state = MowerState(state="mowing")
        b.check_mismatch(m, MowerState(state="docked"))   # never got a push
        m.last_push = time.monotonic()
        b.check_mismatch(m, MowerState(state="mowing"))
        b.cloud.force_reconnect.assert_not_called()

    def test_location_array_is_published_raw(self):
        b, m = self._bridge()
        b.publish = mock.Mock()
        msg = [{"type": 1, "postureX": 1.5, "postureY": -2.0, "postureTheta": 0.3}]
        b.on_cloud_message("d1", "location", msg)
        raw = b.publish.call_args_list[0]
        self.assertEqual(raw.args[0], "navimow/garden/location")
        self.assertEqual(json.loads(raw.args[1]), msg)
        self.assertTrue(raw.kwargs["retain"])
        self.assertTrue(m.location_seen)
        pos = b.publish.call_args_list[1]
        self.assertEqual(pos.args[0], "navimow/garden/position")
        self.assertEqual(json.loads(pos.args[1])["x"], 1.5)

    def test_empty_location_keeps_last_position(self):
        b, m = self._bridge()
        b.publish = mock.Mock()
        b.on_cloud_message("d1", "location", [{"type": 1, "postureX": "1", "postureY": "2"}])
        b.publish.reset_mock()
        b.on_cloud_message("d1", "location", [])
        topics_sent = [c.args[0] for c in b.publish.call_args_list]
        self.assertEqual(topics_sent, ["navimow/garden/location"])   # raw only, no position update
        self.assertEqual(m.location.data["y"], 2.0)

    def test_non_dict_state_is_ignored(self):
        b, m = self._bridge()
        b.publish = mock.Mock()
        b.on_cloud_message("d1", "state", [1, 2])
        b.publish.assert_not_called()

    def test_start_while_paused_becomes_resume(self):
        b, m = self._bridge()
        m.state = MowerState(state="paused")
        b.handle_command(m, "start_mowing")
        b.api.send_command.assert_called_once_with("d1", "resume")


class Login(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = NavimowConfig(token_file=os.path.join(self.tmp.name, "t.json"))
        self.tokens = TokenManager(cfg, session=mock.Mock())
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"access_token": "A", "refresh_token": "R", "expires_in": 3600}
        self.tokens.session.post.return_value = resp
        from navimow2mqtt.auth import LoginService
        self.svc = LoginService(cfg, self.tokens)

    def tearDown(self):
        self.tmp.cleanup()

    def _state(self):
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(self.svc.new_url()).query)["state"][0]

    def test_submit_with_issued_state(self):
        st = self._state()
        url = "https://my.home-assistant.io/redirect/_change/?redirect=oauth%%2F%%3Fcode%%3DABC%%26state%%3D%s" % st
        ok, msg = self.svc.submit(url)
        self.assertTrue(ok, msg)
        self.assertEqual(self.tokens.status()["logged_in"], True)
        self.assertEqual(self.tokens.session.post.call_args.kwargs["data"]["code"], "ABC")

    def test_unknown_state_is_refused(self):
        ok, msg = self.svc.submit("https://x/?code=ABC&state=forged")
        self.assertFalse(ok)
        self.tokens.session.post.assert_not_called()

    def test_state_is_single_use(self):
        st = self._state()
        self.assertTrue(self.svc.submit("https://x/?code=A&state=%s" % st)[0])
        self.assertFalse(self.svc.submit("https://x/?code=A&state=%s" % st)[0])

    def test_pasted_bare_code_has_no_state_check(self):
        self.assertTrue(self.svc.submit("rawcode")[0])

    def test_message_has_no_token_values(self):
        _, msg = self.svc.submit("rawcode")
        self.assertNotIn("R", msg)          # the refresh token is "R" in this test


class WebPage(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        import threading
        from navimow2mqtt.auth import LoginService
        from navimow2mqtt.web import make_handler
        self.tmp = tempfile.TemporaryDirectory()
        cfg = NavimowConfig(token_file=os.path.join(self.tmp.name, "t.json"))
        self.tokens = TokenManager(cfg, session=mock.Mock())
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"access_token": "SECRET-A", "refresh_token": "SECRET-R", "expires_in": 3600}
        self.tokens.session.post.return_value = resp
        self.svc = LoginService(cfg, self.tokens)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.svc, self.tokens))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_page_and_login(self):
        import urllib.parse
        import urllib.request
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Not signed in", body)
        self.assertIn("client_id=homeassistant", body)
        self.assertEqual(json.loads(self._get("/health")[1]), {"auth": "required"})
        data = urllib.parse.urlencode({"text": "rawcode"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/login", data=data), timeout=5) as r:
            page = r.read().decode()
        self.assertIn("Signed in.", page)
        self.assertNotIn("SECRET", page)
        self.assertEqual(json.loads(self._get("/health")[1]), {"auth": "ok"})

    def test_page_offers_automatic_return_and_it_completes_the_login(self):
        import re
        import urllib.parse
        status, body = self._get("/")
        # option 2 link: the authorize URL with a redirect_uri that points back at this very server
        link = re.search(r'href="([^"]*redirect_uri=[^"]*127\.0\.0\.1[^"]*)"', body)
        self.assertIsNotNone(link, "no automatic-return link on the page")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(urllib.parse.unquote(link.group(1).replace("&amp;", "&"))).query)
        self.assertEqual(q["redirect_uri"][0], self.base + "/callback")
        status, page = self._get("/callback?code=THECODE&state=" + urllib.parse.quote(q["state"][0]))
        self.assertEqual(status, 200)
        self.assertIn("Signed in.", page)
        sent = self.tokens.session.post.call_args.kwargs["data"]
        self.assertEqual(sent["code"], "THECODE")
        self.assertEqual(sent["redirect_uri"], self.base + "/callback")       # must repeat the one it was issued with

    def test_bookmarked_link_without_state_completes_on_login(self):
        """A link with redirect_uri=http://<host>/login and no state: the exchange repeats that address."""
        status, page = self._get("/login?code=BOOKMARK")
        self.assertEqual(status, 200)
        self.assertIn("Signed in.", page)
        sent = self.tokens.session.post.call_args.kwargs["data"]
        self.assertEqual((sent["code"], sent["redirect_uri"]), ("BOOKMARK", self.base + "/login"))

    def test_navimow_sends_state_null_for_a_link_without_state(self):
        """Seen live: a link without state comes back as ...?code=XXX&state=null."""
        status, page = self._get("/callback?code=NULLSTATE&state=null")
        self.assertEqual(status, 200)
        self.assertIn("Signed in.", page)
        sent = self.tokens.session.post.call_args.kwargs["data"]
        self.assertEqual((sent["code"], sent["redirect_uri"]), ("NULLSTATE", self.base + "/callback"))

    def test_pasted_address_with_state_null(self):
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"text": "http://localhost/callback?code=PASTED&state=null"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/login", data=data), timeout=5) as r:
            self.assertIn("Signed in.", r.read().decode())

    def test_plain_get_login_shows_the_page_and_errors_are_shown(self):
        status, page = self._get("/login")
        self.assertEqual(status, 200)
        self.assertIn("Sign in", page)
        status, page = self._get("/login?error=access_denied&error_description=Niet+toegestaan")
        self.assertEqual(status, 400)
        self.assertIn("Navimow reported: Niet toegestaan", page)
        self.tokens.session.post.assert_not_called()

    def test_option_1_still_uses_the_configured_redirect(self):
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"text": "rawcode"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/login", data=data), timeout=5):
            pass
        self.assertEqual(self.tokens.session.post.call_args.kwargs["data"]["redirect_uri"],
                         NavimowConfig().redirect_uri)

    def test_failed_paste_explains_what_was_wrong(self):
        import urllib.error
        import urllib.parse
        import urllib.request
        bad = "https://navimow-h5-fra.willand.com/smartHome/login?client_id=homeassistant&secret=SHOULDNOTECHO"
        data = urllib.parse.urlencode({"text": bad}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(self.base + "/login", data=data), timeout=5)
            self.fail("expected a 400")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            self.assertEqual(e.code, 400)
        self.assertIn("navimow-h5-fra.willand.com/smartHome/login", body)     # tells where it pointed
        self.assertIn("step 1", body)
        self.assertNotIn("SHOULDNOTECHO", body)                               # never echoes the query

    def test_logged_in_collapses_the_login_section(self):
        self._get("/callback?code=x")      # no state: accepted, logs in
        status, body = self._get("/")
        self.assertIn("You’re signed in", body)
        self.assertNotIn("<details open>", body)      # the paste section stays collapsed
        self.assertIn("Sign in again", body)

    def test_callback_rejects_forged_state(self):
        status, body = self._get("/callback?code=ABC&state=forged")
        self.assertEqual(status, 400)
        self.tokens.session.post.assert_not_called()

    def test_unknown_path(self):
        self.assertEqual(self._get("/nope")[0], 404)


class Translations(unittest.TestCase):
    def tearDown(self):
        from navimow2mqtt import i18n
        i18n.set_language("en")

    def test_default_is_english(self):
        from navimow2mqtt import i18n
        self.assertEqual(parse({}).language, "en")
        self.assertEqual(i18n.current(), "en")

    def test_every_language_has_every_key_with_the_same_placeholders(self):
        import string
        from navimow2mqtt.i18n import STRINGS

        def fields(text):
            return {f for _, f, _, _ in string.Formatter().parse(text) if f}
        en = STRINGS["en"]
        for lang, table in STRINGS.items():
            self.assertEqual(set(table), set(en), "keys differ in %s" % lang)
            for key, text in table.items():
                self.assertEqual(fields(text), fields(en[key]), "%s / %s: placeholders differ" % (lang, key))

    def test_language_setting_is_parsed(self):
        self.assertEqual(parse({"LANGUAGE": "nl"}).language, "nl")
        self.assertEqual(parse({"LANGUAGE": "NL"}).language, "nl")
        self.assertEqual(parse({"LANGUAGE": "nl_NL"}).language, "nl")
        self.assertEqual(parse({"LANGUAGE": "en-GB"}).language, "en")
        with self.assertRaises(ConfigError):
            parse({"LANGUAGE": "xx"})

    def test_unknown_key_in_a_language_falls_back_to_english(self):
        from navimow2mqtt import i18n
        i18n.STRINGS["nl"].pop("btn.map")
        try:
            self.assertEqual(i18n.t("btn.map", "nl"), "View map")
        finally:
            i18n.STRINGS["nl"]["btn.map"] = "Kaart bekijken"

    def test_login_page_in_dutch(self):
        from navimow2mqtt.web import render_login_page
        out = {"logged_in": False, "has_refresh_token": False, "expires_in": None}
        page = render_login_page(out, "https://x/a", "https://x/m", lang="nl")
        for text in ("Niet ingelogd", "Inloggen bij Navimow", "Kaart bekijken", "Problemen met inloggen?",
                     "Toegangstoken", 'lang="nl"'):
            self.assertIn(text, page)
        for english in ("Not signed in", "View map", "Trouble signing in", "Access token"):
            self.assertNotIn(english, page)
        signed = render_login_page({"logged_in": True, "has_refresh_token": True, "expires_in": 5400},
                                   "https://x/a", "https://x/m", lang="nl")
        self.assertIn("1 u 30 min", signed)
        self.assertIn("Automatisch", signed)
        self.assertIn("Opnieuw inloggen", signed)

    def test_messages_follow_the_configured_language(self):
        from navimow2mqtt import i18n
        from navimow2mqtt.auth import LoginService
        cfg = NavimowConfig(token_file=os.path.join(tempfile.gettempdir(), "never-written.json"))
        svc = LoginService(cfg, mock.Mock())
        self.assertIn("No code found", svc.submit("https://my.home-assistant.io/redirect/oauth")[1])
        i18n.set_language("nl")
        ok, msg = svc.submit("https://my.home-assistant.io/redirect/oauth")
        self.assertFalse(ok)
        self.assertIn("Geen code gevonden", msg)
        self.assertIn("my.home-assistant.io/redirect/oauth", msg)           # still says where it pointed
        self.assertFalse(svc.submit("x", state="forged")[0])
        self.assertIn("niet door deze bridge", svc.submit("x", state="forged")[1])

    def test_whole_web_flow_in_dutch(self):
        """The running server follows set_language(): page, callback errors and results."""
        import urllib.request
        from http.server import ThreadingHTTPServer
        import threading
        from navimow2mqtt import i18n
        from navimow2mqtt.auth import LoginService
        from navimow2mqtt.web import make_handler
        with tempfile.TemporaryDirectory() as tmp:
            cfg = NavimowConfig(token_file=os.path.join(tmp, "t.json"))
            tokens = TokenManager(cfg, session=mock.Mock())
            resp = mock.Mock(status_code=200)
            resp.json.return_value = {"access_token": "A", "refresh_token": "R", "expires_in": 3600}
            tokens.session.post.return_value = resp
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(LoginService(cfg, tokens), tokens))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                i18n.set_language("nl")
                with urllib.request.urlopen(base + "/", timeout=5) as r:
                    self.assertIn("Niet ingelogd", r.read().decode())
                with urllib.request.urlopen(base + "/callback?code=X", timeout=5) as r:
                    page = r.read().decode()
                self.assertIn("Ingelogd.", page)
                self.assertIn("Je bent ingelogd", page)
            finally:
                server.shutdown()
                server.server_close()


class LoginPageRender(unittest.TestCase):
    def _page(self, status, message="", ok=True):
        from navimow2mqtt.web import render_login_page
        return render_login_page(status, "https://x/auto?a=1&b=2", "https://x/manual", message, ok)

    NOT_IN = {"logged_in": False, "has_refresh_token": False, "expires_in": None}
    IN = {"logged_in": True, "has_refresh_token": True, "expires_in": 2520}

    def test_not_signed_in(self):
        page = self._page(self.NOT_IN)
        self.assertIn("Not signed in", page)
        self.assertIn("Sign in to Navimow", page)
        self.assertIn('class="btn primary"', page)          # the one call to action
        self.assertIn("&amp;b=2", page)                     # links are escaped

    def test_signed_in_shows_token_tiles(self):
        page = self._page(self.IN)
        self.assertIn("42 min", page)
        self.assertIn("Automatic", page)
        self.assertIn("Sign in again", page)
        self.assertNotIn('class="btn primary" href', page)  # the primary style is kept for the real action

    def test_no_refresh_token_warns(self):
        page = self._page(dict(self.IN, has_refresh_token=False))
        self.assertIn("note warn", page)
        self.assertIn("Manual", page)

    def test_error_message_opens_the_paste_section_and_is_escaped(self):
        page = self._page(self.NOT_IN, "<script>alert(1)</script>", ok=False)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("<details open>", page)
        self.assertIn("note bad", page)

    def test_token_time_formats(self):
        from navimow2mqtt.web import _minutes
        self.assertEqual(_minutes(None), "—")
        self.assertEqual(_minutes(-5), "Renewing")
        self.assertEqual(_minutes(20), "1 min")
        self.assertEqual(_minutes(3600), "1 h 00 min")
        self.assertEqual(_minutes(5400), "1 h 30 min")

    def test_whole_page_is_english(self):
        page = self._page(self.NOT_IN)
        for dutch in ("Inloggen", "Kaart", "Optie", "ingelogd", "adresbalk", "Opnieuw"):
            self.assertNotIn(dutch, page)


class WebTile(WebPage):
    def test_tile_routes_serve_the_same_image(self):
        self.server.map_provider = lambda name: dict(TileView.DATA)
        status, tile = self._get("/tile.svg")
        self.assertEqual(status, 200)
        self.assertTrue(tile.startswith("<svg"))
        self.assertEqual(self._get("/map.svg")[1], tile)         # /map.svg is the same tile
        self.assertNotIn("<text", tile)

    def test_parameters(self):
        self.server.map_provider = lambda name: dict(TileView.DATA)
        self.assertIn('width="500"', self._get("/tile.svg?w=500")[1])
        self.assertIn("--lawn:#3f6f3a", self._get("/tile.svg?theme=dark")[1])
        self.assertEqual(self._get("/tile.svg?swath=abc&rotate=x&w=nope")[0], 200)     # garbage is ignored
        self.assertNotEqual(self._get("/tile.svg")[1], self._get("/tile.svg?rotate=90")[1])

    def test_device_is_passed_to_the_provider(self):
        seen = []
        self.server.map_provider = lambda name: seen.append(name) or dict(TileView.DATA)
        self._get("/tile.svg?device=garden")
        self._get("/tile.svg")
        self.assertEqual(seen, ["garden", None])

    def test_page_variant(self):
        self.server.map_provider = lambda name: dict(TileView.DATA)
        for path in ("/tile", "/map"):
            status, page = self._get(path + "?refresh=9")
            self.assertEqual(status, 200)
            self.assertIn("background:transparent", page)
            self.assertIn('content="9"', page)
            self.assertIn("<svg", page)

    def test_without_a_bridge_it_is_still_a_valid_tile(self):
        status, body = self._get("/tile.svg")
        self.assertEqual(status, 200)
        self.assertIn('class="dock"', body)
        self.assertNotIn('class="body"', body)

    def test_old_card_routes_are_gone(self):
        for path in ("/card", "/card.svg"):
            self.assertEqual(self._get(path)[0], 404)

    def test_bridge_map_data(self):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse({}), mock.Mock(), [Device(id="d1", name="Garden")])
        b.publish = mock.Mock()
        m = b.mowers["d1"]
        m.state = MowerState(state="mowing", battery=50)
        b.on_cloud_message("d1", "location", [{"type": 1, "postureX": "1", "postureY": "2",
                                               "time": int(time.time() * 1000) - 5000}])
        d = b.map_data()
        self.assertEqual((d["x"], d["y"], d["state"], d["battery"]), (1.0, 2.0, "mowing", 50))
        self.assertEqual(d["points"], [[1.0, 2.0]])
        self.assertTrue(4 <= d["position_age_s"] <= 8)
        self.assertEqual(b.map_data("garden")["name"], "Garden")
        self.assertIsNone(b.map_data("nope"))


class WebMaps(WebPage):
    """The map pictures: upload, calibrate, delete, and the tile using them."""

    def setUp(self):
        super().setUp()
        from navimow2mqtt.maps import MapStore
        self.store = MapStore(os.path.join(self.tmp.name, "maps"))
        self.server.maps = self.store
        self.server.map_provider = lambda name: dict(TileView.DATA, zone=6)

    def _post(self, path, body=b"", ctype="application/json"):
        import urllib.error
        import urllib.request
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": ctype} if ctype else {})
        def parse(raw):
            try:
                return json.loads(raw.decode() or "{}")
            except ValueError:
                return {"text": raw.decode(errors="replace")}      # e.g. a plain "not found"
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, parse(r.read())
        except urllib.error.HTTPError as e:
            return e.code, parse(e.read())

    def _upload(self, name="achtertuin", w=447, h=467):
        return self._post("/maps/upload?name=%s" % name, make_png(w, h), "image/png")

    def test_empty_list_page_and_the_login_page_links_to_it(self):
        status, page = self._get("/maps")
        self.assertEqual(status, 200)
        self.assertIn("No pictures yet.", page)
        self.assertIn('type="file"', page)
        self.assertIn('href="/maps"', self._get("/")[1])          # the sign-in page links to it

    def test_upload_list_and_serve_the_picture(self):
        png = make_png(447, 467)
        status, res = self._post("/maps/upload?name=achtertuin", png, "image/png")
        self.assertEqual(status, 200)
        self.assertEqual((res["view"]["w"], res["view"]["h"]), (447, 467))
        page = self._get("/maps")[1]
        self.assertIn("achtertuin", page)
        self.assertIn("447 × 467", page)
        self.assertIn("Not calibrated", page)
        import urllib.request
        with urllib.request.urlopen(self.base + "/maps/achtertuin/image", timeout=5) as r:
            self.assertEqual((r.status, r.headers["Content-Type"], r.headers["X-Content-Type-Options"]),
                             (200, "image/png", "nosniff"))
            self.assertEqual(r.read(), png)

    def test_bad_uploads_are_refused(self):
        self.assertEqual(self._post("/maps/upload?name=x", make_png(200, 200), "text/plain")[0], 415)
        self.assertEqual(self._post("/maps/upload?name=x", b"a=b", "application/x-www-form-urlencoded")[0], 415)
        status, res = self._post("/maps/upload?name=x", b"not a picture", "image/png")
        self.assertEqual(status, 400)
        self.assertIn("PNG and JPEG", res["error"])
        status, res = self._post("/maps/upload?name=Bad/Name", make_png(200, 200), "image/png")
        self.assertEqual(status, 400)
        self.assertEqual(self.store.names(), [])

    def test_a_too_large_picture_is_refused_without_reading_it(self):
        import http.client
        host, port = self.base.replace("http://", "").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=5)
        conn.putrequest("POST", "/maps/upload?name=big")
        conn.putheader("Content-Type", "image/png")
        conn.putheader("Content-Length", str(10 * 1024 * 1024))
        conn.endheaders()
        res = conn.getresponse()
        self.assertEqual(res.status, 413)
        conn.close()

    def test_calibrate_and_the_tile_uses_the_picture(self):
        self._upload()
        self.assertEqual(self._post("/maps/achtertuin/calibrate", b"{}", "text/plain")[0], 415)
        status, res = self._post("/maps/achtertuin/calibrate",
                                 {"dock": [350, 420], "scale": 15, "rotation": 200, "flip": False, "zones": [6]})
        self.assertEqual(status, 200)
        self.assertEqual((res["view"]["scale"], res["view"]["zones"], res["view"]["rotation"]), (15.0, [6], -160.0))
        status, svg = self._get("/tile.svg?view=achtertuin&span=10")
        self.assertIn('<image href="data:image/png;base64,', svg)
        self.assertNotIn('class="lawn"', svg)
        # the whole picture, in its own proportions
        self.assertIn('width="640" height="669"', self._get("/tile.svg?view=achtertuin")[1])

    def test_view_auto_picks_the_picture_of_the_zone(self):
        self._upload("achtertuin")
        self._upload("overzicht")
        self._post("/maps/achtertuin/calibrate", {"dock": [350, 420], "scale": 15, "rotation": 0, "zones": [6]})
        self._post("/maps/overzicht/calibrate", {"dock": [200, 400], "scale": 8, "rotation": 0, "zones": []})
        self.assertIn("data:image/png", self._get("/tile.svg?view=auto")[1])       # the provider reports zone 6
        self.server.map_provider = lambda name: dict(TileView.DATA, zone=99)
        self.assertIn("data:image/png", self._get("/tile.svg?view=auto")[1])       # unknown zone: the overview
        self.assertEqual(self.store.pick(6), "achtertuin")

    def test_an_unknown_or_uncalibrated_view_falls_back_to_the_plain_tile(self):
        self._upload("t")
        for url in ("/tile.svg?view=t", "/tile.svg?view=nope", "/tile.svg?view=auto", "/tile.svg?view=../x"):
            status, svg = self._get(url)
            self.assertEqual(status, 200)
            self.assertIn('class="lawn"', svg, url)

    def _scale(self, svg):
        import re
        return re.search(r'<image [^>]*transform="translate\([-0-9.]+ [-0-9.]+\) scale\(([0-9.]+)\)"', svg).group(1)

    def test_the_whole_picture_at_rest_and_zoomed_in_while_driving(self):
        """span=12&idle_span=0: everything while the mower stands still, a 12 m window as soon as it drives."""
        self._upload()                                                       # 447 x 467
        self._post("/maps/achtertuin/calibrate", {"dock": [350, 420], "scale": 15, "rotation": 0})
        url = "/tile.svg?view=achtertuin&span=12&idle_span=0"
        for state in ("docked", "charging", "idle", None, "unknown"):
            self.server.map_provider = lambda name, s=state: dict(TileView.DATA, state=s)
            svg = self._get(url)[1]
            self.assertEqual(self._scale(svg), "0.89079", state)           # the whole picture (416 / 467) ...
            self.assertIn('width="640" height="416"', svg)                  # ... in a tile of the same size as the zoomed one
        for state in ("mowing", "paused", "returning", "error"):
            self.server.map_provider = lambda name, s=state: dict(TileView.DATA, state=s)
            svg = self._get(url)[1]
            self.assertEqual(self._scale(svg), "3.55556", state)           # 640 / (12 m * 15 px per m)
            self.assertIn('width="640" height="416"', svg)

    def test_idle_span_can_be_a_smaller_window_and_fixed_can_be_switched_off(self):
        self._upload()
        self._post("/maps/achtertuin/calibrate", {"dock": [350, 420], "scale": 15, "rotation": 0})
        self.server.map_provider = lambda name: dict(TileView.DATA, state="docked")
        self.assertEqual(self._scale(self._get("/tile.svg?view=achtertuin&span=12&idle_span=5")[1]), "8.53333")
        svg = self._get("/tile.svg?view=achtertuin&span=12&idle_span=0&fixed=0")[1]
        self.assertIn('width="640" height="669"', svg)                      # the picture's own proportions again

    def test_without_idle_span_nothing_changes(self):
        self._upload()
        self._post("/maps/achtertuin/calibrate", {"dock": [350, 420], "scale": 15, "rotation": 0})
        self.server.map_provider = lambda name: dict(TileView.DATA, state="docked")
        self.assertEqual(self._scale(self._get("/tile.svg?view=achtertuin&span=12")[1]), "3.55556")    # zoomed at rest too
        self.assertEqual(self._scale(self._get("/tile.svg?view=achtertuin&span=12&idle_span=abc")[1]), "3.55556")
        self.assertIn('width="640" height="669"', self._get("/tile.svg?view=achtertuin")[1])

    def test_garbage_span_and_follow_are_ignored(self):
        self._upload()
        self._post("/maps/achtertuin/calibrate", {"dock": [350, 420], "scale": 15, "rotation": 0})
        status, svg = self._get("/tile.svg?view=achtertuin&span=abc&follow=x")
        self.assertEqual(status, 200)
        self.assertIn("data:image/png", svg)

    def test_invalid_calibrations_are_refused(self):
        self._upload()
        status, res = self._post("/maps/achtertuin/calibrate", {"dock": [1, 2], "scale": 0.0001, "rotation": 0})
        self.assertEqual(status, 400)
        self.assertIn("scale", res["error"])
        self.assertEqual(self._post("/maps/achtertuin/calibrate", [1, 2])[0], 400)
        self.assertEqual(self._post("/maps/nope/calibrate", {"dock": [1, 2], "scale": 5, "rotation": 0})[0], 400)

    def test_delete(self):
        self._upload()
        self.assertEqual(self._post("/maps/achtertuin/delete", {}, "text/plain")[0], 415)
        self.assertEqual(self._post("/maps/achtertuin/delete", {})[0], 200)
        self.assertEqual(self._post("/maps/achtertuin/delete", {})[0], 404)
        self.assertEqual(self._get("/maps/achtertuin/image")[0], 404)

    def test_the_calibration_page(self):
        import re
        self._upload()
        status, page = self._get("/maps/achtertuin")
        self.assertEqual(status, 200)
        self.assertIn('id="stage"', page)
        self.assertIn('viewBox="0 0 447 467"', page)
        self.assertIn("/maps/achtertuin/image", page)
        self.assertIn("Calibrate achtertuin", page)
        cfg = json.loads(re.search(r'<script type="application/json" id="cfg">(.*?)</script>', page, re.S).group(1))
        self.assertEqual((cfg["name"], cfg["w"], cfg["h"], cfg["view"]), ("achtertuin", 447, 467, None))
        self.assertIn("cal.saved", cfg["t"])
        self.assertEqual(self._get("/maps/nope")[0], 404)

    def test_the_path_for_the_calibration_page(self):
        status, body = self._get("/maps/x/path.json")        # a path request does not need the picture
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["points"], TileView.DATA["points"])
        self.assertEqual((data["x"], data["y"], data["zone"]), (1.5, 2.25, 6))

    def test_the_load_path_button(self):
        from navimow2mqtt import rebuild
        calls = []

        def rebuilder():
            calls.append(1)
            return {"name": "mower", "points": 120, "total": 120, "started": "s", "ended": "e"}
        self.server.trail_rebuilder = rebuilder
        status, res = self._post("/maps/trail/rebuild", {})
        self.assertEqual((status, res["ok"], res["trail"]["total"]), (200, True, 120))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self._post("/maps/trail/rebuild", b"{}", "text/plain")[0], 415)    # not from another site
        self.assertEqual(len(calls), 1)

        def failing():
            raise rebuild.RebuildError("No mowing session found in the event log for 'mower'.")
        self.server.trail_rebuilder = failing
        status, res = self._post("/maps/trail/rebuild", {})
        self.assertEqual(status, 400)
        self.assertIn("No mowing session", res["error"])
        self.server.trail_rebuilder = None
        self.assertEqual(self._post("/maps/trail/rebuild", {})[0], 404)

    def test_the_load_path_button_is_on_the_calibration_page_in_both_languages(self):
        from navimow2mqtt import i18n
        self._upload()
        page = self._get("/maps/achtertuin")[1]
        self.assertIn('id="loadpath"', page)
        self.assertIn("Load the full path of the last mowing session from the log", page)
        self.assertIn("/maps/trail/rebuild", page)
        try:
            i18n.set_language("nl")
            self.assertIn("Het volledige pad van de laatste maaibeurt uit de log laden", self._get("/maps/achtertuin")[1])
        finally:
            i18n.set_language("en")

    def test_the_zoom_buttons_are_on_the_calibration_page_in_both_languages(self):
        from navimow2mqtt import i18n
        self._upload()
        page = self._get("/maps/achtertuin")[1]
        for ident in ("zoomIn", "zoomOut", "zoomFit", "pan"):
            self.assertIn('id="%s"' % ident, page)
        self.assertIn("Whole picture", page)
        try:
            i18n.set_language("nl")
            self.assertIn("Hele plaatje", self._get("/maps/achtertuin")[1])
        finally:
            i18n.set_language("en")

    def test_the_button_works_even_without_picture_storage(self):
        self.server.maps = None
        self.server.trail_rebuilder = lambda: {"total": 7}
        self.assertEqual(self._post("/maps/trail/rebuild", {})[0], 200)

    def test_pictures_are_off_when_there_is_no_store(self):
        self.server.maps = None
        for path in ("/maps", "/maps/x", "/maps/x/image"):
            self.assertEqual(self._get(path)[0], 404, path)
        self.assertEqual(self._post("/maps/upload?name=x", make_png(200, 200), "image/png")[0], 404)
        self.assertNotIn('href="/maps"', self._get("/")[1])
        self.assertIn("lawn", self._get("/tile.svg?view=x")[1])

    def test_the_pages_follow_the_language(self):
        from navimow2mqtt import i18n
        try:
            i18n.set_language("nl")
            self.assertIn("Nog geen plaatjes.", self._get("/maps")[1])
            self._upload()
            self.assertIn("achtertuin kalibreren", self._get("/maps/achtertuin")[1])
        finally:
            i18n.set_language("en")


class BridgeTrailsAndZones(unittest.TestCase):
    def _bridge(self, tmp, **env):
        from navimow2mqtt.bridge import Bridge
        cfg = parse(dict({"TRAIL_FILE": os.path.join(tmp, "trail.json")}, **env))
        b = Bridge(cfg, mock.Mock(), [Device(id="d1", name="Garden")], maps=env.get("maps"))
        b.publish = mock.Mock()
        return b

    def _drive(self, b, points):
        m = b.mowers["d1"]
        m.state = MowerState(state="mowing")
        for x, y in points:
            b.on_cloud_message("d1", "location", [{"type": 1, "postureX": str(x), "postureY": str(y)}])
        return m

    def test_the_path_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._bridge(tmp)
            m = self._drive(b, [(0.25, 0.5), (0.5, 0.75), (0.75, 1.0)])
            b.persist_trails(force=True)
            again = self._bridge(tmp)
            self.assertEqual(again.mowers["d1"].trail.points, [[0.25, 0.5], [0.5, 0.75], [0.75, 1.0]])
            self.assertEqual(again.mowers["d1"].trail.started, m.trail.started)
            self.assertEqual(again.map_data()["points"], [[0.25, 0.5], [0.5, 0.75], [0.75, 1.0]])

    def test_writes_are_throttled_but_a_session_end_writes_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._bridge(tmp)
            with mock.patch("navimow2mqtt.bridge.save_trails") as save:
                self._drive(b, [(1, 1), (2, 2), (3, 3), (4, 4)])           # four new points in quick succession
                self.assertEqual(save.call_count, 1)
                b.apply(b.mowers["d1"], {"state": "isRunning"}, from_push=True)
                b.apply(b.mowers["d1"], {"state": "isDocked"}, from_push=True)    # the session ends
                self.assertEqual(save.call_count, 2)

    def test_disabled_without_a_file_and_a_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._bridge(tmp, TRAIL_FILE="")
            self._drive(b, [(1, 1), (2, 2)])
            b.persist_trails(force=True)
            self.assertEqual(os.listdir(tmp), [])                              # nothing written
            with open(os.path.join(tmp, "trail.json"), "w") as fh:
                fh.write("{broken")
            self.assertEqual(self._bridge(tmp, TRAIL_FILE=os.path.join(tmp, "trail.json")).mowers["d1"].trail.points, [])

    def test_a_saved_path_is_capped_to_the_configured_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._bridge(tmp, PATH_MAX_POINTS="5")
            self._drive(b, [(i, i) for i in range(1, 12)])
            b.persist_trails(force=True)
            self.assertEqual(len(self._bridge(tmp, PATH_MAX_POINTS="3").mowers["d1"].trail.points), 3)

    def test_zone_ids_are_remembered_for_the_calibration_page(self):
        from navimow2mqtt.maps import MapStore
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(os.path.join(tmp, "maps"))
            b = self._bridge(tmp, maps=store)
            b.on_cloud_message("d1", "location", [{"type": 2, "currentMowBoundary": 6, "currentMowProgress": 100}])
            b.on_cloud_message("d1", "location", [{"type": 2, "currentMowBoundary": 4, "currentMowProgress": 50}])
            self.assertEqual(store.zones_seen(), [4, 6])
            self.assertEqual(b.map_data()["zone"], 4)                          # the zone the mower is in now


class PublicUrl(unittest.TestCase):
    def test_config(self):
        self.assertEqual(parse({"PUBLIC_URL": " http://10.0.0.5:8099/ "}).public_url, "http://10.0.0.5:8099")
        self.assertEqual(parse({}).public_url, "")
        with self.assertRaises(ConfigError):
            parse({"PUBLIC_URL": "10.0.0.5:8099"})

    def test_published_on_connect(self):
        from navimow2mqtt.bridge import Bridge
        b = Bridge(parse({"PUBLIC_URL": "http://10.0.0.5:8099"}), mock.Mock(), [Device(id="d1", name="G")])
        b.publish = mock.Mock()
        b._on_connect(mock.Mock(), None, None, 0)
        b.publish.assert_any_call("navimow/bridge/web_url", "http://10.0.0.5:8099", retain=True)

    def test_not_published_without_url_or_with_page_off(self):
        from navimow2mqtt.bridge import Bridge
        for env in ({}, {"PUBLIC_URL": "http://h:1", "WEB_PORT": "0"}):
            b = Bridge(parse(env), mock.Mock(), [Device(id="d1", name="G")])
            b.publish = mock.Mock()
            b._on_connect(mock.Mock(), None, None, 0)
            self.assertNotIn("navimow/bridge/web_url", [c.args[0] for c in b.publish.call_args_list])

    def test_announce_logs_what_was_sent(self):
        from navimow2mqtt.__main__ import announce_login_needed
        svc = mock.Mock()
        svc.new_url.return_value = "https://login/url"
        with mock.patch("paho.mqtt.publish.multiple"):
            with self.assertLogs(level="INFO") as cm:
                announce_login_needed(parse({"PUBLIC_URL": "http://h:8099"}), svc)
            self.assertIn("navimow/bridge/web_url", "\n".join(cm.output))
            with self.assertLogs(level="INFO") as cm:
                announce_login_needed(parse({}), svc)
            out = "\n".join(cm.output)
            self.assertIn("PUBLIC_URL is not set", out)

    def test_announce_before_first_login(self):
        from navimow2mqtt.__main__ import announce_login_needed
        svc = mock.Mock()
        svc.new_url.return_value = "https://login/url"
        with mock.patch("paho.mqtt.publish.multiple") as multiple:
            announce_login_needed(parse({"PUBLIC_URL": "http://h:8099", "MQTT_HOST": "b"}), svc)
        msgs = {m["topic"]: m["payload"] for m in multiple.call_args.args[0]}
        self.assertEqual(msgs["navimow/bridge/auth"], "required")
        self.assertEqual(msgs["navimow/bridge/login_url"], "https://login/url")
        self.assertEqual(msgs["navimow/bridge/web_url"], "http://h:8099")
        self.assertEqual(multiple.call_args.kwargs["hostname"], "b")


class BridgeLogin(unittest.TestCase):
    def test_login_via_mqtt(self):
        from navimow2mqtt.bridge import Bridge
        svc = mock.Mock()
        svc.submit.return_value = (True, "Signed in.")
        svc.new_url.return_value = "https://login/url"
        b = Bridge(parse({}), mock.Mock(), [Device(id="d1", name="Garden")], svc)
        b.publish = mock.Mock()
        b.set_auth("required")
        b.publish.assert_any_call("navimow/bridge/login_url", "https://login/url", retain=True)
        b.handle_login("https://my.home-assistant.io/redirect/x?code=1")
        b.publish.assert_any_call("navimow/bridge/login_result", "Signed in.")
        b.publish.assert_any_call("navimow/bridge/login_url", "", retain=True)   # cleared
        self.assertEqual(b.auth_state, "ok")


class Auth(unittest.TestCase):
    def test_authorize_url(self):
        url = build_authorize_url(NavimowConfig(), state="s")
        self.assertIn("client_id=homeassistant", url)
        self.assertIn("state=s", url)
        self.assertIn("response_type=code", url)

    def test_extract_code(self):
        self.assertEqual(extract_code("https://my.home-assistant.io/redirect/oauth?code=abc&state=z"), "abc")
        self.assertEqual(extract_code("  rawcode "), "rawcode")

    def test_extract_code_nested_redirect(self):
        # what my.home-assistant.io really produced
        url = ("https://my.home-assistant.io/redirect/_change/"
               "?redirect=oauth%2F%3Fcode%3Dq4SR0OBbHv%26state%3Db8mo8chQtta1L1zsGoJFqg")
        self.assertEqual(extract_code(url), "q4SR0OBbHv")
        self.assertEqual(extract_code("https://example.com/no-code-here"), "")

    def _manager(self, tmp, **tok):
        cfg = NavimowConfig(token_file=os.path.join(tmp, "t.json"))
        tm = TokenManager(cfg, session=mock.Mock())
        tm.save(tok)
        return tm

    def test_fresh_token_used(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._manager(d, access_token="A", expires_at=time.time() + 3600)
            self.assertEqual(tm.access_token(), "A")

    def test_refresh_when_expiring(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._manager(d, access_token="A", refresh_token="R", expires_at=time.time() - 1)
            resp = mock.Mock(status_code=200)
            resp.json.return_value = {"access_token": "B", "expires_in": 3600}
            tm.session.post.return_value = resp
            self.assertEqual(tm.access_token(), "B")
            self.assertEqual(tm.session.post.call_args.kwargs["data"]["grant_type"], "refresh_token")
            self.assertEqual(tm._load()["refresh_token"], "R")   # kept when server omits it

    def test_wrapped_response(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._manager(d, access_token="A")
            resp = mock.Mock(status_code=200)
            resp.json.return_value = {"code": 1, "data": {"access_token": "C", "refresh_token": "R2"}}
            tm.session.post.return_value = resp
            tok = tm.exchange_code("code")
            self.assertEqual((tok["access_token"], tok["refresh_token"]), ("C", "R2"))
            self.assertTrue(tok["expires_assumed"])

    def test_expired_without_refresh_needs_login(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._manager(d, access_token="A", expires_at=time.time() - 10)
            tm.session.post = mock.Mock()
            # expiry was reported by the server -> hard failure
            with self.assertRaises(AuthRequired):
                tm.access_token()

    def test_assumed_expiry_keeps_trying(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._manager(d, access_token="A", expires_at=time.time() - 10, expires_assumed=True)
            self.assertEqual(tm.access_token(), "A")
            with self.assertRaises(AuthRequired):
                tm.access_token(force_refresh=True)   # API said 401 -> login needed

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TokenManager(NavimowConfig(token_file=os.path.join(d, "none.json")), session=mock.Mock())
            with self.assertRaises(AuthRequired):
                tm.access_token()


if __name__ == "__main__":
    unittest.main()
