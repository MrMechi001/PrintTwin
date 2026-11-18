"""The printer watch starts PrintTwin when a print begins, whatever profile or way started it.

usage: python -I tests/test_watch.py
printing() against a small fake PrusaLink / Moonraker server; one round of check_once() with a fake
start (PrintTwin is never started here, and the user's own config, log and watch are never used);
on Windows the Startup shortcut, written into a temporary folder.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import printer_watch as watch  # noqa: E402

failures = 0
TMP = Path(tempfile.mkdtemp(prefix="printtwin-watch-"))
watch.LOG = TMP / "watch.log"                     # never the real log / pid file
watch.PID = TMP / "watch.pid"
STATE = {"prusa": "PRINTING", "klipper": "printing"}


def check(name: str, ok: bool, detail=""):
    global failures
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    failures += not ok


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/api/v1/status":
            if self.headers.get("X-Api-Key") != "KEY":
                return self._send(401, {})
            return self._send(200, {"printer": {"state": STATE["prusa"]}})
        if self.path.startswith("/printer/objects/query"):
            return self._send(200, {"result": {"status": {"print_stats": {"state": STATE["klipper"]}}}})
        self._send(404, {})

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def write_cfg(text: str) -> Path:
    p = TMP / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{srv.server_port}"
    prusa = {"id": "mk4", "kind": "prusalink", "host": host, "api_key": "KEY"}
    klipper = {"id": "voron", "kind": "moonraker", "host": host}

    check("PrusaLink PRINTING: printing", watch.printing(prusa) is True)
    for st in ("PAUSED",):
        STATE["prusa"] = st
        check(f"PrusaLink {st}: printing", watch.printing(prusa) is True)
    for st in ("IDLE", "FINISHED", "BUSY", "ATTENTION"):
        STATE["prusa"] = st
        check(f"PrusaLink {st}: not printing", watch.printing(prusa) is False)
    try:
        watch.printing({**prusa, "api_key": "WRONG"})
        check("wrong API key: raises (logged as not reachable)", False)
    except ConnectionError:
        check("wrong API key: raises (logged as not reachable)", True)
    check("Moonraker printing: printing", watch.printing(klipper) is True)
    STATE["klipper"] = "complete"
    check("Moonraker complete: not printing", watch.printing(klipper) is False)

    started, probed = [], []

    class Proc:
        pid = 4242

    def start(host, cfg_path, who=None):
        started.append(who)
        return Proc()

    def probe(pc):
        probed.append(pc["id"])
        if pc.get("host") == "down.invalid":
            raise ConnectionError("no answer")
        return pc["id"] == "mk4"

    base = '[server]\nport = 1\n'
    mk4 = f'[[printer]]\nid = "mk4"\nhost = "{host}"\napi_key = "KEY"\n'
    bambu = '[[printer]]\nid = "p1s"\nkind = "bambu"\nhost = "192.168.1.50"\nserial = "01P00A123"\naccess_code = "1234"\n'
    cfg = write_cfg(base + bambu + mk4)
    said = {}
    r = watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: False)
    check("a print runs, PrintTwin doesn't: started", r == "mk4" and len(started) == 1 and "mk4" in started[0],
          (r, started))
    check("... the Bambu printer isn't asked", probed == ["mk4"], probed)
    check("... and it's logged", "started PrintTwin (pid 4242" in watch.LOG.read_text(encoding="utf-8"))

    started.clear(), probed.clear()
    r = watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: True)
    check("PrintTwin runs already: printers not asked, nothing started", r is None and not probed and not started)

    write_cfg(base + '[slicer]\nautostart = false\n' + mk4)
    for _ in range(3):
        r = watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: False)
    log = watch.LOG.read_text(encoding="utf-8")
    check("autostart = false: nothing asked or started, logged once", r is None and not probed and not started
          and log.count("autostart = false") == 1, log)

    write_cfg(base + mk4.replace(host, "down.invalid"))
    for _ in range(3):
        watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: False)
    log = watch.LOG.read_text(encoding="utf-8")
    check("printer switched off: logged once, not every round", log.count("mk4: not reachable") == 1, log)
    write_cfg(base + mk4.replace('host = "' + host, 'host = "' + host))
    watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: False)
    check("... and when it answers again", "mk4: answers again" in watch.LOG.read_text(encoding="utf-8"))

    write_cfg('[[printer]]\nid = "mk4"\nserial = 01P00A123\n')            # no quotes: not valid TOML
    try:
        r = watch.check_once(cfg, said, probe=probe, start=start, listening=lambda a: False)
        check("broken config: no crash, logged", r is None and "can't be read" in watch.LOG.read_text(encoding="utf-8"))
    except Exception as e:
        check("broken config: no crash, logged", False, repr(e))

    if os.name == "nt":
        folder = TMP / "Startup"
        folder.mkdir()
        link = watch.install(cfg, folder=folder, now=False)
        check("install: shortcut in the Startup folder", link.is_file() and link.name == watch.LINK_NAME)
        done = watch.uninstall(folder=folder)
        check("uninstall: shortcut removed", not link.exists() and any("removed" in d for d in done), done)
    srv.shutdown()

    for f in sorted(TMP.rglob("*"), reverse=True):
        f.rmdir() if f.is_dir() else f.unlink()
    TMP.rmdir()
    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
