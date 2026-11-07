# mk4.glb – licence and sources

`mk4.glb` (and `mk4.glb.gz`) is a derivative work, licensed **GNU GPL v2.0**
(full text: `LICENSE-GPL-2.0.txt` next to this file).

Built by `tools/cad/build_mk4.py` from:

* **FokieH, "Prusa MK4 Model"** – https://www.printables.com/model/961911 – GPL-2.0
  (`MK4-COMPLETE_withSpoolHolder_20240913.stp`): the complete MK4 assembly. Its printed
  parts are Prusa Research's parts (volumes match the official STEP files).
* **Prusa Research, "MK4/MK3.9 printable parts"** – https://www.printables.com/model/451501 –
  GPL-2.0: Nextruder main plate, and the NEMA17 mounting patterns used to place the motors.
* PrintTwin (`tools/cad/build_mk4.py`, GPL-2.0): motors, MK52 heatbed and steel sheet
  (official dimensions: 254 × 264.4 mm heatbed, 252 × 241 mm sheet), spacers, linear
  bearings, belts and pulleys, nozzle and heater block, LCD knob and reset button.

The source of the model is those STEP files plus the build script: download the two
Printables models into `cad_src/` (see the script's header) and run it.

"Prusa", "Original Prusa" and the Prusa logo are trademarks of Prusa Research a.s.; this
model is for visualising the user's own printer and is not endorsed by Prusa Research.
