"""Printers: the shared part (job-file loader, tracker, pub/sub) and the PrusaLink adapter.

An adapter (PrusaLinkPrinter here, MoonrakerPrinter in moonraker.py) reads the printer's
status into the common shape below and says which job runs and how to download its file:

  status = {"printer": {"state": "IDLE|BUSY|PRINTING|PAUSED|FINISHED|STOPPED|ERROR|ATTENTION",
                        "temp_nozzle", "target_nozzle", "temp_bed", "target_bed",
                        "axis_x", "axis_y", "axis_z", "speed", "flow", "fan_print", "fan_hotend"},
            "job": {"id", "progress" (0-100), "time_remaining", "time_printing"} or None}
  job    = {"id", "display_name", "name", "download", "size", "m_timestamp"}
           (+ optional "plate" / "layers": which plate of a .gcode.3mf, the printer's layer count;
           "download" None: the printer can't hand the file out, e.g. a job on an MK3's SD card)
"""
from __future__ import annotations

import gzip
import json
import queue
import re
import threading
import time
import traceback
import urllib.error
from bisect import bisect_left, bisect_right
from collections import deque
from pathlib import Path

from . import incoming, models
from .config import filled
from .metrics import parse_datagram
from .prusalink import PrusaLink, split_host, url_path
from .toolpath import JobDecoder, JobFileError, load_file
from .tracker import Z_TOL, Tracker

POLL_S = 2.0
RETRY_S = (5, 10, 20, 30)      # waits before retrying a job file the printer refused
NO_DOWNLOAD_RETRY_S = 30.0     # a job the printer can't hand out: look for a hand-over again after this
METRICS_STALE_S = 3.0


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class _Superseded(Exception):
    """A newer job started while this one was still downloading."""


class NotDownloadable(Exception):
    """The printer can't hand out this job's file (a job on an MK3's SD card): only the slicer's
    hand-over can give it to PrintTwin."""


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
        self.estimated = False                           # progress estimated, not measured (Bambu, MK3)
        self.note: str | None = None                     # why the printer can't be read, for the viewer
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
                "online": self.online, "note": None if self.online else self.note,
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

    def _job_file_rejected(self, job: dict, key: str, e: JobFileError):
        """The job file can't be shown (wrong plate, too large): not tried again. An adapter that
        can look for another file (Bambu) does that here."""

    def _not_downloadable(self, job: dict) -> str | None:
        """Why the printer can't hand out this job's file (None: it can)."""
        return None

    def _no_position(self, tracker: Tracker, pr: dict, jb: dict | None):
        """No live position, no file position: place the print from what the status says."""
        tracker.from_axis_z(pr.get("axis_z"), (jb or {}).get("progress"))

    def _drop_job(self, message: str | None = None):
        """Forget the current job and its model (a new print whose file isn't there yet)."""
        with self.lock:
            self._gen += 1                            # a load still running for the old job is superseded
            self.job = self.tp = self.tracker = None
            self.tp_key = self.tp_cache_key = self.tp_name = self._loading_key = None
            self.tp_gz, self.tp_info, self._retry = b"", None, None
            self.last_sdpos = None
            self.loading = {"phase": "search", "message": message} if message else None

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
                    self._no_position(tr, pr, jb)

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
                    if self._gen != gen:
                        raise _Superseded()
                    self.loading = {"phase": "parse", "source": source}
                self.publish_state()
                self._install(load_file(path, job.get("plate"), job.get("layers")), key, gen, job)
                log(self.id, f"toolpath from {source} in {time.time() - t0:.1f} s")
                return
            why = self._not_downloadable(job)
            if why:
                raise NotDownloadable(why)

            dec = JobDecoder(plate=job.get("plate"), layers=job.get("layers"))
            # a new job clears the old model at once; a retry of a refused download shows the
            # model only once data arrives (not a fresh empty one on every attempt)
            installed = not (self._retry and self._retry[0] == key)
            if installed:
                log(self.id, f"downloading {job['download']} ({job.get('size')} bytes), building while it arrives")
                self._install(dec.tp, key, gen, job)
            tmp = path.with_name(path.name + ".part")
            total0 = job.get("size") or 0
            with self.lock:
                if self._gen != gen:
                    raise _Superseded()
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
                        if self._gen != gen:
                            raise _Superseded()
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
        except NotDownloadable as e:                 # only a hand-over can bring it: look again now and then
            first = not (self._retry and self._retry[0] == key)
            if first:
                log(self.id, f"job file {job.get('display_name') or key}: {e}")
            with self.lock:
                if self._gen == gen:
                    # not the last job's model: this print's file isn't there
                    self.tp = self.tracker = None
                    self.tp_key = self.tp_cache_key = self.tp_name = self._loading_key = None
                    self.tp_gz, self.tp_info = b"", None
                    self.loading = {"phase": "search", "message": str(e)}
                    self._retry = (key, (self._retry[1] + 1) if not first else 1, time.time() + NO_DOWNLOAD_RETRY_S)
            self.publish_state()
        except JobFileError as e:                    # trying the same file again won't help
            log(self.id, f"job file {job.get('display_name') or key}: {e}")
            with self.lock:
                if self._gen == gen:
                    self.loading = {"phase": "error", "message": str(e)}
                    self._loading_key = None
                    self.tp_cache_key = None
                    self._retry = (key, 1, float("inf"))
            self._job_file_rejected(job, key, e)
            self.publish_state()
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
            self._no_position(tracker, pr, (self.status or {}).get("job"))

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
            if self._gen != gen:                     # a newer job (or none) since the check above
                raise _Superseded()
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
            guess = {"several": f" (plate {tp.meta.get('plate')} of several - a guess)",
                     "renumbered": f" (the only plate, numbered {tp.meta.get('plate')} - not the one the printer named)"
                     }.get(tp.meta.get("plate_guess"), "")
            log(self.id, f"toolpath {tp.count} segments, {len(tp.layer_z)} layers, {len(gz) / 1e6:.1f} MB gz{guess}")
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


# PrusaLink answers that say why the status can't be read (shown in the viewer, logged once)
NOTES = {401: "PrusaLink refused the login - check api_key (or user and password) in the config",
         403: "PrusaLink refused the API key - check api_key in the config",
         503: "PrusaLink is up, the printer is not connected"}   # Pi: the serial link to the printer is down
SD_JOB = ("job on the SD card - PrintTwin can't download it; the slicer hand-over hook or PrintTwin "
          "as print host gives it the file")
TYPE_RETRY_S = 30.0        # PrusaLink on a Pi that doesn't know its printer yet: ask again after this
NOID_CHECK_S = 10.0        # a job without id: compare its file this often
EST_TICK_S = 0.5           # the estimate moves on between the 2 s status polls
EST_Z_SAMPLES = 3          # Z must hold for this many polls before the layers below count as done
EST_VOTE_LEAD = 2          # normal <-> stealth marks: the other side needs this many votes more
PI_Z_TOL = 0.011           # PrusaLink on a Pi reports the G-code's own Z (two decimals, no bed mesh)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _same_file(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("name", "size", "m_timestamp"))


def _window(marks: list, pct: float, n: int) -> tuple[int, int]:
    """The segments between the last mark the percent passed and the next one."""
    lo, hi = 0, n
    for seg, v in marks:
        if v <= pct:
            lo = seg
        else:
            hi = seg
            break
    return lo, max(lo, hi)


def _nearest(lz: list, z: float) -> int:
    """Index of the layer height nearest to z (lz sorted, not empty)."""
    i = bisect_left(lz, z)
    if i >= len(lz) or (i > 0 and z - lz[i - 1] <= lz[i] - z):
        i -= 1
    return i


class _Estimate:
    """What the estimate of one print knows: the last Z samples, the percent and how long the
    printer has been PRINTING since that percent first showed."""

    def __init__(self, job: dict | None):
        self.job = job
        self.z: deque = deque(maxlen=EST_Z_SAMPLES)
        self.pct: float | None = None
        self.remaining: float | None = None
        self.state: str | None = None
        self.run = 0.0
        self.t = time.time()
        self.votes = {"P": 0, "Q": 0}             # which marks the percent follows: normal (P) or stealth (Q)
        self.use = "P"                             # ... and the side in use (changes on a clear lead only)
        self.vote = False                          # a new Z sample to vote with

    def advance(self, now: float):
        if self.state == "PRINTING":              # time stands still while paused, heating, ...
            self.run += max(0.0, now - self.t)
        self.t = now


class PrusaLinkPrinter(BasePrinter):
    """Original Prusa printers with PrusaLink: HTTP status every 2 s + UDP firmware metrics.

    PrusaLink on a Raspberry Pi (MK3 / MK3S / MK3S+, MK2.5/S) has no metrics and reports no X/Y
    while printing - only Z every 2 s (the planned Z, z-hops included), the whole M73 percent and the
    time left. Without metrics the place in the toolpath is estimated (_estimate_now): the layers
    below the Z of the last polls are done (a z-hop doesn't count), the percent says between which
    M73 marks of the job the print is (never past the next one), and the time since it changed says
    how far."""
    kind = "prusalink"

    def __init__(self, cfg: dict, cache_dir: Path, incoming_dir: Path = incoming.INCOMING):
        super().__init__(cfg, cache_dir, incoming_dir)
        # the examples' placeholders ("PRUSALINK-API-KEY", "...") are no key, no user, no password
        key = str(cfg.get("api_key")).strip() if filled(cfg.get("api_key")) else ""
        user = str(cfg.get("user")).strip() if filled(cfg.get("user")) else ""
        password = str(cfg.get("password")) if filled(cfg.get("password")) else ""
        if not key and not (user and password):
            raise ValueError("no api_key (or user and password) in its [[printer]] block")
        self.client = PrusaLink(self.host, key, user=user or None, password=password or None)
        self.pi = False                    # PrusaLink on a Raspberry Pi (no "printer" in /api/version)
        self.metrics_seen = False          # a file or nozzle position arrived over UDP
        self.estimated = True              # ... until then the position is estimated
        self._type_pending = True
        self._type_logged = False
        self._type_retry_at = 0.0
        self._est: _Estimate | None = None
        self._prev_state: str | None = None
        self._prev_progress: float | None = None
        self._noid_at = 0.0

    def start(self):
        super().start()
        threading.Thread(target=self._tick_loop, name=f"estimate-{self.id}", daemon=True).start()

    def snapshot(self) -> dict:
        snap = super().snapshot()
        if self.estimated and not self.pi:
            snap["metrics"]["hint"] = "turn on the printer's metrics for the live nozzle"
        return snap

    # ------------------------------------------------------------ adapter hooks
    def _fetch_status(self) -> dict | None:
        try:
            code, data = self.client.get_json("/api/v1/status")
        except Exception:
            self.note = None                          # not even PrusaLink answers
            raise
        if code in NOTES:
            self.note = NOTES[code]
            raise ConnectionError(self.note)
        self.note = None
        if code != 200 or not isinstance(data, dict):
            return None
        if self._type_pending and self.online and time.time() >= self._type_retry_at:
            try:
                self._read_version()
            except Exception:
                pass
        return data

    def _on_online(self):
        if self.printer_type is None:
            self._read_version()

    def _read_version(self):
        """The printer model: Buddy firmware has "printer" in /api/version, PrusaLink on a Pi only
        "original" ("PrusaLink I3MK3S"; "Unknown printer type" until it knows - asked again then)."""
        self._type_retry_at = time.time() + TYPE_RETRY_S
        code, ver = self.client.get_json("/api/version")
        if code != 200 or not isinstance(ver, dict):
            return
        pi = "printer" not in ver and ("original" in ver or bool(re.match(r"PrusaLink \d", str(ver.get("text") or ""))))
        t = ver.get("printer") or models.type_from_original(ver.get("original"))
        self._type_pending = pi and not t
        if pi and not self.pi:
            log(self.id, f"{ver.get('text') or 'PrusaLink'} on a Raspberry Pi: no metrics there - "
                         f"the position is estimated from Z and progress")
        self.pi = pi
        if t != self.printer_type or not self._type_logged:
            self._type_logged = True
            self.printer_type = t
            model, src = models.resolve(self.model_cfg, None, t)
            log(self.id, f"printer type {t!r} -> model {model or 'unknown'} ({src})")

    def _on_status(self, data: dict):
        self._observe(data)          # first: the fallback in there (_no_position) uses this poll's Z and percent
        super()._on_status(data)

    def _current_job(self, data: dict) -> dict | None:
        jb = data.get("job")
        state = (data.get("printer") or {}).get("state")
        prev_state, self._prev_state = self._prev_state, state
        progress = _num((jb or {}).get("progress"))
        prev_progress, self._prev_progress = self._prev_progress, progress
        if not jb:
            return None
        jid, old = jb.get("id"), self.job
        if old and jid is not None and old.get("id") == jid:
            return None
        # a fresh start (after the end, or the percent drops): another print, even of the same file -
        # also when the last print had no id and this one has
        restarted = bool(old) and ((state == "PRINTING" and prev_state in ("FINISHED", "STOPPED", "IDLE", "READY")) or (
            progress is not None and prev_progress is not None and progress < prev_progress - 5))
        if jid is None:
            # no job id (PrusaLink just restarted): its file - or a fresh start - tells whether it's another print
            now = time.time()
            if old and not restarted and now < self._noid_at:
                return None
            self._noid_at = now + NOID_CHECK_S
        code, job = self.client.get_json("/api/v1/job")
        if code != 200 or not job or not job.get("file"):
            return None
        f = job["file"]
        new = {"id": job.get("id"), "display_name": f.get("display_name") or f.get("name"),
               "download": (f.get("refs") or {}).get("download"), "size": f.get("size"),
               "m_timestamp": f.get("m_timestamp"), "name": f.get("name")}
        if old and (new["id"] is None or old.get("id") is None) and not restarted and _same_file(old, new):
            if new["id"] is not None:
                old["id"] = new["id"]                 # the same print, its id known (again)
            return None
        self.last_sdpos = None                       # sdpos from the previous job no longer counts
        self._est = None
        return new

    def _not_downloadable(self, job: dict) -> str | None:
        return None if job.get("download") else SD_JOB

    def _stream(self, job: dict, on_chunk):
        # PrusaLink on a Pi: "/api/files/local/PrusaLink gcodes/<name>/raw" - with a space, never encoded
        self.client.stream(url_path(job["download"], verbatim=self.pi), on_chunk)

    # ---------------------------------------------------------------- estimate
    def _observe(self, data: dict):
        """Z, percent and time left of this poll, for the estimate."""
        pr, jb = data.get("printer") or {}, data.get("job") or {}
        with self.lock:
            est = self._est
            if est is None or est.job is not self.job:
                est = self._est = _Estimate(self.job)
            est.advance(time.time())
            est.state = pr.get("state")
            z = _num(pr.get("axis_z"))
            if est.state == "PRINTING" and z is not None:
                est.z.append(z)
                est.vote = True
            pct = _num(jb.get("progress"))
            if pct != est.pct:
                est.pct, est.run = pct, 0.0
            est.remaining = _num(jb.get("time_remaining"))

    def _no_position(self, tracker: Tracker, pr: dict, jb: dict | None):
        if self.estimated:
            self._estimate_now(tracker)
        else:                                        # metrics, but none just now: as before
            super()._no_position(tracker, pr, jb)

    def _tick_loop(self):
        while True:
            time.sleep(EST_TICK_S)
            if not self.estimated:
                continue
            try:
                with self.lock:
                    sample = self._estimate_now(self.tracker) if self.online and self.tracker else None
                    key = self.tp_key
                if sample:
                    self.publish("pos", {"s": [sample], "key": key})
            except Exception:
                traceback.print_exc()

    def _estimate_now(self, tr: Tracker) -> list | None:
        """Place the print (self.lock held); returns a "pos" sample when it moved on."""
        est = self._est
        tp = tr.tp
        n = tp.count
        if est is None or est.job is not self.job or not n:
            return None
        est.advance(time.time())
        pct, lz, ls = est.pct, tp.layer_z, tp.layer_start
        # Z: the layers below the lowest Z of the last polls are done (a z-hop or a travel isn't a
        # layer, and the planned Z runs ahead of the nozzle); the layer of the latest Z isn't passed.
        # Not at 0 %: the start G-code (probing, purge line) moves through the first layers' heights.
        # The lower bound (and the vote below) only with all EST_Z_SAMPLES polls in: a single report
        # may be a z-hop, which on a Pi can look like a higher layer (a hop of a layer multiple).
        lo_z, hi_z = 0, n
        full = len(est.z) == est.z.maxlen
        if lz and est.z and pct is not None and pct > 0:
            top = lz[-1] + Z_TOL
            if not full:
                pass
            elif self.pi:                             # exact G-code Z: anything else is a hop or a travel
                zs = [z for z in est.z if z <= top and abs(lz[_nearest(lz, z)] - z) <= PI_Z_TOL]
                if zs:
                    lo_z = ls[_nearest(lz, min(zs))]
            else:                                     # Buddy: the Z may include the bed mesh
                zs = [z for z in est.z if z <= top]
                i = bisect_right(lz, min(zs) + 1e-3) - 1 if zs else -1
                if i >= 0:
                    lo_z = ls[i]
            last = est.z[-1]
            if last <= top:
                j = bisect_right(lz, last + (PI_Z_TOL if self.pi else Z_TOL))
                if j < len(ls):
                    hi_z = max(lo_z, ls[j])
        # the percent: between the M73 marks it passed and the next; normal (P) or stealth mode (Q) -
        # the printer doesn't say which, so the marks whose window fits the Z win
        wins = {}
        if pct is not None:
            for k, marks in (("P", tp.progress_marks), ("Q", tp.silent_marks)):
                if marks:
                    wins[k] = _window(marks, pct, n)
        if est.vote and full and len(wins) == 2 and (lo_z > 0 or hi_z < n):
            fits = {k: lo < hi_z and hi > lo_z for k, (lo, hi) in wins.items()}
            if fits["P"] != fits["Q"]:
                est.votes["P" if fits["P"] else "Q"] += 1
        est.vote = False
        lead = est.votes["Q"] - est.votes["P"]
        if lead >= EST_VOTE_LEAD:
            est.use = "Q"
        elif -lead >= EST_VOTE_LEAD:
            est.use = "P"
        use = est.use if est.use in wins else next(iter(wins), "P")
        p = float(lo_z)
        if use in wins:
            lo, hi = wins[use]
            # the next mark caps the Z bounds too: hops that look like a layer (a layer multiple, in
            # several polls in a row) can't put the print past what the percent says
            lo_z, hi_z = min(lo_z, hi), min(hi_z, hi)
            left = 100.0 - pct
            per_pct = est.remaining / left if est.remaining and left > 0 else None
            frac = min(0.999, est.run / per_pct) if per_pct and pct > 0 else 0.0
            p = lo + frac * (hi - lo)
        p = min(max(p, float(lo_z)), max(float(lo_z), hi_z - 1e-3))
        before = tr.printed
        tr.advance_to(p)
        if tr.printed > before and tr.nozzle:
            x, y, z = tr.nozzle
            return [round(time.time() * 1000, 1), round(x, 3), round(y, 3), round(z, 3), round(tr.printed, 3)]
        return None

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
                    self._live()
                    self._feed(f["v"], None, now)
                elif name == "pos_x":
                    pend.clear()
                    pend["t"], pend["x"] = t_us, f["v"]
                elif name == "pos_y" and "x" in pend:
                    pend["y"] = f["v"]
                elif name == "pos_z" and "y" in pend:
                    x, y, z, t = pend["x"], pend["y"], f["v"], pend["t"]
                    pend.clear()
                    self._live()
                    sample = self._feed(None, [t / 1000, x, y, z], now)
                    if sample:
                        out.append(sample)
        if out:
            self.publish("pos", {"s": out, "key": self.tp_key})

    def _live(self):
        """The printer sends its file / nozzle position: no more estimates (self.lock held)."""
        if not self.metrics_seen:
            self.metrics_seen, self.estimated = True, False
            log(self.id, "metrics arrive: live position")
