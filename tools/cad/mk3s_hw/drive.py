"""PrintTwin MK3S+ drive parts: GT2 belts, belt idlers, Z nuts, LM8UU bearings, Y-bearing clips.

All shapes are built here from CadQuery primitives (revolved profiles, boxes, extruded 2D outlines).
Kayne's MK3S SolidWorks assembly was used ONLY as a measuring reference (axis positions, diameters,
lengths - see the comments); nothing of its geometry is read, copied or re-used by build().

Master frame: mm, X right, Y up, Z towards the front.  Every part is built at the reference pose
(X axis / carriage / bed exactly where Kayne's assembly has them); build_mk3s.py shifts the groups.

Decisions (see also the notes at each part):
  * Y-bearing fixing: a stock MK3S+ with the old flat Y-carriage uses Prusa's bent sheet-metal
    "bearing clips" (set = 3 clips + 6 screws + 6 nyloc nuts, M3x12 per the MK3S+ guide), not the
    MK3S U-bolts of the reference.  Built as an omega clip around the bearing with two flanges
    under the carriage, M3x12 socket heads under the flanges, nyloc nuts on the carriage top.
    The screw shank is cut 1 mm above the nut (a real M3x12 would end at y 81.2, 1 mm inside the
    MK52 of the reference stack).  The clips use the flat carriage's existing (U-bolt) holes:
    D3.2 at x +-10.0 from each bearing axis, z = bearing centre +0.75 (right bearing) / -0.75 (the
    two left ones) - measured on the reference carriage (solid 59).  The bearing sits in the 10.0 mm
    wide middle of a dog-bone cutout (see CLIP_* below): its top (y 71.88) is 1.9 mm above the
    carriage bottom.  With the holes at +-10 a D5.5 screw head leaves no room for a sheet hugging
    the D15 bearing (head edge x 7.25 vs bearing+sheet 8.1 at the head's bottom), so the clip arc is
    relieved by the head's envelope (a bite <= 0.9 mm deep that the head itself fills).
  * Z nut: Prusa's stock trapezoidal nut is POM (black), not brass -> new material "pom".
    Shape from the reference: flange D25 x 4 on top, body D13 x 6 hanging into the x-end,
    4 holes D4 on PCD 19 (forum: "Prusa bolt hole spacing is 19 mm").
  * Idlers: Prusa "bearing housing 623h" (aluminium, smooth, flanged) on two 623h bearings, axle =
    M3x18 socket screw + M3 nyloc nut (MK3S guide), positions/lengths from the reference's holes.
    Both screws end flush with their nut: the Y one is 18 long, the X one is shortened to 17.5 so
    its tip stays out of the right Z lead screw (a full 18 would reach 0.5 mm into it).
  * Belts: closed GT2 loops (6 mm wide, 1.38 thick, smooth - no teeth) whose inner surface is the
    tooth-tip line: on the 16T pulley the pitch radius is 5.093 (tooth tips 4.089 = the reference's
    tooth root), on a smooth idler the teeth touch the running surface (pitch r = 6.05 + 1.004).
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

import numpy as np

sys.path.insert(0, _CAD)
import cadquery as cq  # noqa: E402
from build_mk4 import frame_for, mesh_of, place  # noqa: E402

NEW_MATS = {
    "pom": ((0.065, 0.066, 0.07), 0.0, 0.45),      # black acetal (Prusa's stock TR8x8 Z nut)
}

DEFL = 0.06            # linear deflection for the small round parts

# ----------------------------------------------------------------------------- measured numbers
# All from Kayne's assembly (circle fits on the tessellated surfaces, mm, master frame).
# Linear bearings: axis centre (two coordinates) + axial start (24 mm long each)
LM8UU_Y = {             # axis along Z, centre (x, y), z start         (solids 60, 61, 62)
    "lm8uu-y-1": ((237.259, 64.379), 293.943),     # right rod, single bearing
    "lm8uu-y-2": ((67.259, 64.379), 260.443),      # left rod, rear
    "lm8uu-y-3": ((67.259, 64.379), 330.443),      # left rod, front
}
LM8UU_X = {             # axis along X, centre (y, z), x start          (solids 158, 156, 157)
    "lm8uu-x-1": ((364.685, 250.049), 44.472),     # upper rod, left
    "lm8uu-x-2": ((364.685, 250.049), 68.472),     # upper rod, right
    "lm8uu-x-3": ((319.685, 250.049), 56.472),     # lower rod
}
LM8UU_Z = {             # axis along Y, centre (x, z), y start          (solids 77, 79, 80, 78)
    "lm8uu-z-1": ((-29.741, 265.049), 346.685),    # left x-end, upper
    "lm8uu-z-2": ((-29.741, 265.049), 313.285),    # left x-end, lower
    "lm8uu-z-3": ((334.259, 265.049), 346.685),    # right x-end, upper
    "lm8uu-z-4": ((334.259, 265.049), 312.685),    # right x-end, lower
}
# Z nuts (solids 94, 95): axis along Y, centre (x, z); bottom of the body at y 364.685, flange top 374.685
Z_NUTS = {"z-nut-left": (-12.741, 265.049), "z-nut-right": (317.259, 265.049)}
Z_NUT_Y0 = 364.685
# Idlers (solids 93, 44): housing running surface r 6.05, flanges r 9.0
X_IDLER_C = (318.759, 340.435)        # axis along Z, centre (x, y)
X_IDLER_A0 = 246.699                  # housing face (z) - 8.7 long; the reference's extra 0.13 boss left out
Y_IDLER_C = (47.687, 421.749)         # axis along X, centre (y, z)
Y_IDLER_A0 = 147.912                  # housing face (x)
# Idler axle holes in the printed housings (solids 74 x-end-idler, 43 y-belt-idler):
X_SCREW_HEAD = 243.55                 # z of the counterbore floor (head bearing face), shank towards +z
X_NUT_FACE = 257.05                   # z where the nut pocket starts (round D6 x 1.5, then a cone to
                                      # D7 at 259.05 - a pressed-in nut; the nyloc's corners r 3.175
                                      # bite 0.175 into the D6 wall as a pressed nut does)
X_SCREW_LEN = 17.5                    # M3x18 shortened to end flush with the nyloc (z 261.05): the right
                                      # Z lead screw (axis x 317.259, z 265.049, r 4) passes 4.0 from there
Y_SCREW_HEAD = 144.76                 # x of the counterbore floor, shank towards +x
Y_NUT_FACE = 158.76                   # x where the nut trap starts
# Motor pulleys (solids 92, 57 - not built here, used for the belt path only): GT2 16T
X_PULLEY_C = (-61.740, 340.435)       # axis along Z, centre (x, y); tooth zone z 247.35..254.75
Y_PULLEY_C = (46.373, 130.500)        # axis along X, centre (y, z); tooth zone x 148.56..155.96
X_BELT_Z0 = 251.05 - 3.0              # belt centred on the tooth zone (= idler running zone 247.8..254.3)
Y_BELT_X0 = 152.26 - 3.0
# Y-carriage plate (solid 59, "frog"): bottom y 69.97, top y 74.22
CARRIAGE_BOT, CARRIAGE_TOP = 69.97, 74.22
# Bearing fixing holes in the frog (solid 59, measured on its bottom face): D3.2 (3.19 x 3.20) at
# x +-10.000 from each bearing axis, z offset from the bearing centre +0.75 for the right bearing and
# -0.75 for the two left ones (the U-bolts 63/65/64 sit on the same z).  The bearing cutout is a
# dog-bone, symmetric about the hole line: 10.0 wide (x +-5.0, the bearing rests on these edges) over
# +-6.75 from the hole line, end lobes up to x +-8.59, 26.5 long overall (+-13.25 from the hole line).
CLIP_HOLE_DX = 10.0
CLIP_HOLE_DZ = {"lm8uu-y-1": +0.75, "lm8uu-y-2": -0.75, "lm8uu-y-3": -0.75}

# ----------------------------------------------------------------------------- datasheet numbers
GT2_PITCH_R16 = 16 * 2.0 / np.pi / 2  # 5.093: 16 teeth x 2 mm pitch
GT2_PLD = 0.254                       # pitch line below the tooth-side land
GT2_TOOTH = 0.75                      # tooth height
GT2_THICK = 1.38                      # total belt thickness (6 mm GT2)
BELT_W = 6.0
IDLER_RUN_R = 6.05                    # reference running surface (bearing housing 623h)
GAP = 0.02                            # keeps coincident contact faces apart (no z-fighting)


# ----------------------------------------------------------------------------- helpers
def _revolve(profile):
    """Closed (r, a) profile -> solid of revolution about local z (a = axial coordinate)."""
    return cq.Workplane("XZ").polyline(profile).close().revolve(360, (0, 0, 0), (0, 1, 0))


def _part(shape, M, t, defl=DEFL):
    P, N, I = place(mesh_of(shape, defl), M, np.asarray(t, float))
    return P.astype(np.float32), N.astype(np.float32), I.astype(np.int64)


def _merge(*meshes):
    P, N, I, base = [], [], [], 0
    for p, n, i in meshes:
        P.append(p)
        N.append(n)
        I.append(i + base)
        base += len(p)
    return (np.concatenate(P).astype(np.float32), np.concatenate(N).astype(np.float32),
            np.concatenate(I).astype(np.int64))


def lm8uu():
    """LM8UU in its own frame: axis z, z 0..24.  D 15 / d 8 / L 24, retaining-ring grooves D1 14.3,
    W 1.1, B 17.5 (datasheet; the reference has the same B and W, its grooves are deeper).  End faces:
    0.35 chamfer, sleeve face r 6.65..7.15, ball retainer recessed 1.0 between r 4.5 and 6.65 (reference)."""
    L, R, r = 24.0, 7.5, 4.0
    g0, g1, rg = (L - 17.5) / 2, (L - 17.5) / 2 + 1.1, 14.3 / 2
    prof = [(r, 0), (4.5, 0), (4.5, 1.0), (6.65, 1.0), (6.65, 0), (7.15, 0), (R, 0.35),
            (R, g0), (rg, g0), (rg, g1), (R, g1),
            (R, L - g1), (rg, L - g1), (rg, L - g0), (R, L - g0),
            (R, L - 0.35), (7.15, L), (6.65, L), (6.65, L - 1.0), (4.5, L - 1.0), (4.5, L), (r, L)]
    return _revolve(prof)


def z_nut():
    """Stock TR8x8 nut (POM): local axis z, z 0 = bottom of the body.  Body D13 x 6 (0.3 chamfer),
    flange D25 x 4 (0.3 chamfers), bore D8 (0.3 chamfers), 4 holes D4 on PCD 19 at 45 deg - all
    measured on the reference (solids 94/95); the thread itself is not modelled."""
    prof = [(4.3, 0), (6.2, 0), (6.5, 0.3), (6.5, 6.0), (12.2, 6.0), (12.5, 6.3), (12.5, 9.7),
            (12.2, 10.0), (4.3, 10.0), (4.0, 9.7), (4.0, 0.3)]
    nut = _revolve(prof)
    for k in range(4):
        a = np.radians(45 + 90 * k)
        nut = nut.cut(cq.Workplane("XY").workplane(offset=5.5).center(9.5 * np.cos(a), 9.5 * np.sin(a))
                      .circle(2.0).extrude(5.0))
    return nut


def idler_housing():
    """Prusa 'bearing housing 623h' with its two 623h bearings (3x10x4) - local axis z, z 0..8.7.
    Housing: flanges D18 x 1.1, running surface D12.1 x 6.5 (reference solids 93/44), bore D10.
    Bearings: back to back, recessed 0.35 per side; face shows inner race / shield / outer race."""
    housing = _revolve([(5.0, 0), (9.0, 0), (9.0, 1.1), (IDLER_RUN_R, 1.1), (IDLER_RUN_R, 7.6),
                        (9.0, 7.6), (9.0, 8.7), (5.0, 8.7)])
    s0, s1 = 0.35, 8.35
    brg = _revolve([(1.55, s0), (2.6, s0), (2.6, s0 + 0.15), (4.4, s0 + 0.15), (4.4, s0), (5.0, s0),
                    (5.0, s1), (4.4, s1), (4.4, s1 - 0.15), (2.6, s1 - 0.15), (2.6, s1), (1.55, s1)])
    return housing.union(brg)


def socket_screw(length):
    """ISO 4762 M3 socket head cap screw: head bearing face at z 0, head D5.5 x 3 towards -z
    (hex socket 2.5 AF), plain shank D3 towards +z (threads not modelled)."""
    head = (cq.Workplane("XY").circle(2.75).extrude(-3.0).faces("<Z").edges().chamfer(0.3)
            .cut(cq.Workplane("XY").workplane(offset=-3.0).polygon(6, 2.5 / np.cos(np.pi / 6)).extrude(1.6)))
    return head.union(cq.Workplane("XY").circle(1.5).extrude(length))


def nyloc_nut():
    """M3 nyloc nut (DIN 985): 5.5 AF, h 4.0 - hex 3.0 + nylon collar; bearing face at z 0, towards +z."""
    hexp = cq.Workplane("XY").polygon(6, 5.5 / np.cos(np.pi / 6)).extrude(3.0).edges(">Z").chamfer(0.3)
    collar = cq.Workplane("XY").workplane(offset=3.0).circle(2.6).extrude(1.0).faces(">Z").edges().chamfer(0.4)
    return hexp.union(collar)


def _hull_outline(wp, c0, r0, c1, r1):
    """Closed outline of the convex hull of two circles (open-belt path) on workplane wp."""
    c0, c1 = np.asarray(c0, float), np.asarray(c1, float)
    d = c1 - c0
    L = float(np.hypot(*d))
    u = d / L
    v = np.array([-u[1], u[0]])
    s = (r0 - r1) / L                  # outward normal m of the straight runs: m.u = (r0 - r1) / L
    c = np.sqrt(1 - s * s)
    mp, mm = s * u + c * v, s * u - c * v
    p0p, p1p, p0m, p1m = c0 + r0 * mp, c1 + r1 * mp, c0 + r0 * mm, c1 + r1 * mm
    far1, far0 = c1 + r1 * u, c0 - r0 * u
    return (wp.moveTo(*p0p).lineTo(*p1p).threePointArc(tuple(far1), tuple(p1m))
            .lineTo(*p0m).threePointArc(tuple(far0), tuple(p0p)).close())


def gt2_loop(plane, origin, c_pulley, c_idler):
    """Closed GT2 loop around a 16T pulley and a smooth idler; teeth inwards.  Inner surface = tooth
    tips (pulley r 4.089 = its tooth root, idler = its running surface), outer surface = belt back."""
    tip0 = GT2_PITCH_R16 - GT2_PLD - GT2_TOOTH + GAP          # 4.109
    tip1 = IDLER_RUN_R + GAP                                    # 6.07
    outer = _hull_outline(cq.Workplane(plane, origin=origin), c_pulley, tip0 + GT2_THICK, c_idler,
                          tip1 + GT2_THICK).extrude(BELT_W)
    inner = _hull_outline(cq.Workplane(plane, origin=origin), c_pulley, tip0, c_idler, tip1).extrude(BELT_W)
    return outer.cut(inner)


def pitch_length(c0, r0, c1, r1):
    L = float(np.hypot(*(np.asarray(c1, float) - np.asarray(c0, float))))
    a = np.arcsin((r1 - r0) / L)
    return 2 * L * np.cos(a) + (np.pi + 2 * a) * r1 + (np.pi - 2 * a) * r0


def bearing_clip(cx, zc, t=0.8, width=10.0, span=32.0, hole_dx=CLIP_HOLE_DX, head_r=2.75, head_h=3.0):
    """MK3S+ bearing clip (bent steel sheet, master frame): omega profile around the LM8UU below the
    flat Y-carriage, bearing axis along Z at (cx, 64.379); zc = z of the carriage's hole line.  The arc
    wraps the bearing up to the carriage bottom (the bearing sits 1.9 mm deep in the carriage cutout);
    two flanges lie flat under the carriage with M3 holes at the carriage's holes (x +-10, measured).
    32 mm overall (retailer listing); width 10 and t 0.8 are estimates (no drawing found).
    The arc is relieved by the envelope of the M3 socket heads (r head_r + GAP, head_h below the
    flange): at x +-10 the head reaches x 7.25 while bearing + sheet reach 8.1 at the head's bottom,
    so without the relief the head would sit up to 0.9 mm inside the sheet."""
    cy = LM8UU_Y["lm8uu-y-1"][0][1]
    ri = 7.5 + GAP
    z0 = zc - width / 2
    ring = (cq.Workplane("XY").workplane(offset=z0).center(cx, cy).circle(ri + t).circle(ri).extrude(width)
            .intersect(cq.Workplane("XY").workplane(offset=z0 - 1).center(cx, CARRIAGE_BOT - 15)
                       .rect(40, 30).extrude(width + 2)))
    clip = ring
    for sgn in (-1, 1):
        x_in, x_out = cx + sgn * 5.9, cx + sgn * span / 2
        fl = (cq.Workplane("XY").workplane(offset=z0).center((x_in + x_out) / 2, CARRIAGE_BOT - t / 2)
              .rect(abs(x_out - x_in), t).extrude(width))
        fl = fl.edges("|Y").edges(">X" if sgn > 0 else "<X").fillet(2.5)
        clip = clip.union(fl)
    for sgn in (-1, 1):
        clip = clip.cut(cq.Workplane("XZ", origin=(0, CARRIAGE_BOT + 1, 0)).center(cx + sgn * hole_dx, zc)
                        .circle(1.6).extrude(t + 2))
        # head envelope = socket_screw()'s head grown by GAP: its 0.3 bottom chamfer as a cone, then the
        # cylinder up to y 69.0 (above y 68.5 the arc is inside x 7.23, so the cut never reaches the
        # flange and makes no coplanar faces); so the head fills the relief to within GAP
        hc = cq.Vector(cx + sgn * hole_dx, 0, zc)
        y_bot = CARRIAGE_BOT - t - head_h                      # head's bottom face (y 66.17)
        cone = cq.Solid.makeCone(head_r - 0.3 + GAP, head_r + GAP, 0.3 + GAP,
                                 hc + cq.Vector(0, y_bot - GAP, 0), cq.Vector(0, 1, 0))
        cyl = cq.Solid.makeCylinder(head_r + GAP, 69.0 - (y_bot + 0.3),
                                    hc + cq.Vector(0, y_bot + 0.3, 0), cq.Vector(0, 1, 0))
        clip = clip.cut(cq.Workplane("XY").add(cone.fuse(cyl)))
    return clip


# ----------------------------------------------------------------------------- build
def build():
    out = []
    I3 = np.eye(3)

    # --- belts
    xb = gt2_loop("XY", (0, 0, X_BELT_Z0), X_PULLEY_C, X_IDLER_C)
    out.append(("x-belt", "xaxis", "belt", _part(xb, I3, (0, 0, 0), 0.02)))
    yb = gt2_loop("YZ", (Y_BELT_X0, 0, 0), Y_PULLEY_C, Y_IDLER_C)
    out.append(("y-belt", "static", "belt", _part(yb, I3, (0, 0, 0), 0.02)))

    # --- idlers (housing + bearings, alu) and their axles (M3x18 + nyloc, steel; X screw cut to 17.5)
    hz = frame_for((0, 0, 1))
    hx = frame_for((1, 0, 0))
    hous = idler_housing()
    out.append(("x-idler", "xaxis", "alu", _part(hous, hz, (X_IDLER_C[0], X_IDLER_C[1], X_IDLER_A0))))
    out.append(("y-idler", "static", "alu", _part(hous, hx, (Y_IDLER_A0, Y_IDLER_C[0], Y_IDLER_C[1]))))
    scr, scr_x, nut = socket_screw(18.0), socket_screw(X_SCREW_LEN), nyloc_nut()
    out.append(("x-idler-axle", "xaxis", "steel", _merge(
        _part(scr_x, hz, (X_IDLER_C[0], X_IDLER_C[1], X_SCREW_HEAD)),
        _part(nut, hz, (X_IDLER_C[0], X_IDLER_C[1], X_NUT_FACE))))
    )
    out.append(("y-idler-axle", "static", "steel", _merge(
        _part(scr, hx, (Y_SCREW_HEAD, Y_IDLER_C[0], Y_IDLER_C[1])),
        _part(nut, hx, (Y_NUT_FACE, Y_IDLER_C[0], Y_IDLER_C[1]))))
    )

    # --- Z nuts
    hy = frame_for((0, 1, 0))
    zn = z_nut()
    for name, (x, z) in Z_NUTS.items():
        out.append((name, "xaxis", "pom", _part(zn, hy, (x, Z_NUT_Y0, z))))

    # --- linear bearings
    lb = lm8uu()
    for name, ((x, y), z0) in LM8UU_Y.items():
        out.append((name, "bed", "steel", _part(lb, hz, (x, y, z0), 0.08)))
    for name, ((y, z), x0) in LM8UU_X.items():
        out.append((name, "carriage", "steel", _part(lb, hx, (x0, y, z), 0.08)))
    for name, ((x, z), y0) in LM8UU_Z.items():
        out.append((name, "xaxis", "steel", _part(lb, hy, (x, y0, z), 0.08)))

    # --- Y-bearing clips + their M3x12 screws (heads under the clip) and nyloc nuts (carriage top)
    t_clip = 0.8
    scr12 = socket_screw(CARRIAGE_TOP + 4.0 + 1.0 - (CARRIAGE_BOT - t_clip))   # real M3x12 shortened, see notes
    for k, (name, ((x, _y), z0)) in enumerate(LM8UU_Y.items(), start=1):
        zc = z0 + 12.0 + CLIP_HOLE_DZ[name]           # the carriage's hole line, not the bearing centre
        parts = [_part(bearing_clip(x, zc, t=t_clip), I3, (0, 0, 0))]
        for sgn in (-1, 1):
            parts.append(_part(scr12, hy, (x + sgn * CLIP_HOLE_DX, CARRIAGE_BOT - t_clip, zc)))
            parts.append(_part(nut, hy, (x + sgn * CLIP_HOLE_DX, CARRIAGE_TOP, zc)))
        out.append((f"y-bearing-clamp-{k}", "bed", "steel", _merge(*parts)))
    return out


# ----------------------------------------------------------------------------- self-check
