"""G-code / bgcode -> toolpath: extrusion segments in file order.

Per segment: start xyz, end xyz, width, height (mm), feature type id and the
byte offset of its G-code line in the decoded text (the firmware's `sdpos`
counts the same stream, so offsets map 1:1 onto live progress).

Binary layout served to the viewer (little endian, N segments):
  f32[3N] start | f32[3N] end | f32[2N] width,height | u8[N] type | pad to 4

Understands PrusaSlicer, OrcaSlicer, Bambu Studio ("; FEATURE:", "; Z_HEIGHT:"), SuperSlicer
and Cura comments; falls back to Z changes when a file has no layer markers. Bambu jobs
(.gcode.3mf / .3mf, a zip) are unpacked: the plate's G-code plus its colours and printer.

Parsing is incremental: `JobDecoder.feed()` takes the job file in arbitrary
chunks (e.g. straight from a slow download) and the toolpath grows as data
arrives; `load_file()` is the same decoder fed in one go.
"""
from __future__ import annotations

import io
import json
import math
import re
import struct
import zipfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from . import bgcode

TYPES = ["Travel", "Perimeter", "External perimeter", "Overhang perimeter",
         "Internal infill", "Solid infill", "Top solid infill", "Bridge infill",
         "Gap fill", "Skirt/Brim", "Support material", "Support material interface",
         "Wipe tower", "Ironing", "Custom", "Other"]
TYPE_ID = {t: i for i, t in enumerate(TYPES)}

# ;TYPE: names of other slicers -> our type (keys lower-case)
_ALIASES = {
    # OrcaSlicer / Bambu Studio
    "outer wall": "External perimeter", "inner wall": "Perimeter", "overhang wall": "Overhang perimeter",
    "sparse infill": "Internal infill", "internal solid infill": "Solid infill",
    "top surface": "Top solid infill", "bottom surface": "Solid infill",
    "bridge": "Bridge infill", "internal bridge": "Bridge infill", "gap infill": "Gap fill",
    "skirt": "Skirt/Brim", "brim": "Skirt/Brim", "support": "Support material",
    "support transition": "Support material", "prime tower": "Wipe tower",
    "support interface": "Support material interface", "floating vertical shell": "Perimeter",
    # SuperSlicer
    "internal perimeter": "Perimeter", "thin wall": "Gap fill", "internal bridge infill": "Bridge infill",
    # Cura
    "wall-outer": "External perimeter", "wall-inner": "Perimeter", "skin": "Solid infill",
    "fill": "Internal infill", "support-interface": "Support material interface",
    "support-infill": "Support material", "prime-tower": "Wipe tower",
}
TYPE_LOOKUP = {t.lower(): TYPE_ID[t] for t in TYPES}
TYPE_LOOKUP.update({k: TYPE_ID[v] for k, v in _ALIASES.items()})

ARC_STEP_MM = 0.4          # chord length used to flatten G2/G3 arcs
MIN_LAYER_STEP = 0.05      # Z rise that starts a new layer when a file has no markers
META_KEYS = ("filament_colour", "bed_shape", "printer_model", "filament_type", "layer_height", "nozzle_diameter")
META_ALIASES = {"printable_area": "bed_shape"}   # Bambu Studio / OrcaSlicer name for the bed outline
ASCII_TAIL = 300_000       # PrusaSlicer keeps its settings block at the end of plain .gcode, Bambu at the start
MAX_TOOL = 63              # T255 / T1000 in Bambu start G-code are no real tools

_WORD = re.compile(rb"([A-Z])(-?\d*\.?\d+)")
_GNUM = re.compile(rb"G(\d+)")


@dataclass
class Toolpath:
    start: array = field(default_factory=lambda: array("f"))
    end: array = field(default_factory=lambda: array("f"))
    wh: array = field(default_factory=lambda: array("f"))
    type: array = field(default_factory=lambda: array("B"))
    line_off: array = field(default_factory=lambda: array("I"))
    line_starts: array = field(default_factory=lambda: array("I"))   # every line, for sync checks
    layer_z: list = field(default_factory=list)
    layer_start: list = field(default_factory=list)
    tool_changes: list = field(default_factory=list)   # [segment index, tool] at every T<n> (XL tools, MMU filaments)
    meta: dict = field(default_factory=dict)
    text_size: int = 0
    complete: bool = False      # False while the job file is still arriving
    rewritten: bool = False     # finish() changed segments already handed out (layers/heights derived)
    # incremental bbox / type statistics (see _scan)
    _scanned: int = field(default=0, repr=False)
    _lo: list = field(default_factory=lambda: [math.inf] * 3, repr=False)
    _hi: list = field(default_factory=lambda: [-math.inf] * 3, repr=False)
    _lo_all: list = field(default_factory=lambda: [math.inf] * 3, repr=False)
    _hi_all: list = field(default_factory=lambda: [-math.inf] * 3, repr=False)
    _types: set = field(default_factory=set, repr=False)

    @property
    def count(self) -> int:
        # `type` is appended last for every segment, so start/end/wh always hold at least this many
        return len(self.type)

    def _scan(self):
        n = self.count
        custom = TYPE_ID["Custom"]
        st, en, ty = self.start, self.end, self.type
        lo, hi, la, ha = self._lo, self._hi, self._lo_all, self._hi_all
        for j in range(self._scanned, n):
            t = ty[j]
            self._types.add(t)
            b = 3 * j
            for arr in (st, en):
                for a in range(3):
                    v = arr[b + a]
                    if v < la[a]:
                        la[a] = v
                    if v > ha[a]:
                        ha[a] = v
                    if t != custom:
                        if v < lo[a]:
                            lo[a] = v
                        if v > hi[a]:
                            hi[a] = v
        self._scanned = n

    def bbox(self, skip_custom: bool = True):
        self._scan()
        lo, hi = (self._lo, self._hi) if skip_custom and self._lo[0] != math.inf else (self._lo_all, self._hi_all)
        if lo[0] == math.inf:
            return [0, 0, 0, 0, 0, 0]
        return [round(v, 3) for v in lo + hi]

    def to_bin(self, start: int = 0, stop: int | None = None) -> bytes:
        """Segments [start, stop) in the viewer layout (planar arrays, see module doc)."""
        n = self.count if stop is None else min(stop, self.count)
        a = max(0, min(start, n))
        out = (self.start[3 * a:3 * n].tobytes() + self.end[3 * a:3 * n].tobytes()
               + self.wh[2 * a:2 * n].tobytes() + self.type[a:n].tobytes())
        return out + b"\0" * (-len(out) % 4)

    def info(self) -> dict:
        n = self.count
        bb = self.bbox()
        out = {
            "count": n,
            "complete": self.complete,
            "rewritten": self.rewritten,
            "layers": [[round(z, 3), s] for z, s in zip(self.layer_z, self.layer_start) if s <= n],
            "bbox": bb,
            "types": TYPES,
            "types_present": sorted(self._types),
            "tool_changes": [[s, t] for s, t in self.tool_changes if s <= n],
            **self.meta,
        }
        if "filament_colour" in self.meta and "filament_colours" not in self.meta:
            out.update(_filaments(self.meta))
        return out


def _filaments(meta: dict) -> dict:
    """filament_colour / filament_type are ';'-separated, one entry per tool (or MMU filament)."""
    cols = [c.strip().strip('"') for c in str(meta.get("filament_colour", "")).split(";")]
    cols = [c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else "#FF8000" for c in cols] or ["#FF8000"]
    out = {"filament_colours": cols, "filament_colour": cols[0]}
    if meta.get("filament_type"):
        out["filament_types"] = [t.strip().strip('"') for t in str(meta["filament_type"]).split(";")]
    return out


def _type_id(name: bytes) -> int:
    return TYPE_LOOKUP.get(name.decode("utf-8", "replace").strip().lower(), TYPE_ID["Other"])


class GcodeParser:
    """Incremental G-code -> Toolpath. feed() any chunks; finish() once at the end."""

    def __init__(self, default_width: float = 0.45, tp: Toolpath | None = None):
        self.tp = tp or Toolpath()
        self._rest = b""
        self._offset = 0
        self._s = {"x": 0.0, "y": 0.0, "z": 0.0, "abs_xyz": True, "abs_e": False, "e_abs_last": 0.0,
                   "cur_type": TYPE_ID["Custom"], "width": default_width, "height": 0.2, "saw_height": False}

    def feed(self, data: bytes):
        if not data:
            return
        buf = self._rest + data if self._rest else data
        lines = buf.split(b"\n")
        self._rest = lines.pop()
        self._run(lines)

    def finish(self) -> Toolpath:
        self._run([self._rest])          # last line (may be empty, like split() of the whole text)
        self._rest = b""
        tp = self.tp
        tp.text_size = self._offset - 1
        if not tp.layer_z:
            _derive_layers(tp)
            tp.rewritten = tp.rewritten or bool(tp.layer_z)
        if not self._s["saw_height"]:
            _heights_from_layers(tp)
            tp.rewritten = True
        tp.complete = True
        return tp

    def _run(self, lines):
        tp, s = self.tp, self._s
        st, en, wh, ty, lo, starts = tp.start, tp.end, tp.wh, tp.type, tp.line_off, tp.line_starts
        x, y, z = s["x"], s["y"], s["z"]
        abs_xyz, abs_e, e_abs_last = s["abs_xyz"], s["abs_e"], s["e_abs_last"]
        cur_type, width, height, saw_height = s["cur_type"], s["width"], s["height"], s["saw_height"]

        def add(x0, y0, z0, x1, y1, z1, off):
            st.extend((x0, y0, z0))
            en.extend((x1, y1, z1))
            wh.extend((width, height))
            lo.append(off)
            ty.append(cur_type)          # last: Toolpath.count = len(type)

        offset = self._offset
        for raw in lines:
            line_off = offset
            offset += len(raw) + 1
            starts.append(line_off)
            if not raw:
                continue
            c = raw[0]
            if c in (32, 9, 13):                 # indented or CR-only line
                raw = raw.strip()
                if not raw:
                    continue
                c = raw[0]
            if c == 78:                          # 'N123 G1 ...' line numbers
                raw = raw.split(b" ", 1)[1].lstrip() if b" " in raw else b""
                if not raw:
                    continue
                c = raw[0]
            if c == 59:  # ';'
                if raw.startswith(b"; ") and len(raw) > 9:    # Bambu Studio: "; FEATURE: Outer wall" ...
                    if raw.startswith(b"; FEATURE:"):
                        cur_type = _type_id(raw[10:])
                    elif raw.startswith(b"; LINE_WIDTH:"):
                        try:
                            width = float(raw[13:])
                        except ValueError:
                            pass
                    elif raw.startswith(b"; LAYER_HEIGHT:"):
                        try:
                            height = float(raw[15:])
                            saw_height = True
                        except ValueError:
                            pass
                    elif raw.startswith(b"; Z_HEIGHT:"):
                        try:
                            zl = float(raw[11:])
                            if not (tp.layer_z and tp.layer_start[-1] == len(ty) and abs(tp.layer_z[-1] - zl) < 1e-6):
                                tp.layer_z.append(zl)
                                tp.layer_start.append(len(ty))
                        except ValueError:
                            pass
                elif raw.startswith(b";TYPE:"):
                    cur_type = _type_id(raw[6:])
                elif raw.startswith(b";WIDTH:"):
                    try:
                        width = float(raw[7:])
                    except ValueError:
                        pass
                elif raw.startswith(b";HEIGHT:"):
                    try:
                        height = float(raw[8:])
                        saw_height = True
                    except ValueError:
                        pass
                elif raw.startswith(b";Z:"):
                    try:
                        zl = float(raw[3:])
                        if not (tp.layer_z and tp.layer_start[-1] == len(ty) and abs(tp.layer_z[-1] - zl) < 1e-6):
                            tp.layer_z.append(zl)
                            tp.layer_start.append(len(ty))
                    except ValueError:
                        pass
                continue
            if 97 <= c <= 122:                   # lower-case g-code
                raw = raw.upper()
                c = raw[0]
            if c == 71:  # 'G'
                code = raw.split(b";", 1)[0]
                m = _GNUM.match(code)
                if not m:
                    continue
                g = int(m.group(1))
                if g <= 3:
                    w = {k: float(v) for k, v in _WORD.findall(code, m.end())}
                    if abs_xyz:
                        nx, ny, nz = w.get(b"X", x), w.get(b"Y", y), w.get(b"Z", z)
                    else:
                        nx, ny, nz = x + w.get(b"X", 0.0), y + w.get(b"Y", 0.0), z + w.get(b"Z", 0.0)
                    e = w.get(b"E")
                    if e is not None and abs_e:
                        e, e_abs_last = e - e_abs_last, e
                    if e and e > 0 and (nx != x or ny != y):
                        if g >= 2 and (b"I" in w or b"J" in w):
                            cx, cy = x + w.get(b"I", 0.0), y + w.get(b"J", 0.0)
                            r = math.hypot(x - cx, y - cy)
                            a0 = math.atan2(y - cy, x - cx)
                            sweep = math.atan2(ny - cy, nx - cx) - a0
                            if g == 2 and sweep >= 0:
                                sweep -= 2 * math.pi
                            elif g == 3 and sweep <= 0:
                                sweep += 2 * math.pi
                            n = max(1, math.ceil(abs(sweep) * r / ARC_STEP_MM))
                            px, py, pz = x, y, z
                            for i in range(1, n + 1):
                                if i == n:
                                    qx, qy = nx, ny
                                else:
                                    a = a0 + sweep * i / n
                                    qx, qy = cx + r * math.cos(a), cy + r * math.sin(a)
                                qz = z + (nz - z) * i / n
                                add(px, py, pz, qx, qy, qz, line_off)
                                px, py, pz = qx, qy, qz
                        else:
                            add(x, y, z, nx, ny, nz, line_off)
                    x, y, z = nx, ny, nz
                elif g == 90:
                    abs_xyz = True
                elif g == 91:
                    abs_xyz = False
                elif g == 92:
                    w = {k: float(v) for k, v in _WORD.findall(code, m.end())}
                    x, y, z = w.get(b"X", x), w.get(b"Y", y), w.get(b"Z", z)
                    if b"E" in w:
                        e_abs_last = w[b"E"]
            elif c == 77:  # 'M'
                if raw.startswith(b"M82"):
                    abs_e = True
                elif raw.startswith(b"M83"):
                    abs_e = False
            elif c == 84 and len(raw) > 1 and 48 <= raw[1] <= 57:   # 'T<n>': tool / filament change
                j = 1
                while j < len(raw) and 48 <= raw[j] <= 57:
                    j += 1
                tool = int(raw[1:j])
                if tool <= MAX_TOOL and tool != s.get("tool", -1):
                    s["tool"] = tool
                    tp.tool_changes.append((len(ty), tool))

        self._offset = offset
        s.update(x=x, y=y, z=z, abs_xyz=abs_xyz, abs_e=abs_e, e_abs_last=e_abs_last,
                 cur_type=cur_type, width=width, height=height, saw_height=saw_height)


def parse_gcode(text: bytes, default_width: float = 0.45) -> Toolpath:
    p = GcodeParser(default_width)
    p.feed(text)
    return p.finish()


def _derive_layers(tp: Toolpath):
    """No ;Z: markers (e.g. Cura): a new layer starts where extrusion rises in Z."""
    last = -math.inf
    for j in range(tp.count):
        zj = tp.start[3 * j + 2]
        if zj > last + MIN_LAYER_STEP:
            tp.layer_z.append(round(zj, 4))
            tp.layer_start.append(j)
            last = zj


def _heights_from_layers(tp: Toolpath):
    """No ;HEIGHT: comments: layer height = distance to the previous layer."""
    bounds = tp.layer_start + [tp.count]
    prev_z = 0.0
    for i, z in enumerate(tp.layer_z):
        h = max(0.05, min(z - prev_z, 1.0)) if z > prev_z else 0.2
        for j in range(bounds[i], bounds[i + 1]):
            tp.wh[2 * j + 1] = h
        prev_z = z


def _nozzle_width(meta: dict) -> float:
    try:
        nozzle = float(str(meta.get("nozzle_diameter", "0.4")).split(",")[0])
    except ValueError:
        nozzle = 0.4
    return round(nozzle * 1.125, 3)


class JobDecoder:
    """Job file bytes (.bgcode, plain .gcode or a Bambu .gcode.3mf, any chunk sizes) -> growing Toolpath.

    `tp` is valid from the first byte on and only ever grows until finish();
    other threads may read it meanwhile (arrays are append-only). A .3mf is a zip whose
    directory sits at the end: it is parsed once complete. `plate`: which plate's G-code
    (Metadata/plate_<n>.gcode) - None = the only one there is, else plate 1.
    """

    def __init__(self, plate: int | None = None):
        self.tp = Toolpath()
        self.plate = plate
        self.fmt: str | None = None          # "bgcode" | "gcode" | "3mf"
        self._headtext = b""                 # gcode: first ASCII_TAIL bytes (Bambu writes its settings there)
        self.bytes_in = 0
        self._buf = bytearray()              # bgcode: unparsed bytes; gcode: rolling tail
        self._head = b""
        self._cks = 0
        self._parser: GcodeParser | None = None

    # ------------------------------------------------------------------ input
    def feed(self, data: bytes):
        if not data:
            return
        self.bytes_in += len(data)
        if self.fmt is None:
            self._head += data
            if len(self._head) < 10:
                return
            data, self._head = self._head, b""
            if data[:4] == b"GCDE":
                self.fmt = "bgcode"
                _, self._cks = struct.unpack_from("<IH", data, 4)
                data = data[10:]
            elif data[:4] == b"PK\x03\x04":
                self.fmt = "3mf"
            else:
                self.fmt = "gcode"
                self._parser = GcodeParser(tp=self.tp)
            self.tp.meta["format"] = self.fmt
        if self.fmt == "bgcode":
            self._buf += data
            self._blocks()
        elif self.fmt == "3mf":
            self._buf += data                      # whole zip: parsed in finish()
        else:
            if len(self._headtext) < ASCII_TAIL:
                self._headtext += data[:ASCII_TAIL - len(self._headtext)]
            self._parser.feed(data)
            self._buf += data
            if len(self._buf) > 2 * ASCII_TAIL:
                del self._buf[:-ASCII_TAIL]

    def finish(self) -> Toolpath:
        tp, meta = self.tp, self.tp.meta
        if self.fmt is None:                       # tiny file (< 10 bytes)
            data, self._head = self._head, b""
            self.fmt = "gcode"
            meta["format"] = "gcode"
            self._parser = GcodeParser(tp=tp)
            self._parser.feed(data)
            self._buf += data
        if self.fmt == "bgcode":
            if self._buf:
                raise ValueError(f"truncated bgcode: {len(self._buf)} bytes after the last block")
            if self._parser is None:               # no G-code blocks at all
                self._parser = GcodeParser(_nozzle_width(meta), tp=tp)
        elif self.fmt == "3mf":
            gcode = self._unzip(bytes(self._buf))
            self._buf = bytearray()
            self._parser = GcodeParser(_nozzle_width(meta), tp=tp)
            self._parser.feed(gcode)
            _scan_meta(meta, gcode[:ASCII_TAIL] + b"\n" + gcode[-ASCII_TAIL:])
        else:
            _scan_meta(meta, self._headtext + b"\n" + bytes(self._buf[-ASCII_TAIL:]))
            self._buf = bytearray()
        self._parser.finish()
        if "filament_colour" in meta:
            meta.update(_filaments(meta))
        return tp

    # ------------------------------------------------------------------ Bambu .gcode.3mf
    def _unzip(self, data: bytes) -> bytes:
        """The plate's G-code; colours, filament types and the printer from the project's settings."""
        meta = self.tp.meta
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            plates = sorted((n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)),
                            key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
            if not plates:
                raise ValueError("3mf without G-code (sliced project expected: Metadata/plate_<n>.gcode)")
            want = f"Metadata/plate_{self.plate}.gcode" if self.plate else None
            name = want if want in plates else plates[0]
            meta["plate"] = int(re.search(r"(\d+)\.gcode$", name).group(1))
            if "Metadata/project_settings.config" in names:
                try:
                    ps = json.loads(z.read("Metadata/project_settings.config"))
                except ValueError:
                    ps = {}
                for k in ("printer_model", "filament_colour", "filament_type", "nozzle_diameter", "printable_area"):
                    v = ps.get(k)
                    if v:
                        meta.setdefault(META_ALIASES.get(k, k), ";".join(map(str, v)) if isinstance(v, list) else str(v))
                if isinstance(ps.get("printable_area"), list):
                    meta["bed_shape"] = ",".join(ps["printable_area"])
            return z.read(name)

    # ------------------------------------------------------------------ bgcode blocks
    def _blocks(self):
        buf, pos = self._buf, 0
        crc = 4 if self._cks == 1 else 0
        while True:
            if len(buf) - pos < 8:
                break
            btype, comp, usize = struct.unpack_from("<HHI", buf, pos)
            hdr = 8
            csize = usize
            if comp != 0:
                if len(buf) - pos < 12:
                    break
                (csize,) = struct.unpack_from("<I", buf, pos + 8)
                hdr = 12
            plen = 6 if btype == 5 else 2
            total = hdr + plen + csize + crc
            if len(buf) - pos < total:
                break
            encoding = struct.unpack_from("<H", buf, pos + hdr)[0] if btype != 5 else 0
            payload = bytes(buf[pos + hdr + plen:pos + hdr + plen + csize])
            self._block(bgcode.Block(btype, comp, usize, csize, encoding, 0, 0, payload))
            pos += total
        if pos:
            del buf[:pos]

    def _block(self, b: bgcode.Block):
        meta = self.tp.meta
        if b.type == 1:
            if self._parser is None:               # metadata blocks come first: nozzle is known now
                self._parser = GcodeParser(_nozzle_width(meta), tp=self.tp)
            self._parser.feed(bgcode.decode_gcode_block(b))
        elif b.type in (2, 3):
            kv = bgcode.parse_kv(bgcode.decompress(b))
            for k in META_KEYS:
                if k in kv and k not in meta:
                    meta[k] = kv[k]


def _scan_meta(meta: dict, text: bytes):
    """'; key = value' settings lines (PrusaSlicer at the end, Bambu Studio at the start)."""
    for k in META_KEYS + tuple(META_ALIASES):
        if k == "layer_height":
            continue
        key = META_ALIASES.get(k, k)
        if key in meta:
            continue
        m = re.search(rb"^; " + k.encode() + rb" = (.*?)\r?$", text, re.M)
        if m:
            meta[key] = m.group(1).decode("utf-8", "replace").strip()


def decode_bytes(buf: bytes) -> Toolpath:
    d = JobDecoder()
    d.feed(buf)
    return d.finish()


def load_file(path: Path) -> Toolpath:
    return decode_bytes(Path(path).read_bytes())
