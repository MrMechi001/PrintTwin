"""Toolpath parser checks: bgcode vs. plain-text variants, other slicers, chunked loading, sync.

usage: python -I tests/test_toolpath.py [job.bgcode]
Default job: tests/fixtures/sample.bgcode (+ its plain .gcode twin), see tools/make_fixtures.py.
"""
import ast
import io
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin import bgcode  # noqa: E402
from printtwin.config import ROOT  # noqa: E402
from printtwin.metrics import parse_datagram  # noqa: E402
from printtwin.toolpath import (TYPE_ID, TYPES, JobDecoder, JobFileError, JobFileTooLarge, WrongJobFile,  # noqa: E402
                                decode_bytes, load_file, parse_gcode)
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

# 8) multi-tool / multi-filament: T<n> changes per segment, one colour and type per tool
mt = b"M83\nT0\nG1 X0 Y0 Z0.2\nG1 X10 E1\nG1 Y10 E1\nT1 S1 L0 D0\nG1 X0 E1\nM104 T1 S200\nG1 Y0 E1\nT0\nG1 X5 Y5 E1\n" \
     b"; filament_colour = #FF0000;#00FF00;#0000FF\n; filament_type = PLA;PETG;PLA\n"
dec = JobDecoder()
dec.feed(mt)
inf = dec.finish().info()
check("tool changes per segment (M104 T1 is no change)", inf["tool_changes"] == [[0, 0], [2, 1], [4, 0]], str(inf["tool_changes"]))
check("filament colours / types per tool", inf["filament_colours"] == ["#FF0000", "#00FF00", "#0000FF"]
      and inf["filament_colour"] == "#FF0000" and inf["filament_types"] == ["PLA", "PETG", "PLA"], str(inf.get("filament_colours")))

# 9) layer fallback (PrusaLink Z only) at the start of a job: the model is ready before the print
#    begins (slicer hand-over), the MK4 heats with the nozzle parked high above the part
lay = b"M83\n" + b"".join(b";LAYER_CHANGE\n;Z:%.1f\n;HEIGHT:0.2\nG1 X0 Y0 Z%.1f\nG1 X10 E1\nG1 Y10 E1\n" % (z, z)
                          for z in (0.2, 0.4, 0.6, 0.8, 1.0))
lt = parse_gcode(lay)
tr = Tracker(lt)
check("parked above the model (Z 40) while heating: nothing printed", tr.from_axis_z(40.0, 0) == 0)
check("Z 40 at 3 % (no file position): still nothing - above the top layer", tr.from_axis_z(40.0, 3) == 0)
check("first layers' height at 0 %, no file position: nothing yet", tr.from_axis_z(0.6, 0) == 0)
check("Z 0.6 at 10 %: the two layers below are done", tr.from_axis_z(0.6, 10) == lt.layer_start[2], f"{tr.printed}")
tr = Tracker(lt)
tr.feed_sdpos(lt.line_off[3])
check("never past the firmware's file position", tr.from_axis_z(lt.layer_z[-1], 50) == tr._sd_index() == 4,
      f"{tr.printed}")


# 10) Bambu Studio G-code (comment style as in Bambu Studio's output) and .gcode.3mf jobs
def bambu_gcode(layers, colour="#FF0000;#00AE42"):
    out = ["; HEADER_BLOCK_START", "; BambuStudio 02.03.00.70", "; total layer number: %d" % layers,
           "; HEADER_BLOCK_END", "", "; CONFIG_BLOCK_START", f"; filament_colour = {colour}",
           "; filament_type = PLA;PETG", "; nozzle_diameter = 0.4",
           "; printable_area = 0x0,256x0,256x256,0x256", "; printer_model = Bambu Lab X1 Carbon",
           "; CONFIG_BLOCK_END", "", "M620 S255", "T255", "M621 S255", "T1000", "G90", "M83"]
    for k in range(layers):
        z = 0.2 * (k + 1)
        out += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z:.1f}", "; LAYER_HEIGHT: 0.2", f"M73 L{k + 1}",
                f"M73 P{20 * k} R{10 - k}", f"G1 Z{z:.1f}",
                "; FEATURE: Outer wall", "; LINE_WIDTH: 0.42", "G1 X100 Y100", "G1 X110 Y100 E0.5",
                "; FEATURE: Sparse infill", "; LINE_WIDTH: 0.45", "G1 X110 Y110 E0.5"]
        if k == 2:
            out += ["M620 S1A", "T1", "M621 S1A", "; FEATURE: Support interface", "G1 X100 Y110 E0.4"]
        if k == 3:
            out += ["M1020 S0 H0", "T5", "G1 X105 Y110 E0.2"]      # M1020 = filament change; T5 > filaments
    return ("\n".join(out) + "\n").encode()


bt = decode_bytes(bambu_gcode(5))
check("Bambu G-code: layers from '; Z_HEIGHT:'", bt.layer_z == [0.2, 0.4, 0.6, 0.8, 1.0], str(bt.layer_z))
check("Bambu G-code: feature types from '; FEATURE:'", [TYPES[t] for t in bt.type[:2]] == ["External perimeter", "Internal infill"],
      str([TYPES[t] for t in bt.type[:2]]))
check("Bambu G-code: 'Support interface' known", TYPES[bt.type[6]] == "Support material interface", TYPES[bt.type[6]])
check("Bambu G-code: widths / heights from '; LINE_WIDTH:' / '; LAYER_HEIGHT:'",
      abs(bt.wh[0] - 0.42) < 1e-6 and abs(bt.wh[2] - 0.45) < 1e-6 and abs(bt.wh[1] - 0.2) < 1e-6, str(list(bt.wh[:4])))
check("Bambu G-code: T255 / T1000 / T5 (only 2 filaments) are no tools; T1 and M1020 S0 are",
      bt.tool_changes == [(6, 1), (9, 0)], str(bt.tool_changes))
check("Bambu G-code: M73 L layer marks and M73 P progress marks",
      bt.layer_marks == [(0, 1), (2, 2), (4, 3), (7, 4), (10, 5)] and [v for _, v in bt.progress_marks] == [0, 20, 40, 60, 80],
      f"{bt.layer_marks} {bt.progress_marks}")
check("Bambu G-code: '; total layer number' kept", bt.meta.get("total_layers") == 5, str(bt.meta.get("total_layers")))
inf = bt.info()
check("Bambu G-code: settings at the start (colours, printer, bed from printable_area)",
      inf["filament_colours"] == ["#FF0000", "#00AE42"] and bt.meta.get("printer_model") == "Bambu Lab X1 Carbon"
      and bt.meta.get("bed_shape") == "0x0,256x0,256x256,0x256", f"{inf['filament_colours']} {bt.meta.get('bed_shape')}")

zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("3D/3dmodel.model", "<model/>")
    z.writestr("Metadata/plate_1.gcode", bambu_gcode(3, "#112233"))
    z.writestr("Metadata/plate_2.gcode", bambu_gcode(6, "#445566"))
    z.writestr("Metadata/project_settings.config", json.dumps({
        "printer_model": "Bambu Lab P1S", "filament_colour": ["#ABCDEF"], "printer_structure": "corexy",
        "printable_area": ["0x0", "256x0", "256x256", "0x256"]}))
    z.writestr("Metadata/slice_info.config", '<config><plate><metadata key="printer_model_id" value="BL-P001"/></plate></config>')
three = zbuf.getvalue()
t1 = decode_bytes(three)
check(".gcode.3mf: plate 1 by default", t1.meta.get("plate") == 1 and len(t1.layer_z) == 3 and t1.meta["format"] == "3mf",
      f"plate {t1.meta.get('plate')}, {len(t1.layer_z)} layers")
check(".gcode.3mf: the G-code's own settings win over the (possibly stale) project's",
      t1.meta.get("printer_model") == "Bambu Lab X1 Carbon" and t1.info()["filament_colour"] == "#112233",
      f"{t1.meta.get('printer_model')} {t1.info()['filament_colour']}")
check(".gcode.3mf: the project fills gaps; printer_model_id from slice_info",
      t1.meta.get("printer_structure") == "corexy" and t1.meta.get("printer_model_id") == "BL-P001",
      f"{t1.meta.get('printer_structure')} {t1.meta.get('printer_model_id')}")
d2 = JobDecoder(plate=2)
for k in range(0, len(three), 1000):            # arrives in pieces, like a download
    d2.feed(three[k:k + 1000])
t2 = d2.finish()
check(".gcode.3mf: plate 2 when asked, fed in pieces", t2.meta.get("plate") == 2 and len(t2.layer_z) == 6,
      f"plate {t2.meta.get('plate')}, {len(t2.layer_z)} layers")

check(".gcode.3mf: no plate given, two plates: plate 1, marked as a guess", t1.meta.get("plate_guess") == "several")
t6 = decode_bytes(three, layers=6)
check(".gcode.3mf: no plate given (P1 / A1): the plate with the printer's layer count", t6.meta.get("plate") == 2
      and len(t6.layer_z) == 6 and not t6.meta.get("plate_guess"), f"plate {t6.meta.get('plate')}")
for args, what in (({"layers": 5}, "no plate with the printer's layer count"), ({"plate": 3}, "the plate asked for is missing")):
    try:
        decode_bytes(three, **args)
        check(f".gcode.3mf: {what} -> WrongJobFile", False)
    except WrongJobFile as e:
        check(f".gcode.3mf: {what} -> WrongJobFile, not plate 1", True, str(e))
one = io.BytesIO()
with zipfile.ZipFile(one, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("Metadata/plate_1.gcode", bambu_gcode(4))
o = decode_bytes(one.getvalue(), plate=2, layers=4)
check(".gcode.3mf: one plate stored under another number, layer count fits: taken (as a guess)",
      o.meta.get("plate") == 1 and o.meta.get("plate_guess") == "renumbered")
bomb = io.BytesIO()
with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("Metadata/plate_1.gcode", b"; total layer number: 1\n" + b"G4\n" * 8_000_000)
try:
    decode_bytes(bomb.getvalue())
    check(f".gcode.3mf: zip bomb ({len(bomb.getvalue()) >> 10} KB -> 24 MB of G-code) refused", False)
except JobFileTooLarge as e:
    check(f".gcode.3mf: zip bomb ({len(bomb.getvalue()) >> 10} KB -> 24 MB of G-code) refused", True, str(e))

bz = io.BytesIO()
with zipfile.ZipFile(bz, "w", zipfile.ZIP_BZIP2) as z:            # bzip2 inflates in one go: never unpacked
    z.writestr("Metadata/plate_1.gcode", bambu_gcode(3))
try:
    decode_bytes(bz.getvalue())
    check(".gcode.3mf: a member that isn't deflate (bzip2) is refused", False)
except JobFileTooLarge as e:
    check(".gcode.3mf: a member that isn't deflate (bzip2) is refused", True, str(e))
bad = bytearray(one.getvalue())
k = bad.index(b"PK\x01\x02") + 16                                 # the CRC in the central directory
bad[k:k + 4] = b"\x00\x00\x00\x00"
k = bad.index(b"PK\x03\x04") + 14                                 # ... and in the local header
bad[k:k + 4] = b"\x00\x00\x00\x00"
try:
    decode_bytes(bytes(bad))
    check(".gcode.3mf: a damaged member (bad CRC) is a permanent error, not retried", False)
except JobFileError as e:
    check(".gcode.3mf: a damaged member (bad CRC) is a permanent error, not retried", True, str(e)[:60])

# a settings line cut by the 300 KB head must not win over the whole line at the end (PrusaSlicer layout)
src = (ROOT / "tests" / "fixtures" / "sample.gcode").read_bytes()
at = src.index(b"; printer_model = ")
pad = 300_000 - 20 - at                         # the cut falls between "MK" and "4IS"
cutf = b"; " + b"p" * (pad - 3) + b"\n" + src
dc = JobDecoder()
for k in range(0, len(cutf), 65536):
    dc.feed(cutf[k:k + 65536])
cm = dc.finish().meta
check("plain .gcode: a settings line cut at the 300 KB mark doesn't win (printer_model)", cm.get("printer_model") == "MK4IS"
      and cm.get("bed_shape") == "0x0,250x0,250x210,0x210", f"{cm.get('printer_model')!r}")

ab = decode_bytes(bambu_gcode(2, "#FF000080;#00AE42FF"))
check("colours with alpha (#RRGGBBAA)", ab.info()["filament_colours"] == ["#FF0000", "#00AE42"], str(ab.info()["filament_colours"]))

print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
