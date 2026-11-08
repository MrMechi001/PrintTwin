"""Printer model detection: slicer printer_model strings, PrusaLink type codes, priorities.

usage: python -I tests/test_models.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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

for s, want in [("MK3.5", "bedslinger"), ("MK3S", "bedslinger"), ("MK2.5S", "bedslinger"),
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
                ("Bambu Lab A1 mini", "bedslinger")]:
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
