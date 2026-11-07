# mk4.glb, mk4s.glb – licence and sources

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

"Prusa", "Original Prusa" and the Prusa logo are trademarks of Prusa Research a.s.; these
models are for visualising the user's own printer and are not endorsed by Prusa Research.
