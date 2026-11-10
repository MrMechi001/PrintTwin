"""Which printer model to show: map what the printer / the job says onto a model key.

Model keys (see web/printers.js): mk4, mk4s, mk39, mk39s, coreone, coreonel, xl, mini, mk3s
(MK3S / MK3S+), mk3, plus the generic fallbacks bedslinger, corexy (bed moves in Z) and
corexy_gantry (bed fixed, the gantry moves in Z: Voron 2.4, Sovol SV08).

Sources, most reliable first:
  1. config:   model = "coreone"            (per [[printer]] block; "auto" = detect)
  2. the printer itself: PrusaLink GET /api/version -> "printer" type code (an MK4 reports
     "1.4.0") - the hardware, even if a job was sliced for a sibling model. PrusaLink on a
     Raspberry Pi (MK3 family) has no "printer" key, only "original": "PrusaLink I3MK3S"
     (type_from_original turns that into the same code, "1.3.1")
  3. the job:  printer_model in the G-code / bgcode metadata, written by the slicer
               (PrusaSlicer: MK4IS, MK4S, COREONE, XL, MINIIS, V2_350, ...; OrcaSlicer:
               "Voron 2.4 350", "Creality K1 Max", ...)
Klipper reports no printer model, only its kinematics ("klipper/corexy+qgl" here): for
Klipper printers the job comes before that, and the kinematics are only the last resort.
"""
from __future__ import annotations

import re

MODEL_KEYS = ("mk4", "mk4s", "mk39", "mk39s", "coreone", "coreonel", "xl", "mini", "mk3s", "mk3", "bedslinger",
              "corexy", "corexy_gantry")

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
    "1.3.1": "mk3s",            # MK3S / MK3S+ (PrusaLink on a Raspberry Pi, see type_from_original)
    "1.3.0": "mk3",             # MK3
    "1.2.6": "bedslinger",      # MK2.5S
    "1.2.5": "bedslinger",      # MK2.5
    "2.1.0": "mini",            # MINI / MINI+
    "3.1.0": "xl",              # XL, any number of tools
    "3.1.1": "xl",              # XL+
    "7.1.0": "coreone",         # CORE One / One+ / One+ Gen 2
    "7.2.0": "coreone",         # CORE One Signature Oak
    "7.10.0": "coreone",        # CORE One INDX
    "8.1.0": "coreonel",        # CORE One L / L+
    "8.10.0": "coreonel",       # CORE One L INDX
}

# PrusaLink on a Raspberry Pi: /api/version "original" = "PrusaLink <printer type>" (the Connect SDK's
# PrinterType names) -> the type code Prusa Connect shows; "Unknown printer type" until PrusaLink knows it
PI_TYPES = {"I3MK3S": "1.3.1", "I3MK3": "1.3.0", "I3MK25S": "1.2.6", "I3MK25": "1.2.5"}

# type families whose sub-versions are different printers (1.3.0 MK3, 1.3.1 MK3S, 1.3.5 MK3.5,
# 1.3.9 MK3.9 ...): an unknown sub-version is not taken for "<type>.<version>.0" - the job decides
NO_SUBVERSION_FALLBACK = {"1.3"}

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
    (r"^MK3S", "mk3s"),          # MK3S (also the MK3S+), MK3SMMU2S, MK3SMMU3
    (r"^MK3", "mk3"),            # MK3, MK3MMU2
    (r"^MK2", "bedslinger"),     # MK2.5, MK2.5S, MK2S ...
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
    if not m or f"{m.group(1)}.{m.group(2)}" in NO_SUBVERSION_FALLBACK:
        return None
    return PRUSALINK_TYPES.get(f"{m.group(1)}.{m.group(2)}.0")


def type_from_original(original: str | None) -> str | None:
    """PrusaLink on a Raspberry Pi: "PrusaLink I3MK3S" -> "1.3.1"; None while it doesn't know."""
    m = re.match(r"^\s*PrusaLink\s+(\w+)\s*$", str(original or ""))
    return PI_TYPES.get(m.group(1).upper()) if m else None


# Bambu Lab: model id (slice_info printer_model_id, SSDP DevModel) and the first three characters of
# the serial number (BambuStudio's sn_prefix) -> name, generic model, build volume (mm). All CoreXY
# with the bed moving in Z, except the A-series bed-slingers ("i3" in BambuStudio's printer_structure).
BAMBU_MODELS = {
    "BL-P001": ("X1 Carbon", "corexy", (256, 256, 256)), "BL-P002": ("X1", "corexy", (256, 256, 256)),
    "C13": ("X1E", "corexy", (256, 256, 256)), "C11": ("P1P", "corexy", (256, 256, 256)),
    "C12": ("P1S", "corexy", (256, 256, 256)), "N7": ("P2S", "corexy", (256, 256, 256)),
    "N6": ("X2D", "corexy", (256, 256, 260)), "O1D": ("H2D", "corexy", (350, 320, 325)),
    "O1E": ("H2D Pro", "corexy", (350, 320, 325)), "O2D": ("H2D Pro", "corexy", (350, 320, 325)),
    "O1S": ("H2S", "corexy", (340, 320, 340)), "O1C": ("H2C", "corexy", (330, 320, 325)),
    "O1C2": ("H2C", "corexy", (330, 320, 325)), "N10": ("H3C", "corexy", (330, 320, 325)),
    "N2S": ("A1", "bedslinger", (256, 256, 256)), "N1": ("A1 mini", "bedslinger", (180, 180, 180)),
    "N9": ("A2L", "bedslinger", (330, 320, 325)),
}
BAMBU_SN_PREFIX = {"00M": "BL-P001", "00W": "BL-P002", "03W": "C13", "01S": "C11", "01P": "C12", "22E": "N7",
                   "20P": "N6", "094": "O1D", "239": "O1E", "093": "O1S", "31B": "O1C2", "32X": "N10",
                   "039": "N2S", "030": "N1", "26A": "N9"}


def bambu_model_id(serial: str | None) -> str | None:
    return BAMBU_SN_PREFIX.get(str(serial or "").strip().upper()[:3])


def from_bambu(printer_type: str | None) -> str | None:
    t = str(printer_type or "")
    m = BAMBU_MODELS.get(t[len("bambu/"):]) if t.startswith("bambu/") else None
    return m[1] if m else None


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
    if str(prusalink_type or "").startswith("bambu/"):      # the hardware (serial number), like PrusaLink
        k = from_bambu(prusalink_type)
        if k:
            return k, "printer"
        k = from_slicer(job_printer_model)
        return (k, "job") if k else ("corexy", "printer")
    k = from_prusalink(prusalink_type)
    if k:
        return k, "printer"
    k = from_slicer(job_printer_model)
    if k:
        return k, "job"
    return None, "unknown"
