"""Bambu Lab position estimate by the job's own timing - offline, no printer, no network.

usage: python -I tests/test_bambu_time.py
The time model of the G-code reader (feed rates, M204, corners, G4, arcs, fan marks), the nozzle
at a model time (travels, never back, not past the next unreported mark), and the estimate against
a simulated print that runs differently from the plan: every layer up to 30 % faster or slower,
Sport speed (124 %) from the middle on, 20 s of AMS loading at each filament change that the
G-code doesn't know about. The printer reports once a second what a real one reports (layer,
percent, minutes left, part fan 0-15, AMS slot, speed); the estimate is asked four times a second.
Compared with the estimate before (evenly through the G-code lines between two marks).
"""
import math
import statistics
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin.toolpath import TYPE_ID, parse_gcode  # noqa: E402
from printtwin.tracker import TimeEstimate, Tracker  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(name)


# ---------------------------------------------------------------- the time model
tp = parse_gcode(b"""G90
M83
M204 S1000
G1 X0 Y0 F6000
G1 X100 Y0 E1 F6000
G1 X200 Y0 E1
G1 X200 Y100 E1
G4 P500
G1 X0 Y100 F12000
G1 X0 Y0 E1 F6000
M106 S255
G1 X10 Y0 E0.1
M106 P2 S128
M107
G3 X20 Y0 I5 J0 E0.2
""")
t0, t1 = list(tp.t0), list(tp.t1)
check("100 mm at 100 mm/s from standstill, 1000 mm/s²: 1 s + 0.05 s accelerating", abs(t1[0] - 1.05) < 1e-3, t1[0])
check("straight on: no braking; the 90° corner after it: braking to 5 mm/s at its end",
      abs(t1[1] - t0[1] - (1.0 + 95 ** 2 / 2e5)) < 1e-3, round(t1[1] - t0[1], 4))
check("G4 P500 and a 200 mm travel at 200 mm/s are in the gap before the next segment",
      abs(t0[3] - t1[2] - (0.5 + 1.1 + 195 ** 2 / 4e5)) < 1e-3, round(t0[3] - t1[2], 4))
arc = [t1[i] - t0[i] for i in range(5, tp.count)]
check("a half circle r = 5 at 100 mm/s: ~0.157 s + accelerating, spread evenly over its chords",
      abs(sum(arc) - (5 * math.pi / 100 + 95 ** 2 / 2e5)) < 2e-3 and max(arc) - min(arc) < 1e-4, round(sum(arc), 4))
check("times only grow", all(t0[i] <= t1[i] <= t0[i + 1] + 1e-6 for i in range(tp.count - 1)))
check("fan marks: part fan full, aux fan 50 %, part fan off (M107)",
      tp.fan_marks == [(4, 1, 255.0), (5, 2, 128.0), (5, 1, 0.0)], tp.fan_marks)
check("total model time", abs(tp.t_total - t1[-1]) < 1e-6, round(tp.t_total, 3))

tr = Tracker(tp)
check("at_time: on a segment", tr.at_time(0.525) and abs(tr.nozzle[0] - 50) < 0.5, tr.nozzle)
mid = (t1[2] + t0[3]) / 2
check("at_time: during a travel the nozzle is on the line between the two segments",
      tr.at_time(mid) and tr.printed == 3.0 and 0 < tr.nozzle[0] < 200 and abs(tr.nozzle[1] - 100) < 1e-6
      and abs(tr.nozzle[2]) < 1e-6, tr.nozzle)
check("at_time: never back", not tr.at_time(0.1) and tr.printed == 3.0)
check("at_time: not past the end of the segment before the next unreported mark",
      tr.at_time(t1[-1], hi=4) and tr.printed == 4.0, tr.printed)


# ---------------------------------------------------------------- a Bambu-like job
def job(layers=30):
    """About a minute per layer, like a mid-sized part: 4 walls of 240 short lines, an overhang every
    3rd layer (part fan up), 80 infill lines with travels, the other AMS filament every 8th layer."""
    g = ["G90", "M83", "M204 S10000", "M106 S0", "G1 X10 Y10 F30000"]
    tool = 0
    for L in range(1, layers + 1):
        g += [f"M73 L{L}", f"; Z_HEIGHT: {0.2 * L:.2f}", f"G1 Z{0.2 * L:.2f} F1200"]
        if L == 2:
            g.append("M106 S178")                                   # 70 % from layer 2 on
        g += ["M204 S5000", "; FEATURE: Outer wall"]
        for w, rad in enumerate((60, 58, 56, 54)):
            g.append(f"G1 X{128 + rad} Y128 F30000")
            for k in range(1, 241):
                a = 2 * math.pi * k / 240
                g.append(f"G1 X{128 + rad * math.cos(a):.3f} Y{128 + rad * math.sin(a):.3f} E0.05 "
                         f"F{9000 if w == 0 else 12000}")
        if L % 3 == 0:                                              # an overhang: fan up, slow
            g += ["M106 S255", "; FEATURE: Overhang wall"]
            for k in range(1, 61):
                a = math.pi * k / 60
                g.append(f"G1 X{128 + 62 * math.cos(a):.3f} Y{128 + 62 * math.sin(a):.3f} E0.05 F2400")
            g.append("M106 S178")
        g += ["M204 S10000", "; FEATURE: Sparse infill"]
        for k in range(80):
            y = 78 + 1.25 * k
            x0, x1 = (73, 183) if k % 2 == 0 else (183, 73)
            g += [f"G1 X{x0} Y{y} F30000", f"G1 X{x1} Y{y} E3.5 F12000"]
        if L % 8 == 0:                                              # AMS: the other filament
            tool = 1 - tool
            g += ["M620 S1A", "M106 S0", "G1 X-20 Y250 F30000", "G4 P500", f"T{tool}", "M621 S1A",
                  "; FEATURE: Prime tower", "G1 X-20 Y240 E5 F300", "M106 S178"]
    return "\n".join(g) + "\n"


base = parse_gcode(job().encode())
# the slicer's percent marks: "M73 P<n> R<minutes left>" where the job's own time passes each percent
lines = job().split("\n")
T = base.t_total
offs = [0]
for ln in lines:
    offs.append(offs[-1] + len(ln) + 1)
ins = {}
for pc in range(0, 100):
    i = bisect_left(base.t0, T * pc / 100)
    if i < base.count:
        ins.setdefault(bisect_right(offs, base.line_off[i]) - 1, []).append(f"M73 P{pc} R{int(T * (100 - pc) / 100 / 60)}")
out = []
for n, ln in enumerate(lines):
    out += ins.get(n, []) + [ln]
tp = parse_gcode("\n".join(out).encode())
check("generated job: 30 layers, percent marks, fan marks, filament changes",
      len(tp.layer_marks) == 30 and len(tp.progress_marks) >= 95 and len(tp.fan_marks) > 20
      and len(tp.tool_changes) == 3,
      f"{tp.count} segments, {tp.t_total / 60:.0f} min by its own timing, {len(tp.progress_marks)} percent marks, "
      f"{len(tp.fan_marks)} fan marks, {len(tp.tool_changes)} filament changes")

# the "real" print, not as planned: each layer up to 30 % faster / slower, the walls 15 % and the
# overhangs 25 % slower and the infill 10 % faster than the time model says, Sport speed (124 %)
# from layer 16, and 20 s of AMS loading at each filament change that the G-code doesn't know about
layer_seg = [s for s, _ in tp.layer_marks] + [tp.count]
TYPEF = {TYPE_ID["External perimeter"]: 1.15, TYPE_ID["Overhang perimeter"]: 1.25, TYPE_ID["Internal infill"]: 0.9}
tool_segs = {s for s, _ in tp.tool_changes}
speed_at = [124 if L >= 15 else 100 for L in range(30)]
rt0, rt1, r, prev_end, L = [], [], 0.0, 0.0, 0
for i in range(tp.count):
    while L < 29 and i >= layer_seg[L + 1]:
        L += 1
    lf = (1.0 + 0.3 * (((L * 7919) % 13) / 6.0 - 1.0)) / (speed_at[L] / 100)
    r += (tp.t0[i] - prev_end) * lf + (20.0 if i in tool_segs else 0.0)
    rt0.append(r)
    r += (tp.t1[i] - tp.t0[i]) * lf * TYPEF.get(tp.type[i], 1.0)
    rt1.append(r)
    prev_end = tp.t1[i]
END = r


def true_model_time(real):
    """Where the real nozzle is after `real` seconds, as model time of the job."""
    i = bisect_left(rt1, real)
    if i >= tp.count:
        return tp.t_total
    if real >= rt0[i]:
        d = rt1[i] - rt0[i]
        return tp.t0[i] + ((real - rt0[i]) / d if d > 0 else 1.0) * (tp.t1[i] - tp.t0[i])
    a, ma = (rt1[i - 1], tp.t1[i - 1]) if i else (0.0, 0.0)          # in the gap before segment i
    return ma + (real - a) / (rt0[i] - a) * (tp.t0[i] - ma) if rt0[i] > a else tp.t0[i]


def seg_at(t):
    i = bisect_left(tp.t1, t)
    return min(i, tp.count - 1)


def last_before(marks, i, default):
    v = default
    for s, val in marks:
        if s > i:
            break
        v = val
    return v


def report(real):
    t = true_model_time(real)
    i = seg_at(t)
    layer = last_before(tp.layer_marks, i, 0)
    pct = last_before(tp.progress_marks, i, 0)
    fan = last_before([(s, v) for s, f, v in tp.fan_marks if f == 1], i, 0.0)
    tray = last_before(tp.tool_changes, i, 0)
    rem = int((END - real) / 60)                     # the printer's own minutes left
    return {"layer_num": layer, "mc_percent": pct, "mc_remaining_time": rem,
            "cooling_fan_speed": str(round(fan / 255 * 15)), "spd_mag": speed_at[max(0, layer - 1)],
            "tray": tray}


def nozzle_at(t):
    trk = Tracker(tp)
    trk.at_time(t)
    return trk.nozzle


new_tr, est = Tracker(tp), TimeEstimate(tp)
old_tr, old_mark, old_t = Tracker(tp), None, 0.0
err_new, err_old, mm_new, mm_old = [], [], [], []
rep = report(0.0)
step, now = 0.25, 1000.0
for k in range(int(END / step)):
    real = k * step
    if k % 4 == 0:
        rep = report(real)
    now += step
    got = est.update(now, "RUNNING", rep["layer_num"], rep["mc_percent"], rep["mc_remaining_time"],
                     rep["spd_mag"], {"cooling_fan_speed": rep["cooling_fan_speed"]}, rep["tray"])
    if got:
        new_tr.at_time(*got)
    # the estimate before: evenly through the segments between the marks, by the time per percent
    mark = (rep["layer_num"], rep["mc_percent"])
    if mark != old_mark:
        old_mark, old_t = mark, now
    left = 100.0 - rep["mc_percent"]
    per = rep["mc_remaining_time"] * 60.0 / left if rep["mc_remaining_time"] and left > 0 else None
    frac = (now - old_t) / per if per else 0.0
    p = old_tr.estimate(rep["layer_num"] or None, rep["mc_percent"], frac)
    if rep["layer_num"] < 2:
        continue
    truth = true_model_time(real)
    i = min(int(p), tp.count - 1)
    t_old = tp.t0[i] + (p - i) * (tp.t1[i] - tp.t0[i])
    err_new.append(abs(new_tr._t - truth))
    err_old.append(abs(t_old - truth))
    if k % 8 == 0:
        tx = nozzle_at(truth)
        mm_new.append(math.dist(new_tr.nozzle, tx))
        mm_old.append(math.dist(old_tr.nozzle, tx))


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))]


print(f"     print: {END:.0f} s real, {tp.t_total:.0f} s by the job's timing; {est.syncs} fan / AMS syncs")
print(f"     time off (s, median / 90 %):  new {statistics.median(err_new):.2f} / {q(err_new, .9):.2f}   "
      f"before {statistics.median(err_old):.2f} / {q(err_old, .9):.2f}")
print(f"     nozzle off (mm, median / 90 %): new {statistics.median(mm_new):.1f} / {q(mm_new, .9):.1f}   "
      f"before {statistics.median(mm_old):.1f} / {q(mm_old, .9):.1f}")
check("fan and AMS changes put the estimate right", est.syncs >= 20, est.syncs)
check("the rate is learnt: Sport speed and slower / faster layers", est.base is not None, est.base)
within_new = sum(e <= 1.0 for e in err_new) / len(err_new)
within_old = sum(e <= 1.0 for e in err_old) / len(err_old)
print(f"     within 1 s of the real nozzle: new {within_new:.0%}   before {within_old:.0%}")
check("much closer in time than before (median and 90 %)",
      statistics.median(err_new) < 0.5 * statistics.median(err_old) and q(err_new, .9) < 0.6 * q(err_old, .9))
check("within 1 s of the real nozzle far more often than before", within_new > within_old + 0.2)

# paused: the estimate stands still; after the pause it goes on from there
est2, tr2 = TimeEstimate(tp), Tracker(tp)
r5 = report(300.0)
for k in range(8):
    g = est2.update(2000.0 + k, "RUNNING", r5["layer_num"], r5["mc_percent"], r5["mc_remaining_time"], 100, {}, None)
    tr2.at_time(*g)
here = tr2._t
for k in range(20):
    g = est2.update(2008.0 + k, "PAUSE", r5["layer_num"], r5["mc_percent"], r5["mc_remaining_time"], 100, {}, None)
    tr2.at_time(*g)
check("paused: stands still", tr2._t == here, f"{here:.1f} s")
g = est2.update(2029.0, "RUNNING", r5["layer_num"], r5["mc_percent"], r5["mc_remaining_time"], 100, {}, None)
g = est2.update(2030.0, "RUNNING", r5["layer_num"], r5["mc_percent"], r5["mc_remaining_time"], 100, {}, None)
check("... and goes on afterwards", tr2.at_time(*g) and tr2._t > here)
check("not while the start G-code runs (layer 0)", TimeEstimate(tp).update(1.0, "PREPARE", 0, 0, 30, 100, {}, None) is None)

print("\nall passed" if not FAILED else f"\n{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
