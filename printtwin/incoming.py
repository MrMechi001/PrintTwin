"""Job files handed over by the slicer (tools/prusaslicer_hook.py) before they reach the printer.

PrusaSlicer runs post-processing scripts on the finished file - the exact bytes
that are then uploaded (Prusa Connect, PrusaLink) or exported (USB stick). The
hook drops a copy plus a small JSON sidecar here; when that job starts on the
printer, the bridge finds it by size (+ name) instead of downloading it over Wi-Fi.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .config import ROOT

INCOMING = ROOT / "incoming"
KEEP = 40                     # newest hand-overs kept
MAX_AGE_S = 30 * 86400        # older ones are never matched


def _norm(name: str | None) -> str:
    base = os.path.basename(str(name or "")).lower()
    for ext in (".bgcode", ".gcode", ".bgc", ".gco", ".g"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def store(src: Path, output_name: str | None, host: str | None, folder: Path = INCOMING) -> Path:
    """Copy a sliced file into the hand-over folder (used by the PrusaSlicer hook)."""
    folder.mkdir(parents=True, exist_ok=True)
    data = Path(src).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    ext = ".bgcode" if data[:4] == b"GCDE" else ".gcode"
    stem = time.strftime("%Y%m%d-%H%M%S") + "_" + digest[:10]
    dst = folder / (stem + ext)
    tmp = dst.with_name(dst.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dst)
    side = {"file": dst.name, "name": os.path.basename(str(output_name or src)), "output_name": output_name,
            "host": host, "size": len(data), "sha256": digest, "time": time.time()}
    (folder / (stem + ".json")).write_text(json.dumps(side, indent=1), encoding="utf-8")
    prune(folder)
    return dst


def prune(folder: Path = INCOMING, keep: int = KEEP):
    sides = sorted(folder.glob("*.json"))
    for side in sides[:-keep] if len(sides) > keep else []:
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            (folder / meta.get("file", "")).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        side.unlink(missing_ok=True)


def find(size: int | None, name: str | None, folder: Path = INCOMING) -> Path | None:
    """Newest hand-over with exactly this size; one with the same file name wins."""
    if not size or not folder.is_dir():
        return None
    want = _norm(name)
    best, best_rank = None, None
    now = time.time()
    for side in folder.glob("*.json"):
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f = folder / str(meta.get("file", ""))
        if meta.get("size") != size or now - float(meta.get("time", 0)) > MAX_AGE_S or not f.is_file():
            continue
        if f.stat().st_size != size:
            continue
        rank = (bool(want) and _norm(meta.get("name")) == want, float(meta.get("time", 0)))
        if best_rank is None or rank > best_rank:
            best, best_rank = f, rank
    return best


def _total_layers(path: Path) -> int | None:
    import re
    try:
        with open(path, "rb") as f:
            head = f.read(300_000)
    except OSError:
        return None
    m = re.search(rb"; total layer number: (\d+)", head)
    return int(m.group(1)) if m else None


def find_bambu(total_layers: int | None, folder: Path = INCOMING, not_before: float | None = None,
               not_after: float | None = None, exclude=()) -> list[tuple[float, str, Path]]:
    """Bambu jobs handed over by the slicer's post-processing hook that fit: [(time, sha256, file)],
    oldest first.

    The printer reports no file size (it stores a .gcode.3mf, the hook sees the plate G-code) and
    Bambu Studio / OrcaSlicer hand over a temporary file name, so a hand-over fits when it has the
    printer's total layer count ("; total layer number: N") and was sliced between `not_before`
    and `not_after` (a slice made after the print started can't be its file). `exclude`: paths
    already found wrong. Several different sha256 = ambiguous (the caller decides)."""
    if not total_layers or not folder.is_dir():
        return []
    fits = []
    for side in folder.glob("*.json"):
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            t = float(meta.get("time", 0))
        except (OSError, ValueError, TypeError):
            continue
        f = folder / str(meta.get("file", ""))
        if (not_before is not None and t < not_before) or (not_after is not None and t > not_after):
            continue
        if str(f) in exclude or not f.is_file() or _total_layers(f) != total_layers:
            continue
        fits.append((t, str(meta.get("sha256") or f.name), f))
    fits.sort(key=lambda e: e[0])
    return fits


def copy_to(src: Path, dst: Path):
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)
