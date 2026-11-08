"""Minimal HTTP client for printer APIs - PrusaLink, Moonraker (stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


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


class PrusaLink:
    """HTTP client used for PrusaLink and Moonraker alike (X-Api-Key, JSON, streamed downloads)."""
    def __init__(self, host: str, api_key: str, timeout: float = 10.0):
        netloc, prefix = split_host(host)
        self.base = f"http://{netloc}{prefix}"
        self.key = api_key
        self.timeout = timeout

    def _open(self, path: str, timeout: float | None = None):
        # no key configured (e.g. Moonraker with trusted clients): send no X-Api-Key at all
        req = urllib.request.Request(self.base + path, headers={"X-Api-Key": self.key} if self.key else {})
        return urllib.request.urlopen(req, timeout=timeout or self.timeout)

    def get_json(self, path: str):
        """Return (http_status, parsed_json_or_None)."""
        try:
            with self._open(path) as r:
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            return e.code, None
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
