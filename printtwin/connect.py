"""Prusa Connect (opt-in): the job file of a print that came via Prusa Connect.

An MK4 / MK3.9 / CORE One / XL / MINI doesn't hand out the file Prusa Connect sent while it prints
it (HTTP 404 on the download, the file listed read-only). Prusa Connect keeps the file in the team's
storage: signed in with the user's Prusa account, PrintTwin finds the printer's current job there
and downloads it - the same way as from the printer, the model builds while it arrives.

Unofficial: these are the calls the connect.prusa3d.com web app makes (OAuth 2 authorization code
with PKCE at account.prusa3d.com, the web app's client id), Prusa may change them. PrintTwin never
sees the account password: you sign in on Prusa's own page (`python run.py connect-login`), only the
tokens are kept - in connect.local.json next to the config (access token ~1 h, refresh token ~30 days,
renewed on every use). Off unless the config says [connect] enabled = true. Only reads: the printer
list (to find the job) and the job's file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

BASE = "https://connect.prusa3d.com"
AUTH_URL = "https://account.prusa3d.com/o/authorize/"
TOKEN_URL = "https://account.prusa3d.com/o/token/"
CLIENT_ID = "MRHTlZhZqkNrrQ6FUPtjyusAz8nc59ErHXP8XkS4"       # the connect.prusa3d.com web app
REDIRECT_URI = "https://connect.prusa3d.com/login/auth-callback"
USER_AGENT = f"PrintTwin/{__version__}"
TIMEOUT = 20.0
PRINTERS_TTL_S = 10.0          # the printer list (with the current jobs) is asked at most this often


class ConnectError(Exception):
    """Prusa Connect couldn't give the file (not signed in, job not found, ...)."""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)"""
    verifier = _b64url(os.urandom(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorize_url(challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})


def code_from(pasted: str) -> str:
    """The authorization code from what the user pasted: the whole callback address, or the code."""
    s = pasted.strip().strip('"').strip("'")
    if "?" in s or "code=" in s:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query or s.split("?", 1)[-1])
        if q.get("error"):
            raise ConnectError(f"Prusa refused the sign-in: {q['error'][0]}")
        if not q.get("code"):
            raise ConnectError("no code= in that address")
        return q["code"][0]
    if not s:
        raise ConnectError("nothing pasted")
    return s


def jwt_exp(token: str) -> float:
    """Expiry (unix time) of a JWT - read, not verified (Prusa checks it); 0 if unreadable."""
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("exp") or 0)
    except (IndexError, ValueError, TypeError):
        return 0.0


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": USER_AGENT,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace")
        e.close()
        raise ConnectError(f"Prusa account: HTTP {e.code} {detail}".strip()) from None


def exchange(code: str, verifier: str, token_url: str = TOKEN_URL) -> dict:
    return _post_form(token_url, {"grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code,
                                  "code_verifier": verifier, "redirect_uri": REDIRECT_URI})


def save_tokens(path: Path, tokens: dict):
    keep = {k: tokens[k] for k in ("access_token", "refresh_token", "token_type") if tokens.get(k)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


class Connect:
    """Signed-in Prusa Connect session: finds a printer's current job and streams its file."""

    def __init__(self, tokens_path: Path, base: str = BASE, token_url: str = TOKEN_URL):
        self.path = Path(tokens_path)
        self.base = base.rstrip("/")
        self.token_url = token_url
        self.lock = threading.Lock()
        self._printers: tuple[float, list] = (0.0, [])
        try:
            self.tokens = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConnectError(f"not signed in to Prusa Connect - run: python run.py connect-login") from None
        except ValueError:
            raise ConnectError(f"{self.path.name} is damaged - sign in again: python run.py connect-login") from None
        if not self.tokens.get("refresh_token") and not self.tokens.get("access_token"):
            raise ConnectError(f"no tokens in {self.path.name} - run: python run.py connect-login")

    # ---------------------------------------------------------------- tokens
    def _access(self) -> str:
        with self.lock:
            tok = self.tokens.get("access_token") or ""
            if tok and jwt_exp(tok) > time.time() + 60:
                return tok
            ref = self.tokens.get("refresh_token")
            if not ref:
                raise ConnectError("the Prusa Connect sign-in ran out - run: python run.py connect-login")
            new = _post_form(self.token_url, {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                              "refresh_token": ref})
            if not new.get("access_token"):
                raise ConnectError("Prusa account gave no access token - run: python run.py connect-login")
            self.tokens = {**self.tokens, **new}           # the refresh token is renewed on every use
            save_tokens(self.path, self.tokens)
            return self.tokens["access_token"]

    def _open(self, path: str, timeout: float = TIMEOUT):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": USER_AGENT,
                                                                "Authorization": f"Bearer {self._access()}"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code in (401, 403):
                raise ConnectError(f"Prusa Connect refused the sign-in (HTTP {e.code}) - "
                                   f"run: python run.py connect-login") from None
            raise ConnectError(f"Prusa Connect: HTTP {e.code} for {path.split('?')[0]}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ConnectError(f"Prusa Connect not reachable: {getattr(e, 'reason', e)}") from None

    # ---------------------------------------------------------------- printers and jobs
    def printers(self) -> list:
        t, cached = self._printers
        if time.time() - t < PRINTERS_TTL_S:
            return cached
        with self._open("/app/printers") as r:
            data = json.loads(r.read())
        out = data.get("printers", []) if isinstance(data, dict) else data
        # keep only what is needed: the list also holds the printers' API keys
        out = [{"name": p.get("name"), "sn": p.get("sn"), "team_id": p.get("team_id"),
                "job_info": p.get("job_info") or {}} for p in out if isinstance(p, dict)]
        self._printers = (time.time(), out)
        return out

    def find_job(self, job: dict, serial: str | None = None) -> tuple[int, str, str]:
        """(team id, file hash, name) of the Connect job that is this PrusaLink job: same printer
        (serial number, when known) and the same job id on the printer or the same file name."""
        want_id = str(job.get("id")) if job.get("id") is not None else None
        want_name = (job.get("display_name") or "").strip().lower()
        best = None
        for p in self.printers():
            ji = p["job_info"]
            if not ji.get("hash") or p.get("team_id") is None:
                continue
            if serial and p.get("sn") and p["sn"] != serial:
                continue
            same_id = want_id is not None and str(ji.get("origin_id")) == want_id
            same_name = bool(want_name) and (ji.get("display_name") or "").strip().lower() == want_name
            score = 2 * same_id + same_name + (1 if serial and p.get("sn") == serial else 0)
            if (same_id or same_name) and (best is None or score > best[0]):
                best = (score, p["team_id"], ji["hash"], ji.get("display_name") or "")
        if best is None:
            raise ConnectError(f"Prusa Connect has no running job {job.get('display_name') or want_id!r}")
        return best[1], best[2], best[3]

    def stream(self, team_id: int, file_hash: str, on_chunk: Callable[[bytes, int, int], None],
               chunk: int = 64 * 1024) -> int:
        n = 0
        with self._open(f"/app/teams/{int(team_id)}/files/{urllib.parse.quote(str(file_hash))}/raw",
                        timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            while data := r.read1(chunk):
                n += len(data)
                on_chunk(data, n, total)
        if total and n != total:
            raise ConnectError(f"Prusa Connect download ended after {n} of {total} bytes")
        return n


def login(tokens_path: Path, open_browser: bool = True, ask: Callable[[str], str] = input) -> None:
    """Sign in on Prusa's page in the browser, paste the address it ends on; keeps the tokens."""
    verifier, challenge = pkce()
    url = authorize_url(challenge, _b64url(os.urandom(12)))
    print("PrintTwin <-> Prusa Connect (unofficial, read-only: finds the running job and downloads its file)\n"
          "1. Sign in to your Prusa account on the page that opens now (or open this address):\n\n"
          f"   {url}\n\n"
          "2. The browser passes connect.prusa3d.com/login/auth-callback?code=... and Prusa Connect\n"
          "   moves on at once: open the browser history (Ctrl+H), copy that auth-callback address\n"
          "   (the newest one with 'state=' in it) and paste it here - within a minute, the code expires.\n"
          "   Already signed in? Then the page skips the password: just copy the address from the history.\n"
          "   PrintTwin never sees your password; it keeps only the sign-in tokens, in\n"
          f"   {tokens_path}\n")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    tokens = exchange(code_from(ask("Address (or code): ")), verifier)
    if not tokens.get("refresh_token"):
        raise ConnectError("Prusa account gave no refresh token")
    save_tokens(Path(tokens_path), tokens)
    print("Signed in. Turn it on in the config:  [connect]  enabled = true   - then restart PrintTwin.")
