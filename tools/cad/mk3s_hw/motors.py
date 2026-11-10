"""PrintTwin MK3S+ (flat frame): stepper motors, GT2 pulleys, Z motors with integrated lead screws.

Every shape here is built from CadQuery primitives with dimensions MEASURED on Jonathan Kayne's MK3S
SolidWorks assembly (thingiverse 3434917, GPL; used as a measuring reference only - none of its
triangles are used, and build() does not read it) or taken from the parts' data:
  * X / Y / E motors: LDO NEMA17, 40 mm body (LDO-42STH40 class; the reference's "NEMA-17 40mm").
  * Z motors: LDO NEMA17 with integrated Tr8 lead screw, 34 mm body (LDO "42STH34" Z motor sold for
    the MK2..MK3S+; FYSETC's replacement is "42-34 with integrated 320 mm T8 lead screw").
  * Pulleys: GT2 16T, 5 mm bore: pitch diameter 16 * 2 mm / pi = 10.186 mm.
No text, no logos (the reference's motor labels are left out).

Master frame: mm, X right, Y up, Z towards the front. X / Y / E motors and the pulleys sit at the
reference pose (X rods y 319.7 / 364.7 at z 250, X motor face on the x-end-motor's rear face z 241.55;
extruder as in the reference); the build script moves the groups. The Z motors + lead screws keep our
build's Z axis (axes x -12.75 / 317.25, z 265.05; face under the current z-axis-bottom's 7 mm plate), which
differs from the reference's older R2 z-axis-bottom (5 mm plate) by 1.98 mm in face height.
The lead screws are plain 8 mm rods (no thread geometry).
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

sys.path.insert(0, _CAD)
import numpy as np  # noqa: E402
import cadquery as cq  # noqa: E402
from build_mk4 import mesh_of, place  # noqa: E402

NEW_MATS: dict = {}

# ----------------------------------------------------------------------------- measured dimensions
# NEMA17 (all measured on the reference X/Y/E motors, which agree to 0.01 mm), local frame:
# mounting face z = 0, body towards -z, shaft towards +z, cable exit towards local -y.
CAP_HALF, CAP_LEG, CAP_R = 21.0, 3.8, 2.0         # end caps 42 x 42, 45-degree corner chamfer x+y = 38.2, r 2 blends
STATOR_HALF, STATOR_LEG, STATOR_R = 21.15, 6.4, 3.9  # stator stack 42.3 x 42.3, chamfer x+y = 35.9, r 3.9 blends
REAR_CAP_L = 12.0                                  # rear cap 201.55..213.55 (X motor)
STATOR_Z = (-31.5, -8.0)                           # stator 210.05..233.55 on the X motor (face 241.55), 40 mm body
FRONT_CAP_Z0 = -10.6                               # front cap 230.95..241.55
EDGE_CH = 0.5      # outer edge of the face / rear face: r 0.5 round in the reference, a 0.5 chamfer here
                   # (< 0.15 mm apart; saves ~2300 triangles per motor)
BOSS_R, BOSS_H, BOSS_CH = 11.0, 2.0, 0.45          # pilot 22 mm x 2 mm (241.55..243.55, r 10.58 at the top)
FACE_RECESS = (11.0, 16.5, 0.5)                    # face: ring r 11..16.5 sunk 0.5 (241.05; raised outer face r > 16.5)
BOSS_GROOVE = (11.0, 11.5, 0.7)                    # ... and a groove round the pilot, 0.7 deep (240.85)
BOSS_BORE_R, BOSS_BORE_D = 3.65, 1.4               # pilot bore round the shaft, floor 1.4 down (242.15, bearing face)
REAR_BORE_R, REAR_BORE_D = 4.5, 2.5                # rear bearing pocket r 4.5, 2.5 deep (204.05); shaft end inside
SCREW_XY, CBORE_R, CBORE_D = 15.5, 2.9, 0.6        # 31 mm screw square; heads r 2.75 sit 0.6 below the rear face
HEX_AF, HEX_D = 1.2, 1.0                           # small socket in the screw heads (reference: r 0.69 polygon)
SHAFT_R, SHAFT_Z, SHAFT_CH = 2.5, (-38.0, 22.0), 0.5  # 5 mm shaft 203.55..263.55, 0.5 chamfer at the tip
# cable exit of the X/Y/E motors: a block in the rear cap's side (0.5 proud) and 4 wire stubs (2 x 2, 2 mm apart)
GLAND_W, GLAND_A = 7.78, 6.87                      # across x 7.78, along the axis 6.87 (203.18..210.05)
GLAND_Y = (-18.0, -21.5)                           # 3 mm inside the cap side to 0.5 outside (322.43..318.93)
GLAND_Z0 = 1.63                                    # starts 1.63 mm in front of the rear face
WIRE_R, WIRE_Y1, WIRE_PITCH = 0.8, -28.2, 2.0      # insulated wires end 6.7 mm below the block (312.23)
# Z motor: cable leaves through a 3.1 mm grommet stub on the rear side, 3 mm above the motor's end, 5 mm long
Z_BODY = 34.0
Z_STUB_R, Z_STUB_UP, Z_STUB_Y1 = 1.55, 3.0, -26.15  # stub ends 26.15 from the axis (238.9 vs axis 265.05)

# GT2 16T pulley (reference pulleys 92 and 57, identical part), local frame: flange end face z = 0, hub at the top
PUL_FLANGE_R, PUL_FLANGE_T, PUL_BEVEL_R1 = 7.35, 0.45, 6.57   # 14.7 mm flange 0.45 thick + 0.45 bevel to r 6.57
PUL_TEETH_Z = (0.9, 8.3)                                       # toothed section 7.4 long (247.35..254.75)
PUL_HUB_Z = (8.75, 14.3)                                       # hub 14.7 dia (255.2..260.75)
PUL_TIP_R, PUL_LAND_DEG = 4.85, 6.07                           # tooth tip r 4.85, land 6.07 deg wide
# groove flank measured from the land edge (deg offset, radius); the groove is symmetric, root r 4.088
# (every 2nd measured point; linear in between stays within 0.04 mm of the measured flank)
PUL_FLANK = [(0.0, 4.85), (0.375, 4.772), (0.785, 4.609), (1.81, 4.356), (3.33, 4.220), (5.41, 4.127),
             (7.82, 4.088)]
PUL_HOLE_R, PUL_HOLE_Z = 1.7, 11.9                             # two radial M3 holes in the hub
PITCH_DIA = 16 * 2.0 / np.pi                                   # 10.186 mm

# ----------------------------------------------------------------------------- poses (master frame)
# M: columns = local x, y, z in the master frame; t = local origin (centre of the mounting face).
X_AXIS_C = (-61.741, 340.433)        # X motor axis (x, y): body -82.74..-40.74 / 319.43..361.43, shaft -61.74 / 340.435
X_FACE = 241.55                      # X motor face z (front cap face; boss top 243.55, shaft tip 263.55)
Y_AXIS_C = (46.373, 130.500)         # Y motor axis (y, z): body 25.37..67.37 / 109.5..151.5
Y_FACE = 139.27                      # Y motor face x (boss top 141.27, shaft tip 161.27)
E_AXIS_C = (64.972, 371.685)         # E motor axis (x, y): body 43.97..85.97 / 350.68..392.68
E_FACE = 289.70                      # E motor face z; shaft points to the rear (boss 287.7, tip 267.7)
# Z: our build's values (Prusa drawing + current z-axis-bottom STL: motor face under its 7 mm plate)
Z_AXES = (("left", -12.75), ("right", 317.25))   # drawing x 20 / 350 - 32.75 (reference -12.741 / 317.259)
Z_AXIS_Z = 265.05                    # 29.3 mm in front of the frame face 235.75 (reference 265.049)
Z_FACE = 67.2                        # drawing y 44.8 + 22.4 (reference R2 part with a 5 mm plate: 69.18)
SCREW_R, SCREW_TOP = 4.0, 389.2      # Tr8 lead screw, top = reference 389.18 (= Z rod tops)

M_X = np.eye(3)                                                       # shaft +z, cable -y
M_Y = np.column_stack([[0, 0, -1.0], [0, 1.0, 0], [1.0, 0, 0]])     # shaft +x, cable -y
M_E = np.column_stack([[-1.0, 0, 0], [0, 1.0, 0], [0, 0, -1.0]])    # shaft -z, cable -y
M_Z = np.column_stack([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])     # shaft +y, cable -z (towards the frame)
# pulleys: X flange towards the motor (flange face z 246.45, hub outboard to 260.75); Y reversed (hub at the
# motor 142.56..148.11, flange face outboard at x 156.86). Local x chosen so tooth / hole phases match.
X_PUL_T, X_PUL_M = np.array([X_AXIS_C[0], X_AXIS_C[1], 246.45]), np.eye(3)
Y_PUL_T = np.array([156.86, Y_AXIS_C[0], Y_AXIS_C[1]])
Y_PUL_M = np.column_stack([[0, 1.0, 0], [0, 0, -1.0], [-1.0, 0, 0]])  # local z -> -x (towards the motor)
X_PUL_PHASE, X_PUL_HOLES = 5.04, (22.0, -45.0)      # tooth-land centre and hole angles in the local xy plane
Y_PUL_PHASE, Y_PUL_HOLES = 1.02, (-60.0, -125.0)


# ----------------------------------------------------------------------------- shapes (local frames)
def _prism(half, leg, rad, z0, z1):
    w = cq.Workplane("XY").workplane(offset=z0).rect(2 * half, 2 * half).extrude(z1 - z0)
    return w.edges("|Z").chamfer(leg).edges("|Z").fillet(rad)


def motor_body(length):
    """Black NEMA17 housing (caps + stator + pilot + rear screw heads), face z = 0, body to -length."""
    rear = _prism(CAP_HALF, CAP_LEG, CAP_R, -length, -length + REAR_CAP_L).faces("<Z").edges().chamfer(EDGE_CH)
    front = _prism(CAP_HALF, CAP_LEG, CAP_R, FRONT_CAP_Z0, 0.0).faces(">Z").edges().chamfer(EDGE_CH)
    stator = _prism(STATOR_HALF, STATOR_LEG, STATOR_R, -length + (40.0 + STATOR_Z[0]), STATOR_Z[1])
    boss = cq.Workplane("XY").circle(BOSS_R).extrude(BOSS_H).faces(">Z").edges().chamfer(BOSS_CH)
    body = rear.union(stator).union(front).union(boss)
    ring = lambda r0, r1, d, z: cq.Workplane("XY").workplane(offset=z).circle(r1).circle(r0).extrude(d)  # noqa: E731
    body = body.cut(ring(FACE_RECESS[0], FACE_RECESS[1], FACE_RECESS[2], -FACE_RECESS[2]))
    body = body.cut(ring(BOSS_GROOVE[0], BOSS_GROOVE[1], BOSS_GROOVE[2], -BOSS_GROOVE[2]))
    body = body.cut(cq.Workplane("XY").workplane(offset=BOSS_H - BOSS_BORE_D).circle(BOSS_BORE_R).extrude(BOSS_BORE_D + 0.1))
    body = body.cut(cq.Workplane("XY").workplane(offset=-length - 0.1).circle(REAR_BORE_R).extrude(REAR_BORE_D + 0.1))
    pts = [(sx * SCREW_XY, sy * SCREW_XY) for sx in (-1, 1) for sy in (-1, 1)]
    cb = cq.Workplane("XY").workplane(offset=-length).pushPoints(pts).circle(CBORE_R).extrude(CBORE_D)
    hexes = (cq.Workplane("XY").workplane(offset=-length + CBORE_D - 0.01).pushPoints(pts)
             .polygon(6, HEX_AF / np.cos(np.radians(30))).extrude(HEX_D))
    return body.cut(cb).cut(hexes)


def motor_shaft():
    s = cq.Workplane("XY").workplane(offset=SHAFT_Z[0]).circle(SHAFT_R).extrude(SHAFT_Z[1] - SHAFT_Z[0])
    return s.faces(">Z").edges().chamfer(SHAFT_CH).faces("<Z").edges().chamfer(SHAFT_CH)


def cable_gland(length):
    z0 = -length + GLAND_Z0
    return (cq.Workplane("XY").box(GLAND_W, GLAND_Y[0] - GLAND_Y[1], GLAND_A, centered=(True, False, False))
            .translate((0, GLAND_Y[1], z0)))


def gland_wires(length):
    zc = -length + GLAND_Z0 + GLAND_A / 2
    w = None
    for dx in (-WIRE_PITCH / 2, WIRE_PITCH / 2):
        for dz in (-WIRE_PITCH / 2, WIRE_PITCH / 2):
            c = (cq.Workplane("XZ").workplane(offset=-GLAND_Y[1] - 0.2).center(dx, zc + dz).polygon(10, 2 * WIRE_R)
                 .extrude(GLAND_Y[1] + 0.2 - WIRE_Y1))
            w = c if w is None else w.union(c)
    return w


def z_cable_stub(length):
    return (cq.Workplane("XZ").workplane(offset=20.5).center(0, -length + Z_STUB_UP).circle(Z_STUB_R)
            .extrude(-Z_STUB_Y1 - 20.5))


def gt2_profile(phase_deg):
    """Closed 16-tooth GT2 outline (list of (x, y)), tooth-land centres at phase + k * 22.5 deg."""
    half = 22.5 / 2
    flank = [(PUL_LAND_DEG / 2 + d, r) for d, r in PUL_FLANK]            # offsets from the land centre
    one = [(-PUL_LAND_DEG / 2, PUL_TIP_R), (PUL_LAND_DEG / 2, PUL_TIP_R)] + flank[1:]
    one += [(half, 4.086)] + [(22.5 - a, r) for a, r in reversed(flank[1:])]   # mirrored second flank
    pts = []
    for k in range(16):
        for a, r in one:
            t = np.radians(phase_deg + 22.5 * k + a)
            pts.append((float(r * np.cos(t)), float(r * np.sin(t))))
    return pts


def gt2_pulley(phase_deg, holes_deg):
    fl0 = cq.Workplane("XY").circle(PUL_FLANGE_R).extrude(PUL_FLANGE_T)
    bev0 = cq.Solid.makeCone(PUL_FLANGE_R, PUL_BEVEL_R1, PUL_TEETH_Z[0] - PUL_FLANGE_T,
                             cq.Vector(0, 0, PUL_FLANGE_T), cq.Vector(0, 0, 1))
    teeth = (cq.Workplane("XY").workplane(offset=PUL_TEETH_Z[0]).polyline(gt2_profile(phase_deg)).close()
             .extrude(PUL_TEETH_Z[1] - PUL_TEETH_Z[0]))
    bev1 = cq.Solid.makeCone(PUL_BEVEL_R1, PUL_FLANGE_R, PUL_HUB_Z[0] - PUL_TEETH_Z[1],
                             cq.Vector(0, 0, PUL_TEETH_Z[1]), cq.Vector(0, 0, 1))
    hub = cq.Workplane("XY").workplane(offset=PUL_HUB_Z[0]).circle(PUL_FLANGE_R).extrude(PUL_HUB_Z[1] - PUL_HUB_Z[0])
    p = fl0.union(cq.Workplane().add(bev0)).union(teeth).union(cq.Workplane().add(bev1)).union(hub)
    p = p.cut(cq.Workplane("XY").workplane(offset=-0.1).circle(2.5).extrude(PUL_HUB_Z[1] + 0.2))   # 5 mm bore
    for a in holes_deg:
        t = np.radians(a)
        d = cq.Vector(float(np.cos(t)), float(np.sin(t)), 0)
        h = cq.Solid.makeCylinder(PUL_HOLE_R, PUL_FLANGE_R - 2.5 + 0.5, cq.Vector(0, 0, PUL_HOLE_Z) + d * 2.5, d)
        p = p.cut(cq.Workplane().add(h))
    return p


def lead_screw():
    """Master frame: Tr8 lead screw as a plain 8 mm rod from the motor face to the top, 0.5 chamfer on top."""
    s = cq.Workplane("XZ").circle(SCREW_R).extrude(-(SCREW_TOP - Z_FACE))     # XZ normal is -y: extrude(-) -> +y
    return s.faces(">Y").edges().chamfer(0.5)


# ----------------------------------------------------------------------------- build
def build():
    out = []

    def add(name, group, mat, shape, M, t, defl=0.06):
        out.append((name, group, mat, place(mesh_of(shape, defl), M, np.asarray(t, dtype=float))))

    body40 = motor_body(40.0)
    shaft = motor_shaft()
    gland = cable_gland(40.0)
    wires = gland_wires(40.0)
    for key, grp, M, t in (("x", "xaxis", M_X, (X_AXIS_C[0], X_AXIS_C[1], X_FACE)),
                           ("y", "static", M_Y, (Y_FACE, Y_AXIS_C[0], Y_AXIS_C[1])),
                           ("e", "carriage", M_E, (E_AXIS_C[0], E_AXIS_C[1], E_FACE))):
        add(f"{key}-motor", grp, "motor", body40, M, t)
        add(f"{key}-motor-shaft", grp, "steel", shaft, M, t, 0.03)
        add(f"{key}-motor-cable-gland", grp, "fan", gland, M, t, 0.03)
        add(f"{key}-motor-wires", grp, "rubber", wires, M, t, 0.03)
    add("x-pulley", "xaxis", "alu", gt2_pulley(X_PUL_PHASE, X_PUL_HOLES), X_PUL_M, X_PUL_T, 0.04)
    add("y-pulley", "static", "alu", gt2_pulley(Y_PUL_PHASE, Y_PUL_HOLES), Y_PUL_M, Y_PUL_T, 0.04)

    body34 = motor_body(Z_BODY)
    stub = z_cable_stub(Z_BODY)
    screw = lead_screw()
    for side, x in Z_AXES:
        t = (x, Z_FACE, Z_AXIS_Z)
        add(f"z-motor-{side}", "static", "motor", body34, M_Z, t)
        add(f"z-motor-{side}-cable", "static", "rubber", stub, M_Z, t, 0.03)
        add(f"z-leadscrew-{side}", "static", "leadscrew", screw, np.eye(3), (x, Z_FACE, Z_AXIS_Z), 0.03)
    return out


ANCHORS = {
    # belt centre plane = middle of the toothed section; axis = shaft direction (away from the motor)
    # (reference toothed bands: X z 247.35..254.75, Y x 148.56..155.96; tooth tips r 4.85 = GT2 16T OD 9.70)
    "x_pulley": {"center": [X_AXIS_C[0], X_AXIS_C[1], round(246.45 + (PUL_TEETH_Z[0] + PUL_TEETH_Z[1]) / 2, 3)],
                 "axis": [0.0, 0.0, 1.0], "pitch_dia": round(float(PITCH_DIA), 4)},
    "y_pulley": {"center": [round(156.86 - (PUL_TEETH_Z[0] + PUL_TEETH_Z[1]) / 2, 3), Y_AXIS_C[0], Y_AXIS_C[1]],
                 "axis": [1.0, 0.0, 0.0], "pitch_dia": round(float(PITCH_DIA), 4)},
}


# ----------------------------------------------------------------------------- self-check (reads the reference)
