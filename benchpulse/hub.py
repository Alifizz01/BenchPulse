"""The hub: one process on the main host that every bench PC reports to and every colleague
opens in a browser. Plain HTTP + JSON, Python standard library only.

    GET    /api/board                      all PCs with status, who is on them, reservations now/next
    POST   /api/heartbeat                  {name, usage, os, uptime_s, version}   (sent by agents)
    POST   /api/pcs/<name>/note            {note}         e.g. "dSPACE rack 2, CAN cards"
    DELETE /api/pcs/<name>                 forget a retired PC
    GET    /api/reservations?from=&to=     reservations overlapping a time window
    POST   /api/reservations               {pc, who, start, end, purpose}  -> 201, or 409 on a clash
    DELETE /api/reservations/<id>
    GET    /api/people                     who's who: RDP client IP / computer name -> colleague
    POST   /api/people                     {key, person}
    DELETE /api/people/<key>
    GET    /rdp/<name>.rdp                 a Remote Desktop file: double-click to connect

Status of a PC:
    online    heartbeat within the timeout, nobody using it
    in_use    someone is connected over Remote Desktop, or working at the console
    missing   no heartbeat within the timeout: switched off, crashed, or the agent isn't running
"""
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import benchpulse
from benchpulse.store import TIME_FMT, Conflict

WEB = os.path.abspath(os.path.join(os.path.dirname(__file__), "web"))
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")       # what Windows allows in a computer name, roughly
MAX_BODY = 64 * 1024


def board(store, now=None, timeout=30.0):
    """Everything the dashboard shows, computed from the last heartbeats and the reservations."""
    now = now or time.time()
    stamp = datetime.fromtimestamp(now).strftime(TIME_FMT)
    later = (datetime.fromtimestamp(now) + timedelta(days=7)).strftime(TIME_FMT)
    upcoming = store.reservations(start=stamp, end=later)
    pcs = []
    for pc in store.pcs():
        info = pc["info"]
        usage = info.get("usage") or {}
        seen = pc["last_seen"] or None                 # 0/NULL: registered but never heard from
        alive = seen is not None and now - seen <= timeout
        status = "missing" if not alive else "in_use" if usage.get("in_use") else "online"
        mine = [r for r in upcoming if r["pc"].lower() == pc["name"].lower()]
        current = next((r for r in mine if r["start"] <= stamp < r["end"]), None)
        nxt = next((r for r in mine if r["start"] > stamp), None)
        person = store.who_is(usage.get("client_ip"), usage.get("client_name"), usage.get("user")) \
            if alive and usage.get("in_use") else ""
        pcs.append({
            "name": pc["name"], "ip": pc["ip"], "status": status, "note": pc["note"],
            "last_seen": seen, "seen_ago": None if seen is None else round(now - seen),
            "usage": usage if alive else {}, "person": person,
            "os": info.get("os", ""), "uptime_s": info.get("uptime_s"), "agent": info.get("version", ""),
            "reserved_now": current, "reserved_next": nxt,
            # someone is on a PC that is booked for somebody else right now
            "clash": bool(current and status == "in_use" and person and person.lower() != current["who"].lower()),
        })
    counts = {s: sum(p["status"] == s for p in pcs) for s in ("online", "in_use", "missing")}
    return {"now": stamp, "timeout": timeout, "counts": counts, "pcs": pcs, "version": benchpulse.__version__}


def rdp_file(name):
    return (f"full address:s:{name}\r\nprompt for credentials:i:1\r\nscreen mode id:i:2\r\n"
            "use multimon:i:0\r\nredirectclipboard:i:1\r\n")


class Hub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store, token=None, timeout=30.0):
        super().__init__(addr, Handler)
        self.store, self.token, self.timeout = store, token, timeout


class Handler(BaseHTTPRequestHandler):
    server: Hub
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):          # quiet: agents call every few seconds
        pass

    # ------------------------------------------------------------------ plumbing
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, code, msg):
        self._send(code, {"error": msg})

    def _json(self):
        # JSON only: a form on some other web page can't post JSON without a CORS preflight,
        # so random sites can't book or cancel bench PCs through a colleague's browser.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("Send JSON (Content-Type: application/json).")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large.")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _route(self):
        u = urlparse(self.path)
        return [unquote(p) for p in u.path.strip("/").split("/") if p], parse_qs(u.query)

    def _guard(self, fn):
        try:
            fn()
        except Conflict as e:
            self._error(409, str(e))
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self._error(400, str(e) if not isinstance(e, KeyError) else f"Missing field {e}.")

    # ------------------------------------------------------------------ verbs
    def do_GET(self):
        self._guard(self._get)

    do_HEAD = do_GET

    def do_POST(self):
        self._guard(self._post)

    def do_DELETE(self):
        self._guard(self._delete)

    def _get(self):
        parts, q = self._route()
        st = self.server.store
        if parts[:2] == ["api", "board"]:
            return self._send(200, board(st, timeout=self.server.timeout))
        if parts[:2] == ["api", "reservations"]:
            return self._send(200, st.reservations(q.get("from", [None])[0], q.get("to", [None])[0]))
        if parts[:2] == ["api", "people"]:
            return self._send(200, st.people())
        if parts[:2] == ["api", "health"]:
            return self._send(200, {"ok": True, "version": benchpulse.__version__})
        if len(parts) == 2 and parts[0] == "rdp" and parts[1].endswith(".rdp"):
            name = parts[1][:-4]
            if not NAME_RE.match(name) or not any(p["name"].lower() == name.lower() for p in st.pcs()):
                return self._error(404, "Unknown PC.")
            return self._send(200, rdp_file(name).encode(), "application/x-rdp",
                              {"Content-Disposition": f'attachment; filename="{name}.rdp"'})
        return self._static(parts)

    def _static(self, parts):
        path = os.path.abspath(os.path.join(WEB, *parts)) if parts else os.path.join(WEB, "index.html")
        if not (path == WEB or path.startswith(WEB + os.sep)) or not os.path.isfile(path):
            return self._error(404, "Not found.")
        with open(path, "rb") as f:
            self._send(200, f.read(), TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))

    def _post(self):
        parts, _ = self._route()
        st = self.server.store
        if parts[:2] == ["api", "heartbeat"]:
            token = self.server.token
            if token and not hmac.compare_digest(self.headers.get("X-BenchPulse-Token", ""), token):
                return self._error(403, "Wrong agent token.")
            d = self._json()
            name = str(d["name"])
            if not NAME_RE.match(name):
                raise ValueError("Bad PC name.")
            usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
            info = {"usage": {k: usage.get(k) for k in ("in_use", "kind", "user", "client_name", "client_ip", "idle_s")},
                    "os": str(d.get("os", ""))[:80], "uptime_s": d.get("uptime_s"), "version": str(d.get("version", ""))[:20]}
            st.heartbeat(name, self.client_address[0], info)
            return self._send(200, {"ok": True})
        if parts[:2] == ["api", "reservations"]:
            d = self._json()
            pc, who = _text(d, "pc", 64), _text(d, "who", 60)
            if not NAME_RE.match(pc):
                raise ValueError("Bad PC name.")
            rid = st.reserve(pc, who, str(d["start"]), str(d["end"]), _text(d, "purpose", 200, required=False))
            return self._send(201, {"id": rid})
        if parts[:2] == ["api", "people"]:
            d = self._json()
            st.set_person(_text(d, "key", 64), _text(d, "person", 60))
            return self._send(200, {"ok": True})
        if len(parts) == 4 and parts[:2] == ["api", "pcs"] and parts[3] == "note":
            st.set_note(parts[2], _text(self._json(), "note", 200, required=False))
            return self._send(200, {"ok": True})
        self._error(404, "Not found.")

    def _delete(self):
        parts, _ = self._route()
        st = self.server.store
        if len(parts) == 3 and parts[:2] == ["api", "reservations"]:
            return self._send(200 if st.cancel(int(parts[2])) else 404, {"ok": True})
        if len(parts) == 3 and parts[:2] == ["api", "people"]:
            st.remove_person(parts[2])
            return self._send(200, {"ok": True})
        if len(parts) == 3 and parts[:2] == ["api", "pcs"]:
            st.forget(parts[2])
            return self._send(200, {"ok": True})
        self._error(404, "Not found.")


def _text(d, key, limit, required=True):
    v = str(d.get(key) or "").strip()
    if required and not v:
        raise ValueError(f"Please fill in '{key}'.")
    if len(v) > limit:
        raise ValueError(f"'{key}' is too long (max {limit}).")
    return v
