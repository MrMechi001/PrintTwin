"""Regenerate the test jobs in tests/fixtures/ with PrusaSlicer (only needed when they should change).

A ring (30 mm, 6 mm hole, 3 mm high -> arcs, perimeters, infill, 15 layers) sliced with the stock
"Original Prusa MK4 Input Shaper 0.4 nozzle" profiles, once as binary .bgcode, once as plain .gcode.

usage: python -I tools/make_fixtures.py [path\\to\\prusa-slicer-console.exe]
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures"
PRINTER = "Original Prusa MK4 Input Shaper 0.4 nozzle"
PRINT = "0.20mm STRUCTURAL @MK4IS 0.4"
MATERIAL = "Prusament PLA @PGIS"


def ring_stl(path: Path, r_out=15.0, r_in=3.0, h=3.0, n=96):
    tris = []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        o0 = (r_out * math.cos(a0), r_out * math.sin(a0))
        o1 = (r_out * math.cos(a1), r_out * math.sin(a1))
        i0 = (r_in * math.cos(a0), r_in * math.sin(a0))
        i1 = (r_in * math.cos(a1), r_in * math.sin(a1))
        for z, up in ((0.0, False), (h, True)):         # top / bottom annulus
            q = [(*o0, z), (*o1, z), (*i1, z), (*i0, z)]
            tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3])] if up else [(q[0], q[2], q[1]), (q[0], q[3], q[2])]
        tris += [((*o0, 0), (*o1, 0), (*o1, h)), ((*o0, 0), (*o1, h), (*o0, h))]   # outer wall
        tris += [((*i0, 0), (*i1, h), (*i1, 0)), ((*i0, 0), (*i0, h), (*i1, h))]   # inner wall
    with open(path, "wb") as f:
        f.write(b"PrintTwin test ring".ljust(80, b" ") + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v) for v in t) + b"\0\0")


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "PRUSASLICER", r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        stl = Path(td) / "ring.stl"
        ring_stl(stl)
        base = [exe, "--printer-profile", PRINTER, "--print-profile", PRINT, "--material-profile", MATERIAL,
                "--center", "125,105", "--export-gcode"]
        for name, binary in (("sample.bgcode", True), ("sample.gcode", False)):
            # the MK4IS printer profile turns binary G-code on by itself -> set it explicitly both ways
            cmd = base + (["--binary-gcode"] if binary else ["--no-binary-gcode"]) + ["--output", str(OUT / name), str(stl)]
            subprocess.run(cmd, check=True, capture_output=True, input="N\n", text=True)
            print(f"wrote {OUT / name} ({(OUT / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
