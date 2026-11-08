"""The minimal MQTT client (printtwin/mqtt.py) against a fake broker that speaks MQTT 3.1.1.

usage: python -I tests/test_mqtt.py
Like a Bambu Lab printer's local broker: user name + access code, a subscription to the
report topic, a "pushall" request, a large status message (3-byte length), a QoS 1 message
that needs a PUBACK, keep-alive pings and a refused login; a refused subscription (wrong
serial), a message before the SUBACK, and a certificate check that runs before the access code
is sent.
"""
import json
import socket
import ssl
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin.mqtt import MqttClient, MqttError, SubscribeRefused, _varint  # noqa: E402

failures = 0
SERIAL = "01S00C123456789"


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def read_packet(conn):
    first = conn.recv(1)
    if not first:
        return None, None
    n, mult = 0, 1
    while True:
        b = conn.recv(1)[0]
        n += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    body = b""
    while len(body) < n:
        body += conn.recv(n - len(body))
    return first[0], body


def mstr(b, pos):
    (n,) = struct.unpack_from("!H", b, pos)
    return b[pos + 2:pos + 2 + n].decode(), pos + 2 + n


class FakeBroker:
    """One client at a time; records what it receives."""

    def __init__(self, password="12345678", tls_ctx=None, early=False):
        self.password, self.early = password, early
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        if tls_ctx:
            self.srv = tls_ctx.wrap_socket(self.srv, server_side=True)
        self.seen = {"connect": None, "subscribe": [], "publish": [], "puback": [], "pings": 0}
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except ssl.SSLError:
                continue                                 # a client that refused our certificate
            except OSError:
                return
            threading.Thread(target=self.client, args=(conn,), daemon=True).start()

    def client(self, conn):
        with conn:
            ptype, body = read_packet(conn)
            if ptype != 0x10:
                return                                   # no CONNECT (the client refused the certificate)
            proto, pos = mstr(body, 0)
            level, flags = body[pos], body[pos + 1]
            pos += 4
            cid, pos = mstr(body, pos)
            user, pos = mstr(body, pos) if flags & 0x80 else (None, pos)
            pw, pos = mstr(body, pos) if flags & 0x40 else (None, pos)
            self.seen["connect"] = (proto, level, cid, user, pw)
            if user != "bblp" or pw != self.password:
                conn.sendall(bytes([0x20, 2, 0, 4]))       # bad user name or password
                return
            conn.sendall(bytes([0x20, 2, 0, 0]))
            while True:
                ptype, body = read_packet(conn)
                if ptype is None:
                    return
                t = ptype & 0xF0
                if t == 0x80:                                # SUBSCRIBE
                    pid = body[:2]
                    topic, pos = mstr(body, 2)
                    self.seen["subscribe"].append(topic)
                    if self.early:                           # a report may come before the SUBACK
                        rep = f"device/{SERIAL}/report"
                        tb = struct.pack("!H", len(rep)) + rep.encode()
                        msg = b'{"print": {"early": 1}}'
                        conn.sendall(bytes([0x30]) + _varint(len(tb) + len(msg)) + tb + msg)
                    conn.sendall(bytes([0x90, 3]) + pid + bytes([0 if SERIAL in topic else 0x80]))
                elif t == 0x30:                              # PUBLISH from the client (the request)
                    topic, pos = mstr(body, 0)
                    self.seen["publish"].append((topic, body[pos:]))
                    big = json.dumps({"print": {"command": "push_status", "gcode_state": "RUNNING",
                                                "mc_percent": 42, "pad": "x" * 300_000}}).encode()
                    rep = f"device/{SERIAL}/report"
                    tb = struct.pack("!H", len(rep)) + rep.encode()
                    conn.sendall(bytes([0x30]) + _varint(len(tb) + len(big)) + tb + big)
                    small = json.dumps({"print": {"layer_num": 7}}).encode()
                    conn.sendall(bytes([0x32]) + _varint(len(tb) + 2 + len(small)) + tb + b"\x00\x09" + small)
                elif t == 0x40:                              # PUBACK
                    self.seen["puback"].append(struct.unpack("!H", body)[0])
                elif t == 0xC0:                              # PINGREQ
                    self.seen["pings"] += 1
                    conn.sendall(bytes([0xD0, 0]))
                elif t == 0xE0:                              # DISCONNECT
                    return


broker = FakeBroker()
c = MqttClient("127.0.0.1", broker.port, "bblp", "12345678", client_id="printtwin-test", tls=False, keepalive=2)
c.connect()
check("CONNECT: MQTT 3.1.1 with user bblp and the access code",
      broker.seen["connect"] == ("MQTT", 4, "printtwin-test", "bblp", "12345678"), str(broker.seen["connect"]))
c.subscribe(f"device/{SERIAL}/report")
c.publish(f"device/{SERIAL}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
m1 = c.recv(5)
m2 = c.recv(5)
check("SUBSCRIBE to the report topic", broker.seen["subscribe"] == [f"device/{SERIAL}/report"], str(broker.seen["subscribe"]))
check("PUBLISH of the pushall request", broker.seen["publish"] and json.loads(broker.seen["publish"][0][1])["pushing"]["command"] == "pushall")
check("large status message (300 KB, 3-byte length) arrives whole",
      m1 is not None and m1[0] == f"device/{SERIAL}/report" and json.loads(m1[1])["print"]["mc_percent"] == 42,
      f"{m1 and len(m1[1])} bytes")
check("QoS 1 message arrives", m2 is not None and json.loads(m2[1]) == {"print": {"layer_num": 7}}, str(m2))
time.sleep(0.3)
check("... and gets its PUBACK", broker.seen["puback"] == [9], str(broker.seen["puback"]))
t0 = time.monotonic()
none = c.recv(2.5)
check("recv() times out with None, keep-alive pings sent meanwhile", none is None and broker.seen["pings"] >= 1
      and 2.3 < time.monotonic() - t0 < 4.0, f"pings {broker.seen['pings']}")
c.close()

r = MqttClient("127.0.0.1", broker.port, "bblp", "12345678", tls=False)
r.connect()
try:
    r.subscribe("device/01s00c123456789/report")
    check("a refused subscription (wrong serial) raises", False)
except SubscribeRefused as e:
    check("a refused subscription (wrong serial) raises", "refused" in str(e), str(e))
r.close()

eb = FakeBroker(early=True)
e = MqttClient("127.0.0.1", eb.port, "bblp", "12345678", tls=False)
e.connect()
e.subscribe(f"device/{SERIAL}/report")
em = e.recv(2)
check("a message that came before the SUBACK is not lost", em is not None and json.loads(em[1]) == {"print": {"early": 1}},
      str(em))
e.close()

bad = MqttClient("127.0.0.1", broker.port, "bblp", "wrong", tls=False)
try:
    bad.connect()
    check("wrong access code refused", False)
except MqttError as e:
    check("wrong access code refused with a clear reason", "access code" in str(e), str(e))

# TLS with a self-signed certificate, like the printer's own (needs the openssl tool; skipped without)
import shutil  # noqa: E402
import ssl  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

openssl = shutil.which("openssl")
if openssl:
    with tempfile.TemporaryDirectory() as d:
        crt, key = str(Path(d) / "c.pem"), str(Path(d) / "k.pem")
        r = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                            "-days", "1", "-subj", "/CN=BBL Printer"], capture_output=True)
        if r.returncode == 0:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, key)
            tls_broker = FakeBroker(tls_ctx=ctx)
            tc = MqttClient("127.0.0.1", tls_broker.port, "bblp", "12345678", tls=True, keepalive=5)
            try:
                tc.connect()
                tc.subscribe(f"device/{SERIAL}/report")
                tc.publish(f"device/{SERIAL}/request", '{"pushing": {"command": "pushall"}}')
                m = tc.recv(5)
                check("TLS (self-signed certificate): connect, subscribe, large message",
                      m is not None and json.loads(m[1])["print"]["gcode_state"] == "RUNNING")
            finally:
                tc.close()
            tls_broker.seen["connect"] = None

            def refuse(sock):
                raise ssl.SSLError("pin mismatch (test)")
            pc = MqttClient("127.0.0.1", tls_broker.port, "bblp", "SECRET-CODE", tls=True, on_tls=refuse)
            try:
                pc.connect()
                check("on_tls refusing the certificate: connect fails", False)
            except ssl.SSLError:
                pass
            finally:
                pc.close()
            time.sleep(0.5)
            check("on_tls refusing the certificate: the access code was never sent", tls_broker.seen["connect"] is None,
                  str(tls_broker.seen["connect"]))
            strict = MqttClient("127.0.0.1", tls_broker.port, "bblp", "12345678", tls=True, verify=True)
            try:
                strict.connect()
                check("verify=True refuses an unknown self-signed certificate", False)
            except (ssl.SSLError, OSError):
                check("verify=True refuses an unknown self-signed certificate", True)
        else:
            print("SKIP TLS check (openssl could not make a certificate)")
else:
    print("SKIP TLS check (no openssl)")

print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
