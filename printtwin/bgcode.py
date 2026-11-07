"""Pure-Python reader for Prusa binary G-code (.bgcode).

Spec: https://github.com/prusa3d/libbgcode/blob/main/doc/specifications.md
File  = header("GCDE", u32 version, u16 checksum_type) + blocks
Block = u16 type, u16 compression, u32 uncompressed_size, [u32 compressed_size]
        + params (thumbnail: 6 bytes, others: u16 encoding) + data + [u32 crc32]
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

BLOCK_TYPES = {0: "file_metadata", 1: "gcode", 2: "slicer_metadata",
               3: "printer_metadata", 4: "print_metadata", 5: "thumbnail"}
COMPRESSION = {0: "none", 1: "deflate", 2: "heatshrink_11_4", 3: "heatshrink_12_4"}
ENCODING = {0: "none", 1: "meatpack", 2: "meatpack_comments"}


@dataclass
class Block:
    type: int
    compression: int
    usize: int
    csize: int
    encoding: int
    file_offset: int        # offset of the block header in the file
    data_offset: int        # offset of the (compressed) payload in the file
    payload: bytes = field(repr=False, default=b"")

    @property
    def name(self) -> str:
        return BLOCK_TYPES.get(self.type, f"type{self.type}")


def heatshrink_decode(data: bytes, window: int, lookahead: int, out_size: int) -> bytes:
    out = bytearray()
    n = len(data)
    i = 0
    acc = 0
    nbits = 0

    def bits(k: int) -> int:
        nonlocal acc, nbits, i
        while nbits < k:
            if i >= n:
                raise EOFError
            acc = ((acc & ((1 << nbits) - 1)) << 8) | data[i]
            i += 1
            nbits += 8
        nbits -= k
        return (acc >> nbits) & ((1 << k) - 1)

    try:
        while len(out) < out_size:
            if bits(1):
                out.append(bits(8))
            else:
                offset = bits(window) + 1
                count = bits(lookahead) + 1
                start = len(out) - offset
                if offset >= count:
                    out += out[start:start + count]
                else:
                    for k in range(count):
                        out.append(out[start + k])
    except EOFError:
        pass
    return bytes(out[:out_size])


_MP_CHARS = b"0123456789. \nGX"


def meatpack_decode(data: bytes) -> bytes:
    """MeatPack -> plain text, as the firmware sees it (no spaces re-inserted)."""
    out = bytearray()
    packing = False
    nospace = False
    cmd_active = False
    ff_seen = False
    full_queue = 0
    char_buf = 0

    def get_char(nib: int) -> int:
        if nib == 0b1011 and nospace:
            return ord("E")
        return _MP_CHARS[nib]

    def inner(c: int):
        nonlocal full_queue, char_buf
        if not packing:
            out.append(c)
            return
        if full_queue > 0:
            out.append(c)
            if char_buf:
                out.append(char_buf)
                char_buf = 0
            full_queue -= 1
            return
        lo, hi = c & 0xF, (c >> 4) & 0xF
        if lo == 0xF:
            full_queue += 1
            if hi == 0xF:
                full_queue += 1
            else:
                char_buf = get_char(hi)
        else:
            ch = get_char(lo)
            out.append(ch)
            if ch != 0x0A:
                if hi == 0xF:
                    full_queue += 1
                else:
                    out.append(get_char(hi))

    for c in data:
        if cmd_active:
            if c == 251:
                packing = True
            elif c in (250, 249):
                packing = False
            elif c == 247:
                nospace = True
            elif c == 246:
                nospace = False
            cmd_active = False
        elif ff_seen:
            ff_seen = False
            if c == 0xFF:
                cmd_active = True
            else:
                inner(0xFF)
                inner(c)
        elif c == 0xFF:
            ff_seen = True
        else:
            inner(c)
    return bytes(out)


def read_blocks(buf: bytes, with_payload: bool = True) -> tuple[dict, list[Block]]:
    if buf[:4] != b"GCDE":
        raise ValueError("not a bgcode file")
    version, cks = struct.unpack_from("<IH", buf, 4)
    pos = 10
    blocks: list[Block] = []
    while pos < len(buf):
        hdr_pos = pos
        btype, comp, usize = struct.unpack_from("<HHI", buf, pos)
        pos += 8
        csize = usize
        if comp != 0:
            (csize,) = struct.unpack_from("<I", buf, pos)
            pos += 4
        plen = 6 if btype == 5 else 2
        params = buf[pos:pos + plen]
        pos += plen
        encoding = struct.unpack_from("<H", params, 0)[0] if btype != 5 else 0
        data_off = pos
        payload = buf[pos:pos + csize] if with_payload else b""
        pos += csize
        if cks == 1:
            pos += 4
        blocks.append(Block(btype, comp, usize, csize, encoding, hdr_pos, data_off, payload))
    return {"version": version, "checksum": cks}, blocks


def decompress(b: Block) -> bytes:
    if b.compression == 0:
        return b.payload
    if b.compression == 1:
        return zlib.decompress(b.payload)
    if b.compression == 2:
        return heatshrink_decode(b.payload, 11, 4, b.usize)
    if b.compression == 3:
        return heatshrink_decode(b.payload, 12, 4, b.usize)
    raise ValueError(f"unknown compression {b.compression}")


def decode_gcode_block(b: Block) -> bytes:
    raw = decompress(b)
    return meatpack_decode(raw) if b.encoding in (1, 2) else raw


def parse_kv(text: bytes) -> dict:
    d = {}
    for line in text.decode("utf-8", "replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d
