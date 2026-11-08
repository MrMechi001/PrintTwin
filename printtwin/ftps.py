"""Implicit FTPS client (port 990) for Bambu Lab printers - standard library only.

Read-only: list files, their size and time, and download one. The quirks Bambu printers
(vsFTPd) need, as the open-source integrations found them:
  - TLS from the first byte (implicit FTPS, not AUTH TLS),
  - the data connection reuses the control connection's TLS session,
  - no TLS shutdown (unwrap) at the end of a transfer - it can hang; the socket is just closed,
  - TLS 1.2 at most,
  - PROT C (plain data channel, TLS control channel) as a fallback when the data channel's TLS
    fails (A1 / A1 mini),
  - one session at a time, always QUIT; "421 too many connections" means: leave it alone a while.
"""
from __future__ import annotations

import ftplib
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import tlsutil


class FtpsBusy(OSError):
    """421: the printer has no free FTP session - try again in a few minutes."""


class _ImplicitFTPS(ftplib.FTP_TLS):
    def __init__(self, ctx: ssl.SSLContext, server_name: str | None, timeout: float):
        super().__init__(context=ctx, timeout=timeout)
        self.server_name = server_name
        self.reuse_session = True

    def connect(self, host="", port=990, timeout=-999, source_address=None):
        self.host, self.port = host, port
        if timeout != -999:
            self.timeout = timeout
        raw = socket.create_connection((host, port), self.timeout)
        self.af = raw.family
        self.sock = self.context.wrap_socket(raw, server_hostname=self.server_name or host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.server_name or self.host,
                                            session=self.sock.session if self.reuse_session else None)
        return conn, size


class FtpsClient:
    """with FtpsClient(host, "12345678", cafile=... or pin_file=..., server_name=serial) as f:
           f.listing("/cache/"); f.retr("/cache/x.gcode.3mf", on_chunk)"""

    def __init__(self, host: str, password: str, user: str = "bblp", port: int = 990,
                 cafile: str | None = None, server_name: str | None = None, pin_file: Path | None = None,
                 timeout: float = 20.0):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.cafile, self.server_name, self.pin_file = cafile, server_name, pin_file
        self.timeout = timeout
        self.prot_c = False                       # learned: this printer's data channel needs PROT C
        self.ftp: _ImplicitFTPS | None = None

    # ------------------------------------------------------------------ session
    def open(self):
        ftp = _ImplicitFTPS(tlsutil.context(self.cafile), self.server_name, self.timeout)
        try:
            ftp.connect(self.host, self.port)
            if not self.cafile and self.pin_file:
                tlsutil.pin(ftp.sock, self.pin_file)
            ftp.login(self.user, self.password)
            ftp.prot_c() if self.prot_c else ftp.prot_p()
        except ftplib.error_temp as e:
            ftp.close()
            if str(e).startswith("421"):
                raise FtpsBusy(str(e)) from e
            raise
        except BaseException:
            ftp.close()
            raise
        self.ftp = ftp
        return self

    def close(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except (OSError, EOFError, ftplib.Error):
                pass
            try:
                self.ftp.close()
            except OSError:
                pass
            self.ftp = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------------ files
    def listing(self, folder: str = "/") -> list[tuple[str, int | None, float | None]]:
        """[(path, size, modification time)] of the files in `folder` (LIST, then MDTM per file)."""
        lines: list[str] = []
        self._data(lambda: self.ftp.retrlines(f"LIST {folder}", lines.append))
        out = []
        for ln in lines:
            parts = ln.split(None, 8)
            if len(parts) < 9 or ln.startswith("d"):
                continue
            name = parts[8]
            path = folder.rstrip("/") + "/" + name
            try:
                size = int(parts[4])
            except ValueError:
                size = None
            out.append((path, size, self.mtime(path)))
        return out

    def mtime(self, path: str) -> float | None:
        try:
            r = self.ftp.sendcmd(f"MDTM {path}")
        except ftplib.error_perm:
            return None
        m = re.match(r"213 (\d{14})", r)
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp() if m else None

    def size(self, path: str) -> int | None:
        try:
            return self.ftp.size(path)
        except ftplib.error_perm:
            return None

    def retr(self, path: str, on_chunk: Callable[[bytes, int, int], None], total: int = 0, chunk: int = 64 * 1024) -> int:
        """Download `path`, handing each piece to on_chunk(data, bytes_so_far, total)."""
        n = 0

        def run():
            nonlocal n
            self.ftp.voidcmd("TYPE I")
            conn = self.ftp.transfercmd(f"RETR {path}")
            try:
                while data := conn.recv(chunk):
                    n += len(data)
                    on_chunk(data, n, total)
            finally:
                conn.close()                      # no unwrap(): it hangs on some printers
            self.ftp.voidresp()

        self._data(run, lambda: n == 0)            # retry only before the first byte arrived
        return n

    def _data(self, run, can_retry=lambda: True):
        """A data-channel operation; when its TLS fails, once more with PROT C (A1 / A1 mini)."""
        try:
            run()
        except (ssl.SSLError, socket.timeout, ConnectionResetError) as e:
            if self.prot_c or not can_retry():
                raise
            self.prot_c = True
            self.close()
            self.open()
            try:
                run()
            except Exception:
                self.prot_c = False
                raise e
