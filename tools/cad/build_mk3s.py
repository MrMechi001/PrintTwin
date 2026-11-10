"""Build web/models/mk3s.glb: a true-to-size, animatable Original Prusa i3 MK3S+ (with the old, flat frame).

Geometry
  * Prusa Research, "Original-Prusa-i3", branch MK3S (github.com/prusa3d/Original-Prusa-i3, GPL-2.0;
    the SCAD headers say GPL v3): the frame drawing (Frame/MK3v8b.dxf, extruded here as the flat
    6.25 mm plate), the printed parts as STEP where Prusa publishes one, else as STL, and the SCAD
    sources, which give the parts' features (rod holes, screw holes) in the STL's own frame.
  * Prusa Research, "i3 MK3S+ printable parts" (printables.com/model/57217, GPL-2.0): the current
    revisions of the parts (x-ends, x-carriage, Raspberry Pi Zero frame, ...).
  * FokieH, "Prusa MK4 Model" (printables.com/model/961911, GPL-2.0): the MK4 keeps the MK3S+'s
    Y axis, so its Y plates, extrusions, Y rods, Y-carriage and power supply are reused; its frame
    body (the same plate with ribs added) places Prusa's drawing: 43 holes agree to 0.02 mm.
  * Where the other parts sit: Jonathan Kayne, "Prusa i3 MK3S (SolidWorks with STEP)"
    (grabcad.com/library/prusa-i3-mk3s-solidworks-with-step-1, also thingiverse.com/thing:3434917) -
    a measuring reference only: Prusa's parts were registered on it and the poses are written below;
    the motors, fans, hotend, boards and belts are PrintTwin's own simplified models sized from it.
    None of its geometry is in the model.
  * Firmware (Prusa-Firmware 3.14, variants/MK3S.h): X 0..255, Y -4..212.5, Z 0.15..210, probe
    offset (23, 5).
  * The engraved PRUSA lettering of the frame and the LCD cover's are left out (no logos).

Frames
  Model frame = FokieH's: mm, X right, Y up, Z towards the front (frame front face z = 235.75).
  Output (= PrintTwin G-code frame), groups as in build_mk4.py / build_mini.py:
    static    printer frame; the nozzle moves in the plane y = NOZZLE_Y
    bed       G-code bed coordinates (sheet surface z = 0, print area 0..250 x 0..210)
    xaxis     X axis, local z relative to the nozzle
      carriage  print head, local x relative to the nozzle

usage:  python -I tools/cad/build_mk3s.py [--out PATH]
        (a Python with CadQuery 2.x / OCP 7.7+, numpy and ezdxf; the viewer needs none of this)
sources: cad_src/prusa_mk3s (GitHub zip, unpacked), cad_src/prusa_mk3s_printables (Printables pack),
         cad_src/fokieh (as for build_mk4.py). The reference assembly is not needed to build.
"""
from __future__ import annotations

import gzip
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import cadquery as cq  # noqa: E402
import ezdxf  # noqa: E402
from build_mk4 import MATS, frame_for, mesh_of, place  # noqa: E402
from mk3s_hw import anchors as hw_anchors, drive, electronics, fans, hotend, motors  # noqa: E402
from cadutil import read_step  # noqa: E402
from glb import GlbWriter, tessellate  # noqa: E402
from OCP.TopAbs import TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

SRC = ROOT / "cad_src"
GH = SRC / "prusa_mk3s/Original-Prusa-i3-MK3S"
PR = SRC / "prusa_mk3s_printables"
FOKIEH = SRC / "fokieh/MK4-COMPLETE_withSpoolHolder_20240913.stp"
OUT = ROOT / "web/models/mk3s.glb"

# ----------------------------------------------------------------------------- frame and Z axis (model frame)
DXF_OFF = np.array([-32.75, 22.4])      # Prusa's frame drawing (x, y) -> model (x, y)
FRAME_Z0, FRAME_Z1 = 229.5, 235.75       # plate: rear face (rear Y extrusions end), front face (front ones start)


def dxf_to_model(x, y, depth=0.0):
    """A point of the frame drawing, `depth` mm in front of the frame's front face."""
    return np.array([x + DXF_OFF[0], y + DXF_OFF[1], FRAME_Z1 + depth])


# Z axis from the SCAD sources + the drawing: z-axis-bottom/-top are designed with x = distance from
# the frame's front face, z pointing down; their frame-screw patterns sit on the drawing's holes
# (bottom: (10|30, 39.8) + (20, 19.8), top: (10|30, 359.8) and mirrored on the right).
Z_ROD_DEPTH = 25 + 4.3                  # rod and leadscrew axes in front of the frame
Z_ROD_X = (3.0, 367.0)                   # drawing x of the Z rods (364 mm apart) ...
Z_BOTTOM_TOP = 51.8                      # drawing y of the z-bottom plate's top face (SCAD z = 0)
Z_ROD_LO = Z_BOTTOM_TOP - 5.5            # rods (8 x 320) stand in a 5.5 mm deep pocket
R_ZDOWN = np.array([[0, -1.0, 0], [0, 0, -1.0], [1.0, 0, 0]])   # SCAD (x, y, z) -> model (-y, -z, x)


def zpart_t(side, scad_y_to_dxf_x0, plate_top_y):
    """Translation for a z-axis part: model = R_ZDOWN @ scad + t, with drawing x = c - scad y."""
    c = scad_y_to_dxf_x0
    return np.array([c + DXF_OFF[0], plate_top_y + DXF_OFF[1], FRAME_Z1])


Z_PARTS = [   # (name, STL, which component (by y: 'right' y >= -6.5 / 'left'), material, t)
    ("z-bottom-right", "z-axis-bottom", "right", "orange", zpart_t("r", 370.0, Z_BOTTOM_TOP)),
    ("z-bottom-left", "z-axis-bottom", "left", "orange", zpart_t("l", -13.0, Z_BOTTOM_TOP)),
]

# ----------------------------------------------------------------------------- FokieH bodies kept (shared with the MK4)
SHARED = {
    4: ("y-extrusion-front-r", "static", "extrusion"),
    5: ("y-extrusion-front-l", "static", "extrusion"),
    6: ("y-extrusion-rear-l", "static", "extrusion"),
    7: ("y-extrusion-rear-r", "static", "extrusion"),
    22: ("y-rod-left", "static", "chrome"),
    23: ("y-rod-right", "static", "chrome"),
}

# Y-carriage underside (the MK4 and the reference agree) and thickness: the old flat carriage is drawn inside
# the frame window of the frame drawing, i.e. cut from the same plate - taken as the frame's 6.25 mm (to measure)
CARRIAGE_Y0, CARRIAGE_T = 69.97, 6.25
PLATE_T = 6.25           # front / rear Y plates: no thickness published - taken as the frame's (to measure)
SPACER_H = 6.0           # the 9 "6x6x3t" spacers between the Y-carriage and the MK52 (manual; reference: 6.0)
MK52_T, SHEET_T = 3.2, 0.8

# Prusa STEP parts placed on the MK4 assembly's counterparts (ICP over the 24 axis-aligned starts; rms of
# matched surface points, fraction of the part's surface within 0.3 mm): (name, file, group, material, R, t)
ON_MK4 = [
    ("y-rod-holder-rl", "y-rod-holder", "static", "petg", [[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [67.27, 57.38, 119.49]),
    ("y-rod-holder-rr", "y-rod-holder", "static", "petg", [[1, 0, 0], [0, -1, 0], [0, 0, -1]], [237.17, 57.39, 109.50]),
    ("y-rod-holder-fl", "y-rod-holder", "static", "petg", [[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [67.28, 57.38, 440.74]),
    ("y-rod-holder-fr", "y-rod-holder", "static", "petg", [[1, 0, 0], [0, -1, 0], [0, 0, -1]], [237.26, 57.38, 430.76]),
]   # rms 0.30 mm, 79 % within 0.3 mm (the MK4's holder is a slightly different revision)

# Prusa's MK3S+ parts registered on the reference assembly's counterparts (which is in this frame as it
# comes: its frame plate fits ours with no shift), at the reference's pose of the X axis and carriage:
# model = R @ part + t; "file#k" = the k-th piece of a multi-piece STL (pieces sorted by lowest y).
# Comments: reference part, fraction of our surface / of the reference's within 0.3 mm, rms. Fits below
# ~0.8 are newer MK3S+ revisions of the reference's MK3S parts (extruder: SuperPINDA mount, ...).
GHS = "prusa_mk3s/Original-Prusa-i3-MK3S/Printed-Parts/STEP/"
PRS = "prusa_mk3s_printables/"
REF_POSES = [   # (name, source, group, material, R, t)
    # print head (black: the two parts Prusa prints in black PETG; the rest orange)
    ("extruder-body", GHS + "extruder-body.stp", "carriage", "petg", [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [68.959, 335.204, 261.687]),  # 0.66/0.70, 1.01
    ("extruder-motor-plate", GHS + "extruder-motor-plate.stp", "carriage", "petg", [[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [22.938, 371.68, 289.717]),  # 0.67/0.70, 0.99
    ("extruder-cover", GHS + "extruder-cover.stp", "carriage", "orange", [[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [41.041, 366.279, 313.682]),  # 0.53/0.59, 1.48
    ("extruder-idler", GHS + "extruder-idler.stp", "carriage", "orange", [[0, 0, -1], [0, -1, 0], [-1, 0, 0]], [111.037, 387.118, 278.722]),  # 0.79/0.82, 0.77
    ("print-fan-support", GHS + "print-fan-support.stp", "carriage", "orange", [[0, 0, -1], [0, 1, 0], [1, 0, 0]], [65.346, 343.387, 365.301]),  # 0.76/0.78, 0.71
    ("fs-lever", GHS + "fs-lever.stp", "carriage", "orange",
     [[-0.982636, 0.185542, 0.000432], [0.185541, 0.982614, 0.006686], [0.000816, 0.00665, -0.999978]], [59.22, 371.103, 260.653]),  # 0.83/0.89, 0.63 (lever swung by the filament)
    ("adapter-printer", GHS + "adapter-printer.stp", "carriage", "orange", [[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [81.425, 392.455, 274.102]),  # 0.68/0.52, 0.58
    ("x-carriage", PRS + "E-Axis/x-carriage.stl", "carriage", "orange", [[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [51.975, 319.692, 261.701]),  # 0.95/0.91, 0.44
    ("x-carriage-back", PRS + "E-Axis/x-carriage-back.stl", "carriage", "orange", [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [68.968, 344.688, 261.805]),  # 0.94/0.88, 0.39
    ("cable-holder", PRS + "E-Axis/cable-holder.stl", "carriage", "orange", [[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [68.985, 344.676, 262.702]),  # 0.98/0.98, 0.35
    # MK3S+ revisions that differ a lot from the reference's MK3S parts: the best axis-aligned pose of all 28
    # starts (its centre within 2 mm of the reference part's)
    ("fan-shroud", GHS + "fan-shroud.stp", "carriage", "orange", [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [66.49, 305.084, 297.697]),  # 0.42/0.49, 1.45
    ("fs-cover", GHS + "fs-cover.stp", "carriage", "orange", [[1, 0, 0], [0, 0, -1], [0, 1, 0]], [69.215, 401.275, 244.721]),  # 0.48/0.42, 0.80
    # X axis
    ("x-end-motor", PRS + "X-Axis/x-end-motor.stl", "xaxis", "orange", [[0, -1, 0], [0, 0, -1], [1, 0, 0]], [-29.719, 370.676, 265.057]),  # 0.93/0.92, 0.68
    ("x-end-idler", PRS + "X-Axis/x-end-idler.stl", "xaxis", "orange", [[0, -1, 0], [0, 0, -1], [1, 0, 0]], [334.247, 370.693, 265.057]),  # 0.95/0.94, 0.47
    # Z tops and screw covers (the bottoms are placed from the frame drawing, Z_PARTS: the MK3S+ revision
    # differs from the reference's too much to register)
    ("z-top-left", PRS + "Z-Axis/z-axis-top.stl#0", "static", "orange", [[0, -1, 0], [0, 0, -1], [1, 0, 0]], [-44.756, 392.179, 235.752]),  # 0.99/0.88, 0.35
    ("z-top-right", PRS + "Z-Axis/z-axis-top.stl#1", "static", "orange", [[0, -1, 0], [0, 0, -1], [1, 0, 0]], [337.242, 392.192, 235.742]),  # 0.99/0.88, 0.36
    ("z-screw-cover-left", PRS + "Z-Axis/z-screw-cover.stl", "static", "orange",
     [[-0.065507, 0.997852, -0.000649], [-0.000732, 0.000603, 1.0], [0.997852, 0.065507, 0.000691]], [-12.744, 71.182, 265.051]),  # 1.00/1.00, 0.15
    ("z-screw-cover-right", PRS + "Z-Axis/z-screw-cover.stl", "static", "orange",
     [[-0.052901, 0.9986, 0.000436], [0.000795, -0.000394, 1.0], [0.998599, 0.052901, -0.000773]], [317.262, 71.178, 265.051]),  # 1.00/1.00, 0.15
    # Y axis
    ("y-motor-holder", GHS + "y-motor-holder.stp", "static", "orange", [[0, 0, 1], [0, 1, 0], [-1, 0, 0]], [139.268, 46.372, 130.499]),  # 0.97/0.97, 0.35
    ("y-belt-idler", GHS + "y-belt-idler.stp", "static", "orange", [[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [152.262, 47.677, 421.749]),  # 0.98/0.97, 0.35
    ("y-belt-holder", GHS + "y-belt-holder.stp", "bed", "orange", [[0, 0, -1], [0, -1, 0], [-1, 0, 0]], [152.301, 69.973, 306.674]),  # 0.99/0.99, 0.34
    ("y-belt-tensioner", GHS + "y-belt-tensioner.stp", "bed", "orange", [[0, 0, -1], [0, -1, 0], [-1, 0, 0]], [152.239, 69.968, 303.702]),  # 0.99/0.99, 0.34
    ("heatbed-cable-cover", PRS + "Heatbed & PSU/heatbed-cable-cover.stl", "bed", "orange", [[-1, 0, 0], [0, 0, -1], [0, -1, 0]], [42.33, 79.113, 189.221]),  # 0.65/0.68, 0.97
    ("heatbed-cable-clip", PRS + "Heatbed & PSU/heatbed-cable-cover-clip.stl", "bed", "orange", [[1, 0, 0], [0, 0, 1], [0, -1, 0]], [42.296, 79.219, 191.16]),  # 0.63/0.75, 1.29 (second pass)
    # electronics box (rear left), LCD (front), spool holder (frame top)
    ("einsy-doors", PRS + "Electronics/Einsy-doors.stl", "static", "orange", [[0, 0, -1], [-1, 0, 0], [0, 1, 0]], [17.158, 201.668, 136.991]),  # 0.96/0.96, 0.52
    # the MK3S+ base differs from the reference's: fitted on the reference base + doors + hinges (0.46 of
    # its surface on them), its frame-side face put on the frame's rear face (z 229.5)
    ("einsy-base", PRS + "Electronics/Einsy-base.stl", "static", "orange", [[0, 0, 1], [-1, 0, 0], [0, -1, 0]], [-32.91, 199.13, 229.5]),
    ("einsy-hinge-top", PRS + "Electronics/Einsy-hinges.stl#0", "static", "orange", [[0, 1, 0], [0, 0, -1], [-1, 0, 0]], [12.624, 201.179, 224.52]),  # 1.00/1.00, 0.21
    ("einsy-hinge-bottom", PRS + "Electronics/Einsy-hinges.stl#1", "static", "orange", [[0, -1, 0], [0, 0, 1], [-1, 0, 0]], [18.627, 86.164, 224.508]),  # 1.00/1.00, 0.17
    ("lcd-cover", PRS + "LCD/LCD-cover-ORIGINAL-MK3.stl", "static", "orange",
     [[1.0, 0.000184, -5.6e-05], [9.1e-05, -0.707564, -0.706649], [-0.00017, 0.706649, -0.707564]], [151.556, 63.976, 485.938]),  # 0.81/0.80, 0.68 (45 deg)
    ("lcd-support-left", PRS + "LCD/lcd-supports.stl#0", "static", "orange", [[0, 0, -1], [0, -1, 0], [-1, 0, 0]], [97.264, 14.4, 446.49]),  # 0.99/0.99, 0.36
    ("lcd-support-right", PRS + "LCD/lcd-supports.stl#0", "static", "orange", [[0, 0, -1], [0, -1, 0], [-1, 0, 0]], [217.278, 14.395, 446.486]),  # 0.99/0.99, 0.36
    ("spool-holder", PRS + "Other/Spool-holder.stl", "static", "orange", [[0, 0, 1], [0, 1, 0], [-1, 0, 0]], [86.148, 379.502, 561.581]),  # 0.99/0.99, 0.58
]
REF_NOZZLE = np.array([68.95, 297.9, 278.7])     # the reference's nozzle tip: the pose all REF_POSES are at
# LCD knob: Prusa's STEP carries three print tabs, so the knob (20 mm, 8 mm high) is generated, on the
# reference's encoder axis (outwards, 45 deg like the cover) with its back face where the reference's is
KNOB_AXIS = np.array([0.0065, 0.6986, 0.7155])
KNOB_BASE = np.array([212.44, 53.74, 502.31]) - 3.56 * KNOB_AXIS
X_RODS = ((319.685, 250.05), (364.685, 250.05))  # (y, z) of the X rods at the reference pose; x -36.74..333.26

# ----------------------------------------------------------------------------- calibration (G-code)
SHEET_TOP = CARRIAGE_Y0 + CARRIAGE_T + SPACER_H + MK52_T + SHEET_T     # 86.22: sheet surface (G-code Z 0)
BED_D0 = 306.7 + 110.7   # model depth of bed y = 0 (carriage centre = MK52 centre = bed y 110.7), as the MK4
X_OFF = 150.8 - 125.0    # G-code X = model X - X_OFF: the MK52's centre hole on the carriage's (x 150.8) is X 125
NOZZLE_Y = 105.0
NOZ = np.array(hotend.ANCHORS["nozzle_tip"])   # the nozzle tip at the build pose (= the reference's pose)
ROT = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])     # model (x, y, z) -> (x, -z, y)


def to_frame(P, group):
    v = np.asarray(P, dtype=np.float64) @ ROT.T
    if group == "bed":
        return v + np.array([-X_OFF, BED_D0, -SHEET_TOP])
    v = v + np.array([-X_OFF, NOZZLE_Y + NOZ[2], -SHEET_TOP])
    if group in ("xaxis", "carriage"):
        v[:, 2] -= NOZ[1] - SHEET_TOP
    if group == "carriage":
        v[:, 0] -= NOZ[0] - X_OFF
    return v


# ----------------------------------------------------------------------------- helpers
def stl_components(path):
    """Binary/ASCII STL -> list of connected components, each (P, N, I) with flat normals, sorted by min y."""
    b = Path(path).read_bytes()
    n = int.from_bytes(b[80:84], "little")
    if len(b) == 84 + 50 * n:
        a = np.frombuffer(b, dtype=np.uint8, offset=84, count=50 * n).reshape(n, 50)
        tri = a[:, 12:48].copy().view(np.float32).reshape(n, 3, 3).astype(np.float64)
    else:
        v = [list(map(float, ln.split()[1:4])) for ln in b.decode("ascii", "replace").splitlines()
             if ln.strip().startswith("vertex")]
        tri = np.array(v, dtype=np.float64).reshape(-1, 3, 3)
    # connected components over shared (rounded) vertices
    key = np.round(tri.reshape(-1, 3), 3)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    parent = np.arange(vid.max() + 1)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a_, b_, c_ in vid:
        ra, rb, rc = find(a_), find(b_), find(c_)
        parent[rb] = ra
        parent[rc] = ra
    roots = np.array([find(i) for i in vid[:, 0]])
    comps = []
    for r in np.unique(roots):
        t = tri[roots == r]
        nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        P = t.reshape(-1, 3).astype(np.float32)
        N = np.repeat(nrm, 3, axis=0).astype(np.float32)
        I = np.arange(len(P), dtype=np.uint32).reshape(-1, 3)
        comps.append((P, N, I))
    comps.sort(key=lambda m: float(m[0][:, 1].min()))
    return comps


_PRUSA = {}


def prusa_mesh(src):
    """A Prusa part in its file's own frame: "<path>.stp" whole, "<path>.stl#k" its k-th piece (pieces of
    more than 60 triangles, sorted by lowest y - as registered), "<path>.stl" a one-piece STL."""
    path, _, k = src.partition("#")
    if path not in _PRUSA:
        f = SRC / path
        _PRUSA[path] = ([tessellate(read_step(f), 0.05, 0.3)] if f.suffix.lower() in (".stp", ".step")
                        else [c for c in stl_components(f) if len(c[2]) > 60])
    pieces = _PRUSA[path]
    if k:
        return pieces[int(k)]
    if len(pieces) != 1:
        raise ValueError(f"{src}: {len(pieces)} pieces - say which (#k)")
    return pieces[0]


def merge(meshes):
    off, P, N, I = 0, [], [], []
    for p, n_, i in meshes:
        P.append(p), N.append(n_), I.append(i + off)
        off += len(p)
    return np.vstack(P), np.vstack(N), np.vstack(I).astype(np.uint32)


def cyl(r, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def along(axis, origin, shape):
    return place(mesh_of(shape), frame_for(axis), np.asarray(origin, dtype=float))


def bspline_points(e, step=0.25):
    """Points along a (non-rational) DXF SPLINE, without ezdxf's flattening."""
    from scipy.interpolate import BSpline
    cp = np.array(e.control_points)[:, :2]
    kn = np.array(list(e.knots))
    k = e.dxf.degree
    s = BSpline(kn, cp, k)
    t = np.linspace(kn[k], kn[-k - 1], max(200, int((kn[-k - 1] - kn[k]) / step)))
    return s(t)


def simplify(P, tol=1e-3):
    """Drop collinear points of a closed polyline (the splines encode straight edges and 45 degree chamfers)."""
    P = np.asarray(P)
    keep = []
    n = len(P)
    for i in range(n):
        a, b, c = P[i - 1], P[i], P[(i + 1) % n]
        if abs(np.cross(b - a, c - b)) > tol * max(np.linalg.norm(b - a) * np.linalg.norm(c - b), 1e-9):
            keep.append(b)
    return np.array(keep)


def dxf_loops(entities, tol=0.01):
    """Chain LINE / ARC / CIRCLE / LWPOLYLINE entities into closed loops (arrays of 2D points)."""
    loops, open_ = [], []
    for e in entities:
        t = e.dxftype()
        if t == "LINE":
            s = np.array([[e.dxf.start.x, e.dxf.start.y], [e.dxf.end.x, e.dxf.end.y]])
        elif t in ("ARC", "CIRCLE"):
            c, r = np.array([e.dxf.center.x, e.dxf.center.y]), e.dxf.radius
            a0, a1 = (np.radians(e.dxf.start_angle), np.radians(e.dxf.end_angle)) if t == "ARC" else (0.0, 2 * np.pi)
            if a1 <= a0:
                a1 += 2 * np.pi
            a = np.linspace(a0, a1, max(8, int((a1 - a0) * r / 0.3) + 1))
            s = c + r * np.c_[np.cos(a), np.sin(a)]
        elif t == "LWPOLYLINE":
            s = np.array(e.get_points("xy"))
            if e.closed:
                s = np.vstack([s, s[:1]])
        else:
            continue
        if len(s) > 2 and np.linalg.norm(s[0] - s[-1]) < tol:
            loops.append(s[:-1])                 # closed by itself (circle, closed polyline)
        elif np.linalg.norm(s[0] - s[-1]) >= tol:
            open_.append(s)
    key = lambda p: (round(float(p[0]) / tol), round(float(p[1]) / tol))  # noqa: E731
    ends = {}
    for k, s in enumerate(open_):
        ends.setdefault(key(s[0]), []).append((k, 0))
        ends.setdefault(key(s[-1]), []).append((k, 1))
    used = set()
    for k0 in range(len(open_)):
        if k0 in used:
            continue
        used.add(k0)
        chain = list(open_[k0])
        start = key(chain[0])
        while key(chain[-1]) != start:
            nxt = [(k, side) for k, side in ends.get(key(chain[-1]), []) if k not in used]
            if not nxt:
                break
            k, side = nxt[0]
            used.add(k)
            s = open_[k] if side == 0 else open_[k][::-1]
            chain.extend(s[1:])
        if key(chain[-1]) == start and len(chain) > 3:
            loops.append(np.array(chain[:-1]))
    return loops


def plate_from_loops(loops, thickness):
    """Largest loop = outline, every other loop inside it = hole; extruded along z from 0."""
    loops = sorted(loops, key=lambda p: -np.ptp(p[:, 0]) * np.ptp(p[:, 1]))
    wire = lambda P: cq.Workplane("XY").polyline([tuple(map(float, p)) for p in P]).close()  # noqa: E731
    body = wire(loops[0]).extrude(thickness)
    for h in loops[1:]:
        body = body.cut(wire(h).extrude(thickness))
    return body


def flat_frame():
    """The MK3S frame plate from Prusa's drawing: outer outline - window - holes, 6.25 mm thick."""
    doc = ezdxf.readfile(GH / "Frame/MK3v8b.dxf")
    msp = doc.modelspace()
    loops = sorted((bspline_points(e) for e in msp if e.dxftype() == "SPLINE" and e.dxf.layer == "0"),
                   key=lambda p: -np.ptp(p[:, 0]) * np.ptp(p[:, 1]))
    outer, window = (simplify(p[:-1]) for p in loops[:2])
    wire = lambda P: cq.Workplane("XY").polyline([tuple(map(float, p)) for p in P]).close()  # noqa: E731
    plate = wire(outer).extrude(FRAME_Z1 - FRAME_Z0).cut(wire(window).extrude(FRAME_Z1 - FRAME_Z0))
    from matplotlib.path import Path as MPath
    win = MPath(window)
    holes = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in msp if e.dxftype() == "CIRCLE"]
    holes = [h for h in holes if not win.contains_point(h[:2])]          # the Y-carriage drawn in the window
    for x, y, r in holes:
        plate = plate.cut(cq.Workplane("XY").center(x, y).circle(r).extrude(FRAME_Z1 - FRAME_Z0))
    return plate.translate((DXF_OFF[0], DXF_OFF[1], FRAME_Z0)), len(holes)


def main():
    t0 = time.time()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT
    parts = []      # (name, group, material, mesh in the model frame)

    def add(name, group, mat, mesh):
        parts.append((name, group, mat, mesh))

    # ---------------------------------------------------------------- frame (Prusa's drawing) + shared MK4 bodies
    frame, nholes = flat_frame()
    add("frame", "static", "frame", mesh_of(frame, 0.05))
    print(f"frame plate from the drawing: {nholes} holes, {time.time() - t0:.1f} s")
    fk = read_step(FOKIEH)
    solids = []
    ex = TopExp_Explorer(fk, TopAbs_SOLID)
    while ex.More():
        solids.append(ex.Current())
        ex.Next()
    for i, (name, grp, mat) in SHARED.items():
        add(name, grp, mat, tessellate(solids[i], 0.06, 0.3))
    print(f"FokieH shared bodies in {time.time() - t0:.1f} s")

    # Y plates and Y-carriage from Prusa's drawings (the MK4's front plate is perforated, the MK3S's plain),
    # at the MK4 bodies' places (same outlines; 17 of the carriage's 19 holes are the MK4 carriage's too)
    # the plates' inner faces touch the extrusion ends (rear z 109.5, front z 440.75)
    for name, dxf, (x, y, z) in (("y-rear-plate", "MK3-rear-plate.dxf", (152.26, 20.38, 109.5 - PLATE_T)),
                                 ("y-front-plate", "MK3-front-plate.dxf", (152.27, 67.38, 440.75))):
        loops = dxf_loops(ezdxf.readfile(GH / "Frame" / dxf).modelspace())
        add(name, "static", "extrusion", place(mesh_of(plate_from_loops(loops, PLATE_T), 0.05), np.eye(3), np.array([x, y, z])))
    msp = ezdxf.readfile(GH / "Frame/MK3v8b.dxf").modelspace()
    from matplotlib.path import Path as MPath
    win = MPath(sorted((bspline_points(e) for e in msp if e.dxftype() == "SPLINE" and e.dxf.layer == "0"),
                       key=lambda p: -np.ptp(p[:, 0]) * np.ptp(p[:, 1]))[1])

    def in_window(e):
        t = e.dxftype()
        if t not in ("LINE", "ARC", "CIRCLE") or e.dxf.layer not in ("0", "diry a zavity"):
            return False
        p = (e.dxf.start.x, e.dxf.start.y) if t == "LINE" else (e.dxf.center.x, e.dxf.center.y)
        return win.contains_point(p)
    car = plate_from_loops(dxf_loops([e for e in msp if in_window(e)]), CARRIAGE_T)
    R_car = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])     # drawing (x, y), thickness z -> model (x, -z, y)
    add("y-carriage", "bed", "anodised", place(mesh_of(car, 0.05), R_car, np.array([-29.241, CARRIAGE_Y0, 491.693])))

    for name, f, grp, mat, R, t in ON_MK4:
        add(name, grp, mat, place(tessellate(read_step(GH / f"Printed-Parts/STEP/{f}.stp"), 0.05, 0.3),
                                  np.array(R, dtype=float), np.array(t, dtype=float)))

    # ---------------------------------------------------------------- Prusa's parts at the reference pose
    for name, src, grp, mat, R, t in REF_POSES:
        add(name, grp, mat, place(prusa_mesh(src), np.array(R, dtype=float), np.array(t, dtype=float)))
    knob = (cq.Workplane("XY").circle(9.95).extrude(8.0).faces(">Z").edges().fillet(1.2)
            .cut(cq.Workplane("XY").workplane(offset=7.4).circle(6.5).extrude(1.0)))     # shallow dish on top
    add("lcd-knob", "static", "orange", place(mesh_of(knob, 0.04), frame_for(KNOB_AXIS / np.linalg.norm(KNOB_AXIS)), KNOB_BASE))

    # ---------------------------------------------------------------- Z axis
    for name, stl, side, mat, t in Z_PARTS:
        if not name.startswith("z-bottom"):
            continue                                     # the tops: REF_POSES
        comps = stl_components(GH / f"Printed-Parts/STL/{stl}.stl")
        big = [c for c in comps if len(c[2]) > 200]
        left, right = big[0], big[-1]                    # left part mirrored to negative y
        add(name, "static", mat, place(right if side == "right" else left, R_ZDOWN, t))
    for x in Z_ROD_X:
        add(f"z-rod-{x:.0f}", "static", "chrome",
            along([0, 1.0, 0], dxf_to_model(x, Z_ROD_LO, Z_ROD_DEPTH), cyl(4.0, 0, 320.0)))
    # ---------------------------------------------------------------- X axis (x-ends, carriage: REF_POSES)
    for k, (y, z) in zip(("bottom", "top"), X_RODS):
        add(f"x-rod-{k}", "xaxis", "chrome", along([1.0, 0, 0], [-36.74, y, z], cyl(4.0, 0, 370.0)))

    # ---------------------------------------------------------------- bed: MK52 + sheet in G-code bed coordinates
    bedparts = []
    sheet = cq.Workplane("XY").center(125, 105).rect(252, 241).extrude(-0.8).edges("|Z").fillet(5.0)
    for x in (55.0, 195.0):        # rear V-notches over the alignment pins
        sheet = sheet.cut(cq.Workplane("XY").polyline([(x - 3.5, 225.6), (x + 3.5, 225.6), (x, 221.5)]).close()
                          .extrude(-2).translate((0, 0, 0.5)))
    bedparts.append(("sheet", "sheet", mesh_of(sheet, 0.05)))
    hb = cq.Workplane("XY").center(125, 110.7).rect(254, 264.4).extrude(-3.2).edges("|Z").fillet(3.0).translate((0, 0, -0.8))
    bedparts.append(("heatbed", "pcb", mesh_of(hb, 0.08)))
    for x in (55.0, 195.0):
        bedparts.append((f"bed-pin-{x:.0f}", "steel", mesh_of(cyl(2.5, 0, 2.0).translate((x, 223.5, -0.8)), 0.03)))
    for x in (40.8, 150.8, 255.8):     # spacers on the carriage's heatbed holes
        for z in (201.7, 306.7, 411.7):
            g = to_frame(np.array([[x, CARRIAGE_Y0 + CARRIAGE_T, z]]), "bed")[0]
            sp = cq.Workplane("XY").polygon(6, 7.0).extrude(SPACER_H).translate((g[0], g[1], -(SPACER_H + MK52_T + SHEET_T)))
            bedparts.append((f"spacer-{x:.0f}-{z:.0f}", "steel", mesh_of(sp, 0.03)))

    # ---------------------------------------------------------------- hardware (PrintTwin, tools/cad/mk3s_hw)
    for mod in (hotend, fans, motors, drive, electronics, hw_anchors):
        for name, grp, mat, mesh in mod.build():
            add(name, grp, mat, mesh)
        print(f"{mod.__name__.split('.')[-1]} in {time.time() - t0:.0f} s")

    if "--dump" in sys.argv:           # the parts in the master frame (for registering a reference assembly)
        dump = Path(sys.argv[sys.argv.index("--dump") + 1])
        arrs = {}
        for name, grp, mat, (P, N, I) in parts:
            arrs[f"{grp}|{name}|P"] = np.asarray(P, dtype=np.float32)
            arrs[f"{grp}|{name}|I"] = np.asarray(I, dtype=np.int32)
        np.savez_compressed(dump, **arrs)
        print(f"dumped {len(parts)} parts to {dump}")

    # ---------------------------------------------------------------- write GLB
    w = GlbWriter()

    def lin(c):
        return tuple(round(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92, 5) for v in c)

    used = {p[2] for p in parts} | {b[1] for b in bedparts}
    allmats = dict(MATS)
    for mod in (hotend, fans, motors, drive, electronics, hw_anchors):
        allmats.update(getattr(mod, "NEW_MATS", {}))
    missing = used - set(allmats)
    assert not missing, f"materials missing: {missing}"
    mats = {k: w.material(k, lin(c), m, r) for k, (c, m, r) in allmats.items() if k in used}
    g_static, g_bed, g_x = w.node("static"), w.node("bed"), w.node("xaxis")
    g_c = w.node("carriage", parent=g_x)
    groups = {"static": g_static, "bed": g_bed, "xaxis": g_x, "carriage": g_c}
    ntri = 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    raw = "--raw" in sys.argv          # everything in the printer frame as built (for inspection)
    for name, grp, mat, mesh in parts:
        P, N, I = mesh
        P = to_frame(P, "static" if raw else grp).astype(np.float32)
        N = (np.asarray(N, dtype=np.float64) @ ROT.T).astype(np.float32)
        if grp == "static":
            lo, hi = np.minimum(lo, P.min(0)), np.maximum(hi, P.max(0))
        ntri += len(I)
        w.node(name, w.mesh(name, P, N, np.asarray(I, dtype=np.uint32), mats[mat]), parent=groups[grp])
    for name, mat, (P, N, I) in bedparts:
        ntri += len(I)
        if raw:
            P = (P + np.array([0, NOZZLE_Y + NOZ[2] - BED_D0, 0])).astype(np.float32)
        w.node(name, w.mesh(name, P, N, I, mats[mat]), parent=g_bed)
    A, E = hw_anchors.ANCHORS, electronics.ANCHORS
    sp = A["spool"]
    hd = A["cable_handles"]

    def st(p):
        return to_frame(np.array([p]), "static")[0].round(2).tolist()
    extras = {
        "printtwin": {
            "version": 1, "units": "mm", "up": "z", "nozzle_y": NOZZLE_Y, "model": "mk3s",
            "pose": {"x": round(float(NOZ[0] - X_OFF), 2), "y": round(float(BED_D0 - NOZ[2]), 2),
                     "z": round(float(NOZ[1] - SHEET_TOP), 2)},
            "lcd_screen": [st(c) for c in E["lcd_screen"]], "lcd_style": "char20x4", "lcd_portrait": False,
            "spool": {"center": st(sp["center"]), "axis": (ROT @ np.array(sp["axis"])).round(4).tolist(),
                      "radius": sp["radius"], "width": sp["width"], "hub_radius": sp["hub_radius"]},
            "filament_inlet_carriage": to_frame(np.array([A["filament_inlet"]]), "carriage")[0].round(2).tolist(),
            "cable_start_carriage": to_frame(np.array([A["cable_start"]]), "carriage")[0].round(2).tolist(),
            "cable_end_static": st(A["cable_end"]),
            "cable_radius": A["cable_radius"],
            "cable_handles": [(ROT @ np.array(hd["dir_start"]) * hd["len_start"]).round(2).tolist(),
                              (ROT @ np.array(hd["dir_end"]) * hd["len_end"]).round(2).tolist()],
            "bounds": [lo.round(1).tolist(), hi.round(1).tolist()],
            "sources": [
                "Prusa Research, 'Original Prusa i3 MK3S' printed parts and frame drawings, "
                "github.com/prusa3d/Original-Prusa-i3, GPL-2.0",
                "Prusa Research, 'Original Prusa i3 MK3S+ printable parts', printables.com/model/57217, GPL-2.0",
                "FokieH, 'Prusa MK4 Model', printables.com/model/961911, GPL-2.0 (Y extrusions and rods)",
                "part poses and hardware dimensions measured on Jonathan Kayne, 'Prusa i3 MK3S (SolidWorks with "
                "STEP)', grabcad.com/library/prusa-i3-mk3s-solidworks-with-step-1 (reference only, no geometry used)",
                "motors, hotend, fans, belts, bearings, electronics, bed, sheet: PrintTwin tools/cad (GPL-2.0)",
            ],
        }
    }
    print("pose:", extras["printtwin"]["pose"], "bounds:", extras["printtwin"]["bounds"])
    out.parent.mkdir(parents=True, exist_ok=True)
    size = w.write(out, extras)
    out.with_name(out.name + ".gz").write_bytes(gzip.compress(out.read_bytes(), 6))
    print(f"wrote {out}: {size / 1e6:.1f} MB, {ntri:,} triangles, {len(parts)} parts in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
