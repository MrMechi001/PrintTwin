# Changelog

## In development

* **True-to-size Original Prusa MK4S** in the *Printer* view: the new Nextruder (front
  print-fan case, 360° shroud, fan-door cover) from Prusa's MK4S parts, black Z parts.
  MK3.9 / MK3.9S are shown as the MK4 / MK4S (approximate).
* MK4 model: Nextruder idler (swivels, nut, lever) and hotend-fan rotor added.
* **True-to-size Original Prusa MINI+** in the *Printer* view, from Prusa's MINI STEP parts
  and drawings: the extruder rides on the Z carriage, a PTFE bowden runs to the hotend, the
  portrait LCD shows the live status, the spool sits on the printed spool holder.
* **Original Prusa XL** in the *Printer* view (approximate): CoreXY gantry, bed lowering in Z,
  Prusa's XL parts and the Nextruder on a frame measured from Prusa's dimension photo;
  filament from the side spools through the entry sensors and the cable bundle onto the head.
* **Multi-material**: tool / filament changes (`T<n>`) and all filament colours are read from
  the job; the print is coloured per filament, and the XL shows all its heads (up to five, from
  the printer model, e.g. XL5IS) with a spool per head (three left, two right, smooth PTFE
  tubes from the sensor blocks), parks the idle heads in their docks and switches the active
  one at every tool change.
* The printer model is detected from the printer itself (PrusaLink type code), then the job
  file; `model = "…"` in the config overrides it. Generic bed-slinger / CoreXY models for
  printers without their own model yet.
* A second PrintTwin no longer shares the port with a running one (Windows): it says
  "already running" and opens the browser.
* More printers: Klipper (Moonraker), OctoPrint and Bambu Lab adapters; true-to-size
  CORE One, CORE One L and XL. Other PrusaLink printers (MK3.9, CORE One, XL, MINI) should
  already work – test reports welcome.
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
