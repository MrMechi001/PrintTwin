"""Prusa Connect (opt-in): a job the printer doesn't hand out (sent via Prusa Connect) comes from
Prusa Connect - against a fake Connect + Prusa account server on localhost.

Covers: the pasted sign-in address, token expiry and refresh (the refresh token is renewed and saved),
the printer list (only what is needed is kept - it holds the printers' API keys), finding the job by
the printer's job id or file name, the file download, and PrusaLinkPrinter falling back to Connect
on the printer's 404 (and keeping the 404 when Connect can't help)."""
import base64
import json
import sys
import tempfile
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import connect as C  # noqa: E402
from printtwin.printer import PrusaLinkPrinter  # noqa: E402

JOB = ROOT / "tests" / "fixtures" / "sample.bgcode"
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def jwt(exp: float, n: int) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp, 'type': 'access', 'n': n})}.sig"


DATA = JOB.read_bytes()
HASH = "ab12cd"


class Fake(BaseHTTPRequestHandler):
    access = jwt(time.time() + 3600, 1)
    refresh = "refresh-1"
    refreshed = 0
    jobs = [{"name": "Other", "sn": "SN-OTHER", "team_id": 9, "api_key": "SECRET-OTHER",
             "job_info": {"origin_id": 7, "display_name": "x.bgcode", "hash": "zz"}},
            {"name": "MK4", "sn": "SN-MK4", "team_id": 5, "api_key": "SECRET-MK4", "prusalink_api_key": "SECRET",
             "job_info": {"origin_id": 440, "display_name": "ruben_part.bgcode", "hash": HASH}}]

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(p.split("=", 1) for p in self.rfile.read(n).decode().split("&"))
        if self.path == "/o/token/" and form.get("grant_type") == "refresh_token" and form.get("refresh_token") == Fake.refresh:
            Fake.refreshed += 1
            Fake.access = jwt(time.time() + 3600, 1 + Fake.refreshed)
            Fake.refresh = f"refresh-{1 + Fake.refreshed}"            # renewed on every use
            return self._send(200, json.dumps({"access_token": Fake.access, "refresh_token": Fake.refresh,
                                               "token_type": "Bearer"}).encode())
        self._send(400, b'{"error": "invalid_grant"}')

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {Fake.access}":
            return self._send(401, b"{}")
        if self.path == "/app/printers":
            return self._send(200, json.dumps({"printers": Fake.jobs}).encode())
        if self.path == f"/app/teams/5/files/{HASH}/raw":
            return self._send(200, DATA, "application/octet-stream")
        self._send(404, b"{}")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
tmp = Path(tempfile.mkdtemp(prefix="printtwin-connect-"))
tokens = tmp / "connect.local.json"

# ---------------------------------------------------------------- sign-in helpers
check("pasted callback address -> code", C.code_from(" https://connect.prusa3d.com/login/auth-callback?code=AbC&state=x ") == "AbC")
check("pasted code alone", C.code_from("AbC") == "AbC")
try:
    C.code_from("https://connect.prusa3d.com/login/auth-callback?error=access_denied")
    check("refused sign-in says so", False)
except C.ConnectError as e:
    check("refused sign-in says so", "access_denied" in str(e))
v, ch = C.pkce()
check("PKCE: S256 challenge, URL has client, redirect, challenge", 43 <= len(v) <= 128 and
      all(s in C.authorize_url(ch, "st") for s in (C.CLIENT_ID, "code_challenge=" + ch, "S256", "auth-callback")))
check("JWT expiry read", abs(C.jwt_exp(jwt(1234.0, 0)) - 1234.0) < 1e-6 and C.jwt_exp("garbage") == 0.0)
try:
    C.Connect(tmp / "missing.json")
    check("not signed in: tells how", False)
except C.ConnectError as e:
    check("not signed in: tells how", "connect-login" in str(e))

# ---------------------------------------------------------------- expired access token -> refresh, saved
C.save_tokens(tokens, {"access_token": jwt(time.time() - 10, 0), "refresh_token": "refresh-1", "id_token": "drop-me"})
cn = C.Connect(tokens, base=base, token_url=base + "/o/token/")
pr = cn.printers()
saved = json.loads(tokens.read_text(encoding="utf-8"))
check("expired access token refreshed once", Fake.refreshed == 1)
check("renewed refresh token saved, nothing else kept", saved.get("refresh_token") == "refresh-2"
      and set(saved) <= {"access_token", "refresh_token", "token_type"})
check("printer list keeps no API keys", "SECRET" not in json.dumps(pr), json.dumps(pr)[:120])

# ---------------------------------------------------------------- find the job
check("job found by the printer's job id", cn.find_job({"id": 440, "display_name": "RUBEN_~1.BGC"})[:2] == (5, HASH))
check("job found by file name", cn.find_job({"id": None, "display_name": "Ruben_Part.bgcode"})[:2] == (5, HASH))
try:
    cn.find_job({"id": 1, "display_name": "nothing.bgcode"})
    check("unknown job: ConnectError", False)
except C.ConnectError:
    check("unknown job: ConnectError", True)
got = bytearray()
n = cn.stream(5, HASH, lambda d, k, t: got.extend(d))
check("file downloaded whole", n == len(DATA) and bytes(got) == DATA, f"{n} bytes")

# ---------------------------------------------------------------- PrusaLinkPrinter: 404 -> Connect
p = PrusaLinkPrinter({"id": "mk4", "host": "127.0.0.1:9", "api_key": "k"}, tmp / "cache", tmp / "incoming")


def refuse(path, on_chunk):
    raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)


p.client.stream = refuse
job = {"id": 440, "display_name": "ruben_part.bgcode", "download": "/usb/RUBEN_~1.BGC", "size": 0}
try:
    p._stream(job, lambda d, k, t: None)
    check("without Connect the printer's 404 stays", False)
except urllib.error.HTTPError as e:
    check("without Connect the printer's 404 stays", e.code == 404)
p.connect = cn
got = bytearray()
p._stream(job, lambda d, k, t: got.extend(d))
check("printer 404 -> file from Prusa Connect", bytes(got) == DATA)
try:
    p._stream({**job, "id": 1, "display_name": "nothing.bgcode"}, lambda d, k, t: None)
    check("Connect can't help -> the printer's 404 (retried later)", False)
except urllib.error.HTTPError as e:
    check("Connect can't help -> the printer's 404 (retried later)", e.code == 404)
Fake.access = "revoked"                                  # signed out elsewhere: 401
cn.tokens["access_token"] = jwt(time.time() + 3600, 99)
cn._printers = (0.0, [])
try:
    cn.printers()
    check("refused token: asks to sign in again", False)
except C.ConnectError as e:
    check("refused token: asks to sign in again", "connect-login" in str(e))

srv.shutdown()
print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
