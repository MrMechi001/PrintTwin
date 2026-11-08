"""TLS for printers with their own certificates (Bambu Lab: MQTT on 8883, FTPS on 990).

Two ways to know it is really the printer:
  "ca"  - a CA file (Bambu's CAs ship in printtwin/certs/bambu.pem; or the simulator's): the
          chain is verified, and the certificate's name (Bambu: the printer's serial number is
          its CN) is compared with the configured serial - by check_name(), not by OpenSSL's
          host-name check, which trips over printers reached by IP address.
  "pin" - no CA, or a printer whose chain the bundled CAs don't cover (a model newer than them):
          trust on first use. The SHA-256 of the printer's certificate is stored on the first
          connection; a different certificate later is refused (someone in between, or a
          replaced printer - delete the pin file then). A CA-verified login stores the
          fingerprint too, so falling back to "pin" never trusts an unknown certificate where a
          verified one was seen before.
Both cap TLS at 1.2: some Bambu firmware (P2S) never answers a TLS 1.3 handshake.
"""
from __future__ import annotations

import hashlib
import os
import re
import ssl
from pathlib import Path


class PinMismatch(ssl.SSLError):
    def __str__(self):                  # ssl.SSLError would show the tuple of its arguments
        return str(self.args[0]) if self.args else "certificate pin mismatch"


class NameMismatch(PinMismatch):
    """A certificate from the right CA, but for another printer (serial number)."""


def context(cafile: str | None = None) -> ssl.SSLContext:
    """A client context: verifying the chain against `cafile` (the name: check_name), or
    unverified for pinning."""
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
        # Bambu's CA lacks extensions Python 3.13+ demands under VERIFY_X509_STRICT
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        ctx.check_hostname = False                       # the serial number: check_name() after the handshake
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                  # checked by pin() right after the handshake
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def fingerprint(sock: ssl.SSLSocket) -> str:
    der = sock.getpeercert(binary_form=True)
    if not der:
        raise PinMismatch("the printer sent no certificate")
    return hashlib.sha256(der).hexdigest()


def peer_name(sock: ssl.SSLSocket) -> str | None:
    """The CN of a verified peer certificate."""
    cert = sock.getpeercert() or {}
    for rdn in cert.get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                return str(v)
    return None


def check_name(sock: ssl.SSLSocket, serial: str) -> str | None:
    """A verified Bambu certificate must be the configured printer's: its CN is the serial number.
    A CN that is clearly another serial number is refused; one in an unknown form is let through
    (the chain is Bambu's) and returned, so the caller can say so."""
    cn = peer_name(sock)
    if cn is None or cn.strip().upper() == serial.strip().upper():
        return None
    if re.fullmatch(r"[0-9A-Z]{15}", cn.strip().upper()):
        raise NameMismatch(f"the printer here has the serial number {cn}, not {serial} - check the serial "
                           f"number and the host in the config")
    return cn


def _write(pin_file: Path, fp: str):
    pin_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pin_file.with_name(pin_file.name + ".tmp")
    with open(tmp, "w", encoding="ascii") as f:
        f.write(fp + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pin_file)


def remember(sock: ssl.SSLSocket, pin_file: Path):
    """After a CA-verified login: note the certificate's fingerprint (a reset printer's new,
    verified certificate replaces the old one)."""
    fp = fingerprint(sock)
    pin_file = Path(pin_file)
    try:
        if pin_file.is_file() and pin_file.read_text(encoding="ascii", errors="replace").strip() == fp:
            return
        _write(pin_file, fp)
    except OSError:
        pass


def pin(sock: ssl.SSLSocket, pin_file: Path, store: bool = True) -> str:
    """Trust on first use: store the certificate's fingerprint, refuse a different one later.
    Call it right after the handshake, before anything secret (the access code) is sent, with
    store=False (compare only) - and again with store=True once the login worked, so a wrong
    host at the first setup doesn't get pinned."""
    fp = fingerprint(sock)
    pin_file = Path(pin_file)
    try:
        want = pin_file.read_text(encoding="ascii", errors="replace").strip() if pin_file.is_file() else ""
    except OSError as e:
        raise PinMismatch(f"cannot read the certificate pin {pin_file}: {e}") from e
    if not want:                                     # first contact (or an empty file a failed write left)
        if store:
            _write(pin_file, fp)
    elif not re.fullmatch(r"[0-9a-f]{64}", want):
        raise PinMismatch(f"the certificate pin {pin_file} is damaged - delete it (the printer's certificate "
                          f"is then pinned again on the next connection)")
    elif want != fp:
        raise PinMismatch(f"the printer's certificate changed (pinned {want[:16]}..., got {fp[:16]}...) - "
                          f"if the printer was replaced or reset, delete {pin_file}")
    return fp


def same_peer(a: ssl.SSLSocket, b: ssl.SSLSocket) -> bool:
    """Both connections show the same certificate (FTPS: the data channel is the control channel's printer)."""
    return fingerprint(a) == fingerprint(b)
