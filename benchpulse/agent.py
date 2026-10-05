"""The agent: runs on every bench PC and tells the hub "I'm on, and this is who's using me"
every few seconds. If the hub is down the agent just keeps trying; nothing queues up."""
import json
import platform
import socket
import sys
import time
import urllib.error
import urllib.request

import benchpulse
from benchpulse.sessions import current_usage


def uptime_seconds():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
        return int(ctypes.windll.kernel32.GetTickCount64() / 1000)
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except OSError:
        return None


def heartbeat(hub, name=None, token=None, usage=None, timeout=5):
    body = {"name": name or socket.gethostname(), "usage": usage if usage is not None else current_usage(),
            "os": platform.platform(terse=True), "uptime_s": uptime_seconds(), "version": benchpulse.__version__}
    req = urllib.request.Request(hub.rstrip("/") + "/api/heartbeat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"X-BenchPulse-Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run(hub, name=None, token=None, interval=10.0, quiet=False):
    """Heartbeat forever. Prints only when the connection state changes, so logs stay readable."""
    ok = None
    while True:
        try:
            heartbeat(hub, name, token)
            if ok is not True and not quiet:
                print(f"benchpulse agent: reporting to {hub} every {interval:g} s", flush=True)
            ok = True
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.exit("benchpulse agent: the hub rejected our token (--token).")
            ok = _lost(ok, f"hub answered {e.code}", quiet)
        except (OSError, ValueError) as e:
            ok = _lost(ok, e, quiet)
        time.sleep(interval)


def _lost(ok, why, quiet):
    if ok is not False and not quiet:
        print(f"benchpulse agent: can't reach the hub ({why}); retrying", flush=True)
    return False
