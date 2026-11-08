"""PrusaSlicer post-processing hook: hand PrintTwin a copy of every sliced job.

PrusaSlicer passes the finished file (the exact .bgcode/.gcode bytes it then
uploads to Prusa Connect / PrusaLink or writes to the USB stick) to every
post-processing script. This one only COPIES it to <PrintTwin folder>/incoming and
never changes it. When the job starts printing, the PrintTwin bridge finds the
copy by size + name and shows the full model at once instead of downloading the
file from the printer over Wi-Fi.

PrusaSlicer > Print Settings > Output options > Post-processing scripts, one line:
    "C:\\path\\to\\python.exe" "C:\\path\\to\\PrintTwin\\tools\\prusaslicer_hook.py"
(full paths; quotes if they contain spaces; macOS/Linux: /usr/bin/python3 /path/to/...).
Bambu Studio / OrcaSlicer: Process > Others > Post-processing scripts (advanced mode), same line.
They hand over the plate G-code before packing it into the .gcode.3mf they send; for Bambu
printers PrintTwin then finds the copy by its layer count, name and age instead of its size.
Any Python 3.11+ works; prefer pythonw.exe on Windows if your Python has it (no console flash).

Never blocks or breaks an export: any problem is logged to logs\\hook.log and
the script still exits 0.
"""
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        src = Path(sys.argv[-1])
        if len(sys.argv) < 2 or not src.is_file():
            raise FileNotFoundError(f"no job file in {sys.argv!r}")
        sys.path.insert(0, str(ROOT))
        from printtwin import incoming
        dst = incoming.store(src, os.environ.get("SLIC3R_PP_OUTPUT_NAME"), os.environ.get("SLIC3R_PP_HOST"))
        _log(f"stored {dst.name} ({dst.stat().st_size} bytes) for {os.environ.get('SLIC3R_PP_HOST')}: "
             f"{os.path.basename(os.environ.get('SLIC3R_PP_OUTPUT_NAME') or '')}")
    except Exception:
        _log("FAILED\n" + traceback.format_exc())
    return 0


def _log(msg: str):
    try:
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        with open(logs / "hook.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
