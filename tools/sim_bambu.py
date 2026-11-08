"""Simulated Bambu Lab printer (local MQTT + FTPS, both TLS) + PrintTwin bridge, for testing without one.

Built from the protocol as the open-source integrations document it (see printtwin/bambu.py):
  MQTT  TLS, user "bblp" + access code, device/<serial>/report and /request; answers "pushall"
        (full status) and "get_version"; reports once a second - in P1/A1 style only what changed,
        in X1 style everything
  FTPS  implicit TLS, vsFTPd-like: USER/PASS, PBSZ/PROT, PASV/EPSV, LIST, SIZE, MDTM, REST, RETR
  print PREPARE (heating) -> RUNNING, layer_num / mc_percent / mc_remaining_time taken from the job's
        own "M73 L" / "M73 P" marks -> FINISH
Certificates: a throwaway CA and a device certificate with CN = the serial (needs the openssl tool).

usage: python -I tools/sim_bambu.py JOB [--serial 01P00A000000001] [--style p1|x1] [--duration 60]
  JOB          a .gcode.3mf (Bambu Studio / Orca "export plate sliced file") or a plain Bambu .gcode
  --style x1   like an X1: gcode_file "/data/Metadata/plate_1.gcode", the .3mf shows up in /cache only
               a few seconds into RUNNING and is still growing at first
  --internal   like an H2D printing from internal storage: the file is never on FTPS
  --pin        no CA in the bridge config: PrintTwin pins the certificate on first contact
  --handover   put the plate G-code into the hand-over folder first, like the slicer hook does
  --wrong-code give the bridge a wrong access code (to see it refused)
Viewer: http://localhost:<port>/
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import incoming  # noqa: E402
from printtwin.toolpath import load_file  # noqa: E402

SIM = ROOT / "logs" / "sim_bambu"


# ---------------------------------------------------------------- certificates
def make_certs(folder: Path, serial: str) -> tuple[Path, Path, Path]:
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit("sim_bambu needs the openssl tool to make its TLS certificates")
    ca, ca_key = folder / "ca.pem", folder / "ca.key"
    dev, dev_key, csr = folder / "device.pem", folder / "device.key", folder / "device.csr"
    run = lambda *a: subprocess.run([openssl, *a], check=True, capture_output=True)  # noqa: E731
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(ca), "-days", "2",
        "-subj", "/CN=PrintTwin Sim CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(dev_key), "-out", str(csr), "-subj", f"/CN={serial}")
    run("x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(dev), "-days", "2")
    return ca, dev, dev_key


# ---------------------------------------------------------------- the print
class Print:
    """The simulated job: its marks, and where it is at a given time."""

    def __init__(self, job_3mf: Path, name: str, heat: float, duration: float):
        self.path, self.name, self.heat, self.duration = job_3mf, name, heat, duration
        tp = load_file(job_3mf)
        self.n = max(1, tp.count)
        self.layer_marks = tp.layer_marks or [(s, i + 1) for i, s in enumerate(tp.layer_start)]
        self.progress_marks = tp.progress_marks
        self.total_layers = tp.meta.get("total_layers") or len(tp.layer_z)
        self.t0 = time.time()

    def at(self, now: float) -> dict:
        t = now - self.t0
        if t < self.heat:
            return {"gcode_state": "PREPARE", "gcode_file_prepare_percent": str(min(100, int(100 * t / self.heat))),
                    "mc_percent": 0, "layer_num": 0, "mc_remaining_time": int(self.duration / 60) + 1,
                    "nozzle_temper": 30.0 + 190 * t / self.heat, "nozzle_target_temper": 220.0,
                    "bed_temper": 25.0 + 30 * t / self.heat, "bed_target_temper": 55.0}
        run = t - self.heat
        if run >= self.duration:
            return {"gcode_state": "FINISH", "gcode_file_prepare_percent": "100", "mc_percent": 100,
                    "layer_num": self.total_layers, "mc_remaining_time": 0, "nozzle_temper": 220.0,
                    "nozzle_target_temper": 0.0, "bed_temper": 55.0, "bed_target_temper": 0.0}
        seg = run / self.duration * self.n
        layer = max([v for s, v in self.layer_marks if s <= seg] or [1])
        pct = max([v for s, v in self.progress_marks if s <= seg] or [0])
        return {"gcode_state": "RUNNING", "gcode_file_prepare_percent": "100", "mc_percent": pct,
                "layer_num": layer, "mc_remaining_time": int((self.duration - run) / 60) + 1,
                "nozzle_temper": 220.0, "nozzle_target_temper": 220.0, "bed_temper": 55.0, "bed_target_temper": 55.0}


# ---------------------------------------------------------------- MQTT broker
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_packet(c):
    first = c.recv(1)
    if not first:
        return None, None
    n, mult = 0, 1
    while True:
        b = c.recv(1)
        if not b:
            return None, None
        n += (b[0] & 0x7F) * mult
        if not b[0] & 0x80:
            break
        mult *= 128
    body = b""
    while len(body) < n:
        chunk = c.recv(n - len(body))
        if not chunk:
            return None, None
        body += chunk
    return first[0], body


def _mstr(b: bytes, pos: int):
    (n,) = struct.unpack_from("!H", b, pos)
    return b[pos + 2:pos + 2 + n].decode("utf-8", "replace"), pos + 2 + n


class MqttBroker:
    def __init__(self, port: int, ctx: ssl.SSLContext, serial: str, code: str, status, style: str):
        self.serial, self.code, self.status, self.style = serial, code, status, style
        self.srv = ctx.wrap_socket(socket.create_server(("127.0.0.1", port)), server_side=True)
        self.clients = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except ssl.SSLError:
                continue                      # a client that refused the certificate
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _send(self, c, lock, obj):
        topic = f"device/{self.serial}/report".encode()
        payload = json.dumps(obj).encode()
        body = struct.pack("!H", len(topic)) + topic + payload
        with lock:
            c.sendall(bytes([0x30]) + _varint(len(body)) + body)

    def _client(self, c):
        lock = threading.Lock()
        with c:
            ptype, body = _read_packet(c)
            if ptype != 0x10:
                return
            _, pos = _mstr(body, 0)
            flags = body[pos + 1]
            pos += 4
            _, pos = _mstr(body, pos)
            user, pos = _mstr(body, pos) if flags & 0x80 else (None, pos)
            pw, pos = _mstr(body, pos) if flags & 0x40 else (None, pos)
            if user != "bblp" or pw != self.code:
                c.sendall(bytes([0x20, 2, 0, 5]))            # not authorized
                return
            c.sendall(bytes([0x20, 2, 0, 0]))
            self.clients += 1
            subscribed, last = threading.Event(), {}

            def pusher():
                nonlocal last
                seq = 1000
                while not subscribed.wait(0.5):
                    pass
                while True:
                    time.sleep(1.0)
                    full = self.status()
                    seq += 1
                    delta = full if self.style == "x1" else {k: v for k, v in full.items() if last.get(k) != v}
                    last = full
                    try:
                        self._send(c, lock, {"print": {**delta, "command": "push_status", "sequence_id": str(seq)}})
                    except OSError:
                        return

            threading.Thread(target=pusher, daemon=True).start()
            while True:
                try:
                    ptype, body = _read_packet(c)
                except OSError:
                    return
                if ptype is None:
                    return
                t = ptype & 0xF0
                if t == 0x80:                                # SUBSCRIBE
                    topic, _ = _mstr(body, 2)
                    ok = topic == f"device/{self.serial}/report"
                    with lock:
                        c.sendall(bytes([0x90, 3]) + body[:2] + bytes([0 if ok else 0x80]))
                    if ok:
                        subscribed.set()
                elif t == 0x30:                              # a request
                    topic, pos = _mstr(body, 0)
                    try:
                        req = json.loads(body[pos:])
                    except ValueError:
                        continue
                    if req.get("pushing", {}).get("command") == "pushall":
                        full = self.status()
                        last.clear()
                        last.update(full)
                        self._send(c, lock, {"print": {**full, "command": "push_status", "msg": 0}})
                    if req.get("info", {}).get("command") == "get_version":
                        self._send(c, lock, {"info": {"command": "get_version", "sequence_id": "0", "module": [
                            {"name": "ota", "sw_ver": "01.10.00.00", "product_name": "Bambu Lab P1S"}]}})
                elif t == 0xC0:                              # PINGREQ
                    with lock:
                        c.sendall(bytes([0xD0, 0]))
                elif t == 0xE0:
                    return


# ---------------------------------------------------------------- FTPS server
class FtpsServer:
    def __init__(self, port: int, ctx: ssl.SSLContext, root: Path, code: str):
        self.ctx, self.root, self.code = ctx, root, code
        self.srv = socket.create_server(("127.0.0.1", port))
        self.sessions = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                raw, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _file(self, arg: str) -> Path | None:
        p = (self.root / arg.lstrip("/")).resolve()
        return p if self.root.resolve() in p.parents or p == self.root.resolve() else None

    def _session(self, raw):
        try:
            c = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            raw.close()
            return
        self.sessions += 1
        f = c.makefile("rb")
        say = lambda s: c.sendall((s + "\r\n").encode())  # noqa: E731
        authed, prot_p, pasv, rest, user = False, False, None, 0, None
        say("220 (vsFTPd 3.0.3)")
        try:
            while True:
                line = f.readline()
                if not line:
                    return
                cmd, _, arg = line.decode("utf-8", "replace").strip().partition(" ")
                cmd = cmd.upper()
                if cmd == "USER":
                    user = arg
                    say("331 Please specify the password.")
                elif cmd == "PASS":
                    authed = user == "bblp" and arg == self.code
                    say("230 Login successful." if authed else "530 Login incorrect.")
                elif cmd == "QUIT":
                    say("221 Goodbye.")
                    return
                elif not authed:
                    say("530 Please login with USER and PASS.")
                elif cmd == "PBSZ":
                    say("200 PBSZ set to 0.")
                elif cmd == "PROT":
                    prot_p = arg.upper() == "P"
                    say("200 PROT now Private." if prot_p else "200 PROT now Clear.")
                elif cmd == "TYPE":
                    say("200 Switching to Binary mode.")
                elif cmd == "PASV":
                    pasv = socket.create_server(("127.0.0.1", 0))
                    port = pasv.getsockname()[1]
                    say(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255}).")
                elif cmd == "EPSV":
                    pasv = socket.create_server(("127.0.0.1", 0))
                    say(f"229 Entering Extended Passive Mode (|||{pasv.getsockname()[1]}|)")
                elif cmd == "REST":
                    rest = int(arg)
                    say(f"350 Restart position accepted ({rest}).")
                elif cmd in ("SIZE", "MDTM"):
                    p = self._file(arg)
                    if not p or not p.is_file():
                        say("550 Could not get file size." if cmd == "SIZE" else "550 Could not get file modification time.")
                    elif cmd == "SIZE":
                        say(f"213 {p.stat().st_size}")
                    else:
                        say("213 " + datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y%m%d%H%M%S"))
                elif cmd in ("LIST", "RETR"):
                    p = self._file(arg or "/")
                    if not pasv:
                        say("425 Use PORT or PASV first.")
                        continue
                    if cmd == "RETR" and (not p or not p.is_file()):
                        say("550 Failed to open file.")
                        pasv.close()
                        pasv = None
                        continue
                    pasv.settimeout(10)
                    data_raw, _ = pasv.accept()
                    pasv.close()
                    pasv = None
                    if cmd == "LIST":
                        say("150 Here comes the directory listing.")
                        rows = []
                        if p and p.is_dir():
                            for e in sorted(p.iterdir()):
                                st = e.stat()
                                kind = "d" if e.is_dir() else "-"
                                stamp = datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
                                rows.append(f"{kind}rw-r--r--    1 0        0        {st.st_size:>10} {stamp} {e.name}")
                        payload = ("\r\n".join(rows) + ("\r\n" if rows else "")).encode()
                    else:
                        say(f"150 Opening BINARY mode data connection for {arg} ({p.stat().st_size} bytes).")
                        payload = p.read_bytes()[rest:]
                        rest = 0
                    data = self.ctx.wrap_socket(data_raw, server_side=True) if prot_p else data_raw
                    try:
                        for k in range(0, len(payload), 65536):
                            data.sendall(payload[k:k + 65536])
                    finally:
                        data.close()
                    say("226 Directory send OK." if cmd == "LIST" else "226 Transfer complete.")
                elif cmd in ("SYST",):
                    say("215 UNIX Type: L8")
                elif cmd == "PWD":
                    say('257 "/" is the current directory')
                elif cmd == "CWD":
                    say("250 Directory successfully changed.")
                else:
                    say("502 Command not implemented.")
        except (OSError, ssl.SSLError, ValueError):
            return
        finally:
            c.close()


# ---------------------------------------------------------------- main
def as_3mf(job: Path, out: Path):
    data = job.read_bytes()
    if data[:4] == b"PK\x03\x04":
        out.write_bytes(data)
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Metadata/plate_1.gcode", data)
        z.writestr("Metadata/slice_info.config", '<config><plate><metadata key="index" value="1"/></plate></config>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("--serial", default="01P00A000000001")          # 01P = P1S
    ap.add_argument("--access-code", default="12345678")
    ap.add_argument("--style", choices=("p1", "x1"), default="p1")
    ap.add_argument("--heat", type=float, default=4.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--late", type=float, default=3.0, help="x1 style: seconds into RUNNING before the file shows up")
    ap.add_argument("--internal", action="store_true")
    ap.add_argument("--pin", action="store_true")
    ap.add_argument("--handover", action="store_true")
    ap.add_argument("--wrong-code", action="store_true")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--mqtt-port", type=int, default=18883)
    ap.add_argument("--ftps-port", type=int, default=18990)
    a = ap.parse_args()

    shutil.rmtree(SIM, ignore_errors=True)
    sd = SIM / "sdcard"
    for d in (sd / "cache", SIM / "cache", SIM / "incoming", SIM / "certs"):
        d.mkdir(parents=True)
    ca, dev, dev_key = make_certs(SIM / "certs", a.serial)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(dev), str(dev_key))

    name = a.job.name.split(".")[0]
    staged = SIM / f"{name}.gcode.3mf"
    as_3mf(a.job, staged)
    pr = Print(staged, name, a.heat, a.duration)
    if a.handover:                                   # what the post-processing hook would have stored
        with zipfile.ZipFile(staged) as z:
            plate = SIM / f"{name}_plate_1.gcode"
            plate.write_bytes(z.read("Metadata/plate_1.gcode"))
        incoming.store(plate, f"{name}.gcode", "BambuStudio", SIM / "incoming")
    target = sd / "cache" / f"{name}.gcode.3mf" if a.style == "x1" else sd / f"{name}.gcode.3mf"

    def place_file():
        """p1: the file is there from the start; x1: it shows up `late` s into RUNNING, growing for 2 s."""
        if a.internal:
            return
        if a.style == "p1":
            shutil.copyfile(staged, target)
            return
        time.sleep(a.heat + a.late)
        data = staged.read_bytes()
        target.write_bytes(data[: len(data) // 3])
        time.sleep(2.0)
        target.write_bytes(data)

    threading.Thread(target=place_file, daemon=True).start()

    def status():
        s = pr.at(time.time())
        gfile = "/data/Metadata/plate_1.gcode" if a.style == "x1" else f"{name}.gcode.3mf"
        return {**s, "gcode_file": gfile, "subtask_name": name, "print_type": "local", "task_id": "0",
                "total_layer_num": pr.total_layers, "spd_lvl": 2, "spd_mag": 100, "cooling_fan_speed": "10",
                "heatbreak_fan_speed": "15", "plate_idx": 1, "print_error": 0, "mc_print_stage": "2"}

    MqttBroker(a.mqtt_port, sctx, a.serial, a.access_code, status, a.style)
    FtpsServer(a.ftps_port, sctx, sd, a.access_code)
    print(f"simulated Bambu printer {a.serial} ({a.style} style): MQTT 127.0.0.1:{a.mqtt_port}, "
          f"FTPS 127.0.0.1:{a.ftps_port}, job {name}, {pr.total_layers} layers in {a.duration:.0f} s"
          f"{', file only in internal storage' if a.internal else ''}", flush=True)

    cfg = SIM / "config.toml"
    cfg.write_text(
        f'cache_dir = "{(SIM / "cache").as_posix()}"\nincoming_dir = "{(SIM / "incoming").as_posix()}"\n\n'
        f'[[printer]]\nid = "sim"\nname = "Simulated Bambu printer"\nkind = "bambu"\nhost = "127.0.0.1"\n'
        f'serial = "{a.serial}"\naccess_code = "{"87654321" if a.wrong_code else a.access_code}"\n'
        f'mqtt_port = {a.mqtt_port}\nftps_port = {a.ftps_port}\n'
        + ("" if a.pin else f'cafile = "{ca.as_posix()}"\n')
        + f'\n[server]\nlisten_host = "127.0.0.1"\nport = {a.port}\n\n'
        f'[metrics]\nlisten_host = "127.0.0.1"\nlisten_port = {a.mqtt_port + 2}\n', encoding="utf-8")
    from printtwin.__main__ import main as bridge
    bridge(["--config", str(cfg)])


if __name__ == "__main__":
    main()
