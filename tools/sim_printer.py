"""Simulated PrusaLink printer + PrintTwin bridge, for testing and demos without a real print.

Serves one job file the way an MK4 does mid-print (slow Wi-Fi download, state PRINTING,
Z rising layer by layer) and starts the bridge against it with its own cache/hand-over folders.

usage: python -I tools/sim_printer.py JOB.bgcode [--kbps 18] [--handover] [--port 8791]
  --kbps      download speed of the fake printer (real MK4 during a print: ~18 KB/s)
  --handover  put a copy into the hand-over folder first, like the PrusaSlicer hook does
  --heat S    seconds of "heating" before Z starts to rise (default 20)
  --layer S   seconds per layer (default 4)
  --refuse S  answer 404 to the job file download for the first S seconds (like a job sent via
              Prusa Connect, which the MK4 doesn't hand out while printing it)
Viewer: http://localhost:<port>/
Print host for a slicer (PrusaLink, API key "sim"): http://localhost:<port>/p/sim - an upload
with "upload and print" becomes the simulated job.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import incoming  # noqa: E402
from printtwin.toolpath import load_file  # noqa: E402

SIM = ROOT / "logs" / "sim"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("--kbps", type=float, default=18.0)
    ap.add_argument("--handover", action="store_true")
    ap.add_argument("--heat", type=float, default=20.0)
    ap.add_argument("--layer", type=float, default=4.0)
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--refuse", type=float, default=0.0)
    ap.add_argument("--printer-port", type=int, default=8799)
    ap.add_argument("--type", default="1.4.0", help="PrusaLink printer type code to report (MK4 = 1.4.0)")
    ap.add_argument("--model", default="auto", help="model = ... for the bridge config (default: detect)")
    a = ap.parse_args()

    shutil.rmtree(SIM, ignore_errors=True)
    (SIM / "cache").mkdir(parents=True)
    (SIM / "incoming").mkdir(parents=True)
    (SIM / "usb").mkdir(parents=True)
    if a.handover:
        incoming.store(a.job, a.job.name, "PrusaConnect", SIM / "incoming")
    job: dict = {}
    lock = threading.Lock()

    def start_job(path: Path):
        """The simulated print: the file, its layer heights, start time (a slicer upload replaces it)."""
        with lock:
            n = job.get("id", 0) + 1
            ext = path.suffix.lower()
            job.update(id=n, path=path, data=path.read_bytes(), layers=load_file(path).layer_z or [0.2],
                       t0=time.time(), m_ts=int(time.time()),          # new key every run -> no cache hit
                       short=f"SIMJOB~{n}" + (".BGC" if ext == ".bgcode" else ".GCO"))

    start_job(a.job)

    def z_now():
        layers = job["layers"]
        k = int(max(0.0, time.time() - job["t0"] - a.heat) / a.layer)
        return layers[min(k, len(layers) - 1)], min(1.0, k / max(1, len(layers)))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _key_ok(self):
            if self.headers.get("X-Api-Key") == "sim":
                return True
            self._json({"title": "Unauthorized", "message": "wrong or missing X-Api-Key"}, 401)
            return False

        def do_PUT(self):
            """PrusaLink upload (what PrusaSlicer / OrcaSlicer send): PUT /api/v1/files/usb/<name>."""
            if not self._key_ok():
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            if not self.path.startswith("/api/v1/files/usb/") or len(body) != n:
                return self._json({"title": "Bad request"}, 400)
            dst = SIM / "usb" / Path(unquote(self.path)).name
            if dst.exists() and self.headers.get("Overwrite") != "?1":
                return self._json({"title": "Conflict", "message": "file exists"}, 409)
            dst.write_bytes(body)
            started = self.headers.get("Print-After-Upload") == "?1"
            print(f"fake printer: upload {dst.name} {n} bytes{', printing it' if started else ''}", flush=True)
            if started:
                start_job(dst)
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._key_ok():
                return
            z, prog = z_now()
            t0, data, short, layers = job["t0"], job["data"], job["short"], job["layers"]
            printing = time.time() - t0 > a.heat
            if self.path == "/api/v1/storage":
                return self._json({"storage_list": [{"path": "/usb", "name": "usb", "type": "USB", "read_only": False,
                                                     "free_space": 30 << 30, "available": True}]})
            if self.path == "/api/v1/status":
                return self._json({
                    "printer": {"state": "PRINTING", "temp_nozzle": 215.0 if printing else 180.0,
                                "target_nozzle": 215.0, "temp_bed": 60.0, "target_bed": 60.0,
                                "axis_z": z, "fan_hotend": 7000, "fan_print": 60, "speed": 100, "flow": 100},
                    "job": {"id": job["id"], "progress": round(100 * prog, 1),
                            "time_remaining": int((len(layers) * a.layer) * (1 - prog)),
                            "time_printing": int(max(0, time.time() - t0))}})
            if self.path == "/api/version":
                return self._json({"api": "2.0.0", "server": "sim", "text": "PrusaLink", "hostname": "sim",
                                   "firmware": "sim", "printer": a.type, "capabilities": {"upload-by-put": True}})
            if self.path == "/api/v1/job":
                return self._json({"id": job["id"], "state": "PRINTING", "progress": round(100 * prog, 1),
                                   "file": {"name": short, "display_name": job["path"].name, "size": len(data),
                                            "m_timestamp": job["m_ts"], "refs": {"download": f"/usb/{short}"}}})
            if self.path == f"/usb/{short}" and time.time() - t0 < a.refuse:
                self.send_error(404)
                return
            if self.path == f"/usb/{short}":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                step = 2048
                for i in range(0, len(data), step):
                    self.wfile.write(data[i:i + step])
                    self.wfile.flush()
                    time.sleep(step / (a.kbps * 1024))
                return
            self.send_error(404)

    fake = ThreadingHTTPServer(("127.0.0.1", a.printer_port), H)
    fake.daemon_threads = True
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    print(f"fake printer on 127.0.0.1:{a.printer_port}: {a.job.name}, {len(job['data'])} bytes at {a.kbps} KB/s"
          f"{', handed over' if a.handover else ''}", flush=True)

    cfg = SIM / "config.toml"
    cfg.write_text(
        f'cache_dir = "{(SIM / "cache").as_posix()}"\nincoming_dir = "{(SIM / "incoming").as_posix()}"\n\n'
        f'[[printer]]\nid = "sim"\nname = "Simulated printer"\nkind = "prusalink"\nmodel = "{a.model}"\n'
        f'host = "127.0.0.1:{a.printer_port}"\napi_key = "sim"\n\n'
        f'[server]\nlisten_host = "127.0.0.1"\nport = {a.port}\n\n'
        f'[metrics]\nlisten_host = "127.0.0.1"\nlisten_port = {a.printer_port + 1}\n\n'
        f'[slicer]\nprint_host = true\n', encoding="utf-8")
    from printtwin.__main__ import main as bridge
    bridge(["--config", str(cfg)])


if __name__ == "__main__":
    main()
