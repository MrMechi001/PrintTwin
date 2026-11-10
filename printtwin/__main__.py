"""Run the PrintTwin bridge:  python run.py [--config file.toml] [--open]"""
from __future__ import annotations

import argparse
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, config, incoming
from .config import PLACEHOLDERS, ROOT, filled  # noqa: F401 (PLACEHOLDERS: as before, for scripts)
from .bambu import BambuPrinter
from .moonraker import MoonrakerPrinter
from .printer import PrusaLinkPrinter, log
from .server import serve

KINDS = {"prusalink": PrusaLinkPrinter, "moonraker": MoonrakerPrinter, "bambu": BambuPrinter}


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
  host    = the printer's IP address   (printer: Settings > Network)
  api_key = its PrusaLink API key      (printer: Settings > Network > PrusaLink)
            MK3 / MK3S / MK3S+ with PrusaLink on a Raspberry Pi: the API key from PrusaLink's web page
            (Settings), or user + password from its setup
For the live nozzle position also turn on the printer's metrics (README: "Turn on the printer's metrics").
Then start PrintTwin again.
""")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="printtwin", description="Live 3D view of what a 3D printer is printing.")
    ap.add_argument("--config", type=Path, help="config file (default: config.local.toml next to run.py)")
    ap.add_argument("--open", action="store_true", help="open the viewer in the browser once the server runs")
    ap.add_argument("--version", action="version", version=f"PrintTwin {__version__}")
    args = ap.parse_args(argv)
    if args.config is None and first_run(config.DEFAULT_CONFIG):
        sys.exit(1)
    cfg = config.load(args.config)
    def _incomplete(pc: dict) -> bool:
        kind = pc.get("kind", "prusalink")
        if not filled(pc.get("host")):
            return True
        if kind == "prusalink":                 # the API key - or user + password (PrusaLink on a Raspberry Pi)
            return not filled(pc.get("api_key")) and not (filled(pc.get("user")) and filled(pc.get("password")))
        if kind == "bambu":                     # the serial number and the LAN access code (printer screen)
            return not all(filled(pc.get(k)) for k in ("serial", "access_code"))
        return False
    pending = [pc.get("id", "?") for pc in cfg.get("printer", []) if _incomplete(pc)]
    if pending or not cfg.get("printer"):
        print(f"Please enter your printer's IP address and its PrusaLink API key (or PrusaLink's user and "
              f"password; Bambu Lab: serial number and LAN access code) in {args.config or config.DEFAULT_CONFIG} "
              f"(printer {', '.join(pending) or '- none configured -'}).")
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
        httpd = serve(s["listen_host"], s["port"], printers, slicer=cfg["slicer"]["print_host"], log=log)
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
    if args.open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
