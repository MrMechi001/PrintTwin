"""Map live printer telemetry onto toolpath progress.

`printed` is a float in segment units: int part = segments fully done,
fraction = how far along the current segment the nozzle is.

Inputs, best first:
  1. nozzle xyz (firmware metrics pos_x/y/z, ~65 Hz) + sdpos (10 Hz):
     sdpos runs a few moves ahead of the nozzle (planner buffer), so the
     segment under the nozzle is searched in a small window before sdpos.
  2. sdpos only: assume a fixed planner lag.
  3. PrusaLink axis_z only: everything below the current layer is done.
  4. no position at all (Bambu Lab, PrusaLink on a Raspberry Pi): estimated between the slicer's
     progress marks (estimate / advance_to).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque

from .toolpath import Toolpath

SDPOS_LAG_MOVES = 6        # measured median on MK4 fw 8.0 (p5 4, p95 9)
WINDOW_BACK = 400          # max segments searched before sdpos
Z_TOL = 1.5                # pos_z includes mesh-bed-levelling offset (seen -0.8..+0.5)


class Tracker:
    def __init__(self, tp: Toolpath, window_back: int = WINDOW_BACK, sdpos_lag: int = SDPOS_LAG_MOVES):
        """window_back: how many segments before the file position the nozzle may be (Prusa: the
        planner holds a few moves; Klipper reads ~1 s of motion ahead, thousands of short moves)."""
        self.tp = tp
        self.window_back = window_back
        self.sdpos_lag = sdpos_lag
        self.sdpos: int | None = None
        self.printed = 0.0
        self.nozzle: tuple[float, float, float] | None = None
        self._sync = deque(maxlen=200)   # does each new sdpos land on a line start?
        self._t = -1.0                   # model time at_time() last put the nozzle at

    def _sd_index(self) -> int:
        return bisect_right(self.tp.line_off, self.sdpos)

    def feed_sdpos(self, v: int):
        if v != self.sdpos:
            ls = self.tp.line_starts
            # while the job file is still arriving, positions past the loaded text can't be judged
            if self.tp.complete or (ls and v < ls[-1]):
                i = bisect_left(ls, v)
                self._sync.append(i < len(ls) and ls[i] == v)
        self.sdpos = v

    def ahead(self) -> bool:
        """The printer is beyond the part of the job file loaded so far."""
        ls = self.tp.line_starts
        return (not self.tp.complete and self.sdpos is not None
                and (not ls or self.sdpos >= ls[-1]))

    def sync(self) -> dict | None:
        """Share of recent sdpos values that hit a line start (1.0 = file and firmware agree)."""
        if not self._sync:
            return None
        return {"ok": round(sum(self._sync) / len(self._sync), 3), "n": len(self._sync)}

    def feed_pos(self, x: float, y: float, z: float) -> float:
        self.nozzle = (x, y, z)
        tp = self.tp
        n = tp.count
        if n == 0 or self.sdpos is None:
            return self.printed
        k = self._sd_index()
        cur = int(self.printed)
        lo = max(0, k - self.window_back, cur - 2)
        hi = min(n, k + 3)
        st, en = tp.start, tp.end
        best_d, best_j, best_u = 1e18, -1, 0.0
        for j in range(lo, hi):
            b = 3 * j
            if abs(st[b + 2] - z) > Z_TOL:
                continue
            x0, y0 = st[b], st[b + 1]
            dx, dy = en[b] - x0, en[b + 1] - y0
            l2 = dx * dx + dy * dy
            u = 0.0 if l2 == 0 else ((x - x0) * dx + (y - y0) * dy) / l2
            u = 0.0 if u < 0 else 1.0 if u > 1 else u
            ex, ey = x0 + u * dx - x, y0 + u * dy - y
            d = ex * ex + ey * ey
            if d < best_d - 1e-9 or (d <= best_d + 1e-9 and j > best_j):
                best_d, best_j, best_u = d, j, u
        if best_j >= 0:
            tol = max(0.35, tp.wh[2 * best_j])
            if best_d <= tol * tol:
                p = best_j + best_u
                if p > self.printed:
                    self.printed = p
        return self.printed

    def from_sdpos_only(self) -> float:
        if self.sdpos is not None and self.tp.count:
            p = float(max(0, self._sd_index() - self.sdpos_lag))
            if p > self.printed:
                self.printed = p
                b = 3 * min(int(p), self.tp.count - 1)
                self.nozzle = tuple(self.tp.start[b:b + 3])
        return self.printed

    def from_axis_z(self, axis_z: float | None, progress: float | None = None) -> float:
        """Everything below the nozzle's layer is done - but only while Z can be a print layer:
        not while the nozzle is parked above the whole model (heating, start G-code), not at 0 %
        without a file position, and never beyond the firmware's file position (sdpos)."""
        tp = self.tp
        if axis_z is None or not tp.layer_z or axis_z > tp.layer_z[-1] + Z_TOL:
            return self.printed
        if self.sdpos is None and progress is not None and progress <= 0:
            return self.printed
        i = bisect_right(tp.layer_z, axis_z + 1e-3) - 1
        if i >= 0:
            p = float(tp.layer_start[i])
            if self.sdpos is not None:
                p = min(p, float(self._sd_index()))
            if p > self.printed:
                self.printed = p
        return self.printed

    def estimate(self, layer: int | None, percent: float | None, frac: float) -> float:
        """No position from the printer (Bambu Lab): it reports the layer number and the percent of
        the slicer's "M73 L" / "M73 P" lines. The print is between the last mark it passed and the
        next one; `frac` (0..1, from the time per percent) says how far. Only ever moves forward."""
        tp = self.tp
        n = tp.count
        if not n:
            return self.printed
        lo, hi = 0, n
        if layer:
            marks = tp.layer_marks or [(s, i + 1) for i, s in enumerate(tp.layer_start)]
            at = [s for s, v in marks if v == layer]
            after = [s for s, v in marks if v > layer]
            if at:
                lo = max(lo, at[0])
            if after:
                hi = min(hi, after[0])
        if percent is not None and tp.progress_marks:
            before = [s for s, v in tp.progress_marks if v <= percent]
            after = [s for s, v in tp.progress_marks if v > percent]
            if before:
                lo = max(lo, before[-1])
            if after:
                hi = min(hi, after[0])
        hi = max(hi, lo)
        return self.advance_to(lo + max(0.0, min(0.999, frac)) * (hi - lo))

    def advance_to(self, p: float) -> float:
        """An estimated place in the toolpath (segment units): only ever forward; the nozzle is put
        on the toolpath there."""
        n = self.tp.count
        if n and p > self.printed:
            self.printed = p = min(p, float(n))
            k = min(int(p), n - 1)
            u = min(1.0, p - k)
            st, en, b = self.tp.start, self.tp.end, 3 * k
            self.nozzle = (st[b] + (en[b] - st[b]) * u, st[b + 1] + (en[b + 1] - st[b + 1]) * u,
                           st[b + 2] + (en[b + 2] - st[b + 2]) * u)
        return self.printed

    def at_time(self, t: float, hi: int | None = None) -> bool:
        """The nozzle where the job's own timing puts it at model time t (Toolpath.t0 / t1), not past
        the end of segment hi - 1 (the next mark the printer hasn't reported yet). Between two
        segments (a travel) the nozzle moves along the straight line between them. Only ever
        forward. True if the nozzle moved."""
        tp = self.tp
        n = min(tp.count, len(tp.t1))
        if not n:
            return False
        if hi is not None and 0 < hi < n:
            t = min(t, float(tp.t1[hi - 1]))
        if t <= self._t:
            return False
        self._t = t
        t0, t1, st, en = tp.t0, tp.t1, tp.start, tp.end
        i = bisect_left(t1, t, 0, n)                  # the first segment that ends at or after t
        if i >= n:
            p, b = float(n), 3 * (n - 1)
            xyz = (en[b], en[b + 1], en[b + 2])
        elif t >= t0[i]:
            d = t1[i] - t0[i]
            u = 1.0 if d <= 0 else min(1.0, (t - t0[i]) / d)
            p, b = i + u, 3 * i
            xyz = tuple(st[b + k] + (en[b + k] - st[b + k]) * u for k in range(3))
        else:                                         # travelling to segment i
            p, b = float(i), 3 * i
            if i:
                ta, pb = t1[i - 1], 3 * (i - 1)
                u = (t - ta) / (t0[i] - ta) if t0[i] > ta else 1.0
                xyz = tuple(en[pb + k] + (st[b + k] - en[pb + k]) * u for k in range(3))
            else:
                xyz = (st[b], st[b + 1], st[b + 2])
        if p < self.printed:                          # put further already (another estimate)
            return False
        self.printed, self.nozzle = p, xyz
        return True

    def finish(self):
        self.printed = float(self.tp.count)


# ------------------------------------------------------------------------------- time estimate
FANS = (("cooling_fan_speed", 1), ("big_fan1_speed", 2), ("big_fan2_speed", 3))   # report field, M106 P
NO_TRAY = 255                       # AMS "tray_now": nothing loaded (254 = the external spool)
REPORT_LAG_S = 0.5                  # a change is reported this long after it happened, on average
RATE_TAU_S = 20.0                   # the rate follows the print over about this much printing time


class TimeEstimate:
    """Where the nozzle is when the printer reports no position (Bambu Lab): its reports give the
    layer, the percent of the slicer's "M73 P" lines, the minutes left, the fan speeds (0-15) and
    the AMS slot. Between them the nozzle follows the job's own timing (Toolpath.t0 / t1), at the
    rate the print really runs (model seconds per real second, learnt from the time between the
    printer's reports, times the speed level). Every new layer / percent, part-fan or AMS change
    puts it back on the spot in the G-code where that happens."""

    def __init__(self, tp: Toolpath):
        self.tp = tp
        self.clock = 0.0                    # real seconds of printing (not paused, not preparing)
        self.last: float | None = None      # wall time of the last update
        self.anchor: tuple | None = None    # (segment, model time, clock, counts for the rate) of the last sync
        self.base: float | None = None      # model seconds per real second at 100 % speed
        self.mark: tuple | None = None
        self.seen: dict = {}
        self.syncs = 0                      # how often a fan / AMS change put the estimate right
        self._speed = self._rate_now = 1.0
        self._tables_for: tuple | None = None

    # ---- the job's marks
    def _tables(self):
        tp = self.tp
        key = (tp.count, len(tp.layer_marks), len(tp.progress_marks), len(tp.fan_marks), len(tp.tool_changes))
        if key == self._tables_for:
            return
        self._tables_for = key
        lm = tp.layer_marks or [(s, i + 1) for i, s in enumerate(tp.layer_start)]
        self._layer_at, self._pct_at = {}, {}
        for s, v in lm:
            self._layer_at.setdefault(v, s)
        for s, v in tp.progress_marks:
            self._pct_at.setdefault(v, s)
        self._layer_vals = sorted(self._layer_at)
        self._pct_vals = sorted(self._pct_at)
        self._mark_segs = sorted({s for s, _ in lm} | {s for s, _ in tp.progress_marks})
        self._fans = {}
        last: dict = {}
        for s, f, v in tp.fan_marks:
            self._fans.setdefault(f, []).append((s, v, last.get(f, 0.0)))
            last[f] = v
        self._tools = [s for s, _ in tp.tool_changes]

    def _next(self, vals: list, at: dict, v) -> int | None:
        i = bisect_right(vals, v)
        return at[vals[i]] if i < len(vals) else None

    def window(self, layer, percent) -> tuple[int, int, int]:
        """(the last mark passed, the next one, the one after it) as segment indices."""
        self._tables()
        n = self.tp.count
        lo, hi = 0, n
        if layer:
            if layer in self._layer_at:
                lo = max(lo, self._layer_at[layer])
            nx = self._next(self._layer_vals, self._layer_at, layer)
            if nx is not None:
                hi = min(hi, nx)
        if percent is not None and self._pct_vals:
            i = bisect_right(self._pct_vals, percent) - 1
            if i >= 0:
                lo = max(lo, self._pct_at[self._pct_vals[i]])
            nx = self._next(self._pct_vals, self._pct_at, percent)
            if nx is not None:
                hi = min(hi, nx)
        hi = max(hi, lo)
        j = bisect_right(self._mark_segs, hi)
        hi2 = self._mark_segs[j] if j < len(self._mark_segs) else n
        return lo, hi, max(hi2, hi)

    # ---- syncing
    def _sync(self, seg: int, counts: bool):
        """The printer is at segment seg now: learn the rate from the last sync, start from here."""
        t = self.tp.time_at(seg)
        a = self.anchor
        if a and counts and a[3]:
            dt_real, dt_model = self.clock - a[2], t - a[1]
            tools = self._tools
            no_change = bisect_right(tools, seg) == bisect_right(tools, a[0])   # AMS loading takes real time
            if dt_real >= 3.0 and dt_model > 0 and no_change:
                sample = dt_model / (dt_real * self._speed)
                if self.base is None:
                    self.base = sample
                else:
                    sample = min(max(sample, self.base / 3), self.base * 3)
                    self.base += dt_real / (dt_real + RATE_TAU_S) * (sample - self.base)
        self.anchor = (seg, t, self.clock - REPORT_LAG_S, counts)

    def _rate(self, remaining_min, seg: int) -> float:
        if self.base is not None:
            return self.base * self._speed
        tp = self.tp
        if remaining_min and remaining_min > 0 and tp.complete and tp.t_total:
            r = (tp.t_total - tp.time_at(seg)) / (remaining_min * 60.0)
            return min(max(r, 0.2), 5.0)
        return self._speed

    def now(self) -> float | None:
        """Model time the estimate is at (from the last sync and the rate)."""
        a = self.anchor
        return None if a is None else a[1] + (self.clock - a[2]) * self._rate_now

    def _match(self, cands, seg_lo: int, seg_hi: int) -> int | None:
        """The candidate segment closest in model time to where the estimate is now."""
        here = self.now()
        best, best_d = None, None
        for s in cands:
            if seg_lo <= s <= seg_hi:
                d = abs(self.tp.time_at(s) - here) if here is not None else s - seg_lo
                if best_d is None or d < best_d:
                    best, best_d = s, d
        return best

    def update(self, now: float, state: str, layer=None, percent=None, remaining_min=None,
               speed=None, fans: dict | None = None, tray=None) -> tuple[float, int] | None:
        """One poll: state = the printer's gcode_state; speed = spd_mag (percent); fans = the report's
        fan fields; tray = AMS tray_now. Returns (model time, the next unreported mark) - or None."""
        dt = 0.0 if self.last is None else min(max(now - self.last, 0.0), 5.0)
        self.last = now
        running = state == "RUNNING"
        if running:
            self.clock += dt
        self._speed = min(max((speed or 100) / 100.0, 0.2), 3.0)
        layer = int(layer) if layer else None
        lo, hi, hi2 = self.window(layer, percent)
        self._rate_now = self._rate(remaining_min, lo)
        # not the start G-code (heating, levelling); a job without "M73 L" lines (OrcaSlicer) may
        # leave the layer at 0 - its percent still moves on
        printing = bool((layer and layer >= 1) or (percent and percent >= 1))
        if (layer, percent) != self.mark:
            self.mark = (layer, percent)
            if self.anchor is None or lo > self.anchor[0]:    # (a fan / AMS change may be further already)
                self._sync(lo, printing)
        for key, fan in FANS:
            v = (fans or {}).get(key)
            v = None if v in (None, "") else float(v)
            prev = self.seen.get(key)
            self.seen[key] = v
            if running and printing and prev is not None and v is not None and v != prev:
                cands = [s for s, sv, pv in self._fans.get(fan, ())
                         if abs(sv / 255 * 15 - v) <= 1.0 and abs(pv / 255 * 15 - v) > 0.5]
                s = self._match(cands, lo, hi2)
                if s is not None:
                    self._sync(s, True)
                    self.syncs += 1
        if tray not in (None, ""):
            tray = int(tray)
            prev = self.seen.get("tray")
            if tray != NO_TRAY:
                self.seen["tray"] = tray
                if running and printing and prev is not None and tray != prev:
                    s = self._match(self._tools, lo, hi2)
                    if s is not None:
                        self._sync(s, False)            # the loading time is not in the G-code
                        self.syncs += 1
        if self.anchor is None or not (running or state == "PAUSE"):
            return None
        return self.now(), hi
