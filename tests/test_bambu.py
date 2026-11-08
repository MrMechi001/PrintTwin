"""Bambu Lab printers, end to end: simulated printer (tools/sim_bambu.py, MQTT + FTPS over TLS) -> PrintTwin.

usage: python -I tests/test_bambu.py        (needs the openssl tool for the simulator's certificates)
Read-only status over MQTT, the job file over FTPS (P1 style: there from the start; X1 style: shows up
late and still growing), the slicer hand-over when the printer prints from internal storage, the
estimated progress (no position from the printer), certificate pinning and a wrong access code;
a second print of the same job (re-sliced), an earlier print's file under the same name, a job
name with '/' and a mistyped serial number. The job logic in detail: tests/test_bambu_adapter.py.
"""
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bambu_job(layers=30, x0=100) -> bytes:
    """A small job in Bambu Studio's G-code style (comments, M73 marks), written for this test."""
    out = ["; HEADER_BLOCK_START", "; generated for PrintTwin's tests", f"; total layer number: {layers}",
           "; HEADER_BLOCK_END", "", "; CONFIG_BLOCK_START", "; filament_colour = #00AE42", "; filament_type = PLA",
           "; nozzle_diameter = 0.4", "; printable_area = 0x0,256x0,256x256,0x256",
           "; printer_model = Bambu Lab P1S", "; printer_structure = corexy", "; CONFIG_BLOCK_END", "",
           "M73 P0 R10", "G90", "M83", "T1000"]
    pct = 0
    for k in range(layers):
        z = 0.2 * (k + 1)
        out += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z:.1f}", "; LAYER_HEIGHT: 0.2", f"M73 L{k + 1}", f"G1 Z{z:.1f}",
                "; FEATURE: Outer wall", "; LINE_WIDTH: 0.42"]
        for i in range(30):
            d = 5 + i
            out += [f"G1 X{x0 + d} Y100 E0.05", f"G1 X{x0 + d} Y{100 + d} E0.05", f"G1 X{x0} Y{100 + d} E0.05",
                    f"G1 X{x0} Y100 E0.05"]
            if (k * 30 + i) % max(1, layers * 30 // 100) == 0 and pct < 99:
                pct += 1
                out.append(f"M73 P{pct} R{max(0, 10 - pct // 10)}")
    out += ["M73 P100 R0", "M400", ""]
    return "\n".join(out).encode()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def run_sim(job: Path, *extra):
    port, mq, ft = free_port(), free_port(), free_port()
    proc = subprocess.Popen([sys.executable, "-I", "-u", str(ROOT / "tools" / "sim_bambu.py"), str(job),
                             "--port", str(port), "--mqtt-port", str(mq), "--ftps-port", str(ft), *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc, f"http://127.0.0.1:{port}"


def wait(base, cond, timeout=40.0):
    t0, st = time.time(), None
    while time.time() - t0 < timeout:
        try:
            st = get(f"{base}/api/p/sim/state")
            if cond(st):
                return st
        except OSError:
            pass
        time.sleep(0.25)
    return st


def stop(proc) -> str:
    proc.terminate()
    try:
        return proc.communicate(timeout=10)[0].decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.communicate()[0].decode("utf-8", "replace")


def p1_style(job: Path):
    proc, base = run_sim(job, "--serial", "01P00A000000001", "--duration", "16", "--heat", "2")
    try:
        st = wait(base, lambda s: s.get("online"))
        check("P1S: online over MQTT (TLS verified against the CA, CN = serial)", bool(st and st.get("online")))
        check("P1S: model from the serial number (01P = P1S) - CoreXY, 256 mm",
              st.get("printer_type") == "bambu/C12" and st.get("model") == "corexy"
              and (st.get("volume") or {}).get("max") == [256.0, 256.0, 256.0], f"{st.get('printer_type')} {st.get('model')}")
        st = wait(base, lambda s: s.get("toolpath_complete"))
        check("P1S: job file over FTPS (.gcode.3mf in /), toolpath built", bool(st and st.get("toolpath_complete")),
              f"{st and st.get('toolpath_count')} segments")
        info = get(f"{base}/api/p/sim/toolpath.json")
        check("P1S: Bambu G-code read (layers, M73 marks, colour)", len(info["layers"]) == 30
              and len(info["layer_marks"]) == 30 and info.get("filament_colour") == "#00AE42", f"{len(info['layers'])} layers")
        wait(base, lambda s: s.get("state") == "PRINTING" and (s.get("printed") or 0) > 0)
        samples, t0 = [], time.time()
        while time.time() - t0 < 6:
            s = get(f"{base}/api/p/sim/state")
            samples.append((s.get("printed") or 0, s.get("progress"), s["metrics"].get("estimated")))
            time.sleep(0.4)
        pr = [p for p, _, _ in samples]
        check("P1S: progress estimated and moving on, never back", pr[-1] > pr[0] > 0 and all(b >= a for a, b in zip(pr, pr[1:]))
              and all(e for _, _, e in samples), f"{pr[0]:.0f} -> {pr[-1]:.0f}")
        layers, ok, seen = info["layers"], 0, 0
        for _ in range(8):
            s = get(f"{base}/api/p/sim/state")
            if s.get("state") == "PRINTING" and s.get("layer"):
                li = max(i for i, (_, start) in enumerate(layers) if start <= s["printed"]) + 1
                seen += 1
                ok += abs(li - s["layer"]) <= 1          # the printer's report may be a moment older
            time.sleep(0.5)
        check("P1S: the estimate stays in the layer the printer reports", seen >= 4 and ok == seen, f"{ok} of {seen}")
        st = wait(base, lambda s: s.get("state") == "FINISHED", 30)
        check("P1S: FINISH -> FINISHED, all printed", st.get("state") == "FINISHED" and st.get("printed") == info["count"],
              f"{st.get('state')} {st.get('printed')}/{info['count']}")
    finally:
        out = stop(proc)
    check("P1S: the job file came from the printer", "job file on the printer: /" in out, "see log" if "job file" not in out else "")


def x1_style(job: Path):
    proc, base = run_sim(job, "--serial", "00M00A000000001", "--style", "x1", "--duration", "40", "--heat", "1",
                         "--late", "2")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete"), 40)
        info = get(f"{base}/api/p/sim/toolpath.json") if st and st.get("toolpath_complete") else {}
        check("X1C: file shows up late in /cache and grows - taken only once complete",
              bool(st and st.get("toolpath_complete")) and len(info.get("layers", [])) == 30,
              f"{st and st.get('toolpath_count')} segments")
        check("X1C: model X1 Carbon from the serial (00M)", st.get("printer_type") == "bambu/BL-P001", st.get("printer_type"))
    finally:
        out = stop(proc)
    check("X1C: found in /cache", "job file on the printer: /cache/" in out)


def internal_with_handover(job: Path):
    proc, base = run_sim(job, "--serial", "094000000000001", "--internal", "--handover", "--duration", "30")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete"), 25)
        check("H2D, internal storage: no file over FTPS, the slicer's hand-over is used",
              bool(st and st.get("toolpath_complete")), f"{st and st.get('toolpath_count')} segments")
        check("H2D: model from the serial (094 = H2D) - 350 x 320", (st.get("volume") or {}).get("max") == [350.0, 320.0, 325.0],
              str(st.get("volume")))
    finally:
        out = stop(proc)
    check("H2D: no FTPS download happened", "job file on the printer" not in out)


def pin_mode(job: Path):
    proc, base = run_sim(job, "--pin", "--duration", "20")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete"), 30)
        check("no CA: certificate pinned on first contact, job loaded", bool(st and st.get("toolpath_complete")))
        pins = sorted((ROOT / "logs" / "sim_bambu" / "cache" / "tls").glob("*.sha256"))
        check("pin files for MQTT and FTPS", [p.name for p in pins] == ["01P00A000000001-ftps.sha256", "01P00A000000001-mqtt.sha256"],
              str([p.name for p in pins]))
    finally:
        stop(proc)


def wrong_code(job: Path):
    proc, base = run_sim(job, "--wrong-code", "--duration", "20")
    try:
        time.sleep(4)
        st = get(f"{base}/api/p/sim/state")
        check("wrong access code: offline", st.get("online") is False)
    finally:
        out = stop(proc)
    check("wrong access code: the log says why", "not authorized" in out, "see log" if "not authorized" not in out else "")


def second_print(job: Path, job2: Path):
    proc, base = run_sim(job, "--duration", "8", "--heat", "1", "--repeat", "--then", str(job2), "--gap", "3")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete") and s.get("state") == "PRINTING", 30)
        first = (st.get("job") or {}).get("id")
        wait(base, lambda s: s.get("state") == "FINISHED", 30)
        st = wait(base, lambda s: (s.get("job") or {}).get("id") not in (None, first) and s.get("toolpath_complete")
                  and s.get("state") == "PRINTING" and (s.get("printed") or 0) > 0, 30)
        info = get(f"{base}/api/p/sim/toolpath.json")
        check("second print of the job (same name, file name, task id): a new print with the re-sliced file",
              st and (st.get("job") or {}).get("id") != first and len(info["layers"]) == 20,
              f"job {(st.get('job') or {}).get('id')} (first {first}), {len(info['layers'])} layers")
        check("... starting over, not 'all printed'", 0 < (st.get("printed") or 0) < info["count"],
              f"{st.get('printed')}/{info['count']}")
    finally:
        stop(proc)


def stale_file(job: Path, old: Path):
    proc, base = run_sim(job, "--serial", "00M00A000000001", "--style", "x1", "--stale", str(old), "--duration", "30",
                         "--heat", "2", "--late", "4")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete"), 40)
        info = get(f"{base}/api/p/sim/toolpath.json") if st and st.get("toolpath_complete") else {}
        check("X1C: an earlier print's file under the same name is not shown - this print's is",
              len(info.get("layers", [])) == 30, f"{len(info.get('layers', []))} layers")
    finally:
        out = stop(proc)
    check("... the old file was never downloaded", out.count("job file on the printer") == 1, out.count("job file on the printer"))


def odd_name(job: Path):
    proc, base = run_sim(job, "--name", "Hase / Osterhase", "--duration", "20")
    try:
        st = wait(base, lambda s: s.get("toolpath_complete"), 30)
        check("job name with '/': the file the printer stored ('2f') is found", bool(st and st.get("toolpath_complete")))
    finally:
        stop(proc)


def wrong_serial(job: Path, *mode):
    proc, base = run_sim(job, "--config-serial", "01P00A0000000O1", "--duration", "20", *mode)
    what = "pinned certificate: the printer refuses the topic" if mode else "CA: the certificate is for another serial"
    try:
        time.sleep(5)
        st = get(f"{base}/api/p/sim/state")
        check(f"mistyped serial number ({what}): offline", st.get("online") is False)
    finally:
        out = stop(proc)
    check("... and the log says to check the serial number", "check the serial number" in out,
          "" if "check the serial" in out else [ln for ln in out.splitlines() if "MQTT" in ln][:2])


def unit_pin():
    from printtwin import tlsutil

    class FakeSock:
        def __init__(self, der):
            self.der = der

        def getpeercert(self, binary_form=False):
            return self.der
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.sha256"
        tlsutil.pin(FakeSock(b"cert-A"), f)
        check("pin: first contact stores the fingerprint", f.read_text().strip() == hashlib.sha256(b"cert-A").hexdigest())
        tlsutil.pin(FakeSock(b"cert-A"), f)
        try:
            tlsutil.pin(FakeSock(b"cert-B"), f)
            check("pin: another certificate is refused", False)
        except tlsutil.PinMismatch as e:
            check("pin: another certificate is refused, with what to do", "delete" in str(e), str(e)[:60])


unit_pin()
if not shutil.which("openssl"):
    print("SKIP the simulated printer runs (no openssl tool for its certificates)")
else:
    with tempfile.TemporaryDirectory(prefix="pt_bambu_") as d:
        job = Path(d) / "Cube.gcode"
        job.write_bytes(bambu_job())
        job2 = Path(d) / "Cube_v2.gcode"
        job2.write_bytes(bambu_job(20, x0=120))
        p1_style(job)
        second_print(job, job2)
        stale_file(job, job2)
        odd_name(job)
        wrong_serial(job)
        wrong_serial(job, "--pin")
        x1_style(job)
        internal_with_handover(job)
        pin_mode(job)
        wrong_code(job)
print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
