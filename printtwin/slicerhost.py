"""PrintTwin as the slicer's print host: a transparent PrusaLink pass-through.

In PrusaSlicer (Physical Printer) or OrcaSlicer (Device) the print host is set to
http://<this PC>:8790/p/<printer id> with host type PrusaLink and the printer's own API key.
The slicer then shows PrintTwin's live 3D view in its printer tab, and every API call
(connection test, storage list, upload, "upload and print") is passed on to the printer
unchanged: the slicer's API key goes with it, PrintTwin adds no credentials of its own and
answers nothing itself, so it cannot do more than the slicer could do on the printer directly.

Uploads are passed through while they arrive (the slicer's progress bar shows the printer's
real speed, the printer's answer goes back to the slicer) and PrintTwin keeps a copy in the
hand-over folder, so the 3D model is ready the moment the print starts.

Off unless config.local.toml has  [slicer] print_host = true.
"""
from __future__ import annotations

import http.client
import os
import re
import select
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import incoming

# hop-by-hop headers and the ones the pass-through sets itself
_SKIP_REQ = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
             "transfer-encoding", "upgrade", "host", "expect", "content-length"}
_SKIP_RESP = {"connection", "keep-alive", "transfer-encoding", "server", "date", "content-length"}
CHUNK = 64 * 1024
TIMEOUT_S = 60.0                  # per socket operation, towards the printer and the slicer
MAX_UPLOAD = 4 << 30              # bytes
MAX_PER_PRINTER = 2               # requests passed on to one printer at the same time
SLOT_WAIT_S = 30.0
_slots: dict[str, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


def is_printer_api(path: str) -> bool:
    """Paths of the PrusaLink / OctoPrint API the slicers call (never PrintTwin's own /api/...)."""
    return path == "/api/version" or path.startswith(("/api/v1/", "/api/files"))


def _upload_name(method: str, path: str) -> str | None:
    """File name of an upload: PUT /api/v1/files/<storage>/<path> (PrusaLink) or POST /api/files/<loc>."""
    if method == "PUT" and path.startswith("/api/v1/files/"):
        name = os.path.basename(unquote(path))
        return name or None
    if method == "POST" and path.startswith("/api/files/"):
        return ""                                     # multipart: the name is in the body
    return None


def _multipart_file(blob: Path, ctype: str) -> tuple[bytes, str, bool] | None:
    """File part + name + print flag of a multipart/form-data body (OctoPrint-style POST /api/files/local)."""
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return None
    sep = b"--" + m.group(1).encode()
    found, start = None, False
    for part in blob.read_bytes().split(sep):
        head, _, body = part.partition(b"\r\n\r\n")
        body = body[:-2] if body.endswith(b"\r\n") else body
        fm = re.search(rb'filename="([^"]*)"', head)
        if fm and body:
            found = body, fm.group(1).decode("utf-8", "replace")
        elif re.search(rb'name="print"', head):
            start = body.strip().lower() == b"true"
    return (*found, start) if found else None


def _slot(printer) -> threading.BoundedSemaphore:
    with _slots_lock:
        return _slots.setdefault(printer.id, threading.BoundedSemaphore(MAX_PER_PRINTER))


def forward(h, printer, path_qs: str, log=print) -> None:
    """Pass the request in handler `h` on to `printer` (path_qs = printer-side path + query)."""
    method = h.command
    path = urlsplit(path_qs).path
    if any(seg in (".", "..") for seg in unquote(path).split("/")):
        return h._json({"error": "bad path"}, 400)
    if "chunked" in (h.headers.get("Transfer-Encoding") or "").lower():
        return h._json({"error": "chunked uploads are not supported, send Content-Length"}, 411)
    cl = (h.headers.get("Content-Length") or "0").strip()
    if not (cl.isascii() and cl.isdigit()):         # no "-1", "+5", "1_0" or other digits
        return h._json({"error": "bad Content-Length"}, 400)
    n = int(cl)
    if n > MAX_UPLOAD:
        return h._json({"error": f"upload too large (max {MAX_UPLOAD >> 30} GB)"}, 413)
    slot = _slot(printer)
    if not slot.acquire(timeout=SLOT_WAIT_S):
        return h._json({"error": f"printer {printer.id} is busy with other requests"}, 503)
    try:
        h.connection.settimeout(TIMEOUT_S)          # a client that stops sending must not hold the printer
        _forward(h, printer, method, path, path_qs, n, log)
    finally:
        slot.release()


class _ClientGone(Exception):
    """The slicer stopped sending (hung up or idle past the timeout)."""


def _read_client(h, size: int) -> bytes:
    try:
        chunk = h.rfile.read(size)
    except OSError as e:
        raise _ClientGone(str(e)) from e
    if not chunk:
        raise _ClientGone("the slicer hung up")
    return chunk


def _answered_early(conn) -> bool:
    """The printer sent its answer before the upload ended (wrong key, busy, storage full).
    Checked between chunks: once it closes the connection, Windows may discard that answer."""
    sock = conn.sock
    if sock is None or not select.select([sock], [], [], 0)[0]:
        return False
    try:
        peek = sock.recv(12, socket.MSG_PEEK)
    except OSError:
        return False                                 # the next send fails and says why
    if not peek:
        raise ConnectionResetError("the printer closed the connection")
    return peek.startswith(b"HTTP/") and peek[9:12] != b"100"


def _drain(h, left: int) -> None:
    """Read and drop the rest of the slicer's upload, so it gets to read our answer."""
    try:
        while left > 0:
            left -= len(_read_client(h, min(CHUNK, left)))
    except _ClientGone:
        pass


def _forward(h, printer, method, path, path_qs, n, log):
    conn = http.client.HTTPConnection(printer.host, timeout=TIMEOUT_S)
    try:
        conn.putrequest(method, path_qs, skip_accept_encoding=True)
        for k, v in h.headers.items():
            if k.lower() not in _SKIP_REQ:
                conn.putheader(k, v)
        if n or method in ("PUT", "POST", "PATCH"):
            conn.putheader("Content-Length", str(n))
        conn.endheaders()
    except OSError as e:
        conn.close()
        return h._json({"error": f"printer {printer.id} ({printer.host}) not reachable: {e}"}, 502)

    name = _upload_name(method, path) if n else None
    tmp = tee = kept = resp = None
    if name is not None:
        try:
            printer.incoming_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix="upload_", suffix=".tmp", dir=printer.incoming_dir)
            tmp, tee = Path(tmp_name), os.fdopen(fd, "wb")
        except OSError as e:                         # no copy then - the upload itself goes on
            log(f"{printer.id}: could not keep a copy of the upload: {e}")
    t0 = time.time()
    left = n
    try:
        if n and "100-continue" in (h.headers.get("Expect") or "").lower():
            h.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")     # curl would otherwise wait 1 s
            h.wfile.flush()
        while left:
            if _answered_early(conn):
                resp = conn.getresponse()            # e.g. wrong key, busy: stop sending
                break
            chunk = _read_client(h, min(CHUNK, left))
            left -= len(chunk)
            if tee:
                try:
                    tee.write(chunk)
                    if not left:
                        # keep the copy before the printer has the whole file: with "upload and
                        # print" it starts the job at once, and the next status poll must already
                        # find the copy (no download over Wi-Fi)
                        tee.close()
                        tee = None
                        kept = _keep_upload(printer, tmp, name, h.headers)
                except OSError as e:                 # e.g. disk full: the upload goes on
                    log(f"{printer.id}: could not keep a copy of the upload: {e}")
                    if tee:
                        tee.close()
                        tee = None
            try:
                conn.send(chunk)
            except OSError:
                # the printer stopped reading - it may have answered just now
                resp = conn.getresponse()
                break
        if resp is None:
            resp = conn.getresponse()
        body = resp.read()
    except _ClientGone as e:
        conn.close()                                 # the printer drops the partial upload
        _drop(kept)
        log(f"{printer.id}: slicer {method} {path}: {e} after {n - left} of {n} bytes")
        return None
    except (OSError, http.client.HTTPException) as e:
        conn.close()
        _drop(kept)
        log(f"{printer.id}: slicer {method} {path} failed: {e}")
        _drain(h, left)                              # else the slicer may not read our answer
        try:
            return h._json({"error": f"printer {printer.id}: {e}"}, 502)
        except OSError:
            return None
    finally:
        if tee:
            tee.close()
        if tmp:
            tmp.unlink(missing_ok=True)

    conn.close()
    if left:
        _drain(h, left)                              # the printer answered before the end
    # 409 on "upload and print": the MK4 stored the file but can't start it right now
    if kept and not (200 <= resp.status < 300 or (resp.status == 409 and not left)):
        _drop(kept)                                  # the printer refused it: no copy
        kept = None
    if kept:
        how = ", printing" if kept[2] and resp.status < 300 else (" (printer busy)" if resp.status == 409 else "")
        log(f"{printer.id}: slicer uploaded {kept[1]} ({n / 1e6:.1f} MB in {time.time() - t0:.0f} s)"
            f"{how} - copy kept for the 3D view")
    elif method not in ("GET", "HEAD"):
        log(f"{printer.id}: slicer {method} {path} -> {resp.status}")
    try:
        h.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in _SKIP_RESP:
                h.send_header(k, v)
        h.send_header("Content-Length",
                      resp.getheader("Content-Length", "0") if method == "HEAD" else str(len(body)))
        h.end_headers()
        if method != "HEAD":
            h.wfile.write(body)
    except OSError:
        pass                                         # the slicer hung up; the printer has the file


def _keep_upload(printer, tmp: Path, name: str, headers) -> tuple[Path, str, bool] | None:
    """Store the uploaded file in the hand-over folder; (stored file, name, print) or None."""
    start = "?1" in (headers.get("Print-After-Upload") or "")
    if name == "":                                  # multipart POST: take the file part out
        part = _multipart_file(tmp, headers.get("Content-Type"))
        if not part:
            return None
        data, name, start = part
        tmp.write_bytes(data)
    if not name:
        return None
    return incoming.store(tmp, name, "slicer upload", printer.incoming_dir), name, start


def _drop(kept) -> None:
    if kept:
        kept[0].unlink(missing_ok=True)
        kept[0].with_suffix(".json").unlink(missing_ok=True)
