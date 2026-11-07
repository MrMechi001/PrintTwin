# Contributing to PrintTwin

Thanks for helping! PrintTwin started on one Prusa MK4 and is growing to more printers –
the most valuable help right now is **testing on printers we don't have**.

## Test PrintTwin with your printer

Works with any printer that has **PrusaLink** today (MK4S, MK3.9, CORE One, XL, MINI …):

1. Set it up as in the [README quick start](README.md#quick-start), including the
   printer's metrics (*Settings → Network → Metrics & Log*).
2. Start a print and watch for a few minutes.
3. Open an issue with the **"Printer test report"** template and tell us:
   * printer model and firmware version (*Settings → Info*),
   * what the HUD shows next to the live dot (e.g. *Live nozzle position · 80 Hz*),
   * whether the part grows in step with the real print, and anything that looks off,
   * a screenshot if you can.

Please **don't attach** your `config.local.toml` (contains the API key) or metrics
recordings from `tools/probe_metrics.py` (they contain the printer's MAC address).

## Add a printer type (adapter)

An adapter connects one kind of printer. Today there is `prusalink`
(`printtwin/printer.py`, class `PrusaLinkPrinter`); Moonraker (Klipper), OctoPrint and
Bambu Lab are next. A new adapter is a class registered in `KINDS` in
`printtwin/__main__.py` and selected by `kind = "..."` in the config. It provides:

| Member | Used for |
|---|---|
| `__init__(cfg, cache_dir, incoming_dir)`, `start()` | config block of the printer; start polling threads |
| `id`, `name`, `kind`, `host`, `lock` | identity, LAN address (also matches UDP metrics) |
| `snapshot() -> dict` | the state the viewer shows (see `PrusaLinkPrinter.snapshot`) |
| `toolpath_info()`, `toolpath_bin(start)` | the current job's toolpath, also while it is still loading |
| `subscribe()`, `unsubscribe(q)`, `publish(event, obj)` | Server-Sent Events: `state` and `pos` |
| `on_datagram(payload)` | optional: UDP telemetry pushed by the printer |

The job file goes through `toolpath.JobDecoder` (any G-code dialect, any chunk sizes) and
`tracker.Tracker` (live position → printed segment). Most adapters only need to get the
job file, the file position and – if the printer reports it – the nozzle position.

`tools/sim_printer.py` shows how a printer is simulated for tests; a new adapter should
come with a similar simulator or recorded fixture so CI can test it without hardware.

## Add a printer model for the *Printer* view

Models are GLB files in `web/models/` with four groups – `static`, `bed` (G-code bed
coordinates, slides in Y), `xaxis` (moves in Z) and `carriage` (moves in X) – plus
anchors in `extras.printtwin` (see `tools/cad/build_mk4.py`). CoreXY printers (moving bed
in Z, head in X/Y) will need a second layout; open an issue first so we can agree on it.
Models must come from sources whose licence allows redistribution (e.g. GPL, CC BY,
CC BY-SA) and must not contain manufacturer logos.

## Development

* Python 3.11+, **standard library only** for the bridge (no `pip install` for users).
* Tests: `python -I tests/test_toolpath.py` and `python -I tests/test_bridge.py`
  (CI runs them on Windows and Linux, Python 3.11–3.13).
* Try changes without a printer: `python -I tools/sim_printer.py tests/fixtures/sample.bgcode`
  → <http://localhost:8791/>.
* Keep pull requests focused; describe what you tested (printer, firmware, slicer).

By contributing you agree that your contribution is licensed under the project's
**GPL-3.0** licence (printer models: GPL-2.0-compatible, see `web/models/NOTICE.md`).
