"""PrintTwin - Original Prusa i3 MK3S+ (stock): hotend fan + print fan, group "carriage".

Every shape here is built from CadQuery primitives (boxes, cylinders, arcs, revolves, lofts, cuts,
fillets).  The numbers were MEASURED on Kayne's MK3S SolidWorks assembly (a measuring reference
only - slices, ray casts, circle fits; none of its triangles is used and build() does not read it)
or taken from datasheets; the comment next to each number says which.  No text / logos.

Master frame: mm, X right, Y up, Z towards the front.  Parts are built at the reference pose
(X carriage centre x ~68.5, X rods at z 250); the main build script moves the "carriage" group.

  hotend-fan          Noctua NF-A4x10 FLX frame (beige): bore with flared inlet + chamfered outlet,
                      corner flanges (recessed 1 mm on both faces) with counterbored holes, side notches
                      between the flanges, 4 curved struts and the stator hub on the outlet side (+X)
  hotend-fan-rotor    its rotor (brown): 8.3 mm hub + 7 lofted, twisted blades.  The reference fan has
                      NO rotor (frame + struts only) - the rotor is designed from the datasheet/photos
  print-fan           5015 radial blower housing (black), tilted 42 deg about X: two-arc volute with the
                      tongue, outlet duct (pointing down and back), two mounting ears, inlet (r 16) on the
                      lower-front face, ribbed motor-side plate with the cable slot, clip and wire tab
  print-fan-impeller  its impeller: cup hub + flange + 33 forward-curved blades (visible through the inlet)

ANCHORS  {"hotend_fan_axis", "print_fan_axis"}: center = a point on the rotor axis (mid-plane of the
rotor), dir = unit axis pointing the way the air flows through the rotor (hotend fan: +X into the
heatsink; blower: from the inlet into the housing).  Extra key "spin": the real rotation is
right-handed about spin*dir, i.e. spin = -1 means the rotor turns clockwise when seen from the
downstream side (looking back against dir).
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

import numpy as np

sys.path.insert(0, _CAD)
import cadquery as cq  # noqa: E402
from build_mk4 import place  # noqa: E402
from glb import tessellate  # noqa: E402  (what build_mk4.mesh_of calls; here with a coarser angular step)

NEW_MATS = {
    # Noctua's beige frame / brown rotor (sRGB, eyeballed from product photos), matte-satin plastic
    "noctua-beige": ((0.80, 0.71, 0.59), 0.0, 0.55),
    "noctua-brown": ((0.40, 0.22, 0.16), 0.0, 0.45),
}

# ============================================================================ hotend fan (NF-A4x10)
# Local frame: a = master y - NF_CY, b = master z - NF_CZ, axial = master x (absolute).
NF_CY, NF_CZ = 328.120, 281.765   # centre of the frame square (ref 142 bbox centre 328.119 / 281.765)
NF_X0, NF_X1 = 34.974, 45.972     # intake face / outlet face (ref 142 bbox x; datasheet says 10 mm thick,
#                                   the reference has 11.0 at the side centres and 9.0 at the corner flanges)
NF_HALF = 20.03                   # 40.06 square (ref bbox; datasheet 40)
NF_RC = 2.1                       # rounded frame corners (circle fit on slices x 36.3 / 44.6: R 2.09 / 2.11)
NF_HOLE = 16.065                  # hole centres at +-16.065 (circle fit; datasheet pitch 32)
NF_HOLE_R = 2.157                 # mounting hole (circle fit r 2.157)
NF_CB_R, NF_CB_X = 2.87, 37.895   # intake-side counterbore r 2.87 down to x 37.895 (ray casts)
NF_CORNER = 13.25                 # corner flanges: |a|,|b| > 13.25 recessed 1.0 mm on both faces (height maps)
NF_FLAT = 19.03                   # the side walls stay 1.0 thick: bore clipped to +-19.03 (slices)
NF_BORE = 19.36                   # bore radius between x 36.6 and 44.3 (slices)
NF_FLARE = [(0.00, 20.73), (0.33, 20.10), (0.63, 19.78), (0.93, 19.58), (1.23, 19.45), (1.63, 19.37),
            (2.00, NF_BORE)]      # intake flare: (depth from the intake face, radius) from slices
NF_CHAMF_X = 44.30                # outlet chamfer: r 19.36 at x 44.30 -> 20.73 at the outlet face (slices)
NF_RING = 20.9                    # bore ring outer radius between the flanges (arc through (6.0,20.03), (11.6,17.4))
NF_RING_X = [(36.574, 24.0), (36.574, 22.0), (37.6, 21.1), (38.4, NF_RING), (43.0, NF_RING), (43.6, 21.3),
             (44.372, 22.0), (44.372, 24.0)]   # (x, r): the ring widens towards both flanges (notch start traced
#                                               on slices x 36.8 .. 44.2: r 21.8, 21.1, 20.9 .. 20.97, 21.3, 21.9)
NF_BOSS = 4.66                    # corner boss radius around each hole between the flanges
NF_NOTCH = (36.574, 44.372)       # side notches between the flanges (ray hits at (12, 19.6))
NF_STRUT = (43.972, 45.718)       # strut + stator-hub plate (ray hits through struts / hub)
NF_HUB_R = 8.48                   # stator hub radius (window boundaries r 8.48)
NF_RIM = (8.0, 45.972)            # raised rim r 8.0..8.48 up to the outlet face (ray hits r 8.2)


def _pol(r, deg):
    a = np.radians(deg)
    return (float(r * np.cos(a)), float(r * np.sin(a)))


# One strut window (the one centred at -132 deg); the 4 windows repeat every 90 deg (measured on the slice
# x 44.85: edge 1 is an arc R 48.1 (rms 0.01), edge 2 an arc R 16.8 (rms 0.18); both extended beyond the bore)
NF_WIN = {
    "H2": _pol(NF_HUB_R, 162.2), "Hm": _pol(NF_HUB_R, 191.55), "H1": _pol(NF_HUB_R, 220.9),   # hub arc
    "E1m": (-0.92, -14.40), "E1o": (5.52, -20.88),                                            # edge 1
    "Om": _pol(21.6, -113.0), "E2o": (-18.83, -10.54),                                        # outside the bore
    "E2m": (-14.84, -1.69),                                                                   # edge 2
}

# Rotor: designed (no reference).  7 blades (retail spec sheets: 7-blade NF-A4x10), hub r 8.3 (stator hub
# 8.48), tips r 18.95 (0.41 clear of the bore, 0.08 of the side-wall flats), hub x 36.1..43.6, blades
# x 36.0..43.7 (0.3 clear of the stator plate at 43.97).  Blade pitch/chord/sweep are a design, not measured.
NR_HUB_R, NR_Z0, NR_Z1 = 8.3, 36.1, 43.6
NR_BLADES = 7
NR_ZC = 39.85
#            r      chord  pitch  sweep  thickness
NR_SECT = [(7.6, 9.5, 52.0, 0.0, 1.2),        # planar sections (tangent planes at r); the tip section lies
           (13.0, 11.0, 38.0, 5.0, 1.0),       # outside the tip radius and the blade is trimmed to a cylinder
           (19.6, 13.5, 29.0, 12.0, 0.8)]
NR_TIP = 18.95
NR_SPIN = -1.0      # rotation about +X (airflow) is negative: CCW seen from the intake, CW from the outlet


def _wp(z=0.0):
    return cq.Workplane("XY").workplane(offset=z)


def _box(x0, x1, y0, y1, z0, z1):
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def _cyl(r, z0, z1, cx=0.0, cy=0.0):
    return _wp(z0).center(cx, cy).circle(r).extrude(z1 - z0)


def _noctua_frame():
    X0, X1 = NF_X0, NF_X1
    body = _box(-NF_HALF, NF_HALF, -NF_HALF, NF_HALF, X0, X1).edges("|Z").fillet(NF_RC)
    holes = [(sx * NF_HOLE, sy * NF_HOLE) for sx in (-1, 1) for sy in (-1, 1)]
    # corner flanges: 1.0 mm recess on the intake face, and on the outlet face except a ring around the hole
    for hx, hy in holes:
        sx, sy = np.sign(hx), np.sign(hy)
        xa, xb = sorted((sx * NF_CORNER, sx * (NF_HALF + 2)))
        ya, yb = sorted((sy * NF_CORNER, sy * (NF_HALF + 2)))
        body = body.cut(_box(xa, xb, ya, yb, X0 - 1, X0 + 1.0))
        body = body.cut(_box(xa, xb, ya, yb, X1 - 1.0, X1 + 1).cut(_cyl(NF_CB_R, X1 - 2, X1 + 2, hx, hy)))
    # side notches between the flanges: outside the bore ring and the corner bosses
    ring = (cq.Workplane("XZ").polyline([(0.0, X0 - 1), (24.0, X0 - 1)] + [(r, x) for x, r in NF_RING_X]
                                        + [(24.0, X1 + 1), (0.0, X1 + 1)]).close().revolve(360, (0, 0, 0), (0, 1, 0)))
    notch = _box(-30, 30, -30, 30, NF_NOTCH[0], NF_NOTCH[1]).cut(ring)
    for hx, hy in holes:
        notch = notch.cut(_cyl(NF_BOSS, X0 - 1, X1 + 1, hx, hy))
    body = body.cut(notch)
    # bore: flared inlet, straight, chamfered outlet; clipped to the 1.0 mm side walls
    flare = [(r, X0 + d) for d, r in NF_FLARE if d in (0.0, 0.63, 1.23, 2.0)]   # 3 cones through the flare
    prof = ([(0.0, X0 - 1), (NF_FLARE[0][1], X0 - 1)] + flare
            + [(NF_BORE, NF_CHAMF_X), (NF_FLARE[0][1], X1), (NF_FLARE[0][1], X1 + 1), (0.0, X1 + 1)])
    s = cq.Workplane("XZ").polyline(prof).close().revolve(360, (0, 0, 0), (0, 1, 0))
    bore = s.intersect(_box(-NF_FLAT, NF_FLAT, -NF_FLAT, NF_FLAT, X0 - 2, X1 + 2))
    body = body.cut(bore)
    # mounting holes + intake counterbores
    for hx, hy in holes:
        body = body.cut(_cyl(NF_HOLE_R, X0 - 1, X1 + 1, hx, hy)).cut(_cyl(NF_CB_R, X0 - 1, NF_CB_X, hx, hy))
    # outlet side: 4 curved struts + stator hub (plate minus 4 windows) and the hub rim
    plate = _cyl(21.0, NF_STRUT[0], NF_STRUT[1]).intersect(
        _box(-NF_HALF + 0.1, NF_HALF - 0.1, -NF_HALF + 0.1, NF_HALF - 0.1, X0, X1))
    W = NF_WIN
    win = (_wp(NF_STRUT[0] - 1).moveTo(*W["H2"]).threePointArc(W["Hm"], W["H1"]).threePointArc(W["E1m"], W["E1o"])
           .threePointArc(W["Om"], W["E2o"]).threePointArc(W["E2m"], W["H2"]).close().extrude(NF_STRUT[1] - NF_STRUT[0] + 2))
    for k in range(4):
        plate = plate.cut(win.rotate((0, 0, 0), (0, 0, 1), 90.0 * k))
    rim = _cyl(NF_HUB_R, NF_STRUT[1] - 0.1, NF_RIM[1]).cut(_cyl(NF_RIM[0], NF_STRUT[1] - 1, NF_RIM[1] + 1))
    return body.union(plate).union(rim)


def _noctua_rotor():
    hub = _cyl(NR_HUB_R, NR_Z0, NR_Z1).faces("<Z").edges().chamfer(0.8)     # chamfer: far cheaper than a torus
    fs = [0.0, 0.15, 0.4, 0.7, 1.0]
    half = [0.25, 0.8, 1.0, 0.75, 0.2]          # half-thickness factors (round LE, thin TE)
    tipcyl = _cyl(NR_TIP, NR_Z0 - 5, NR_Z1 + 5).val()
    rot = hub.val()
    for k in range(NR_BLADES):
        th0 = 360.0 * k / NR_BLADES
        wires = []
        for r, c, beta, sweep, t in NR_SECT:
            b = np.radians(beta)
            d = np.array([np.cos(b), np.sin(b)])           # chord LE -> TE in (tangential +theta, z)
            ns = np.array([np.sin(b), -np.cos(b)])         # suction side (upstream, behind the motion)
            cam = 0.06 * c
            tc = np.radians(th0 + sweep)
            er, et = np.array([np.cos(tc), np.sin(tc), 0.0]), np.array([-np.sin(tc), np.cos(tc), 0.0])
            pts = []
            for side in (-1, 1):
                seq = list(zip(fs, half)) if side < 0 else list(zip(fs, half))[::-1]
                for f, h in seq:
                    p = (f - 0.5) * c * d + ns * (cam * 4 * f * (1 - f) + side * h * t / 2)
                    q = r * er + p[0] * et
                    pts.append(cq.Vector(float(q[0]), float(q[1]), NR_ZC + float(p[1])))
            wires.append(cq.Wire.makePolygon(pts, close=True))
        blade = cq.Solid.makeLoft(wires, ruled=False).intersect(tipcyl)
        rot = rot.fuse(blade)
    return rot.clean()


# ============================================================================ print fan (5015 blower)
# Local frame: a along u, b along v, c along w (axis), origin = impeller axis on the inlet face.
B_TILT = 42.0                     # axis = (0, cos, -sin): area-weighted normal of the housing plates -> 42.000 deg
B_U0, B_V0, B_W0 = 86.579, -453.020, 16.76   # impeller axis in (u, v) (inlet-circle fit 86.579/-453.020; tip
#                                              circle 86.581/-453.018; ear holes at round offsets), inlet face w
_ct, _st = np.cos(np.radians(B_TILT)), np.sin(np.radians(B_TILT))
B_M = np.column_stack([[1.0, 0, 0], [0, -_st, -_ct], [0, _ct, -_st]])   # columns u, v (outlet dir), w (axis)
B_T = B_M @ np.array([B_U0, B_V0, B_W0])
B_H = 15.0                        # housing height w 16.76..31.76 (ref bbox in the fan frame) = the "15" of 5015
B_PLATE = 1.2                     # inlet plate c 0..1.2, motor plate c 13.8..15.0 (axial sections)
B_WALL = 1.0                      # volute / duct wall (sections: r_out - r_in = 0.99..1.00)
B_ARC_B = ((1.70, 0.0), 22.10)    # volute exterior, tongue -> +a axis: circle fit, residual 0.007
B_ARC_A = ((-1.85, 0.0), 25.65)   # volute exterior, +a axis -> -a axis: circle fit R 25.644, residual 0.010
B_LEFT, B_OUT = -27.5, 25.4       # straight outer duct wall a = -27.5, outlet face b = 25.4 (fan-frame bbox)
B_TONGUE = -7.5                   # tongue outer face a = -7.5 (inner -8.5)
B_TFIL = 2.0                      # tongue fillet r 2.0 (exterior) / 3.0 (cavity), tangent to arc B
B_EARS = [(20.0, 18.0), (-23.0, -20.0)]       # ear hole centres (circle fits: exactly these offsets)
B_EAR_R, B_EAR_HOLE = 3.3, 2.247              # ear ring r 3.3, hole r 2.247 (circle fits)
B_INLET = 16.0                    # inlet hole r (circle fit 15.99..16.00)
B_CELLS = ((0.685, -0.686), 21.867, 12.0, 0.5)  # motor plate recess: outer circle fit, inner r 12.0, 0.5 deep
B_SPOKE = 1.0                     # radial ribs between the recesses (8 at 45 deg; the 0-deg one is the cable slot)
B_SLOT_RIB = 3.35                 # rib edges beside the cable slot at b = +-3.35
B_CHOLE = 2.5                     # centre hole r 2.5 (circle fit 2.498)
B_SLOT = [(4.5, -4.5), (8.7, -4.5), (10.6, -2.6), (30.0, -2.6), (30.0, 2.6), (10.6, 2.6), (8.7, 4.5), (4.5, 4.5)]
B_NOTCH_C = 13.04                 # cable notch in the wall top from w 29.8 (section at 0 deg)
B_CLIP = (19.4, 21.9, -2.6, 0.4, 14.24, 15.0)   # cable clip over the slot (slices w 31.1 / 31.5)
B_TAB = (11.014, 15.514, -2.6, 2.6, 13.8, 14.3)  # flat wire tab in the slot (ref solid 150, 0.5 thick)
# impeller (axial sections + slices): cup hub, flange, 33 crescent blades
B_IMP_PROF = [(0.0, 1.30), (11.8, 1.30), (12.3, 1.80), (12.3, 9.29), (14.6, 9.29), (15.9, 10.84),
              (18.0, 10.84), (18.0, 11.80), (15.9, 11.80), (14.6, 10.59), (11.5, 10.59), (11.5, 2.14), (0.0, 2.14)]
B_BLADES = 33                     # blade count (slice w 24: 33 loops, pitch 10.909 deg)
B_BL_PHASE = -170.77              # inner end of one blade (deg)
B_BL_R = (14.62, 19.5)            # inner end (measured 14.5; 14.62 keeps it off the flat flange rings,
#                                   which saves ~700 triangles) / tip radius (tip circle fit 19.50)
B_BL_LEAD = -3.51                 # tip angle - inner end angle (tip leads: forward curved)
B_BL_SAG = (1.50, 0.59)           # crescent: convex / concave sagitta at mid-chord (0.9 thick)
B_BL_C = (2.59, 11.80)            # blade bottom (w 19.35) / top (w 28.56, = flange top)
B_SPIN = -1.0                     # turns towards -theta (clockwise seen from the motor side): the volute widens
#                                   that way and the blades are forward curved


def _circ_pt(c, r, deg):
    a = np.radians(deg)
    return (float(c[0] + r * np.cos(a)), float(c[1] + r * np.sin(a)))


def _volute(off, top=B_OUT):
    """Housing outline offset inwards by `off` (0 = exterior, B_WALL = cavity), extruded later."""
    (cb, RB), (ca, RA) = B_ARC_B, B_ARC_A
    RB, RA, rf = RB - off, RA - off, B_TFIL + off
    tx = B_TONGUE - off
    fc = (B_TONGUE + B_TFIL, float(np.sqrt((B_ARC_B[1] + B_TFIL) ** 2 - (B_TONGUE + B_TFIL - cb[0]) ** 2)))
    d = np.array(fc) - np.array(cb)
    d /= np.linalg.norm(d)
    T = (float(cb[0] + RB * d[0]), float(cb[1] + RB * d[1]))           # tangent point fillet / arc B
    angT = np.degrees(np.arctan2(T[1] - fc[1], T[0] - fc[0])) % 360
    fil_mid = _circ_pt(fc, rf, (180.0 + angT) / 2)
    return (cq.Workplane("XY").moveTo(B_LEFT + off, top).lineTo(tx, top).lineTo(tx, fc[1])
            .threePointArc(fil_mid, T).threePointArc(_circ_pt(cb, RB, 45.0), (cb[0] + RB, 0.0))
            .threePointArc(_circ_pt(ca, RA, -90.0), (ca[0] - RA, 0.0)).close())


def _blower_housing():
    H, P = B_H, B_PLATE
    ext = _volute(0.0).extrude(H)
    # mounting ears: ring + neck towards the axis, concave junctions filleted
    for ex, ey in B_EARS:
        L = float(np.hypot(ex, ey))
        ang = float(np.degrees(np.arctan2(-ey, -ex)))
        neck = (cq.Workplane("XY").center(ex - 4.0 * ex / L, ey - 4.0 * ey / L).rect(8.0, 2 * B_EAR_R)
                .extrude(H).rotate((ex - 4.0 * ex / L, ey - 4.0 * ey / L, 0), (ex - 4.0 * ex / L, ey - 4.0 * ey / L, 1), ang))
        ext = ext.union(_cyl(B_EAR_R, 0, H, ex, ey)).union(neck)
    sol = ext.val()
    sel = []
    for e in sol.Edges():
        if e.geomType() != "LINE":
            continue
        v = e.endPoint() - e.startPoint()
        if abs(v.z) < 0.99 * v.Length:
            continue
        cpt = e.Center()
        for ex, ey in B_EARS:
            dd = float(np.hypot(cpt.x - ex, cpt.y - ey))
            if B_EAR_R + 0.05 < dd < 8.0:
                sel.append(e)
    try:
        sol = sol.fillet(1.2, sel)
    except Exception as exc:  # keep the sharp junctions rather than fail
        print("print-fan: ear fillet skipped:", exc)
    w = cq.Workplane("XY").add(sol)
    # cavity (open through the outlet), inlet, ear holes
    w = w.cut(_volute(B_WALL, top=B_OUT + 3.0).extrude(H - 2 * P).translate((0, 0, P)))
    w = w.cut(_cyl(B_INLET, -1, P + 1))
    for ex, ey in B_EARS:
        w = w.cut(_cyl(B_EAR_HOLE, -1, H + 1, ex, ey))
    # motor-side plate: recessed cells between radial ribs, centre hole, cable slot + notch, clip, wire tab
    (ccx, ccy), Ro, Ri, dep = B_CELLS
    cells = _cyl(Ro, H - dep, H + 1, ccx, ccy).cut(_cyl(Ri, H - 2, H + 2))
    for ang in (45.0, 90.0, 135.0):
        cells = cells.cut(_box(-30, 30, -B_SPOKE / 2, B_SPOKE / 2, H - 2, H + 2).rotate((0, 0, 0), (0, 0, 1), ang))
    cells = cells.cut(_box(-30, 0, -B_SPOKE / 2, B_SPOKE / 2, H - 2, H + 2)).cut(_box(0, 30, -B_SLOT_RIB, B_SLOT_RIB, H - 2, H + 2))
    w = w.cut(cells).cut(_cyl(B_CHOLE, H - P - 1, H + 1))
    w = w.cut(cq.Workplane("XY").workplane(offset=B_NOTCH_C).polyline(B_SLOT).close().extrude(H + 1 - B_NOTCH_C))
    w = w.union(_box(*B_CLIP)).union(_box(*B_TAB))
    return w


def _blower_impeller():
    pr = B_IMP_PROF
    s = cq.Workplane("XZ").moveTo(*pr[0])        # (the cup's r 0.5 bottom corner is a 0.5 chamfer here)
    for p in pr[1:]:
        s = s.lineTo(*p)
    imp = s.close().revolve(360, (0, 0, 0), (0, 1, 0))
    r0, r1 = B_BL_R
    for k in range(B_BLADES):
        th = B_BL_PHASE + 360.0 * k / B_BLADES
        A = np.array(_pol(r0, th))
        B = np.array(_pol(r1, th + B_BL_LEAD))
        L = np.linalg.norm(B - A)
        t = (B - A) / L
        n = np.array([-t[1], t[0]])                        # towards +theta
        m_out = A + t * L / 2 + n * B_BL_SAG[0]
        m_in = A + t * L / 2 + n * B_BL_SAG[1]
        bl = (_wp(B_BL_C[0]).moveTo(*A).threePointArc(tuple(m_out), tuple(B)).threePointArc(tuple(m_in), tuple(A))
              .close().extrude(B_BL_C[1] - B_BL_C[0]))
        imp = imp.union(bl)
    return imp


# ============================================================================ build
M_HOT = np.array([[0.0, 0, 1], [1, 0, 0], [0, 1, 0]])      # local (a, b, axial) -> master (axial, a, b)
T_HOT = np.array([0.0, NF_CY, NF_CZ])


def _anchor_blower():
    return (B_M @ np.array([B_U0, B_V0, B_W0 + B_H / 2])).round(4)


ANCHORS = {
    "hotend_fan_axis": {"center": [round((NF_X0 + NF_X1) / 2, 4), NF_CY, NF_CZ], "dir": [1.0, 0.0, 0.0], "spin": NR_SPIN},
    "print_fan_axis": {"center": [float(v) for v in _anchor_blower()],
                       "dir": [float(v) for v in B_M[:, 2].round(6)], "spin": B_SPIN},
}


def _mesh(shape, lin=0.05, ang=0.5):
    """build_mk4.mesh_of with a selectable angular deflection (0.5 rad keeps the small parts at ~2-4k triangles)."""
    if isinstance(shape, cq.Workplane):
        shape = cq.Compound.makeCompound([v for v in shape.vals() if isinstance(v, cq.Shape)])
    P, N, I = tessellate(shape.wrapped, lin, ang)
    return P.astype(np.float32), N.astype(np.float32), I


def build():
    out = []
    out.append(("hotend-fan", "carriage", "noctua-beige", place(_mesh(_noctua_frame()), M_HOT, T_HOT)))
    out.append(("hotend-fan-rotor", "carriage", "noctua-brown", place(_mesh(_noctua_rotor()), M_HOT, T_HOT)))
    out.append(("print-fan", "carriage", "fan", place(_mesh(_blower_housing()), B_M, B_T)))
    out.append(("print-fan-impeller", "carriage", "fan", place(_mesh(_blower_impeller()), B_M, B_T)))
    return out


# ============================================================================ self-check
