"""Printer model detection: slicer printer_model strings, PrusaLink type codes, priorities.
Also: every model key has an entry in web/printers.js (with a fallback while its GLB is missing).

usage: python -I tests/test_models.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import models  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    print(("PASS " if ok else "FAIL ") + f"{name}: {got!r}" + ("" if ok else f" (want {want!r})"))
    failures += 0 if ok else 1


for s, want in [("MK4", "mk4"), ("MK4IS", "mk4"), ("MK4ISMMU3", "mk4"), ("MK4S", "mk4s"), ("MK4SMMU3", "mk4s"),
                ("MK3.9", "mk39"), ("MK3.9S", "mk39s"), ("MK3.9MMU3", "mk39"), ("COREONE", "coreone"),
                ("COREONEL", "coreonel"), ("COREONEMMU3", "coreone"), ("XL", "xl"), ("XL5", "xl"), ("XLIS", "xl"),
                ("MINI", "mini"), ("MINIIS", "mini"), ("ENDER3", None), ("", None), (None, None)]:
    check(f"slicer {s!r}", models.from_slicer(s), want)

for s, want in [("MK3.5", "bedslinger"), ("MK3S", "mk3s"), ("MK2.5S", "bedslinger"),
                ("XLP5T", "xl"), ("XL2IS", "xl"), ("COREONEOAK", "coreone"), ("COREONE_INDX4T", "coreone"),
                ("COREONEL-INDX", "coreonel"), ("MK3.9SMMU3", "mk39s")]:
    check(f"slicer {s!r}", models.from_slicer(s), want)

for code, want in [("1.4.0", "mk4"), ("1.4.1", "mk4s"), ("1.3.9", "mk39"), ("1.3.10", "mk39s"),
                   ("1.3.5", "bedslinger"), ("2.1.0", "mini"), ("3.1.0", "xl"), ("3.1.1", "xl"),
                   ("7.1.0", "coreone"), ("7.10.0", "coreone"), ("7.2.0", "coreone"), ("8.1.0", "coreonel"),
                   ("8.10.0", "coreonel")]:
    check(f"prusalink {code}", models.from_prusalink(code), want)
check("prusalink unknown sub-version 1.4.7", models.from_prusalink("1.4.7"), "mk4")
check("prusalink unknown 9.9.9", models.from_prusalink("9.9.9"), None)

# MK3 family: PrusaLink on a Raspberry Pi names the printer only in /api/version "original"
for s, want in [("MK3S", "mk3s"), ("MK3SMMU2S", "mk3s"), ("MK3SMMU3", "mk3s"), ("MK3", "mk3"), ("MK3MMU2", "mk3"),
                ("MK3.5S", "bedslinger"), ("MK3.9", "mk39"), ("MK2.5", "bedslinger"), ("MK2.5SMMU2S", "bedslinger")]:
    check(f"slicer {s!r}", models.from_slicer(s), want)
for orig, want in [("PrusaLink I3MK3S", "1.3.1"), ("PrusaLink I3MK3", "1.3.0"), ("PrusaLink I3MK25", "1.2.5"),
                   ("PrusaLink I3MK25S", "1.2.6"), ("Unknown printer type", None), ("PrusaLink", None), (None, None),
                   ("PrusaLink I3MK39", None)]:
    check(f"original {orig!r}", models.type_from_original(orig), want)
for code, want in [("1.3.1", "mk3s"), ("1.3.0", "mk3"), ("1.2.5", "bedslinger"), ("1.2.6", "bedslinger"),
                   ("1.3.10", "mk39s"), ("1.3.9", "mk39")]:
    check(f"prusalink {code}", models.from_prusalink(code), want)
# an unknown 1.3.x is another MK3-family printer (MK3, MK3S, MK3.5, MK3.9 ... all differ): not taken
# for the MK3 (1.3.0) - the job's printer_model decides
check("prusalink unknown 1.3.7 -> not the MK3", models.from_prusalink("1.3.7"), None)
check("prusalink unknown 1.3.11 -> not the MK3", models.from_prusalink("1.3.11"), None)
check("unknown 1.3.x: the job decides", models.resolve("auto", "MK3.9S", "1.3.7"), ("mk39s", "job"))
check("unknown 1.3.x, no job: unknown", models.resolve("auto", None, "1.3.7"), (None, "unknown"))
check("MK3S+ on a Pi: the printer before the job",
      models.resolve("auto", "MK4S", models.type_from_original("PrusaLink I3MK3S")), ("mk3s", "printer"))
check("PrusaLink on a Pi not knowing its printer yet: the job", models.resolve("auto", "MK3S", None), ("mk3s", "job"))
check("config mk3s", models.resolve("mk3s", None, None), ("mk3s", "config"))
check("config mk3", models.resolve("mk3", None, "1.3.1"), ("mk3", "config"))

# web/printers.js: every model key is there; the MK3 entries use mk3s.glb and fall back to the
# generic bed-slinger while it doesn't exist (the viewer skips a GLB /api/models doesn't list, and a
# 404 falls back like any failed load)
js = (ROOT / "web" / "printers.js").read_text(encoding="utf-8")
entries = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\w+):\s*\{(.*?)\},?\s*$", js, re.M)}
check("printers.js: an entry per model key", sorted(k for k in models.MODEL_KEYS if k not in entries), [])
for key in ("mk3s", "mk3"):
    e = entries.get(key, "")
    check(f"printers.js {key}: mk3s.glb, falls back to the bed-slinger",
          ("models/mk3s.glb" in e and "fallback: 'bedslinger'" in e), True)
check("printers.js mk3: marked approximate", "approx: true" in entries.get("mk3", ""), True)

check("config wins", models.resolve("coreone", "MK4IS", "1.4.0"), ("coreone", "config"))
check("printer hardware before job", models.resolve("auto", "MK4IS", "1.4.1"), ("mk4s", "printer"))
check("job when printer unknown", models.resolve("auto", "COREONE", None), ("coreone", "job"))
check("nothing known", models.resolve(None, None, None), (None, "unknown"))
check("bad config value", models.resolve("ender", None, "1.4.0"), (None, "config"))

# Klipper printers: slicer names (PrusaSlicer ids + OrcaSlicer names) and kinematics
for s, want in [("V2_350", "corexy_gantry"), ("V2_250", "corexy_gantry"), ("Voron_v2_300_afterburner", "corexy_gantry"),
                ("Voron 2.4 350", "corexy_gantry"), ("VT_300", "corexy"), ("Voron Trident 250", "corexy"),
                ("V0_120", "corexy"), ("Voron_v0_120", "corexy"), ("Voron 0.1", "corexy"),
                ("Voron_v1_250_afterburner", "corexy"), ("VS_MK52", "bedslinger"), ("Voron_SW", "bedslinger"),
                ("Voron Switchwire 250", "bedslinger"), ("Sovol SV08", "corexy_gantry"), ("Sovol SV08 MAX", "corexy_gantry"),
                ("Sovol SV07", "bedslinger"), ("Sovol Zero", "corexy"), ("RatRig V-Core 3 300", "corexy"),
                ("VC4_300_XY", "corexy"), ("RatRig V-Minion", "bedslinger"), ("Creality K1 Max", "corexy"),
                ("Creality K2 Plus", "corexy"), ("Creality Ender-3 V3 KE", "bedslinger"), ("Creality Hi", "bedslinger"),
                ("Qidi Q1 Pro", "corexy"), ("Q1 Pro", "corexy"), ("X-Plus 3", "corexy"), ("X-MAX 3", "corexy"),
                ("Elegoo Neptune 4 Pro", "bedslinger"), ("Anycubic Kobra 3", "bedslinger"),
                ("Anycubic Kobra S1", "corexy"), ("XL5IS", "xl")]:
    check(f"slicer {s!r}", models.from_slicer(s), want)
for s, want in [("Bambu Lab X1 Carbon", "corexy"), ("Bambu Lab X1E", "corexy"), ("Bambu Lab P1S", "corexy"),
                ("Bambu Lab P1P", "corexy"), ("Bambu Lab H2D", "corexy"), ("Bambu Lab A1", "bedslinger"),
                ("Bambu Lab A1 mini", "bedslinger"), ("Bambu Lab A2L", "bedslinger"), ("Bambu Lab X2D", "corexy"),
                ("Bambu Lab H2C", "corexy"), ("Bambu Lab P2S", "corexy")]:
    check(f"slicer {s!r}", models.from_slicer(s), want)
for t, want in [("klipper/corexy", "corexy"), ("klipper/corexy+qgl", "corexy_gantry"), ("klipper/cartesian", "bedslinger"),
                ("klipper/corexz", "bedslinger"), ("klipper/delta", None), ("1.4.0", None)]:
    check(f"klipper {t}", models.from_klipper(t), want)
check("klipper: the job's model first", models.resolve("auto", "VT_300", "klipper/corexy+qgl"), ("corexy", "job"))
check("klipper: kinematics when the job says nothing", models.resolve("auto", None, "klipper/corexy+qgl"),
      ("corexy_gantry", "printer"))
check("klipper: config wins", models.resolve("bedslinger", "V2_350", "klipper/corexy"), ("bedslinger", "config"))
check("klipper: delta, unknown job -> unknown", models.resolve("auto", "FLSun V400", "klipper/delta"), (None, "unknown"))

print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
