"""Everything the hub remembers, in one SQLite file: PCs, reservations, and the who's-who list
that turns an RDP client (IP or computer name) into a colleague's name."""
import json
import sqlite3
import threading
import time
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS pcs (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    ip TEXT, last_seen REAL, info TEXT DEFAULT '{}', note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pc TEXT NOT NULL COLLATE NOCASE, who TEXT NOT NULL, start TEXT NOT NULL, "end" TEXT NOT NULL,
    purpose TEXT DEFAULT '', created REAL
);
CREATE TABLE IF NOT EXISTS people (
    key TEXT PRIMARY KEY COLLATE NOCASE,   -- an IP address or a computer name
    person TEXT NOT NULL
);
"""
TIME_FMT = "%Y-%m-%dT%H:%M"     # reservations are wall-clock times at the lab, as typed in the browser


class Conflict(ValueError):
    pass


def parse_time(s):
    return datetime.strptime(s[:16], TIME_FMT)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()          # one writer at a time; the hub is small
        with self.lock:
            self.db.executescript(SCHEMA)

    def _q(self, sql, args=()):
        with self.lock, self.db:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ PCs
    def heartbeat(self, name, ip, info, now=None):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO pcs(name, ip, last_seen, info) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen, info=excluded.info",
                (name, ip, time.time() if now is None else now, json.dumps(info)))

    def pcs(self):
        rows = self._q("SELECT * FROM pcs ORDER BY name")
        for r in rows:
            r["info"] = json.loads(r["info"] or "{}")
        return rows

    def set_note(self, name, note):
        self._q("UPDATE pcs SET note=? WHERE name=?", (note, name))

    def forget(self, name):
        self._q("DELETE FROM pcs WHERE name=?", (name,))

    # ------------------------------------------------------------------ reservations
    def reserve(self, pc, who, start, end, purpose=""):
        s, e = parse_time(start), parse_time(end)
        if e <= s:
            raise ValueError("The end has to be after the start.")
        start, end = s.strftime(TIME_FMT), e.strftime(TIME_FMT)
        with self.lock, self.db:
            clash = self.db.execute(
                'SELECT who, start, "end" FROM reservations WHERE pc=? AND start < ? AND "end" > ?',
                (pc, end, start)).fetchone()
            if clash:
                raise Conflict(f"{pc} is already reserved by {clash['who']} "
                               f"from {clash['start'].replace('T', ' ')} to {clash['end'][11:]}.")
            cur = self.db.execute(
                'INSERT INTO reservations(pc, who, start, "end", purpose, created) VALUES(?,?,?,?,?,?)',
                (pc, who, start, end, purpose, time.time()))
            return cur.lastrowid

    def reservations(self, start=None, end=None, pc=None):
        sql, args = 'SELECT * FROM reservations WHERE 1=1', []
        if start:
            sql += ' AND "end" > ?'; args.append(start)
        if end:
            sql += " AND start < ?"; args.append(end)
        if pc:
            sql += " AND pc = ?"; args.append(pc)
        return self._q(sql + " ORDER BY start", args)

    def cancel(self, rid):
        with self.lock, self.db:
            return self.db.execute("DELETE FROM reservations WHERE id=?", (rid,)).rowcount > 0

    # ------------------------------------------------------------------ who's who
    def people(self):
        return self._q("SELECT key, person FROM people ORDER BY person")

    def set_person(self, key, person):
        self._q("INSERT INTO people(key, person) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET person=excluded.person",
                (key.strip(), person.strip()))

    def remove_person(self, key):
        self._q("DELETE FROM people WHERE key=?", (key,))

    def who_is(self, *keys):
        """The colleague behind an RDP client IP or computer name, if someone told us."""
        for k in keys:
            if k:
                r = self._q("SELECT person FROM people WHERE key=?", (k,))
                if r:
                    return r[0]["person"]
        return ""
