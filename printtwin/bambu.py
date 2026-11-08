"""Bambu Lab printers on the local network - read-only: status over MQTT, the job file over FTPS.

What the printer tells (MQTT, TLS on 8883, user "bblp" + the LAN access code, topic
device/<serial>/report): state, temperatures, speed, fans, the layer number, the percent of the
slicer's "M73 P" lines and the remaining minutes. No nozzle position and no file position on any
model - so the place in the toolpath is ESTIMATED between the job's "M73 L" / "M73 P" marks
(Tracker.estimate), and the viewer says so.

Where the job file comes from, best first:
  1. the slicer's hand-over (tools/prusaslicer_hook.py as post-processing script in Bambu Studio /
     OrcaSlicer) - matched by the total layer count, the name and its age;
  2. FTPS (implicit TLS on 990, same credentials): the .gcode.3mf in /cache/ or / - not reachable
     when an H2 / P2S / X2D prints from internal storage ("Store sent files on external storage" +
     a USB stick / SD card makes it reachable).

Only status is read: PrintTwin sends just "pushall" and "get_version" - no print control, no cloud,
no Developer Mode needed (Bambu's own rules exempt status readers from the 2025 authorization).

Config:  kind = "bambu", host = "192.168.1.40", serial = "01P00A...", access_code = "12345678",
         optional cafile = Bambu's CA (else the printer's certificate is pinned on first contact).
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import incoming, models, tlsutil
from .ftps import FtpsBusy, FtpsClient
from .mqtt import MqttClient
from .printer import BasePrinter, log

STATES = {"RUNNING": "PRINTING", "PAUSE": "PAUSED", "FINISH": "FINISHED", "FAILED": "ERROR",
          "PREPARE": "BUSY", "SLICING": "BUSY", "INIT": "BUSY", "IDLE": "IDLE"}
ACTIVE = ("PREPARE", "RUNNING", "PAUSE")
CANCELLED = 50348044                  # print_error of a print stopped by the user
STALE_S = 60.0                        # no message for this long: offline, reconnect
PUSHALL_MIN_S = 300.0                 # pushall loads the printer's small broker: not more often
FIND_EARLY_S, FIND_EARLY_FOR, FIND_LATE_S = 5.0, 90.0, 300.0   # FTPS search: every 5 s at first, then every 5 min
CERTS = Path(__file__).resolve().parent / "certs" / "bambu.pem"


def _merge(dst: dict, src: dict):
    """P1 / A1 send only what changed: fold every report into the full state."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class BambuPrinter(BasePrinter):
    kind = "bambu"
    poll_s = 0.25                     # the estimate moves on between the printer's ~1 Hz reports

    def __init__(self, cfg: dict, cache_dir: Path, incoming_dir: Path = incoming.INCOMING):
        super().__init__(cfg, cache_dir, incoming_dir)
        self.serial = str(cfg["serial"]).strip()
        self.access_code = str(cfg["access_code"]).strip()
        self.mqtt_port = int(cfg.get("mqtt_port", 8883))
        self.ftps_port = int(cfg.get("ftps_port", 990))
        ca = cfg.get("cafile") or (str(CERTS) if CERTS.is_file() else None)
        self.cafile = str(ca) if ca else None
        self.pin_dir = cache_dir / "tls"
        self.estimated = True
        mid = models.bambu_model_id(self.serial)
        if mid:
            self.printer_type = f"bambu/{mid}"
            w, d, h = models.BAMBU_MODELS[mid][2]
            self.volume = {"min": [0.0, 0.0, 0.0], "max": [float(w), float(d), float(h)]}
        self.shadow: dict = {}
        self._shadow_lock = threading.Lock()
        self._mqtt_ok = False
        self._mqtt_why: str | None = None
        self._last_msg = 0.0
        self._last_pushall = 0.0
        self._ident: tuple | None = None
        self._seq = 0
        self._wanted: dict | None = None          # a running job whose file is still being looked for
        self._found: dict | None = None
        self._search_at = 0.0
        self._searching = False
        self._mark: tuple | None = None           # (layer, percent) and when it was first seen
        self._mark_t = 0.0
        self._paused_for = 0.0

    # ------------------------------------------------------------------ MQTT
    def start(self):
        threading.Thread(target=self._mqtt_loop, name=f"mqtt-{self.id}", daemon=True).start()
        super().start()

    def _mqtt_loop(self):
        backoff, why_last = 1.0, None
        while True:
            client = None
            try:
                client = MqttClient(self.http_netloc.split(":")[0], self.mqtt_port, "bblp", self.access_code,
                                    client_id=f"printtwin-{uuid.uuid4().hex[:8]}", keepalive=30,
                                    context=tlsutil.context(self.cafile),
                                    server_name=self.serial if self.cafile else None)
                client.connect()
                if not self.cafile:
                    tlsutil.pin(client.sock, self.pin_dir / f"{self.serial}-mqtt.sha256")
                client.subscribe(f"device/{self.serial}/report")
                req = f"device/{self.serial}/request"
                client.publish(req, json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
                client.publish(req, json.dumps({"info": {"sequence_id": "0", "command": "get_version"}}))
                self._last_pushall = self._last_msg = time.time()
                self._mqtt_ok, backoff, why_last, self._mqtt_why = True, 1.0, None, None
                while True:
                    msg = client.recv(5.0)
                    now = time.time()
                    if msg is None:
                        if now - self._last_msg > STALE_S:
                            raise TimeoutError(f"no report for {STALE_S:.0f} s")
                        continue
                    self._on_report(msg[1])
                    self._last_msg = now
                    p = self.shadow.get("print") or {}
                    if "total_layer_num" not in p and now - self._last_pushall > PUSHALL_MIN_S:
                        client.publish(req, json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
                        self._last_pushall = now
            except Exception as e:          # printer off, wrong access code, certificate changed, ...
                self._mqtt_ok = False
                why = str(e) or repr(e)
                self._mqtt_why = f"MQTT: {why}"
                if why != why_last:
                    log(self.id, f"MQTT: {why}")
                    why_last = why
            finally:
                if client:
                    client.close()
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    def _on_report(self, payload: bytes):
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return
        with self._shadow_lock:
            if isinstance(msg.get("print"), dict):
                _merge(self.shadow.setdefault("print", {}), msg["print"])
            if isinstance(msg.get("info"), dict) and msg["info"].get("command") == "get_version":
                self.shadow["info"] = msg["info"]

    # ------------------------------------------------------------------ status
    def _fetch_status(self) -> dict | None:
        if not self._mqtt_ok or time.time() - self._last_msg > STALE_S:
            raise ConnectionError(self._mqtt_why or "waiting for the printer's MQTT reports")
        with self._shadow_lock:
            p = json.loads(json.dumps(self.shadow.get("print") or {}))
        gs = str(p.get("gcode_state") or "IDLE").upper()
        state = STATES.get(gs, "BUSY")
        if gs == "FAILED" and p.get("print_error") == CANCELLED:
            state = "STOPPED"
        nozzle, target = _num(p.get("nozzle_temper")), _num(p.get("nozzle_target_temper"))
        ext = ((p.get("device") or {}).get("extruder") or {})
        if isinstance(ext.get("info"), list) and ext["info"]:        # H2D / H2C: two nozzles
            active = (int(ext.get("state") or 0) >> 4) & 0xF
            info = next((i for i in ext["info"] if i.get("id") == active), ext["info"][0])
            t = int(info.get("temp") or 0)
            nozzle, target = float(t & 0xFFFF), float(t >> 16)
        fan = _num(p.get("cooling_fan_speed"))
        layer, total = p.get("layer_num"), p.get("total_layer_num")
        name = html.unescape(str(p.get("subtask_name") or "")).strip()
        gfile = str(p.get("gcode_file") or "")
        ident = (name, gfile, str(p.get("task_id") or ""))
        if gs in ACTIVE and (name or gfile) and ident != self._ident:
            self._ident = ident
            self._seq += 1
            self._wanted = {"name": name, "gcode_file": gfile, "plate": self._plate(p), "seq": self._seq}
            self._found, self._search_at = None, 0.0
            log(self.id, f"new print: {name or gfile}")
        status = {
            "printer": {"state": state, "temp_nozzle": nozzle, "target_nozzle": target,
                        "temp_bed": _num(p.get("bed_temper")), "target_bed": _num(p.get("bed_target_temper")),
                        "axis_x": None, "axis_y": None, "axis_z": None,
                        "speed": _num(p.get("spd_mag")), "flow": None,
                        "fan_print": None if fan is None else min(100, round(fan / 15 * 10 + 0.49) * 10),
                        "fan_hotend": None, "layer": layer, "total_layers": total},
            "job": None if gs == "IDLE" or not (name or gfile) else {
                "id": self._seq, "progress": _num(p.get("mc_percent")),
                "time_remaining": None if _num(p.get("mc_remaining_time")) is None else int(p["mc_remaining_time"]) * 60,
                "time_printing": None},
        }
        self._estimate(gs, layer, _num(p.get("mc_percent")), _num(p.get("mc_remaining_time")))
        return status

    @staticmethod
    def _plate(p: dict) -> int | None:
        m = re.search(r"plate_(\d+)\.gcode", str(p.get("gcode_file") or ""))
        if m:
            return int(m.group(1))
        try:
            return int(p.get("plate_idx")) or None
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ estimate
    def _estimate(self, gs: str, layer, percent, remaining_min):
        """Where the print is between the marks the printer reports, moved on by time."""
        now = time.time()
        mark = (layer, percent)
        if mark != self._mark:
            self._mark, self._mark_t, self._paused_for = mark, now, 0.0
        if gs == "PAUSE":
            self._paused_for += self.poll_s                  # time stands still while paused
            return
        if gs != "RUNNING" or percent is None:
            return
        left = 100.0 - percent
        per_percent = (remaining_min * 60.0 / left) if remaining_min and left > 0 else None
        frac = (now - self._mark_t - self._paused_for) / per_percent if per_percent else 0.0
        sample = None
        with self.lock:
            tr = self.tracker
            if tr and self.job and self.job.get("id") == self._seq:
                before = tr.printed
                p = tr.estimate(int(layer) if layer else None, percent, frac)
                self.last_pos_wall = now
                if p > before and tr.nozzle:
                    x, y, z = tr.nozzle
                    sample = [round(now * 1000, 1), round(x, 3), round(y, 3), round(z, 3), round(p, 3)]
        if sample:
            self.publish("pos", {"s": [sample], "key": self.tp_key})

    # ------------------------------------------------------------------ job file
    def _current_job(self, data: dict) -> dict | None:
        w = self._wanted
        if not w:
            return None
        if self._found and self._found.get("seq") == w["seq"]:
            job, self._wanted = self._found, None
            return job
        total = (self.shadow.get("print") or {}).get("total_layer_num")
        hit = incoming.find_bambu(int(total) if total else None, w["name"], self.incoming_dir)
        if hit:                                              # the slicer handed the job over
            self._wanted = None
            st = hit.stat()
            return {"id": w["seq"], "display_name": w["name"] or hit.name, "name": hit.name,
                    "download": f"handover:{hit}", "size": st.st_size, "m_timestamp": int(st.st_mtime),
                    "plate": None}
        now = time.time()
        if not self._searching and now >= self._search_at:
            self._searching = True
            threading.Thread(target=self._search, args=(dict(w),), name=f"ftps-{self.id}", daemon=True).start()
        return None

    def _search(self, w: dict):
        """Find the job's .gcode.3mf over FTPS (in the background: it can take seconds)."""
        started = w.setdefault("t0", time.time())
        try:
            found = self._locate(w)
            if found:
                self._found = {"id": w["seq"], "seq": w["seq"], "display_name": w["name"] or Path(found[0]).name,
                               "name": found[0], "download": found[0], "size": found[1],
                               "m_timestamp": int(found[2] or 0), "plate": w["plate"]}
                log(self.id, f"job file on the printer: {found[0]} ({found[1]} bytes)")
        except FtpsBusy as e:
            log(self.id, f"FTPS busy ({e}) - trying again in 5 min")
            self._search_at = time.time() + FIND_LATE_S
        except Exception as e:
            log(self.id, f"FTPS: {e}")
        finally:
            if not self._found:
                late = time.time() - (self._wanted or w).get("t0", started) > FIND_EARLY_FOR
                self._search_at = max(self._search_at, time.time() + (FIND_LATE_S if late else FIND_EARLY_S))
                if late and not getattr(self, "_told_storage", False):
                    self._told_storage = True
                    log(self.id, "job file not found over FTPS - if the printer prints from internal storage "
                                 "(H2D / H2S / H2C / P2S / X2D), turn on 'Store sent files on external storage' and "
                                 "insert a USB stick or SD card, or use the slicer hand-over")
            self._searching = False

    def _ftps(self) -> FtpsClient:
        return FtpsClient(self.http_netloc.split(":")[0], self.access_code, port=self.ftps_port,
                          cafile=self.cafile, server_name=self.serial if self.cafile else None,
                          pin_file=self.pin_dir / f"{self.serial}-ftps.sha256")

    def _locate(self, w: dict) -> tuple[str, int | None, float | None] | None:
        """The newest .gcode.3mf / .3mf whose name fits the job (gcode_file, else subtask_name), once
        its size stops changing (the X1 is still writing it during the first seconds)."""
        names = set()
        gf = Path(w["gcode_file"]).name
        if gf.lower().endswith(".3mf"):
            names.add(gf.lower())
        if w["name"]:
            for n in (w["name"], re.sub(r'[\\/:*?"<>|]', "_", w["name"])):
                names |= {n.lower() + ".gcode.3mf", n.lower() + ".3mf"}
        if not names:
            return None
        with self._ftps() as f:
            hits = [e for folder in ("/cache/", "/") for e in f.listing(folder) if Path(e[0]).name.lower() in names]
            if not hits:
                return None
            path, size, mtime = max(hits, key=lambda e: e[2] or 0)
            time.sleep(2.0)
            again = {e[0]: e for e in f.listing(str(Path(path).parent).replace("\\", "/") + "/")}
            if again.get(path, (None, None))[1] != size:
                return None                                  # still being written: next round
        return path, size, mtime

    def _stream(self, job: dict, on_chunk):
        src = job["download"]
        if src.startswith("handover:"):
            data = Path(src[len("handover:"):]).read_bytes()
            for k in range(0, len(data), 256 * 1024):
                on_chunk(data[k:k + 256 * 1024], min(len(data), k + 256 * 1024), len(data))
            return
        with self._ftps() as f:
            f.retr(src, on_chunk, total=job.get("size") or 0)

    def _not_handed_out(self, e: Exception) -> bool:
        return str(e).startswith("550")
