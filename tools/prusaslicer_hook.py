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

When the slicer SENDS the job to a printer (Prusa Connect, PrusaLink, OctoPrint, ... - not a
plain export to a file) and PrintTwin isn't running, the hook starts it in the background (no
window, log in logs\\printtwin.log); that PrintTwin stops by itself 10 min after the print
(config.local.toml, [slicer]: autostart = false turns this off; stop_after / start_within = minutes).

Never blocks or breaks an export: any problem is logged to logs\\hook.log and
the script still exits 0.
"""
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        src = Path(sys.argv[-1])
        if len(sys.argv) < 2 or not src.is_file():
            raise FileNotFoundError(f"no job file in {sys.argv!r}")
        from printtwin import incoming
        dst = incoming.store(src, os.environ.get("SLIC3R_PP_OUTPUT_NAME"), os.environ.get("SLIC3R_PP_HOST"))
        _log(f"stored {dst.name} ({dst.stat().st_size} bytes) for {os.environ.get('SLIC3R_PP_HOST')}: "
             f"{os.path.basename(os.environ.get('SLIC3R_PP_OUTPUT_NAME') or '')}")
    except Exception:
        _log("FAILED\n" + traceback.format_exc())
    host = os.environ.get("SLIC3R_PP_HOST")
    try:
        proc = autostart(host)
        if proc is not None:
            _log(f"started PrintTwin in the background for {host} (pid {proc.pid}, log: logs/printtwin.log)")
    except Exception:
        _log("autostart FAILED\n" + traceback.format_exc())
    return 0


def _listening(addr: tuple) -> bool:
    try:
        with socket.create_connection(addr, timeout=0.5):
            return True
    except OSError:
        return False


def autostart(host: str | None, cfg_path: Path | None = None, log_path: Path | None = None, wait: float = 15.0,
              who: str | None = None):
    """The slicer sent the job to a printer (any host but "File", a plain export): start PrintTwin
    hidden unless it runs already - with --until-done, so it stops by itself after the print.
    Waits until it answers (the slicer may upload to it right after this script). Returns the
    process, None if nothing was started."""
    if not host or host == "File":
        return None
    from printtwin import config
    try:
        cfg = config.load(cfg_path)
    except (OSError, ValueError):               # no config yet / not valid TOML
        return None
    if not cfg["slicer"]["autostart"] or not cfg.get("printer"):
        return None
    s = cfg["server"]
    addr = ("127.0.0.1" if s["listen_host"] in ("", "0.0.0.0", "::") else s["listen_host"], int(s["port"]))
    if _listening(addr):                        # running already (PrintTwin.bat or an earlier job)
        return None
    # python.exe with a hidden console (pythonw.exe isn't in every Python, e.g. Blender's)
    cmd = [sys.executable, "-u", str(ROOT / "run.py"), "--until-done"] + (["--config", str(cfg_path)] if cfg_path else [])
    log_path = log_path or ROOT / "logs" / "printtwin.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    big = log_path.is_file() and log_path.stat().st_size > (5 << 20)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # own stdin/stdout/stderr and no other inherited handles: the slicer waits for the end of the
    # script's output, which must not wait for PrintTwin
    with open(log_path, "w" if big else "a", encoding="utf-8") as out:
        out.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} started by {who or f'the slicer hook ({host})'}\n")
        out.flush()
        kw = dict(cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                  close_fds=True)
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:                                # outlive the slicer, even if it runs scripts in a job object
                proc = subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
            except OSError:                     # that job doesn't allow breaking away
                proc = subprocess.Popen(cmd, creationflags=flags, **kw)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    end = time.monotonic() + wait
    while time.monotonic() < end and proc.poll() is None and not _listening(addr):
        time.sleep(0.2)
    return proc


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
