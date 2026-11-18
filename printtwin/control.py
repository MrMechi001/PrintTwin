"""Printer control from the 3D view - opt-in: [control] enabled = true in config.local.toml.

Pause / resume / stop the running print, print a file from the printer's storage again (newest
first), upload a file and print it. Prusa printers through PrusaLink, Klipper printers through
Moonraker. Bambu Lab printers: none - PrintTwin only watches them.

Who may use it is checked in server.py: this PC itself, other devices only with [control] password.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import quote

from . import incoming

EXTS = (".bgcode", ".gcode", ".gco", ".g")
MAX_FILES = 40
UPLOAD_TIMEOUT_S = 120.0                 # per socket operation: the printer writes to its USB stick


class ControlError(Exception):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def supported(p) -> bool:
    return p.kind in ("prusalink", "moonraker")


def _need(p):
    if not supported(p):
        raise ControlError("PrintTwin doesn't control Bambu Lab printers - it only watches them", 403)


def _ok(answer: tuple, what: str):
    status, obj, text = answer
    if status in (200, 201, 204):
        return obj
    if status in (401, 403):
        raise ControlError(f"the printer refused to {what} (HTTP {status}{': ' + text if text else ''}) - "
                           f"check its api_key in config.local.toml", 502)
    raise ControlError(f"the printer didn't {what}: {text or f'HTTP {status}'}", 409 if status == 409 else 502)


def _q(path: str) -> str:
    return quote(path, safe="/")


def valid_name(name: str) -> str | None:
    """A plain file name for an upload (no folders), or None."""
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not name or name.startswith(".") or len(name) > 200 or not name.lower().endswith(EXTS) \
            or any(c in name for c in '<>:"|?*\0'):
        return None
    return name


# ------------------------------------------------------------------ the running print
def _prusa_job(p) -> tuple[int | None, str]:
    obj = _ok(p.client.call("GET", "/api/v1/status"), "answer") or {}
    return (obj.get("job") or {}).get("id"), str((obj.get("printer") or {}).get("state") or "").upper()


def job_action(p, action: str):
    """action: pause, resume or stop."""
    _need(p)
    if action not in ("pause", "resume", "stop"):
        raise ControlError(f"unknown action {action!r}", 400)
    if p.kind == "moonraker":
        _ok(p.client.call("POST", f"/printer/print/{'cancel' if action == 'stop' else action}"), action)
        return
    jid, state = _prusa_job(p)
    want = {"pause": ("PRINTING",), "resume": ("PAUSED",), "stop": ("PRINTING", "PAUSED", "ATTENTION")}[action]
    if jid is None or state not in want:
        raise ControlError(f"nothing to {action}: the printer is {state.lower() or 'not printing'}")
    if action == "stop":
        _ok(p.client.call("DELETE", f"/api/v1/job/{jid}"), "stop the print")
    else:
        _ok(p.client.call("PUT", f"/api/v1/job/{jid}/{action}"), action)


# ------------------------------------------------------------------ files on the printer
def _prusa_storages(p) -> list[dict]:
    status, obj, _ = p.client.call("GET", "/api/v1/storage")
    lst = (obj or {}).get("storage_list") if status == 200 and isinstance(obj, dict) else None
    out = [s for s in lst or [] if isinstance(s, dict) and s.get("path") and s.get("available", True)]
    return out or [{"path": "/usb", "read_only": False}]


def files(p) -> list[dict]:
    """The print files on the printer, newest first: [{path, name, size, date}] (path: what print() takes)."""
    _need(p)
    out: list[dict] = []
    if p.kind == "moonraker":
        status, obj, _ = p.client.call("GET", "/server/history/list?limit=100&order=desc")
        seen = set()
        for j in ((obj or {}).get("result") or {}).get("jobs") or [] if status == 200 else []:
            fn = j.get("filename")
            if fn and fn not in seen and j.get("exists", True) is not False:
                seen.add(fn)
                out.append({"path": fn, "name": os.path.basename(fn), "size": (j.get("metadata") or {}).get("size"),
                            "date": j.get("end_time") or j.get("start_time")})
        if not out:                                  # no history (component off): the files themselves
            obj = _ok(p.client.call("GET", "/server/files/list?root=gcodes"), "list its files") or {}
            for f in obj.get("result") or []:
                if str(f.get("path", "")).lower().endswith(EXTS):
                    out.append({"path": f["path"], "name": os.path.basename(f["path"]), "size": f.get("size"),
                                "date": f.get("modified")})
    else:
        for st in _prusa_storages(p):
            _walk(p, st["path"].rstrip("/"), out, depth=1)
    out.sort(key=lambda f: f.get("date") or 0, reverse=True)
    return out[:MAX_FILES]


def _walk(p, folder: str, out: list, depth: int):
    status, obj, _ = p.client.call("GET", "/api/v1/files" + _q(folder))
    if status != 200 or not isinstance(obj, dict):
        return
    for c in obj.get("children") or []:
        name = str(c.get("name") or "")
        if not name or name in (".", ".."):
            continue
        path = f"{folder}/{name}"
        if c.get("type") == "FOLDER":
            if depth > 0:
                _walk(p, path, out, depth - 1)
        elif c.get("type") == "PRINT_FILE" or name.lower().endswith(EXTS):
            out.append({"path": path, "name": c.get("display_name") or name, "size": c.get("size"),
                        "date": c.get("m_timestamp")})


def _safe_path(path: str) -> str:
    path = str(path or "")
    if not path or "\0" in path or any(seg == ".." for seg in path.replace("\\", "/").split("/")):
        raise ControlError("bad file path", 400)
    return path


def print_file(p, path: str):
    """Start printing a file that is on the printer (a path from files())."""
    _need(p)
    path = _safe_path(path)
    if p.kind == "moonraker":
        _ok(p.client.call("POST", "/printer/print/start?filename=" + quote(path.lstrip("/"), safe="")), "start the print")
        return
    if not path.startswith("/"):
        raise ControlError("bad file path", 400)
    _, state = _prusa_job(p)
    if state in ("PRINTING", "PAUSED", "BUSY", "ATTENTION"):
        raise ControlError(f"the printer is busy ({state.lower()})")
    _ok(p.client.call("POST", "/api/v1/files" + _q(path)), "start the print")


# ------------------------------------------------------------------ upload
def upload(p, src: Path, name: str, start: bool) -> str:
    """Send a file to the printer (and print it). A copy goes to the hand-over folder first, so the
    3D model is ready the moment the print starts. Returns where it went on the printer."""
    _need(p)
    name = valid_name(name) or ""
    if not name:
        raise ControlError("only .bgcode / .gcode files", 400)
    if start:
        if p.kind == "prusalink":
            _, state = _prusa_job(p)
            if state in ("PRINTING", "PAUSED", "BUSY", "ATTENTION"):
                raise ControlError(f"the printer is busy ({state.lower()}) - upload it without printing")
    try:
        incoming.store(src, name, "PrintTwin", p.incoming_dir)
    except OSError:
        pass
    size = Path(src).stat().st_size
    if p.kind == "moonraker":
        b = uuid.uuid4().hex
        head = (f'--{b}\r\nContent-Disposition: form-data; name="root"\r\n\r\ngcodes\r\n'
                f'--{b}\r\nContent-Disposition: form-data; name="print"\r\n\r\n{"true" if start else "false"}\r\n'
                f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n').encode()
        tail = f"\r\n--{b}--\r\n".encode()

        def body():
            yield head
            with open(src, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk
            yield tail
        _ok(p.client.call("POST", "/server/files/upload", body(),
                          {"Content-Type": f"multipart/form-data; boundary={b}",
                           "Content-Length": str(len(head) + size + len(tail))}, UPLOAD_TIMEOUT_S),
            "take the file")
        return f"gcodes/{name}"
    storage = next((s["path"].rstrip("/") for s in _prusa_storages(p) if not s.get("read_only")), "/usb")
    with open(src, "rb") as f:
        _ok(p.client.call("PUT", f"/api/v1/files{_q(storage)}/{quote(name)}", f,
                          {"Content-Type": "application/octet-stream", "Content-Length": str(size),
                           "Print-After-Upload": "?1" if start else "?0", "Overwrite": "?1"}, UPLOAD_TIMEOUT_S),
            "take the file")
    return f"{storage}/{name}"
