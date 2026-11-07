"""End-to-end: simulated PrusaLink printer -> bridge -> HTTP API, for both loading paths.

usage: python -I tests/test_bridge.py
Starts tools/sim_printer.py on free local ports (no real printer needed).
"""
import gzip
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "tests" / "fixtures" / "sample.bgcode"
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, gz=False):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"} if gz else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return r.headers, body


def run(handover: bool):
    port, pport = free_port(), free_port()
    args = [sys.executable, "-I", "-u", str(ROOT / "tools" / "sim_printer.py"), str(JOB), "--kbps", "40",
            "--heat", "1", "--layer", "0.5", "--port", str(port), "--printer-port", str(pport)]
    if handover:
        args.append("--handover")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    label = "hand-over" if handover else "progressive"
    try:
        t0, seen_partial, state = time.time(), False, None
        while time.time() - t0 < 60:
            try:
                state = json.loads(get(f"{base}/api/p/sim/state")[1])
            except OSError:
                time.sleep(0.3)
                continue
            if state.get("toolpath_count") and not state.get("toolpath_complete"):
                seen_partial = True
            if state.get("toolpath_complete"):
                break
            time.sleep(0.2)
        took = time.time() - t0
        check(f"{label}: toolpath complete", bool(state and state.get("toolpath_complete")), f"{took:.1f} s")
        if not handover:
            check("progressive: model visible before the download finished", seen_partial)
        else:
            check("hand-over: no download needed (< 10 s)", took < 10, f"{took:.1f} s")
        info = json.loads(get(f"{base}/api/p/sim/toolpath.json")[1])
        n = info["count"]
        hdr, binary = get(f"{base}/api/p/sim/toolpath.bin", gz=True)
        check(f"{label}: toolpath.bin matches count", len(binary) == 33 * n + (-33 * n % 4)
              and int(hdr["X-Toolpath-Count"]) == n, f"{n} segments, {len(info['layers'])} layers")
        hdr, part = get(f"{base}/api/p/sim/toolpath.bin?from={n - 100}")
        check(f"{label}: toolpath.bin?from= returns the tail", len(part) == 33 * 100 + (-33 * 100 % 4)
              and hdr["X-Toolpath-From"] == str(n - 100))
        if not handover:
            hdr, html = get(f"{base}/")
            check("static: viewer page served", b"PrintTwin" in html and "text/html" in hdr["Content-Type"])
            hdr, glb = get(f"{base}/models/mk4.glb", gz=True)
            check("static: MK4 model served (gzip)", glb[:4] == b"glTF" and hdr["Content-Type"] == "model/gltf-binary",
                  f"{len(glb) / 1e6:.1f} MB")
            code = None
            try:
                get(f"{base}/../config.local.toml")
            except urllib.error.HTTPError as e:
                code = e.code
            check("static: no path traversal out of web/", code == 404)
    finally:
        proc.terminate()
        proc.wait(10)


run(handover=False)
run(handover=True)
print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
