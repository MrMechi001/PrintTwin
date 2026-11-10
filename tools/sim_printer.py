"""Simulated PrusaLink printer + PrintTwin bridge, for testing and demos without a real print.

Serves one job file the way an MK4 does mid-print (slow Wi-Fi download, state PRINTING,
Z rising layer by layer) and starts the bridge against it with its own cache/hand-over folders.
With --pi it is an MK3S+ with PrusaLink 0.7.2 on a Raspberry Pi instead.

usage: python -I tools/sim_printer.py JOB.bgcode [--kbps 18] [--handover] [--port 8791]
  --kbps      download speed of the fake printer (real MK4 during a print: ~18 KB/s)
  --handover  put a copy into the hand-over folder first, like the PrusaSlicer hook does
  --heat S    seconds of "heating" before Z starts to rise (default 20)
  --layer S   seconds per layer (default 4)
  --refuse S  answer 404 to the job file download for the first S seconds (like a job sent via
              Prusa Connect, which the MK4 doesn't hand out while printing it)
  --type T    PrusaLink printer type code to report (MK4 = 1.4.0); 1.3.1 / 1.3.0 / 1.2.6 / 1.2.5
              mean PrusaLink on a Raspberry Pi (as --pi)
  --dir D     working folder (cache, hand-over, config; default logs/sim) - one per simulator

PrusaLink on a Raspberry Pi (MK3 / MK3S / MK3S+, what PrusaLink 0.7.2 answers):
  --pi        /api/version without "printer", with "original": "PrusaLink I3MK3S", api "0.9.0-legacy",
              text "PrusaLink 0.7.2"; status storage as a list, no axis_x/axis_y while printing, axis_z
              only every 2 s (M155 auto-report) and at random a z-hop above the layer (--zhop height,
              --zhop-rate share of the reports, --seed);
              the M73 percent of the job (P marks), time left; no UDP metrics; the job file under
              /api/files/local/PrusaLink gcodes/<name>/raw (with the space); state FINISHED at the end
  --stealth   the printer is in stealth mode: it reports the M73 Q percent (silent marks)
  --sd        the job is on the printer's SD card: refs.download is null (not downloadable)
  --digest U:P  no API key: HTTP digest login (realm "Administrator", MD5-sess, qop auth) with
              user U, password P; --nonce-ttl S: a nonce older than S seconds is stale (default 60)
  --no-printer S  answer 503 for the first S seconds (PrusaLink up, serial link to the printer down)
  --no-id S   no job id for the first S seconds (PrusaLink just restarted)
  GET /sim/truth and /sim/stats (no login): where the simulated print is, requests per path.

Viewer: http://localhost:<port>/
Print host for a slicer (PrusaLink, API key "sim"): http://localhost:<port>/p/sim - an upload
with "upload and print" becomes the simulated job.

A .claude/launch.json entry for the MK3S simulator (the viewer on port 8792):
  {"name": "sim-mk3s", "runtimeExecutable": "D:/PrintViz/pyenv/Scripts/python.exe",
   "runtimeArgs": ["-I", "tools/sim_printer.py", "tests/fixtures/sample.gcode", "--pi", "--port", "8792",
                   "--printer-port", "8798", "--dir", "logs/sim-mk3s"], "port": 8792}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from bisect import bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import incoming  # noqa: E402
from printtwin.toolpath import load_file  # noqa: E402

PI_TYPES = {"1.3.1": "I3MK3S", "1.3.0": "I3MK3", "1.2.6": "I3MK25S", "1.2.5": "I3MK25"}
REALM = "Administrator"
LOCAL = "PrusaLink gcodes"           # PrusaLink's default storage folder on the Pi - with a space
REPORT_S = 2.0                       # M155 S2: the firmware reports its position every 2 s
STEALTH_SLOWER = 1.15                # stealth mode's estimate is this much longer


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class DigestServer:
    """The server side of PrusaLink's digest login (PoorWSGI's defaults), checked strictly: opaque
    and algorithm must come back exactly, the uri must end with the request path."""

    def __init__(self, user: str, password: str, ttl: float):
        self.user, self.password, self.ttl = user, password, ttl
        self.opaque = _md5(os.urandom(8).hex())
        self.nonces: dict[str, float] = {}
        self.stats = {"ok": 0, "challenged": 0, "stale": 0, "refused": 0}
        self.lock = threading.Lock()

    def challenge(self, stale: bool = False) -> str:
        nonce = _md5(os.urandom(8).hex())
        with self.lock:
            self.nonces[nonce] = time.time()
            self.stats["stale" if stale else "challenged"] += 1
        return (f'Digest realm="{REALM}",qop="auth",algorithm="MD5-sess",nonce="{nonce}",'
                f'opaque="{self.opaque}"' + (",stale=true" if stale else ""))

    def check(self, header: str | None, method: str, path: str) -> str:
        """-> "ok", "stale", "login" (none sent) or "refused"."""
        if not header:
            return "login"                           # no login sent yet: the challenge
        if not header.startswith("Digest "):
            self.stats["refused"] += 1
            return "refused"
        f = {k: (q if q else v) for k, q, v in re.findall(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', header[7:])}
        with self.lock:
            issued = self.nonces.get(f.get("nonce", ""))
        if (f.get("username") != self.user or f.get("realm") != REALM or f.get("opaque") != self.opaque
                or f.get("algorithm") != "MD5-sess" or f.get("qop") != "auth" or issued is None
                or not unquote(f.get("uri", "")).endswith(unquote(path)) or not f.get("cnonce") or not f.get("nc")):
            self.stats["refused"] += 1
            return "refused"
        ha1 = _md5(f"{_md5(f'{self.user}:{REALM}:{self.password}')}:{f['nonce']}:{f['cnonce']}")
        want = _md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:auth:{_md5(method + ':' + f['uri'])}")
        if f.get("response") != want:
            self.stats["refused"] += 1
            return "refused"
        if time.time() - issued > self.ttl:
            return "stale"
        self.stats["ok"] += 1
        return "ok"


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
    ap.add_argument("--type", default=None, help="PrusaLink printer type code to report (MK4 = 1.4.0)")
    ap.add_argument("--model", default="auto", help="model = ... for the bridge config (default: detect)")
    ap.add_argument("--dir", type=Path, default=ROOT / "logs" / "sim", help="working folder of this simulator")
    ap.add_argument("--pi", action="store_true", help="PrusaLink 0.7.2 on a Raspberry Pi (MK3S)")
    ap.add_argument("--stealth", action="store_true", help="Pi: report the stealth-mode percent (M73 Q)")
    ap.add_argument("--sd", action="store_true", help="Pi: the job is on the SD card (not downloadable)")
    ap.add_argument("--digest", default=None, help="Pi: USER:PASSWORD for an HTTP digest login instead of a key")
    ap.add_argument("--nonce-ttl", type=float, default=60.0)
    ap.add_argument("--zhop", type=float, default=0.4, help="Pi: z-hop height in some of the 2 s Z reports")
    ap.add_argument("--zhop-rate", type=float, default=0.33, help="Pi: share of the Z reports taken during a z-hop")
    ap.add_argument("--seed", type=int, default=1, help="Pi: which z-hop reports (random, the same per seed)")
    ap.add_argument("--no-printer", type=float, default=0.0, help="Pi: 503 for the first S seconds")
    ap.add_argument("--no-id", type=float, default=0.0, help="Pi: no job id for the first S seconds")
    a = ap.parse_args()
    a.pi = a.pi or a.type in PI_TYPES
    a.type = a.type or ("1.3.1" if a.pi else "1.4.0")
    if a.pi and a.type not in PI_TYPES:
        ap.error(f"--pi: --type must be one of {', '.join(PI_TYPES)}")
    digest = None
    if a.digest:
        user, _, password = a.digest.partition(":")
        digest = DigestServer(user, password, a.nonce_ttl)

    SIM = a.dir if a.dir.is_absolute() else ROOT / a.dir
    shutil.rmtree(SIM, ignore_errors=True)
    (SIM / "cache").mkdir(parents=True)
    (SIM / "incoming").mkdir(parents=True)
    (SIM / "usb").mkdir(parents=True)
    if a.handover:
        incoming.store(a.job, a.job.name, "PrusaConnect", SIM / "incoming")
    job: dict = {}
    lock = threading.Lock()
    t_start = time.time()
    stats: dict[str, int] = {}

    def start_job(path: Path):
        """The simulated print: the file, its layer heights, start time (a slicer upload replaces it)."""
        with lock:
            n = job.get("id", 0) + 1
            ext = path.suffix.lower()
            tp = load_file(path)
            job.update(id=n, path=path, data=path.read_bytes(), layers=tp.layer_z or [0.2], tp=tp,
                       t0=time.time(), m_ts=int(time.time()),          # new key every run -> no cache hit
                       short=f"SIMJOB~{n}" + (".BGC" if ext == ".bgcode" else ".GCO"))

    start_job(a.job)

    def z_now():
        layers = job["layers"]
        k = int(max(0.0, time.time() - job["t0"] - a.heat) / a.layer)
        return layers[min(k, len(layers) - 1)], min(1.0, k / max(1, len(layers)))

    def pi_print(t: float) -> dict:
        """The MK3S's print at time t: where the nozzle is, the M73 percent the printer reports."""
        tp, t0 = job["tp"], job["t0"]
        ls, lz, n = tp.layer_start, job["layers"], tp.count
        e = t - t0 - a.heat
        if e < 0:                                    # heating, probing: Z all over the place, 0 %
            return {"state": "PRINTING", "seg": 0.0, "layer": -1, "z": (5.0, 0.2, 2.0)[int(t / REPORT_S) % 3],
                    "P": 0, "Q": 0, "done": False}
        k = int(e / a.layer)
        if k >= len(ls):
            return {"state": "FINISHED", "seg": float(n), "layer": len(ls) - 1, "z": lz[-1], "P": 100, "Q": 100,
                    "done": True}
        end = ls[k + 1] if k + 1 < len(ls) else n
        seg = ls[k] + (e / a.layer - k) * (end - ls[k])

        def pct(marks):
            i = bisect_right([s for s, _ in marks], seg) - 1
            return marks[i][1] if i >= 0 else 0
        return {"state": "PRINTING", "seg": seg, "layer": k, "z": lz[k], "P": pct(tp.progress_marks),
                "Q": pct(tp.silent_marks or tp.progress_marks), "done": False}

    def pi_report_z(now: float) -> float:
        """axis_z as PrusaLink has it: the last M155 report (every 2 s), during a z-hop at random
        (--zhop-rate; reports in a row too, as on a real print), the same for every run of a --seed."""
        k = int((now - t_start) / REPORT_S)
        at = pi_print(t_start + k * REPORT_S)
        in_hop = random.Random(a.seed * 1_000_003 + k).random() < a.zhop_rate
        hop = a.zhop if at["state"] == "PRINTING" and at["layer"] >= 0 and in_hop else 0.0
        return round(at["z"] + hop, 2)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, code=200, headers=None):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _key_ok(self):
            if a.pi and time.time() - t_start < a.no_printer:     # before any login, like PrusaLink
                self._json({"title": "Service Unavailable", "message": "printer not connected"}, 503)
                return False
            key = self.headers.get("X-Api-Key")
            if key == "sim" and not digest:
                return True
            if a.pi and key is not None:
                self._json({"title": "Forbidden", "message": "Bad X-Api-Key."}, 403)
                return False
            if digest and key is None:
                r = digest.check(self.headers.get("Authorization"), self.command, self.path)
                if r == "ok":
                    return True
                self._json({"title": "Unauthorized"}, 401,
                           {"WWW-Authenticate": digest.challenge(stale=r == "stale")})
                return False
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
            with lock:
                stats[unquote(self.path)] = stats.get(unquote(self.path), 0) + 1
            if self.path == "/sim/stats":
                return self._json({"requests": stats, "digest": digest.stats if digest else None})
            if self.path == "/sim/truth":
                st = pi_print(time.time())
                return self._json({**st, "count": job["tp"].count, "layer_start": job["tp"].layer_start})
            if not self._key_ok():
                return
            if a.pi:
                return self._pi()
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
                return self._send_file(data)
            self.send_error(404)

        def _send_file(self, data: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            step = 2048
            for i in range(0, len(data), step):
                self.wfile.write(data[i:i + step])
                self.wfile.flush()
                time.sleep(step / (a.kbps * 1024))

        def _pi(self):
            """PrusaLink 0.7.2 on a Raspberry Pi (prusa/link/web/main.py), MK3S+ firmware 3.14.1."""
            now = time.time()
            st = pi_print(now)
            data, name = job["data"], job["path"].name
            total = len(job["layers"]) * a.layer
            pct = st["Q"] if a.stealth else st["P"]
            remaining = int(round(total * (STEALTH_SLOWER if a.stealth else 1.0) * (1 - pct / 100)))
            with_id = now - t_start >= a.no_id
            folder = "/SD Card" if a.sd else f"/{LOCAL}"
            if self.path == "/api/version":
                return self._json({"api": "0.9.0-legacy", "server": "2.1.2", "original": f"PrusaLink {PI_TYPES[a.type]}",
                                   "text": "PrusaLink 0.7.2", "firmware": "3.14.1-8237", "sdk": "0.7.1",
                                   "capabilities": {"upload-by-put": True}, "hostname": "prusa-mk3s"})
            if self.path == "/api/v1/status":
                printer = {"state": st["state"], "temp_nozzle": 215.0, "target_nozzle": 215.0, "temp_bed": 60.0,
                           "target_bed": 60.0, "axis_z": pi_report_z(now), "flow": 100, "speed": 100,
                           "fan_hotend": 4800, "fan_print": 5000, "status_connect": {"ok": True, "message": "OK"},
                           "status_printer": {"ok": True, "message": "OK"}}
                if st["state"] not in ("PRINTING", "BUSY"):
                    printer.update(axis_x=125.0, axis_y=-3.0)
                jb = {"progress": float(pct), "time_remaining": remaining, "time_printing": int(now - job["t0"])}
                if with_id:
                    jb["id"] = job["id"]
                return self._json({"storage": [{"path": "/local", "read_only": False, "name": LOCAL,
                                                "free_space": 12 << 30},
                                               {"path": "/sdcard", "read_only": True, "name": "SD Card"}],
                                   "printer": printer, "job": jb})
            if self.path == "/api/v1/job":
                jb = {"state": st["state"], "progress": float(pct), "time_remaining": remaining,
                      "time_printing": int(now - job["t0"]), "inaccurate_estimates": False,
                      "file": {"name": name, "display_name": name, "path": f"{folder}/{name}",
                               "display_path": f"{folder}/{name}", "size": len(data), "m_timestamp": job["m_ts"],
                               "refs": {"download": None if a.sd else f"/api/files/local/{LOCAL}/{name}/raw",
                                        "icon": None, "thumbnail": None}, "meta": {}}}
                if with_id:
                    jb["id"] = job["id"]
                return self._json(jb)
            if self.path == "/api/v1/storage":
                return self._json({"storage_list": [{"path": "/local", "name": LOCAL, "type": "LOCAL",
                                                     "read_only": False, "free_space": 12 << 30, "available": True}]})
            if not a.sd and unquote(self.path) == f"/api/files/local/{LOCAL}/{name}/raw":
                return self._send_file(data)
            self.send_error(404)

    fake = ThreadingHTTPServer(("127.0.0.1", a.printer_port), H)
    fake.daemon_threads = True
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    what = (f"PrusaLink 0.7.2 on a Pi ({PI_TYPES[a.type]}{', stealth' if a.stealth else ''}"
            f"{', SD card' if a.sd else ''}{', digest login' if digest else ''})") if a.pi else f"type {a.type}"
    print(f"fake printer on 127.0.0.1:{a.printer_port}: {a.job.name}, {len(job['data'])} bytes at {a.kbps} KB/s, "
          f"{what}{', handed over' if a.handover else ''}", flush=True)

    cfg = SIM / "config.toml"
    login = (f'user = "{digest.user}"\npassword = "{digest.password}"\n' if digest else 'api_key = "sim"\n')
    cfg.write_text(
        f'cache_dir = "{(SIM / "cache").as_posix()}"\nincoming_dir = "{(SIM / "incoming").as_posix()}"\n\n'
        f'[[printer]]\nid = "sim"\nname = "Simulated printer"\nkind = "prusalink"\nmodel = "{a.model}"\n'
        f'host = "127.0.0.1:{a.printer_port}"\n{login}\n'
        f'[server]\nlisten_host = "127.0.0.1"\nport = {a.port}\n\n'
        f'[metrics]\nlisten_host = "127.0.0.1"\nlisten_port = {a.printer_port + 1}\n\n'
        f'[slicer]\nprint_host = true\n', encoding="utf-8")
    from printtwin.__main__ import main as bridge
    bridge(["--config", str(cfg)])


if __name__ == "__main__":
    main()
