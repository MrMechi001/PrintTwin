"""Printer control from the 3D view (control.py + server.py): what it does and who may use it.

usage: python -I tests/test_control.py
Starts tools/sim_printer.py with control on and a password (free local ports, no real printer):
pause / resume / stop, the file list, print again, upload (and print); the guards: no control
header, another web site's page, a web site's name pointing here (DNS rebinding) without / with the
password, bad names and paths. Then control off, and a Bambu Lab printer, in-process.
"""
import http.client
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import server  # noqa: E402

JOB = ROOT / "tests" / "fixtures" / "sample.bgcode"
failures = 0


def check(name, ok, detail=""):
    global failures
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    failures += not ok


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(port, method, path, body=None, headers=None, control=True):
    h = {"X-PrintTwin": "control"} if control else {}
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        try:
            return r.status, json.loads(data or b"{}")
        except ValueError:
            return r.status, {}
    finally:
        c.close()


def printer_state(pport):
    c = http.client.HTTPConnection("127.0.0.1", pport, timeout=10)
    c.request("GET", "/api/v1/status", headers={"X-Api-Key": "sim"})
    st = json.loads(c.getresponse().read())
    c.close()
    return st["printer"]["state"]


def moonraker():
    """control.py against a small fake Moonraker: the calls and the multipart upload it sends."""
    import tempfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from printtwin import control
    from printtwin.prusalink import PrusaLink
    calls = []

    class M(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/server/history/list"):
                return self._send({"result": {"jobs": [
                    {"filename": "cube.gcode", "end_time": 200, "exists": True, "metadata": {"size": 10}},
                    {"filename": "old/gone.gcode", "end_time": 150, "exists": False},
                    {"filename": "cube.gcode", "end_time": 100, "exists": True}]}})
            self._send({"error": {"message": "not found"}}, 404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            calls.append((self.path, body))
            self._send({"result": "ok"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), M)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="printtwin-ctl-"))

    class K:
        id, kind, incoming_dir = "voron", "moonraker", tmp / "incoming"
        client = PrusaLink(f"127.0.0.1:{srv.server_port}", "")

    for a in ("pause", "resume", "stop"):
        control.job_action(K, a)
    check("Klipper: pause / resume / stop -> pause, resume, cancel",
          [c[0] for c in calls] == ["/printer/print/pause", "/printer/print/resume", "/printer/print/cancel"], calls)
    fl = control.files(K)
    check("Klipper: history as the file list (once each, only files that still exist)",
          [f["path"] for f in fl] == ["cube.gcode"], fl)
    calls.clear()
    control.print_file(K, "sub dir/cube.gcode")
    check("Klipper: print again", calls and calls[0][0] == "/printer/print/start?filename=sub%20dir%2Fcube.gcode", calls)
    src = tmp / "x.gcode"
    src.write_bytes(b"G28\nG1 X10\n")
    calls.clear()
    control.upload(K, src, "part.gcode", True)
    path, body = calls[0]
    check("Klipper: upload = multipart with the file, print=true, root=gcodes",
          path == "/server/files/upload" and b'filename="part.gcode"' in body and b"G28\nG1 X10\n" in body
          and b'name="print"\r\n\r\ntrue' in body and b'name="root"\r\n\r\ngcodes' in body, body[:300])
    check("... and a copy in the hand-over folder", any((tmp / "incoming").iterdir()))
    srv.shutdown()
    for f in sorted(tmp.rglob("*"), reverse=True):
        f.rmdir() if f.is_dir() else f.unlink()
    tmp.rmdir()


def main():
    port, pport = free_port(), free_port()
    proc = subprocess.Popen([sys.executable, "-I", "-u", str(ROOT / "tools" / "sim_printer.py"), str(JOB),
                             "--kbps", "400", "--heat", "1", "--layer", "0.5", "--port", str(port),
                             "--printer-port", str(pport), "--control", "secret"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                if req(port, "GET", "/api/p/sim/state")[0] == 200:
                    break
            except OSError:
                time.sleep(0.3)
        st, info = req(port, "GET", "/api/p/sim/control")
        check("this PC: control allowed", st == 200 and info.get("enabled") and info.get("allowed"), info)

        api = "/api/p/sim/control"
        check("no X-PrintTwin header: refused", req(port, "POST", api + "/pause", control=False)[0] == 403)
        check("another web site's page (Origin): refused",
              req(port, "POST", api + "/pause", headers={"Origin": "http://evil.example"})[0] == 403)
        st, body = req(port, "POST", api + "/pause", headers={"Host": "evil.example:%d" % port})
        check("a web site's name pointing here (DNS rebinding): password needed", st == 401, (st, body))
        st, _ = req(port, "POST", api + "/pause", headers={"Host": "evil.example:%d" % port,
                                                          "X-PrintTwin-Password": "wrong"})
        check("... wrong password: refused", st == 401)
        check("... nothing paused by any of them", printer_state(pport) == "PRINTING")
        st, info = req(port, "GET", api, headers={"Host": "phone.example", "X-PrintTwin-Password": "secret"})
        check("other device with the password: allowed", info.get("allowed") is True, info)

        st, _ = req(port, "POST", api + "/pause")
        check("pause", st == 200 and printer_state(pport) == "PAUSED")
        check("pause again: refused (nothing to pause)", req(port, "POST", api + "/pause")[0] == 409)
        st, _ = req(port, "POST", api + "/resume", headers={"Host": "phone.example", "X-PrintTwin-Password": "secret"})
        check("resume (other device, password)", st == 200 and printer_state(pport) == "PRINTING")
        st, files = req(port, "GET", api + "/files")
        names = [f["name"] for f in files.get("files", [])]
        check("file list", st == 200 and "sample.bgcode" in names, files)
        path = next(f["path"] for f in files["files"] if f["name"] == "sample.bgcode")
        st, body = req(port, "POST", api + "/print", json.dumps({"path": path}))
        check("print again while printing: refused", st == 409 and "busy" in body.get("error", ""), body)
        check("stop", req(port, "POST", api + "/stop")[0] == 200 and printer_state(pport) == "STOPPED")
        st, body = req(port, "POST", api + "/print", json.dumps({"path": "/usb/../secret.bgcode"}))
        check("path with ..: refused", st == 400, (st, body))
        st, body = req(port, "POST", api + "/print", json.dumps({"path": path}))
        check("print again", st == 200 and printer_state(pport) == "PRINTING", body)

        data = JOB.read_bytes()
        st, body = req(port, "PUT", api + "/upload?name=evil.exe&print=0", data)
        check("upload of a non-G-code name: refused", st == 400, (st, body))
        st, body = req(port, "PUT", api + "/upload?name=part%20two.bgcode&print=0", data)
        check("upload (no print)", st == 200 and body.get("path") == "/usb/part two.bgcode", (st, body))
        sim_usb = ROOT / "logs" / "sim" / "usb" / "part two.bgcode"
        check("... the file is on the printer, unchanged", sim_usb.is_file() and sim_usb.read_bytes() == data)
        st, body = req(port, "PUT", api + "/upload?name=part3.bgcode&print=1", data)
        check("upload and print while printing: refused", st == 409, (st, body))
        req(port, "POST", api + "/stop")
        st, body = req(port, "PUT", api + "/upload?name=part3.bgcode&print=1", data)
        check("upload and print", st == 200 and printer_state(pport) == "PRINTING", (st, body))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # in-process: control off, and a Bambu Lab printer
    class P:
        def __init__(self, pid, kind):
            self.id, self.name, self.kind = pid, pid, kind

    for ctl, label in (({"enabled": False, "password": ""}, "off"), ({"enabled": True, "password": ""}, "on")):
        httpd = server.serve("127.0.0.1", 0, {"mk4": P("mk4", "prusalink"), "p1s": P("p1s", "bambu")},
                             log=lambda *a: None, control=ctl)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        sp = httpd.server_address[1]
        if label == "off":
            st, info = req(sp, "GET", "/api/p/mk4/control")
            check("control off: viewer told so", info.get("enabled") is False and info.get("allowed") is False, info)
            check("control off: requests refused", req(sp, "POST", "/api/p/mk4/control/stop")[0] == 403)
        else:
            st, info = req(sp, "GET", "/api/p/p1s/control")
            check("Bambu Lab printer: not supported", info.get("supported") is False and not info.get("allowed"), info)
            check("Bambu Lab printer: requests refused", req(sp, "POST", "/api/p/p1s/control/stop")[0] == 403)
        httpd.shutdown()
        httpd.server_close()

    moonraker()
    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
