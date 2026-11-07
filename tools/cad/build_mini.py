"""Build web/models/mini.glb: a true-to-size, animatable Original Prusa MINI+.

Geometry
  * Prusa Research, "Original-Prusa-MINI" (github.com/prusa3d/Original-Prusa-MINI, GPL-3.0):
    every printed part plus the heatsink, Y-carriage, Z bottom plate and steel sheet are Prusa's
    own STEP files (STEP/). Prusa's drawings in the same repository (DOCUMENTATION/) give the
    30x30 extrusions, rods, LDO motors and the hotend (heatbreak, heater block, nozzle) that
    are generated below.
  * Where the parts sit: Luis, "STEP File for Prusa Mini" (printables.com/model/307054, public
    domain) is a SolidWorks assembly of Prusa's parts. Prusa's files share its part frames
    (median deviation 0.000 mm), so its placements are copied into POSES - that file is a
    reference only, nothing of it is in the model. Not in it / placed here: the SuperPINDA
    holder (newer part: firmware probe offset + its M3 hole in the x-carriage, 0.1 mm), the
    z-bottom cover (z-bottom frame), the steel sheet and the printed spool holder.
  * Firmware (Prusa-Firmware-Buddy, Configuration_MINI.h): bed 180 x 180, Z max 185, X homes
    to 180.4 against the Z carriage, Y to -3 against the rear plate, probe offset (-29, -3).
  * The front Y plate's engraved "ORIGINAL PRUSA MINI" lettering is filled (no logos).
  * Not modelled: Buddy board and wiring inside the base, the optional filament sensor,
    extruder internals.

Frames
  Model frame: mm, X right, Y towards the back, Z up (origin from the reference assembly).
  Output (= PrintTwin G-code frame), groups as in build_mk4.py:
    static    printer frame; the nozzle moves in the plane y = NOZZLE_Y
    bed       G-code bed coordinates (sheet surface z = 0, print area 0..180 x 0..180)
    xaxis     X axis and the extruder on the Z carriage; local z relative to the nozzle
      carriage  print head; local x relative to the nozzle

usage:  python -I tools/cad/build_mini.py
        (a Python with CadQuery 2.x / OCP 7.7+ and numpy; the viewer needs none of this)
sources: unzip github.com/prusa3d/Original-Prusa-MINI into cad_src/prusa_mini/extracted/.
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
from build_mk4 import MATS, belt_loop, fan_rotor, frame_for, mesh_of, nema17, place, pulley  # noqa: E402
from cadutil import read_step  # noqa: E402
from glb import GlbWriter, tessellate  # noqa: E402

STEP = ROOT / "cad_src/prusa_mini/extracted/Original-Prusa-MINI-master/STEP"
OUT = ROOT / "web/models/mini.glb"

# ----------------------------------------------------------------------------- calibration
# Nozzle tip: heatbreak axis of the heatsink; heatsink bottom 80.33 - 19.5 (the heatbreak, seated
# in the 25 mm bore, ends 7 mm below the heatsink; the 12.5 mm nozzle butts against it).
NOZ = np.array([41.09, -93.5, 60.83])
SHEET_TOP = 44.29 + 1.4  # heatbed top (3 mm plate on 10 mm standoffs) + Prusa's 1.4 mm sheet
# X and Y home sensorless against hard stops; the firmware calls those positions X 180.4
# (MANUAL_X_HOME_POS) and Y -3 (Y_MIN_POS). The stops were measured by sliding the parts as placed
# below along their rods with exact solid intersections:
#   print head  +X 82.11 mm until the x-carriage and its bearings touch the Z carriage
#               (-X 107.26 mm to the X end: X -2 leaves 7 mm)
#   bed         +Y 96.75 mm until the rear left bearing enters the rear plate's bearing pocket
#               (-Y 84.71 mm to the front plate: Y reaches 178.5 of the firmware's 180 there)
X_C = 180.4 - 82.11                     # G-code pose of the parts as placed below
Y_C = -3.0 + 96.75
Z_C = NOZ[2] - SHEET_TOP
PC = np.array([90 + NOZ[0] - X_C, 90 + NOZ[1] - Y_C])   # centre of the 180 x 180 print area
NOZZLE_Y = 90.0


def to_frame(P, group):
    v = np.asarray(P, dtype=np.float64) + np.array([90 - PC[0], 0, -SHEET_TOP])
    if group == "bed":
        v[:, 1] += 90 - PC[1]
        return v
    v[:, 1] += NOZZLE_Y - NOZ[1]
    if group in ("xaxis", "carriage"):
        v[:, 2] -= Z_C
    if group == "carriage":
        v[:, 0] -= X_C
    return v


def point(p, group):
    return to_frame(np.asarray(p, dtype=np.float64)[None, :], group)[0].round(2).tolist()


# ----------------------------------------------------------------------------- Prusa's parts
# (name, STEP file, group, material, R, t): model point = R @ part point + t
_I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
_YP = [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]          # Y plates, Y idler
_ZF = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]         # parts flipped upside down
_ZC = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]         # Z carriage rear, extruder
_XC = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]          # x-carriage, heatsink
_LCD_C, _LCD_S = 0.364511, 0.931199               # display box tilted back 21.4 degrees
POSES = [
    ("y-plate-front", "PRINTED PARTS/MINI-y-plate-front", "static", "orange", _YP, [92.399, -235.948, 20.704]),
    ("y-plate-rear", "PRINTED PARTS/MINI-y-plate-rear", "static", "orange", _YP, [92.399, 38.002, 20.704]),
    ("y-idler-holder", "PRINTED PARTS/MINI-y-idler", "static", "orange", _YP, [33.299, -223.998, 3.704]),
    ("z-bottom", "PRINTED PARTS/MINI-z-bottom", "static", "orange", _I, [164.324, -56.498, -23.271]),
    ("z-bottom-cover", "PRINTED PARTS/MINI-z-bottom-cover", "static", "orange", _I, [164.324, -56.498, 13.229]),
    ("z-bottom-cable-cover", "PRINTED PARTS/MINI-z-bottom-cable-cover", "static", "orange", _ZF, [192.651, -56.456, 13.229]),
    ("z-plate-bottom", "MECHANICAL PARTS/MINI-z-plate-bottom", "static", "anodised", _ZF, [164.324, -56.498, 16.729]),
    ("z-top", "PRINTED PARTS/MINI-z-top", "static", "orange", _ZF, [164.324, -56.498, 310.729]),
    ("display-box", "PRINTED PARTS/MINI-display-box", "static", "orange",
     [[1, 0, 0], [0, -_LCD_C, _LCD_S], [0, -_LCD_S, -_LCD_C]], [167.149, -213.328, 49.039]),
    ("lcd-knob", "PRINTED PARTS/MINI-knob", "static", "orange",
     [[-1, 0, 0], [0, _LCD_C, _LCD_S], [0, _LCD_S, -_LCD_C]], [167.149, -240.099, 5.34]),
    ("y-carriage", "MECHANICAL PARTS/MINI-y-carriage", "bed", "anodised", _I, [32.399, -99.027, 26.294]),
    ("y-belt-holder", "PRINTED PARTS/MINI-y-belt-holder", "bed", "orange", [[0, 1, 0], [-1, 0, 0], [0, 0, 1]], [24.399, -99.027, 26.294]),
    ("heatbed-cable-cover-bottom", "PRINTED PARTS/MINI-heatbed-cable-cover-bottom", "bed", "orange", _ZF, [107.899, 2.973, 41.294]),
    ("heatbed-cable-cover-top", "PRINTED PARTS/MINI-heatbed-cable-cover-top", "bed", "orange", _I, [107.899, 2.973, 43.794]),
    ("z-carriage-front", "PRINTED PARTS/MINI-z-carriage-front", "xaxis", "orange", [[1, 0, 0], [0, 0, -1], [0, 1, 0]], [164.324, -83.498, 105.33]),
    ("z-carriage-rear", "PRINTED PARTS/MINI-z-carriage-rear", "xaxis", "orange", _ZC, [164.324, -56.498, 105.33]),
    ("x-end", "PRINTED PARTS/MINI-x-end", "xaxis", "orange", [[0, 0, 1], [0, -1, 0], [1, 0, 0]], [-78.176, -72.498, 104.33]),
    ("extruder-rear", "PRINTED PARTS/MINI-extruder-rear", "xaxis", "orange", _ZC, [164.324, -42.498, 105.33]),
    ("extruder-front", "PRINTED PARTS/MINI-extruder-front", "xaxis", "orange", _ZC, [164.324, -11.498, 105.33]),
    ("extruder-idler", "PRINTED PARTS/MINI-extruder-idler", "xaxis", "orange",
     [[-0.999893, -0.014633, 0], [0, 0, 1], [-0.014633, 0.999893, 0]], [144.324, -32.248, 122.33]),
    ("inspection-door", "PRINTED PARTS/MINI-inspection-door", "xaxis", "orange",
     [[-0.529847, 0.848093, 0], [0, 0, 1], [0.848093, 0.529847, 0]], [182.824, -35.498, 127.33]),
    ("x-carriage", "PRINTED PARTS/MINI-x-carriage", "carriage", "orange", _XC, [41.09, -72.498, 121.33]),
    ("heatsink", "MECHANICAL PARTS/MINI-heatsink", "carriage", "anodised", _XC, [41.09, -72.498, 121.33]),
    ("fan-spacer", "PRINTED PARTS/MINI-fan-spacer", "carriage", "orange", [[0, 0, -1], [-1, 0, 0], [0, 1, 0]], [35.09, -101.998, 100.83]),
    # SuperPINDA holder: probe ring on the firmware offset (-29, -3), end hole on the x-carriage's M3
    ("superpinda-holder", "PRINTED PARTS/MINI-minda-holder", "carriage", "orange", _XC, [-0.91, -65.0, 71.83]),
]
# front Y plate: engraved lettering (17 letters, 0.75 mm deep) inside this box of its own frame
LETTERING = ((-25.2, -31.0, 0.0), (80.8, -9.0, 0.75))

# extruder: filament path at 45 degrees through the drive (exit fitting top-left, inlet lower right)
FIL_O = np.array([170.34, -31.75, 111.35])
FIL_D = np.array([-1.0, 0.0, 1.0]) / np.sqrt(2)
HOTEND_TOP = np.array([NOZ[0], NOZ[1], 135.33])      # top of the heatsink (M8 fitting)

# printed spool holder (2 x 2 bases with 608 bearings, 2 rails) behind the Z column; spool axis X
FLOOR = -23.271
SPOOL_R, SPOOL_W = 100.0, 68.0
SH_GAP = 7.0                                          # bearing width between the two bases
SH_S = SPOOL_W - 3.5                                  # bearing pairs under the two flanges
SH_X0, SH_Y0 = 139.25, 160.0                          # holder origin (first base's inner face)
SPOOL_C = np.array([SH_X0 + SH_GAP / 2 + SH_S / 2, SH_Y0,
                    FLOOR + 17.0 + np.sqrt((SPOOL_R + 11.0) ** 2 - 57.5 ** 2)])


def rot_ok(R):
    u, _, vt = np.linalg.svd(np.asarray(R, dtype=float))
    return u @ vt


def cyl(r, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def along(axis, origin, shape):
    """Place a shape built along its local z on the model axis `axis`, local origin at `origin`."""
    return place(mesh_of(shape), frame_for(axis), np.asarray(origin, dtype=float))


def extrusion30(length):
    """Prusa's 30 x 30 profile (drawing ID4019): 8 mm slots, corner and centre holes; along local z."""
    body = cq.Workplane("XY").rect(30, 30).extrude(length).edges("|Z").fillet(2.0)
    for a in range(4):
        slot = (cq.Workplane("XY").polyline([(15.5, 4.0), (12.6, 4.0), (12.6, 8.2), (7.0, 3.6), (7.0, -3.6),
                                             (12.6, -8.2), (12.6, -4.0), (15.5, -4.0)]).close()
                .extrude(length).rotate((0, 0, 0), (0, 0, 1), 90 * a))
        body = body.cut(slot)
    holes = cq.Workplane("XY").pushPoints([(11.625 * sx, 11.625 * sy) for sx in (-1, 1) for sy in (-1, 1)] + [(0, 0)])
    body = body.cut(holes.circle(2.1).extrude(length))
    return body.cut(cq.Workplane("XY").circle(3.4).extrude(length))


def ring(r_out, r_in, width):
    return cq.Workplane("XY").circle(r_out).circle(r_in).extrude(width).translate((0, 0, -width / 2))


def main():
    t0 = time.time()
    parts = []      # (name, group, material, mesh in the model frame)

    def add(name, group, mat, mesh):
        parts.append((name, group, mat, mesh))

    # ---------------------------------------------------------------- Prusa's STEP parts
    for name, f, group, mat, R, t in POSES:
        shape = read_step(STEP / f"{f}.stp")
        if name == "y-plate-front":
            (x0, y0, z0), (x1, y1, z1) = LETTERING
            shape = cq.Shape.cast(shape).fuse(cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))).clean().wrapped
        add(name, group, mat, place(tessellate(shape, 0.06, 0.3), rot_ok(R), np.array(t, dtype=float)))
    print(f"Prusa parts placed in {time.time() - t0:.1f} s")

    # ---------------------------------------------------------------- frame: extrusions, rods, leadscrew
    ex_y = extrusion30(262.0)
    for x in (113.3, -6.7):
        add(f"y-extrusion-{'right' if x > 0 else 'left'}", "static", "extrusion",
            along([0, 1, 0], [x, -224.0, -3.3], ex_y))
    add("z-extrusion", "static", "extrusion", along([0, 0, 1], [208.3, -56.5, 16.729], extrusion30(289.0)))
    for x in (-27.6, 92.4):
        add(f"y-rod-{x:.0f}", "static", "chrome", along([0, 1, 0], [x, -232.0, 20.704], cyl(4.0, 0, 280)))
    for x in (144.324, 184.324):
        add(f"z-rod-{x:.0f}", "static", "chrome", along([0, 0, 1], [x, -56.498, -19.27], cyl(5.0, 0, 342)))
    add("z-leadscrew", "static", "leadscrew", along([0, 0, 1], [164.324, -56.498, 322.73 - 311.0], cyl(4.0, 0, 311)))
    for z in (87.33, 121.33):
        add(f"x-rod-{z:.0f}", "xaxis", "chrome", along([1, 0, 0], [-94.1, -72.498, z], cyl(4.0, 0, 285)))

    # ---------------------------------------------------------------- motors (LDO, Prusa's drawings)
    c45, s45 = np.sqrt(0.5), np.sqrt(0.5)
    r45 = np.array([[c45, -s45, 0], [s45, c45, 0], [0, 0, 1.0]])
    motors = [
        ("z-motor", "static", [164.324, -56.498, 322.73], [0, 0, -1.0], 34, True),
        ("y-motor", "static", [42.9, 17.0, 2.7], [-1.0, 0, 0], 40, False),
        ("x-motor", "xaxis", [164.324, -85.5, 106.33], [0, 1.0, 0], 40, True),
        ("e-motor", "xaxis", [166.32, -11.5, 105.33], [0, -1.0, 0], 40, True),
    ]
    for name, grp, face, shaft, length, turned in motors:
        M = frame_for(shaft) @ (r45 if turned else np.eye(3))
        for mat, shape in nema17(length).items():
            add(f"{name}-{mat}", grp, mat, place(mesh_of(shape), M, np.array(face, dtype=float)))

    # ---------------------------------------------------------------- pulleys, idlers, belts
    hub = cyl(6.5, -6.0, 0)
    xp = pulley().union(hub)
    add("x-pulley", "xaxis", "alu", along([0, -1, 0], [164.324, -67.7, 106.33], xp))
    add("x-idler", "xaxis", "steel", along([0, -1, 0], [-94.7, -67.7, 104.33], pulley(12.2, 7.0, 16.0)))
    Mxz = np.column_stack([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])     # local x -> X, y -> Z, z -> -Y
    add("x-belt", "xaxis", "belt", place(mesh_of(belt_loop((164.324, 106.33), (-94.7, 104.33), 12.2)), Mxz,
                                       np.array([0, -72.2, 0])))
    add("y-pulley", "static", "alu", along([-1, 0, 0], [37.0, 17.0, 2.7], pulley().union(cyl(6.5, 9.0, 15.0))))
    add("y-idler", "static", "steel", along([-1, 0, 0], [37.75, -207.5, 3.7], pulley(12.2, 7.0, 16.0)))
    Myz = np.column_stack([[0, 1.0, 0], [0, 0, 1.0], [1.0, 0, 0]])     # local x -> Y, y -> Z, z -> X
    add("y-belt", "static", "belt", place(mesh_of(belt_loop((17.0, 2.7), (-207.5, 3.7), 12.2)), Myz,
                                        np.array([32.75, 0, 0])))

    # ---------------------------------------------------------------- linear bearings
    lm8 = cq.Workplane("XY").circle(7.5).extrude(24).translate((0, 0, -12)).faces(">Z or <Z").chamfer(0.6)
    lm10 = cq.Workplane("XY").circle(9.5).extrude(29).translate((0, 0, -14.5)).faces(">Z or <Z").chamfer(0.8)
    # Y bearings under the Y-carriage's clamp holes (carriage frame: x -60 / +60, y +-28.25 / 0),
    # each held by Prusa's spring-steel clip whose screw tabs sit on the carriage's underside
    clip = tessellate(read_step(STEP / "MECHANICAL PARTS/MINI-bearing-clip.stp"), 0.03, 0.3)
    R_clip = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])      # clip x -> X, y -> Z (tabs up), z -> -Y
    for k, (x, y) in enumerate(((-60.0, 28.25), (-60.0, -28.25), (60.0, 0.0))):
        cx, cy = 32.399 + x, -99.027 + y
        add(f"y-bearing-{k}", "bed", "steel", along([0, 1, 0], [cx, cy, 20.704], lm8))
        add(f"y-bearing-clip-{k}", "bed", "steel", place(clip, R_clip, np.array([cx, cy, 26.294 - 4.91])))
    for z in (87.33, 121.33):
        add(f"x-bearing-{z:.0f}", "carriage", "steel", along([1, 0, 0], [41.2, -72.498, z], lm8))
    for x in (144.324, 184.324):
        for z in (91.8, 118.8):
            add(f"z-bearing-{x:.0f}-{z:.0f}", "xaxis", "steel", along([0, 0, 1], [x, -56.498, z], lm10))

    # ---------------------------------------------------------------- hotend (Prusa's drawings) and print head
    ax = [NOZ[0], NOZ[1]]
    add("heatbreak", "carriage", "steel", along([0, 0, 1], [*ax, 73.33], cyl(2.75, 0, 7.0)))
    block = cq.Workplane("XZ").polyline([(-8, 0), (14, 0), (14, 9), (6, 11), (-8, 11)]).close().extrude(16)
    bb = block.val().BoundingBox()                                          # M6 axis at x = 0, heater side +x
    block = block.translate((0, -(bb.ymin + bb.ymax) / 2, -bb.zmin)).edges("|Y").chamfer(0.25)
    add("heater-block", "carriage", "alu", place(mesh_of(block, 0.04), np.eye(3), np.array([*ax, 67.83])))
    add("heater-cartridge", "carriage", "steel", along([0, 1, 0], [NOZ[0] + 6.5, NOZ[1] - 10.0, 72.33], cyl(3.0, 0, 20)))
    noz = (cq.Workplane("XY").circle(0.5).workplane(offset=2.0).circle(3.0).loft()
           .union(cq.Workplane("XY").workplane(offset=2.0).polygon(6, 7.0 / np.cos(np.pi / 6)).extrude(3.0))
           .union(cq.Workplane("XY").workplane(offset=5.0).circle(3.0).extrude(2.0)))
    add("nozzle", "carriage", "brass", place(mesh_of(noz, 0.02), np.eye(3), NOZ))

    fitting = (cq.Workplane("XY").polygon(6, 12.0 / np.cos(np.pi / 6)).extrude(6.0)
               .union(cyl(4.5, 6.0, 9.0))
               .union(cq.Workplane("XY").workplane(offset=9.0).polygon(6, 11.0 / np.cos(np.pi / 6)).extrude(8.0))
               .faces(">Z").workplane().hole(4.2))
    add("hotend-fitting", "carriage", "brass", along([0, 0, 1], HOTEND_TOP, fitting))
    fil_out = FIL_O + FIL_D * 20.71                                        # end of the bore in the extruder
    add("extruder-fitting", "xaxis", "brass", along(FIL_D, fil_out, fitting))

    fan = (cq.Workplane("XY").rect(40, 40).extrude(10).edges("|Z").fillet(2.5)
           .cut(cq.Workplane("XY").circle(19.0).extrude(10))
           .cut(cq.Workplane("XY").pushPoints([(sx * 16, sy * 16) for sx in (-1, 1) for sy in (-1, 1)]).circle(1.7).extrude(10)))
    M_fan = frame_for([1.0, 0, 0])
    add("hotend-fan", "carriage", "fan", place(mesh_of(fan), M_fan, np.array([19.1, -102.0, 100.8])))
    add("hotend-fan-rotor", "carriage", "fan", place(mesh_of(fan_rotor(9.8, 18.4, 7.0, 7, 32.0)), M_fan,
                                                     np.array([20.6, -102.0, 100.8])))
    blower = (cq.Workplane("YZ").center(-37.8, 101.8).circle(25.0).extrude(15.0)
              .union(cq.Workplane("YZ").center(-53.8, 86.6).rect(20.0, 30.4).extrude(15.0))
              .cut(cq.Workplane("YZ").workplane(offset=13.0).center(-37.8, 101.8).circle(13.0).extrude(2.0)))
    add("print-fan", "carriage", "fan", place(mesh_of(blower), np.eye(3), np.array([33.6, 0, 0])))
    pinda = cyl(4.0, 0, 46.0).union(cyl(2.6, 46.0, 52.0))
    add("superpinda", "carriage", "motor", along([0, 0, 1], [NOZ[0] - 29.0, NOZ[1] - 3.0, NOZ[2] + 1.0], pinda))

    # ---------------------------------------------------------------- spool holder (Prusa's parts, 608 bearings)
    base = read_step(STEP / "PRINTED PARTS/MINI-base-spoolholder.stp")
    rail = read_step(STEP / "PRINTED PARTS/MINI-rail-spoolholder.stp")
    base_m, rail_m = tessellate(base, 0.06, 0.3), tessellate(rail, 0.06, 0.3)
    R_sh = np.array([[0, 0, 1.0], [1.0, 0, 0], [0, 1.0, 0]])       # base x -> Y, y -> Z (bottom edge y = -17), z -> X
    o_sh = np.array([SH_X0, SH_Y0, FLOOR + 17.0])
    flip = np.diag([-1.0, 1.0, -1.0])                              # second base of a pair: turned about its y
    b608 = mesh_of(ring(11.0, 4.0, SH_GAP).union(ring(6.0, 4.0, SH_GAP + 0.6)))
    for k, dz in enumerate((0.0, SH_S)):
        add(f"spool-base-{k}a", "static", "orange", place(base_m, R_sh, o_sh + R_sh @ [0, 0, dz]))
        add(f"spool-base-{k}b", "static", "orange", place(base_m, R_sh @ flip, o_sh + R_sh @ [0, 0, dz + SH_GAP]))
        for x in (-57.5, 57.5):
            add(f"spool-bearing-{k}-{x:.0f}", "static", "steel",
                place(b608, R_sh, o_sh + R_sh @ np.array([x, 0, dz + SH_GAP / 2])))
    for x in (-37.5, 37.5):
        add(f"spool-rail-{x:.0f}", "static", "orange", place(rail_m, R_sh, o_sh + R_sh @ np.array([x, -17.0, -9.0])))

    # ---------------------------------------------------------------- bed: heatbed on standoffs + Prusa's sheet
    hb = (cq.Workplane("XY").polyline([(-60.6, -192.0), (125.4, -192.0), (125.4, -6.0), (122.4, -6.0), (122.4, 10.37),
                                       (-21.6, 10.37), (-21.6, -6.0), (-60.6, -6.0)]).close()
          .extrude(3.0).translate((0, 0, 41.29)))
    add("heatbed", "bed", "pcb", mesh_of(hb, 0.08))
    for x, y in ((-85, -85), (-85, 85), (85, -85), (85, 85), (0, -80), (0, 80), (-80, 0), (80, 0), (0, 0)):
        sp = cq.Workplane("XY").polygon(6, 7.0).extrude(10.0)
        add(f"standoff-{x}-{y}", "bed", "steel", place(mesh_of(sp, 0.03), np.eye(3), np.array([32.399 + x, -99.027 + y, 31.294])))
    sheet = tessellate(read_step(STEP / "MECHANICAL PARTS/MINI-steel-sheet.stp"), 0.05, 0.3)
    add("sheet", "bed", "sheet", place(sheet, np.eye(3), np.array([32.399, -4.4 - 94.15, 44.29])))   # rear edge at the cable cover

    # ---------------------------------------------------------------- write GLB
    w = GlbWriter()

    def lin(c):     # MATS colours are sRGB; glTF baseColorFactor is linear
        return tuple(round(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92, 5) for v in c)

    used = {p[2] for p in parts}
    mats = {k: w.material(k, lin(c), m, r) for k, (c, m, r) in MATS.items() if k in used}
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
        P = to_frame(P, grp).astype(np.float32)
        if grp == "static":
            lo, hi = np.minimum(lo, P.min(0)), np.maximum(hi, P.max(0))
        ntri += len(I)
        w.node(name, w.mesh(name, P, np.asarray(N, dtype=np.float32), I, mats[mat]), parent=groups[grp])

    # LCD: Prusa's 2.8" portrait screen behind the display box window (box frame: front face z = 0, up = -y)
    R_lcd = rot_ok(POSES[[p[0] for p in POSES].index("display-box")][4])
    t_lcd = np.array([167.149, -213.328, 49.039])

    def lcd(x, y):
        return point(R_lcd @ np.array([x, y, 1.2]) + t_lcd, "static")

    fil_in = FIL_O - FIL_D * 30.63                                          # inlet: right face of the extruder
    extras = {
        "printtwin": {
            "version": 1, "units": "mm", "up": "z", "nozzle_y": NOZZLE_Y,
            "pose": {"x": round(float(X_C), 2), "y": round(float(Y_C), 2), "z": round(float(Z_C), 2)},
            "lcd_screen": [lcd(-22.3, 29.2), lcd(22.3, 29.2), lcd(22.3, -29.7), lcd(-22.3, -29.7)],
            "lcd_portrait": True,
            "spool": {"center": point(SPOOL_C, "static"), "axis": [1, 0, 0], "radius": SPOOL_R, "width": SPOOL_W,
                      "hub_radius": 27.0},
            "filament_inlet_xaxis": point(fil_in, "xaxis"),
            # PTFE bowden (275 mm) from the extruder's fitting to the top of the hotend
            "bowden": {"start_xaxis": point(fil_out + FIL_D * 17.0, "xaxis"), "dir_start": FIL_D.round(4).tolist(),
                       "end_carriage": point(HOTEND_TOP + [0, 0, 17.0], "carriage"), "dir_end": [0, 0, 1],
                       "length": 275.0, "radius": 2.0},
            "bounds": [lo.round(1).tolist(), hi.round(1).tolist()],
            "model": "mini",
            "sources": [
                "Prusa Research, 'Original-Prusa-MINI', github.com/prusa3d/Original-Prusa-MINI, GPL-3.0",
                "part poses from Luis, 'STEP File for Prusa Mini', printables.com/model/307054, public domain "
                "(reference only)",
                "extrusions, rods, motors, hotend, bearings, belts, fans, heatbed: PrintTwin tools/cad/build_mini.py "
                "from Prusa's drawings (GPL-3.0)",
            ],
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    size = w.write(OUT, extras)
    gz = gzip.compress(OUT.read_bytes(), 6)
    OUT.with_name(OUT.name + ".gz").write_bytes(gz)
    print(f"wrote {OUT}: {size / 1e6:.1f} MB ({len(gz) / 1e6:.1f} MB gz), {ntri:,} triangles, "
          f"{len(parts)} parts in {time.time() - t0:.0f} s")
    print("pose of the parts (G-code):", extras["printtwin"]["pose"], "static bounds:", extras["printtwin"]["bounds"])


if __name__ == "__main__":
    main()
