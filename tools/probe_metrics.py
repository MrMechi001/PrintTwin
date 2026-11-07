"""Record the raw UDP metrics stream + PrusaLink status side by side.

Writes logs/metrics_<stamp>.log  (recv_time<TAB>source_ip<TAB>repr(payload))
       logs/status_<stamp>.jsonl (PrusaLink /api/v1/status every 5 s)
Stop the main bridge first - both use the same UDP port.
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin import config  # noqa: E402
from printtwin.config import ROOT  # noqa: E402
from printtwin.prusalink import PrusaLink  # noqa: E402

cfg = config.load()
mcfg = cfg["metrics"]
p = cfg["printer"][0]
stamp = time.strftime("%Y%m%d_%H%M%S")
logdir = ROOT / "logs"
logdir.mkdir(exist_ok=True)
mlog = open(logdir / f"metrics_{stamp}.log", "a", encoding="utf-8", buffering=1)
slog = open(logdir / f"status_{stamp}.jsonl", "a", encoding="utf-8", buffering=1)


def poll_status():
    pl = PrusaLink(p["host"], p["api_key"])
    while True:
        try:
            st, data = pl.get_json("/api/v1/status")
            slog.write(json.dumps({"t": time.time(), "http": st, "data": data}) + "\n")
        except Exception as e:
            slog.write(json.dumps({"t": time.time(), "error": str(e)}) + "\n")
        time.sleep(5)


threading.Thread(target=poll_status, daemon=True).start()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((mcfg["listen_host"], mcfg["listen_port"]))
print(f"listening UDP {mcfg['listen_host']}:{mcfg['listen_port']} -> {mlog.name}", flush=True)
n = 0
while True:
    payload, addr = sock.recvfrom(65535)
    n += 1
    mlog.write(f"{time.time():.3f}\t{addr[0]}\t{payload!r}\n")
    if n in (1, 10, 100) or n % 1000 == 0:
        print(f"{n} datagrams, last from {addr[0]}: {payload[:120]!r}", flush=True)
