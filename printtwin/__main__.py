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
from .config import ROOT
from .printer import PrusaLinkPrinter, log
from .server import serve

KINDS = {"prusalink": PrusaLinkPrinter}
PLACEHOLDERS = {"192.168.x.y", "PRUSALINK-API-KEY", ""}


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
    pending = [pc.get("id", "?") for pc in cfg.get("printer", [])
               if str(pc.get("host", "")).strip() in PLACEHOLDERS or str(pc.get("api_key", "")).strip() in PLACEHOLDERS]
    if pending or not cfg.get("printer"):
        print(f"Please enter your printer's IP address and PrusaLink API key in "
              f"{args.config or config.DEFAULT_CONFIG} (printer {', '.join(pending) or '- none configured -'}).")
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
        printers[pc["id"]] = cls(pc, cache, handover)
    by_ip = {}
    for p in printers.values():
        try:
            by_ip[socket.gethostbyname(p.host.split(":")[0])] = p
        except OSError as e:
            log(f"{p.id}: cannot resolve {p.host}: {e}")
    s = cfg["server"]
    url = f"http://localhost:{s['port']}/"
    try:                                    # bind first: a second PrintTwin must not start next to the first
        httpd = serve(s["listen_host"], s["port"], printers)
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
    if args.open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
