"""benchpulse hub | agent | demo | status"""
import argparse
import json
import os
import sys
import urllib.request

import benchpulse


def _startup_dir():
    return os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")


def install(role, argv):
    """Start this role at every logon: a .pyw launcher in the user's Startup folder (pythonw, no
    console window). The agent must run inside the user's session to see keyboard idle time."""
    if sys.platform != "win32":
        sys.exit("--install is for Windows; on Linux use a systemd unit or cron @reboot.")
    args = [a for a in argv if a != "--install"]
    path = os.path.join(_startup_dir(), f"benchpulse-{role}.pyw")
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(benchpulse.__file__)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Started at logon by Windows. Remove this file to stop BenchPulse {role}.\n"
                f"import sys\nsys.path.insert(0, {pkg_parent!r})\n"
                f"from benchpulse.cli import main\nmain({args!r})\n")
    print(f"Installed: {path}\nIt starts at your next logon. Delete that file to undo.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="benchpulse", description="See which bench PCs are on, who is using them, and book them.")
    p.add_argument("--version", action="version", version=f"benchpulse {benchpulse.__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hub", help="run the dashboard + API on this PC")
    h.add_argument("--port", type=int, default=8600)
    h.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all, so colleagues can reach it)")
    h.add_argument("--db", default=os.path.join(os.path.expanduser("~"), ".benchpulse.db"))
    h.add_argument("--token", default=os.environ.get("BENCHPULSE_TOKEN"), help="shared secret agents must send")
    h.add_argument("--timeout", type=float, default=30, help="seconds without a heartbeat before a PC is 'missing'")
    h.add_argument("--install", action="store_true", help="start the hub at every logon (Windows)")

    a = sub.add_parser("agent", help="report this PC to a hub")
    a.add_argument("--hub", required=True, help="e.g. http://10.0.0.5:8600")
    a.add_argument("--name", help="name shown on the dashboard (default: computer name)")
    a.add_argument("--token", default=os.environ.get("BENCHPULSE_TOKEN"))
    a.add_argument("--interval", type=float, default=10)
    a.add_argument("--once", action="store_true", help="send one heartbeat, print it, exit")
    a.add_argument("--install", action="store_true", help="start the agent at every logon (Windows)")

    d = sub.add_parser("demo", help="a hub with a simulated bench of 8 PCs, to try it out")
    d.add_argument("--port", type=int, default=8600)
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--no-browser", action="store_true")

    s = sub.add_parser("status", help="print the board of a running hub")
    s.add_argument("--hub", default="http://127.0.0.1:8600")

    args = p.parse_args(argv)
    if getattr(args, "install", False):
        return install(args.cmd, argv)

    if args.cmd == "hub":
        from benchpulse.hub import Hub
        from benchpulse.store import Store
        hub = Hub((args.host, args.port), Store(args.db), token=args.token, timeout=args.timeout)
        print(f"BenchPulse hub on http://{_lan_ip() if args.host == '0.0.0.0' else args.host}:{args.port}  (db: {args.db})")
        try:
            hub.serve_forever()
        except KeyboardInterrupt:
            pass
    elif args.cmd == "agent":
        from benchpulse import agent
        if args.once:
            from benchpulse.sessions import current_usage
            u = current_usage()
            print(json.dumps(u, indent=2))
            print(agent.heartbeat(args.hub, args.name, args.token, usage=u))
        else:
            agent.run(args.hub, args.name, args.token, args.interval)
    elif args.cmd == "demo":
        from benchpulse.demo import run_demo
        run_demo(args.host, args.port, open_browser=not args.no_browser)
    elif args.cmd == "status":
        with urllib.request.urlopen(args.hub.rstrip("/") + "/api/board", timeout=5) as r:
            b = json.loads(r.read())
        for pc in b["pcs"]:
            u = pc["usage"]
            who = pc["person"] or u.get("client_name") or u.get("user") or ""
            print(f"{pc['status']:8} {pc['name']:18} {pc['ip'] or '':15} {who}")


def _lan_ip():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))       # no packet is sent; just picks the LAN interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
