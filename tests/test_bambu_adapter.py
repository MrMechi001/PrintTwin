"""The Bambu Lab adapter's job logic, offline: printer reports fed in by hand, FTPS faked.

usage: python -I tests/test_bambu_adapter.py      (the FTPS / MQTT parts need the openssl tool)
New print vs reprint, a layer count left over from the last print, hand-overs sliced after the
start or several that fit, a wrong (older) file found and dropped, the plate of a multi-plate
job, the FTPS search's pace and its hint, file names as the printer stores them, a paused print,
certificate pins, the FTPS data channel's checks, and the MQTT reconnect pace.
"""
import io
import json
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from printtwin import bambu as B, incoming, printer as P, tlsutil  # noqa: E402
from printtwin.ftps import FtpsClient  # noqa: E402

failures = 0
P.log = B.log = lambda *a: LOG.append(" ".join(map(str, a)))     # keep the output readable, check the log
LOG: list[str] = []
B.FIND_EARLY_S, B.FIND_EARLY_FOR, B.FIND_LATE_S = 0.2, 1.5, 4.0       # the search's pace, scaled down
B.STABLE_S, B.MISMATCH_S, B.TRUST_AFTER_S = 0.05, 0.4, 0.6


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def gcode(layers: int, x0: float = 100) -> bytes:
    """A small job in Bambu Studio's G-code style, written for this test."""
    out = ["; HEADER_BLOCK_START", f"; total layer number: {layers}", "; HEADER_BLOCK_END",
           "; CONFIG_BLOCK_START", "; printer_model = Bambu Lab P1S", "; filament_colour = #00AE42",
           "; CONFIG_BLOCK_END", "M73 P0 R5"]
    for k in range(layers):
        z = 0.2 * (k + 1)
        out += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z:.1f}", "; LAYER_HEIGHT: 0.2", f"M73 L{k + 1}",
                f"M73 P{int(100 * k / layers)} R{max(0, 5 - k // 4)}", f"G1 Z{z:.1f}", "; FEATURE: Outer wall"]
        for i in range(20):
            out += [f"G1 X{x0 + i} Y100 E0.1", f"G1 X{x0 + i} Y{100 + i} E0.1"]
    return ("\n".join(out + ["M73 P100 R0", ""])).encode()


def three_mf(*plates: bytes) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for i, g in enumerate(plates, 1):
            z.writestr(f"Metadata/plate_{i}.gcode", g)
    return b.getvalue()


class FakeFtps:
    """What _locate / _stream ask of FtpsClient, from a dict {path: (bytes, mtime)}."""

    def __init__(self, files: dict, calls: list, error=None):
        self.files, self.calls, self.prot_c, self.error = files, calls, False, error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def listing(self, folder, want=None):
        self.calls.append((time.time(), folder))
        if self.error:
            raise self.error
        def ok(name):
            return want is None or (want(name) if callable(want) else name.lower() in want)
        return [(p, len(d), mt) for p, (d, mt) in list(self.files.items())
                if p.rsplit("/", 1)[0] + "/" == folder and ok(p.rsplit("/", 1)[1])]

    def retr(self, path, on_chunk, total=0):
        d = self.files[path][0]
        on_chunk(d, len(d), len(d))


class Rig:
    def __init__(self, d: Path, serial="01P00A000000001"):
        self.d, self.files, self.calls, self.error = d, {}, [], None
        (d / "cache").mkdir(exist_ok=True)
        (d / "incoming").mkdir(exist_ok=True)
        self.p = B.BambuPrinter({"id": "t", "host": "127.0.0.1:1", "serial": serial, "access_code": "x"},
                                d / "cache", d / "incoming")
        self.p._mqtt_ok = True
        self.p._ftps = lambda: FakeFtps(self.files, self.calls, self.error)

    def report(self, **f):
        self.p._on_report(json.dumps({"print": f}).encode())
        self.p._last_msg = time.time()

    def poll(self):
        data = self.p._fetch_status()
        self.p._on_status(data)
        return self.p.snapshot()

    def until(self, cond, timeout=8.0, **f):
        t0, s = time.time(), None
        while time.time() - t0 < timeout:
            if f:
                self.report(**f)
            s = self.poll()
            if cond(s):
                return s
            time.sleep(0.05)
        return s

    def handover(self, g: bytes, age: float = 60.0, name="x") -> Path:
        src = self.d / f".{name}.gcode"
        src.write_bytes(g)
        dst = incoming.store(src, str(src), "File", self.d / "incoming")
        side = dst.with_suffix(".json")
        meta = json.loads(side.read_text())
        meta["time"] = time.time() - age
        side.write_text(json.dumps(meta))
        return dst


def layers_of(s):
    return s and s.get("toolpath_complete") and s.get("toolpath_count")


def running(name, total, layer=1, pct=5, gfile=None, **kw):
    return dict(gcode_state="RUNNING", subtask_name=name, gcode_file=gfile or f"{name}.gcode.3mf", task_id="0",
                total_layer_num=total, layer_num=layer, mc_percent=pct, mc_remaining_time=10, **kw)


def tp_layers(r):
    return r.p.tp and r.p.tp.complete and len(r.p.tp.layer_z)


# ---------------------------------------------------------------- new print vs reprint
def reprint():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.files["/Cube.gcode.3mf"] = (three_mf(gcode(10)), time.time() - 30)
        s = r.until(lambda s: tp_layers(r) == 10, **running("Cube", 10, 5, 50))
        first = s["job"]["id"] if s and s.get("job") else None
        r.report(gcode_state="FINISH", mc_percent=100, layer_num=10)
        s = r.until(lambda s: s["state"] == "FINISHED")
        check("finished print: all printed", s["printed"] == r.p.tp.count, f"{s['printed']}/{r.p.tp.count}")
        r.report(gcode_state="PREPARE", mc_percent=0, layer_num=0, gcode_file_prepare_percent="100")
        r.poll()
        s = r.until(lambda s: tp_layers(r) == 10 and (s.get("printed") or 0) > 0, **running("Cube", 10, 1, 3))
        check("the same file printed again (same name, file, task id 0): a new print, starting over",
              s["job"] and s["job"]["id"] != first and 0 < s["printed"] < r.p.tp.count / 3,
              f"job {s['job'] and s['job']['id']} (was {first}), printed {s['printed']}")
        r.report(gcode_state="FINISH", mc_percent=100)
        r.until(lambda s: s["state"] == "FINISHED")
        r.files["/Cube.gcode.3mf"] = (three_mf(gcode(20)), time.time())          # re-sliced, same name
        r.report(gcode_state="PREPARE", mc_percent=0, layer_num=0)
        r.poll()
        s = r.until(lambda s: tp_layers(r) == 20, **running("Cube", 20, 1, 2))
        check("re-sliced under the same name: the new file is loaded", tp_layers(r) == 20, f"{tp_layers(r)} layers")
        pause_keeps = r.p._ctx["seq"]
        r.report(gcode_state="PAUSE")
        r.poll()
        r.until(lambda s: True, timeout=0.3, **running("Cube", 20, 2, 8))
        check("pause and resume: the same print", r.p._ctx["seq"] == pause_keeps)


# ---------------------------------------------------------------- hand-over matching
def stale_total():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.handover(gcode(30), age=900, name="a")
        b = r.handover(gcode(20, x0=150), age=60, name="b")
        r.until(lambda s: tp_layers(r) == 30, **running("Bracket", 30, 10, 40))
        r.report(gcode_state="FINISH", mc_percent=100, layer_num=30)
        r.until(lambda s: s["state"] == "FINISHED")
        # P1 / A1 send only what changed: the new print's first reports still carry the old layer count
        r.report(gcode_state="PREPARE", subtask_name="Clip", gcode_file="Clip.gcode.3mf", mc_percent=0, layer_num=0)
        s = r.until(lambda s: False, timeout=0.4)
        check("new print, layer count still the last print's: no hand-over taken yet", r.p.job is None
              and (s.get("loading") or {}).get("phase") == "search" and s["job"]["name"] == "Clip", str(r.p.job))
        r.report(total_layer_num=20)
        s = r.until(lambda s: tp_layers(r) == 20)
        check("... once the printer reports this print's layer count: its hand-over", tp_layers(r) == 20
              and r.p.job["download"] == f"handover:{b}", f"{tp_layers(r)} layers")


def late_handover():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")          # H2D, internal storage: nothing on FTPS
        r.until(lambda s: False, timeout=0.5, **running("Vase", 30, 4, 12))
        r.handover(gcode(30), age=-300, name="next")          # sliced 5 min after the print began
        s = r.until(lambda s: False, timeout=0.6, **running("Vase", 30, 4, 12))
        check("a hand-over sliced after the print started is not this print's", r.p.job is None and r.p.tp is None
              and s["job"]["name"] == "Vase", str(s["job"]))


def ambiguous_handover():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.handover(gcode(30), age=600, name="p1")
        newest = r.handover(gcode(30, x0=150), age=300, name="p2")
        t0 = time.time()
        s = r.until(lambda s: False, timeout=0.8, **running("Organizer", 30, 2, 5))
        check("two different hand-overs fit: none taken at first (the printer's own file is tried)", r.p.job is None
              and len(r.calls) >= 2, f"{len(r.calls)} FTPS listings")
        s = r.until(lambda s: tp_layers(r) == 30, timeout=6, **running("Organizer", 30, 2, 5))
        check("... and when FTPS has nothing: the newest hand-over", r.p.job and r.p.job["download"] == f"handover:{newest}"
              and time.time() - t0 > B.FIND_EARLY_FOR, f"after {time.time() - t0:.1f} s")
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.handover(gcode(30), age=600, name="p1")
        r.handover(gcode(30, x0=150), age=300, name="p2")
        r.files["/Organizer.gcode.3mf"] = (three_mf(gcode(30, x0=50)), time.time() - 5)
        r.until(lambda s: tp_layers(r) == 30, **running("Organizer", 30, 2, 5))
        check("two hand-overs fit, the printer's file is reachable: that one", r.p.job["download"] == "/Organizer.gcode.3mf",
              r.p.job["download"])


# ---------------------------------------------------------------- the printer's files
def wrong_file():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="00M00A000000001")          # X1: this print's file shows up only into RUNNING
        LOG.clear()
        r.files["/cache/Cube.gcode.3mf"] = (three_mf(gcode(20)), time.time() - 9 * 86400)
        s = r.until(lambda s: False, timeout=0.8, **running("Cube", 30, 1, 1, gfile="/data/Metadata/plate_1.gcode"))
        check("an older same-named file is not taken while this print's may still show up", r.p.job is None)
        s = r.until(lambda s: "not this print's" in " ".join(LOG), timeout=8,
                    **running("Cube", 30, 1, 1, gfile="/data/Metadata/plate_1.gcode"))
        check("... taken at last, but its layer count is wrong: dropped", "not this print's" in " ".join(LOG)
              and s["toolpath_key"] is None, [m for m in LOG if "not this" in m][:1])
        r.files["/cache/Cube.gcode.3mf"] = (three_mf(gcode(30)), time.time())
        s = r.until(lambda s: tp_layers(r) == 30, timeout=8, **running("Cube", 30, 2, 4, gfile="/data/Metadata/plate_1.gcode"))
        check("... and this print's file is taken when it shows up", tp_layers(r) == 30, f"{tp_layers(r)} layers")


def rejected_stays():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="00M00A000000001")          # the wrong file stays on the printer
        LOG.clear()
        r.files["/cache/Cube.gcode.3mf"] = (three_mf(gcode(20)), time.time() - 9 * 86400)
        x1 = running("Cube", 30, 1, 1, gfile="/data/Metadata/plate_1.gcode")
        r.until(lambda s: "not this print's" in " ".join(LOG), timeout=8, **x1)
        n, found = len(r.calls), sum("job file on the printer" in m for m in LOG)
        r.until(lambda s: False, timeout=2.5, **x1)
        check("a dropped file that stays on the printer is not taken again, the search slows down",
              sum("job file on the printer" in m for m in LOG) == found and len(r.calls) - n <= 4,
              f"{len(r.calls) - n} listings in 2.5 s")


def long_prepare():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="00M00A000000001")          # X1 cloud print: a long download in PREPARE
        LOG.clear()
        r.files["/cache/Cube.3mf"] = (three_mf(gcode(30, x0=150)), time.time() - 3 * 86400)    # an older version
        x1 = dict(subtask_name="Cube", gcode_file="/data/Metadata/plate_1.gcode", task_id="0", total_layer_num=30,
                  layer_num=0, mc_percent=0, mc_remaining_time=30)
        r.until(lambda s: False, timeout=B.FIND_EARLY_FOR + 0.5, gcode_state="PREPARE", gcode_file_prepare_percent="40", **x1)
        r.until(lambda s: False, timeout=0.3, gcode_state="RUNNING", gcode_file_prepare_percent="100", **x1)
        r.files["/cache/Cube.3mf"] = (three_mf(gcode(30)), time.time())       # written into RUNNING
        t0 = time.time()
        r.until(lambda s: tp_layers(r) == 30, timeout=5, gcode_state="RUNNING", **x1)
        check("X1, PREPARE longer than the early window: the older same-named file is not taken, this print's is",
              tp_layers(r) == 30 and r.p.tp.bbox()[0] < 140 and time.time() - t0 < 2,
              f"after {time.time() - t0:.1f} s, x from {r.p.tp and r.p.tp.bbox()[0]}")
        check("... and no internal-storage hint", not any("internal storage" in m for m in LOG))


def restart_mid_print():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fields = dict(gcode_state="RUNNING", subtask_name="Vase", gcode_file="/data/Metadata/plate_1.gcode",
                      task_id="0", total_layer_num=60, layer_num=30, mc_percent=50, mc_remaining_time=150)
        r = Rig(d, serial="094000000000001")                 # H2D, internal storage: only the hand-over
        h = r.handover(gcode(60), age=9000 - 1200 + 60)      # the print runs 20 min ahead of its time figures
        r.until(lambda s: tp_layers(r) == 60, timeout=6, **fields)
        check("PrintTwin started mid-print, the time figures off by 20 min: the hand-over is still found",
              tp_layers(r) == 60 and r.p.job["download"] == f"handover:{h}")
        side = h.with_suffix(".json")
        meta = json.loads(side.read_text())
        meta["time"] = time.time() - 30 * 3600                # restart much later: the slice is too old to match
        side.write_text(json.dumps(meta))
        r2 = Rig(d, serial="094000000000001")
        r2.until(lambda s: tp_layers(r2) == 60, timeout=6, **fields)
        check("PrintTwin restarted during the print: the file it used before, whatever its age", tp_layers(r2) == 60)


def printing_file_first():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))                                     # P1 prints /Bracket.gcode.3mf from the card
        r.files["/Bracket.gcode.3mf"] = (three_mf(gcode(12)), time.time() - 20 * 86400)
        r.files["/cache/Bracket.3mf"] = (three_mf(gcode(12, x0=150)), time.time() - 7 * 86400)   # newer, another job
        r.until(lambda s: tp_layers(r) == 12, **running("Bracket", 12, 2, 10))
        check("P1: the file it reports printing wins over a newer same-named one", r.p.job["download"] == "/Bracket.gcode.3mf",
              r.p.job and r.p.job["download"])


def collapsed_name():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="00M00A000000001")
        r.files["/Mini_Hase_Osterhase.gcode.3mf"] = (three_mf(gcode(9)), time.time())
        r.until(lambda s: tp_layers(r) == 9, **running("Mini Hase / Osterhase", 9, 1, 1, gfile="/data/Metadata/plate_1.gcode"))
        check("X1: a file saved with '/' left out and spaces as '_' is found", tp_layers(r) == 9)


def stale_prepare_percent():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.files["/Cube.gcode.3mf"] = (three_mf(gcode(10)), time.time())
        r.until(lambda s: tp_layers(r) == 10, **running("Cube", 10, 5, 50, gcode_file_prepare_percent="100"))
        r.report(gcode_state="FINISH", mc_percent=100)
        r.until(lambda s: s["state"] == "FINISHED")
        n = len(r.calls)
        # a new cloud print: the P1 hasn't sent this print's prepare percent yet, the shadow still says 100
        r.until(lambda s: False, timeout=0.8, gcode_state="PREPARE", subtask_name="Box", gcode_file="Box.3mf", task_id="0")
        check("P1, new print, prepare percent still the last print's: no FTPS search while it downloads",
              len(r.calls) == n, f"{len(r.calls) - n} listings")
        r.until(lambda s: False, timeout=0.3, gcode_state="PREPARE", gcode_file_prepare_percent="35")
        r.files["/cache/Box.3mf"] = (three_mf(gcode(7)), time.time())
        r.until(lambda s: tp_layers(r) == 7, timeout=5, gcode_state="PREPARE", gcode_file_prepare_percent="100",
                total_layer_num=7)
        check("... and the search starts once the printer has the file", tp_layers(r) == 7)


def lost_finish():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.files["/Cube.gcode.3mf"] = (three_mf(gcode(10)), time.time())
        r.until(lambda s: tp_layers(r) == 10, **running("Cube", 10, 9, 95))
        first = r.p._ctx["seq"]
        r.until(lambda s: False, timeout=0.3, gcode_state="PREPARE", mc_percent=0, layer_num=0)   # FINISH went by unseen
        check("back to PREPARE without a FINISH seen: a new print", r.p._ctx["seq"] != first)


def ftps_failure_shown():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.error = ConnectionRefusedError("[WinError 10061] connection refused")
        s = r.until(lambda s: "FTPS failed" in ((s.get("loading") or {}).get("message") or ""), timeout=3,
                    **running("Cube", 10, 1, 1))
        check("FTPS fails: the viewer says so (not the internal-storage hint)",
              "FTPS failed" in ((s.get("loading") or {}).get("message") or ""), (s.get("loading") or {}).get("message"))


def tie_break():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")         # internal storage: only hand-overs
        g1, g2 = gcode(30), gcode(30, x0=150)
        h1 = r.handover(g1.replace(b"M73 P0 R5", b"M73 P0 R47"), age=600, name="p1")   # plate 1: 47 min
        r.handover(g2.replace(b"M73 P0 R5", b"M73 P0 R33"), age=300, name="p2")        # plate 2, newer: 33 min
        f = dict(gcode_state="RUNNING", subtask_name="Organizer", gcode_file="/data/Metadata/plate_1.gcode", task_id="0",
                 total_layer_num=30, layer_num=1, mc_percent=0, mc_remaining_time=47)
        r.until(lambda s: tp_layers(r) == 30, timeout=6, **f)
        check("two equal-height slices, no printer file: the one whose time mark the printer reports (not the newest)",
              r.p.job and r.p.job["download"] == f"handover:{h1}", r.p.job and r.p.job["download"])


def memo_other_print():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        f = dict(gcode_state="RUNNING", subtask_name="Vase", gcode_file="/data/Metadata/plate_1.gcode", task_id="0",
                 total_layer_num=60, layer_num=30, mc_percent=50, mc_remaining_time=150)
        r = Rig(d, serial="094000000000001")
        v1 = r.handover(gcode(60), age=9000 + 60, name="v1")
        r.until(lambda s: tp_layers(r) == 60, timeout=6, **f)
        # hours later the same job (same name, file, task id 0) is re-sliced at the same height and printed
        # while PrintTwin is off; PrintTwin starts 10 min into that print
        memo = json.loads(r.p.memo.read_text())
        memo["start"] -= 6 * 3600
        r.p.memo.write_text(json.dumps(memo))
        v2 = r.handover(gcode(60, x0=150), age=600 + 60, name="v2")
        r2 = Rig(d, serial="094000000000001")
        r2.until(lambda s: tp_layers(r2) == 60, timeout=6, **dict(f, mc_percent=5, layer_num=3, mc_remaining_time=190))
        check("restart during a later print of the same job: not the earlier print's file", r2.p.job
              and r2.p.job["download"] == f"handover:{v2}", r2.p.job and r2.p.job["download"])
        check("(the earlier one was the other)", str(v1) != str(v2))


def pause_keeps_rejection():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")          # H2D, an older 25-layer Vase on the stick
        LOG.clear()
        r.files["/Vase.gcode.3mf"] = (three_mf(gcode(25)), time.time() - 3 * 86400)
        r.until(lambda s: s["state"] == "FINISHED", gcode_state="FINISH", subtask_name="Bowl", gcode_file="/data/Metadata/plate_1.gcode",
                task_id="0", total_layer_num=30, layer_num=30, mc_percent=100, mc_remaining_time=0)
        vase = dict(subtask_name="Vase", gcode_file="/data/Metadata/plate_1.gcode", task_id="0", total_layer_num=30,
                    mc_remaining_time=40)
        r.until(lambda s: False, timeout=0.3, gcode_state="PREPARE", layer_num=0, mc_percent=0, **vase)
        for k in range(60):                                   # running, layers rising: 30 is this print's count
            r.report(gcode_state="RUNNING", layer_num=1 + k // 10, mc_percent=k // 6, **vase)
            r.poll()
            time.sleep(0.05)
            if "not this print's" in " ".join(LOG):
                break
        rejected = "not this print's" in " ".join(LOG)
        r.until(lambda s: False, timeout=B.FIND_LATE_S + 1, gcode_state="PAUSE", layer_num=7, mc_percent=10, **vase)
        check("a file dropped as wrong stays dropped during a pause", rejected and r.p.job is None,
              f"rejected {rejected}, job {r.p.job and r.p.job['download']}")


def ambiguous_ftps_down():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")
        r.error = ConnectionRefusedError("[WinError 10061] connection refused")
        h1 = r.handover(gcode(30).replace(b"M73 P0 R5", b"M73 P0 R47"), age=600, name="p1")
        r.handover(gcode(30, x0=150).replace(b"M73 P0 R5", b"M73 P0 R33"), age=300, name="p2")
        f = dict(gcode_state="RUNNING", subtask_name="Organizer", gcode_file="/data/Metadata/plate_1.gcode", task_id="0",
                 total_layer_num=30, layer_num=1, mc_percent=0, mc_remaining_time=47)
        r.until(lambda s: tp_layers(r) == 30, timeout=6, **f)
        check("several hand-overs fit and FTPS always fails: still decided after the early window",
              r.p.job and r.p.job["download"] == f"handover:{h1}", r.p.job and r.p.job["download"])


def restart_later_slice():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")
        own = r.handover(gcode(60), age=9000 + 60, name="own")      # sliced just before the print
        r.handover(gcode(60, x0=150), age=1800, name="later")       # another job, sliced during the print
        r.until(lambda s: tp_layers(r) == 60, timeout=6, gcode_state="RUNNING", subtask_name="Vase",
                gcode_file="/data/Metadata/plate_1.gcode", task_id="0", total_layer_num=60, layer_num=30,
                mc_percent=50, mc_remaining_time=150)
        check("PrintTwin started mid-print: the slice from before the print, not one made during it",
              r.p.job and r.p.job["download"] == f"handover:{own}", r.p.job and r.p.job["download"])


def x1_waits_for_running():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="00M00A000000001")
        r.until(lambda s: False, timeout=0.8, gcode_state="PREPARE", subtask_name="Cube", gcode_file="/data/Metadata/plate_1.gcode",
                task_id="0", total_layer_num=30, layer_num=0, mc_percent=0, gcode_file_prepare_percent="35")
        r.until(lambda s: False, timeout=0.5, gcode_state="PREPARE", gcode_file_prepare_percent="100")
        check("X1: no FTPS search while preparing (its file appears only into RUNNING)", not r.calls, f"{len(r.calls)} listings")


def old_sd_file():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))                                  # P1: started from the touchscreen, an old file on the card
        r.files["/Bracket.gcode.3mf"] = (three_mf(gcode(12)), time.time() - 30 * 86400)
        t0 = time.time()
        r.until(lambda s: tp_layers(r) == 12, **running("Bracket", 12, 2, 10))
        check("P1 prints an old file from the card: it is the one it reports - taken at once", tp_layers(r) == 12
              and time.time() - t0 < B.FIND_EARLY_FOR, f"after {time.time() - t0:.1f} s")


def multi_plate():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.files["/Box.gcode.3mf"] = (three_mf(gcode(3), gcode(8, x0=150)), time.time())
        r.until(lambda s: tp_layers(r) == 8, **running("Box", 8, 2, 20))
        check("P1 (no plate number), several plates: the plate with the printer's layer count",
              tp_layers(r) == 8 and r.p.tp.meta.get("plate") == 2, f"plate {r.p.tp and r.p.tp.meta.get('plate')}")


def search_pace():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial="094000000000001")
        LOG.clear()
        t0 = time.time()
        s = r.until(lambda s: False, timeout=B.FIND_EARLY_FOR + 3.0, **running("Vase", 30, 4, 12))
        early = [t for t, f in r.calls if f == "/" and t - t0 < B.FIND_EARLY_FOR]
        late = [t for t, f in r.calls if f == "/" and t - t0 > B.FIND_EARLY_FOR + 0.5]
        check("FTPS search: often at first, then only every few minutes", len(early) >= 4 and len(late) <= 1,
              f"{len(early)} early, {len(late)} late")
        hints = [m for m in LOG if "internal storage" in m]
        check("... and the internal-storage hint, once, also in the viewer", len(hints) == 1
              and "internal storage" in ((s.get("loading") or {}).get("message") or ""), f"{len(hints)} in the log")
        r.report(gcode_state="FINISH", mc_percent=100)
        r.until(lambda s: s["state"] == "FINISHED")
        n = len(r.calls)
        r.until(lambda s: False, timeout=1.0, gcode_state="IDLE", subtask_name="", gcode_file="")
        check("print over: the search stops", len(r.calls) == n, f"{len(r.calls) - n} more listings")


def names():
    n = B.BambuPrinter._names({"gcode_file": "", "raw": "Hase / Osterhase", "name": "Hase / Osterhase"})
    check("file names: '/' stored as hex (as the printer does) and as '_'", "hase 2f osterhase.gcode.3mf" in n
          and "hase _ osterhase.3mf" in n)
    n = B.BambuPrinter._names({"gcode_file": "", "raw": "Rear Dry Pod AMS 2 PRO ", "name": "Rear Dry Pod AMS 2 PRO"})
    check("file names: the name as reported (trailing space) and stripped", "rear dry pod ams 2 pro .gcode.3mf" in n
          and "rear dry pod ams 2 pro.gcode.3mf" in n)
    n = B.BambuPrinter._names({"gcode_file": "Tom &amp; Jerry.3mf", "raw": "Tom &amp; Jerry", "name": "Tom & Jerry"})
    check("file names: HTML-escaped and plain, gcode_file itself", "tom & jerry.gcode.3mf" in n and "tom &amp; jerry.3mf" in n)


def paused_start():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.handover(gcode(30), age=3600)
        s = r.until(lambda s: tp_layers(r) == 30 and (s.get("printed") or 0) > 0, timeout=6,
                    gcode_state="PAUSE", subtask_name="Cube", gcode_file="Cube.gcode.3mf", task_id="0",
                    total_layer_num=30, layer_num=18, mc_percent=60, mc_remaining_time=40)
        starts = r.p.tp.layer_start if r.p.tp else []
        check("PrintTwin started during a pause: the model shows where the printer stopped",
              s and starts and starts[17] <= (s.get("printed") or 0) <= starts[18] + 1, f"printed {s and s.get('printed')}")


def serial_upper():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), serial=" 01p00a000000001 ")
        check("serial number upper-cased (MQTT topics are case-sensitive)", r.p.serial == "01P00A000000001")


# ---------------------------------------------------------------- TLS pins, FTPS data channel, MQTT pace
def pin_after_login():
    import sim_bambu as S
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ca, dev, key = S.make_certs(d, "01P00A000000001")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(dev), str(key))
        ports = []
        for _ in range(2):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                ports.append(s.getsockname()[1])
        S.MqttBroker(ports[0], ctx, "01P00A000000001", "other-code", lambda: {"gcode_state": "IDLE"}, "p1")
        p = B.BambuPrinter({"id": "w", "host": "127.0.0.1", "serial": "01P00A000000001", "access_code": "12345678",
                            "mqtt_port": ports[0]}, d / "cache", d / "in")
        threading.Thread(target=p._mqtt_loop, daemon=True).start()
        time.sleep(2)
        check("pin mode: a server that refuses the access code is not pinned (MQTT)",
              not (d / "cache" / "tls" / "01P00A000000001-mqtt.sha256").exists())
        root = d / "sd"
        root.mkdir()
        S.FtpsServer(ports[1], ctx, root, "other-code")
        try:
            with FtpsClient("127.0.0.1", "12345678", port=ports[1], pin_file=d / "f.sha256"):
                pass
        except Exception:
            pass
        check("... nor over FTPS", not (d / "f.sha256").exists())
        e = tlsutil.PinMismatch("the printer's certificate changed - delete C:\\x\\y.sha256")
        check("pin errors read as a sentence (no tuple, no doubled backslashes)", str(e).startswith("the printer") and "\\\\" not in str(e),
              str(e))


def pins():
    class Sock:
        def __init__(self, der):
            self.der = der

        def getpeercert(self, binary_form=False):
            return self.der
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.sha256"
        f.write_text("")                                      # what a failed first write could leave
        tlsutil.pin(Sock(b"cert-A"), f)
        try:
            tlsutil.pin(Sock(b"cert-B"), f)
            check("empty pin file: pinned again, then another certificate is refused", False)
        except tlsutil.PinMismatch:
            check("empty pin file: pinned again, then another certificate is refused", True)
        f.write_text("2e46640a0e")
        try:
            tlsutil.pin(Sock(b"cert-A"), f)
            check("damaged pin file: refused, with what to do", False)
        except tlsutil.PinMismatch as e:
            check("damaged pin file: refused, with what to do", "delete" in str(e), str(e)[:50])


def ftps_data_channel():
    import sim_bambu as S
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a").mkdir()
        (d / "b").mkdir()
        ca, dev, key = S.make_certs(d / "a", "01P00A000000001")
        _, dev2, key2 = S.make_certs(d / "b", "01P00A000000001")      # another CA, another certificate
        ctx, ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(dev), str(key))
        ctx2.load_cert_chain(str(dev2), str(key2))
        root = d / "sd"
        root.mkdir()
        (root / "Cube.gcode.3mf").write_bytes(b"x" * 1000)

        def server(**kw):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            S.FtpsServer(port, ctx, root, "12345678", **kw)
            return port

        port = server()
        with FtpsClient("127.0.0.1", "12345678", port=port, pin_file=d / "pin") as f:
            ok = f.listing("/") == [("/Cube.gcode.3mf", 1000, f.listing("/")[0][2])]
        check("FTPS listing over a TLS data channel (no unwrap hang, nothing doubled)", ok)
        port = server(data_ctx=ctx2)
        try:
            with FtpsClient("127.0.0.1", "12345678", port=port, pin_file=d / "pin") as f:
                f.listing("/")
            check("pinned: a data channel with another certificate is refused", False)
        except tlsutil.PinMismatch:
            check("pinned: a data channel with another certificate is refused", True)
        try:
            with FtpsClient("127.0.0.1", "12345678", port=port, cafile=str(ca), server_name="01P00A000000001",
                            allow_plain_data=True) as f:
                f.listing("/")
            check("CA mode: a data channel that fails verification is refused, no plain fallback", False)
        except ssl.SSLCertVerificationError:
            check("CA mode: a data channel that fails verification is refused, no plain fallback", True)
        port = server(plain_data=True)                     # data-channel TLS fails, as on an A1
        try:
            with FtpsClient("127.0.0.1", "12345678", port=port, pin_file=d / "pin", timeout=3) as f:
                f.listing("/")
            check("data-channel TLS fails: no plain fallback unless the model needs it", False)
        except (ssl.SSLError, OSError):
            check("data-channel TLS fails: no plain fallback unless the model needs it", True)
        logs = []
        with FtpsClient("127.0.0.1", "12345678", port=port, pin_file=d / "pin", timeout=3, allow_plain_data=True,
                        log=logs.append) as f:
            got = f.listing("/")
        check("... A1: falls back to a plain data channel, and says so", len(got) == 1 and f.prot_c and logs, str(logs)[:60])


def mqtt_pace():
    import sim_bambu as S
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ca, dev, key = S.make_certs(d, "01P00A000000001")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(dev), str(key))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        status = lambda: {"gcode_state": "IDLE", "total_layer_num": 0}   # noqa: E731
        broker = S.MqttBroker(port, ctx, "01P00A000000001", "12345678", status, "p1", drop_after=0.3)
        p = B.BambuPrinter({"id": "m", "host": "127.0.0.1", "serial": "01P00A000000001", "access_code": "12345678",
                            "mqtt_port": port, "cafile": str(ca)}, d / "cache", d / "in")
        threading.Thread(target=p._mqtt_loop, daemon=True).start()
        time.sleep(9)
        check("a broker that drops PrintTwin at once: reconnects slow down, pushall not every time",
              broker.clients <= 5 and broker.pushalls <= 2, f"{broker.clients} connects, {broker.pushalls} pushalls in 9 s")


for t in (reprint, stale_total, late_handover, ambiguous_handover, wrong_file, rejected_stays, long_prepare,
          restart_mid_print, printing_file_first, collapsed_name, stale_prepare_percent, lost_finish, ftps_failure_shown,
          tie_break, memo_other_print, pause_keeps_rejection, ambiguous_ftps_down, restart_later_slice,
          x1_waits_for_running, old_sd_file, multi_plate, search_pace, names,
          paused_start, serial_upper, pins):
    t()
if shutil.which("openssl"):
    ftps_data_channel()
    mqtt_pace()
    pin_after_login()
else:
    print("SKIP FTPS data channel and MQTT pace (no openssl tool)")
print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
