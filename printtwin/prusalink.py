"""Minimal HTTP client for printer APIs - PrusaLink, Moonraker (stdlib only).

Logins: X-Api-Key (PrusaLink on the printer, Moonraker), or HTTP Digest with user name and
password (PrusaLink on a Raspberry Pi without an API key: realm "Administrator", MD5-sess with
qop=auth - which urllib's own digest handler can't do).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import __version__

USER_AGENT = f"PrintTwin/{__version__}"   # fixed: PrusaLink binds its digest nonce to the User-Agent
_PARAM = re.compile(r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))')
_HASHES = {"MD5": hashlib.md5, "SHA-256": hashlib.sha256}


def split_host(host: str) -> tuple[str, str]:
    """"192.168.1.5", "voron.local:7125" or "http://voron.local/moonraker" -> (host[:port], path prefix)."""
    h = str(host).strip()
    if h.lower().startswith("https://"):
        raise ValueError(f"host {host!r}: https is not supported - use the printer's plain http address "
                         f"(e.g. voron.local or 192.168.1.30:7125)")
    if h.lower().startswith("http://"):
        h = h[len("http://"):]
    netloc, _, prefix = h.partition("/")
    return netloc, ("/" + prefix.strip("/")) if prefix.strip("/") else ""


def url_path(path: str) -> str:
    """A file path as PrusaLink names it ("/api/files/local/PrusaLink gcodes/a b.gcode/raw") made
    safe for the request line: spaces, '#', '?', non-ASCII and a lone '%' are percent-encoded
    (http.client refuses a space; the printer decodes the path again). Escapes already there stay."""
    return quote(re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", str(path)), safe="/:@!$&'()*+,;=~%")


def digest_challenge(header: str | None) -> dict | None:
    """'Digest realm="x", nonce="y", ...' -> {"realm": "x", "nonce": "y", ...}; None if not digest."""
    h = str(header or "").strip()
    if not h.lower().startswith("digest"):
        return None
    out = {}
    for k, quoted, plain in _PARAM.findall(h[6:]):
        out[k.lower()] = re.sub(r"\\(.)", r"\1", quoted) if quoted else plain
    return out if "nonce" in out else None


class DigestError(PermissionError):
    """The printer refused the user name / password."""


class _Digest:
    """RFC 7616 client side: MD5 / MD5-sess / SHA-256(-sess), qop=auth (or none), nc counting.
    The challenge's realm, nonce, opaque and algorithm go back exactly as received."""

    def __init__(self, user: str, password: str):
        self.user, self.password = user, password
        self.ch: dict | None = None
        self.nc = 0
        self.cnonce = ""
        self.lock = threading.Lock()

    def take(self, header: str | None) -> bool:
        ch = digest_challenge(header)
        if not ch:
            return False
        with self.lock:
            self.ch, self.nc, self.cnonce = ch, 0, os.urandom(8).hex()
        return True

    def header(self, method: str, uri: str) -> str | None:
        with self.lock:
            ch = self.ch
            if ch is None:
                return None
            self.nc += 1
            nc, cnonce = f"{self.nc:08x}", self.cnonce
        alg = ch.get("algorithm") or "MD5"
        base = alg.upper()
        sess = base.endswith("-SESS")
        if sess:
            base = base[:-5]
        fn = _HASHES.get(base)
        if fn is None:
            raise DigestError(f"digest algorithm {alg!r} is not supported")
        h = lambda s: fn(s.encode("utf-8")).hexdigest()     # noqa: E731
        realm, nonce = ch.get("realm", ""), ch["nonce"]
        ha1 = h(f"{self.user}:{realm}:{self.password}")
        if sess:
            ha1 = h(f"{ha1}:{nonce}:{cnonce}")
        ha2 = h(f"{method}:{uri}")
        qops = [q.strip() for q in ch.get("qop", "").split(",") if q.strip()]
        if "auth" in qops:
            resp = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        elif qops:
            raise DigestError(f"digest qop {ch['qop']!r} is not supported (only auth)")
        else:
            resp = h(f"{ha1}:{nonce}:{ha2}")

        def q(v):
            return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
        parts = [f"username={q(self.user)}", f"realm={q(realm)}", f"nonce={q(nonce)}", f"uri={q(uri)}",
                 f"response={q(resp)}"]
        if "algorithm" in ch:
            parts.append(f"algorithm={ch['algorithm']}")
        if "opaque" in ch:
            parts.append(f"opaque={q(ch['opaque'])}")
        if "auth" in qops:
            parts += ["qop=auth", f"nc={nc}", f"cnonce={q(cnonce)}"]
        return "Digest " + ", ".join(parts)


class PrusaLink:
    """HTTP client used for PrusaLink and Moonraker alike (X-Api-Key or digest, JSON, streamed downloads)."""
    def __init__(self, host: str, api_key: str | None, timeout: float = 10.0,
                 user: str | None = None, password: str | None = None):
        netloc, prefix = split_host(host)
        self.base = f"http://{netloc}{prefix}"
        self.prefix = prefix
        self.key = str(api_key or "").strip()
        self.timeout = timeout
        # the API key wins when both are set (PrusaLink checks a key that is sent and ignores digest)
        self.digest = _Digest(str(user), str(password)) if user and password and not self.key else None

    def _request(self, path: str, auth: str | None) -> urllib.request.Request:
        headers = {"User-Agent": USER_AGENT}
        if self.key:                          # no key configured: send no X-Api-Key at all
            headers["X-Api-Key"] = self.key
        if auth:
            headers["Authorization"] = auth
        return urllib.request.Request(self.base + path, headers=headers)

    def _open(self, path: str, timeout: float | None = None):
        if self.digest is None:
            return urllib.request.urlopen(self._request(path, None), timeout=timeout or self.timeout)
        uri = self.prefix + path
        tried_fresh = False
        for _ in range(3):
            sent = self.digest.header("GET", uri)          # the last challenge's nonce, nc + 1
            try:
                return urllib.request.urlopen(self._request(path, sent), timeout=timeout or self.timeout)
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                hdr = e.headers.get("WWW-Authenticate")
                ch = digest_challenge(hdr)
                e.close()
                if ch is None:
                    raise
                stale = str(ch.get("stale", "")).lower() == "true"
                if sent is not None and not stale:
                    if tried_fresh:                         # a fresh nonce and still refused
                        raise DigestError("PrusaLink refused the user name / password (HTTP 401)") from None
                    tried_fresh = True
                self.digest.take(hdr)
        raise DigestError("PrusaLink refused the login (HTTP 401)")

    def get_json(self, path: str):
        """Return (http_status, parsed_json_or_None)."""
        try:
            with self._open(path) as r:
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            return e.code, None
        except DigestError:
            return 401, None
        if status == 204 or not body:
            return status, None
        return status, json.loads(body)

    def stream(self, path: str, on_chunk: Callable[[bytes, int, int], None], chunk: int = 32 * 1024) -> int:
        """GET `path` and hand every piece to on_chunk(data, bytes_so_far, total) as it arrives."""
        n = 0
        with self._open(path, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            while data := r.read1(chunk):
                n += len(data)
                on_chunk(data, n, total)
        if total and n != total:
            raise IOError(f"download ended after {n} of {total} bytes")
        return n

    def download(self, path: str, dest: Path,
                 progress: Callable[[int, int], None] | None = None) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        n = 0
        with self._open(path, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(64 * 1024):
                f.write(chunk)
                n += len(chunk)
                if progress:
                    progress(n, total)
        tmp.replace(dest)
        return n
