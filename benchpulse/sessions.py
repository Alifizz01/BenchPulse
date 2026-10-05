"""Who is using this PC right now: Remote Desktop sessions and the local console.

Windows keeps one "session" per login: the console (keyboard and screen) and one per Remote
Desktop connection. The Terminal Services API (wtsapi32) lists them with their state, user,
and for RDP the *client's* computer name and IP, so no qwinsta/netstat parsing is needed and
it works on every Windows edition.
"""
import sys

ACTIVE, DISCONNECTED = 0, 4          # WTS_CONNECTSTATE_CLASS values we care about
LOCAL_IDLE_LIMIT = 15 * 60           # console counts as "in use" if touched within 15 min


def classify(sessions, console_idle_s=None, idle_limit=LOCAL_IDLE_LIMIT):
    """Turn raw sessions into the one answer the dashboard needs.

    sessions: [{"station": "RDP-Tcp#3", "state": 0, "user": "max", "client_name": "LAPTOP-MAX",
                "client_ip": "10.0.0.23"}, ...]
    Remote Desktop wins over local use: if someone is connected remotely, that's who has it.
    """
    for s in sessions:
        if s["state"] == ACTIVE and s["station"].upper().startswith("RDP-TCP") and s.get("user"):
            return {"in_use": True, "kind": "remote", "user": s["user"],
                    "client_name": s.get("client_name") or "", "client_ip": s.get("client_ip") or ""}
    for s in sessions:
        if s["state"] == ACTIVE and s["station"].lower() == "console" and s.get("user"):
            if console_idle_s is not None and console_idle_s < idle_limit:
                return {"in_use": True, "kind": "local", "user": s["user"], "client_name": "", "client_ip": "",
                        "idle_s": int(console_idle_s)}
            return {"in_use": False, "kind": "idle", "user": s["user"], "client_name": "", "client_ip": "",
                    "idle_s": None if console_idle_s is None else int(console_idle_s)}
    return {"in_use": False, "kind": "free", "user": "", "client_name": "", "client_ip": ""}


def ipv4_from_wts_address(raw):
    """WTS_CLIENT_ADDRESS: DWORD family, then 20 bytes; IPv4 sits at bytes 2..5 of the address."""
    if len(raw) < 10 or int.from_bytes(raw[:4], "little") != 2:     # AF_INET
        return ""
    return ".".join(str(b) for b in raw[6:10])


def windows_sessions():
    import ctypes
    from ctypes import wintypes

    wts = ctypes.WinDLL("wtsapi32")

    class SESSION(ctypes.Structure):
        _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR), ("State", ctypes.c_int)]

    def query(sid, cls, raw=False):
        buf, n = ctypes.c_void_p(), wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(None, sid, cls, ctypes.byref(buf), ctypes.byref(n)):
            return b"" if raw else ""
        try:
            return ctypes.string_at(buf, n.value) if raw else ctypes.wstring_at(buf)
        finally:
            wts.WTSFreeMemory(buf)

    info, count = ctypes.POINTER(SESSION)(), wintypes.DWORD()
    if not wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(info), ctypes.byref(count)):
        return []
    try:
        out = []
        for i in range(count.value):
            s = info[i]
            out.append({"station": s.pWinStationName or "", "state": s.State,
                        "user": query(s.SessionId, 5),                       # WTSUserName
                        "client_name": query(s.SessionId, 10),               # WTSClientName
                        "client_ip": ipv4_from_wts_address(query(s.SessionId, 14, raw=True))})
        return out
    finally:
        wts.WTSFreeMemory(info)


def console_idle_seconds():
    """Seconds since the last keyboard/mouse input (needs to run in the user's desktop session)."""
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def current_usage():
    if sys.platform != "win32":
        return {"in_use": False, "kind": "unsupported", "user": "", "client_name": "", "client_ip": ""}
    return classify(windows_sessions(), console_idle_seconds())
