"""Implicit FTPS client (port 990) for Bambu Lab printers - standard library only.

Read-only: list files, their size and time, and download one. The quirks Bambu printers
(vsFTPd) need, as the open-source integrations found them:
  - TLS from the first byte (implicit FTPS, not AUTH TLS),
  - the data connection reuses the control connection's TLS session,
  - no TLS shutdown (unwrap) at the end of a transfer - it can hang; the socket is just closed,
  - TLS 1.2 at most,
  - PROT C (plain data channel, TLS control channel) as a fallback when the data channel's TLS
    fails - only where it is needed (A1 / A1 mini / A2L: allow_plain_data), never after a
    certificate error,
  - one session at a time, always QUIT; "421 too many connections" means: leave it alone a while.
Without a CA file the control channel's certificate is pinned (tlsutil.pin) before the login,
and every data channel must show the same certificate.
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
    def __init__(self, ctx: ssl.SSLContext, server_name: str | None, timeout: float, pinned: bool = False):
        super().__init__(context=ctx, timeout=timeout)
        self.server_name = server_name
        self.reuse_session = True
        self.pinned = pinned                  # unverified context: data channels must match the control channel

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
            if self.pinned and not tlsutil.same_peer(conn, self.sock):
                conn.close()
                raise tlsutil.PinMismatch("the FTPS data channel shows another certificate than the printer")
        return conn, size


class FtpsClient:
    """with FtpsClient(host, "12345678", cafile=... or pin_file=..., server_name=serial) as f:
           f.listing("/cache/"); f.retr("/cache/x.gcode.3mf", on_chunk)"""

    def __init__(self, host: str, password: str, user: str = "bblp", port: int = 990,
                 cafile: str | None = None, server_name: str | None = None, pin_file: Path | None = None,
                 timeout: float = 20.0, allow_plain_data: bool = False, prot_c: bool = False, log=None):
        """allow_plain_data: fall back to PROT C (plain data channel) when the data channel's TLS
        fails - for printers known to need it (A1 / A1 mini). prot_c: start with it (learned before)."""
        self.host, self.port, self.user, self.password = host, port, user, password
        self.cafile, self.server_name, self.pin_file = cafile, server_name, pin_file
        self.timeout = timeout
        self.allow_plain_data = allow_plain_data
        self.prot_c = prot_c and allow_plain_data  # learned: this printer's data channel needs PROT C
        self.log = log
        self.ftp: _ImplicitFTPS | None = None

    # ------------------------------------------------------------------ session
    def open(self):
        pinned = not self.cafile and self.pin_file is not None
        ftp = _ImplicitFTPS(tlsutil.context(self.cafile), self.server_name, self.timeout, pinned)
        try:
            ftp.connect(self.host, self.port)
            if pinned:
                tlsutil.pin(ftp.sock, self.pin_file, store=False)   # before the access code goes out
            ftp.login(self.user, self.password)
            if pinned:
                tlsutil.pin(ftp.sock, self.pin_file)          # a first pin only once the login worked
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
    def listing(self, folder: str = "/", want=None) -> list[tuple[str, int | None, float | None]]:
        """[(path, size, modification time)] of the files in `folder`. `want`: only these file names
        (a set of lower-case names, or a function name -> bool) - the modification time costs one
        MDTM per file, so only for those."""
        lines: list[str] = []

        def run():
            lines.clear()                         # a retry starts over (no doubled entries)
            self.ftp.voidcmd("TYPE A")
            conn = self.ftp.transfercmd(f"LIST {folder}")
            data = b""
            try:
                while chunk := conn.recv(65536):
                    data += chunk
                    if len(data) > 16 << 20:
                        raise ftplib.error_proto("directory listing too long")
            finally:
                conn.close()                      # no unwrap() (ftplib's retrlines does one): it can hang
            self.ftp.voidresp()
            lines.extend(ln.rstrip("\r") for ln in data.decode(self.ftp.encoding, "replace").split("\n") if ln)

        self._data(run)
        out = []
        for ln in lines:
            parts = ln.split(None, 8)
            if len(parts) < 9 or ln.startswith("d"):
                continue
            name = parts[8]
            if want is not None and not (want(name) if callable(want) else name.lower() in want):
                continue
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
        """A data-channel operation; when its TLS fails, once more with PROT C - only on printers
        that need it (allow_plain_data), and never after a certificate error."""
        try:
            run()
        except (ssl.SSLCertVerificationError, tlsutil.PinMismatch):
            raise
        except (ssl.SSLError, socket.timeout, ConnectionResetError) as e:
            if self.prot_c or not self.allow_plain_data or not can_retry():
                raise
            self.prot_c = True
            self.close()
            self.open()
            try:
                run()
            except Exception:
                self.prot_c = False
                raise e
            if self.log:
                self.log(f"FTPS: the data channel's TLS failed ({e}) - this printer's files come over a plain "
                         f"data channel (PROT C, as A1 printers need)")
