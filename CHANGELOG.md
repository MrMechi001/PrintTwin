# Changelog

## In development

* More printers: Klipper (Moonraker), OctoPrint and Bambu Lab adapters; printer models for
  MK4S, CORE One and generic bed-slinger / CoreXY machines. Other PrusaLink printers
  (MK4S, MK3.9, CORE One, XL, MINI) should already work – test reports welcome.
* PrintTwin inside PrusaSlicer / OrcaSlicer (as a print host shown in the slicer's printer tab).
* Printer control in the 3D view (start / pause / stop, upload, print queue) – opt-in, with login.
* Setup page in the browser, Windows package.

## 0.3.0 – first public release

* **True-to-size Original Prusa MK4** in the *Printer* view, built from Prusa's CAD parts
  and FokieH's assembly; bed, gantry and extruder follow the real printer.
* **Fast job loading**: PrusaSlicer hand-over hook (model ready ~2 s after the start);
  otherwise the model builds up while the file downloads.
* **Plain `.gcode`** from PrusaSlicer, OrcaSlicer, Bambu Studio, SuperSlicer and Cura.
* **Phone access** with QR code; sync warning when the printer's file and PrintTwin's copy differ.
* Without metrics the print head is placed at the current toolpath position.
* First start creates `config.local.toml`; `PrintTwin.bat` / `start.sh` find any Python 3.11+.
* URL options `?printer=1` and `?color=features` for shareable views.
* Simulated printer (`tools/sim_printer.py`), tests, CI.

## 0.1.0

* Live 3D view of the MK4's current print from PrusaLink + firmware metrics.
