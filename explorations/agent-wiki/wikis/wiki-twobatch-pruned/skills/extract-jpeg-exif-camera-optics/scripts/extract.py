#!/usr/bin/env python3
"""Read one camera-optics EXIF tag from a JPEG via stdlib struct.

Walks: SOI -> APP1 (0xFFE1) -> 'Exif\\x00\\x00' -> TIFF -> IFD0 ->
Exif sub-IFD (via tag 0x8769) -> requested tag.

Usage:
    python3 extract.py <jpeg-path> <tag-hex>
        e.g. python3 extract.py sample.jpg 0xA434
"""

from __future__ import annotations
import struct, sys
from pathlib import Path

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _read_ifd(exif: bytes, off: int, bo: str) -> dict[int, tuple[int, int, bytes]]:
    n = struct.unpack(bo + "H", exif[off : off + 2])[0]
    out: dict[int, tuple[int, int, bytes]] = {}
    for k in range(n):
        e = off + 2 + k * 12
        tag, typ, cnt = struct.unpack(bo + "HHI", exif[e : e + 8])
        size = TYPE_SIZE.get(typ, 1) * cnt
        valoff = e + 8
        if size <= 4:
            raw = exif[valoff : valoff + 4]
        else:
            ptr = struct.unpack(bo + "I", exif[valoff : valoff + 4])[0]
            raw = exif[ptr : ptr + size]
        out[tag] = (typ, cnt, raw)
    return out


def _ascii(raw: bytes) -> str:
    return raw.split(b"\x00")[0].decode("ascii", "replace")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract.py <jpeg-path> <tag-hex>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    target = int(sys.argv[2], 16)
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        print("not a JPEG", file=sys.stderr)
        return 1
    i = 2
    exif: bytes | None = None
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xE1:
            seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
            seg = data[i + 4 : i + 2 + seglen]
            if seg[:6] == b"Exif\x00\x00":
                exif = seg[6:]
                break
            i += 2 + seglen
        elif marker in (0xD8, 0xD9):
            i += 2
        else:
            seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seglen
    if exif is None:
        print("no EXIF found", file=sys.stderr)
        return 1
    bo = "<" if exif[:2] == b"II" else ">"
    ifd0_off = struct.unpack(bo + "I", exif[4:8])[0]
    ifd0 = _read_ifd(exif, ifd0_off, bo)
    if target in ifd0:
        typ, cnt, raw = ifd0[target]
    elif 0x8769 in ifd0:
        sub_off = struct.unpack(bo + "I", ifd0[0x8769][2])[0]
        sub = _read_ifd(exif, sub_off, bo)
        if target not in sub:
            print(f"tag 0x{target:04X} not present", file=sys.stderr)
            return 1
        typ, cnt, raw = sub[target]
    else:
        print("no Exif sub-IFD (0x8769) and tag not in IFD0", file=sys.stderr)
        return 1
    if typ == 2:
        print(_ascii(raw))
    elif typ == 3:
        print(struct.unpack(bo + "H", raw[:2])[0])
    elif typ == 4:
        print(struct.unpack(bo + "I", raw[:4])[0])
    elif typ == 5:
        num, den = struct.unpack(bo + "II", raw[:8])
        print(f"{num}/{den}" if den != 1 else str(num))
    else:
        print(raw.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
