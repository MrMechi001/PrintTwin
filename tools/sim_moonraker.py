"""Simulated Klipper printer (Moonraker API) + PrintTwin bridge, for testing without a Voron.

Plays a .gcode job the way Klipper reports it (see printtwin/moonraker.py): print_stats,
virtual_sdcard.file_position about 1 s of motion ahead of the nozzle, motion_report.live_position
refreshed every 250 ms (in toolhead coordinates: G-code offset Z +0.02 mm), metadata, file
download, the slicer upload routes (POST /server/files/upload and the OctoPrint-style
/api/files/local, "upload and print" starts the job) and POST /printer/print/start.

usage: python -I tools/sim_moonraker.py JOB.gcode [--duration 120] [--heat 10] [--port 8793]
  --duration S   seconds from the first to the last extrusion (default 120)
  --heat S       seconds of PRINT_START (heating, homing) before the first move (default 10)
  --kinematics K Klipper kinematics to report (default corexy); --qgl: quad gantry level (V2.4)
  --api-key K    require this X-Api-Key (default: trusted clients, no key)
  --rewrite      change uploaded files (like Moonraker's object processing) - size differs
  --kbps N       download speed of /server/files/gcodes (default 4000)
Viewer: http://localhost:<port>/  -  print host for a slicer: http://localhost:<port>/p/sim
(PrusaSlicer "Klipper (via Moonraker)" / OrcaSlicer "Moonraker (Klipper)" or OctoPrint).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin.toolpath import load_file  # noqa: E402

SIM = ROOT / "logs" / "sim_moonraker"
GCODE_OFFSET_Z = 0.02          # SET_GCODE_OFFSET Z=0.02: toolhead = file coordinates + this
LEAD_S = 1.0                   # Klipper reads about this much motion ahead of the nozzle


def multipart(body: bytes, ctype: str) -> dict:
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    out = {}
    for part in body.split(b"--" + m.group(1).encode()) if m else []:
        head, _, val = part.partition(b"\r\n\r\n")
        val = val[:-2] if val.endswith(b"\r\n") else val
        nm = re.search(rb'name="([^"]*)"', head)
        fn = re.search(rb'filename="([^"]*)"', head)
        if nm:
            out[nm.group(1).decode()] = (val, fn.group(1).decode() if fn else None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--printer-port", type=int, default=7125)
    ap.add_argument("--kinematics", default="corexy")
    ap.add_argument("--qgl", action="store_true")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--rewrite", action="store_true")
    ap.add_argument("--kbps", type=float, default=4000.0)
    a = ap.parse_args()

    shutil.rmtree(SIM, ignore_errors=True)
    gdir, cdir = SIM / "gcodes", SIM / "config"
    for d in (gdir, cdir, SIM / "cache", SIM / "incoming"):
        d.mkdir(parents=True)
    (cdir / "printer.cfg").write_text("[printer]\nkinematics: corexy\n", encoding="utf-8")
    shutil.copyfile(a.job, gdir / a.job.name)
    lock = threading.Lock()
    job: dict = {}
    calls: list = []                             # (method, path) of every request, for the tests

    def start(rel: str) -> bool:
        """SDCARD_PRINT_FILE: start printing gcodes/<rel> (False while another print runs). Like
        Klipper, a name that only matches case-insensitively is found, and reported as asked."""
        with lock:
            if job.get("state") == "printing":
                return False
            path = gdir / rel
            if not path.is_file():
                hits = [f for f in gdir.rglob("*") if f.is_file()
                        and f.relative_to(gdir).as_posix().lower() == rel.lower()]
                if not hits:
                    return False
                path = hits[0]
            tp = load_file(path)
            n = max(1, tp.count)
            job.update(rel=rel, path=path, tp=tp, n=n, t0=time.time(), state="printing",
                       seg_s=a.duration / n, size=path.stat().st_size, mtime=path.stat().st_mtime)
            return True

    start(a.job.name)

    def now_state():
        """(state, segment index the nozzle is on + fraction, file position)."""
        tp, n = job["tp"], job["n"]
        t = time.time() - job["t0"] - a.heat
        if t < 0:
            return "printing", None, tp.line_off[0] if tp.count else 0      # PRINT_START: heating, homing
        i = t / job["seg_s"]
        if i >= n:
            job["state"] = "complete"
            return "complete", float(n), job["size"]
        lead = int(i + LEAD_S / job["seg_s"])
        return "printing", i, tp.line_off[min(lead, n - 1)]

    def live_pos(i):
        tp = job["tp"]
        if i is None or not tp.count:
            return [175.0, 175.0, 30.0 + GCODE_OFFSET_Z, 0.0]
        k = min(int(i), tp.count - 1)
        u = min(1.0, i - k)
        s, e = tp.start[3 * k:3 * k + 3], tp.end[3 * k:3 * k + 3]
        return [s[0] + (e[0] - s[0]) * u, s[1] + (e[1] - s[1]) * u, s[2] + (e[2] - s[2]) * u + GCODE_OFFSET_Z, 0.0]

    cache = {"t": 0.0, "pos": None}

    def status(obj: str):
        st, i, fp = now_state()
        tp = job["tp"]
        if time.time() - cache["t"] >= 0.25 or cache["pos"] is None:     # motion_report refresh
            cache["t"], cache["pos"] = time.time(), live_pos(i)
        lp = cache["pos"]
        pd = max(0.0, time.time() - job["t0"])
        done = st == "complete"
        return {
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            "print_stats": {"state": st, "filename": job["rel"], "print_duration": pd, "total_duration": pd,
                            "filament_used": 0.0, "message": "", "info": {"total_layer": None, "current_layer": None}},
            "virtual_sdcard": {"file_path": None if done else str(job["path"]), "file_position": fp,
                               "file_size": job["size"], "progress": fp / job["size"], "is_active": not done},
            "motion_report": {"live_position": lp, "live_velocity": 0.0 if i is None else 120.0,
                              "live_extruder_velocity": 0.0},
            "gcode_move": {"position": [lp[0], lp[1], lp[2], 0.0], "gcode_position": [lp[0], lp[1], lp[2] - GCODE_OFFSET_Z, 0.0],
                           "homing_origin": [0.0, 0.0, GCODE_OFFSET_Z, 0.0], "speed_factor": 1.0, "extrude_factor": 1.0,
                           "speed": 6000.0},
            "extruder": {"temperature": 265.0 if i is not None else 180.0, "target": 265.0, "power": 0.4},
            "heater_bed": {"temperature": 110.0, "target": 110.0, "power": 0.3},
            "fan": {"speed": 0.4 if i is not None else 0.0, "rpm": None},
            "display_status": {"progress": round(fp / job["size"], 2), "message": None},
            "toolhead": {"axis_minimum": [0.0, 0.0, -5.0, 0], "axis_maximum": [350.0, 350.0, 330.0, 0],
                         "homed_axes": "xyz", "position": lp, "print_time": 0.0, "estimated_print_time": 0.0},
            "configfile": {"settings": {"printer": {"kinematics": a.kinematics, "max_velocity": 500.0},
                                        **({"quad_gantry_level": {"speed": 100.0}} if a.qgl else {}),
                                        "stepper_z": {"position_max": 330.0}}},
        }.get(obj, {})

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def _send(self, code, obj=None, raw=None, headers=None):
            body = raw if raw is not None else (b"" if obj is None else json.dumps(obj).encode())
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=UTF-8" if raw is None else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            key = (self.headers.get("X-Api-Key") or "").strip()
            if (a.api_key and key != a.api_key) or (key and key != a.api_key):
                self._send(401, {"error": {"code": 401, "message": "Unauthorized"}})
                return False
            return True

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/sim/calls":              # for the tests: what reached the "printer"
                return self._send(200, calls)
            if u.path == "/sim/files":              # for the tests: every file the "printer" has
                return self._send(200, sorted(f.relative_to(SIM).as_posix() for d in (gdir, cdir)
                                              for f in d.rglob("*") if f.is_file()))
            calls.append(("GET", u.path))
            if not self._auth():
                return
            q = parse_qs(u.query, keep_blank_values=True)
            with lock:
                if u.path == "/printer/objects/query":
                    out = {}
                    for obj, fields in q.items():
                        full = status(obj)
                        want = [f for f in fields[0].split(",") if f] if fields and fields[0] else None
                        out[obj] = full if not want else {f: full.get(f) for f in want}
                    return self._send(200, {"result": {"eventtime": round(time.monotonic(), 6), "status": out}})
                if u.path == "/server/info":
                    return self._send(200, {"result": {"klippy_connected": True, "klippy_state": "ready",
                                                       "components": ["file_manager", "octoprint_compat", "history"],
                                                       "moonraker_version": "v0.11.0-sim", "api_version": [1, 5, 0]}})
                if u.path == "/server/files/roots":
                    return self._send(200, {"result": [{"name": "gcodes", "path": str(gdir), "permissions": "rw"},
                                                       {"name": "config", "path": str(SIM), "permissions": "rw"}]})
                if u.path == "/server/files/metadata":
                    rel = (q.get("filename") or [""])[0]
                    f = gdir / rel
                    if not f.is_file():
                        return self._send(404, {"error": {"code": 404, "message": f"Metadata not available for {rel}"}})
                    tp = job["tp"] if job.get("rel") == rel else load_file(f)
                    return self._send(200, {"result": {
                        "size": f.stat().st_size, "modified": f.stat().st_mtime, "slicer": "PrusaSlicer",
                        "gcode_start_byte": 0,     # Moonraker: the first G/M line - before PRINT_START
                        "gcode_end_byte": f.stat().st_size, "estimated_time": int(a.duration + a.heat),
                        "printer_model": tp.meta.get("printer_model"), "job_id": f"{int(job['t0']) % 0xFFFFFF:06X}",
                        "filename": rel}})
                if u.path == "/api/version":
                    return self._send(200, {"server": "1.5.0", "api": "0.1", "text": "OctoPrint (Moonraker v0.11.0-sim)"})
            if u.path.startswith("/server/files/gcodes/"):
                f = gdir / unquote(u.path[len("/server/files/gcodes/"):])
                if not f.is_file():
                    return self._send(404, {"error": {"code": 404, "message": "File not found"}})
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                step = 16384
                for k in range(0, len(data), step):
                    self.wfile.write(data[k:k + step])
                    time.sleep(step / (a.kbps * 1024))
                return
            self._send(404, {"error": {"code": 404, "message": "Not Found"}})

        def do_POST(self):
            u = urlsplit(self.path)
            calls.append(("POST", u.path))
            n = int(self.headers.get("Content-Length") or 0)
            if not self._auth():                    # Moonraker checks before reading the body
                return
            body = self.rfile.read(n)
            if len(body) < n:                       # the sender gave up: Moonraker stores nothing
                return
            if u.path in ("/server/files/upload", "/api/files/local"):
                parts = multipart(body, self.headers.get("Content-Type"))
                data, fname = parts.get("file", (b"", None))
                if not fname:
                    return self._send(400, {"error": {"code": 400, "message": "Bad Request"}})
                folder = parts.get("path", (b"", None))[0].decode().strip().strip("/")
                rel = (f"{folder}/" if folder else "") + fname.strip().lstrip("/")
                root_ = parts.get("root", (b"gcodes", None))[0].decode().strip() or "gcodes"
                dst = (cdir if root_ == "config" else gdir) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data + (b"\n; processed by the simulated Moonraker\n" if a.rewrite else b""))
                started = parts.get("print", (b"", None))[0].strip() == b"true" and start(rel)
                print(f"sim moonraker: upload {rel} ({len(data)} bytes){', printing' if started else ''}", flush=True)
                return self._send(201, {"item": {"path": rel, "root": "gcodes", "modified": dst.stat().st_mtime,
                                                 "size": dst.stat().st_size, "permissions": "rw"},
                                        "print_started": started, "print_queued": False, "action": "create_file"},
                                  headers={"Location": f"http://127.0.0.1:{a.printer_port}/server/files/gcodes/{rel}"})
            if u.path == "/printer/print/start":
                try:
                    rel = json.loads(body or b"{}").get("filename") or parse_qs(u.query).get("filename", [""])[0]
                except ValueError:
                    rel = parse_qs(u.query).get("filename", [""])[0]
                if not (gdir / rel).is_file() or not start(rel):
                    return self._send(400, {"error": {"code": 400, "message": "SD busy"}})
                return self._send(200, {"result": "ok"})
            self._send(404, {"error": {"code": 404, "message": "Not Found"}})

    srv = ThreadingHTTPServer(("127.0.0.1", a.printer_port), H)
    srv.daemon_threads = True
    srv.calls = calls
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"simulated Moonraker on 127.0.0.1:{a.printer_port}: {a.job.name}, {job['n']} segments in "
          f"{a.duration:.0f} s{', API key required' if a.api_key else ''}", flush=True)

    cfg = SIM / "config.toml"
    cfg.write_text(
        f'cache_dir = "{(SIM / "cache").as_posix()}"\nincoming_dir = "{(SIM / "incoming").as_posix()}"\n\n'
        f'[[printer]]\nid = "sim"\nname = "Simulated Klipper printer"\nkind = "moonraker"\n'
        f'host = "127.0.0.1:{a.printer_port}"\napi_key = "{a.api_key}"\n\n'
        f'[server]\nlisten_host = "127.0.0.1"\nport = {a.port}\n\n'
        f'[metrics]\nlisten_host = "127.0.0.1"\nlisten_port = {a.printer_port + 1}\n\n'
        f'[slicer]\nprint_host = true\n', encoding="utf-8")
    from printtwin.__main__ import main as bridge
    bridge(["--config", str(cfg)])


if __name__ == "__main__":
    main()
