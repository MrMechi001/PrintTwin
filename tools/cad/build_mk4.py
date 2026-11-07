"""Build web/models/mk4.glb: a true-to-size, animatable Original Prusa MK4.

Geometry
  * FokieH, "Prusa MK4 Model" (Printables 961911, GPL-2.0): complete MK4 assembly whose
    printed parts are Prusa's exact parts (volumes match the official STEP files to 0.01 cm3).
    It is a merged CATIA part without names/colours: bodies are identified below by index and
    verified by volume.
  * Prusa Research, "MK4/MK3.9 printable parts" (Printables 451501, GPL-2.0): used to locate
    the motor mounts (NEMA17 hole patterns) and to add the Nextruder main plate.
  * Added here from datasheet / official dimensions: motors, heatbed MK52 + steel sheet,
    spacers, linear bearings, belts, nozzle + heater block, LCD knob / reset button.

Frames
  FokieH: mm, X right, Y up, Z towards the front.  Output (= PrintTwin G-code frame): mm,
  X right, Y towards the back, Z up.  Groups:
    static    printer frame, nozzle plane at y = NOZZLE_Y
    bed       G-code bed coordinates (sheet surface z = 0, print area 0..250 x 0..210);
              the viewer slides it in Y under the nozzle
    xaxis     X gantry, local z relative to the nozzle: viewer sets position.z = Z
      carriage  extruder, local x relative to the nozzle: viewer sets position.x = X

usage:  python -I tools/cad/build_mk4.py
        (a Python with CadQuery 2.x / OCP 7.7+ and numpy, e.g. `pip install cadquery`;
         the viewer itself needs none of this - the built GLB is in the repo)
sources: put the two Printables downloads into cad_src/ (see the paths below).
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import cadquery as cq  # noqa: E402
from cadutil import bbox, placement, props, read_step  # noqa: E402
from glb import GlbWriter, tessellate  # noqa: E402
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

FOKIEH = ROOT / "cad_src/fokieh/MK4-COMPLETE_withSpoolHolder_20240913.stp"
PRUSA = ROOT / "cad_src/prusa_mk4_parts/extracted"
OUT = ROOT / "web/models/mk4.glb"

# ----------------------------------------------------------------------------- calibration
# Nozzle tip in FokieH (lowest point of the hotend body; the X axis sits at the top of Z travel).
NOZ = np.array([126.9, 309.4, 274.3])
SHEET_TOP = 88.4        # Y-carriage top 78.0 + spacer 6.4 + MK52 3.2 + sheet 0.8  -> nozzle Z = 221.0 (= firmware Z max)
X_OFF = 27.8            # G-code X = model X - X_OFF: centre of the X travel the x-ends allow -> X 125
BED_D0 = 306.7 + 110.7  # model depth of bed y = 0: carriage centre = heatbed centre = bed y 110.7 (PrusaSlicer MK4 bed)
NOZZLE_Y = 105.0        # viewer: the nozzle moves in the plane y = NOZZLE_Y
X_C = NOZ[0] - X_OFF    # G-code pose of the FokieH model
Z_C = NOZ[1] - SHEET_TOP
ROT = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])     # model (x, y, z) -> (x, -z, y)


def to_frame(P, group):
    v = P @ ROT.T
    if group == "bed":
        return v + np.array([-X_OFF, BED_D0, -SHEET_TOP], dtype=np.float32)
    v = v + np.array([-X_OFF, NOZZLE_Y + NOZ[2], -SHEET_TOP], dtype=np.float32)
    if group in ("xaxis", "carriage"):
        v[:, 2] -= Z_C
    if group == "carriage":
        v[:, 0] -= X_C
    return v


def model_to_static(p):
    return to_frame(np.asarray(p, dtype=np.float64)[None, :], "static")[0]


# ----------------------------------------------------------------------------- materials (stock look)
MATS = {
    "orange":   ((0.941, 0.337, 0.122), 0.0, 0.42),      # Prusa orange PETG ~#F0561F
    "petg":     ((0.161, 0.169, 0.169), 0.0, 0.5),       # black PETG #292B2B
    "pccf":     ((0.145, 0.150, 0.152), 0.0, 0.85),      # carbon-fibre PC, matte
    "frame":    ((0.140, 0.143, 0.150), 0.35, 0.72),     # black powder coat (viewer adds sparkle)
    "extrusion": ((0.105, 0.110, 0.118), 0.65, 0.42),    # black anodised
    "chrome":   ((0.90, 0.91, 0.93), 1.0, 0.12),
    "leadscrew": ((0.62, 0.64, 0.67), 0.95, 0.32),
    "steel":    ((0.55, 0.57, 0.60), 0.9, 0.35),
    "alu":      ((0.70, 0.72, 0.75), 0.9, 0.33),
    "anodised": ((0.10, 0.10, 0.11), 0.6, 0.45),         # Y-carriage
    "motor":    ((0.055, 0.056, 0.06), 0.25, 0.55),
    "pcb":      ((0.045, 0.047, 0.05), 0.2, 0.55),       # MK52 heatbed
    "sheet":    ((0.22, 0.225, 0.235), 0.55, 0.62),      # satin sheet (viewer adds grain)
    "psu":      ((0.075, 0.077, 0.082), 0.6, 0.55),      # perforated steel cover (viewer)
    "elbox":    ((0.085, 0.087, 0.093), 0.55, 0.5),
    "brass":    ((0.85, 0.64, 0.27), 1.0, 0.28),
    "sock":     ((0.10, 0.10, 0.105), 0.0, 0.8),         # silicone sock (viewer: glows when hot)
    "belt":     ((0.05, 0.05, 0.05), 0.0, 0.9),
    "fan":      ((0.06, 0.06, 0.065), 0.0, 0.6),
    "screen":   ((0.02, 0.02, 0.025), 0.0, 0.3),
    "red":      ((0.75, 0.08, 0.06), 0.0, 0.45),
    "rubber":   ((0.03, 0.03, 0.03), 0.0, 0.95),
}

# ----------------------------------------------------------------------------- FokieH bodies
# index: (name, group, material, expected volume cm3) - volumes cross-checked with Prusa's STEP parts
SOLIDS = {
    0: ("y-carriage", "bed", "anodised", None),
    1: ("y-rear-plate", "static", "extrusion", 83.12),
    2: ("frame", "static", "frame", 484.77),
    3: ("y-front-plate", "static", "extrusion", 60.88),
    4: ("y-extrusion-front-r", "static", "extrusion", 64.36),
    5: ("y-extrusion-front-l", "static", "extrusion", 64.36),
    6: ("y-extrusion-rear-l", "static", "extrusion", 37.67),
    7: ("y-extrusion-rear-r", "static", "extrusion", 37.67),
    8: ("z-top-right", "static", "orange", 16.40),
    9: ("z-bottom-left", "static", "orange", 44.63),
    10: ("z-bottom-right", "static", "orange", 44.63),
    11: ("z-top-left", "static", "orange", 16.40),
    12: ("y-motor-holder", "static", "petg", 19.44),
    13: ("y-belt-idler", "static", "petg", 14.27),
    14: ("y-rod-holder-rl", "static", "petg", 2.75),
    15: ("y-rod-holder-rr", "static", "petg", 2.75),
    16: ("y-rod-holder-fl", "static", "petg", 2.75),
    17: ("y-rod-holder-fr", "static", "petg", 2.75),
    18: ("z-rod-left", "static", "chrome", 25.53),
    19: ("z-rod-right", "static", "chrome", 25.53),
    20: ("z-leadscrew-left", "static", "leadscrew", 16.34),
    21: ("z-leadscrew-right", "static", "leadscrew", 16.34),
    22: ("y-rod-left", "static", "chrome", 16.59),
    23: ("y-rod-right", "static", "chrome", 16.59),
    24: ("y-belt-holder-tensioner", "bed", "pccf", 9.34),
    25: ("y-belt-holder", "bed", "pccf", 9.02),
    26: ("electronics-box", "static", "elbox", None),
    27: ("psu", "static", "psu", None),
    28: ("xlcd-support-left", "static", "petg", 10.55),
    29: ("xlcd-support-right", "static", "petg", 9.48),
    30: ("xlcd-cover", "static", "petg", 27.57),
    31: ("x-end-motor", "xaxis", "orange", 66.32),
    32: ("fan-shroud", "carriage", "pccf", 5.75),
    33: ("loveboard-cover", "carriage", "pccf", 9.43),
    34: ("loveboard-cover-right", "carriage", "pccf", 1.83),
    35: ("pg-case", "carriage", "petg", 5.84),
    36: ("x-carriage-clip", "carriage", "petg", 6.87),
    37: ("x-carriage", "carriage", "petg", 39.86),
    38: ("x-end-clip-r", "xaxis", "orange", 10.49),
    39: ("x-end-idler", "xaxis", "orange", 41.69),
    40: ("x-end-clip-l", "xaxis", "orange", 10.49),
    41: ("hotend-heatsink", "carriage", "fan", None),     # seen through the fan opening: dark like the real fan
    42: ("hotend-fan", "carriage", "fan", None),
    43: ("hotend", "carriage", "steel", None),
    44: ("x-carriage-cable-holder-cover", "carriage", "petg", 2.28),
    45: ("x-carriage-cable-holder", "carriage", "petg", 4.36),
    46: ("x-carriage-back", "carriage", "petg", 11.74),
    47: ("x-rod-top", "xaxis", "chrome", 18.60),
    48: ("x-rod-bottom", "xaxis", "chrome", 18.60),
    49: None,                                   # simplified spool -> built by the viewer (anchor)
}
SPOOL_HOLDER_SHELL = 199                      # the only closed surface body; the others are Nextruder details


# ----------------------------------------------------------------------------- generated parts
def mesh_of(shape, defl=0.08):
    if isinstance(shape, cq.Workplane):
        shape = cq.Compound.makeCompound([v for v in shape.vals() if isinstance(v, cq.Shape)])
    return tessellate(shape.wrapped if hasattr(shape, "wrapped") else shape, defl, 0.35)


def place(mesh, M, t):
    """mesh in a local frame -> model frame: p' = M p + t (M orthonormal, columns = local axes)."""
    P, N, I = mesh
    return (P @ M.T + t).astype(np.float32), (N @ M.T).astype(np.float32), I


def frame_for(axis):
    """Orthonormal M whose 3rd column is `axis` (local +z -> axis)."""
    z = np.asarray(axis, float) / np.linalg.norm(axis)
    a = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(a, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def nema17(length):
    """Local frame: mounting face at z=0, body towards -z, shaft towards +z."""
    body = cq.Workplane("XY").rect(42.3, 42.3).extrude(-length).edges("|Z").chamfer(4.6)
    boss = cq.Workplane("XY").circle(11.0).extrude(2.0)
    shaft = cq.Workplane("XY").circle(2.5).extrude(22.0)
    plug = cq.Workplane("XY").workplane(offset=-length + 4).center(0, -21.15 - 3.5).rect(16, 7).extrude(10)
    return {"motor": body.union(plug), "steel": boss.union(shaft)}       # Prusa's motors are black all over


def pulley(teeth_dia=12.2, width=7.0, flange=16.0):
    return (cq.Workplane("XY").circle(flange / 2).extrude(1.0)
            .union(cq.Workplane("XY").workplane(offset=1.0).circle(teeth_dia / 2).extrude(width))
            .union(cq.Workplane("XY").workplane(offset=1.0 + width).circle(flange / 2).extrude(1.0)))


def belt_loop(c0, c1, pitch_dia, width=6.0, thick=1.4):
    """GT2 belt loop around two pulleys whose axes are parallel to local z; c0/c1 = centres in local xy."""
    d = np.asarray(c1, float) - np.asarray(c0, float)
    L = float(np.hypot(*d))
    ang = float(np.degrees(np.arctan2(d[1], d[0])))
    outer = cq.Workplane("XY").slot2D(L + pitch_dia + thick, pitch_dia + thick, ang).extrude(width)
    inner = cq.Workplane("XY").slot2D(L + pitch_dia - thick, pitch_dia - thick, ang).extrude(width)
    return outer.cut(inner).translate(((c0[0] + c1[0]) / 2, (c0[1] + c1[1]) / 2, -width / 2))


def main():
    t0 = time.time()
    fk = read_step(FOKIEH)
    solids, shells = [], []
    ex = TopExp_Explorer(fk, TopAbs_SOLID)
    while ex.More():
        solids.append(ex.Current())
        ex.Next()
    ex = TopExp_Explorer(fk, TopAbs_SHELL, TopAbs_SOLID)
    while ex.More():
        shells.append(ex.Current())
        ex.Next()
    if len(solids) != 50 or len(shells) != 200:
        sys.exit(f"unexpected FokieH file: {len(solids)} solids / {len(shells)} shells (expected 50 / 200)")
    for i, spec in SOLIDS.items():
        if spec and spec[3] is not None:
            vol = props(solids[i])[0] / 1000
            if abs(vol - spec[3]) > 0.02:
                sys.exit(f"solid {i} ({spec[0]}): volume {vol:.2f} cm3, expected {spec[3]}")
    print(f"FokieH read + verified in {time.time() - t0:.1f} s")

    parts = []      # (name, group, material, mesh in MODEL frame) or (..., mesh, "bedframe") for bed-native meshes

    # FokieH's hotend is one 13 x 21 x 88 mm block that reaches down to the nozzle tip. Keep only the
    # part above the heater block (= heat sink inside the Nextruder); nozzle + sock are modelled below.
    lo_box = cq.Solid.makeBox(60, 18.0, 60, cq.Vector(NOZ[0] - 30, NOZ[1] - 1, NOZ[2] - 30))
    solids[43] = cq.Shape.cast(solids[43]).cut(lo_box).wrapped

    for i, spec in SOLIDS.items():
        if spec:
            parts.append((spec[0], spec[1], spec[2], tessellate(solids[i], 0.06, 0.3)))
    parts.append(("spool-holder", "static", "petg", tessellate(shells[SPOOL_HOLDER_SHELL], 0.1, 0.35)))
    details = [tessellate(s, 0.05, 0.3) for k, s in enumerate(shells) if k != SPOOL_HOLDER_SHELL]
    details = [d for d in details if d]
    if details:
        off, P, N, I = 0, [], [], []
        for p, n, i in details:
            P.append(p), N.append(n), I.append(i + off)
            off += len(p)
        parts.append(("nextruder-details", "carriage", "pccf2", (np.vstack(P), np.vstack(N), np.vstack(I))))

    # Nextruder main plate: designed in the same frame as the pg-case -> same placement
    pg = read_step(PRUSA / "Nextruder/pg-case-R2.stp")
    R, t, err = placement(pg, solids[35])
    mp = tessellate(read_step(PRUSA / "Nextruder/main-plate-r2.stp"), 0.05, 0.3)
    parts.append(("main-plate", "carriage", "pccf", place(mp, R, t)))
    e_face = R @ np.array([0, 0, 19.1]) + t            # extruder motor sits behind the main plate,
    e_dir = R @ np.array([0, 0, -1.0])                 # shaft pointing forward into the gearbox

    # ---------------------------------------------------------------- motors (positions from the parts' NEMA17 patterns)
    motors = [
        ("z-motor-left", "static", np.array([-19.75, 62.18, 264.25]), np.array([0, 1.0, 0]), 34),
        ("z-motor-right", "static", np.array([324.25, 62.17, 264.24]), np.array([0, 1.0, 0]), 34),
        ("y-motor", "static", np.array([145.16, 47.88, 131.5]), np.array([1.0, 0, 0]), 40),
        ("x-motor", "xaxis", np.array([-72.5, 345.88, 260.75]), np.array([0, 0, -1.0]), 34),
        ("e-motor", "carriage", e_face, e_dir, 23),
    ]
    for name, grp, face, shaft_dir, length in motors:
        M = frame_for(shaft_dir)
        for mat, shape in nema17(length).items():
            parts.append((f"{name}-{mat}", grp, mat, place(mesh_of(shape), M, face)))

    # pulleys + belts
    xm_axis = np.array([-72.5, 345.88])                # X motor axis (x, y) in model; belt plane z ~ 250.5
    x_idler = np.array([334.8, 345.88])
    Mz = frame_for([0, 0, 1.0])
    parts.append(("x-pulley", "xaxis", "alu", place(mesh_of(pulley()), Mz, np.array([*xm_axis, 246.0]))))
    parts.append(("x-idler", "xaxis", "alu", place(mesh_of(pulley(12.2, 7, 16)), Mz, np.array([*x_idler, 246.0]))))
    parts.append(("x-belt", "xaxis", "belt", place(mesh_of(belt_loop(xm_axis, x_idler, 12.2)), np.eye(3),
                                                    np.array([0, 0, 250.5]))))
    ym_axis = np.array([47.88, 131.5])                 # Y motor axis (y, z); belt plane x ~ 156.3
    y_idler = np.array([49.9, 470.0])
    Mx = np.column_stack([[0, 1.0, 0], [0, 0, 1.0], [1.0, 0, 0]])  # local x->Y, y->Z, z->X
    parts.append(("y-pulley", "static", "alu", place(mesh_of(pulley()), Mx, np.array([151.8, *ym_axis]))))
    parts.append(("y-idler", "static", "alu", place(mesh_of(pulley(12.2, 7, 16)), Mx, np.array([151.8, *y_idler]))))
    parts.append(("y-belt", "static", "belt", place(mesh_of(belt_loop(ym_axis, y_idler, 12.2)), Mx,
                                                     np.array([156.3, 0, 0]))))

    # linear bearings LM8UU under the Y-carriage (clamp-hole positions on the carriage)
    lm = cq.Workplane("XY").circle(7.5).extrude(24).translate((0, 0, -12)).faces(">Z or <Z").chamfer(0.6)
    for k, (x, z) in enumerate(((67.3, 271.65), (67.3, 341.65), (237.3, 306.65))):
        parts.append((f"y-bearing-{k}", "bed", "steel", place(mesh_of(lm), frame_for([0, 0, 1.0]), np.array([x, 64.4, z]))))

    # hotend: brass nozzle + heater block with silicone sock (tip exactly at the nozzle point)
    noz = (cq.Workplane("XY").circle(0.6).workplane(offset=1.6).circle(3.0).loft()
           .union(cq.Workplane("XY").workplane(offset=1.6).polygon(6, 8.1).extrude(3.0))
           .union(cq.Workplane("XY").workplane(offset=4.6).circle(3.0).extrude(2.0)))
    parts.append(("nozzle", "carriage", "brass", place(mesh_of(noz, 0.02), frame_for([0, 1.0, 0]), NOZ)))
    sock = cq.Workplane("XY").workplane(offset=5.6).rect(21, 17).extrude(12).edges("|Z").fillet(3)
    parts.append(("heater-sock", "carriage", "sock", place(mesh_of(sock, 0.05), frame_for([0, 1.0, 0]), NOZ + [1.5, 0, 0])))

    # LCD: knob + reset button on the tilted front face (normal (0, .766, .643))
    n_lcd = np.array([0, 0.766, 0.643])
    knob = cq.Workplane("XY").circle(11.5).extrude(9).faces(">Z").edges().fillet(1.2)
    parts.append(("lcd-knob", "static", "petg", place(mesh_of(knob, 0.04), frame_for(n_lcd),
                                                     np.array([206.9, 33.1, 512.8]))))
    btn = cq.Workplane("XY").circle(3.6).extrude(2.5).faces(">Z").edges().fillet(0.8)
    parts.append(("lcd-reset", "static", "red", place(mesh_of(btn, 0.03), frame_for(n_lcd), np.array([210.1, 45.7, 497.8]))))

    # ---------------------------------------------------------------- bed: MK52 + sheet, built in G-code bed coordinates
    bedparts = []
    sheet = (cq.Workplane("XY").center(125, 105).rect(252, 241).extrude(-0.8).edges("|Z").fillet(5.0))
    for x in (55.0, 195.0):        # rear V-notches over the alignment pins
        sheet = sheet.cut(cq.Workplane("XY").polyline([(x - 3.5, 225.6), (x + 3.5, 225.6), (x, 221.5)]).close()
                          .extrude(-2).translate((0, 0, 0.5)))
    bedparts.append(("sheet", "sheet", mesh_of(sheet, 0.05)))
    hb = cq.Workplane("XY").center(125, 110.7).rect(254, 264.4).extrude(-3.2).edges("|Z").fillet(3.0).translate((0, 0, -0.8))
    bedparts.append(("heatbed", "pcb", mesh_of(hb, 0.08)))
    for x in (55.0, 195.0):
        pin = cq.Workplane("XY").circle(2.5).extrude(2.0).translate((x, 223.5, -0.8))
        bedparts.append((f"bed-pin-{x:.0f}", "steel", mesh_of(pin, 0.03)))
    for x in (40.8, 150.8, 255.8):     # spacers on the carriage's heatbed holes
        for z in (201.7, 306.7, 411.7):
            g = to_frame(np.array([[x, 78.0, z]]), "bed")[0]
            sp = cq.Workplane("XY").polygon(6, 7.0).extrude(6.4).translate((g[0], g[1], -10.4))
            bedparts.append((f"spacer-{x:.0f}-{z:.0f}", "steel", mesh_of(sp, 0.03)))

    # ---------------------------------------------------------------- write GLB
    w = GlbWriter()

    def lin(c):     # MATS colours are sRGB; glTF baseColorFactor is linear
        return tuple(round(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92, 5) for v in c)

    mats = {k: w.material(k, lin(c), m, r) for k, (c, m, r) in MATS.items()}
    mats["pccf2"] = w.material("pccf2", lin(MATS["pccf"][0]), *MATS["pccf"][1:], double=True)   # open surface details
    g_static = w.node("static")
    g_bed = w.node("bed")
    g_x = w.node("xaxis")
    g_c = w.node("carriage", parent=g_x)
    groups = {"static": g_static, "bed": g_bed, "xaxis": g_x, "carriage": g_c}
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    ntri = 0
    for name, grp, mat, mesh in parts:
        if mesh is None:
            continue
        P, N, I = mesh
        P = to_frame(P.astype(np.float64), grp).astype(np.float32)
        N = (N @ ROT.T).astype(np.float32)
        if grp == "static":
            lo, hi = np.minimum(lo, P.min(0)), np.maximum(hi, P.max(0))
        ntri += len(I)
        w.node(name, w.mesh(name, P, N, I, mats[mat]), parent=groups[grp])
    for name, mat, (P, N, I) in bedparts:
        ntri += len(I)
        w.node(name, w.mesh(name, P, N, I, mats[mat]), parent=g_bed)

    # anchors for things the viewer animates or draws itself
    def lcd_corner(x, y, z):
        return (model_to_static(np.array([x, y, z]) - n_lcd * 0.6)).round(2).tolist()
    spool_c = model_to_static([179.7, 515.7, 180.2])
    extras = {
        "printtwin": {
            "version": 1, "units": "mm", "up": "z", "nozzle_y": NOZZLE_Y,
            "pose": {"x": round(float(X_C), 2), "y": round(float(BED_D0 - NOZ[2]), 2), "z": round(float(Z_C), 2)},
            "lcd_screen": [lcd_corner(115.71, 32.45, 513.58), lcd_corner(191.71, 32.45, 513.58),
                           lcd_corner(191.71, 64.27, 475.67), lcd_corner(115.71, 64.27, 475.67)],
            "spool": {"center": spool_c.round(2).tolist(), "axis": [1, 0, 0], "radius": 100.0, "width": 68.0,
                      "hub_radius": 27.0},
            "filament_inlet_carriage": (to_frame(np.array([[NOZ[0], 432.0, NOZ[2]]]), "carriage")[0]).round(2).tolist(),
            # extruder cable: leaves the back-left of the Nextruder, hangs in a loop in front of the frame
            # window and enters the electronics box (left, behind the frame) at its top
            "cable_start_carriage": (to_frame(np.array([[NOZ[0] - 30, 395.0, 246.0]]), "carriage")[0]).round(2).tolist(),
            "cable_end_static": model_to_static([-8.7, 198.0, 214.0]).round(2).tolist(),
            "bounds": [lo.round(1).tolist(), hi.round(1).tolist()],
            "sources": [
                "FokieH, 'Prusa MK4 Model', printables.com/model/961911, GPL-2.0",
                "Prusa Research, 'MK4/MK3.9 printable parts', printables.com/model/451501, GPL-2.0",
                "motors, bed, sheet, belts, bearings, nozzle: PrintTwin tools/cad/build_mk4.py (GPL-2.0)",
            ],
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    size = w.write(OUT, extras)
    gz = gzip.compress(OUT.read_bytes(), 6)
    OUT.with_name(OUT.name + ".gz").write_bytes(gz)
    print(f"wrote {OUT}: {size / 1e6:.1f} MB ({len(gz) / 1e6:.1f} MB gz), {ntri:,} triangles, "
          f"{len(parts) + len(bedparts)} parts in {time.time() - t0:.0f} s")
    print("pose of the source model (G-code):", extras["printtwin"]["pose"], "static bounds:", extras["printtwin"]["bounds"])


if __name__ == "__main__":
    main()
