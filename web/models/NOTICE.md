# Printer models – licence and sources

## mk4.glb, mk4s.glb

`mk4.glb` and `mk4s.glb` (and their `.gz` copies) are derivative works, licensed
**GNU GPL v2.0** (full text: `LICENSE-GPL-2.0.txt` next to this file).

Built by `tools/cad/build_mk4.py` (`mk4` / `mk4s`) from:

* **FokieH, "Prusa MK4 Model"** – https://www.printables.com/model/961911 – GPL-2.0
  (`MK4-COMPLETE_withSpoolHolder_20240913.stp`): the complete MK4 assembly. Its printed
  parts are Prusa Research's parts (volumes match the official STEP files).
* **Prusa Research, "MK4/MK3.9 printable parts"** – https://www.printables.com/model/451501 –
  GPL-2.0: Nextruder main plate and idler, and the NEMA17 mounting patterns used to place
  the motors.
* **Prusa Research, "MK4S/MK3.9S printable parts"** – https://www.printables.com/model/451500 –
  GPL-2.0 (`mk4s.glb` only): MK4S Nextruder print-fan case and cover, 360° fan shroud,
  fan-door cover, heatsink spacer, LoveBoard cover r3, Y belt holder and tensioner r3.
* **Marcus_3D, "Prusa MK4S Model for PrusaSlicer"** – https://www.printables.com/model/1030417 –
  GPL-2.0: used only as a measuring reference to find where the idler and the MK4S Nextruder
  parts sit (each Prusa part was registered on it); none of its geometry is in the models.
* PrintTwin (`tools/cad/build_mk4.py`, GPL-2.0): motors, MK52 heatbed and steel sheet
  (official dimensions: 254 × 264.4 mm heatbed, 252 × 241 mm sheet), spacers, linear
  bearings, belts and pulleys, nozzle and heater block, LCD knob and reset button, hotend-fan
  rotor and MK4S print-fan impeller.

The source of the models is those STEP files plus the build script: download the
Printables models into `cad_src/` (see the script's header) and run it.

## mini.glb

`mini.glb` (and `mini.glb.gz`) is a derivative work, licensed **GNU GPL v3.0** (full text:
`LICENSE` in the repository root). Built by `tools/cad/build_mini.py` from:

* **Prusa Research, "Original-Prusa-MINI"** – https://github.com/prusa3d/Original-Prusa-MINI –
  GPL-3.0: all printed parts (MINI+), heatsink, Y-carriage, Z bottom plate and steel sheet
  (STEP files), and the drawings used for the parts generated below. The engraved
  "ORIGINAL PRUSA MINI" lettering on the front Y plate is filled in the model.
* **Luis, "STEP File for Prusa Mini"** – https://www.printables.com/model/307054 – public
  domain: used only as a reference for where Prusa's parts sit (they share its part frames);
  none of its geometry is in the model.
* PrintTwin (`tools/cad/build_mini.py`, GPL-3.0), from Prusa's drawings: 30 × 30 extrusions,
  smooth rods and leadscrew, motors, hotend (heatbreak, heater block, nozzle, fittings),
  linear bearings, pulleys and belts, fans, SuperPINDA, heatbed plate and standoffs, 608
  bearings of the spool holder.

Source: unzip Prusa's repository into `cad_src/prusa_mini/extracted/` and run the script.

## xl.glb

`xl.glb` (and `xl.glb.gz`) is a derivative work, licensed **GNU GPL v2.0**. Built by
`tools/cad/build_xl.py` from:

* **Prusa Research, "XL printable parts"** – https://www.printables.com/model/449554 – GPL-2.0:
  x-carriage, y-carriages, xLCD cover and knob, Z bearing housings, bed controller case,
  telescopic spool holder.
* The Nextruder of `mk4.glb` (sources above, GPL-2.0).
* PrintTwin (`tools/cad/build_xl.py`, GPL-2.0): frame, panels, rails, belts, motors, heatbed
  tiles and frame, steel sheet (outline measured from PrusaSlicer's `xl_bed.stl`). The frame is
  estimated – no XL assembly has been published.

"Prusa", "Original Prusa" and the Prusa logo are trademarks of Prusa Research a.s.; these
models are for visualising the user's own printer and are not endorsed by Prusa Research.
