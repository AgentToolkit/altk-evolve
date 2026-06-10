#!/usr/bin/env python3
"""Detect image format by magic bytes and print dimensions.

Supports PNG, GIF87a/GIF89a, BMP (BITMAPINFOHEADER), WebP (VP8/VP8L/VP8X).
Usage: python3 read_dim.py <path>
"""
from __future__ import annotations
import struct, sys
from pathlib import Path


def png(d: bytes) -> str:
    # Width/height at offsets 16-24 (>II, big-endian).
    w, h = struct.unpack(">II", d[16:24])
    return f"{w}x{h}"


def gif(d: bytes) -> str:
    # 6-byte signature + 14-byte LSD; w/h at 6-10 (<HH, little-endian).
    ver = d[0:6].decode("ascii", "replace")
    w, h = struct.unpack("<HH", d[6:10])
    return f"{ver} {w}x{h}"


def bmp(d: bytes) -> str:
    # 14-byte file header + BITMAPINFOHEADER. Width at 18 (<I), bit depth at 28 (<H).
    width = struct.unpack("<I", d[18:22])[0]
    bpp = struct.unpack("<H", d[28:30])[0]
    height = struct.unpack("<i", d[22:26])[0]  # signed: BMP allows negative for top-down
    return f"{width}x{abs(height)} {bpp}bpp"


def webp(d: bytes) -> str:
    # RIFF...WEBP container. Chunk type at 12-16 dispatches.
    chunk = d[12:16]
    if chunk == b"VP8 ":
        # Lossy: 14-bit (w-1) and (h-1) at offsets 26-30 (little-endian, masked).
        w = struct.unpack("<H", d[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", d[28:30])[0] & 0x3FFF
        return f"{w}x{h}"
    if chunk == b"VP8L":
        # Lossless: 1-byte 0x2F sig, then 4 bytes packed: 14b (w-1) + 14b (h-1) + alpha + version.
        bits = struct.unpack("<I", d[21:25])[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return f"{w}x{h}"
    if chunk == b"VP8X":
        # Extended: 24-bit (w-1) at 24-27, 24-bit (h-1) at 27-30 (both little-endian).
        w = (d[24] | d[25] << 8 | d[26] << 16) + 1
        h = (d[27] | d[28] << 8 | d[29] << 16) + 1
        return f"{w}x{h}"
    raise ValueError(f"unknown WebP chunk: {chunk!r}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: read_dim.py <path>", file=sys.stderr)
        return 2
    p = Path(sys.argv[1])
    d = p.read_bytes()
    if d[:8] == b"\x89PNG\r\n\x1a\n":
        print(png(d)); return 0
    if d[:6] in (b"GIF87a", b"GIF89a"):
        print(gif(d)); return 0
    if d[:2] == b"BM":
        print(bmp(d)); return 0
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        print(webp(d)); return 0
    print(f"unrecognized format: {d[:16].hex()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
