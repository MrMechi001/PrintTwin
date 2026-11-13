# Changelog

## In development

* More printers: true-to-size Bambu Lab printers in the *Printer* view; an OctoPrint adapter;
  true-to-size CORE One and CORE One L, the Voron 2.4 and Trident from the Voron CAD, the XL
  refined with real measurements. Other PrusaLink printers (MK3.9, CORE One, XL, MINI) and
  Klipper printers should already work – test reports welcome.
* Klipper: objects excluded during the print (EXCLUDE_OBJECT) shown as not printed.
* Printer control in the 3D view (start / pause / stop, upload, print queue) – opt-in, with login.
* Setup page in the browser, Windows package.

## 0.8.1 – Prints sent via Prusa Connect

* **Prints sent via Prusa Connect** (opt-in, unofficial): the printer doesn't hand out their file
  while printing, so PrintTwin downloads it from Prusa Connect (`[connect] enabled = true`, sign in
  once with `python run.py connect-login` on Prusa's own page – PrintTwin never sees the password;
  the tokens stay in `connect.local.json`). Only the printer list and the job file are read.

## 0.8.0 – Original Prusa MK3 / MK3S / MK3S+

* **True-to-size MK3S+ model** (`web/models/mk3s.glb`) in the *Printer* view: the flat frame from
  Prusa's drawings, Prusa's MK3S / MK3S+ printed parts (orange PETG, black extruder body), motors,
  belts, hotend, SuperPINDA, fans, Einsy box, PSU and the 20 × 4 character LCD showing the job,
  filament from the spool on top of the frame. Built by `tools/cad/build_mk3s.py` (GPL-2.0, sources
  in `web/models/NOTICE.md`). Bed-stack and plate thicknesses are still to be measured on a real
  printer. The MK3 (non-S) is shown with it, marked approximate.
* Viewer: a character-LCD style (20 × 4), a spool turning about any axis, the extruder cable's
  shape and thickness from the model.
* **Original Prusa MK3 / MK3S / MK3S+** with PrusaLink on a Raspberry Pi (PrusaLink 0.7 / 0.8, tested
  against a simulated PrusaLink 0.7.2): the printer type from PrusaLink's `original` field
  (`PrusaLink I3MK3S` → MK3S / MK3S+, `I3MK3` → MK3, MK2.5/S → generic bed-slinger), new model keys
  `mk3s` and `mk3`;
  an unknown 1.3.x type code is no longer taken for the MK3 – the job's printer model decides.
* **Login with user and password** (HTTP digest, MD5-sess as PrusaLink on the Pi wants it) as an
  alternative to the API key: `user` + `password` in the printer's block (also a `\` in the user
  name). PrintTwin never sends an empty API key; the examples' placeholders (`"..."`,
  `PRUSALINK-API-KEY`) count as not filled in.
* **Estimated position for printers without metrics:** PrusaLink on the Pi reports only Z every 2 s
  (z-hops included), the whole percent and the time left. The print is placed between the job's M73
  marks inside the layer the printer is at – a z-hop doesn't mark layers printed (hop reports in a
  row can't put it past the printer's next M73 mark), the estimate only moves forward, stealth mode
  (M73 Q marks) is recognised by which marks fit the height – the height counts only after three Z
  reports and the mode changes only on a clear lead, so a z-hop at the start can't make it jump
  ahead. The HUD says *estimated*. An MK4 that
  sends metrics behaves exactly as before; one that doesn't is estimated too, with a hint to turn
  the metrics on.
* Job files with a space in their path (PrusaLink's `PrusaLink gcodes` folder) or a `%` in their name
  download; a job on the MK3's SD card (not downloadable) says so, takes the slicer hand-over when
  there is one and isn't retried over the network.
* PrusaLink answering 503 (the Pi runs, the printer isn't connected) shows *PrusaLink is up, the
  printer is not connected*; a refused login says so too. A job without id (PrusaLink just
  restarted) is told apart by its file; a reprint of the same file starts over, also when the first
  print had no id. Status `READY` and the Pi's storage list are fine.
* Simulator: `tools/sim_printer.py --pi` (MK3S+ on PrusaLink 0.7.2) with `--stealth`, `--sd`,
  `--digest`, `--no-printer`, `--no-id`, `--dir`, z-hop reports at random (`--zhop`, `--zhop-rate`,
  `--seed`); `tools/probe_api.py` logs in with user + password too and takes a printer id.

## 0.7.1 – Bambu printers checked against Bambu's certificates

* Bambu's CA certificates come with PrintTwin (`printtwin/certs/bambu.pem`, from ha-bambulab, MIT):
  a Bambu printer's certificate is checked from the first connection on - signed by Bambu and made
  out to the configured serial number - before the access code is sent. A printer reset with a new
  certificate connects without deleting anything.
* A newer model whose certificate chain the bundled CAs don't cover yet is checked by its
  fingerprint instead (trust on first use) and the log says so. A printer that was verified once
  is remembered, so this fallback can't be used to slip in another certificate later.
* A certificate for another printer (wrong host or serial in the config) is refused with a clear
  message.

## 0.7.0 – Bambu Lab printers (read-only)

* **Bambu Lab printers** (read-only): `kind = "bambu"` with the serial number and the LAN access
  code. Status over MQTT, the job file (.gcode.3mf) over FTPS or from the slicer's hand-over – no
  printer control, no cloud, no Developer Mode. Bambu printers report no nozzle position: the
  position is estimated between the job's progress marks from the reported layer and percent, and
  the viewer says so. Model and build volume from the serial number. The printer's certificate is
  verified against a CA file or pinned on first contact (checked before the access code is sent,
  on the FTPS data channel too). A reprint of the same job starts over; an earlier file under the
  same name, a hand-over sliced after the start or a file with the wrong layer count is not shown;
  the plate of a multi-plate job is picked by its layer count. A mistyped serial number is named
  in the log.
* .gcode.3mf archives are unpacked in pieces, with limits (no memory blow-up from a crafted file).
* Bambu Studio / OrcaSlicer jobs: .gcode.3mf archives (plate G-code, project settings), Bambu's
  G-code comments, M73 progress marks, M1020 filament changes.
* The slicer hand-over (post-processing hook) also works with Bambu Studio and OrcaSlicer.
* Simulated Bambu printer: `tools/sim_bambu.py`.

## 0.6.0 – Klipper printers (Voron & co.)

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
* Simulated Klipper printer: `tools/sim_moonraker.py`. Klipper support is tested against it
  (built from Moonraker's and Klipper's source) – reports from real Klipper printers welcome.
* Fix: printing the same file again started with the old progress (all printed).
* Fix: when PrintTwin started during a pause, or the model arrived during the start macro, the
  part could show as printed up to the parked nozzle height; the printer's file position now
  caps what counts as done.
* Fix: live playback stalled after the printer (or its Klipper host) restarted.
* Fix: a viewer opened in a hidden tab (or a slicer tab not shown yet) showed nothing until *Fit*.
* Fix: the spool turned the wrong way (generic CoreXY, MINI+) – every spool now unwinds towards
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
