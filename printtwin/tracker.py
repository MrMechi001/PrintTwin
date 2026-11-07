"""Map live printer telemetry onto toolpath progress.

`printed` is a float in segment units: int part = segments fully done,
fraction = how far along the current segment the nozzle is.

Inputs, best first:
  1. nozzle xyz (firmware metrics pos_x/y/z, ~65 Hz) + sdpos (10 Hz):
     sdpos runs a few moves ahead of the nozzle (planner buffer), so the
     segment under the nozzle is searched in a small window before sdpos.
  2. sdpos only: assume a fixed planner lag.
  3. PrusaLink axis_z only: everything below the current layer is done.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque

from .toolpath import Toolpath

SDPOS_LAG_MOVES = 6        # measured median on MK4 fw 8.0 (p5 4, p95 9)
WINDOW_BACK = 400          # max segments searched before sdpos
Z_TOL = 1.5                # pos_z includes mesh-bed-levelling offset (seen -0.8..+0.5)


class Tracker:
    def __init__(self, tp: Toolpath):
        self.tp = tp
        self.sdpos: int | None = None
        self.printed = 0.0
        self.nozzle: tuple[float, float, float] | None = None
        self._sync = deque(maxlen=200)   # does each new sdpos land on a line start?

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
        lo = max(0, k - WINDOW_BACK, cur - 2)
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
            p = float(max(0, self._sd_index() - SDPOS_LAG_MOVES))
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

    def finish(self):
        self.printed = float(self.tp.count)
