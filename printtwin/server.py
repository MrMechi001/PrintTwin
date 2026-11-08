"""HTTP + Server-Sent-Events server for the viewer (stdlib only)."""
from __future__ import annotations

import gzip
import ipaddress
import json
import mimetypes
import os
import queue
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, slicerhost
from .config import ROOT

WEB = ROOT / "web"


def local_urls(printers: dict, port: int) -> list[dict]:
    """Addresses other devices can use: the LAN interface that routes to the
    printer first, then Tailscale (100.64.0.0/10) and other private IPv4s."""
    ips: list[str] = []
    for p in printers.values():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((p.host, 80))           # no packet is sent for UDP connect
                ips.append(s.getsockname()[0])
        except OSError:
            pass
    try:
        ips += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    out, seen = [], set()
    for ip in ips:
        if ip in seen or ip.startswith(("127.", "169.254.")):
            continue
        seen.add(ip)
        a = ipaddress.ip_address(ip)
        if a in ipaddress.ip_network("100.64.0.0/10"):
            kind = "tailscale"
        elif a.is_private:
            kind = "lan"
        else:
            continue
        out.append({"kind": kind, "url": f"http://{ip}:{port}/"})
    return out


def make_handler(printers: dict, port: int, slicer: bool = False, log=print):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"PrintTwin/{__version__}"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def do_GET(self):
            u = urlsplit(self.path)
            try:
                if self._slicer(u):
                    return
                if u.path.startswith("/api/"):
                    return self._api(u.path[5:].strip("/").split("/"), parse_qs(u.query))
                return self._static(u.path)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _other(self):
            """PUT / POST / DELETE / HEAD / PATCH: only the slicer's print-host calls."""
            try:
                if not self._slicer(urlsplit(self.path)):
                    if self.command == "HEAD":
                        return self.do_GET()
                    self._json({"error": "method not allowed"}, 405)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        do_PUT = do_POST = do_DELETE = do_HEAD = do_PATCH = _other

        def _slicer(self, u) -> bool:
            """Print-host routes for PrusaSlicer / OrcaSlicer (see slicerhost.py); True if handled.
            /p/<id>            the viewer for that printer (what the slicer's printer tab shows)
            /p/<id>/api/...    passed on to the printer (connection test, storage, upload)
            /api/version, /api/v1/..., /api/files...  the same without /p/<id> (one printer only)"""
            m = re.match(r"^/p/([^/]+)(/.*)?$", u.path)
            if m:
                p = printers.get(unquote(m.group(1)))
                rest = m.group(2) or ""
                if p is None:
                    self._json({"error": f"no printer {unquote(m.group(1))!r} in the config"}, 404)
                elif rest in ("", "/", "/index.html") and self.command in ("GET", "HEAD"):
                    self._send(302, b"", "text/plain", {"Location": f"/?p={quote(p.id)}"})
                elif slicerhost.is_printer_api(rest) or rest.startswith("/api/"):   # (403 unless allowed)
                    if p.kind != "bambu" and (self.headers.get("Authorization") or "").lower().startswith("digest"):
                        # the digest hash covers the URL the slicer asked for (/p/<id>/api/...), the
                        # printer checks it against its own (/api/...): it can never match here
                        self._json({"error": "HTTP digest login does not work through /p/<printer id>: in the "
                                             "slicer set Authorization Type = API key and enter the printer's "
                                             "PrusaLink password / API key (with one printer, "
                                             "http://<this PC>:%d works with digest too)" % port}, 400)
                    else:
                        self._pass(p, rest + (f"?{u.query}" if u.query else ""))
                else:
                    self._json({"error": "not found"}, 404)
                return True
            if not slicerhost.is_printer_api(u.path):
                return False
            if len(printers) != 1:
                self._json({"error": "several printers: use http://<this PC>:%d/p/<printer id> as the "
                                     "slicer's print host" % port}, 404)
            else:
                self._pass(next(iter(printers.values())), u.path + (f"?{u.query}" if u.query else ""))
            return True

        def _pass(self, p, path_qs: str):
            if p.kind == "bambu":
                return self._json({"error": "Bambu Lab printers have no print host in PrintTwin: send the job from "
                                            "Bambu Studio / OrcaSlicer to the printer itself (the slicer hand-over "
                                            f"gives PrintTwin a copy); the 3D view is at /p/{p.id}"}, 403)
            if not slicer:
                return self._json({"error": "PrintTwin is not set up as print host - add  [slicer]  "
                                            "print_host = true  to config.local.toml and restart PrintTwin"}, 403)
            if not slicerhost.allowed(p.kind, self.command, urlsplit(path_qs).path):
                return self._json({"error": f"PrintTwin passes on only the slicer's calls (connection test, "
                                            f"upload, print) to {p.kind} printers, not {self.command} "
                                            f"{urlsplit(path_qs).path}"}, 403)
            slicerhost.forward(self, p, path_qs, log)

        def _api(self, parts: list[str], query: dict):
            if parts == ["printers"]:
                return self._json([{"id": p.id, "name": p.name, "kind": p.kind} for p in printers.values()])
            if parts == ["info"]:
                return self._json({"urls": local_urls(printers, port)})
            if parts == ["models"]:          # printer models the viewer can load (web/models/*.glb)
                return self._json({"glb": sorted(f.name for f in (WEB / "models").glob("*.glb"))})
            if len(parts) == 3 and parts[0] == "p" and parts[1] in printers:
                p = printers[parts[1]]
                what = parts[2]
                if what == "state":
                    return self._json(p.snapshot())
                if what == "toolpath.json":
                    info = p.toolpath_info()
                    return self._json(info) if info else self._json({"error": "no toolpath"}, 404)
                if what == "toolpath.bin":
                    # ?from=K: only segments K.. (the viewer appends them while a job file is arriving)
                    try:
                        start = max(0, int((query.get("from") or ["0"])[0]))
                    except ValueError:
                        start = 0
                    r = p.toolpath_bin(start)
                    if r is None:
                        return self._json({"error": "no toolpath"}, 404)
                    key, count, data, is_gz = r
                    hdr = {"X-Toolpath-Key": key, "X-Toolpath-From": str(min(start, count)),
                           "X-Toolpath-Count": str(count),
                           "Access-Control-Expose-Headers": "X-Toolpath-Key, X-Toolpath-From, X-Toolpath-Count"}
                    accepts_gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
                    if is_gz and not accepts_gz:
                        data, is_gz = gzip.decompress(data), False
                    elif not is_gz and accepts_gz and len(data) > 64 * 1024:
                        data, is_gz = gzip.compress(data, 1), True
                    if is_gz:
                        hdr["Content-Encoding"] = "gzip"
                    return self._send(200, data, "application/octet-stream", hdr)
                if what == "events":
                    return self._sse(p)
            return self._json({"error": "not found"}, 404)

        def _sse(self, p):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = p.subscribe()
            try:
                snap = json.dumps(p.snapshot(), separators=(",", ":"))
                self.wfile.write(f"event: state\ndata: {snap}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        ev, data = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    else:
                        self.wfile.write(f"event: {ev}\ndata: {data}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                p.unsubscribe(q)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            f = (WEB / rel).resolve()
            if WEB.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if f.suffix == ".js":
                ctype = "text/javascript"
            elif f.suffix == ".glb":
                ctype = "model/gltf-binary"
            gz = f.with_name(f.name + ".gz")          # pre-compressed sibling (e.g. models/mk4.glb.gz)
            if gz.is_file() and "gzip" in (self.headers.get("Accept-Encoding") or "") \
                    and gz.stat().st_mtime >= f.stat().st_mtime:
                return self._send(200, gz.read_bytes(), ctype, {"Content-Encoding": "gzip"})
            self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port and steal requests
    # (an old PrintTwin kept answering). Claim the port exclusively there instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(host: str, port: int, printers: dict, slicer: bool = False, log=print):
    """Raises OSError if the port is taken (e.g. PrintTwin is already running).
    slicer: pass the slicers' print-host calls on to the printers ([slicer] print_host = true)."""
    return _Server((host, port), make_handler(printers, port, slicer, log))
