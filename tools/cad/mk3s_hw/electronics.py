"""MK3S+ electronics for build_mk3s.py: LCD controller board + 20x4 character LCD, power supply, Einsy RAMBo.

All shapes are CadQuery primitives built here; nothing of the reference assembly is used.  The numbers
marked "measured" were measured on Kayne's MK3S assembly (measuring reference only, already in the
master frame): plane levels, edges and hole centres of the LCD board solid, the window/slot pattern of
the LCD cover, the Einsy base.  Others come from datasheets / Prusa's SCAD sources (cited per value).

Master frame: mm, X right, Y up, Z towards the front.  Everything here is group "static".

LCD frame (u, v, w): the LCD board is tilted exactly 45 deg (measured: plane normal (0, 0.7071, 0.7071)).
  u = master +X (user's right), v = (0, s, -s) = up the slope (user's "up" on the screen),
  w = (0, s, s) = out of the glass towards the user (s = sqrt 1/2).  master = u*U + v*V + w*W.
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

import numpy as np

sys.path.insert(0, _CAD)
import cadquery as cq  # noqa: E402
from build_mk4 import mesh_of, place  # noqa: E402

NEW_MATS: dict = {}

S2 = float(np.sqrt(0.5))
U_AX = np.array([1.0, 0.0, 0.0])
V_AX = np.array([0.0, S2, -S2])
W_AX = np.array([0.0, S2, S2])
M_LCD = np.column_stack([U_AX, V_AX, W_AX])          # local (u, v, w) -> master
ZERO = np.zeros(3)

# ----------------------------------------------------------------------------- LCD (measured, local u v w)
# controller PCB: edges u 76.81..226.81, v -344.32..-289.32, faces w 373.73 / 375.43 (150 x 55 x 1.7);
# 4 holes d 3.0 (u 79.56 / 224.06, v -341.57 / -292.07)
PCB_U, PCB_V, PCB_W = (76.81, 226.81), (-344.32, -289.32), (373.73, 375.43)
PCB_HOLES = [(79.56, -341.57), (224.06, -341.57), (79.56, -292.07), (224.06, -292.07)]
# 2004A LCD module PCB 98 x 60 x 1.6 (datasheet; measured u 90.11..188.11, v -352.32..-292.32, w 378.03..379.63),
# holes d 3.0 on 93 x 55 (datasheet; measured centres u 92.61 / 185.61, v -349.82 / -294.82)
MOD_U, MOD_V, MOD_W = (90.11, 188.11), (-352.32, -292.32), (378.03, 379.63)
MOD_HOLES = [(92.61, -349.82), (185.61, -349.82), (92.61, -294.82), (185.61, -294.82)]
# 16-pin header body between the boards (measured u 98.81..139.21, v -295.57..-294.07)
HDR16 = ((98.81, 139.21), (-295.57, -294.07), (375.43, 378.03))
# metal bezel (2004A datasheet 97.0 x 40.0; measured u 90.51..187.71, v -342.02..-302.32, corner r 1.2,
# top w 387.63 with a ~0.4 round-over)
BEZ_U, BEZ_V, BEZ_W, BEZ_R = (90.51, 187.71), (-342.02, -302.32), (379.63, 387.63), 1.2
# visible glass = bezel window (measured u 101.16..177.06, v -335.12..-309.22, corner r ~0.86, glass w 386.83)
# (datasheet viewing area 76.0 x 25.2; measured 75.9 x 25.9)
GLASS_U, GLASS_V, GLASS_TOP, GLASS_R = (101.16, 177.06), (-335.12, -309.22), 386.83, 0.86
# character (active) area of a 2004A: 20 x 4 chars of 2.95 x 4.75 at pitch 3.55 x 5.35 -> 70.4 x 20.8
# (datasheets: Sitronix-based 2004A, RC2004A, BC2004A all give AA 70.4 x 20.8), centred in the viewing area
CHAR_W, CHAR_H = 70.4, 20.8
# buzzer d 11.96 x 9.0 (measured centre u 213.005 v -299.815, sides to w 384.08, top w 384.43)
BUZ_C, BUZ_R, BUZ_W = (213.005, -299.815), 5.98, (375.43, 384.43)
# rotary encoder (measured): body u 207.41..220.81, v -324.57..-311.57, w 375.43..382.13; bushing d 11.55 with
# flats 10.0 apart, w 382.13..384.03; shaft d 6.0 to w 394.63; axis (u 214.11, v -318.07) -> the knob's shaft
ENC_C = (214.11, -318.07)
# SD socket on the back, card entry from the left through the cover's side opening (measured: shell
# u 76.81..103.41, v -336.22..-309.82, w 370.63..373.73; card pocket u ..96.81, v -335.02..-311.02, floor w 371.13)
# two right-angle 2x5 box headers EXP1/EXP2 on the back, openings towards -v (measured: bodies u 122.66..142.95 /
# 145.66..165.95, v -327.55..-318.66, w 364.97..373.35; cavities u +1.14..-1.14, v ..-321.20, w 365.91..372.41;
# key notch u 130.77..134.84 / 153.77..157.84, v ..-320.64, w ..367.00)


def blk(x, y, z):
    """Axis-aligned box from ranges."""
    return (cq.Workplane("XY").box(x[1] - x[0], y[1] - y[0], z[1] - z[0], centered=False)
            .translate((x[0], y[0], z[0])))


def cyl(cx, cy, z, r):
    return cq.Workplane("XY").workplane(offset=z[0]).center(cx, cy).circle(r).extrude(z[1] - z[0])


def rrect(x, y, z, r):
    """Box with rounded vertical edges."""
    return (cq.Workplane("XY").workplane(offset=z[0]).center((x[0] + x[1]) / 2, (y[0] + y[1]) / 2)
            .rect(x[1] - x[0], y[1] - y[0]).extrude(z[1] - z[0]).edges("|Z").fillet(r))


def plate_with_holes(x, y, z, holes, d):
    s = blk(x, y, z)
    for hx, hy in holes:
        s = s.cut(cyl(hx, hy, (z[0] - 1, z[1] + 1), d / 2))
    return s


def lcd_parts():
    out = []
    out.append(("lcd-board", "pcb", plate_with_holes(PCB_U, PCB_V, PCB_W, PCB_HOLES, 3.0)))
    out.append(("lcd-module-pcb", "pcb", plate_with_holes(MOD_U, MOD_V, MOD_W, MOD_HOLES, 3.0)))
    out.append(("lcd-module-header", "petg", blk(*HDR16)))

    bez = rrect(BEZ_U, BEZ_V, BEZ_W, BEZ_R).faces(">Z").edges().fillet(0.4)
    bez = bez.cut(rrect(GLASS_U, GLASS_V, (385.0, 388.5), GLASS_R))          # window down into the glass
    out.append(("lcd-bezel", "steel", bez))
    # glass 0.2 mm larger than the window so its edges hide inside the bezel (no coplanar faces)
    out.append(("lcd-glass", "screen", blk((GLASS_U[0] - 0.2, GLASS_U[1] + 0.2), (GLASS_V[0] - 0.2, GLASS_V[1] + 0.2),
                                          (385.4, GLASS_TOP))))

    buz = cyl(BUZ_C[0], BUZ_C[1], BUZ_W, BUZ_R).faces(">Z").edges().fillet(0.35)
    buz = buz.cut(cyl(BUZ_C[0], BUZ_C[1], (BUZ_W[1] - 1.5, BUZ_W[1] + 1), 0.95))   # sound port
    out.append(("lcd-buzzer", "petg", buz))

    cu, cv = ENC_C
    enc = blk((207.41, 220.81), (-324.57, -311.57), (375.43, 382.13))
    bush = cyl(cu, cv, (382.13, 384.03), 5.775).intersect(blk((cu - 5.0, cu + 5.0), (cv - 6, cv + 6), (382.0, 384.2)))
    enc = enc.union(bush).union(cyl(cu, cv, (384.03, 394.63), 3.0))
    out.append(("lcd-encoder", "steel", enc))

    sd = blk((76.81, 103.41), (-336.22, -309.82), (370.63, 373.73))
    sd = sd.cut(blk((76.0, 96.81), (-335.02, -311.02), (371.13, 374.0)))
    out.append(("lcd-sd-slot", "steel", sd))

    hdr = None
    for u0, key in ((122.66, 130.77), (145.66, 153.77)):
        b = blk((u0, u0 + 20.29), (-327.55, -318.66), (364.97, 373.35))
        b = b.cut(blk((u0 + 1.14, u0 + 19.15), (-328.0, -321.20), (365.91, 372.41)))
        b = b.cut(blk((key, key + 4.07), (-328.0, -320.64), (364.5, 367.0)))
        hdr = b if hdr is None else hdr.union(b)
    out.append(("lcd-box-headers", "petg", hdr))
    return out


# ----------------------------------------------------------------------------- PSU
# Stock MK3S+ (2021-2023) = the black Delta PSU (24 V 240 W, Prusa-specific, mains inlet + red rocker switch
# integrated in its rear end face); the old silver PSU with PSU-cover-MK3 + separate inlet block is what the
# reference shows.  The MK4 kept this black PSU and its mount, so the body is the MK4 assembly's (FokieH) box:
# 48 x 250 x 105 with its left face on the rear-right Y extrusion (x 289.26) and its right face flush with the
# frame's right edge (x 337.25); MK4 frame ribs end at z 223.75, the MK3S+ flat frame's rear face is z 229.5
# (Prusa: PSU "in direct contact with the vertical frame") -> shifted +5.75 in z.
# The 4-screw output terminal strip + power-panic board sit at the BOTTOM end of the vented outer (+X) face, next
# to the extrusion; the printed psu-cover-DELTA (PRUSA logo outwards, 100.6 long along z) is screwed over them
# (Prusa guide "How to replace PSU on MK3 printers", g20152, steps 5/6/12 photos + text; the printer lies on its
# left side there, so "logo facing up" = +X).  Its suggested pose is in ANCHORS (psu_cover_delta_*).
PSU_X, PSU_Y, PSU_Z = (289.26, 337.26), (29.88, 279.88), (124.5, 229.5)
# frame holes for the PSU's M4 screws (frame drawing MK3v8b.dxf, d 4.2): (300.65, 110.4) and (300.65, 227.4)
# IEC C14 inlet + red rocker switch on the PSU's rear face (-Z), close to the outer (+X) edge, the inlet just above
# the terminal end and the switch above the inlet (g20152 steps 5/6 photos, seen over the PSU's rear corner;
# estimated from photos, +-5 mm).  Flange / bezel right edges 2 mm in from the +X edge.
INLET_C, SWITCH_C = (319.76, 69.0), (327.76, 93.5)
M_REAR = np.column_stack([[-1.0, 0, 0], [0, 1.0, 0], [0, 0, -1.0]])   # local x right (seen from behind), y up, z out


def psu_parts():
    out = [("psu", "psu", blk(PSU_X, PSU_Y, PSU_Z), np.eye(3), ZERO)]
    # C14 snap-in inlet: flange 31 x 23.5, 3 proud; receptacle 24.5 x 16.5 x 2.5 deep, top corners chamfered 4
    fl = cq.Workplane("XY").rect(31.0, 23.5).extrude(3.0).edges("|Z").fillet(1.0)
    rec = (cq.Workplane("XY").workplane(offset=0.5)
           .polyline([(-12.25, -8.25), (12.25, -8.25), (12.25, 4.25), (8.25, 8.25), (-8.25, 8.25), (-12.25, 4.25)])
           .close().extrude(3.0))
    inlet = fl.cut(rec)
    t_in = np.array([INLET_C[0], INLET_C[1], PSU_Z[0]])
    # rocker switch: bezel 15 x 21 x 3 proud with an opening 12.6 x 18.6, rocker tilted 8 deg
    sw = cq.Workplane("XY").rect(15.0, 21.0).extrude(3.0).edges("|Z").fillet(0.8)
    sw = sw.cut(cq.Workplane("XY").workplane(offset=1.0).rect(12.6, 18.6).extrude(3.0))
    t_sw = np.array([-(SWITCH_C[0] - INLET_C[0]), SWITCH_C[1] - INLET_C[1], 0.0])    # local x = -master x
    rocker = (cq.Workplane("XY").box(12.2, 18.2, 3.0).rotate((0, 0, 0), (1, 0, 0), 8.0)
              .translate((0, 0, 2.2)).intersect(cq.Workplane("XY").workplane(offset=1.0).rect(12.6, 18.6).extrude(4.5)))
    housing = inlet.union(sw.translate(tuple(t_sw)))
    out.append(("psu-inlet", "petg", housing, M_REAR, t_in))
    out.append(("psu-switch", "red", rocker.translate(tuple(t_sw)), M_REAR, t_in))     # red rocker (g20152 photos)
    # C14 pins: earth (centre, upper) and L / N at +-7 mm, 2 x 4 blades, 1.5 long inside the receptacle
    pins = None
    for px, py in ((0.0, 3.0), (-7.0, -2.0), (7.0, -2.0)):
        p = blk((px - 1.0, px + 1.0), (py - 2.0, py + 2.0), (0.5, 2.0))
        pins = p if pins is None else pins.union(p)
    out.append(("psu-inlet-pins", "steel", pins, M_REAR, t_in))
    return out


# ----------------------------------------------------------------------------- Einsy RAMBo
# Visible with the doors closed: Einsy-doors.scad cuts 30 through-slots 3.65 x 19 (10 columns x 3 rows) in the
# 1.5 mm door plate, which look straight onto the board ~40 mm inside.
# Einsy-base SCAD frame -> master.  The Printables MK3S+ Einsy-base.stl uses the SCAD frame (measured: its 4 hex
# board-standoff tops are at (7.5|102.5, 14|75), z 8.0), and build_mk3s.py shows it with REF_POSES
# R = [[0,0,1],[-1,0,0],[0,-1,0]], t = (-32.91, 199.13, 229.5):  master = (zs - 32.91, 199.13 - xs, 229.5 - ys).
# The board uses the same pose so it sits on the base that is actually shown (the reference's older base is
# 1.55 mm higher, at t (-32.84, 200.68, 229.5)).
M_EINSY = np.column_stack([[0, -1.0, 0], [0, 0, -1.0], [1.0, 0, 0]])
T_EINSY = np.array([-32.91, 199.13, 229.5])
# board 105 x 71 x 1.6, holes 95 x 61 at 5 mm from the edges (UltiMachine DimensionDrawing, same footprint as the
# base's standoffs at SCAD (7.5|102.5, 14|75)); it rests on the standoffs at SCAD z 8.0
EB_X, EB_Y, EB_Z = (2.5, 107.5), (9.0, 80.0), (8.0, 9.6)
EB_HOLES = [(7.5, 14.0), (102.5, 14.0), (102.5, 75.0), (7.5, 75.0)]


def einsy_parts():
    out = [("einsy-board", "pcb", plate_with_holes(EB_X, EB_Y, EB_Z, EB_HOLES, 3.2))]
    # USB-B receptacle (12.0 x 16.0 x 10.9) behind the base's USB hole (SCAD y 41.5..54.5, z 11..22)
    usb = blk((1.6, 17.6), (42.0, 54.0), (9.6, 20.5))
    usb = usb.cut(blk((1.0, 10.0), (48.0 - 4.2, 48.0 + 4.2), (15.05 - 3.9, 15.05 + 3.9)))
    out.append(("einsy-usb", "steel", usb))
    # MCU (TQFP-100 body 14 x 14 x 1.2) near the board centre - generic placement
    out.append(("einsy-mcu", "fan", blk((45.0, 59.0), (37.5, 51.5), (9.6, 10.8))))
    # power input / heatbed screw terminal, 6 x 5.08 mm, wire entries towards the rear panel where the heatbed
    # cable comes in (SCAD (75, 90..101)) - generic placement
    term = blk((62.0, 92.5), (70.0, 80.0), (9.6, 22.0))
    for k in range(6):
        cx = 62.0 + 2.54 + 5.08 * k
        term = term.cut(cyl(cx, 74.0, (20.0, 23.0), 1.6))
        term = term.cut(blk((cx - 1.75, cx + 1.75), (76.0, 81.0), (12.0, 15.5)))
    out.append(("einsy-terminal", "petg", term))
    return out


# ----------------------------------------------------------------------------- build
COARSE = {"lcd-buzzer": 0.15, "lcd-encoder": 0.08, "einsy-terminal": 0.15, "einsy-board": 0.1}   # barely visible


def build():
    res = []
    for name, mat, shape in lcd_parts():
        res.append((name, "static", mat, place(mesh_of(shape, COARSE.get(name, 0.04)), M_LCD, ZERO)))
    for name, mat, shape, M, t in psu_parts():
        res.append((name, "static", mat, place(mesh_of(shape, 0.05), M, t)))
    for name, mat, shape in einsy_parts():
        res.append((name, "static", mat, place(mesh_of(shape, COARSE.get(name, 0.05)), M_EINSY, T_EINSY)))
    out = []
    for name, grp, mat, (P, N, I) in res:
        out.append((name, grp, mat, (P.astype(np.float32), N.astype(np.float32), I.astype(np.int64))))
    return out


# ----------------------------------------------------------------------------- anchors (master frame)
def _lcd_pt(u, v, w):
    return [round(float(x), 4) for x in u * U_AX + v * V_AX + w * W_AX]


def _anchors():
    w = GLASS_TOP + 0.6                                     # 0.6 mm in front of the glass, along +W
    cu, cv = (GLASS_U[0] + GLASS_U[1]) / 2, (GLASS_V[0] + GLASS_V[1]) / 2     # 139.11, -322.17
    a = {}
    u0, u1, v0, v1 = cu - CHAR_W / 2, cu + CHAR_W / 2, cv - CHAR_H / 2, cv + CHAR_H / 2
    a["lcd_screen"] = [_lcd_pt(u0, v0, w), _lcd_pt(u1, v0, w), _lcd_pt(u1, v1, w), _lcd_pt(u0, v1, w)]
    a["lcd_char_area_mm"] = [CHAR_W, CHAR_H]
    g0, g1, h0, h1 = GLASS_U[0], GLASS_U[1], GLASS_V[0], GLASS_V[1]
    a["lcd_glass_corners"] = [_lcd_pt(g0, h0, w), _lcd_pt(g1, h0, w), _lcd_pt(g1, h1, w), _lcd_pt(g0, h1, w)]
    a["lcd_glass_normal"] = [round(float(x), 6) for x in W_AX]
    # LCD knob: on the encoder shaft axis (measured on ref 160: u 214.11, v -318.07; ref knob 169's bore agrees to
    # 0.05 mm), back face at w 389.83 (ref knob 169's lowest point), axis = the glass normal
    a["lcd_knob_base"] = _lcd_pt(ENC_C[0], ENC_C[1], 389.83)
    a["lcd_knob_dir"] = [round(float(x), 6) for x in W_AX]
    # psu-cover-DELTA (Printables MK3S+ "Electronics/psu-cover-DELTA.stl", printed logo-face down: logo plate
    # z -46..-44 with the 0.5 mm deep PRUSA deboss on z -46, 4 mm cable wall at x 22.3..26.3 with the cable notch,
    # 2 screw bosses reaching to z -16.3, length y 2.2..102.8).  On the PSU: logo plate on the bottom end of the
    # +X face (logo outwards, reading front -> rear, upright), cable wall at the bottom facing down (-y), bosses
    # going into the PSU's terminal recess (inside our plain box), centred in depth.  Wall face 0.1 below the
    # box bottom (no coplanar faces).  Pose in build_mk3s.py's REF_POSES convention (p' = R p + t, R rows):
    #   x_m = 293.26 - z  (plate inner face z -44 -> x 337.26 = the PSU's +X face; plate 2 mm proud)
    #   y_m = 56.08 - x   (wall face x 26.3 -> y 29.78; open edge x 0 -> y 56.08)
    #   z_m = 124.5 + y   (y 2.2..102.8 -> z 126.7..227.3, centred on the PSU's z 124.5..229.5)
    y0, y1 = PSU_Y[0] - 0.1, PSU_Y[0] - 0.1 + 26.3
    z0, z1 = (PSU_Z[0] + PSU_Z[1]) / 2 - 50.3, (PSU_Z[0] + PSU_Z[1]) / 2 + 50.3
    x = PSU_X[1]
    a["psu_cover_delta_R"] = [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]
    a["psu_cover_delta_t"] = [round(x - 44.0, 3), round(y1, 3), round((PSU_Z[0] + PSU_Z[1]) / 2 - 52.5, 3)]
    # its footprint on the +X face as seen from the printer's right side (left = front): BL, BR, TR, TL
    a["psu_cover_delta_footprint"] = [[x, round(y0, 3), z1], [x, round(y0, 3), z0], [x, round(y1, 3), z0],
                                      [x, round(y1, 3), z1]]
    return a


ANCHORS = _anchors()


# ----------------------------------------------------------------------------- self-check
