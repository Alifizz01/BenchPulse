import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from benchpulse import demo
from benchpulse.hub import Hub, board
from benchpulse.sessions import ACTIVE, DISCONNECTED, classify, ipv4_from_wts_address
from benchpulse.store import TIME_FMT, Conflict, Store

RDP = {"station": "RDP-Tcp#4", "state": ACTIVE, "user": "max", "client_name": "LAPTOP-MAX", "client_ip": "10.0.0.23"}
CONSOLE = {"station": "Console", "state": ACTIVE, "user": "alif", "client_name": "", "client_ip": ""}


def t(dt):
    return dt.strftime(TIME_FMT)


class Classify(unittest.TestCase):
    def test_remote_desktop_wins(self):
        u = classify([CONSOLE, RDP], console_idle_s=1)
        self.assertEqual((u["in_use"], u["kind"], u["client_name"], u["client_ip"]), (True, "remote", "LAPTOP-MAX", "10.0.0.23"))

    def test_disconnected_rdp_is_not_use(self):
        u = classify([dict(RDP, state=DISCONNECTED)], console_idle_s=None)
        self.assertFalse(u["in_use"])

    def test_console_in_use_only_when_recently_touched(self):
        self.assertEqual(classify([CONSOLE], console_idle_s=30)["kind"], "local")
        idle = classify([CONSOLE], console_idle_s=3600)
        self.assertEqual((idle["in_use"], idle["kind"], idle["user"]), (False, "idle", "alif"))

    def test_nobody_signed_in(self):
        services = {"station": "Services", "state": DISCONNECTED, "user": "", "client_name": "", "client_ip": ""}
        self.assertEqual(classify([services, dict(CONSOLE, user="")], 5)["kind"], "free")

    def test_wts_client_address(self):
        raw = (2).to_bytes(4, "little") + bytes([0, 0, 10, 20, 5, 23]) + bytes(14)
        self.assertEqual(ipv4_from_wts_address(raw), "10.20.5.23")
        self.assertEqual(ipv4_from_wts_address((23).to_bytes(4, "little") + bytes(20)), "")   # IPv6: not shown
        self.assertEqual(ipv4_from_wts_address(b""), "")


class Reservations(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")

    def test_overlap_is_refused_but_back_to_back_is_fine(self):
        self.s.reserve("HIL-1", "Max", "2026-10-05T09:00", "2026-10-05T12:00")
        with self.assertRaises(Conflict) as e:
            self.s.reserve("hil-1", "Ana", "2026-10-05T11:30", "2026-10-05T13:00")      # PC names are case-insensitive
        self.assertIn("Max", str(e.exception))
        self.s.reserve("HIL-1", "Ana", "2026-10-05T12:00", "2026-10-05T13:00")
        self.s.reserve("HIL-2", "Ana", "2026-10-05T10:00", "2026-10-05T11:00")          # other PC: no clash
        self.assertEqual(len(self.s.reservations()), 3)

    def test_bad_times(self):
        with self.assertRaises(ValueError):
            self.s.reserve("HIL-1", "Max", "2026-10-05T12:00", "2026-10-05T12:00")
        with self.assertRaises(ValueError):
            self.s.reserve("HIL-1", "Max", "tomorrow", "2026-10-05T12:00")

    def test_window_query_and_cancel(self):
        rid = self.s.reserve("HIL-1", "Max", "2026-10-05T09:00", "2026-10-05T12:00")
        self.s.reserve("HIL-1", "Max", "2026-10-12T09:00", "2026-10-12T12:00")
        self.assertEqual(len(self.s.reservations("2026-10-05T00:00", "2026-10-12T00:00")), 1)
        self.assertTrue(self.s.cancel(rid))
        self.assertFalse(self.s.cancel(rid))


class Board(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")
        self.now = time.time()

    def pc(self, name):
        return next(p for p in board(self.s, self.now, timeout=30)["pcs"] if p["name"] == name)

    def test_statuses(self):
        self.s.heartbeat("A", "10.0.0.1", {"usage": {"in_use": False}}, now=self.now - 5)
        self.s.heartbeat("B", "10.0.0.2", {"usage": classify([RDP])}, now=self.now - 5)
        self.s.heartbeat("C", "10.0.0.3", {"usage": classify([RDP])}, now=self.now - 120)   # silent for 2 min
        b = board(self.s, self.now, timeout=30)
        self.assertEqual({p["name"]: p["status"] for p in b["pcs"]}, {"A": "online", "B": "in_use", "C": "missing"})
        self.assertEqual(b["counts"], {"online": 1, "in_use": 1, "missing": 1})
        self.assertEqual(self.pc("C")["usage"], {})                                         # stale usage isn't shown

    def test_who_is_on_it_and_booking_clash(self):
        self.s.set_person("10.0.0.23", "Max Mustermann")
        self.s.heartbeat("B", "10.0.0.2", {"usage": classify([RDP])}, now=self.now)
        self.assertEqual(self.pc("B")["person"], "Max Mustermann")
        self.s.set_person("10.0.0.23", "Max")
        now = datetime.fromtimestamp(self.now)
        self.s.reserve("B", "Ana", t(now - timedelta(hours=1)), t(now + timedelta(hours=1)), "release tests")
        self.s.reserve("B", "Max", t(now + timedelta(hours=2)), t(now + timedelta(hours=3)))
        pc = self.pc("B")
        self.assertEqual((pc["reserved_now"]["who"], pc["reserved_next"]["who"]), ("Ana", "Max"))
        self.assertTrue(pc["clash"])

    def test_no_clash_when_the_booker_is_on_it(self):
        self.s.set_person("LAPTOP-MAX", "Max")                                              # matched by computer name
        self.s.heartbeat("B", "10.0.0.2", {"usage": classify([RDP])}, now=self.now)
        now = datetime.fromtimestamp(self.now)
        self.s.reserve("B", "max", t(now - timedelta(hours=1)), t(now + timedelta(hours=1)))
        self.assertFalse(self.pc("B")["clash"])

    def test_demo_bench(self):
        demo.seed(self.s)
        bench = demo.Bench(self.s)
        bench.tick()
        b = board(self.s, timeout=30)
        self.assertEqual(len(b["pcs"]), 8)
        self.assertEqual(next(p for p in b["pcs"] if p["name"] == "FLASH-PC")["status"], "missing")
        self.assertGreaterEqual(b["counts"]["in_use"], 1)


class Http(unittest.TestCase):
    """The real server on a free port, driven like the browser and the agent drive it."""

    @classmethod
    def setUpClass(cls):
        cls.hub = Hub(("127.0.0.1", 0), Store(":memory:"), token="s3cret")
        cls.base = f"http://127.0.0.1:{cls.hub.server_address[1]}"
        threading.Thread(target=cls.hub.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.hub.shutdown()

    def call(self, method, path, body=None, headers=None, ctype="application/json"):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": ctype, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def beat(self, name="HIL-RACK-01", token="s3cret", **extra):
        return self.call("POST", "/api/heartbeat", {"name": name, "usage": classify([RDP]), "os": "Windows-10", **extra},
                         {"X-BenchPulse-Token": token})

    def test_agent_heartbeat_shows_on_board(self):
        self.assertEqual(self.beat()[0], 200)
        code, body, _ = self.call("GET", "/api/board")
        pc = next(p for p in json.loads(body)["pcs"] if p["name"] == "HIL-RACK-01")
        self.assertEqual((pc["status"], pc["ip"], pc["usage"]["client_name"]), ("in_use", "127.0.0.1", "LAPTOP-MAX"))

    def test_heartbeat_needs_token_and_a_sane_name(self):
        self.assertEqual(self.beat(token="wrong")[0], 403)
        self.assertEqual(self.beat(name="bad name; rm -rf")[0], 400)

    def test_reservation_flow(self):
        r = {"pc": "HIL-RACK-07", "who": "Ana", "start": "2030-01-07T09:00", "end": "2030-01-07T11:00", "purpose": "x"}
        code, body, _ = self.call("POST", "/api/reservations", r)
        self.assertEqual(code, 201)
        rid = json.loads(body)["id"]
        code, body, _ = self.call("POST", "/api/reservations", dict(r, who="Max", start="2030-01-07T10:00"))
        self.assertEqual(code, 409)
        self.assertIn("Ana", json.loads(body)["error"])
        self.assertEqual(self.call("POST", "/api/reservations", dict(r, who=""))[0], 400)
        listed = json.loads(self.call("GET", "/api/reservations?from=2030-01-07T00:00&to=2030-01-08T00:00")[1])
        self.assertEqual([x["id"] for x in listed], [rid])
        self.assertEqual(self.call("DELETE", f"/api/reservations/{rid}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/reservations/{rid}")[0], 404)

    def test_form_posts_are_refused(self):
        # a form on some other website can only send form-encoded data, never JSON without a preflight
        code, _, _ = self.call("POST", "/api/reservations", b"pc=HIL&who=x", ctype="application/x-www-form-urlencoded")
        self.assertEqual(code, 400)

    def test_rdp_file_only_for_known_pcs(self):
        self.beat(name="CONFIG-PC-01")
        code, body, headers = self.call("GET", "/rdp/CONFIG-PC-01.rdp")
        self.assertEqual(code, 200)
        self.assertIn(b"full address:s:CONFIG-PC-01", body)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.call("GET", "/rdp/UNKNOWN-PC.rdp")[0], 404)

    def test_people_and_notes(self):
        self.beat(name="HIL-RACK-02")
        self.assertEqual(self.call("POST", "/api/people", {"key": "10.0.0.23", "person": "Max"})[0], 200)
        self.assertEqual(self.call("POST", "/api/pcs/HIL-RACK-02/note", {"note": "rack 2"})[0], 200)
        pc = next(p for p in json.loads(self.call("GET", "/api/board")[1])["pcs"] if p["name"] == "HIL-RACK-02")
        self.assertEqual((pc["person"], pc["note"]), ("Max", "rack 2"))
        self.assertEqual(self.call("DELETE", "/api/people/10.0.0.23")[0], 200)

    def test_static_files_and_no_path_traversal(self):
        code, body, headers = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"BenchPulse", body)
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        self.assertEqual(self.call("GET", "/../hub.py")[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/hub.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
