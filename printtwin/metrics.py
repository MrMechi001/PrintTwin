"""Parse Prusa Buddy syslog metrics datagrams.

Datagram: '<14>1 - <mac> buddy - - - msg=<n>,tm=<us>,v=4 ' + lines
Line:     '<name>[,<tags>] <fields> <offset_us>'   (time = tm + offset, µs since boot)
Fields:   v=12i (int) | v=1.5 (float) | v="text" | k=..,k2=.. (custom)
"""
from __future__ import annotations

import re

_HDR = re.compile(rb"^<\d+>1 \S+ (\S+) \S+ - - - msg=(\d+),tm=(\d+),v=(\d+) ")
_FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^,]+)')


def _value(s: str):
    if s.startswith('"'):
        return s[1:-1]
    if s.endswith("i"):
        return int(s[:-1])
    try:
        return float(s)
    except ValueError:
        return s


def parse_datagram(payload: bytes):
    """Return (mac, msg_no, [(t_us, name, tags, fields), ...]) or None."""
    m = _HDR.match(payload)
    if not m:
        return None
    mac, msg, tm = m.group(1).decode(), int(m.group(2)), int(m.group(3))
    out = []
    for line in payload[m.end():].decode("utf-8", "replace").split("\n"):
        if not line:
            continue
        try:
            head, rest = line.split(" ", 1)
            fieldstr, off = rest.rsplit(" ", 1)
            name, *tagparts = head.split(",")
            tags = dict(t.split("=", 1) for t in tagparts)
            fields = {k: _value(v) for k, v in _FIELD.findall(fieldstr)}
            out.append((tm + int(off), name, tags, fields))
        except ValueError:
            continue
    return mac, msg, out
