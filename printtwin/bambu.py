"""Bambu Lab printers on the local network - read-only: status over MQTT, the job file over FTPS.

What the printer tells (MQTT, TLS on 8883, user "bblp" + the LAN access code, topic
device/<serial>/report): state, temperatures, speed, fans, the layer number, the percent of the
slicer's "M73 P" lines and the remaining minutes. No nozzle position and no file position on any
model - so the place in the toolpath is ESTIMATED between the job's "M73 L" / "M73 P" marks
(Tracker.estimate), and the viewer says so.

A print is new whenever the printer goes into PREPARE / RUNNING / PAUSE with another job, or
again after it ended (a reprint of the same file is a new print too). Its file comes from, best first:
  1. the slicer's hand-over (tools/prusaslicer_hook.py as post-processing script in Bambu Studio /
     OrcaSlicer): the printer's total layer count, sliced before the print started; when several
     different slices fit, the printer's own file (2.) is tried first;
  2. FTPS (implicit TLS on 990, same credentials): the .gcode.3mf in /cache/ or / under the job's
     name - not reachable when an H2 / P2S / X2D prints from internal storage ("Store sent files
     on external storage" + a USB stick / SD card makes it reachable). An earlier print's file
     under the same name is taken only when no other shows up in the first 90 s of the print.
Once the toolpath is there, its layer count must match the printer's; a file that doesn't is
dropped and the search goes on (an earlier version of the job, another plate).

Only status is read: PrintTwin sends just "pushall" and "get_version" - no print control, no cloud,
no Developer Mode needed (Bambu's own rules exempt status readers from the 2025 authorization).

Config:  kind = "bambu", host = "192.168.1.40", serial = "01P00A...", access_code = "12345678",
         optional cafile = a CA file of your own (default: Bambu's CAs in printtwin/certs/bambu.pem;
         a printer whose chain they don't cover is pinned on first contact instead),
         ftps_plain_data = true/false (plain FTPS data channel allowed; default: A1 / A1 mini / A2L).
"""
from __future__ import annotations

import html
import json
import re
import ssl
import threading
import time
import uuid
from pathlib import Path

from . import incoming, models, tlsutil
from .ftps import FtpsBusy, FtpsClient
from .mqtt import MqttClient, MqttError, SubscribeRefused
from .printer import BasePrinter, log
from .toolpath import JobFileError

STATES = {"RUNNING": "PRINTING", "PAUSE": "PAUSED", "FINISH": "FINISHED", "FAILED": "ERROR",
          "PREPARE": "BUSY", "SLICING": "BUSY", "INIT": "BUSY", "IDLE": "IDLE"}
ACTIVE = ("PREPARE", "RUNNING", "PAUSE")
CANCELLED = 50348044                  # print_error of a print stopped by the user
STALE_S = 60.0                        # no message for this long: offline, reconnect
FIRST_REPORT_S = 20.0                 # connected, but no report this long: wrong serial number?
PUSHALL_MIN_S = 300.0                 # pushall loads the printer's small broker: not more often ...
PUSHALL_RECONNECT_S = 30.0            # ... and after a reconnect not sooner than this after the last one
FIND_EARLY_S, FIND_EARLY_FOR, FIND_LATE_S = 5.0, 90.0, 300.0   # FTPS search: every 5 s for the first 90 s
                                      # of the print (RUNNING / PAUSE), then every 5 min
OLD_FILE_S = 120.0 + 14 * 3600.0      # older than the print by this much: an earlier print's (the printer's
                                      # FTP clock may be off by a time zone)
STABLE_S = 2.0                        # a found file must keep its size this long (still being written?)
HANDOVER_MAX_AGE = 12 * 3600.0        # a hand-over sliced at most this long before the print started
HANDOVER_SLACK_S = 30.0               # ... and not after it began (the hook runs when the slicing ends)
TRUST_AFTER_S = 10.0                  # RUNNING this long and a new layer: an unchanged layer count is this job's
MISMATCH_S = 5.0                      # toolpath and printer disagree on the layer count this long: wrong file
PLAIN_DATA = ("N1", "N2S", "N9")      # A1 mini, A1, A2L: their FTPS data channel may need PROT C
CERTS = Path(__file__).resolve().parent / "certs" / "bambu.pem"
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')    # stored on the printer as hex ('/' -> '2f'), '_' or left out
_M73 = re.compile(rb"^M73 P(\d+) R(\d+)", re.M)


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


def _ref(entry: tuple) -> str:
    """What identifies one version of a file on the printer: a listing entry (path, size, mtime)."""
    path, size, mtime = entry
    return f"{path}|{int(mtime or 0)}|{size}"


def _fuzzy(name: str) -> str:
    """A file name without its extension, illegal characters left out and separators collapsed:
    'Mini Hase / Osterhase' and 'Mini_Hase_Osterhase.gcode.3mf' give the same."""
    n = name.lower()
    for ext in (".gcode.3mf", ".3mf"):
        if n.endswith(ext):
            n = n[:-len(ext)]
            break
    return re.sub(r"[\s_\-]+", "_", _ILLEGAL.sub("", n)).strip("_")


class BambuPrinter(BasePrinter):
    kind = "bambu"
    poll_s = 0.25                     # the estimate moves on between the printer's ~1 Hz reports

    def __init__(self, cfg: dict, cache_dir: Path, incoming_dir: Path = incoming.INCOMING):
        super().__init__(cfg, cache_dir, incoming_dir)
        self.serial = str(cfg["serial"]).strip().upper()     # MQTT topics are case-sensitive
        self.access_code = str(cfg["access_code"]).strip()
        self.mqtt_port = int(cfg.get("mqtt_port", 8883))
        self.ftps_port = int(cfg.get("ftps_port", 990))
        ca = cfg.get("cafile") or (str(CERTS) if CERTS.is_file() else None)
        self.cafile = str(ca) if ca else None
        # with the bundled CAs, a printer whose chain they don't know (a newer model) is pinned instead;
        # a CA file from the config is meant: no way around it
        self.ca_fallback = bool(self.cafile) and not cfg.get("cafile")
        self._tls = {"mqtt": "ca" if self.cafile else "pin", "ftps": "ca" if self.cafile else "pin"}
        self.pin_dir = cache_dir / "tls"
        self.estimated = True
        mid = models.bambu_model_id(self.serial)
        self.model_name = models.BAMBU_MODELS[mid][0] if mid else None
        self.plain_data = bool(cfg.get("ftps_plain_data", mid in PLAIN_DATA))
        self._prot_c = False                       # learned: this printer's data channel needs PROT C
        if mid:
            self.printer_type = f"bambu/{mid}"
            w, d, h = models.BAMBU_MODELS[mid][2]
            self.volume = {"min": [0.0, 0.0, 0.0], "max": [float(w), float(d), float(h)]}
        self.memo = cache_dir / f"bambu-{self.serial}-job.json"   # the hand-over the running print uses
        self.files_seen = cache_dir / f"bambu-{self.serial}-files.json"   # files earlier prints used
        self.shadow: dict = {}
        self._shadow_lock = threading.Lock()
        self._mqtt_ok = False
        self._mqtt_why: str | None = None
        self._ever_got = False                     # this serial has delivered a report since PrintTwin started
        self._last_msg = 0.0
        self._last_pushall = 0.0
        self._ident: tuple | None = None
        self._seq = 0
        self._ctx: dict | None = None              # the current print: what is known about it, its file search
        self._seen_status = False
        self._total_seen: int | None = None        # the layer count at the previous poll
        self._found: dict | None = None
        self._search_at = 0.0
        self._searching = False
        self._ftps_why: str | None = None
        self._prev_refs: list[str] = self._load_prev()  # files earlier prints used (an X1 may still show one)
        self._stalled = False                      # the last connection ended without reports (P1 broker stall?)
        self._reject_req: tuple | None = None      # (job, reason) from a load that found the file wrong
        self._gs = "IDLE"                          # gcode_state at the last poll
        self._mark: tuple | None = None            # (layer, percent) and when it was first seen
        self._mark_t = 0.0
        self._paused_for = 0.0

    # ------------------------------------------------------------------ MQTT
    def start(self):
        threading.Thread(target=self._mqtt_loop, name=f"mqtt-{self.id}", daemon=True).start()
        super().start()

    def _pushall(self, client, req: str):
        client.publish(req, json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        self._last_pushall = time.time()

    def _mqtt_loop(self):
        backoff, why_last = 1.0, None
        pin_file = self.pin_dir / f"{self.serial}-mqtt.sha256"
        while True:
            client, up_at, got = None, None, False
            try:
                ca = self.cafile if self._tls["mqtt"] == "ca" else None
                client = MqttClient(self.http_netloc.split(":")[0], self.mqtt_port, "bblp", self.access_code,
                                    client_id=f"printtwin-{uuid.uuid4().hex[:8]}", keepalive=30,
                                    context=tlsutil.context(ca), server_name=self.serial,
                                    # the certificate is checked before the access code is sent: its name
                                    # (CA-verified) or its fingerprint (pinned) ...
                                    on_tls=(lambda s: self._name_ok(s, "MQTT")) if ca
                                    else (lambda s: tlsutil.pin(s, pin_file, store=False)))
                try:
                    client.connect()
                except ssl.SSLCertVerificationError as e:
                    if self._fallback("mqtt", e):
                        continue                   # at once, pinned (the finally below closes the client)
                    raise
                if ca:                             # ... and stored only once the printer took the code
                    tlsutil.remember(client.sock, pin_file)
                else:
                    tlsutil.pin(client.sock, pin_file)
                topic = f"device/{self.serial}/report"
                try:
                    client.subscribe(topic)
                except SubscribeRefused:
                    raise MqttError(f"the printer refused {topic} - check the serial number in the config") from None
                req = f"device/{self.serial}/request"
                up_at = time.time()
                owed_at = None                     # a pushall still to send (not too soon after the last)
                gap = PUSHALL_MIN_S if self._stalled else PUSHALL_RECONNECT_S
                if up_at - self._last_pushall >= gap or "gcode_state" not in (self.shadow.get("print") or {}):
                    self._pushall(client, req)
                elif self._stalled:
                    owed_at = self._last_pushall + PUSHALL_MIN_S
                else:
                    owed_at = min(self._last_pushall + PUSHALL_RECONNECT_S, up_at + FIRST_REPORT_S / 2)
                client.publish(req, json.dumps({"info": {"sequence_id": "0", "command": "get_version"}}))
                while True:
                    msg = client.recv(min(5.0, max(0.2, (owed_at or time.time() + 5) - time.time())))
                    now = time.time()
                    if msg is not None:
                        self._on_report(msg[1])
                        self._last_msg = now
                        # online once the printer's state is known (the first message may be the version)
                        if not got and "gcode_state" in (self.shadow.get("print") or {}):
                            got, self._ever_got, self._mqtt_ok, self._mqtt_why = True, True, True, None
                        self._stalled = False
                    elif not got and now - up_at > FIRST_REPORT_S:
                        raise TimeoutError(f"no report since connecting - check the serial number ({self.serial})"
                                           if not self._ever_got else
                                           "connected, but the printer sends nothing (if this lasts, restart the printer)")
                    elif got and now - self._last_msg > STALE_S:
                        raise TimeoutError(f"no report for {STALE_S:.0f} s")
                    p = self.shadow.get("print") or {}
                    if owed_at and now >= owed_at:
                        owed_at = None
                        self._pushall(client, req)
                    elif "total_layer_num" not in p and now - self._last_pushall > PUSHALL_MIN_S:
                        self._pushall(client, req)
            except Exception as e:          # printer off, wrong access code, certificate changed, ...
                self._mqtt_ok = False
                if isinstance(e, TimeoutError):
                    self._stalled = True           # connected, but nothing came: don't ask with pushall every time
                why = str(e) or repr(e)
                if isinstance(e, ssl.SSLCertVerificationError) and "mismatch" in why.lower():
                    why = f"the printer's certificate is for another serial number - check the serial number ({self.serial})"
                self._mqtt_why = f"MQTT: {why}"
                if why != why_last:
                    log(self.id, f"MQTT: {why}")
                    why_last = why
            finally:
                if client:
                    client.close()
            if got and up_at and self._last_msg - up_at > STALE_S:    # reports came for a good while
                backoff, why_last = 1.0, None
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    def _fallback(self, service: str, e: ssl.SSLCertVerificationError) -> bool:
        """The bundled CAs don't cover this printer's certificate chain (a newer model?): check it by
        its fingerprint instead - pinned on first contact, or the one a verified login noted before."""
        if not self.ca_fallback or self._tls[service] != "ca":
            return False
        self._tls[service] = "pin"
        log(self.id, f"{service.upper()}: the printer's certificate is not signed by a Bambu CA PrintTwin knows "
                     f"({getattr(e, 'verify_message', None) or e}) - checking it by its fingerprint instead "
                     f"(trust on first use). If this is a Bambu printer, please report its model.")
        return True

    def _name_ok(self, sock, service: str):
        other = tlsutil.check_name(sock, self.serial)
        if other and not getattr(self, f"_told_name_{service}", False):
            setattr(self, f"_told_name_{service}", True)
            log(self.id, f"{service}: the printer's certificate is Bambu's, named {other!r} (expected the serial "
                         f"number {self.serial}) - accepted")

    def _on_report(self, payload: bytes):
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(msg, dict):
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
        if "gcode_state" not in p:
            raise ConnectionError("waiting for the printer's status")
        now = time.time()
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
        total_n = int(_num(total) or 0) or None
        prep = _num(p.get("gcode_file_prepare_percent"))
        raw = str(p.get("subtask_name") or "")
        name = html.unescape(raw).strip()
        gfile = str(p.get("gcode_file") or "")
        ident = (name, gfile, str(p.get("task_id") or ""))
        ctx = self._ctx
        if gs == "PREPARE" and self._gs in ("RUNNING", "PAUSE"):
            self._ident = None                     # back to PREPARE: a new print (its FINISH went by unseen)
        if gs not in ACTIVE:
            self._ident = None                     # the next print is a new one, even of the same file
            if ctx and ctx["looking"]:
                ctx["looking"] = False
                with self.lock:
                    if self.job is None and (self.loading or {}).get("phase") == "search":
                        self.loading = None
        elif (name or gfile) and ident != self._ident:
            self._new_print(p, ident, name, raw, gfile, now)
            ctx = self._ctx
        if ctx and not ctx["active_since"] and (gs == "RUNNING" or (gs == "PAUSE" and ctx["restart"])):
            ctx["active_since"] = now              # the early search window counts from here
            if ctx["looking"] and not self._found:
                self._search_at = 0.0
        if ctx and gs == "RUNNING" and not ctx["running_since"]:
            ctx["running_since"] = now
        if ctx and gs == "PREPARE" and prep is not None and prep < 99:
            ctx["prep_low"] = True                 # the printer fetches this print's file now
        self._seen_status = True
        trusted = self._trusted_total(ctx, gs, total_n, _num(layer), now)
        self._total_seen = total_n
        if self._reject_req:
            job, why = self._reject_req
            self._reject_req = None
            if ctx and job.get("id") == ctx["seq"]:
                self._reject(ctx, job, why, job.get("layers"))
        if ctx:
            ctx["total"] = trusted
            ctx["percent"], ctx["remaining"] = _num(p.get("mc_percent")), _num(p.get("mc_remaining_time"))
        self._check_layers(ctx, gs, trusted, now)
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
        self._gs = gs
        self._estimate(gs, layer, _num(p.get("mc_percent")), _num(p.get("mc_remaining_time")))
        return status

    def _new_print(self, p: dict, ident: tuple, name: str, raw: str, gfile: str, now: float):
        self._ident = ident
        self._seq += 1
        start, restart = now, not self._seen_status
        if restart:                                # PrintTwin (re)started mid-print: when did it begin?
            pct, rem = _num(p.get("mc_percent")), _num(p.get("mc_remaining_time"))
            if pct and rem and 0 < pct < 100:
                start = now - rem * 60.0 * pct / (100.0 - pct)
        if self._ctx and self._ctx.get("taken") and self._ctx["taken"] not in self._prev_refs:
            self._prev_refs = (self._prev_refs + [self._ctx["taken"]])[-8:]
            try:
                self.files_seen.parent.mkdir(parents=True, exist_ok=True)
                self.files_seen.write_text(json.dumps(self._prev_refs), encoding="utf-8")
            except OSError:
                pass
        self._ctx = {"seq": self._seq, "ident": list(ident), "name": name, "raw": raw, "gcode_file": gfile,
                     "plate": self._plate(p), "t0": now, "start": start, "restart": restart,
                     "stale_total": None if restart else self._total_seen, "layer0": _num(p.get("layer_num")),
                     "looking": True, "rejected": {}, "told": False, "active_since": None, "running_since": None,
                     "prep_low": False, "searched": 0, "mismatch_since": None, "total": None, "trusted": None,
                     "replated": False,
                     "taken": None, "percent": None, "remaining": None, "m73": {}}
        self._found, self._search_at = None, 0.0
        self._drop_job("looking for the job file (slicer hand-over, FTPS)")
        log(self.id, f"new print: {name or gfile}")
        if restart:
            self._found = self._remembered(self._ctx)

    def _trusted_total(self, ctx: dict | None, gs: str, total: int | None, layer, now: float) -> int | None:
        """The printer's total layer count, once it surely is this print's: P1 / A1 send only what
        changed, so right after a new print starts it can still be the last print's."""
        if not total:
            return None
        if not ctx:
            return total
        if (ctx["stale_total"] is None or total != ctx["stale_total"] or total == ctx["trusted"]
                or (gs == "RUNNING" and ctx["running_since"] and now - ctx["running_since"] >= TRUST_AFTER_S
                    and layer and layer >= 1 and layer != ctx["layer0"])):     # unchanged, but on its way: same height
            ctx["trusted"] = total
            return total
        return None

    @staticmethod
    def _plate(p: dict) -> int | None:
        m = re.search(r"plate_(\d+)\.gcode", str(p.get("gcode_file") or ""))
        if m:
            return int(m.group(1))
        try:
            return int(p.get("plate_idx")) or None
        except (TypeError, ValueError):
            return None

    def snapshot(self) -> dict:
        snap = super().snapshot()
        ctx = self._ctx
        if snap["job"] is None and ctx and self._ident is not None:    # the print runs, its file not there yet
            snap["job"] = {"id": ctx["seq"], "name": ctx["name"] or Path(ctx["gcode_file"]).name}
        return snap

    # ------------------------------------------------------------------ the file a running print used
    def _load_prev(self) -> list[str]:
        try:
            refs = json.loads(self.files_seen.read_text(encoding="utf-8"))
            return [str(r) for r in refs][-8:] if isinstance(refs, list) else []
        except (OSError, ValueError):
            return []

    def _remember(self, job: dict):
        """Note the file the print uses; a hand-over also in the memo file, for a restart mid-print
        (the printer's own file is simply found again)."""
        ctx = self._ctx
        if not ctx or job.get("id") != ctx["seq"]:
            return
        ctx["taken"] = job.get("ref")
        if not str(job.get("download", "")).startswith("handover:"):
            return
        try:
            self.memo.parent.mkdir(parents=True, exist_ok=True)
            keep = {k: job.get(k) for k in ("display_name", "name", "download", "ref", "size", "m_timestamp", "plate")}
            self.memo.write_text(json.dumps({"ident": ctx["ident"], "start": ctx["start"], "total": job.get("layers"),
                                             "job": keep}), encoding="utf-8")
        except OSError:
            pass

    def _remembered(self, ctx: dict) -> dict | None:
        """After a restart mid-print: the hand-over this print used before - only if it is the same
        print (it began about when this one did; LAN prints of one job share name, file and task id)."""
        try:
            m = json.loads(self.memo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(m, dict) or m.get("ident") != ctx["ident"] or not isinstance(m.get("job"), dict):
            return None
        try:
            off = abs(float(m.get("start")) - ctx["start"])
        except (TypeError, ValueError):
            return None
        if off > max(900.0, 0.5 * (ctx["t0"] - ctx["start"])):
            return None                            # an earlier print of the same job
        job = m["job"]
        src = str(job.get("download") or "")
        if not src.startswith("handover:") or not Path(src[len("handover:"):]).is_file():
            return None
        log(self.id, f"the print's hand-over from before: {job.get('name')}")
        return dict(job, id=ctx["seq"], seq=ctx["seq"], layers=m.get("total"))

    def _forget(self, ref: str | None):
        try:
            m = json.loads(self.memo.read_text(encoding="utf-8"))
            if isinstance(m, dict) and isinstance(m.get("job"), dict) and m["job"].get("ref") == ref:
                self.memo.unlink()
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ wrong file
    def _toolpath_layers(self) -> int | None:
        tp = self.tp
        if tp is None or not tp.complete:
            return None
        n = tp.meta.get("total_layers")
        if not n and tp.layer_marks:
            n = max(v for _, v in tp.layer_marks)
        return int(n) if n else None

    def _check_layers(self, ctx: dict | None, gs: str, total: int | None, now: float):
        """The toolpath must have the printer's layer count - else it is another file (an earlier
        version under the same name, another plate): look again."""
        with self.lock:
            job = self.job
            n = self._toolpath_layers()
        if not (ctx and job and job.get("id") == ctx["seq"] and gs == "RUNNING" and total and n) or n == total:
            if ctx:
                ctx["mismatch_since"] = None
            return
        if ctx["mismatch_since"] is None:
            ctx["mismatch_since"] = now
            return
        if now - ctx["mismatch_since"] < MISMATCH_S:
            return
        ctx["mismatch_since"] = None
        with self.lock:
            guess = bool(self.tp and self.tp.meta.get("plate_guess"))
            plate = self.tp.meta.get("plate") if self.tp else None
        if guess and not job.get("layers") and not ctx["replated"]:     # same file, the plate with this layer count
            ctx["replated"] = True
            log(self.id, f"the job file's plate {plate} has {n} layers, the printer prints {total} - "
                         f"taking the plate that fits")
            self._drop_job("finding the plate that is printing")
            self._found = dict(job, layers=total, seq=ctx["seq"])
            ctx["looking"] = True
            return
        self._reject(ctx, job, f"it has {n} layers, the printer prints {total}", total)

    def _reject(self, ctx: dict, job: dict, why: str, total: int | None):
        """Leave this file out for this print - while the printer's layer count stays what it was
        then (a count that arrived late can make the file right again)."""
        ref = job.get("ref") or job.get("download")
        ctx["rejected"][ref] = total
        if ctx["taken"] == ref:
            ctx["taken"] = None
        self._forget(ref)
        log(self.id, f"job file {job.get('name')} is not this print's ({why}) - looking for another")
        self._drop_job(f"looking for the job file again ({why})")
        self._found, self._search_at = None, 0.0
        ctx["looking"] = True

    @staticmethod
    def _excluded(ctx: dict) -> set[str]:
        # a rejection made at another layer count is lifted only by a known, different count
        return {r for r, t in ctx["rejected"].items() if t is None or ctx["total"] is None or t == ctx["total"]}

    def _job_file_rejected(self, job: dict, key: str, e: JobFileError):
        self._reject_req = (job, str(e))          # handled by the poll thread (_fetch_status)

    # ------------------------------------------------------------------ estimate
    def _estimate(self, gs: str, layer, percent, remaining_min):
        """Where the print is between the marks the printer reports, moved on by time."""
        now = time.time()
        mark = (layer, percent)
        if mark != self._mark:
            self._mark, self._mark_t, self._paused_for = mark, now, 0.0
        paused = gs == "PAUSE"
        if paused:
            self._paused_for += self.poll_s                  # time stands still while paused
        elif gs != "RUNNING":
            return
        if percent is None:
            return
        left = 100.0 - percent
        per_percent = (remaining_min * 60.0 / left) if remaining_min and left > 0 else None
        # paused: only where the reported marks put it (a model loaded during the pause shows that)
        frac = 0.0 if paused or not per_percent else (now - self._mark_t - self._paused_for) / per_percent
        sample = None
        with self.lock:
            tr = self.tracker
            if tr and self.job and self._ctx and self.job.get("id") == self._ctx["seq"]:
                before = tr.printed
                p = tr.estimate(int(layer) if layer else None, percent, frac)
                self.last_pos_wall = now
                if p > before and tr.nozzle:
                    x, y, z = tr.nozzle
                    sample = [round(now * 1000, 1), round(x, 3), round(y, 3), round(z, 3), round(p, 3)]
        if sample:
            self.publish("pos", {"s": [sample], "key": self.tp_key})

    # ------------------------------------------------------------------ job file
    def _early(self, ctx: dict, now: float) -> bool:
        """Still in the first 90 s of the print (RUNNING / PAUSE) - or not printing yet."""
        return not ctx["active_since"] or now - ctx["active_since"] <= FIND_EARLY_FOR

    def _current_job(self, data: dict) -> dict | None:
        ctx = self._ctx
        if not ctx or not ctx["looking"]:
            return None
        now = time.time()
        f = self._found
        if f and f.get("seq") == ctx["seq"] and f.get("ref") not in self._excluded(ctx):
            ctx["looking"] = False
            job = dict(f, layers=f.get("layers") or ctx["total"])
            self._remember(job)
            return job
        if ctx["total"]:
            hit = self._handover(ctx, now)
            if hit:
                ctx["looking"] = False
                st = hit.stat()
                job = {"id": ctx["seq"], "display_name": ctx["name"] or hit.name, "name": hit.name,
                       "download": f"handover:{hit}", "ref": str(hit), "size": st.st_size,
                       "m_timestamp": int(st.st_mtime), "plate": None, "layers": ctx["total"]}
                self._remember(job)
                return job
        # P1 / A1 fetch a cloud job themselves while preparing: wait for that (or for RUNNING)
        preparing = self._gs == "PREPARE" and not (ctx["prep_low"] and (self._prep_now() or 0) >= 99)
        if not self._printing(ctx) and not ctx["active_since"]:
            preparing = True                       # X1 / H2 write the print's file only into RUNNING
        if not self._searching and now >= self._search_at and not preparing:
            self._searching = True
            w = dict(ctx, rejected=self._excluded(ctx), prev=list(self._prev_refs))
            threading.Thread(target=self._search, args=(w,), name=f"ftps-{self.id}", daemon=True).start()
        return None

    def _prep_now(self):
        with self._shadow_lock:
            return _num((self.shadow.get("print") or {}).get("gcode_file_prepare_percent"))

    def _handover(self, ctx: dict, now: float) -> Path | None:
        """The slicer's hand-over for this print: the printer's layer count, sliced before the print
        began. When several different slices fit, the printer's own file is tried first (it knows the
        plate); after that the one whose printer and time marks fit, else the newest."""
        sure_until = None
        if ctx["restart"]:                         # the start is estimated: give the estimate room ...
            spread = 0.5 * (ctx["t0"] - ctx["start"])
            not_before, not_after = ctx["start"] - HANDOVER_MAX_AGE - spread, ctx["t0"] + HANDOVER_SLACK_S
            sure_until = ctx["start"] + max(600.0, spread) + 120.0     # ... later slices may be made during it
        else:
            not_before, not_after = ctx["t0"] - HANDOVER_MAX_AGE, ctx["t0"] + HANDOVER_SLACK_S
        fits = incoming.find_bambu(ctx["total"], self.incoming_dir, not_before=not_before, not_after=not_after,
                                   exclude=self._excluded(ctx))
        if not fits:
            return None
        sure = [e for e in fits if sure_until is None or e[0] <= sure_until]
        if sure and len({sha for _, sha, _ in sure}) == 1 and len(sure) == len(fits):
            return fits[-1][2]
        if sure and len({sha for _, sha, _ in sure}) == 1 and sure_until is not None:
            return sure[-1][2]                     # one slice from before the print, the rest made during it
        if self._early(ctx, now) or not ctx["searched"]:
            return None                            # several, or only ones made during the print: FTPS first
        pick = self._tie_break(ctx, [f for _, _, f in fits])
        log(self.id, "several slicer hand-overs fit this print - showing "
                     + ("the one whose printer and time marks fit" if pick else "the newest (could not tell them apart)"))
        return pick or fits[-1][2]

    def _tie_break(self, ctx: dict, files: list[Path]) -> Path | None:
        """Among several fitting hand-overs: the one sliced for this printer model and containing the
        printer's current progress mark ("M73 P<percent> R<minutes left>"), if exactly one does."""
        def info(f: Path):
            if str(f) not in ctx["m73"]:
                try:
                    data = f.read_bytes()
                except OSError:
                    data = b""
                m = re.search(rb"^; printer_model = (.*?)\r?$", data[:300_000], re.M)
                ctx["m73"][str(f)] = (m.group(1).decode("utf-8", "replace").strip().lower() if m else None,
                                      {(int(a), int(b)) for a, b in _M73.findall(data)})
            return ctx["m73"][str(f)]
        want = f"bambu lab {self.model_name}".lower() if self.model_name else None
        cands = files
        if want:
            same = [f for f in cands if info(f)[0] == want]
            cands = same or cands
        pr = (ctx["percent"], ctx["remaining"])
        if None not in pr:
            mark = [f for f in cands if (int(pr[0]), int(pr[1])) in info(f)[1]]
            cands = mark or cands
        return cands[-1] if len(cands) < len(files) else None      # only when something told them apart

    def _search(self, w: dict):
        """Find the job's .gcode.3mf over FTPS (in the background: it can take seconds)."""
        found, failed = None, None
        try:
            found = self._locate(w)
            ctx = self._ctx
            if found and ctx and ctx["seq"] == w["seq"]:
                path, size, mtime = found
                self._found = {"id": w["seq"], "seq": w["seq"], "display_name": w["name"] or Path(path).name,
                               "name": path, "download": path, "ref": _ref(found), "size": size,
                               "m_timestamp": int(mtime or 0), "plate": w["plate"]}
                log(self.id, f"job file on the printer: {path} ({size} bytes)")
            self._ftps_why = None
        except FtpsBusy as e:
            log(self.id, f"FTPS busy ({e}) - trying again in 5 min")
            self._search_at = time.time() + FIND_LATE_S    # about the printer, not the print
            failed = f"FTPS busy ({e})"
        except Exception as e:
            failed = str(e) or repr(e)
            if failed != self._ftps_why:           # once, and again when the reason changes
                log(self.id, f"FTPS: {failed}")
                self._ftps_why = failed
        finally:
            ctx = self._ctx
            if ctx and ctx["seq"] == w["seq"]:
                if w["active_since"]:
                    ctx["searched"] += 1           # FTPS was asked (failed or not)
                if not found:
                    late = not self._early(w, time.time())
                    self._search_at = max(self._search_at, time.time() + (FIND_LATE_S if late else FIND_EARLY_S))
                    self._tell(ctx, late, failed)
            self._searching = False

    def _tell(self, ctx: dict, late: bool, failed: str | None):
        """What the viewer says while the file isn't found: why FTPS failed, or - once looking has
        gone on a while - where the file may be."""
        if failed:
            msg = f"looking for the job file - FTPS failed: {failed}"
        elif late:
            msg = ("job file not found over FTPS - if the printer prints from internal storage (H2D / H2S / H2C / "
                   "P2S / X2D), turn on 'Store sent files on external storage' and insert a USB stick or SD card, "
                   "or use the slicer hand-over (README)")
            if not ctx["told"]:
                ctx["told"] = True
                log(self.id, msg)
        else:
            return
        with self.lock:
            if self.job is None and (self.loading or {}).get("phase") == "search":
                self.loading = {"phase": "search", "message": msg}

    def _ftps_client(self) -> FtpsClient:
        return FtpsClient(self.http_netloc.split(":")[0], self.access_code, port=self.ftps_port,
                          cafile=self.cafile if self._tls["ftps"] == "ca" else None, server_name=self.serial,
                          pin_file=self.pin_dir / f"{self.serial}-ftps.sha256",
                          allow_plain_data=self.plain_data, prot_c=self._prot_c, log=lambda m: log(self.id, m))

    def _ftps(self):
        """An FTPS session (with ...): CA-verified, or pinned once the bundled CAs turned out not to
        cover this printer."""
        printer = self

        class Session:
            def __enter__(self):
                for _ in range(2):
                    self.f = printer._ftps_client()
                    try:
                        return self.f.open()
                    except ssl.SSLCertVerificationError as e:
                        if not printer._fallback("ftps", e):
                            raise
                raise ConnectionError("FTPS: no TLS mode left")

            def __exit__(self, *a):
                self.f.close()
        return Session()

    @staticmethod
    def _variants(t: str) -> tuple[str, str, str]:
        return t, _ILLEGAL.sub(lambda m: format(ord(m.group()), "02x"), t), _ILLEGAL.sub("_", t)

    @classmethod
    def _printing(cls, w: dict) -> set[str]:
        """P1 / A1 report the .3mf they print from as gcode_file: its possible names (lower case)."""
        gf = w["gcode_file"].strip()
        return {n.lower() for t in {gf, gf.rsplit("/", 1)[-1]} if t.lower().endswith(".3mf") for n in cls._variants(t)}

    @classmethod
    def _names(cls, w: dict) -> set[str]:
        """The file names the job can have on the printer (lower case): gcode_file when it is the
        .3mf itself, else the job name + .gcode.3mf / .3mf - characters a file name can't hold
        are stored as hex ('/' -> '2f') or '_'."""
        names = cls._printing(w)
        for t in {w["raw"], html.unescape(w["raw"]), w["name"]} - {""}:
            for n in cls._variants(t):
                names |= {n.lower() + ".gcode.3mf", n.lower() + ".3mf"}
        return names

    def _locate(self, w: dict) -> tuple[str, int | None, float | None] | None:
        """The job's .gcode.3mf / .3mf, once its size stops changing (the X1 is still writing it during
        the first seconds). Best first: the file a P1 / A1 says it prints, an exact name, a name with
        the separators changed; the newest of those. An earlier print's file (one PrintTwin showed
        before, or one far older than the print) is taken only after the first 90 s of the print."""
        names = self._names(w)
        fuzzy = {_fuzzy(n) for n in names} - {""}
        printing = self._printing(w)
        if not names:
            return None
        want = lambda n: n.lower() in names or (n.lower().endswith(".3mf") and _fuzzy(n) in fuzzy)  # noqa: E731
        with self._ftps() as f:
            hits = [e for folder in ("/cache/", "/") for e in f.listing(folder, want) if _ref(e) not in w["rejected"]]
            self._prot_c = f.prot_c
            if not hits:
                return None
            base = lambda e: e[0].rsplit("/", 1)[1].lower()      # noqa: E731
            path, size, mtime = best = max(hits, key=lambda e: (base(e) in printing, base(e) in names, e[2] or 0))
            old = _ref(best) in w["prev"] or (mtime and mtime < w["start"] - OLD_FILE_S)
            if old and base(best) not in printing and self._early(w, time.time()):
                return None                                  # an earlier print's file: wait for this one's
            time.sleep(STABLE_S)
            again = {e[0]: e for e in f.listing(path.rsplit("/", 1)[0] + "/", {base(best)})}
            self._prot_c = f.prot_c
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
            self._prot_c = f.prot_c

    def _not_handed_out(self, e: Exception) -> bool:
        return str(e).startswith("550")
