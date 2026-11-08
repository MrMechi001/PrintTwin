"""Klipper printers (Voron & co.) through Moonraker: status, job file and live nozzle position.

One HTTP query per poll (Klipper refreshes its status every 250 ms, so ~4 Hz while printing):
  print_stats      state, file name, durations       -> state, job
  virtual_sdcard   file_position (byte offset of the line being read - ~1 s of motion ahead of
                   the nozzle; Klipper never alters the file)         -> tracker.feed_sdpos
  motion_report    live_position (where the nozzle is now, toolhead coordinates)
  gcode_move       position - gcode_position = the G-code offsets (SET_GCODE_OFFSET, G92), to
                   turn the live position into file coordinates     -> tracker.feed_pos
  extruder, heater_bed, fan, display_status, webhooks
The job file comes from GET /server/files/gcodes/<path>; size and modification time from
/server/files/metadata (the cache / slicer hand-over key, like PrusaLink's m_timestamp + size).
Klipper reports no printer model - only kinematics (+ quad_gantry_level for a flying gantry),
see models.py.

Config:  kind = "moonraker",  host = "voron.local" (Mainsail/Fluidd on port 80), "192.168.1.30:7125"
         or a full URL with Moonraker's route_prefix;  api_key = "..." only if Moonraker wants one.
"""
from __future__ import annotations

import os
import time
from urllib.parse import quote

from .printer import BasePrinter, log
from .prusalink import PrusaLink

QUERY = ("/printer/objects/query?webhooks=state,state_message"
         "&print_stats=state,filename,print_duration,total_duration,message"
         "&virtual_sdcard=file_path,file_position,file_size,progress,is_active"
         "&motion_report=live_position,live_velocity"
         "&gcode_move=position,gcode_position,speed_factor,extrude_factor"
         "&extruder=temperature,target&heater_bed=temperature,target&fan=speed&display_status=progress")
CONFIG_QUERY = "/printer/objects/query?toolhead=axis_minimum,axis_maximum&configfile=settings"
STATES = {"standby": "IDLE", "printing": "PRINTING", "paused": "PAUSED", "complete": "FINISHED",
          "cancelled": "STOPPED", "error": "ERROR"}
ACTIVE = ("printing", "paused")
FAST_S, IDLE_S = 0.1, 1.0          # pause between polls while printing / otherwise
CONFIG_RETRY_S = 30.0


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class MoonrakerPrinter(BasePrinter):
    kind = "moonraker"
    # Klipper reads ~1 s of motion ahead of the nozzle: with short moves that is thousands of segments
    tracker_args = {"window_back": 6000, "sdpos_lag": 60}

    def __init__(self, cfg: dict, cache_dir, incoming_dir):
        super().__init__(cfg, cache_dir, incoming_dir)
        key = str(cfg.get("api_key") or "").strip()
        if key in ("PRUSALINK-API-KEY", "MOONRAKER-API-KEY", "..."):  # the example's placeholder: no key
            key = ""
        self.client = PrusaLink(self.host, key)
        self._active = False
        self._last_state: str | None = None
        self._last_file: str | None = None
        self._seq = 0
        self._new_job: dict | None = None
        self._meta: dict = {}
        self._last_sample: tuple | None = None
        self._config_tried = 0.0

    @property
    def poll_s(self) -> float:
        return FAST_S if self._active and self.online else IDLE_S       # not 10/s at a printer that's down

    # ------------------------------------------------------------ adapter hooks
    def _on_online(self):
        if self.printer_type is None and time.time() - self._config_tried > 2.0:
            self._read_config()

    def _read_config(self):
        """Kinematics, flying gantry and travel limits - once per connection (configfile is large)."""
        self._config_tried = time.time()
        code, data = self.client.get_json(CONFIG_QUERY)
        st = ((data or {}).get("result") or {}).get("status") or {} if code == 200 else {}
        settings = (st.get("configfile") or {}).get("settings") or {}
        kin = (settings.get("printer") or {}).get("kinematics")
        if not kin:
            return                                   # Klippy not ready yet: try again later
        self.printer_type = f"klipper/{kin}" + ("+qgl" if "quad_gantry_level" in settings else "")
        th = st.get("toolhead") or {}
        lo, hi = th.get("axis_minimum") or [], th.get("axis_maximum") or []
        if len(lo) >= 3 and len(hi) >= 3:
            self.volume = {"min": [float(v) for v in lo[:3]], "max": [float(v) for v in hi[:3]]}
        from . import models
        model, src = models.resolve(self.model_cfg, None, self.printer_type)
        log(self.id, f"Klipper {kin}{' + quad gantry level' if '+qgl' in self.printer_type else ''}, "
                     f"travel {self.volume and self.volume['max']} -> model {model or 'unknown'} ({src})")

    def _fetch_status(self) -> dict | None:
        code, data = self.client.get_json(QUERY)
        res = (data or {}).get("result") if isinstance(data, dict) else None
        if code != 200 or not isinstance(res, dict):
            return self._klippy_down()
        st = res.get("status") or {}
        wh, ps = st.get("webhooks") or {}, st.get("print_stats") or {}
        vs, mr, gm = st.get("virtual_sdcard") or {}, st.get("motion_report") or {}, st.get("gcode_move") or {}
        ex, hb, fan, ds = st.get("extruder") or {}, st.get("heater_bed") or {}, st.get("fan") or {}, st.get("display_status") or {}

        kstate, pstate = wh.get("state"), ps.get("state")
        if kstate == "ready":
            state = STATES.get(pstate, "IDLE")
        else:
            state = "BUSY" if kstate == "startup" else "ERROR"
        if self.printer_type is None and kstate == "ready" and time.time() - self._config_tried > CONFIG_RETRY_S:
            self._read_config()

        # a new print: another file, or the same file started again after it ended
        fname = ps.get("filename") or ""
        fp_now = vs.get("file_position")
        if pstate in ACTIVE and fname and (self._last_state not in ACTIVE or fname != self._last_file):
            self._seq += 1
            self._new_job = self._job_for(fname, vs)
            with self.lock:
                self.last_sdpos = None
        if pstate in ACTIVE and isinstance(fp_now, int) and fp_now > 0:
            with self.lock:
                self.last_sdpos = fp_now             # seeds the tracker even before the job is set
        self._last_state, self._last_file = pstate, fname or self._last_file
        self._active = pstate in ACTIVE

        # the live nozzle in file coordinates
        nz = None
        lp, pos, gp = mr.get("live_position"), gm.get("position"), gm.get("gcode_position")
        if isinstance(lp, list) and len(lp) >= 3 and isinstance(pos, list) and isinstance(gp, list) \
                and len(pos) >= 3 and len(gp) >= 3:
            nz = [lp[i] - (pos[i] - gp[i]) for i in range(3)]

        progress, remaining = self._progress(vs, ps, ds)
        sf, ef, fs = _num(gm.get("speed_factor")), _num(gm.get("extrude_factor")), _num(fan.get("speed"))
        status = {
            "printer": {"state": state,
                        "temp_nozzle": _num(ex.get("temperature")), "target_nozzle": _num(ex.get("target")),
                        "temp_bed": _num(hb.get("temperature")), "target_bed": _num(hb.get("target")),
                        "axis_x": nz and round(nz[0], 2), "axis_y": nz and round(nz[1], 2),
                        "axis_z": nz and round(nz[2], 2),
                        "speed": None if sf is None else round(sf * 100), "flow": None if ef is None else round(ef * 100),
                        "fan_print": None if fs is None else round(fs * 100), "fan_hotend": None},
            "job": None if not self._last_file or pstate == "standby" else {
                "id": self._seq, "progress": progress, "time_remaining": remaining,
                "time_printing": _num(ps.get("print_duration"))},
        }

        # file position + live nozzle into the tracker (only for the job being printed)
        et = _num(res.get("eventtime"))
        fp = vs.get("file_position") if vs.get("is_active") or pstate in ACTIVE else None
        sample = None
        with self.lock:
            if self.job and fname and self.job.get("name") == fname:
                if isinstance(fp, int) and fp > 0:
                    self._feed(fp, None)
                if nz and et is not None and pstate in ACTIVE:
                    key = (round(nz[0], 3), round(nz[1], 3), round(nz[2], 3))
                    if key != self._last_sample:         # live_position changes at most every 250 ms
                        self._last_sample = key
                        sample = self._feed(None, [et * 1000, *nz])
                    else:
                        self.last_pos_wall = time.time()  # same place (heating, pause): still live
        if sample:
            self.publish("pos", {"s": [sample], "key": self.tp_key})
        return status

    def _klippy_down(self) -> dict | None:
        """Moonraker answers but Klipper doesn't (starting, shut down, error): online, not ready."""
        code, info = self.client.get_json("/server/info")
        res = (info or {}).get("result") if isinstance(info, dict) else None
        if code == 401:
            raise PermissionError("Moonraker answered 401 - set api_key for this printer, or add this PC's "
                                  "address to [authorization] trusted_clients in moonraker.conf")
        if code != 200 or not isinstance(res, dict):
            self._active = False
            return None                              # not Moonraker / not reachable
        self._active = False
        ks = res.get("klippy_state")
        return {"printer": {"state": "BUSY" if ks == "startup" else "ERROR"}, "job": None}

    def _job_for(self, fname: str, vs: dict) -> dict:
        # Klipper finds a file name case-insensitively but reports the spelling it was asked for;
        # Moonraker needs the real one - the tail of virtual_sdcard.file_path
        rel, path = fname, vs.get("file_path")
        if isinstance(path, str) and len(path) > len(fname) and path[-len(fname) - 1] in "/\\" \
                and path[-len(fname):].lower() == fname.lower():
            rel = path[-len(fname):]
        code, data = self.client.get_json("/server/files/metadata?filename=" + quote(rel, safe="/"))
        meta = (data or {}).get("result") if code == 200 and isinstance(data, dict) else None
        self._meta = meta if isinstance(meta, dict) else {}
        size = _num(self._meta.get("size")) or _num(vs.get("file_size"))
        modified = _num(self._meta.get("modified"))
        return {"id": self._seq, "display_name": os.path.basename(rel), "name": fname,
                "download": "/server/files/gcodes/" + quote(rel, safe="/"),
                "size": int(size) if size else None, "m_timestamp": int(modified) if modified else 0}

    def _progress(self, vs: dict, ps: dict, ds: dict) -> tuple[float | None, int | None]:
        """Progress in % by file position between the first and last G-code byte (as Mainsail does),
        remaining time from the slicer's estimate."""
        m, fp = self._meta, _num(vs.get("file_position"))
        p = None
        a, b = _num(m.get("gcode_start_byte")), _num(m.get("gcode_end_byte"))
        if fp is not None and a is not None and b and b > a:
            p = min(1.0, max(0.0, (fp - a) / (b - a)))
        elif _num(vs.get("progress")) is not None:
            p = vs["progress"]
        if not p and _num(ds.get("progress")):
            p = ds["progress"]
        done = _num(ps.get("print_duration"))
        if not done:
            p = 0.0 if p is not None else p          # PRINT_START (heating, homing): nothing printed yet
        est = _num(m.get("estimated_time"))
        remaining = None
        if est and done is not None:
            remaining = max(0, int(est - done))
        elif p and p > 0.01 and done:
            remaining = int(done / p - done)
        return (None if p is None else round(100 * p, 1)), remaining

    def _current_job(self, data: dict) -> dict | None:
        job, self._new_job = self._new_job, None
        return job

    def _stream(self, job: dict, on_chunk):
        self.client.stream(job["download"], on_chunk)
