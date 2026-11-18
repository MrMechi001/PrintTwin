"""Run the PrintTwin bridge:  python run.py [--config file.toml] [--open] [--until-done]"""
from __future__ import annotations

import argparse
import shutil
import socket
import sys
import threading
import time
import tomllib
import webbrowser
from pathlib import Path

from . import __version__, config, incoming
from .config import PLACEHOLDERS, ROOT, filled  # noqa: F401 (PLACEHOLDERS: as before, for scripts)
from .bambu import BambuPrinter
from .moonraker import MoonrakerPrinter
from .printer import PrusaLinkPrinter, log
from .server import serve

KINDS = {"prusalink": PrusaLinkPrinter, "moonraker": MoonrakerPrinter, "bambu": BambuPrinter}
BUSY = {"PRINTING", "PAUSED", "BUSY", "ATTENTION"}     # a print runs (or waits for the user)


def watch_until_done(printers: dict, stop, linger: float = 600.0, wait: float = 1800.0, poll: float | None = None,
                     clock=time.monotonic, sleep=time.sleep):
    """--until-done (the slicer hook started PrintTwin for a job it sent): call stop() `linger` s
    after the last print ended - or `wait` s after the start if no print begins at all."""
    poll = poll or min(5.0, max(0.2, min(linger, wait) / 5))
    last = clock()
    seen = False
    while True:
        sleep(poll)
        now = clock()
        try:
            busy = any(p.snapshot().get("state") in BUSY for p in printers.values())
        except Exception as e:                  # never let a bad snapshot stop PrintTwin early
            log(f"until-done: {e!r}")
            busy = True
        if busy:
            seen, last = True, now
        elif now - last >= (linger if seen else wait):
            log("the print is done - PrintTwin stops (it was started in the background)" if seen else
                f"no print started within {wait / 60:g} min - PrintTwin stops (it was started in the background)")
            stop()
            return


def metrics_listener(host: str, port: int, by_ip: dict):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # Windows: no silent port sharing
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind((host, port))
    log(f"metrics: listening on UDP {host}:{port}")
    while True:
        payload, addr = sock.recvfrom(65535)
        p = by_ip.get(addr[0])
        if p is not None:
            try:
                p.on_datagram(payload)
            except Exception as e:
                log(f"metrics from {addr[0]}: {e!r}")


def first_run(path: Path) -> bool:
    """No config yet: create it from the example and explain what to fill in."""
    if path.exists():
        return False
    example = ROOT / "config.example.toml"
    shutil.copyfile(example, path)
    print(f"""
PrintTwin {__version__} - first start
Created {path}
Open it in a text editor and fill in your printer:
  Prusa:     host    = the printer's IP address   (printer: Settings > Network)
             api_key = its PrusaLink API key      (printer: Settings > Network > PrusaLink)
             MK3 / MK3S / MK3S+ with PrusaLink on a Raspberry Pi: the API key from PrusaLink's web page
             (Settings), or user + password from its setup
  Bambu Lab: use the Bambu block further down instead (remove the # in front of its lines):
             kind = "bambu", host = the IP address, serial = the serial number,
             access_code = the LAN access code (both on the printer's screen)
  Klipper:   the Moonraker block further down (kind = "moonraker", host = Mainsail / Fluidd address)
For the live nozzle position also turn on the printer's metrics (README: "Turn on the printer's metrics").
Then start PrintTwin again.
""")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="printtwin", description="Live 3D view of what a 3D printer is printing.")
    ap.add_argument("command", nargs="?", choices=["connect-login"],
                    help="connect-login: sign in to Prusa Connect (for prints sent via Prusa Connect)")
    ap.add_argument("--config", type=Path, help="config file (default: config.local.toml next to run.py)")
    ap.add_argument("--open", action="store_true", help="open the viewer in the browser once the server runs")
    ap.add_argument("--until-done", action="store_true",
                    help="stop by itself after the print (how the slicer hook starts PrintTwin)")
    ap.add_argument("--version", action="version", version=f"PrintTwin {__version__}")
    args = ap.parse_args(argv)
    if args.config is None and first_run(config.DEFAULT_CONFIG):
        sys.exit(1)
    try:
        cfg = config.load(args.config)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"{args.config or config.DEFAULT_CONFIG} can't be read: {e}\n"
                 f'(text needs quotes, e.g.  serial = "01P00A123456789"  and  access_code = "12345678")')
    if args.command == "connect-login":
        from . import connect
        try:
            connect.login(cfg["connect"]["tokens"])
        except (connect.ConnectError, KeyboardInterrupt, EOFError) as e:
            sys.exit(f"Not signed in: {e}")
        return
    try:
        text = Path(args.config or config.DEFAULT_CONFIG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    notes, problems = config.prepare_printers(cfg, text)
    for n in notes:
        log(n)
    if problems:
        print(f"PrintTwin can't start yet - please complete {args.config or config.DEFAULT_CONFIG}:\n  "
              + "\n  ".join(problems)
              + "\n(the README shows a full block for each kind of printer: Prusa, Klipper, Bambu Lab)")
        sys.exit(1)
    cache = Path(cfg.get("cache_dir") or ROOT / "cache")
    cache.mkdir(parents=True, exist_ok=True)
    handover = Path(cfg.get("incoming_dir") or incoming.INCOMING)
    printers = {}
    for pc in cfg.get("printer", []):
        cls = KINDS.get(pc.get("kind", "prusalink"))
        if cls is None:
            log(f"skipping printer {pc.get('id')}: unknown kind {pc.get('kind')!r}")
            continue
        try:
            printers[pc["id"]] = cls(pc, cache, handover)
        except (KeyError, ValueError) as e:
            log(f"skipping printer {pc.get('id')}: {e}")
    if cfg["connect"]["enabled"]:
        from . import connect
        try:
            cn = connect.Connect(cfg["connect"]["tokens"])
            for p in printers.values():
                if p.kind == "prusalink":
                    p.connect = cn
            log("Prusa Connect: on (jobs the printer doesn't hand out come from Prusa Connect)")
        except connect.ConnectError as e:
            log(f"Prusa Connect: {e}")
    by_ip = {}
    for p in printers.values():
        if not hasattr(p, "on_datagram"):           # UDP firmware metrics: Prusa printers only
            continue
        try:
            by_ip[socket.gethostbyname(p.http_netloc.split(":")[0])] = p
        except OSError as e:
            log(f"{p.id}: cannot resolve {p.host}: {e}")
    s = cfg["server"]
    url = f"http://localhost:{s['port']}/"
    try:                                    # bind first: a second PrintTwin must not start next to the first
        httpd = serve(s["listen_host"], s["port"], printers, slicer=cfg["slicer"]["print_host"], log=log,
                      control=cfg["control"])
    except OSError as e:
        print(f"PrintTwin is already running (port {s['port']} is in use: {e.strerror or e}).\n"
              f"Opening the running one: {url}")
        if args.open:
            webbrowser.open(url)
        sys.exit(0)
    m = cfg["metrics"]
    threading.Thread(target=metrics_listener, args=(m["listen_host"], m["listen_port"], by_ip),
                     name="metrics", daemon=True).start()
    for p in printers.values():
        p.start()
    log(f"PrintTwin {__version__} - viewer: {url}  ({len(printers)} printer(s))")
    if cfg["slicer"]["print_host"]:
        host_type = {"prusalink": "PrusaLink, the printer's API key",
                     "moonraker": 'Klipper (via Moonraker) / Moonraker (Klipper) or OctoPrint'}
        for p in printers.values():
            if p.kind in host_type:         # Bambu printers: the slicer sends to the printer itself
                log(f"print host for PrusaSlicer / OrcaSlicer: http://<this PC>:{s['port']}/p/{p.id}  "
                    f"(host type {host_type[p.kind]})")
    if args.until_done:
        sl = cfg["slicer"]
        threading.Thread(target=watch_until_done, args=(printers, httpd.shutdown),
                         kwargs={"linger": sl["stop_after"] * 60, "wait": sl["start_within"] * 60},
                         name="until-done", daemon=True).start()
        log(f"started in the background: stops {sl['stop_after']:g} min after the print "
            f"(or after {sl['start_within']:g} min if no print starts)")
    if args.open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()


if __name__ == "__main__":
    main()
