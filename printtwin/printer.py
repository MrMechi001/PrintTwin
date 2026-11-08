"""Printers: the shared part (job-file loader, tracker, pub/sub) and the PrusaLink adapter.

An adapter (PrusaLinkPrinter here, MoonrakerPrinter in moonraker.py) reads the printer's
status into the common shape below and says which job runs and how to download its file:

  status = {"printer": {"state": "IDLE|BUSY|PRINTING|PAUSED|FINISHED|STOPPED|ERROR|ATTENTION",
                        "temp_nozzle", "target_nozzle", "temp_bed", "target_bed",
                        "axis_x", "axis_y", "axis_z", "speed", "flow", "fan_print", "fan_hotend"},
            "job": {"id", "progress" (0-100), "time_remaining", "time_printing"} or None}
  job    = {"id", "display_name", "name", "download", "size", "m_timestamp"}
"""
from __future__ import annotations

import gzip
import json
import queue
import threading
import time
import traceback
import urllib.error
from pathlib import Path

from . import incoming, models
from .metrics import parse_datagram
from .prusalink import PrusaLink, split_host
from .toolpath import JobDecoder, load_file
from .tracker import Tracker

POLL_S = 2.0
RETRY_S = (5, 10, 20, 30)      # waits before retrying a job file the printer refused
METRICS_STALE_S = 3.0


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class _Superseded(Exception):
    """A newer job started while this one was still downloading."""


class BasePrinter:
    """Everything that doesn't depend on how the printer is asked: see the module docstring."""
    kind = "?"
    poll_s = POLL_S
    tracker_args: dict = {}                  # Tracker(window_back=..., sdpos_lag=...) for this kind

    def __init__(self, cfg: dict, cache_dir: Path, incoming_dir: Path = incoming.INCOMING):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.host = cfg["host"]
        self.http_netloc, self.http_prefix = split_host(self.host)   # for the slicer pass-through
        self.cache_dir = cache_dir
        self.incoming_dir = incoming_dir
        self.model_cfg = str(cfg.get("model", "auto"))   # printer model to show, "auto" = detect
        self.printer_type: str | None = None             # PrusaLink /api/version "printer", e.g. "1.4.0"
        self.volume: dict | None = None                  # build volume the printer reports (Klipper)
        self.estimated = False                           # progress estimated, not measured (Bambu)
        self.lock = threading.RLock()
        self.subs: set[queue.Queue] = set()

        self.online = False
        self.status: dict = {}
        self.job: dict | None = None          # current job info from /api/v1/job
        self.loading: dict | None = None
        self.tp = None
        self.tp_key: str | None = None        # viewer key: <cache key>.<load generation>
        self.tp_cache_key: str | None = None  # <m_timestamp>_<size> of the job file
        self.tp_name: str | None = None
        self.tp_gz: bytes = b""               # whole toolpath, gzip, once complete
        self.tp_info: dict | None = None      # cached info, once complete
        self.tp_lock = threading.Lock()       # Toolpath.info() bookkeeping
        self.tracker: Tracker | None = None
        self._loading_key: str | None = None
        self._gen = 0
        self._retry: tuple[str, int, float] | None = None   # (job key, failures, next try) after a failed load

        self.last_pos_wall = 0.0
        self.last_sdpos_wall = 0.0
        self.last_sdpos: int | None = None    # last file position of the current job (seeds a new tracker)
        self._offline_why: str | None = None
        self.pos_rate = 0.0
        self._rate_n = 0
        self._rate_t0 = time.time()
        self._pending: dict = {}
        self._last_state_pub = 0.0

    # ---------------------------------------------------------------- pub/sub
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=400)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            self.subs.discard(q)

    def publish(self, event: str, obj):
        data = json.dumps(obj, separators=(",", ":"))
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        with self.lock:
            st = self.status or {}
            pr = st.get("printer") or {}
            jb = st.get("job") or {}
            now = time.time()
            tr = self.tracker
            job_model = (self.tp.meta.get("printer_model") if self.tp else None)
            model, model_src = models.resolve(self.model_cfg, job_model, self.printer_type)
            return {
                "id": self.id, "name": self.name, "kind": self.kind,
                "online": self.online,
                "model": model, "model_source": model_src,
                "printer_type": self.printer_type, "printer_model": job_model,
                "state": pr.get("state"),
                "temps": {"nozzle": pr.get("temp_nozzle"), "nozzle_target": pr.get("target_nozzle"),
                          "bed": pr.get("temp_bed"), "bed_target": pr.get("target_bed")},
                "axis_x": pr.get("axis_x"), "axis_y": pr.get("axis_y"), "axis_z": pr.get("axis_z"),
                "speed": pr.get("speed"), "flow": pr.get("flow"),
                "layer": pr.get("layer"), "total_layers": pr.get("total_layers"),   # what the printer reports (Bambu)
                "fan_print": pr.get("fan_print"), "fan_hotend": pr.get("fan_hotend"),
                "progress": jb.get("progress"), "time_remaining": jb.get("time_remaining"),
                "time_printing": jb.get("time_printing"),
                "job": None if not self.job else {"id": self.job.get("id"),
                                                  "name": self.job.get("display_name")},
                "volume": self.volume,
                "toolpath_key": self.tp_key,
                "toolpath_count": self.tp.count if self.tp else 0,
                "toolpath_complete": bool(self.tp and self.tp.complete),
                "loading": self.loading,
                "ahead": bool(tr and tr.ahead()),
                "metrics": {
                    "pos_age": round(now - self.last_pos_wall, 1) if self.last_pos_wall else None,
                    "sdpos_age": round(now - self.last_sdpos_wall, 1) if self.last_sdpos_wall else None,
                    "pos_rate": round(self.pos_rate, 1),
                    "sync": tr.sync() if tr else None,
                    "estimated": self.estimated,          # no position from the printer (Bambu)
                },
                "format": (self.tp_info or {}).get("format"),
                "printed": None if not tr else round(tr.printed, 3),
                "nozzle": None if not tr or not tr.nozzle else [round(v, 3) for v in tr.nozzle],
            }

    def publish_state(self, force: bool = True):
        now = time.time()
        if force or now - self._last_state_pub > 0.5:
            self._last_state_pub = now
            self.publish("state", self.snapshot())

    # ----------------------------------------------------------------- poller
    def start(self):
        threading.Thread(target=self._poll_loop, name=f"poll-{self.id}", daemon=True).start()

    # adapter hooks ---------------------------------------------------------
    def _fetch_status(self) -> dict | None:
        """The printer's status in the common shape, None if it didn't answer properly."""
        raise NotImplementedError

    def _on_online(self):
        """Called once when the printer answers (again): read its type, volume, ..."""

    def _current_job(self, data: dict) -> dict | None:
        """The job dict when a new job is running (None: no change / no job)."""
        raise NotImplementedError

    def _stream(self, job: dict, on_chunk):
        """Download the job file, handing every piece to on_chunk(data, bytes_so_far, total)."""
        raise NotImplementedError

    def _not_handed_out(self, e: Exception) -> bool:
        """The printer refused the job file (it may hand it out later)."""
        return isinstance(e, urllib.error.HTTPError) and e.code == 404

    # ----------------------------------------------------------------- poller
    def _poll_loop(self):
        while True:
            try:
                data = self._fetch_status()
                ok = isinstance(data, dict)
                why = None if ok else "no valid status answer"
            except Exception as e:  # network hiccup, printer off, wrong key, ...
                ok, data, why = False, None, str(e) or repr(e)
            if why and why != self._offline_why:     # say why - once, and again when the reason changes
                log(self.id, "offline:" if self.online else "not reachable:", why)
            self._offline_why = why
            with self.lock:
                came_online = ok and not self.online
                if came_online:
                    log(self.id, "online")
                self.online = ok
                if ok:
                    self.status = data
            if came_online:
                try:
                    self._on_online()
                except Exception as e:
                    log(self.id, f"could not read the printer's details: {e!r}")
            if ok:
                try:
                    self._on_status(data)
                except Exception:
                    traceback.print_exc()
            self.publish_state(force=self.poll_s >= 1.0)
            time.sleep(self.poll_s)

    def _on_status(self, data: dict):
        pr = data.get("printer") or {}
        jb = data.get("job")
        state = pr.get("state")
        job = self._current_job(data)
        if job:
            with self.lock:
                self.job = job
                # the same file again (a reprint): its toolpath stays, the progress starts over
                if f"{job['m_timestamp']}_{job['size']}" == self.tp_cache_key and self.tp is not None:
                    self.tracker = Tracker(self.tp, **self.tracker_args)
            log(self.id, f"job {job['id']}: {job['display_name']}")
        if self.job:
            key = f"{self.job['m_timestamp']}_{self.job['size']}"
            r = self._retry
            waiting = r is not None and r[0] == key and time.time() < r[2]
            if key != self.tp_cache_key and key != self._loading_key and not waiting:
                with self.lock:
                    self._loading_key = key
                    self._gen += 1
                    gen = self._gen
                threading.Thread(target=self._load_job, args=(dict(self.job), key, gen),
                                 name=f"load-{self.id}", daemon=True).start()
        with self.lock:
            tr = self.tracker
            if tr is None:
                return
            if state == "FINISHED":
                tr.finish()
            elif time.time() - self.last_pos_wall > METRICS_STALE_S:     # no live nozzle position
                if time.time() - self.last_sdpos_wall < METRICS_STALE_S:
                    tr.from_sdpos_only()
                else:
                    tr.from_axis_z(pr.get("axis_z"), (jb or {}).get("progress"))

    # ----------------------------------------------------------------- loader
    def _load_job(self, job: dict, key: str, gen: int):
        """Get the job's toolpath as fast as possible, in this order:
        1. cached copy from an earlier load,  2. the copy PrusaSlicer handed over at export,
        3. download from the printer - parsed while it arrives, so the viewer shows the
           first layers after seconds instead of waiting for the whole (slow, Wi-Fi) file."""
        name = (job.get("display_name") or job.get("name") or "").lower()
        ext = ".bgcode" if name.endswith((".bgcode", ".bgc")) else ".gcode"
        path = self.cache_dir / f"{key}{ext}"
        t0 = time.time()
        try:
            source = "cache"
            if not path.exists():
                hit = incoming.find(job.get("size"), job.get("display_name"), self.incoming_dir)
                if hit:
                    incoming.copy_to(hit, path)
                    source = "slicer"
                    log(self.id, f"job file handed over by the slicer: {hit.name}")
            if path.exists():
                with self.lock:
                    self.loading = {"phase": "parse", "source": source}
                self.publish_state()
                self._install(load_file(path, job.get("plate")), key, gen, job)
                log(self.id, f"toolpath from {source} in {time.time() - t0:.1f} s")
                return

            dec = JobDecoder(plate=job.get("plate"))
            # a new job clears the old model at once; a retry of a refused download shows the
            # model only once data arrives (not a fresh empty one on every attempt)
            installed = not (self._retry and self._retry[0] == key)
            if installed:
                log(self.id, f"downloading {job['download']} ({job.get('size')} bytes), building while it arrives")
                self._install(dec.tp, key, gen, job)
            tmp = path.with_name(path.name + ".part")
            total0 = job.get("size") or 0
            with self.lock:
                self.loading = {"phase": "download", "done": 0, "total": total0, "progressive": True}
            self.publish_state()

            with open(tmp, "wb") as f:
                def on_chunk(data: bytes, n: int, total: int):
                    nonlocal installed
                    if self._gen != gen:
                        raise _Superseded()
                    f.write(data)
                    with self.tp_lock:
                        dec.feed(data)
                    if not installed:
                        self._install(dec.tp, key, gen, job)
                        installed = True
                    with self.lock:
                        self.loading = {"phase": "download", "done": n, "total": total or total0,
                                        "progressive": True}
                    self.publish_state(force=False)

                self._stream(job, on_chunk)
            with self.tp_lock:
                dec.finish()
            tmp.replace(path)
            self._install(dec.tp, key, gen, job)
            self._retry = None
            log(self.id, f"downloaded + built in {time.time() - t0:.0f} s")
        except _Superseded:
            log(self.id, f"job {key} superseded while downloading")
        except Exception as e:
            n = self._retry[1] + 1 if self._retry and self._retry[0] == key else 1
            wait = RETRY_S[min(n, len(RETRY_S)) - 1]
            if self._not_handed_out(e):
                msg = "the printer doesn't hand out the job file yet (e.g. sent via Prusa Connect) - retrying"
            else:
                msg = str(e)
            if n == 1 or not isinstance(e, urllib.error.HTTPError):
                if isinstance(e, (urllib.error.URLError, OSError)):
                    log(self.id, f"job file: {e} - retrying in {wait} s")
                else:
                    traceback.print_exc()
            with self.lock:
                if self._gen == gen:
                    self.loading = {"phase": "error", "message": msg}
                    self._loading_key = None
                    self.tp_cache_key = None          # retry after a pause (RETRY_S)
                    self._retry = (key, n, time.time() + wait)
            self.publish_state()

    def _catch_up(self, tracker):
        """Progress for a toolpath that is complete now, before live positions say more."""
        pr = (self.status or {}).get("printer") or {}
        if pr.get("state") == "FINISHED":
            tracker.finish()
        elif pr.get("state") == "PAUSED" and tracker.sdpos is not None:
            tracker.from_sdpos_only()                # paused: the file position is where it stopped
        else:                                        # capped at the file position when there is one
            tracker.from_axis_z(pr.get("axis_z"), ((self.status or {}).get("job") or {}).get("progress"))

    def _install(self, tp, key: str, gen: int, job: dict):
        """Make `tp` the current toolpath (complete, or still growing)."""
        if self._gen != gen:
            raise _Superseded()
        gz, info = b"", None
        if tp.complete:
            gz = gzip.compress(tp.to_bin(), 5)
            info = tp.info()
            info["key"] = f"{key}.{gen}"
            info["name"] = job.get("display_name")
        with self.lock:
            same = self.tp is tp
            if not same:
                tracker = Tracker(tp, **self.tracker_args)
                if self.last_sdpos is not None:     # the printer's file position caps what counts as done
                    tracker.feed_sdpos(self.last_sdpos)
                if tp.complete:
                    self._catch_up(tracker)
                self.tracker = tracker
            elif tp.complete and self.tracker:       # the download just finished
                self._catch_up(self.tracker)
            self.tp, self.tp_key, self.tp_cache_key, self.tp_name = tp, f"{key}.{gen}", key, job.get("display_name")
            self.tp_gz, self.tp_info = gz, info
            if tp.complete:
                self.loading = None
        if tp.complete:
            log(self.id, f"toolpath {tp.count} segments, {len(tp.layer_z)} layers, {len(gz) / 1e6:.1f} MB gz")
        self.publish_state()

    # --------------------------------------------------------- toolpath access
    def toolpath_info(self) -> dict | None:
        with self.lock:
            tp, info, key, name = self.tp, self.tp_info, self.tp_key, self.tp_name
        if tp is None:
            return None
        if info is not None:
            return info
        with self.tp_lock:
            info = tp.info()
        info["key"], info["name"] = key, name
        return info

    def toolpath_bin(self, start: int = 0) -> tuple[str, int, bytes, bool] | None:
        """(key, count, data, gzipped) for segments [start, count)."""
        with self.lock:
            tp, key, gz = self.tp, self.tp_key, self.tp_gz
        if tp is None:
            return None
        if start == 0 and gz:
            return key, tp.count, gz, True
        n = tp.count
        return key, n, tp.to_bin(start, n), False

    # ------------------------------------------------------ live position input
    def _feed(self, sdpos: int | None, pos: list | None, now: float | None = None) -> list:
        """File position and/or nozzle sample [t_ms, x, y, z] into the tracker; returns the
        sample with the tracker's progress appended (for the "pos" event), or []. Call with
        self.lock held."""
        now = now or time.time()
        tr = self.tracker
        if sdpos is not None:
            self.last_sdpos_wall = now
            self.last_sdpos = sdpos
            if tr:
                tr.feed_sdpos(sdpos)
        if pos is None:
            return []
        t, x, y, z = pos
        self.last_pos_wall = now
        self._rate_n += 1
        dt = now - self._rate_t0
        if dt >= 2.0:
            self.pos_rate = self._rate_n / dt
            self._rate_n, self._rate_t0 = 0, now
        if not tr:
            return []
        p = tr.feed_pos(x, y, z)
        return [round(t, 1), round(x, 3), round(y, 3), round(z, 3), round(p, 3)]


class PrusaLinkPrinter(BasePrinter):
    """Original Prusa printers with PrusaLink: HTTP status every 2 s + UDP firmware metrics."""
    kind = "prusalink"

    def __init__(self, cfg: dict, cache_dir: Path, incoming_dir: Path = incoming.INCOMING):
        super().__init__(cfg, cache_dir, incoming_dir)
        self.client = PrusaLink(self.host, cfg["api_key"])

    def _fetch_status(self) -> dict | None:
        code, data = self.client.get_json("/api/v1/status")
        return data if code == 200 and isinstance(data, dict) else None

    def _on_online(self):
        if self.printer_type is None:
            _, ver = self.client.get_json("/api/version")
            self.printer_type = (ver or {}).get("printer")
            model, src = models.resolve(self.model_cfg, None, self.printer_type)
            log(self.id, f"printer type {self.printer_type!r} -> model {model or 'unknown'} ({src})")

    def _current_job(self, data: dict) -> dict | None:
        jb = data.get("job")
        if not jb or (self.job and self.job.get("id") == jb.get("id")):
            return None
        code, job = self.client.get_json("/api/v1/job")
        if code != 200 or not job or not job.get("file"):
            return None
        self.last_sdpos = None                       # sdpos from the previous job no longer counts
        f = job["file"]
        return {"id": job["id"], "display_name": f.get("display_name") or f.get("name"),
                "download": f["refs"]["download"], "size": f.get("size"),
                "m_timestamp": f.get("m_timestamp"), "name": f.get("name")}

    def _stream(self, job: dict, on_chunk):
        self.client.stream(job["download"], on_chunk)

    # ---------------------------------------------------------------- metrics
    def on_datagram(self, payload: bytes):
        r = parse_datagram(payload)
        if not r:
            return
        _, _, samples = r
        out = []
        now = time.time()
        with self.lock:
            pend = self._pending
            for t_us, name, _, f in samples:
                if name == "sdpos":
                    self._feed(f["v"], None, now)
                elif name == "pos_x":
                    pend.clear()
                    pend["t"], pend["x"] = t_us, f["v"]
                elif name == "pos_y" and "x" in pend:
                    pend["y"] = f["v"]
                elif name == "pos_z" and "y" in pend:
                    x, y, z, t = pend["x"], pend["y"], f["v"], pend["t"]
                    pend.clear()
                    sample = self._feed(None, [t / 1000, x, y, z], now)
                    if sample:
                        out.append(sample)
        if out:
            self.publish("pos", {"s": out, "key": self.tp_key})
