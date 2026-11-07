"""Minimal PrusaLink HTTP client (stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


class PrusaLink:
    def __init__(self, host: str, api_key: str, timeout: float = 10.0):
        self.base = f"http://{host}"
        self.key = api_key
        self.timeout = timeout

    def _open(self, path: str, timeout: float | None = None):
        req = urllib.request.Request(self.base + path, headers={"X-Api-Key": self.key})
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
