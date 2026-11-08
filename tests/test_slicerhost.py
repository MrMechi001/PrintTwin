"""PrintTwin as the slicer's print host (printtwin/slicerhost.py): calls pass through to the printer.

usage: python -I tests/test_slicerhost.py
A fake PrusaLink printer and PrintTwin's HTTP server on free local ports; the requests are the
ones PrusaSlicer / OrcaSlicer send (connection test, storage list, PUT upload with
Expect: 100-continue and Print-After-Upload, the older multipart POST).
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import incoming, slicerhost  # noqa: E402
from printtwin.server import serve  # noqa: E402

JOB = ROOT / "tests" / "fixtures" / "sample.bgcode"
KEY = "test-key-123"
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


seen = []          # (method, path, headers, body) the fake printer received
copy_seen = []     # PUT: did PrintTwin's hand-over copy exist when the printer got the file?
IN = None          # PrintTwin's hand-over folder (set in main)


class FakePrinter(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:          # PrintTwin gave up on this request (the tests that hang up)
            pass

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if len(body) < n:        # the sender hung up mid-upload
            return
        seen.append((self.command, self.path, dict(self.headers), body))
        if self.headers.get("X-Api-Key") != KEY:
            return self._reply(401, {"title": "Unauthorized"})
        if self.command == "GET" and self.path == "/api/version":
            return self._reply(200, {"api": "2.0.0", "text": "PrusaLink", "capabilities": {"upload-by-put": True}})
        if self.command == "GET" and self.path == "/api/v1/storage":
            return self._reply(200, {"storage_list": [{"path": "/usb", "available": True}]})
        if self.command in ("PUT", "POST"):
            copy_seen.append(bool(incoming.find(n, os.path.basename(unquote(self.path)), IN))
                             if self.command == "PUT" else None)
            return self._reply(201)
        return self._reply(404, {"title": "Not Found"})

    do_GET = do_PUT = do_POST = _handle


class Stub:
    """The part of a printer the pass-through uses."""
    def __init__(self, pid, host, folder, kind="prusalink"):
        self.id, self.name, self.kind, self.host, self.incoming_dir = pid, pid, kind, host, folder
        self.http_prefix = ""

    @property
    def http_netloc(self):
        return self.host


def request(port, method, path, body=b"", headers=None, expect=False):
    """Raw HTTP/1.1 like curl: with expect=True it waits for '100 Continue' before the body."""
    hdr = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(body)), **(headers or {})}
    if expect:
        hdr["Expect"] = "100-continue"
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall((f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdr.items())
                   + "\r\n").encode())
        t0 = time.time()
        if expect and body:
            s.settimeout(0.8)                      # curl waits 1 s for it, then sends anyway
            try:
                first = s.recv(64)
            except socket.timeout:
                first = b""
            s.settimeout(10)
            if not first.startswith(b"HTTP/1.1 100"):
                return None, {}, b"", time.time() - t0
        s.sendall(body)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    hdrs = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return int(lines[0].split()[1]), hdrs, rest, time.time() - t0


def raw_printer(mode):
    """A printer that misbehaves on uploads, on a raw socket: 'early401' answers right after the
    headers without reading the body, 'drop' reads a little and hangs up without an answer,
    'busy' reads everything and answers 409 (stored, but can't print now)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                c.settimeout(10)
                head = b""
                while b"\r\n\r\n" not in head:
                    d = c.recv(65536)
                    if not d:
                        break
                    head += d
                n = int(next((ln.split(b":")[1] for ln in head.split(b"\r\n")
                              if ln.lower().startswith(b"content-length:")), b"0"))
                got = len(head.split(b"\r\n\r\n", 1)[1]) if b"\r\n\r\n" in head else 0
                if mode == "early401":
                    msg = b'{"title":"Unauthorized"}'
                    c.sendall(b"HTTP/1.0 401 Unauthorized\r\nContent-Type: application/json\r\nContent-Length: "
                              + str(len(msg)).encode() + b"\r\n\r\n" + msg)
                    c.shutdown(socket.SHUT_WR)
                    time.sleep(0.3)
                    continue
                while got < n and (mode != "drop" or got < 100000):
                    d = c.recv(65536)
                    if not d:
                        break
                    got += len(d)
                if mode == "drop":
                    continue
                msg = b'{"title":"Conflict","message":"Printer is busy"}'
                c.sendall(b"HTTP/1.0 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: "
                          + str(len(msg)).encode() + b"\r\n\r\n" + msg)

    threading.Thread(target=serve, daemon=True).start()
    return srv, f"127.0.0.1:{srv.getsockname()[1]}"


def main():
    global IN
    tmp = Path(tempfile.mkdtemp(prefix="pt_slicer_"))
    IN = tmp / "in"
    pport, port_on, port_off = free_port(), free_port(), free_port()
    fake = ThreadingHTTPServer(("127.0.0.1", pport), FakePrinter)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    printers = {"mk4": Stub("mk4", f"127.0.0.1:{pport}", tmp / "in")}
    quiet = lambda *a: None  # noqa: E731
    for port, on in ((port_on, True), (port_off, False)):
        srv = serve("127.0.0.1", port, printers, slicer=on, log=quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    auth = {"X-Api-Key": KEY}

    # the printer tab: /p/<id> shows the viewer for that printer
    c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=5)
    c.request("GET", "/p/mk4")
    r = c.getresponse()
    check("/p/mk4 opens the viewer for mk4", r.status == 302 and r.getheader("Location") == "/?p=mk4",
          f"{r.status} {r.getheader('Location')}")
    c.close()

    # off unless [slicer] print_host = true
    code, _, body, _ = request(port_off, "GET", "/p/mk4/api/version", headers=auth)
    check("print host off: 403 with a hint", code == 403 and b"print_host" in body, f"{code}")
    check("print host off: nothing reaches the printer", not seen)

    # connection test + storage, with and without /p/<id>
    for path in ("/p/mk4/api/version", "/api/version"):
        code, _, body, _ = request(port_on, "GET", path, headers=auth)
        ok = code == 200 and json.loads(body).get("text") == "PrusaLink"
        check(f"GET {path} is the printer's answer", ok, f"{code} {body[:60]!r}")
    check("the slicer's API key reaches the printer", seen and seen[-1][2].get("X-Api-Key") == KEY)
    check("the printer sees its own path", seen and seen[-1][1] == "/api/version", seen and seen[-1][1])
    code, _, body, _ = request(port_on, "GET", "/p/mk4/api/v1/storage", headers=auth)
    check("GET storage passes through", code == 200 and b"storage_list" in body, f"{code}")
    code, _, _, _ = request(port_on, "GET", "/p/mk4/api/version", headers={"X-Api-Key": "wrong"})
    check("a wrong key gets the printer's 401", code == 401, f"{code}")
    code, _, _, _ = request(port_on, "GET", "/p/nope/api/version", headers=auth)
    check("unknown printer id: 404", code == 404, f"{code}")

    # upload and print (PrusaLink PUT), the way curl sends it
    data = JOB.read_bytes()
    name = "Shape-Box_0.4n_0.2mm_PLA_MK4IS_9m.bgcode"
    hdr = {**auth, "Content-Type": "text/x.gcode", "Overwrite": "?1", "Print-After-Upload": "?1"}
    code, _, _, secs = request(port_on, "PUT", f"/p/mk4/api/v1/files/usb/{name}", data, hdr, expect=True)
    check("PUT upload: '100 Continue' at once, then the printer's 201", code == 201 and secs < 0.5,
          f"{code}, {secs:.2f} s")
    m, path, h, body = seen[-1]
    check("the printer got the whole file", m == "PUT" and body == data, f"{m} {len(body)} of {len(data)}")
    check("upload headers go through", h.get("Print-After-Upload") == "?1" and h.get("Overwrite") == "?1"
          and h.get("X-Api-Key") == KEY and "Expect" not in h)
    hit = incoming.find(len(data), name, tmp / "in")
    check("PrintTwin kept a copy for the 3D view", hit is not None and hit.read_bytes() == data, str(hit))
    check("no temp files left", not list((tmp / "in").glob("upload_*")))
    check("the copy is kept before the printer answers (job starts at once)", copy_seen[-1:] == [True],
          str(copy_seen))

    # a rejected upload is not kept
    n_before = len(list((tmp / "in").glob("*.json")))
    code, _, _, _ = request(port_on, "PUT", "/p/mk4/api/v1/files/usb/other.bgcode", data[:5000],
                            {"X-Api-Key": "wrong"}, expect=True)
    check("rejected upload: printer's 401, no copy", code == 401
          and len(list((tmp / "in").glob("*.json"))) == n_before, f"{code}")

    # older OctoPrint-style multipart upload
    b = "----pt" + "x" * 20
    gcode = b"G28\nG1 X10 Y10 Z0.2 E1 F600\n" * 50
    mp = (f"--{b}\r\nContent-Disposition: form-data; name=\"print\"\r\n\r\ntrue\r\n"
          f"--{b}\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n\r\n"
          f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"cube.gcode\"\r\n"
          f"Content-Type: application/octet-stream\r\n\r\n").encode() + gcode + f"\r\n--{b}--\r\n".encode()
    code, _, _, _ = request(port_on, "POST", "/p/mk4/api/files/local", mp,
                            {**auth, "Content-Type": f"multipart/form-data; boundary={b}"}, expect=True)
    hit = incoming.find(len(gcode), "cube.gcode", tmp / "in")
    check("multipart upload: passed on and the file part kept", code == 201 and hit is not None
          and hit.read_bytes() == gcode, f"{code} {hit}")

    # bad requests from anyone on the network
    code, _, _, _ = request(port_on, "PUT", "/p/mk4/api/v1/files/usb/x.bgcode", b"", {**auth, "Content-Length": "-5"})
    check("negative Content-Length: 400, nothing passed on", code == 400 and seen[-1][1] != "/api/v1/files/usb/x.bgcode",
          f"{code}")
    code, _, _, _ = request(port_on, "PUT", "/p/mk4/api/v1/files/usb/x.bgcode", b"12345",
                            {**auth, "Content-Length": "+5"})
    check("Content-Length with a sign: 400", code == 400, f"{code}")
    code, _, body, _ = request(port_on, "GET", "/p/mk4/api/version",
                               headers={"Authorization": 'Digest username="maker", uri="/p/mk4/api/version"'})
    check("digest login via /p/<id>: a clear 400 instead of the printer's bare 401", code == 400
          and b"API key" in body, f"{code} {body[:60]!r}")
    code, _, _, _ = request(port_on, "GET", "/p/mk4/api/v1/../../secret", headers=auth)
    check("dot segments in the path: 400", code == 400, f"{code}")
    with socket.create_connection(("127.0.0.1", port_on), timeout=10) as s:     # slicer hangs up mid-upload
        s.sendall(f"PUT /p/mk4/api/v1/files/usb/cut.bgcode HTTP/1.1\r\nHost: x\r\nX-Api-Key: {KEY}\r\n"
                  f"Content-Length: 100000\r\n\r\n".encode() + data[:20000])
    time.sleep(0.5)
    check("slicer hangs up mid-upload: no copy, no temp file", not incoming.find(100000, "cut.bgcode", IN)
          and not list(IN.glob("upload_*")))
    slicerhost.TIMEOUT_S = 1.5                          # an idle client must not hold the printer for long
    t0 = time.time()
    with socket.create_connection(("127.0.0.1", port_on), timeout=10) as s:
        s.sendall(f"PUT /p/mk4/api/v1/files/usb/idle.bgcode HTTP/1.1\r\nHost: x\r\nX-Api-Key: {KEY}\r\n"
                  f"Content-Length: 1000\r\n\r\n".encode())
        s.settimeout(10)
        reply = s.recv(200)
    check("idle client: dropped after the timeout", time.time() - t0 < 5, f"{time.time() - t0:.1f} s, {reply[:30]!r}")
    slicerhost.TIMEOUT_S = 60.0
    code, _, _, _ = request(port_on, "GET", "/p/mk4/api/version", headers=auth)
    check("and the printer is free again afterwards", code == 200, f"{code}")


    # the printer answers early / hangs up / is busy - the slicer must get a proper answer at once
    big = data * 60                                         # ~3 MB, more than the socket buffers hold
    good_host = printers["mk4"].host
    for mode in ("early401", "drop", "busy"):
        srv, host = raw_printer(mode)
        printers["mk4"].host = host
        name = f"{mode}.bgcode"
        t0 = time.time()
        code, _, body, _ = request(port_on, "PUT", f"/p/mk4/api/v1/files/usb/{name}", big,
                                   {**auth, "Print-After-Upload": "?1"}, expect=True)
        took = time.time() - t0
        srv.close()
        hit = incoming.find(len(big), name, IN)
        if mode == "early401":
            check("printer refuses at once: the slicer gets its 401, no copy", code == 401 and b"Unauthorized" in body
                  and hit is None and took < 10, f"{code} in {took:.1f} s")
        elif mode == "drop":
            check("printer hangs up mid-upload: 502 at once, no copy", code == 502 and hit is None and took < 10,
                  f"{code} in {took:.1f} s")
        else:
            check("printer busy (409): stored but not printed - its answer, copy kept", code == 409
                  and hit is not None, f"{code}, copy {hit}")
        check(f"{mode}: no temp files left", not list(IN.glob("upload_*")))
    printers["mk4"].host = good_host

    # Klipper printers: only LAN clients without a key (Moonraker trusts LAN addresses by default)
    for ip, want in [("127.0.0.1", True), ("192.168.1.40", True), ("10.1.2.3", True), ("172.20.0.5", True),
                     ("fe80::1", True), ("::ffff:192.168.1.9", True), ("100.100.10.20", False), ("8.8.8.8", False),
                     ("2001:db8::1", True), ("2a00:1450:4001::1", False), ("bogus", False)]:
        check(f"LAN client {ip}: {want}", slicerhost.lan_client(ip) is want)
    check("Moonraker: upload passes, G-code does not", slicerhost.allowed("moonraker", "POST", "/server/files/upload")
          and not slicerhost.allowed("moonraker", "POST", "/printer/gcode/script")
          and not slicerhost.allowed("moonraker", "GET", "/server/files/gcodes/x.gcode"))

    # printer switched off
    printers["mk4"].host = f"127.0.0.1:{free_port()}"
    code, _, body, _ = request(port_on, "GET", "/p/mk4/api/version", headers=auth)
    check("printer not reachable: 502 with the reason", code == 502 and b"not reachable" in body, f"{code}")

    # PrintTwin's own API is untouched
    c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=5)
    c.request("GET", "/api/printers")
    r = c.getresponse()
    check("PrintTwin's own /api/printers still works", r.status == 200 and json.loads(r.read())[0]["id"] == "mk4")
    c.close()
    fake.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
