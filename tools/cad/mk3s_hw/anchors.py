"""MK3S+ (flat frame): spool, filament inlet and extruder cable bundle - anchors for the viewer.

Master frame: mm, X right, Y up, Z towards the front; parts at the reference pose (X axis at the top,
carriage nozzle at x 68.95). Every number below was MEASURED on Kayne's MK3S assembly (tessellated,
already in the master frame; a measuring reference only - nothing of it is used) or taken from
community measurements of the Prusament spool. build() does not read the reference.

What the stock printer does (verified on the reference):
  * Spool: Prusa's spool-holder clips onto the frame's top bar (x 86..121), its truss post leans
    backwards and carries a horizontal arm along +X at y 509.6..531.7, z 173.2..198.5 (flat
    underside, round top r 11.0 about (z 184.2, y 520.7)), ending in a lip at x 196.2..207.
    The spool axis is therefore LEFT-RIGHT (master X), not front-back. The spool's centre hole
    hangs on the arm's top line (y 531.69, z 184.2), so its centre is hub_radius below that line;
    it can slide between the post (x 101.2) and the lip (x 196.2) and sits in the middle here.
    The spool is above the frame (bottom 13 mm over the top bar), its centre 45 mm behind the
    frame's rear face. No bearing, no rod: the stock holder is one printed part (placed by the
    main session), so no part is added for it.
  * Filament inlet: the fs-cover's top boss has a conical entry (r 2.17 at the top face y 398.18,
    r 1.6 at y 394) on the hotend axis (x 68.97, z 278.70); the nozzle tip is at (68.95, 297.9, 278.7).
  * Cable bundle: the extruder cables (textile sleeve 13 x 490 mm with a nylon 1.75 mm stiffener)
    lie in the concave top cradle (r 4.99) of the cable-holder that sticks 39.5 mm backwards out of
    the x-carriage-back (z 238.7 -> 199.2) through the frame window, rising 5 deg towards its end.
    The nylon sits in the holder's r 1.75 hole 12.5 mm below the cradle. From the holder's end the
    bundle arcs backwards (behind the frame, never in front of its rear face z 229.5), then down and
    to the left, and enters the electronics box (behind the frame's left bar, x -32.8..5.2) from
    above through the keyhole opening in the einsy-base top (centre (-7.04, 200.68, 201.50), r 6.81,
    slot open to +x), where it is zip-tied / clipped and the nylon goes into its hole.

Part added: the bundle segment lying in the cable holder's cradle (carriage group), so that the
viewer's free cable (which starts at cable_start) continues a visible bundle on the holder.
"""
from __future__ import annotations

import os
import sys

_CAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/cad

if _CAD not in sys.path:
    sys.path.insert(0, _CAD)

import numpy as np  # noqa: E402

NEW_MATS = {
    # black braided textile sleeve; same colour / roughness as the viewer's cable (0x141518, 0.92)
    "sleeve": ((0.078, 0.082, 0.094), 0.0, 0.92),
}

# ----------------------------------------------------------------------------- measured numbers
# spool holder arm (reference solid 193): sections at x 120 / 150 / 180 all give top y 531.688 at
# z 184.20 (round top r 10.99 about (z 184.20, y 520.70)), i.e. the arm is horizontal along +X
ARM_TOP_Y, ARM_Z = 531.69, 184.20
POST_X = 101.21          # post's right face (largest x of the holder inside the spool's radial band)
LIP_X = 196.21           # the end lip's slope starts here (section z 184.2: y 531.688 -> 539.688 at x 201.8)
# Prusament 1 kg spool (community measurements, Prusa forum): outer dia. ~199-200 mm, width 69 mm,
# centre hole 51 mm (50 / 51 / 52 quoted; 51 by calipers / adapter makers)
SPOOL_R, SPOOL_W, HUB_R = 100.0, 69.0, 25.5
SPOOL_C = np.array([(POST_X + LIP_X) / 2, ARM_TOP_Y - HUB_R, ARM_Z])    # hole's top rests on the arm

# fs-cover (solid 152): hole circle fits at y 398.1 .. 393 -> centre (68.972, 278.699); top face y 398.18
INLET = np.array([68.97, 398.18, 278.70])

# cable-holder (solid 159): cradle circle fits (r 4.99) - centre y 330.17 at z 199.5, 326.80 at z 238.0,
# x 68.472; holder spans z 199.20 .. 238.70 (x-carriage-back's rear face at z 238.70)
CRADLE_R = 4.99
CRADLE_X = 68.47
CRADLE_SLOPE = (326.80 - 330.17) / (238.0 - 199.5)          # dy/dz of the cradle line (-0.0875)
HOLDER_Z0, HOLDER_Z1 = 199.20, 238.70
CABLE_R = 5.5            # textile sleeve 13 mm nominal over ~8 wires + nylon: ~11 mm; einsy opening r 6.81
LIFT = CABLE_R - CRADLE_R  # the bundle rests in the r 4.99 cradle: its axis 0.51 mm above the cradle's


def cradle_y(z):
    return 330.17 + CRADLE_SLOPE * (z - 199.5)


CABLE_START = np.array([CRADLE_X, cradle_y(HOLDER_Z0) + LIFT, HOLDER_Z0])     # holder's rear end
_d = np.array([0.0, -CRADLE_SLOPE, -1.0])
CABLE_DIR0 = _d / np.linalg.norm(_d)                                          # backwards, 5.0 deg up
# einsy-base (solid 192): opening circle fits at y 200.5 / 200 / 199.5 -> (-7.04, 201.50), r 6.81;
# top face y 200.68
CABLE_END = np.array([-7.04, 200.68, 201.50])
_t = np.radians(30.0)
CABLE_DIR1 = np.array([np.sin(_t), np.cos(_t), 0.0])     # out of the opening: up, leaning 30 deg to +x
LEN0, LEN1 = 120.0, 100.0                                # handle lengths (pose study in the self-check)
FREE_LENGTH = 400.0      # bundle from holder end to einsy top: 490 mm sleeve - ~40 on the holder - ~30..50
                         # inside the box, shortened by the twist (estimate). Optional length-based variant
                         # (as the viewer's bowden): len_start = len_end = max(30, (FREE_LENGTH - |end - start|)
                         # * 0.55) with the same directions - also collision-free over the pose range.

ANCHORS = {
    "spool": {"center": SPOOL_C.round(2).tolist(), "axis": [1.0, 0.0, 0.0], "radius": SPOOL_R,
              "width": SPOOL_W, "hub_radius": HUB_R},
    "filament_inlet": INLET.round(2).tolist(),                 # carriage group
    # no "filament_path": the straight strand from the viewer's unwind point clears everything (self-check)
    "cable_start": CABLE_START.round(2).tolist(),              # carriage group
    "cable_end": CABLE_END.round(2).tolist(),                  # static
    "cable_radius": CABLE_R,
    # cubic Bezier: p0 = cable_start (moved with the carriage), p1 = p0 + dir_start * len_start,
    # p2 = cable_end + dir_end * len_end, p3 = cable_end (dir_end points OUT of the einsy opening)
    "cable_handles": {"dir_start": CABLE_DIR0.round(4).tolist(), "len_start": LEN0,
                      "dir_end": CABLE_DIR1.round(4).tolist(), "len_end": LEN1,
                      "free_length": FREE_LENGTH},
}


def build():
    """-> [(name, group, material, (P, N, I))] in the master frame (reference pose)."""
    import cadquery as cq
    from build_mk4 import frame_for, mesh_of, place

    # the sleeve lying in the holder's cradle, from the x-carriage-back (0.2 mm into it) to the
    # holder's end, where the viewer's free cable continues with the same radius and tangent
    front = np.array([CRADLE_X, cradle_y(HOLDER_Z1 + 0.2) + LIFT, HOLDER_Z1 + 0.2])
    L = float(np.linalg.norm(CABLE_START - front))
    tube = cq.Workplane("XY").circle(CABLE_R).extrude(L)
    P, N, I = place(mesh_of(tube, 0.04), frame_for(CABLE_DIR0), front)
    return [("extruder-cable-on-holder", "carriage", "sleeve",
             (P.astype(np.float32), N.astype(np.float32), np.asarray(I, dtype=np.int64)))]


# ============================================================================= self-check
