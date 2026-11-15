"""The slicer hook starts PrintTwin for a sent job, and PrintTwin stops by itself after the print.

usage: python -I tests/test_autostart.py
watch_until_done() with a fake clock; then the real hook start on free local ports with a test
config (a printer that never answers): hidden, detached from the slicer's pipes, only once, and
gone again by itself once no print started within start_within.
"""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from printtwin import config  # noqa: E402
from printtwin.__main__ import watch_until_done  # noqa: E402
import prusaslicer_hook as hook  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail=""):
    global failures
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    failures += not ok


class FakePrinter:
    def __init__(self, states):
        self.states = list(states)

    def snapshot(self):
        return {"state": self.states.pop(0) if len(self.states) > 1 else self.states[0]}


def run_watch(states, linger=600.0, wait=1800.0):
    """Returns the fake time at which stop() was called (5 s polls)."""
    t = [0.0]
    stopped = []
    watch_until_done({"p": FakePrinter(states)}, lambda: stopped.append(t[0]), linger=linger, wait=wait, poll=5.0,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    return stopped[0]


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening(port: int) -> bool:
    return hook._listening(("127.0.0.1", port))


def main():
    # --------------------------------------------------------------- watcher
    check("no print at all: stops after start_within", run_watch([None, "IDLE"]) == 1800.0)
    # printing from 10 s to 3600 s, then FINISHED: stops 600 s after the last busy poll
    states = ["IDLE"] * 2 + ["PRINTING"] * 718 + ["FINISHED"]
    check("print done: stops stop_after later", run_watch(states) == 3600.0 + 600.0, run_watch(states))
    states = ["PRINTING"] * 10 + ["PAUSED"] * 200 + ["ATTENTION"] * 200 + ["PRINTING"] * 10 + [None]
    check("paused / attention count as printing", run_watch(states, linger=60) == 420 * 5.0 + 60)
    check("printer off after the print (no state): stops too", run_watch(["PRINTING", None], linger=30) == 35.0)

    # --------------------------------------------------------------- config
    tmp = Path(tempfile.mkdtemp(prefix="printtwin-autostart-"))
    port, mport = free_port(), free_port(socket.SOCK_DGRAM)
    t = tmp.as_posix()
    base = (f'cache_dir = "{t}/cache"\nincoming_dir = "{t}/incoming"\n\n'
            f'[[printer]]\nid = "t"\nkind = "prusalink"\nhost = "127.0.0.1:9"\napi_key = "test"\n\n'
            f'[server]\nlisten_host = "127.0.0.1"\nport = {port}\n\n'
            f'[metrics]\nlisten_host = "127.0.0.1"\nlisten_port = {mport}\n\n[slicer]\n')
    cfg = tmp / "config.toml"
    cfg.write_text(base, encoding="utf-8")
    c = config.load(cfg)["slicer"]
    check("defaults: autostart on, 10 / 30 min", c["autostart"] is True and c["stop_after"] == 10.0
          and c["start_within"] == 30.0, c)
    off = tmp / "off.toml"
    off.write_text(base + "autostart = false\n", encoding="utf-8")
    log = tmp / "printtwin.log"
    check("a plain export (File) starts nothing", hook.autostart("File", cfg, log) is None)
    check("autostart = false starts nothing", hook.autostart("PrusaConnect", off, log) is None)
    check("no config starts nothing", hook.autostart("PrusaLink", tmp / "missing.toml", log) is None)

    # --------------------------------------------------------------- the real start
    # like PrusaSlicer: run the hook with its output on pipes; it must return while PrintTwin runs on
    cfg.write_text(base + "start_within = 0.5\n", encoding="utf-8")
    code = (f"import sys; sys.path.insert(0, {str(ROOT / 'tools')!r}); import prusaslicer_hook as h; "
            f"from pathlib import Path; p = h.autostart('PrusaConnect', Path({str(cfg)!r}), Path({str(log)!r})); "
            f"print(p.pid if p else 0)")
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    took = time.monotonic() - t0
    pid = int((r.stdout.strip() or "0").splitlines()[-1])
    check("hook returns at once although PrintTwin keeps running", took < 20 and pid > 0, f"{took:.1f} s, {r.stderr}")
    check("PrintTwin answers right after the hook", listening(port))
    check("a second send starts no second PrintTwin", hook.autostart("PrusaLink", cfg, log) is None)
    if os.name == "nt":     # the whole tree: a venv's python.exe is a launcher with the real Python as child
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.monotonic() + 10
    while listening(port) and time.monotonic() < end:
        time.sleep(0.2)

    # stops by itself: no print starts within start_within (3 s)
    cfg.write_text(base + "start_within = 0.05\nstop_after = 0.05\n", encoding="utf-8")
    proc = hook.autostart("PrusaLink", cfg, log)
    check("started again", proc is not None and listening(port))
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    text = log.read_text(encoding="utf-8", errors="replace")
    check("stops by itself when no print starts", rc == 0 and not listening(port), rc)
    check("log says why", "no print started within" in text and "started by the slicer hook" in text, text[-400:])

    for f in sorted(tmp.rglob("*"), reverse=True):
        try:
            f.rmdir() if f.is_dir() else f.unlink()
        except OSError:
            pass
    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
