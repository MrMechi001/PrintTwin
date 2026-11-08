# Changelog

## In development

* More printers: Klipper (Moonraker), OctoPrint and Bambu Lab adapters; true-to-size
  CORE One and CORE One L, the XL refined with real measurements. Other PrusaLink printers
  (MK3.9, CORE One, XL, MINI) should already work – test reports welcome.
* Printer control in the 3D view (start / pause / stop, upload, print queue) – opt-in, with login.
* Setup page in the browser, Windows package.

## Unreleased

* **Klipper printers (Voron & co.)** through Moonraker: `kind = "moonraker"`. PrintTwin reads
  Klipper's status about 4 times a second – file position and live toolhead position (G-code
  offsets removed) for the live view, temperatures, progress from the file position, remaining
  time from the slicer's estimate – and loads the job file from Moonraker. Between Klipper's
  position updates the nozzle follows the toolpath instead of cutting across the part.
* Klipper in the slicer: PrintTwin as print host for PrusaSlicer "Klipper (via Moonraker)",
  OrcaSlicer "Moonraker (Klipper)" and the OctoPrint host types. Because Moonraker trusts LAN
  addresses, only the slicer's calls are passed on (connection test, upload, start the
  uploaded file) – no G-code, emergency stop, file deletion or reboot – and without an API key
  only from LAN addresses. A file Moonraker changes after the upload (object processing) is
  loaded from the printer instead of PrintTwin's copy.
* Printer model for Klipper printers from the slicer's printer name (Voron 2.4 / Trident / V0 /
  Switchwire, Sovol SV08, RatRig, Creality K1, Qidi, Elegoo, Anycubic) or Klipper's kinematics;
  new generic **flying-gantry CoreXY** model (bed fixed, the gantry moves in Z: Voron 2.4, SV08).
  Generic models take their size from the printer's travel limits.
* Simulated Klipper printer: `tools/sim_moonraker.py`.
* Fix: printing the same file again started with the old progress (all printed).
* Fix: a viewer opened in a hidden tab (or a slicer tab not shown yet) showed nothing until *Fit*.
* Fix: the spool turned the wrong way (generic CoreXY, MINI+) - every spool now unwinds towards
  where its filament goes.

## 0.5.0 – PrintTwin inside your slicer

* **PrintTwin inside PrusaSlicer and OrcaSlicer**: set `http://<PC>:8790/p/<printer id>` as
  the print host (host type PrusaLink, the printer's PrusaLink password / API key) and the
  slicer's printer tab (*Physical Printer* / *Device*) shows the live 3D view. Connection test, uploads and
  *Upload and print* pass through PrintTwin to the printer unchanged (the slicer's key, none of
  PrintTwin's own); PrintTwin keeps a copy of each upload, so the model is ready the moment the
  print starts. Off unless `[slicer] print_host = true`. Tested with PrusaSlicer 2.9 and an MK4
  (firmware 8.0).
* Simulated printer: accepts uploads (PrusaLink PUT); *Upload and print* starts the new job;
  `--refuse S` refuses the job file for S seconds.
* Fix: a job whose model was ready before the print began (slicer copy) showed as fully printed
  while the printer was still heating with the nozzle parked above it.
* Fix: a job file the printer doesn't hand out (e.g. sent via Prusa Connect, while printing) is
  retried with pauses instead of every 2 s – the 3D view no longer jumps back to the default
  camera with each attempt; a camera the user moved is kept when a model reloads.

## 0.4.0 – more printers in 3D, multi-material

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
