"""Toolpath parser checks: bgcode vs. plain-text variants, other slicers, chunked loading, sync.

usage: python -I tests/test_toolpath.py [job.bgcode]
Default job: tests/fixtures/sample.bgcode (+ its plain .gcode twin), see tools/make_fixtures.py.
"""
import ast
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin import bgcode  # noqa: E402
from printtwin.config import ROOT  # noqa: E402
from printtwin.metrics import parse_datagram  # noqa: E402
from printtwin.toolpath import TYPE_ID, JobDecoder, load_file, parse_gcode  # noqa: E402
from printtwin.tracker import Tracker  # noqa: E402
import random  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def same_geometry(a, b, tol=1e-4):
    if a.count != b.count:
        return False, f"{a.count} vs {b.count} segments"
    for arr in ("start", "end", "wh"):
        x, y = getattr(a, arr), getattr(b, arr)
        worst = max(abs(p - q) for p, q in zip(x, y))
        if worst > tol:
            return False, f"{arr} differs by {worst}"
    if list(a.type) != list(b.type) or a.layer_z != b.layer_z or a.layer_start != b.layer_start:
        return False, "types/layers differ"
    return True, f"{a.count} segments, {len(a.layer_z)} layers"


FIXTURES = Path(__file__).resolve().parent / "fixtures"
job = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES / "sample.bgcode"
ref = load_file(job)
_, blocks = bgcode.read_blocks(job.read_bytes())
stream = b"".join(bgcode.decode_gcode_block(b) for b in blocks if b.type == 1)
check("sample job decodes", ref.count > 1000 and len(ref.layer_z) >= 10 and ref.meta.get("format") == "bgcode",
      f"{ref.count} segments, {len(ref.layer_z)} layers")

# 0) the same slice exported by PrusaSlicer as plain .gcode gives the same toolpath
twin = job.with_suffix(".gcode")
if len(sys.argv) <= 1 and twin.exists():
    ok, d = same_geometry(ref, load_file(twin))
    check("PrusaSlicer .gcode export == .bgcode export", ok, d)

# 1) PrusaSlicer-style ASCII: spaces between parameters, no MeatPack artefacts
spaced = re.sub(rb"(?m)^(G\d+)(?=[A-Z])", rb"\1 ", stream)
spaced = re.sub(rb"(?<=[0-9.])([XYZEFIJ])(?=[-0-9.])", rb" \1", spaced)
spaced = re.sub(rb"\n\n+", b"\n", spaced)
ok, d = same_geometry(ref, parse_gcode(spaced))
check("ASCII with spaces == bgcode", ok, d)

# 2) Windows line endings + plain .gcode through load_file (metadata from the tail)
crlf = spaced.replace(b"\n", b"\r\n") + b"\r\n; filament_colour = #12AB34\r\n; nozzle_diameter = 0.4\r\n"
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "job.gcode"
    p.write_bytes(crlf)
    tp = load_file(p)
ok, d = same_geometry(ref, tp)
check("CRLF .gcode file == bgcode", ok, d)
check(".gcode metadata from file tail", tp.meta.get("filament_colour") == "#12AB34" and tp.meta["format"] == "gcode",
      str(tp.meta))

# 3) OrcaSlicer snippet: own type names, no ;HEIGHT:, relative E
orca = b"""M83
;LAYER_CHANGE
;Z:0.2
G1 Z.2 F720
;TYPE:Outer wall
;WIDTH:0.42
G1 X10 Y10 F3000
G1 X20 Y10 E.5
;TYPE:Sparse infill
G1 X20 Y20 E.5
;LAYER_CHANGE
;Z:0.4
G1 Z.4
;TYPE:Top surface
G1 X10 Y20 E.5
"""
t = parse_gcode(orca)
check("Orca types mapped", list(t.type) == [TYPE_ID["External perimeter"], TYPE_ID["Internal infill"],
                                           TYPE_ID["Top solid infill"]], str(list(t.type)))
check("Orca heights from layers", [round(t.wh[2 * j + 1], 3) for j in range(3)] == [0.2, 0.2, 0.2])

# 4) Cura snippet: absolute E (M82), ;LAYER:n but no ;Z:, lower-case + N line numbers
cura = b"""M82
G92 E0
;LAYER:0
;TYPE:WALL-OUTER
G0 X10 Y10 Z0.3
G1 X20 Y10 E1.0
N12 G1 X20 Y20 E2.0
;LAYER:1
;TYPE:FILL
g1 z0.5
G1 X10 Y20 E3.0
G1 X10 Y30 E3.0
"""
t = parse_gcode(cura)
check("Cura layers derived from Z", t.layer_z == [0.3, 0.5] and t.layer_start == [0, 2], f"{t.layer_z} {t.layer_start}")
check("Cura absolute E / zero-E move skipped", t.count == 3, f"{t.count} segments")
check("Cura first layer height", abs(t.wh[1] - 0.3) < 1e-6 and abs(t.wh[5] - 0.2) < 1e-6,
      f"{t.wh[1]:.3f}, {t.wh[5]:.3f}")

# 5) a real firmware recording: every sdpos lands on a line start of our decoded stream.
#    Optional - recordings contain the printer's MAC address, so they stay out of the repo:
#    tests/private/recording.bgcode + recording.log (tools/probe_metrics.py output for that job)
rec_job, rec_log = ROOT / "tests/private/recording.bgcode", ROOT / "tests/private/recording.log"
if rec_job.exists() and rec_log.exists():
    tr = Tracker(load_file(rec_job))
    for line in rec_log.read_text(encoding="utf-8").splitlines():
        r = parse_datagram(ast.literal_eval(line.split("\t", 2)[2]))
        for _, name, _, f in (r[2] if r else []):
            if name == "sdpos":
                tr.feed_sdpos(f["v"])
    s = tr.sync()
    check("recorded sdpos in sync with bgcode stream", s and s["ok"] == 1.0, str(s))
else:
    print("SKIP recorded sdpos check (no tests/private/recording.*)")


# 6) progressive loading: the file arrives in random pieces, like a slow download
def chunked(buf, seed):
    """Feed in random pieces; remember the first state that is partly loaded."""
    rnd = random.Random(seed)
    d, i, mid = JobDecoder(), 0, None
    while i < len(buf):
        n = rnd.choice((1, 7, 333, 2048, 4096, 16384))
        d.feed(buf[i:i + n])
        i += n
        if mid is None and d.tp.count and i < len(buf):
            mid = (i, d.tp.count, d.tp.complete, len(d.tp.to_bin(d.tp.count // 2)))
    return d.finish(), mid


raw = job.read_bytes()
tp, mid = chunked(raw, 1)
ok, d = same_geometry(ref, tp)
check("bgcode fed in random chunks == whole file", ok and list(tp.line_starts) == list(ref.line_starts)
      and tp.meta == ref.meta, d)
half = mid[1] - mid[1] // 2 if mid else 0
check("partly loaded bgcode already has segments, not complete",
      bool(mid) and 0 < mid[1] < ref.count and not mid[2] and mid[3] == 33 * half + (-33 * half % 4),
      f"{mid[1]} of {ref.count} segments after {mid[0]} of {len(raw)} bytes" if mid else "never partly loaded")
tp, _ = chunked(crlf, 2)
ok, d = same_geometry(ref, tp)
check(".gcode fed in random chunks == bgcode", ok and tp.meta.get("filament_colour") == "#12AB34", d)

# 7) tracker while loading: positions past the loaded text don't count as "out of sync"
dec = JobDecoder()
dec.feed(raw[:mid[0]] if mid else raw[:len(raw) // 2])
tr = Tracker(dec.tp)
last = dec.tp.line_starts[-1]
tr.feed_sdpos(dec.tp.line_starts[len(dec.tp.line_starts) // 2])
tr.feed_sdpos(last + 12345)
check("sync ignores positions beyond the loaded part", tr.sync() == {"ok": 1.0, "n": 1} and tr.ahead(), str(tr.sync()))

print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
