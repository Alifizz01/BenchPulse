"""A pretend HiL lab: eight PCs whose users come and go, a who's-who list and a week of bookings.
Everything goes through the same Store and board() as a real hub; only the heartbeats are made up."""
import random
import threading
import time
import webbrowser
from datetime import datetime, timedelta

from benchpulse.hub import Hub
from benchpulse.store import TIME_FMT, Store

PCS = [  # name, ip, note
    ("HIL-RACK-01", "10.20.0.11", "dSPACE SCALEXIO · powertrain ECU"),
    ("HIL-RACK-02", "10.20.0.12", "dSPACE SCALEXIO · body ECU, 4× CAN-FD"),
    ("HIL-RACK-03", "10.20.0.13", "NI PXI · battery emulator"),
    ("CONFIG-PC-01", "10.20.0.21", "ControlDesk / ConfigurationDesk"),
    ("CONFIG-PC-02", "10.20.0.22", "CANoe · DBC/ARXML maintenance"),
    ("TESTEXEC-01", "10.20.0.31", "AutomationDesk nightly regression"),
    ("TESTEXEC-02", "10.20.0.32", "ECU-TEST · release campaigns"),
    ("FLASH-PC", "10.20.0.41", "ECU flashing station · INCA"),
]
PEOPLE = [  # RDP client (IP or laptop name) -> colleague
    ("10.20.5.23", "Alif"), ("LAPTOP-MKRAUSE", "Martin Krause"), ("10.20.5.41", "Sophie Wagner"),
    ("LAPTOP-JCHEN", "Jia Chen"), ("10.20.5.57", "Lukas Becker"),
]
CLIENTS = [("10.20.5.23", "LAPTOP-ALIF", "alif"), ("10.20.5.31", "LAPTOP-MKRAUSE", "mkrause"),
           ("10.20.5.41", "LAPTOP-SWAGNER", "swagner"), ("10.20.5.48", "LAPTOP-JCHEN", "jchen"),
           ("10.20.5.57", "LAPTOP-LBECKER", "lbecker"), ("10.20.5.66", "LAPTOP-GUEST03", "intern")]


def seed(store, now=None):
    now = now or datetime.now()
    slot = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)    # bookings snap to quarter hours
    for key, person in PEOPLE:
        store.set_person(key, person)
    for name, _, note in PCS:
        store.heartbeat(name, "", {}, now=0)
        store.set_note(name, note)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())   # Monday
    bookings = [  # pc, weekday, from, to, who, purpose
        ("HIL-RACK-01", 0, 8, 12, "Martin Krause", "Torque monitoring regression"),
        ("HIL-RACK-01", 2, 13, 17, "Alif", "CAN timeout fault injection"),
        ("HIL-RACK-02", 1, 9, 16, "Sophie Wagner", "Body ECU release candidate"),
        ("HIL-RACK-02", 3, 8, 11, "Jia Chen", "Wiper LIN tests"),
        ("HIL-RACK-03", 0, 13, 18, "Lukas Becker", "BMS balancing campaign"),
        ("HIL-RACK-03", 4, 8, 12, "Alif", "Cell model calibration"),
        ("TESTEXEC-01", 0, 18, 23, "Nightly", "Full regression"), ("TESTEXEC-01", 1, 18, 23, "Nightly", "Full regression"),
        ("TESTEXEC-01", 2, 18, 23, "Nightly", "Full regression"), ("TESTEXEC-01", 3, 18, 23, "Nightly", "Full regression"),
        ("TESTEXEC-02", 2, 9, 12, "Sophie Wagner", "Release campaign 24.3"),
        ("FLASH-PC", 1, 14, 15, "Jia Chen", "Flash B-sample ECUs"),
    ]
    # whatever time it is: HIL-RACK-02 is booked by the person using it, HIL-RACK-01 by someone
    # else than the person on it (the dashboard flags that)
    for pc, who, purpose in [("HIL-RACK-02", "Sophie Wagner", "Body ECU release candidate"),
                             ("HIL-RACK-01", "Lukas Becker", "Motor derating tests")]:
        try:
            store.reserve(pc, who, (slot - timedelta(minutes=30)).strftime(TIME_FMT),
                          (slot + timedelta(hours=2)).strftime(TIME_FMT), purpose)
        except ValueError:
            pass
    for pc, wd, a, b, who, purpose in bookings:     # the week's routine; skips any that clash with the above
        d = day + timedelta(days=wd)
        try:
            store.reserve(pc, who, (d + timedelta(hours=a)).strftime(TIME_FMT), (d + timedelta(hours=b)).strftime(TIME_FMT), purpose)
        except ValueError:
            pass


class Bench:
    """Who sits where. Changes slowly so the dashboard visibly lives."""

    def __init__(self, store, rng=None):
        self.store, self.rng = store, rng or random.Random(7)
        self.usage = {
            "HIL-RACK-01": _remote(CLIENTS[1]), "HIL-RACK-02": _remote(CLIENTS[2]), "HIL-RACK-03": _free(),
            "CONFIG-PC-01": _local("hil_operator", 40), "CONFIG-PC-02": _free(), "TESTEXEC-01": _remote(CLIENTS[0]),
            "TESTEXEC-02": _free(), "FLASH-PC": None,          # switched off: never reports
        }

    def tick(self):
        name = self.rng.choice([n for n, u in self.usage.items() if u is not None])
        if self.rng.random() < 0.15:
            self.usage[name] = self.rng.choice([_free(), _remote(self.rng.choice(CLIENTS)), _local("hil_operator", 5)])
        for (pc, ip, _), u in zip(PCS, [self.usage[p[0]] for p in PCS]):
            if u is not None:
                self.store.heartbeat(pc, ip, {"usage": u, "os": "Windows-10", "uptime_s": 86400 * 3,
                                              "version": "demo"})


def _remote(client):
    ip, name, user = client
    return {"in_use": True, "kind": "remote", "user": user, "client_name": name, "client_ip": ip}


def _local(user, idle):
    return {"in_use": True, "kind": "local", "user": user, "client_name": "", "client_ip": "", "idle_s": idle}


def _free():
    return {"in_use": False, "kind": "free", "user": "", "client_name": "", "client_ip": ""}


def run_demo(host="127.0.0.1", port=8600, open_browser=True):
    store = Store(":memory:")
    seed(store)
    bench = Bench(store)
    hub = Hub((host, port), store, timeout=30)

    def loop():
        while True:
            bench.tick()
            time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()
    url = f"http://{host}:{port}/"
    print(f"BenchPulse demo on {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        hub.serve_forever()
    except KeyboardInterrupt:
        pass
