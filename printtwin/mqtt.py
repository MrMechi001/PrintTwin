"""Minimal MQTT 3.1.1 client (standard library only), for printers that report over MQTT.

Bambu Lab printers publish their status on the local network over MQTT with TLS (port 8883,
user "bblp", the printer's access code as password, topic device/<serial>/report; a
"pushall" request on device/<serial>/request asks for everything at once). PrintTwin only
needs a small part of MQTT for that:

  CONNECT with user name / password, SUBSCRIBE (QoS 0), PUBLISH (QoS 0) for requests,
  receiving PUBLISH (QoS 0, and QoS 1 with its PUBACK), PINGREQ keep-alive, DISCONNECT.

Not implemented (not needed for a status reader): QoS 2, retained-message handling beyond
receiving, wills, persistent sessions, MQTT 5.
"""
from __future__ import annotations

import socket
import ssl
import struct
import threading
import time

CONNACK_ERRORS = {1: "unacceptable protocol version", 2: "client id rejected", 3: "server unavailable",
                  4: "bad user name or password (access code)", 5: "not authorized"}


class MqttError(Exception):
    pass


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str | bytes) -> bytes:
    b = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


class MqttClient:
    """client = MqttClient(host, 8883, "bblp", access_code); client.connect();
    client.subscribe(f"device/{serial}/report"); client.publish(f"device/{serial}/request", json)
    then client.recv() -> (topic, payload bytes) or None on timeout; keep-alive pings are sent
    from recv(). verify=False accepts the printer's self-signed certificate (as Bambu's own
    LAN tools do); pass cafile to pin one instead."""

    def __init__(self, host: str, port: int = 8883, username: str | None = None, password: str | None = None,
                 client_id: str = "printtwin", tls: bool = True, verify: bool = False, cafile: str | None = None,
                 keepalive: int = 30, timeout: float = 10.0):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.client_id = client_id
        self.tls, self.verify, self.cafile = tls, verify, cafile
        self.keepalive, self.timeout = keepalive, timeout
        self.sock: socket.socket | None = None
        self._wlock = threading.Lock()
        self._last_out = 0.0
        self._pid = 0
        self._buf = b""

    # ------------------------------------------------------------------ connection
    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.tls:
            ctx = ssl.create_default_context(cafile=self.cafile) if (self.verify or self.cafile) \
                else ssl._create_unverified_context()      # the printer's own self-signed certificate
            if not self.verify:
                ctx.check_hostname = False
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self._buf = b""
        flags = 0x02                                       # clean session
        payload = _str(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += _str(self.username)
        if self.password is not None:
            flags |= 0x40
            payload += _str(self.password)
        var = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        self._send(0x10, var + payload)
        ptype, body = self._read_packet(self.timeout)
        if ptype != 0x20 or len(body) < 2:
            raise MqttError(f"no CONNACK (got packet type {ptype:#x})")
        if body[1]:
            raise MqttError(f"connection refused: {CONNACK_ERRORS.get(body[1], body[1])}")

    def close(self):
        if self.sock:
            try:
                self._send(0xE0, b"")
            except OSError:
                pass
            try:
                self.sock.close()
            finally:
                self.sock = None

    # ------------------------------------------------------------------ messages
    def subscribe(self, topic: str, qos: int = 0):
        self._pid = self._pid % 65535 + 1
        self._send(0x82, struct.pack("!H", self._pid) + _str(topic) + bytes([qos]))

    def publish(self, topic: str, payload: bytes | str, qos: int = 0):
        if qos:
            raise MqttError("only QoS 0 publishing is supported")
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        self._send(0x30, _str(topic) + data)

    def recv(self, timeout: float | None = None) -> tuple[str, bytes] | None:
        """The next message (topic, payload), or None when `timeout` passes without one.
        Sends keep-alive pings meanwhile; raises OSError / MqttError when the connection breaks."""
        end = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            if time.monotonic() - self._last_out > self.keepalive * 0.6:
                self._send(0xC0, b"")                      # PINGREQ
            left = end - time.monotonic()
            if left <= 0:
                return None
            got = self._read_packet(min(left, max(0.5, self.keepalive * 0.3)), none_on_timeout=True)
            if got is None:
                continue
            ptype, body = got
            if ptype & 0xF0 == 0x30:                       # PUBLISH
                qos = (ptype >> 1) & 3
                (tlen,) = struct.unpack_from("!H", body, 0)
                topic = body[2:2 + tlen].decode("utf-8", "replace")
                pos = 2 + tlen
                if qos:
                    (pid,) = struct.unpack_from("!H", body, pos)
                    pos += 2
                    if qos == 1:
                        self._send(0x40, struct.pack("!H", pid))   # PUBACK
                return topic, body[pos:]
            # SUBACK (0x90), PINGRESP (0xD0), PUBACK (0x40): nothing to do for a reader

    # ------------------------------------------------------------------ framing
    def _send(self, header: int, body: bytes):
        if not self.sock:
            raise MqttError("not connected")
        with self._wlock:
            self.sock.sendall(bytes([header]) + _varint(len(body)) + body)
            self._last_out = time.monotonic()

    def _read_exact(self, n: int, deadline: float, none_on_timeout: bool) -> bytes | None:
        while len(self._buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                if none_on_timeout and not self._buf:
                    return None
                raise socket.timeout("MQTT read timed out mid-packet")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(max(65536, n - len(self._buf)))
            except (socket.timeout, ssl.SSLWantReadError):
                continue
            if not chunk:
                raise ConnectionResetError("MQTT connection closed by the printer")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_packet(self, timeout: float, none_on_timeout: bool = False) -> tuple[int, bytes] | None:
        deadline = time.monotonic() + timeout
        first = self._read_exact(1, deadline, none_on_timeout)
        if first is None:
            return None
        deadline = max(deadline, time.monotonic() + self.timeout)    # a started packet gets its time
        n, mult = 0, 1
        for _ in range(4):
            b = self._read_exact(1, deadline, False)[0]
            n += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        else:
            raise MqttError("malformed remaining length")
        return first[0], (self._read_exact(n, deadline, False) if n else b"")
