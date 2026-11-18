"""Printer watch: start PrintTwin when a print begins - however it was started (any slicer profile,
Prusa Connect, PrusaLink, a USB stick, the printer's screen).

Runs in the background without a window and asks each Prusa (PrusaLink) and Klipper (Moonraker)
printer every 20 s whether it is printing. When one is and PrintTwin isn't running, it starts
PrintTwin the way the slicer hook does: hidden, and stopping by itself 10 min after the print
(config.local.toml, [slicer]: autostart = false turns both off; stop_after / start_within).
While PrintTwin runs (PrintTwin.bat or started by this), the printers aren't asked at all.
Bambu Lab printers aren't asked (they allow only a few connections): start PrintTwin yourself, or
use the slicer hook.

usage:
  python tools/printer_watch.py               watch (Ctrl+C stops)
  python tools/printer_watch.py --install     Windows: start the watch with Windows (and now)
  python tools/printer_watch.py --uninstall   Windows: no longer start it, stop the running one
  [--config file.toml]                        another config than config.local.toml
Log: logs/watch.log (only what happens: starts, problems).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from printtwin import config  # noqa: E402
from printtwin.prusalink import PrusaLink  # noqa: E402
import prusaslicer_hook as hook  # noqa: E402

EVERY = 20.0                                   # seconds between two questions to the printers
ACTIVE = {"PRINTING", "PAUSED"}                # PrusaLink states of a running print
LOG = ROOT / "logs" / "watch.log"
PID = ROOT / "logs" / "watch.pid"
LINK_NAME = "PrintTwin printer watch.lnk"


def log(msg: str, path: Path | None = None):
    path = path or LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > (1 << 20):
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def printing(pc: dict, timeout: float = 5.0) -> bool:
    """Is this printer printing (or paused in a print)? Raises if it can't be asked."""
    key = str(pc.get("api_key") or "").strip() if config.filled(pc.get("api_key")) else ""
    user = str(pc.get("user") or "").strip() if config.filled(pc.get("user")) else ""
    password = str(pc.get("password") or "") if config.filled(pc.get("password")) else ""
    client = PrusaLink(pc["host"], key, timeout=timeout, user=user or None, password=password or None)
    if pc["kind"] == "moonraker":
        code, data = client.get_json("/printer/objects/query?print_stats=state")
        st = (((data or {}).get("result") or {}).get("status") or {}).get("print_stats") or {}
        if code != 200:
            raise ConnectionError(f"HTTP {code}")
        return st.get("state") in ("printing", "paused")
    code, data = client.get_json("/api/v1/status")
    if code != 200 or not isinstance(data, dict):
        raise ConnectionError(f"HTTP {code}")
    return str((data.get("printer") or {}).get("state") or "").upper() in ACTIVE


def check_once(cfg_path: Path | None, said: dict, probe=printing, start=hook.autostart,
               listening=hook._listening) -> str | None:
    """One round: returns the id of the printer PrintTwin was started for (None: nothing started).
    said: what was logged already (a problem is logged when it starts, not every 20 s)."""
    def state(key, msg=None, fine=None):            # msg: the problem now (None: none)
        if said.get(key) != msg:
            if msg or (fine and said.get(key)):
                log(msg or fine)
            said[key] = msg
    try:
        cfg = config.load(cfg_path)                 # read every round: changes apply without a restart
    except (OSError, ValueError) as e:
        state("config", f"config can't be read: {e}")
        return None
    if not cfg["slicer"]["autostart"]:
        state("config", "autostart = false in [slicer] - not watching")
        return None
    s = cfg["server"]
    addr = ("127.0.0.1" if s["listen_host"] in ("", "0.0.0.0", "::") else s["listen_host"], int(s["port"]))
    if listening(addr):                             # PrintTwin runs: it watches the printers itself
        return None
    notes, problems = config.prepare_printers(cfg)
    state("config", "config incomplete: " + "; ".join(problems) if problems else None, "config fine again")
    for pc in cfg["printer"]:
        if pc["kind"] not in ("prusalink", "moonraker") or not config.filled(pc.get("host")):
            continue
        try:
            busy = probe(pc)
            state(pc["id"], None, f"{pc['id']}: answers again")
        except Exception as e:
            state(pc["id"], f"{pc['id']}: not reachable ({e.__class__.__name__}: {e})")
            continue
        if busy:
            proc = start("printer watch", cfg_path, who=f"the printer watch ({pc['id']} is printing)")
            if proc is not None:
                log(f"{pc['id']} is printing - started PrintTwin (pid {proc.pid}, log: logs/printtwin.log)")
                return pc["id"]
            return None
    return None


def watch(cfg_path: Path | None = None, every: float = EVERY):
    if not _single():
        return                                      # one watch is enough
    try:
        PID.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    log(f"watching the printers every {every:g} s (pid {os.getpid()})")
    said: dict = {}
    while True:
        try:
            check_once(cfg_path, said)
        except Exception:
            tb = traceback.format_exc()
            if said.get("crash") != tb:
                said["crash"] = tb
                log("FAILED\n" + tb)
        time.sleep(every)


_mutex = None


def _single() -> bool:
    """True for the only watch running (Windows: a named mutex; elsewhere: a lock file)."""
    global _mutex
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _mutex = k32.CreateMutexW(None, False, "Local\\PrintTwinPrinterWatch")
        return bool(_mutex) and ctypes.get_last_error() != 183      # ERROR_ALREADY_EXISTS
    import fcntl
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _mutex = open(LOG.parent / "watch.lock", "w")
    try:
        fcntl.flock(_mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ Windows: start with Windows
def startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _args(cfg_path: Path | None) -> list[str]:
    return [str(Path(__file__).resolve())] + (["--config", str(Path(cfg_path).resolve())] if cfg_path else [])


def start_hidden(cfg_path: Path | None = None) -> subprocess.Popen:
    """The watch as its own hidden process (no window), detached from this one."""
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    cmd = [sys.executable, "-u"] + _args(cfg_path)
    try:
        return subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        return subprocess.Popen(cmd, creationflags=flags, **kw)


def _pythonw() -> str | None:
    """pythonw.exe (no window at all) - only if it works: a venv's pythonw needs one in its base Python
    (Blender's Python, for one, has none)."""
    w = Path(sys.executable).with_name("pythonw.exe")
    base = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    return str(w) if w.is_file() and base.is_file() else None


def install(cfg_path: Path | None = None, folder: Path | None = None, now: bool = True) -> Path:
    """A shortcut in the Startup folder: pythonw.exe <this>, or python.exe <this> --background
    (minimized for a moment, then it goes on hidden)."""
    link = (folder or startup_dir()) / LINK_NAME
    target = _pythonw() or sys.executable
    args = " ".join(f'"{a}"' for a in _args(cfg_path) + ([] if _pythonw() else ["--background"]))
    env = {**os.environ, "PT_LINK": str(link), "PT_TARGET": target, "PT_ARGS": args, "PT_DIR": str(ROOT)}
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PT_LINK); $s.TargetPath = $env:PT_TARGET; "
          "$s.Arguments = $env:PT_ARGS; $s.WorkingDirectory = $env:PT_DIR; $s.WindowStyle = 7; "
          "$s.Description = 'PrintTwin: starts PrintTwin when a print begins'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   capture_output=True, timeout=60)
    if now:
        start_hidden(cfg_path)
    return link


def uninstall(folder: Path | None = None) -> list[str]:
    done = []
    link = (folder or startup_dir()) / LINK_NAME
    if link.is_file():
        link.unlink()
        done.append(f"removed {link}")
    try:
        pid = int(PID.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = None
    if pid:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True).stdout.lower()
        if "python" in out:                         # still the watch (not a reused process id)
            # no /T: a PrintTwin the watch started keeps running until its print is done
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            done.append(f"stopped the running watch (pid {pid})")
        PID.unlink(missing_ok=True)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(prog="printer_watch", description="Start PrintTwin when a print begins.")
    ap.add_argument("--config", type=Path, help="config file (default: config.local.toml)")
    ap.add_argument("--install", action="store_true", help="Windows: start the watch with Windows, and now")
    ap.add_argument("--uninstall", action="store_true", help="Windows: don't start it any more, stop it")
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)   # the Startup shortcut
    args = ap.parse_args(argv)
    if (args.install or args.uninstall or args.background) and os.name != "nt":
        sys.exit("--install / --uninstall are for Windows; elsewhere start this script with your "
                 "system's autostart (e.g. a systemd user service)")
    if args.uninstall:
        print("\n".join(uninstall()) or "the printer watch wasn't installed")
    elif args.install:
        link = install(args.config)
        print(f"installed: {link}\nThe printer watch runs now and starts with Windows (log: {LOG}).")
    elif args.background:
        start_hidden(args.config)
    else:
        try:
            watch(args.config)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
