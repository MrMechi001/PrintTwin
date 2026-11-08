"""PrintTwin as the slicer's print host: a transparent pass-through to the printer.

In PrusaSlicer (Physical Printer) or OrcaSlicer (Device) the print host is set to
http://<this PC>:8790/p/<printer id> - host type PrusaLink for Prusa printers, "Klipper (via
Moonraker)" / "Moonraker (Klipper)" or OctoPrint for Klipper printers. The slicer then shows
PrintTwin's live 3D view in its printer tab, and its API calls (connection test, storage list,
upload, "upload and print") are passed on to the printer unchanged: the slicer's API key goes
with them, PrintTwin adds no credentials of its own and answers nothing itself.

PrusaLink checks the API key on every call, so any PrusaLink call may pass. Moonraker usually
trusts every LAN address instead (trusted_clients) and would take PrintTwin's address for the
slicer's: for Klipper printers only the handful of calls the slicers make are passed on (no
G-code, no emergency stop, no file deletion, no reboot), and without an API key only from LAN
addresses.

Uploads are passed through while they arrive (the slicer's progress bar shows the printer's
real speed, the printer's answer goes back to the slicer) and PrintTwin keeps a copy in the
hand-over folder, so the 3D model is ready the moment the print starts.

Off unless config.local.toml has  [slicer] print_host = true.
"""
from __future__ import annotations

import hmac
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
             "transfer-encoding", "upgrade", "host", "expect", "content-length",
             # never let a client claim another address (Moonraker trusts these from a trusted proxy)
             "x-real-ip", "x-forwarded-for", "forwarded", "x-forwarded-host", "x-forwarded-proto"}
_SKIP_RESP = {"connection", "keep-alive", "transfer-encoding", "server", "date", "content-length"}
CHUNK = 64 * 1024
TIMEOUT_S = 60.0                  # per socket operation, towards the printer and the slicer
UPLOAD_REPLY_S = 900.0            # Moonraker answers an upload only after scanning the file (minutes)
MAX_UPLOAD = 4 << 30              # bytes
MAX_PER_PRINTER = 2               # requests passed on to one printer at the same time
SLOT_WAIT_S = 30.0
_slots: dict[str, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


# the calls PrusaSlicer and OrcaSlicer make to a Klipper printer (OctoPrint emulation and the
# native Moonraker host types); nothing else is passed on to Moonraker
MOONRAKER_CALLS = {("GET", "/api/version"), ("POST", "/api/files/local"), ("GET", "/server/info"),
                   ("GET", "/server/files/roots"), ("POST", "/server/files/upload"), ("POST", "/printer/print/start")}


def is_printer_api(path: str) -> bool:
    """Paths of a printer API the slicers call (never PrintTwin's own /api/...)."""
    return path == "/api/version" or path.startswith(("/api/v1/", "/api/files", "/server/", "/printer/",
                                                       "/machine/", "/access/"))


def allowed(kind: str, method: str, path: str) -> bool:
    """May this printer API call be passed on to a printer of this kind?"""
    if kind == "moonraker":
        return (method, path.rstrip("/")) in MOONRAKER_CALLS
    if kind == "prusalink":
        return path == "/api/version" or path.startswith(("/api/v1/", "/api/files"))
    return False                    # Bambu printers: no print host (the slicer talks to the printer itself)


def lan_client(ip: str) -> bool:
    """A client on the local network (what Moonraker's default trusted_clients cover)."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_loopback or a.is_link_local or a.is_private) and not (
        a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"))           # not Tailscale / CGNAT


def client_ok(printer, ip: str, key: str | None) -> bool:
    """May this client use the pass-through? Klipper printers: LAN clients, others only with the
    api_key from PrintTwin's own config - older Moonraker ignores a wrong key from a trusted
    address (PrintTwin), so "any key" would hand out PrintTwin's trust."""
    if printer.kind != "moonraker" or lan_client(ip):
        return True
    want = (getattr(getattr(printer, "client", None), "key", "") or "").strip()
    got = (key or "").strip()
    return bool(want) and hmac.compare_digest(got.encode(), want.encode())


def _upload_name(method: str, path: str) -> str | None:
    """File name of an upload: PUT /api/v1/files/<storage>/<path> (PrusaLink), POST /api/files/<loc>
    (OctoPrint style) or POST /server/files/upload (Moonraker); "" = multipart, name in the body."""
    if method == "PUT" and path.startswith("/api/v1/files/"):
        name = os.path.basename(unquote(path))
        return name or None
    if method == "POST" and (path.startswith("/api/files/") or path.rstrip("/") == "/server/files/upload"):
        return ""                                     # multipart: the name is in the body
    return None


def _multipart_file(blob: Path, ctype: str) -> tuple[bytes, str, bool, str] | None:
    """File part, its name, the print flag and the target root of a multipart/form-data upload
    (POST /api/files/local or /server/files/upload: fields file, print, path, root)."""
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return None
    sep = b"--" + m.group(1).encode()
    found, start, folder, root = None, False, "", ""
    for part in blob.read_bytes().split(sep):
        head, _, body = part.partition(b"\r\n\r\n")
        body = body[:-2] if body.endswith(b"\r\n") else body
        fm = re.search(rb'filename="([^"]*)"', head)
        field = re.search(rb'name="([^"]*)"', head)
        field = field.group(1) if field else b""
        if fm and body:
            found = body, fm.group(1).decode("utf-8", "replace")
        elif field == b"print":
            start = body.strip().lower() == b"true"
        elif field == b"path":
            folder = body.decode("utf-8", "replace").strip().replace("\\", "/").strip("/")
        elif field == b"root":
            root = body.decode("utf-8", "replace").strip()
    if not found:
        return None
    name = found[1].strip().lstrip("/")
    return found[0], (f"{folder}/{name}" if folder else name), start, root


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
    if not client_ok(printer, h.client_address[0], h.headers.get("X-Api-Key")):
        return h._json({"error": "this Klipper printer is only reachable through PrintTwin from the local "
                                 "network - or set api_key for it in config.local.toml and enter the same "
                                 "key in the slicer"}, 403)
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


class _Refused(Exception):
    """An upload PrintTwin does not pass on (checked before its last bytes go to the printer)."""


_PART_HEAD = re.compile(rb'Content-Disposition: form-data; name="(file|print|path|root|checksum|plateindex)"'
                        rb'(?:; filename="[^"\\\r\n]*")?(?:\r\nContent-Type: [^\r\n]+)?', re.I)   # no backslash


def _filename_as_moonraker_reads_it(head: bytes) -> str | None:
    """The file name Moonraker gets from this part header: its multipart parser (streaming_form_data)
    reads Content-Disposition with Python's email header parser, which decodes RFC 2047 words
    (=?utf-8?b?...?=) and quoted-pair escapes - a name can look harmless here and be ../x there."""
    from email.message import EmailMessage
    line = head.split(b"\r\n", 1)[0].decode("utf-8", "replace")
    em = EmailMessage()
    em["content-disposition"] = line.split(":", 1)[1].strip()
    return dict(em["content-disposition"].params).get("filename")


def _moonraker_upload_problem(tmp: Path, ctype: str | None) -> str | None:
    """Why a multipart upload to a Klipper printer is not passed on, or None. Fail closed: only the
    plain form the slicers send (fields file, print, path, root, checksum, plateindex), at most one
    root and that one "gcodes" - an upload into Moonraker's config root would overwrite printer.cfg."""
    # split exactly like Moonraker's parser (streaming_form_data): a part starts at CRLF--boundary,
    # the form ends at CRLF--boundary-- - a "--boundary--" inside a file is data there, so it must
    # not end the check here (or a later root=config part would go through unseen)
    m = re.fullmatch(r'\s*multipart/form-data;\s*boundary=([A-Za-z0-9_.\-]{1,70})\s*', ctype or "", re.I)
    if not m:                                        # only plain boundaries (curl: ------...<hex>)
        return "not a plain multipart upload"
    data, b = tmp.read_bytes(), m.group(1).encode()
    if not data.startswith(b"--" + b + b"\r\n"):
        return "unexpected upload layout"
    pieces = data[len(b) + 4:].split(b"\r\n--" + b)
    parts = [pieces[0]]
    for i, pc in enumerate(pieces[1:], 1):
        if pc.startswith(b"--") and i == len(pieces) - 1:
            break                                    # the end of the form (only an epilogue may follow)
        if not pc.startswith(b"\r\n"):
            return "unexpected upload layout"
        parts.append(pc[2:])
    else:
        return "unexpected upload layout"            # never ended
    roots = []
    for part in parts:
        head, sep, body = part.partition(b"\r\n\r\n")
        if not sep or not _PART_HEAD.fullmatch(head):
            return "unexpected upload field"
        field = re.match(rb'Content-Disposition: form-data; name="([^"]*)"', head, re.I).group(1).lower()
        fn = re.search(rb'filename="([^"]*)"', head)
        names = [fn.group(1)] if fn else []
        if fn:
            try:
                seen = _filename_as_moonraker_reads_it(head)
            except Exception:                        # anything the header parser chokes on: refuse
                seen = None
            if seen != fn.group(1).decode("utf-8", "replace") or "=?" in (seen or ""):
                return "unusual file name (encoded or escaped)"
        if field == b"path":
            names.append(body)
        if any(seg.strip() == b".." for nm in names for seg in nm.replace(b"\\", b"/").split(b"/")):
            return "no '..' in upload names"
        if any(re.match(rb"\s*(/|\\|[A-Za-z]:)", nm) for nm in names):
            return "no absolute paths in upload names"
        if field == b"root":
            roots.append(body.strip().lower())
    if len(roots) > 1 or (roots and roots[0] != b"gcodes"):
        return "only uploads to the gcodes folder are passed on (not config files)"
    return None


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
    conn = http.client.HTTPConnection(printer.http_netloc, timeout=TIMEOUT_S)
    try:
        conn.putrequest(method, printer.http_prefix + path_qs, skip_accept_encoding=True)
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
    checked = printer.kind == "moonraker" and name == ""      # multipart upload to a Klipper printer
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
        if checked and tee is None:
            raise _Refused("PrintTwin cannot check this upload (no room for a temporary copy)")
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
                        tee.close()
                        tee = None
                        # Klipper: check the whole upload before its last bytes go out - the
                        # printer only stores it once complete
                        why = None
                        if checked:
                            ctypes = h.headers.get_all("Content-Type") or []
                            why = (_moonraker_upload_problem(tmp, ctypes[0]) if len(ctypes) == 1
                                   else "exactly one Content-Type, please")
                        if why:
                            raise _Refused(why)
                        # keep the copy before the printer has the whole file: with "upload and
                        # print" it starts the job at once, and the next status poll must already
                        # find the copy (no download over Wi-Fi)
                        kept = _keep_upload(printer, tmp, name, h.headers)
                except OSError as e:                 # e.g. disk full: the upload goes on
                    log(f"{printer.id}: could not keep a copy of the upload: {e}")
                    if tee:
                        tee.close()
                        tee = None
                    if checked:
                        raise _Refused("PrintTwin cannot check this upload (writing its temporary copy failed)")
            try:
                conn.send(chunk)
            except OSError:
                # the printer stopped reading - it may have answered just now
                resp = conn.getresponse()
                break
        if resp is None:
            if name is not None and printer.kind == "moonraker" and conn.sock:
                conn.sock.settimeout(UPLOAD_REPLY_S)  # Moonraker scans the file before it answers
            resp = conn.getresponse()
        body = resp.read()
    except _Refused as e:
        conn.close()                                 # the last bytes never went out: nothing stored
        _drop(kept)
        log(f"{printer.id}: slicer {method} {path} refused: {e}")
        _drain(h, left)
        try:
            return h._json({"error": str(e)}, 403)
        except OSError:
            return None
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
    if kept and printer.kind == "moonraker" and not _same_file(kept, body):
        _drop(kept)                                  # Moonraker rewrote the file: the printer's copy counts
        log(f"{printer.id}: the printer changed the uploaded file (e.g. object processing) - "
            f"PrintTwin loads it from the printer instead")
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


def _same_file(kept, body: bytes) -> bool:
    """Moonraker's upload reply {"item": {"size": ...}, "print_started": ...}: is the stored file still
    the bytes the slicer sent? (object processing / Klipper Estimator rewrite it)"""
    import json
    try:
        size = json.loads(body).get("item", {}).get("size")
    except (ValueError, AttributeError):
        return True                                  # not Moonraker's reply (OctoPrint-style host): keep
    return size is None or size == kept[0].stat().st_size


def _keep_upload(printer, tmp: Path, name: str, headers) -> tuple[Path, str, bool] | None:
    """Store the uploaded file in the hand-over folder; (stored file, name, print) or None."""
    start = "?1" in (headers.get("Print-After-Upload") or "")
    if name == "":                                  # multipart POST: take the file part out
        part = _multipart_file(tmp, headers.get("Content-Type"))
        if not part:
            return None
        data, name, start, root = part
        if root not in ("", "gcodes"):               # e.g. a config file: nothing that prints
            return None
        tmp.write_bytes(data)
    if not name:
        return None
    return incoming.store(tmp, name, "slicer upload", printer.incoming_dir), name, start


def _drop(kept) -> None:
    if kept:
        kept[0].unlink(missing_ok=True)
        kept[0].with_suffix(".json").unlink(missing_ok=True)
