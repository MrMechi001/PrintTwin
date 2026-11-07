"""Build web/models/xl.glb: an animatable Original Prusa XL (single tool), CoreXY.

There is no published XL assembly (Prusa released full CAD only for the CORE One / CORE One L),
so the model is built from:
  * Prusa Research, "XL printable parts" (printables.com/model/449554, GPL-2.0): Prusa's STEP
    files for the front CoreXY corners, x-carriage, y-carriages, xLCD cover and knob, Z bearing
    housings, bed controller case, entry filament sensor, telescopic spool holder.
  * The Nextruder (same as the MK4's) from web/models/mk4.glb (GPL-2.0), without the MK4's
    x-carriage, cable holder and LoveBoard covers.
  * Firmware (Prusa-Firmware-Buddy, Configuration_XL.h): bed 360 x 360, Z 370, CoreXY, X / Y
    home to their minimum (-8 / -9), Y up to 461 for the tool docks at Y 455.
  * PrusaSlicer's XL bed model (xl_bed.stl): sheet 365 x 380.75 at X -2.5..362.5, Y -13.29..367.46.
  * Prusa's dimension photo of the XL (front + side view, 70 x 72 x 90 cm) measured with a
    perspective correction (scale from the 365 mm sheet: 1.148 mm/px at its front edge,
    1.267 at its back edge): base 504 x 538 mm, top side beams 618 mm long overhanging 17 mm
    in front and 63 mm at the back, Z-axis posts (LED strips) 160 mm behind the base front,
    rear posts 500 mm behind it, top of the frame 530 mm, bed at Z 370 -> sheet 146 mm above
    the floor (so the nozzle tip is 516 mm up; the heads' bodies stand above the frame), parked
    tools at Y 417..509 (the firmware docks them at Y 455 - consistent), spools Ø200 on the
    side panels; layout from the photos of Prusa's XL assembly manual. The frame sizes are
    the constants below (+-1 cm).

Frames
  Output (= PrintTwin G-code frame, mm, Z up), extras.printtwin.kinematics = "corexy":
    static    printer frame, floor at z = 0; the nozzle tip stays at z = NOZZLE_Z
    bed       G-code bed coordinates (sheet surface z = 0); the viewer lowers it by Z
    gantry    X beam + Y carriages: x absolute, y relative to the nozzle (viewer sets y = Y)
      carriage  print head: x relative to the nozzle (viewer sets x = X)

usage:  python -I tools/cad/build_xl.py
"""
from __future__ import annotations

import gzip
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import cadquery as cq  # noqa: E402
from build_mini import extrusion30  # noqa: E402
from build_mk4 import MATS, frame_for, mesh_of, nema17, place, pulley  # noqa: E402
from cadutil import read_step  # noqa: E402
from glb import GlbWriter, tessellate  # noqa: E402

PARTS = ROOT / "cad_src/prusa_xl_parts/extracted"
MK4_GLB = ROOT / "web/models/mk4.glb"
OUT = ROOT / "web/models/xl.glb"

# ----------------------------------------------------------------------------- frame (measured, see above)
FX0, FX1 = -72.0, 432.0          # outer X (504)
BY0, BY1 = -100.0, 438.0         # base front / rear
TY0, TY1 = -117.0, 501.0         # top side beams
ZP_Y, RP_Y = 75.0, 400.0         # Z-axis posts (front, LED strips) / rear posts: centre Y
P, FOOT = 30.0, 10.0             # extrusion profile, feet
BASE_Z = FOOT + P                # top of the base
TOP_Z0, TOP_Z1 = 463.0, 530.0    # top side beams ("upper metal flanges", LEDs inside)
BEAM_W = 34.0                    # top side beam width
NOZZLE_Z = 516.0                 # nozzle tip above the floor
RAIL_X = (FX0 + BEAM_W - 12.0, FX1 - BEAM_W + 12.0)   # Y rails on top of the top beams' inner edges (centre)
RAIL_Z = TOP_Z1                  # rail base
GANTRY_DY = 81.5                 # X beam centre behind the nozzle (x-carriage 48.5 + block 13 + beam 15 + 5)
GANTRY_Z = 556.0                 # X beam centre height
CAR_DZ = 47.5                    # x-carriage centre above the nozzle (as the MK4's)
POSE = {"x": 180.0, "y": 180.0, "z": 120.0}
MK4_NOZZLE_Y = 105.0             # mk4.glb carriage frame: x, z relative to the nozzle, nozzle at y = 105
SPOOL = {"center": [FX1 + 44.0, 158.0, 392.0], "radius": 100.0, "width": 68.0}   # right side, upper holder
DOCK_X0, DOCK_DX, DOCK_Y = 25.0, 82.0, 455.0      # firmware: tool docks (nozzle position when parked)
# spool holders on the side panels, one per tool (5-tool XL photos): left side 1 upper front, 2 lower front,
# 3 lower rear (at the rear post, reaching behind the frame); right side 4 upper front, 5 lower front
TOOL_SPOOLS = [[FX0 - 44.0, 158.0, 392.0], [FX0 - 44.0, 158.0, 153.0], [FX0 - 44.0, 395.0, 232.0],
               [FX1 + 44.0, 158.0, 392.0], [FX1 + 44.0, 158.0, 153.0]]

MK4_TOOL = ("fan-shroud", "pg-case", "hotend-heatsink", "hotend-fan", "hotend", "nextruder-details", "main-plate",
            "hotend-fan-rotor", "idler-nut", "idler-swivel-front", "idler-swivel-back", "idler-lever-a", "idler-lever-b",
            "e-motor-motor", "e-motor-steel", "nozzle", "heater-sock")


def read_glb_nodes(path, names):
    """{name: (material, P, N, I)} for mesh nodes of a GLB written by glb.GlbWriter."""
    d = path.read_bytes()
    clen = struct.unpack_from("<I", d, 12)[0]
    g = json.loads(d[20:20 + clen])
    b0 = 20 + clen + 8

    def arr(i):
        a = g["accessors"][i]
        bv = g["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC3": 3}[a["type"]]
        dt = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}[a["componentType"]]
        x = np.frombuffer(d, dt, a["count"] * n, b0 + bv.get("byteOffset", 0) + a.get("byteOffset", 0))
        return x.reshape(-1, n) if n > 1 else x
    out = {}
    for n in g["nodes"]:
        if n.get("name") in names and "mesh" in n:
            pr = g["meshes"][n["mesh"]]["primitives"][0]
            out[n["name"]] = (g["materials"][pr["material"]]["name"], arr(pr["attributes"]["POSITION"]).astype(np.float64),
                              arr(pr["attributes"]["NORMAL"]).astype(np.float64), arr(pr["indices"]).reshape(-1, 3))
    return out


def step(name):
    return read_step(next(PARTS.rglob(f"{name}.stp")))


def box(x0, y0, z0, x1, y1, z1):
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def cyl(r, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def along(axis, origin, shape):
    return place(mesh_of(shape), frame_for(axis), np.asarray(origin, dtype=float))


def smooth_path(a, da, b, db, k, n=14):
    """Points on a cubic Bezier leaving a along da and entering b along db: a PTFE tube without sharp bends."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    da, db = np.asarray(da, dtype=float), np.asarray(db, dtype=float)
    c1, c2 = a + da / np.linalg.norm(da) * k, b - db / np.linalg.norm(db) * k
    t = np.linspace(0, 1, n + 1)[:, None]
    p = (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * b
    return np.round(p, 2).tolist()


def hex_plate(x0, x1, z0, z1, y, t=1.5, cell=13.0):
    """Perforated sheet (hexagonal holes) in the XZ plane at depth y..y+t."""
    plate = box(x0, y, z0, x1, y + t, z1)
    pts = []
    for i, z in enumerate(np.arange(z0 + 9, z1 - 6, cell * 0.87)):
        for x in np.arange(x0 + 9 + (cell / 2 if i % 2 else 0), x1 - 6, cell):
            pts.append((x, z))
    holes = cq.Workplane("XZ").pushPoints(pts).polygon(6, cell * 0.8).extrude(-3 * t).translate((0, y - t, 0))
    return plate.cut(holes)


def main():
    t0 = time.time()
    parts = []          # (name, group, material, (P, N, I)) - coordinates already in the group's frame

    def add(name, group, mat, mesh):
        if mesh is not None:
            parts.append((name, group, mat, mesh))

    # ---------------------------------------------------------------- frame (static)
    ex = {}

    def extr(name, axis, a, b, c0, c1):
        """30 x 30 extrusion along `axis` from a to b; c0/c1 = centre in the two other axes."""
        key = round(b - a, 1)
        if key not in ex:
            ex[key] = mesh_of(extrusion30(b - a), 0.1)
        o = {"x": [a, c0, c1], "y": [c0, a, c1], "z": [c0, c1, a]}[axis]
        ax = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis]
        add(name, "static", "extrusion", place(ex[key], frame_for(ax), np.array(o, dtype=float)))
    h = P / 2
    zb = FOOT + h
    extr("base-front", "x", FX0, FX1, BY0 + h, zb)
    extr("base-rear", "x", FX0, FX1, BY1 - h, zb)
    extr("base-left", "y", BY0 + P, BY1 - P, FX0 + h, zb)
    extr("base-right", "y", BY0 + P, BY1 - P, FX1 - h, zb)
    add("base-plate", "static", "frame", mesh_of(box(FX0 + P, BY0 + P, BASE_Z - 3, FX1 - P, BY1 - P, BASE_Z), 0.1))
    for sx, x in (("left", FX0 + h), ("right", FX1 - h)):
        for sy, y in (("z", ZP_Y), ("rear", RP_Y)):
            extr(f"post-{sy}-{sx}", "z", BASE_Z, TOP_Z0, x, y)
        for y in (BY0 + 20, BY1 - 20):
            add(f"foot-{sx}-{y:.0f}", "static", "rubber", along([0, 0, 1], [x, y, 0], cyl(13, 0, FOOT)))
        # top side beam (sheet-metal flange over the extrusion, LED strip inside)
        x0 = FX0 if sx == "left" else FX1 - BEAM_W
        add(f"top-beam-{sx}", "static", "frame", mesh_of(box(x0, TY0 + 27, TOP_Z0, x0 + BEAM_W, TY1, TOP_Z1), 0.1))
        # side panel from the Z post to the rear post
        xp = FX0 - 1.5 if sx == "left" else FX1
        add(f"side-panel-{sx}", "static", "frame", mesh_of(box(xp, ZP_Y + h, BASE_Z, xp + 1.5, RP_Y - h, TOP_Z0), 0.1))
        # orange LED diffuser on the front face of the Z post
        add(f"led-{sx}", "static", "orange", mesh_of(box(x - 6, ZP_Y - h - 1.2, BASE_Z + 20, x + 6, ZP_Y - h, TOP_Z0 - 18), 0.1))
    extr("top-rear", "x", FX0 + BEAM_W, FX1 - BEAM_W, TY1 - 50, TOP_Z1 - h)
    add("rear-panel", "static", "frame", mesh_of(box(FX0 + P, RP_Y + h, BASE_Z, FX1 - P, RP_Y + h + 1.5, TOP_Z0), 0.1))
    add("rear-top-plate", "static", "frame", mesh_of(hex_plate(FX0 + BEAM_W, FX1 - BEAM_W, TOP_Z0 - 40, TOP_Z1 - P, TY1 - 66), 0.1))
    add("electronics-box", "static", "elbox", mesh_of(box(40, RP_Y + h + 1.5, 105, 320, RP_Y + h + 47, 336), 0.1))
    # tool docks at the rear top (multi-tool XLs; the viewer shows them only when a job uses several tools)
    add("docks", "static", "frame", mesh_of(box(FX0 + BEAM_W, DOCK_Y + 22, TOP_Z1 - 6, FX1 - BEAM_W, DOCK_Y + 34, TOP_Z1 + 10), 0.1))

    # front CoreXY corners (Prusa's parts) at the front ends of the top beams: 93 mm tall, 27 deep
    R_fc = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])        # local x -> X, y -> Z, z -> -Y
    for nm, x_lo in (("corexy-front-left", FX0 - 20.2), ("corexy-front-right", FX1 - 20.3)):
        add(nm, "static", "petg", place(tessellate(step(nm), 0.06, 0.3), R_fc, np.array([x_lo, TY0 + 6.0, TOP_Z1 - 92.8])))

    # Y rails on the top beams; CoreXY belts; A / B motors under the rear ends of the top beams
    for sx, xc in zip(("left", "right"), RAIL_X):
        add(f"y-rail-{sx}", "static", "steel", mesh_of(box(xc - 6, TY0 + 30, RAIL_Z, xc + 6, TY1 - 8, RAIL_Z + 8), 0.1))
        xb = xc + (16 if sx == "left" else -16)
        for k, zz in enumerate((RAIL_Z + 14, RAIL_Z + 22)):
            add(f"belt-{sx}-{k}", "static", "belt", mesh_of(box(xb - 0.7, TY0 + 20, zz - 3, xb + 0.7, TY1 - 25, zz + 3), 0.1))
        xm = xc + (8 if sx == "left" else -8)
        for mat, shape in nema17(40).items():
            add(f"{sx}-xy-motor-{mat}", "static", mat, place(mesh_of(shape), frame_for([0, 0, 1.0]), np.array([xm, TY1 - 26, TOP_Z0 - 2])))
        add(f"{sx}-xy-pulley", "static", "alu", along([0, 0, 1], [xm, TY1 - 26, RAIL_Z + 10], pulley(9.6, 14.0, 13.0)))

    # Z axes on the Z posts: rails on the inner faces, leadscrews, motors in the base, Prusa's bearing housings
    bh = tessellate(step("z-axis-bearing-housing"), 0.06, 0.3)
    for sx, s in (("left", 1), ("right", -1)):
        xf = FX0 + P if s > 0 else FX1 - P                           # inner face of the post
        add(f"z-rail-{sx}", "static", "steel", mesh_of(box(min(xf, xf + 8 * s), ZP_Y - 6, BASE_Z + 8, max(xf, xf + 8 * s), ZP_Y + 6, TOP_Z0 - 8), 0.1))
        xl, yl = xf + 14 * s, ZP_Y + 24
        add(f"z-leadscrew-{sx}", "static", "leadscrew", along([0, 0, 1], [xl, yl, BASE_Z + 6], cyl(4.0, 0, TOP_Z0 - 30 - BASE_Z)))
        for mat, shape in nema17(40).items():
            add(f"z-motor-{sx}-{mat}", "static", mat, place(mesh_of(shape), frame_for([0, 0, 1.0]), np.array([xl, yl, BASE_Z + 2])))
        Rb = np.eye(3) if s > 0 else np.diag([-1.0, -1.0, 1.0])
        add(f"z-bearing-housing-{sx}", "static", "petg", place(bh, Rb, np.array([xl, yl, TOP_Z0 - 46.0])))

    # ---------------------------------------------------------------- xLCD in front of the base (Prusa's cover + knob)
    c20, s20 = np.cos(np.radians(20)), np.sin(np.radians(20))
    R_lcd = np.column_stack([[1.0, 0, 0], [0, s20, c20], [0, -c20, s20]])       # local z out of the screen, tilted back 20 deg
    lcd_c = np.array([180.0, BY0 - 25.0, 64.0])
    add("xlcd-cover", "static", "petg", place(tessellate(step("xlcd-cover"), 0.06, 0.3), R_lcd, lcd_c))
    add("xlcd-body", "static", "elbox", place(mesh_of(box(-68, -32, -22, 68, 30, 0), 0.1), R_lcd, lcd_c))
    add("lcd-knob", "static", "petg", place(tessellate(step("knob"), 0.04, 0.3), R_lcd @ np.diag([1.0, -1.0, -1.0]),
                                              lcd_c + R_lcd @ np.array([52.0, -12.0, 12.4 + 15.0])))

    # ---------------------------------------------------------------- spool holder (right side) + entry filament sensor
    sc = np.array(SPOOL["center"])
    R_sp = frame_for([1.0, 0, 0])
    add("spoolholder-mount", "static", "petg", place(tessellate(step("telescopic-spoolholder-mount"), 0.06, 0.3), R_sp,
                                                      np.array([FX1 + 1.5, sc[1], sc[2]])))
    add("spoolholder-base", "static", "petg", place(tessellate(step("telescopic-spoolholder-base"), 0.06, 0.3), R_sp,
                                                     np.array([FX1 + 1.5 + 6.5 + 78.0, sc[1], sc[2]])))
    add("spoolholder-slider", "static", "petg", place(tessellate(step("telescopic-spoolholder-slider"), 0.06, 0.3), R_sp,
                                                       np.array([FX1 + 1.5 + 6.5 + 79.0 + 6.0, sc[1], sc[2]])))
    fs = np.array([FX1 + 6.0, RP_Y + 10.0, TOP_Z0 - 40.0])                 # entry sensor on the rear post, top
    R_fs = np.array([[0, 0, 1.0], [1.0, 0, 0], [0, 1.0, 0]])               # local x -> Y, y -> Z, z -> X
    fs_mesh = tessellate(step("entry-fs-case"), 0.06, 0.3)
    add("entry-fs-case", "static", "petg", place(fs_mesh, R_fs, fs + [0, -36.0, 0]))
    # left side sensor block (multi-tool XLs feed tools 1-3 from the left): mirror image about the frame's centre
    Pm, Nm, Im = place(fs_mesh, R_fs, fs + [0, -36.0, 0])
    xm = FX0 + FX1
    add("entry-fs-case-left", "static", "petg", (np.column_stack([xm - Pm[:, 0], Pm[:, 1:]]),
                                                  np.column_stack([-Nm[:, 0], Nm[:, 1:]]), Im[:, ::-1]))

    # ---------------------------------------------------------------- gantry: X beam on two Y carriages (Prusa's parts)
    blk = RAIL_Z + 13.0                                                   # top of the MGN12H blocks
    xl_l, xl_r = RAIL_X[0] + 22.0, RAIL_X[1] - 22.0
    add("x-beam", "gantry", "extrusion", place(mesh_of(extrusion30(xl_r - xl_l), 0.1), frame_for([1.0, 0, 0]),
                                               np.array([xl_l, GANTRY_DY, GANTRY_Z])))
    add("x-rail", "gantry", "steel", mesh_of(box(xl_l + 5, GANTRY_DY - 23, GANTRY_Z - 6, xl_r - 5, GANTRY_DY - 15, GANTRY_Z + 6), 0.1))
    for sx, xc in zip(("left", "right"), RAIL_X):
        add(f"y-block-{sx}", "gantry", "steel", mesh_of(box(xc - 13.5, GANTRY_DY - 22.5, RAIL_Z + 3, xc + 13.5, GANTRY_DY + 22.5, blk), 0.1))
    # y-carriages sit on the blocks (block face local z = -2.4, MGN12H pattern centre): left (7, -23.25), right (5.5, -24)
    RL = np.array([[-1.0, 0, 0], [0, 1.0, 0], [0, 0, -1.0]])             # local x -> -X, y -> +Y, z -> -Z
    tL = np.array([RAIL_X[0] + 7.0, GANTRY_DY + 23.25, blk - 2.4])
    RR = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])             # local x -> +X, y -> -Y, z -> -Z
    tR = np.array([RAIL_X[1] - 5.5, GANTRY_DY - 24.0, blk - 2.4])
    for nm, R, t in (("y-carriage-left-base", RL, tL), ("y-carriage-left-top", RL, tL),
                     ("y-carriage-right-base", RR, tR), ("y-carriage-right-top", RR, tR)):
        add(nm, "gantry", "pccf", place(tessellate(step(nm), 0.06, 0.3), R, t))

    # ---------------------------------------------------------------- print head (carriage): MK4 Nextruder + XL x-carriage
    for name, (mat, Pm, Nm, Im) in read_glb_nodes(MK4_GLB, MK4_TOOL).items():
        add(name, "carriage", mat, (Pm + [0, -MK4_NOZZLE_Y, NOZZLE_Z], Nm, Im))
    gz = NOZZLE_Z + CAR_DZ
    Rx = np.array([[0, 0, 1.0], [1.0, 0, 0], [0, 1.0, 0]])                  # local x -> +Y, y -> +Z, z -> +X
    add("x-carriage", "carriage", "pccf", place(tessellate(step("x-carriage"), 0.06, 0.3), Rx, np.array([0, 19.0, gz])))
    add("x-block", "carriage", "steel", mesh_of(box(-13.5, 53.5, GANTRY_Z - 13, 13.5, GANTRY_DY - 23, GANTRY_Z + 13), 0.1))

    # ---------------------------------------------------------------- bed: sheet (PrusaSlicer's XL bed), 16 tiles, frame, carriers
    sheet = cq.Workplane("XY").center(180.0, (-13.29 + 367.46) / 2).rect(365.0, 380.75).extrude(-0.75).edges("|Z").fillet(6.0)
    add("sheet", "bed", "sheet", mesh_of(sheet, 0.05))
    tile = mesh_of(box(0.4, 0.4, -2.35, 89.6, 89.6, -0.75), 0.1)
    for i in range(4):
        for j in range(4):
            Pt, Nt, It = tile
            add(f"tile-{i}{j}", "bed", "pcb", (Pt + [90.0 * i, 90.0 * j, 0], Nt, It))
    add("heatbed-frame", "bed", "frame", mesh_of(box(-12, -20, -24, 372, 380, -2.35).cut(box(10, 10, -30, 350, 350, -8)), 0.1))
    for sx, x0, xr in (("left", -27.0, FX0 + P + 8), ("right", 373.0, FX1 - P - 8)):
        add(f"bed-carrier-{sx}", "bed", "frame", mesh_of(box(x0, -15, -42, x0 + 14, 330, -12), 0.1))      # T arm along the bed
        xa, xb_ = sorted((xr, x0 + (0 if sx == "left" else 14)))
        add(f"bed-carrier-{sx}-arm", "bed", "frame", mesh_of(box(xa, ZP_Y - 20, -42, xb_, ZP_Y + 20, -12), 0.1))  # to the Z block
        bx0, bx1 = (xr, xr + 5) if sx == "left" else (xr - 5, xr)                  # MGN block on the Z rail
        add(f"z-block-{sx}", "bed", "steel", mesh_of(box(bx0, ZP_Y - 13.5, -55, bx1, ZP_Y + 13.5, -10), 0.1))
    add("bed-controller-case", "bed", "petg", place(tessellate(step("bed-controller-case"), 0.06, 0.3), np.eye(3),
                                                     np.array([180.0, 100.0, 53.0])))

    # ---------------------------------------------------------------- write GLB
    w = GlbWriter()

    def lin(c):
        return tuple(round(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92, 5) for v in c)

    used = {p[2] for p in parts}
    mats = {k: w.material(k, lin(c), m, r) for k, (c, m, r) in MATS.items() if k in used}
    if "pccf2" in used:
        mats["pccf2"] = w.material("pccf2", lin(MATS["pccf"][0]), *MATS["pccf"][1:], double=True)
    g_static, g_bed, g_gantry = w.node("static"), w.node("bed"), w.node("gantry")
    g_car = w.node("carriage", parent=g_gantry)
    groups = {"static": g_static, "bed": g_bed, "gantry": g_gantry, "carriage": g_car}
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    ntri = 0
    for name, grp, mat, (Pp, Nn, Ii) in parts:
        Pp = np.asarray(Pp, dtype=np.float32)
        if grp == "static":
            lo, hi = np.minimum(lo, Pp.min(0)), np.maximum(hi, Pp.max(0))
        ntri += len(Ii)
        w.node(name, w.mesh(name, Pp, np.asarray(Nn, dtype=np.float32), Ii, mats[mat]), parent=groups[grp])

    def lcd(x, y):
        return (lcd_c + R_lcd @ np.array([x, y, 12.6])).round(2).tolist()

    def r2(p):
        return np.round(np.asarray(p, dtype=float), 2).tolist()
    # filament: spool -> back along the side panel -> up into the entry sensor at the top of the rear post;
    # PTFE: out of the sensor, a loop behind the printer, up into the cable bundle at the rear top;
    # bundle (textile sleeve with the PTFE inside): arcs over the frame down onto the print head
    wr = SPOOL["radius"] - 22.0
    bundle_start = [FX1 - 70.0, TY1 - 20.0, TOP_Z1 + 18.0]
    extras = {
        "printtwin": {
            "version": 1, "units": "mm", "up": "z", "kinematics": "corexy", "nozzle_z": NOZZLE_Z, "nozzle_y": 0.0,
            "pose": POSE,
            "lcd_screen": [lcd(-38.0, -22.0), lcd(38.0, -22.0), lcd(38.0, 25.0), lcd(-38.0, 25.0)],
            "spool": {**SPOOL, "axis": [1, 0, 0], "hub_radius": 27.0},
            "filament_path_static": [r2(sc + [-SPOOL["width"] / 2 + 8, wr * 0.75, wr * 0.66]), r2(fs + [0, -36.0, 0])],
            "ptfe_static": smooth_path(fs + [0, 8.0, 0], [0, 1.0, 0.25], bundle_start, [0, -0.25, 1.0], 110.0),
            "bowden": {"start_static": r2(bundle_start), "dir_start": [0, -0.25, 1], "end_carriage": [0.0, 8.0, round(NOZZLE_Z + 126.0, 2)],
                       "dir_end": [0, 0, 1], "length": 900.0, "radius": 6.0, "color": "#111214"},
            # tool changer: per tool its dock, spool, filament entry (top of the rear post on the spool's side)
            # and the cable-bundle anchor above its dock; tool_nodes = carriage nodes that form the tool
            "tools": {
                "docks": [[DOCK_X0 + DOCK_DX * i, DOCK_Y] for i in range(5)],
                "spools": TOOL_SPOOLS,
                # sensor blocks on the rear posts' tops: three inputs on the left (tools 1-3), two on the right (4-5)
                "entries": [r2([FX0 - 6.0, RP_Y - 26.0, TOP_Z0 - 28.0 - 13.0 * i]) for i in range(3)]
                           + [r2([FX1 + 6.0, RP_Y - 26.0, TOP_Z0 - 28.0 - 13.0 * i]) for i in range(2)],
                "starts": [r2([DOCK_X0 + DOCK_DX * i, TY1 - 10.0, TOP_Z1 + 14.0]) for i in range(5)],
                "tool_nodes": list(MK4_TOOL),
                "multi_nodes": ["docks", "entry-fs-case-left"],          # shown only for multi-tool XLs
                "holder_nodes": ["spoolholder-mount", "spoolholder-base", "spoolholder-slider"],
                "holder_center": SPOOL["center"],                       # where those holder nodes sit
                "mirror_x": FX0 + FX1,                                  # left-side holders: mirror x -> mirror_x - x
            },
            "bounds": [lo.round(1).tolist(), hi.round(1).tolist()],
            "model": "xl",
            "estimated": True,
            "sources": [
                "Prusa Research, 'XL printable parts', printables.com/model/449554, GPL-2.0",
                "Nextruder from web/models/mk4.glb (FokieH / Prusa Research, GPL-2.0)",
                "frame measured on Prusa's XL dimension photo and assembly-manual photos, firmware geometry, "
                "sheet from PrusaSlicer's xl_bed.stl: PrintTwin tools/cad/build_xl.py (GPL-2.0)",
            ],
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    size = w.write(OUT, extras)
    gz_ = gzip.compress(OUT.read_bytes(), 6)
    OUT.with_name(OUT.name + ".gz").write_bytes(gz_)
    print(f"wrote {OUT}: {size / 1e6:.1f} MB ({len(gz_) / 1e6:.1f} MB gz), {ntri:,} triangles, {len(parts)} parts in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
