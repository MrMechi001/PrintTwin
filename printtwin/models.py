"""Which printer model to show: map what the printer / the job says onto a model key.

Model keys (see web/printers.js): mk4, mk4s, mk39, mk39s, coreone, coreonel, xl, mini,
plus the generic fallbacks bedslinger and corexy.

Sources, most reliable first:
  1. config:   model = "coreone"            (per [[printer]] block; "auto" = detect)
  2. the printer itself: PrusaLink GET /api/version -> "printer" type code (an MK4 reports
     "1.4.0") - the hardware, even if a job was sliced for a sibling model
  3. the job:  printer_model in the G-code / bgcode metadata, written by the slicer
               (PrusaSlicer: MK4IS, MK4S, COREONE, XL, MINIIS, ...)
"""
from __future__ import annotations

import re

MODEL_KEYS = ("mk4", "mk4s", "mk39", "mk39s", "coreone", "coreonel", "xl", "mini", "bedslinger", "corexy")

# PrusaLink /api/version "printer" -> model key (Buddy firmware printer type.version.subversion)
PRUSALINK_TYPES = {
    "1.4.0": "mk4",
    "1.4.1": "mk4s",
}

# slicer printer_model strings (upper case, compared after removing spaces/dots) -> model key
_SLICER = [
    (r"^MK4S", "mk4s"),
    (r"^MK4", "mk4"),            # MK4, MK4IS, MK4MMU3, MK4ISMMU3
    (r"^MK39S", "mk39s"),        # MK3.9S
    (r"^MK39", "mk39"),          # MK3.9, MK3.9MMU3
    (r"^COREONEL", "coreonel"),
    (r"^COREONE", "coreone"),
    (r"^XL", "xl"),              # XL, XL2 ... XL5 (tool count), XLIS
    (r"^MINI", "mini"),          # MINI, MINIIS
]


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


def resolve(configured: str | None, job_printer_model: str | None, prusalink_type: str | None) -> tuple[str | None, str]:
    """-> (model key or None, where it came from)."""
    c = (configured or "auto").strip().lower()
    if c != "auto":
        return (c if c in MODEL_KEYS else None), "config"
    k = from_prusalink(prusalink_type)
    if k:
        return k, "printer"
    k = from_slicer(job_printer_model)
    if k:
        return k, "job"
    return None, "unknown"
