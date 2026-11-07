"""Regression check: replay a recorded metrics log through the real tracker.

usage: python tools/replay_check.py <job file .bgcode/.gcode> [metrics log]
Reports how well nozzle samples match the toolpath and how progress moves.
"""
import ast
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin.config import ROOT  # noqa: E402
from printtwin.metrics import parse_datagram  # noqa: E402
from printtwin.toolpath import load_file  # noqa: E402
from printtwin.tracker import Tracker  # noqa: E402

job = Path(sys.argv[1])
log = Path(sys.argv[2]) if len(sys.argv) > 2 else sorted((ROOT / "logs").glob("metrics_*.log"))[0]
t0 = time.time()
tp = load_file(job)
print(f"{job.name}: {tp.count} segments, {len(tp.layer_z)} layers, load {time.time()-t0:.1f} s")
tr = Tracker(tp)
pend = {}
hist = []
t0 = time.time()
for line in log.read_text(encoding="utf-8").splitlines():
    _, _, rep = line.split("\t", 2)
    r = parse_datagram(ast.literal_eval(rep))
    if not r:
        continue
    for t, name, _, f in r[2]:
        if name == "sdpos":
            tr.feed_sdpos(f["v"])
        elif name == "pos_x":
            pend = {"x": f["v"]}
        elif name == "pos_y" and "x" in pend:
            pend["y"] = f["v"]
        elif name == "pos_z" and "y" in pend:
            before = tr.printed
            tr.feed_pos(pend["x"], pend["y"], f["v"])
            hist.append((t, tr.printed, tr.printed > before))
            pend = {}
dt = time.time() - t0
moved = sum(1 for h in hist if h[2])
span = (hist[-1][0] - hist[0][0]) / 1e6 if hist else 0
print(f"{len(hist)} nozzle samples over {span:.0f} s, tracker {dt*1e3/len(hist):.3f} ms/sample")
print(f"progress advanced on {moved/len(hist)*100:.0f} % of samples; "
      f"segment {hist[0][1]:.1f} -> {hist[-1][1]:.1f} ({hist[-1][1]-hist[0][1]:.0f} segments)")
jumps = sorted(b[1] - a[1] for a, b in zip(hist, hist[1:]))
print(f"largest single-sample jumps (segments): {[round(j, 1) for j in jumps[-5:]]}")
