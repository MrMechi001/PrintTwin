"""Original Prusa i3 MK3S+ hotend (E3D V6) + SuperPINDA probe, group "carriage", for build_mk3s.py.

Every shape is built here from CadQuery primitives. Kayne's MK3S assembly was used only as a
measuring reference (positions below); nothing of its geometry is read by build().

Master frame: mm, X right, Y up, Z towards the front.  Parts are at the reference pose
(x-carriage centre x ~68.5); build_mk3s.py shifts the group.

Sources
  [K]   Kayne's MK3S assembly, solids 122 'E3D V6' and 151 'PINDA Probe' (measured, see numbers).
  [E1]  E3D "V6 1.75 mm sink dimensions" drawing (e3d-online.com, E3D Classics support page):
        nozzle tip 0, heatsink bottom 19.95, groove-mount bottom 49.85, heatsink top 62.55;
        groove mount 3.00 / 6.00 / 3.70 high, radii 8.00 (collars) / 6.00 (groove).
  [E2]  E3D "heatbreak and nozzle position check" drawing (same page), scaled by the 11.5 mm block
        height: block 23.0 long, heater hole d6 8.58 from the slit end, nozzle 15.80, thermistor
        hole d3 21.05; heater centre 4.0 and thermistor centre 5.73 above the block bottom; nozzle
        hex 7 AF x 3.0, cone 2.0 to a d1.0 tip flat, 1.45 gap hex-block; heatbreak neck d3.0 x 2.0.
  [E3]  E3D V6 heatsink diameter 22.3 mm (E3D fan-duct spec "fits a 22.3 mm cylinder").
  [P1]  Prusa "How to replace a hotend thermistor (MK3S/MK3S+)": thermistor held by a grub screw
        in the BOTTOM of the heater block = E3D's sensor-cartridge block (23 x 16 x 11.5); photos:
        16 mm face to the front, thermistor at the front end, heater cartridge behind the nozzle,
        both wired from the left, slit-clamp screw from below at the back end.
  [P2]  Prusa "How to replace SuperPINDA (MK3S/MK3S+)": SuperPINDA = fully threaded M8 nickel-plated
        body, dark sensing cap, cable straight out of the top, no nuts (clamped by the holder's M3
        screw); height set with a zip tie under the probe while the nozzle touches the bed; Prusa
        support (forum): about 1.5 mm between probe and nozzle tip.
        Body material brass, sensing face LCP (Zaribo listing); 35 mm long (Duet3D forum).

HOT SIDE  (build(hot_side=...), anchors(hot_side=...); the module default is HOT_SIDE)
  "reference" (default, as tasked): nozzle tip exactly at Kayne's tip, 297.90182.  Kayne's hotend
      is 3.95 mm shorter than E3D's drawing (tip -> heatsink top 58.60 vs 62.55) while his heatsink
      sits exactly in the extruder body's groove clamp; so everything from the nozzle hex up is built
      true to E3D at the clamp, and only the nozzle is shortened (2.5 mm below the block instead of
      6.45: cone 1.0, hex 1.3, gap 0.2).  NOT TRUE TO LIFE: the fan shroud (Kayne's, and Prusa's
      fan-shroud.stp at build_mk3s REF_POSES) reaches down to y 296.98, 0.92 mm BELOW this tip, so
      at Z 0 it would dip into the sheet.
  "e3d" (recommended): the true E3D V6 chain from the clamp: tip 293.95183, 3.95 mm lower, and the
      SuperPINDA with it (still 1.5 mm above the tip, and still through its holder bore, y 310-322).
      The shroud then ends 3.03 mm above the tip, as reported for working MK3S printers.
  Choosing "e3d" changes the bed/Z mapping, so the main build must take its nozzle reference from
  this module instead of the hard-coded REF_NOZZLE (68.95, 297.9, 278.7):
        parts += hotend.build(hot_side="e3d")
        NOZ = np.array(hotend.anchors("e3d")["nozzle_tip"])     # replaces REF_NOZZLE.copy()
  (The x/z of the anchor are the measured tip centre, 0.022 / 0.0005 mm off REF_NOZZLE.)
  Both values of the switch are applied in ONE place: build() and anchors() compute the tip, the
  nozzle shape, the probe height and the anchors from the same argument, read at call time (so
  setting hotend.HOT_SIDE after import also works), and build() refreshes the module's ANCHORS dict
  in place, so ANCHORS always describes the geometry the last build() returned.

SILICONE SOCK
  A stock MK3S+ ships WITHOUT one (Prusa kits never included it; forum threads "no sock for the hot
  end"); it is the separate optional node 'heater-sock' (OPTIONAL_PARTS).  build() returns it by
  default (INCLUDE_SOCK, as tasked) because the viewer's temperature glow lives on the 'sock'
  material; build(include_sock=False) gives the strictly stock hotend (and no glow).
  Overlaps: the sock's front wall touches Kayne's shroud (0.42 mm from the block's front-bottom
  edge); Prusa's shroud at REF_POSES clears it by at least 1 mm.
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

import numpy as np

sys.path.insert(0, _CAD)
import cadquery as cq  # noqa: E402
from build_mk4 import mesh_of, place  # noqa: E402
from glb import tessellate  # noqa: E402

HOT_SIDE = "reference"          # module default for build()/anchors(); or "e3d" (see the docstring)
INCLUDE_SOCK = True             # module default for build(): deliver the optional 'heater-sock' node
HOT_SIDES = ("reference", "e3d")
OPTIONAL_PARTS = ("heater-sock",)   # nodes a stock MK3S+ does not have

NEW_MATS = {
    "nickel": ((0.80, 0.79, 0.76), 1.0, 0.30),      # SuperPINDA nickel-plated brass body
    "lcp":    ((0.22, 0.23, 0.25), 0.0, 0.55),      # SuperPINDA dark-grey LCP sensing cap
}

# ----------------------------------------------------------------------------- measured positions
NX, NZ = 68.97183, 278.69946    # [K] nozzle axis = centre of the 86-vertex tip ring of solid 122
#                                       (= bbox centre of its heatsink and of its groove mount)
TIP_REF = 297.90182             # [K] lowest vertex of solid 122 (tip flat, ring r 0.66)
HS_TOP = 356.50183              # [K] top face of the V6 groove mount; the extruder body's groove
#                                       clamp (r 6.08 ring at y 346.7..352.7) fits it
PROBE_DX, PROBE_DZ = 23.0, -5.0 # [K] probe axis (91.97183, 273.69946) - nozzle axis; = firmware
#                                       probe offset X+23 / Y+5 (towards the back = -Z)
PROBE_LIFT = 1.5                # [K] probe bottom 299.40182 - tip 297.90182 (Prusa support: ~1.5)
PROBE_LEN = 35.75               # [K] 299.40182 .. 335.15182 (forum: 35 mm, same as PINDA v2)

# ----------------------------------------------------------------------------- E3D V6 numbers
HS_LEN = 42.60                  # [E1] 62.55 - 19.95
HS_BOT = HS_TOP - HS_LEN        # 313.90183  ([K] 314.20)
FIN_D, FIN_N, FIN_PITCH, FIN_T = 22.3, 11, 2.5, 1.0   # [E3] dia; [K] 11 fins, pitch 2.5 (0.9-1.0 thick)
CORE_D = 11.0                   # [K] core between the fins (side faces r 5.50)
NECK_D = 10.0                   # [K] neck above the fins (r 5.00)
DISC_D, DISC_T = 16.0, 0.9      # [K] thin disc 1.5 above the top fin, 0.9 thick (d15.8)
GM = (3.0, 6.0, 3.7)            # [E1] lower collar, groove, top collar
COLLAR_D, GROOVE_D = 16.0, 12.0 # [E1]
BREAK_D, BREAK_GAP = 3.0, 2.0   # [E2] exposed heatbreak neck
BLK_W, BLK_L, BLK_H = 16.0, 23.0, 11.5         # [E2]/[P1] x, depth, height
BLK_BACK = 15.8                 # [E2] nozzle axis -> back (slit) end; front end 7.2 ahead of it
HEATER_BACK, HEATER_UP = 7.22, 4.0             # [E2] heater hole d6: behind the axis, above bottom
THERM_FRONT, THERM_UP = 5.25, 5.73             # [E2] thermistor hole d3: ahead of the axis
NOZ_E3D = (2.0, 3.0, 1.45)      # [E2] cone, hex, gap hex->block  (5.0 + 1.45 = 6.45 below block)
HEX_AF, TIP_D, BORE_D = 7.0, 1.0, 0.4

BLK_TOP = HS_BOT - BREAK_GAP    # 311.90183
BLK_BOT = BLK_TOP - BLK_H       # 300.40183
TIP_E3D = HS_BOT - 19.95        # 293.95183  [E1]


# ----------------------------------------------------------------------------- the hot-side switch
def _side(hot_side=None):
    """resolve a hot side at call time (None -> the module's HOT_SIDE) and validate it."""
    hs = HOT_SIDE if hot_side is None else hot_side
    if hs not in HOT_SIDES:
        raise ValueError(f"hot_side must be one of {HOT_SIDES}, not {hs!r}")
    return hs


def tip_height(hot_side=None):
    """master y of the nozzle tip."""
    return TIP_REF if _side(hot_side) == "reference" else TIP_E3D


def nozzle_dims(hot_side=None):
    """(cone, hex, gap hex->block) heights; they always add up to block bottom - tip."""
    if _side(hot_side) == "reference":
        return 1.0, 1.3, round(BLK_BOT - TIP_REF - 2.3, 5)     # 2.5 mm available below the block
    return NOZ_E3D


def anchors(hot_side=None):
    """{"nozzle_tip", "probe_tip"} in the master frame for a hot side (plain floats)."""
    t = tip_height(hot_side)
    return {"nozzle_tip": [NX, round(t, 5), NZ],
            "probe_tip": [round(NX + PROBE_DX, 5), round(t + PROBE_LIFT, 5), round(NZ + PROBE_DZ, 5)]}


ANCHORS = anchors()             # = anchors(HOT_SIDE) at import; every build() refreshes it in place

# local frame: x = master x, y = towards the BACK (master -z), z = master y (heights are absolute)
M_LOC = np.column_stack([(1.0, 0, 0), (0, 0, -1.0), (0, 1.0, 0)])


def _cyl(d, z0, z1, x=0.0, y=0.0):
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(d / 2).extrude(z1 - z0)


def _xcyl(d, x0, x1, y, z):
    """cylinder along local x from x0 to x1, axis at (y, z)."""
    return cq.Workplane("YZ").workplane(offset=x0).center(y, z).circle(d / 2).extrude(x1 - x0)


def nozzle(hot_side=None):
    tip = tip_height(hot_side)
    cone, hexh, gap = nozzle_dims(hot_side)
    assert abs(tip + cone + hexh + gap - BLK_BOT) < 1e-4, "nozzle chain does not end at the block"
    z_hex = tip + cone
    body = (cq.Workplane("XY").workplane(offset=tip).circle(TIP_D / 2)
            .workplane(offset=cone).circle(1.9).loft())                        # tip cone to d3.8
    hexa = (cq.Workplane("XY").workplane(offset=z_hex).polygon(6, HEX_AF / np.cos(np.pi / 6))
            .extrude(hexh).edges("not |Z").chamfer(0.3 if hexh > 2 else 0.2))
    hexa = hexa.rotate((0, 0, 0), (0, 0, 1), 90)                               # a corner to the front
    shank = _cyl(5.0, z_hex + hexh - 0.01, BLK_BOT + 0.4)                       # M6 thread relief
    n = body.union(hexa).union(shank)
    return n.cut(_cyl(BORE_D, tip - 0.1, tip + 0.6))


def heater_block():
    yb, yf = BLK_BACK, BLK_BACK - BLK_L                       # +15.8 (back) .. -7.2 (front)
    blk = (cq.Workplane("XY").box(BLK_W, BLK_L, BLK_H, centered=False)
           .translate((-BLK_W / 2, yf, BLK_BOT)).edges().chamfer(0.3))
    blk = blk.cut(_xcyl(6.0, -9, 9, HEATER_BACK, BLK_BOT + HEATER_UP))         # heater bore
    blk = blk.cut(_xcyl(3.0, -9, 9, -THERM_FRONT, BLK_BOT + THERM_UP))         # thermistor bore
    slit = (cq.Workplane("XY").box(BLK_W + 2, yb - HEATER_BACK + 1, 1.0, centered=(True, False, True))
            .translate((0, HEATER_BACK, BLK_BOT + HEATER_UP)))
    blk = blk.cut(slit)
    blk = blk.cut(_cyl(3.2, BLK_BOT - 1, BLK_BOT + 1.3, 0, -THERM_FRONT))      # grub-screw port
    return blk


def block_screws():
    """M3 button-head slit clamp (ISO 7380: dk 5.7, k 1.65) at the back end + thermistor grub screw."""
    yc = BLK_BACK - 2.75
    head = (_cyl(5.7, BLK_BOT - 1.65, BLK_BOT, 0, yc).faces("<Z").edges().chamfer(0.7)
            .cut(cq.Workplane("XY").workplane(offset=BLK_BOT - 1.7).center(0, yc).polygon(6, 2.3).extrude(1.0)))
    grub = (_cyl(3.0, BLK_BOT + 0.35, BLK_BOT + 1.25, 0, -THERM_FRONT)
            .cut(cq.Workplane("XY").workplane(offset=BLK_BOT + 0.3).center(0, -THERM_FRONT)
                 .polygon(6, 1.73).extrude(0.6)))
    return head.union(grub)


def heater_cartridge():
    return _xcyl(6.0, -10.0, 10.0, HEATER_BACK, BLK_BOT + HEATER_UP).edges().chamfer(0.3)


def thermistor_cartridge():
    return _xcyl(3.0, -10.5, 9.5, -THERM_FRONT, BLK_BOT + THERM_UP).edges().chamfer(0.2)


def heatbreak():
    return _cyl(BREAK_D, BLK_TOP - 0.5, HS_BOT + 0.5)


def heatsink():
    z = HS_BOT
    w = _cyl(CORE_D, z, z + (FIN_N - 1) * FIN_PITCH + FIN_T)
    for k in range(FIN_N):
        w = w.union(_cyl(FIN_D, z + k * FIN_PITCH, z + k * FIN_PITCH + FIN_T))
    z_fin_top = z + (FIN_N - 1) * FIN_PITCH + FIN_T                    # 339.90
    z_gm = HS_TOP - sum(GM)                                            # 343.80 lower-collar bottom
    w = w.union(_cyl(NECK_D, z_fin_top - 0.01, z_gm + 0.01))
    w = w.union(_cyl(DISC_D, z_fin_top + 1.5, z_fin_top + 1.5 + DISC_T))
    lo = _cyl(COLLAR_D, z_gm, z_gm + GM[0]).edges().chamfer(0.3)
    gr = _cyl(GROOVE_D, z_gm + GM[0] - 0.01, z_gm + GM[0] + GM[1] + 0.01)
    hi = _cyl(COLLAR_D, z_gm + GM[0] + GM[1], HS_TOP).edges().chamfer(0.3)
    return w.union(lo).union(gr).union(hi)


def sock():
    """E3D V6 silicone sock (optional, NOT on a stock MK3S+): 1.5 walls, 1.6 bottom, 1.2 top."""
    t, tb, tt = 1.5, 1.6, 1.2
    yb, yf = BLK_BACK + t, BLK_BACK - BLK_L - t
    outer = (cq.Workplane("XY").box(BLK_W + 2 * t, yb - yf, BLK_H + tb + tt, centered=False)
             .translate((-BLK_W / 2 - t, yf, BLK_BOT - tb)).edges("|Z").fillet(3.0)
             .faces(">Z or <Z").edges().fillet(1.2))
    inner = (cq.Workplane("XY").box(BLK_W, BLK_L, BLK_H, centered=False)
             .translate((-BLK_W / 2, BLK_BACK - BLK_L, BLK_BOT)))
    s = outer.cut(inner)
    s = s.cut(_cyl(9.0, BLK_BOT - 3, BLK_BOT + 0.5))                         # nozzle opening
    s = s.cut(_cyl(6.2, BLK_BOT - 3, BLK_BOT + 0.5, 0, BLK_BACK - 2.75))     # clamp-screw head
    s = s.cut(_cyl(7.0, BLK_TOP - 0.5, BLK_TOP + 3))                         # heatbreak opening
    s = s.cut(_xcyl(6.4, -12, 12, HEATER_BACK, BLK_BOT + HEATER_UP))         # cartridge ports
    s = s.cut(_xcyl(3.4, -12, 12, -THERM_FRONT, BLK_BOT + THERM_UP))
    return s


def superpinda(hot_side=None):
    """-> (cap, threaded body, cable stub) in the probe's local frame (axis = z, absolute heights)."""
    z0 = tip_height(hot_side) + PROBE_LIFT
    z1 = z0 + PROBE_LEN
    cap_h = 3.8                                                        # [P2] photo: ~0.45 x dia
    cap = _cyl(7.0, z0, z0 + cap_h).faces("<Z").edges().chamfer(0.4)
    r_maj, r_min, p = 4.0, 3.46, 1.0                                   # M8 x 1
    zs = z0 + cap_h
    n = int(np.floor((z1 - zs - 0.3) / p))
    pts = [(0, zs), (r_min, zs)]
    for k in range(n):
        pts += [(r_maj, zs + k * p + p / 2), (r_min, zs + (k + 1) * p)]
    pts += [(r_min, z1), (0, z1)]
    body = cq.Workplane("XZ").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0))
    cable = _cyl(3.5, z1 - 0.2, z1 + 5.0)
    return cap, body, cable


def build(hot_side=None, include_sock=None):
    """-> [(name, "carriage", material, (P, N, I))] in the master frame.

    hot_side: "reference" | "e3d" | None (= the module's HOT_SIDE, read now); include_sock: bool |
    None (= INCLUDE_SOCK).  Refreshes the module's ANCHORS in place to anchors(hot_side)."""
    hs = _side(hot_side)
    with_sock = INCLUDE_SOCK if include_sock is None else bool(include_sock)
    out = []
    t_hot = np.array([NX, 0.0, NZ])
    parts = [
        ("nozzle", "brass", nozzle(hs), 0.01),
        ("heater-block", "alu", heater_block(), 0.02),
        ("heater-cartridge", "steel", heater_cartridge(), 0.02),
        ("thermistor-cartridge", "steel", thermistor_cartridge(), 0.02),
        ("heater-block-screws", "anodised", block_screws(), 0.02),
    ]
    if with_sock:
        parts.append(("heater-sock", "sock", sock(), 0.03))
    parts.append(("heatbreak", "steel", heatbreak(), 0.01))
    for name, mat, shape, defl in parts:
        out.append((name, "carriage", mat, place(mesh_of(shape, defl), M_LOC, t_hot)))
    # the heatsink sits inside the extruder body: 23-gon fins are plenty
    out.append(("heatsink", "carriage", "alu",
                place(tessellate(heatsink().val().wrapped, 0.1, 0.5), M_LOC, t_hot)))
    cap, body, cable = superpinda(hs)
    t_pr = np.array([NX + PROBE_DX, 0.0, NZ + PROBE_DZ])
    body_mesh = tessellate(body.val().wrapped, 0.06, 0.6)                # coarse around, exact rings
    out.append(("superpinda", "carriage", "nickel", place(body_mesh, M_LOC, t_pr)))
    out.append(("superpinda-face", "carriage", "lcp", place(mesh_of(cap, 0.02), M_LOC, t_pr)))
    out.append(("superpinda-cable", "carriage", "rubber", place(mesh_of(cable, 0.02), M_LOC, t_pr)))
    ANCHORS.clear()
    ANCHORS.update(anchors(hs))
    return out


# ----------------------------------------------------------------------------- self-check
