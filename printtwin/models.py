"""Which printer model to show: map what the printer / the job says onto a model key.

Model keys (see web/printers.js): mk4, mk4s, mk39, mk39s, coreone, coreonel, xl, mini,
plus the generic fallbacks bedslinger, corexy (bed moves in Z) and corexy_gantry (bed fixed,
the gantry moves in Z: Voron 2.4, Sovol SV08).

Sources, most reliable first:
  1. config:   model = "coreone"            (per [[printer]] block; "auto" = detect)
  2. the printer itself: PrusaLink GET /api/version -> "printer" type code (an MK4 reports
     "1.4.0") - the hardware, even if a job was sliced for a sibling model
  3. the job:  printer_model in the G-code / bgcode metadata, written by the slicer
               (PrusaSlicer: MK4IS, MK4S, COREONE, XL, MINIIS, V2_350, ...; OrcaSlicer:
               "Voron 2.4 350", "Creality K1 Max", ...)
Klipper reports no printer model, only its kinematics ("klipper/corexy+qgl" here): for
Klipper printers the job comes before that, and the kinematics are only the last resort.
"""
from __future__ import annotations

import re

MODEL_KEYS = ("mk4", "mk4s", "mk39", "mk39s", "coreone", "coreonel", "xl", "mini", "bedslinger", "corexy",
              "corexy_gantry")

# PrusaLink /api/version "printer" -> model key. Buddy firmware formats
# PrinterModelInfo::current().version as "type.version.subversion"
# (include/common/printer_model_data.hpp, firmware 6.x-8.0.1). MK4-family firmware reports
# the model selected on the printer; CORE One editions (One / One+ / Gen 2) and MINI / MINI+
# report the same code. Compare as strings ("1.3.10" is not "1.3.1").
PRUSALINK_TYPES = {
    "1.4.0": "mk4",
    "1.4.1": "mk4s",
    "1.3.9": "mk39",
    "1.3.10": "mk39s",
    "1.3.5": "bedslinger",      # MK3.5  (MK3S+ frame and extruder, new electronics)
    "1.3.6": "bedslinger",      # MK3.5S
    "2.1.0": "mini",            # MINI / MINI+
    "3.1.0": "xl",              # XL, any number of tools
    "3.1.1": "xl",              # XL+
    "7.1.0": "coreone",         # CORE One / One+ / One+ Gen 2
    "7.2.0": "coreone",         # CORE One Signature Oak
    "7.10.0": "coreone",        # CORE One INDX
    "8.1.0": "coreonel",        # CORE One L / L+
    "8.10.0": "coreonel",       # CORE One L INDX
}

# slicer printer_model strings (upper case, compared after removing spaces/dots) -> model key
# (PrusaSlicer vendor bundle 2.5.11: MK4S/MK4SMMU3, MK4IS/MK4ISMMU3/MK4, MK3.9S, MK3.9, MK3.5,
#  COREONE/COREONEMMU3/COREONEOAK/COREONE_INDX4T, COREONEL, XLIS/XL2IS/XL5IS/XL, XLP1T..XLP5T,
#  MINIIS/MINI; older MK3S, MK3, MK2.5 ...)
_SLICER = [
    (r"^MK4S", "mk4s"),
    (r"^MK4", "mk4"),            # MK4, MK4IS, MK4MMU3, MK4ISMMU3
    (r"^MK39S", "mk39s"),        # MK3.9S
    (r"^MK39", "mk39"),          # MK3.9, MK3.9MMU3
    (r"^MK35", "bedslinger"),    # MK3.5 / MK3.5S
    (r"^MK3|^MK2", "bedslinger"),  # MK3S, MK3, MK2.5 ...
    (r"^COREONEL", "coreonel"),  # COREONEL, COREONEL-INDX
    (r"^COREONE", "coreone"),    # COREONE, COREONEMMU3, COREONEOAK, COREONE_INDX4T
    (r"^XL", "xl"),              # XLIS, XL2IS, XL5IS, XL, XLP1T ... XLP5T (XL+)
    (r"^MINI", "mini"),          # MINI, MINIIS
    # Voron - PrusaSlicer bundle 3.0.0 (V2_350, VT_300, V0_120, VS_MK52) and its historical ids
    # (Voron_v2_250_afterburner, Voron_v1_*, Voron_v0_120, Voron_SW), OrcaSlicer ("Voron 2.4 350",
    # "Voron Trident 300", "Voron 0.1", "Voron Switchwire 250"); separators are removed first
    (r"^V2\d{3}$|^VORONV2|^VORON24", "corexy_gantry"),   # V2.4: bed fixed, gantry moves in Z
    (r"^VT\d{3}$|^VORONTRIDENT", "corexy"),              # Trident: bed moves in Z
    (r"^V0\d{3}$|^VORONV0|^VORON0", "corexy"),           # V0: bed moves in Z
    (r"^VORONV1", "corexy"),                              # Legacy / V1: bed moves in Z
    (r"^VSMK|^VORONSW", "bedslinger"),                    # Switchwire: CoreXZ, bed moves in Y
    # other Klipper printers (OrcaSlicer / vendor slicer names)
    (r"^SOVOLSV08", "corexy_gantry"),                     # SV08 / SV08 MAX: quad gantry level
    (r"^SOVOLZERO", "corexy"),
    (r"^SOVOL", "bedslinger"),                            # SV06 / SV07 ...
    (r"^RATRIGVMINION|^VMINION", "bedslinger"),
    (r"^RATRIG|^VC\d", "corexy"),                         # V-Core 3 / 4 (PrusaSlicer: VC3_300, VC4_300_XY)
    (r"^CREALITYK\d|^CREALITYSPARKX", "corexy"),          # K1 / K1C / K1 Max / K1 SE / K2 family
    (r"^CREALITY", "bedslinger"),                         # Ender-3 V3 (CoreXZ), Ender-3 V3 KE / Plus, Hi
    (r"^QIDI|^Q1PRO|^Q2|^XPLUS|^XMAX|^XSMART", "corexy"), # Qidi Q1 Pro, X-Plus 3/4/5, X-Max 3, Q2 ...
    (r"^ELEGOONEPTUNE", "bedslinger"),                    # Neptune 4 / Pro / Plus / Max
    (r"^ANYCUBICKOBRAS", "corexy"),                       # Kobra S1
    (r"^ANYCUBICKOBRA", "bedslinger"),                    # Kobra 2 / 3
    # Bambu Lab (Bambu Studio / OrcaSlicer: "Bambu Lab X1 Carbon", "Bambu Lab A1 mini", ...)
    (r"^BAMBULABA\d", "bedslinger"),                      # A1, A1 mini, A2L: bed moves in Y
    (r"^BAMBULAB", "corexy"),                             # X1 / X1 Carbon / X1E, P1P / P1S, H2D ...: bed in Z
]

# Klipper kinematics (printer type "klipper/<kinematics>[+qgl]", see moonraker.py) -> generic model.
# quad_gantry_level means a flying gantry; cartesian / corexz are mostly bed-slingers.
_KLIPPER = {"corexy": "corexy", "limited_corexy": "corexy", "hybrid_corexy": "corexy", "corexy+qgl": "corexy_gantry",
            "limited_corexy+qgl": "corexy_gantry", "cartesian": "bedslinger", "limited_cartesian": "bedslinger",
            "corexz": "bedslinger", "limited_corexz": "bedslinger", "hybrid_corexz": "bedslinger"}


def from_slicer(printer_model: str | None) -> str | None:
    if not printer_model:
        return None
    s = re.sub(r"[\s.\-_]", "", str(printer_model)).upper()
    for pat, key in _SLICER:
        if re.match(pat, s):
            return key
    return None


def from_prusalink(type_code: str | None) -> str | None:
    if not type_code:
        return None
    code = str(type_code).strip()
    if code in PRUSALINK_TYPES:
        return PRUSALINK_TYPES[code]
    # unknown sub-version of a known type: fall back to "<type>.<version>.0"
    m = re.match(r"^(\d+)\.(\d+)", code)
    return PRUSALINK_TYPES.get(f"{m.group(1)}.{m.group(2)}.0") if m else None


def from_klipper(printer_type: str | None) -> str | None:
    t = str(printer_type or "")
    return _KLIPPER.get(t[len("klipper/"):]) if t.startswith("klipper/") else None


def resolve(configured: str | None, job_printer_model: str | None, prusalink_type: str | None) -> tuple[str | None, str]:
    """-> (model key or None, where it came from). prusalink_type: the printer's own type,
    a PrusaLink code ("1.4.0") or "klipper/<kinematics>" from the Moonraker adapter."""
    c = (configured or "auto").strip().lower()
    if c != "auto":
        return (c if c in MODEL_KEYS else None), "config"
    if str(prusalink_type or "").startswith("klipper/"):
        k = from_slicer(job_printer_model)
        if k:
            return k, "job"
        k = from_klipper(prusalink_type)
        return (k, "printer") if k else (None, "unknown")
    k = from_prusalink(prusalink_type)
    if k:
        return k, "printer"
    k = from_slicer(job_printer_model)
    if k:
        return k, "job"
    return None, "unknown"
