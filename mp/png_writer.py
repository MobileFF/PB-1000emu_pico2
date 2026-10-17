"""
Minimal streaming PNG encoder (RGB888, no alpha).

Written so the screen-capture menu (`emulator_menu_capture.py`) can save
`.png` instead of (or alongside) `.ppm` -- PNG is a format ordinary OS file
browsers/viewers thumbnail and open directly, unlike PPM which usually needs
a separate tool (ImageMagick/GIMP/etc.) to even preview.

Why *stored* (uncompressed) deflate blocks, not real DEFLATE compression:
MicroPython's `deflate` module on this build only supports *decompression*
(`MICROPY_PY_DEFLATE_COMPRESS` needs the port's ROM level raised to
FULL_FEATURES, which this project's `mpconfigport.h` doesn't set). Even if
it were enabled, `deflate.DeflateIO` write mode needs a sliding-window
buffer (up to 32 KiB for zlib) allocated from the MicroPython heap for the
life of the write -- unacceptable right next to the VRAM/color-VRAM
snapshots this is called alongside (see `emulator_menu_capture.py`'s
`gc.collect()` calls and its comment about a MemoryError from holding a
whole file in RAM).

Deflate's "stored" block type (RFC 1951 §3.2.4) is a legal degenerate case:
each block just holds raw bytes behind a 5-byte header (no Huffman table,
no back-references, no sliding window needed at all). A PNG built entirely
from stored blocks is a 100%-conforming PNG file -- any viewer opens it --
but its IDAT data ends up about the same size as the raw pixel data (plus
~1 byte/row of PNG filtering overhead and a little per-chunk framing), i.e.
*not* actually compressed. That tradeoff is intentional here: it trades
file size for zero extra heap pressure, matching the row-by-row streaming
(`f.write()` per scanline, never a whole-image buffer) the PPM writers in
`emulator_menu_capture.py` already rely on.

Usage (drop-in for the existing per-row PPM loops):

    enc = PngWriter(f, width, height)
    for y in range(height):
        row = bytearray(width * 3)      # RGB888, one scanline
        ...                              # fill row exactly as for PPM
        enc.write_row(row)
    enc.close()
"""

import struct as _struct
from array import array as _array


def _make_crc_table():
    table = [0] * 256
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if (c & 1) else 0)
        table[n] = c
    return _array('I', table)  # packed uint32s -- ~1 KiB, not 256 boxed ints


_CRC_TABLE = _make_crc_table()
_CRC_INIT = 0xFFFFFFFF
_ADLER_MOD = 65521


def _crc32_update(crc, data):
    t = _CRC_TABLE
    for byte in data:
        crc = t[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


def _u32be(n):
    return _struct.pack(">I", n & 0xFFFFFFFF)


def _write_chunk(f, chunk_type, data):
    f.write(_u32be(len(data)))
    f.write(chunk_type)
    f.write(data)
    crc = _crc32_update(_crc32_update(_CRC_INIT, chunk_type), data) ^ _CRC_INIT
    f.write(_u32be(crc))


class PngWriter:
    """Streaming RGB888 PNG encoder. Call `write_row()` once per scanline,
    top to bottom, then `close()`. Each row becomes its own IDAT chunk, so
    no whole-image buffer is ever held (see module docstring) -- only this
    object's small running state (two adler32 accumulators) persists
    between calls.
    """

    def __init__(self, f, width, height):
        self._f = f
        self._h = height
        self._y = 0
        self._adler_a = 1
        self._adler_b = 0
        self._wrote_zlib_header = False
        f.write(b"\x89PNG\r\n\x1a\n")  # the 8-byte PNG file signature
        _write_chunk(f, b"IHDR", _struct.pack(
            ">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            # bit depth=8, color type=2 (truecolor RGB), compression/filter/
            # interlace methods all 0 (the only values PNG defines).

    def write_row(self, rgb_row):
        """`rgb_row` must be exactly `width * 3` bytes (RGB888). Rows over
        ~65000 bytes (width over ~21000) would overflow the stored-block
        LEN field -- not a real concern for this project's LCD sizes."""
        f = self._f
        last = (self._y == self._h - 1)

        # PNG filter byte (0 = "None", i.e. row stored as-is) + raw pixels.
        payload = bytearray(1 + len(rgb_row))
        payload[1:] = rgb_row

        # adler32 over the *uncompressed* zlib stream content (this
        # filtered scanline). The two mod-65521 reductions are deferred to
        # the end of the row -- safe since a row here is always far under
        # the ~5500-byte point where the running sums could overflow a
        # small int (see RFC 1950's NMAX).
        a, b = self._adler_a, self._adler_b
        s = 0
        for byte in payload:
            a += byte
            s += a
        self._adler_a = a % _ADLER_MOD
        self._adler_b = (b + s) % _ADLER_MOD

        idat = bytearray()
        if not self._wrote_zlib_header:
            idat += b"\x78\x01"  # zlib header: CM=8 (deflate), FLEVEL=0
            self._wrote_zlib_header = True
        # Deflate "stored" block (RFC 1951 §3.2.4): 1-byte header
        # (BFINAL | BTYPE=00; always byte-aligned here since every block
        # in this stream starts on a byte boundary), then LEN/NLEN
        # (16-bit little-endian), then the raw bytes themselves.
        n = len(payload)
        idat += bytes((1 if last else 0,))
        idat += _struct.pack("<HH", n, (~n) & 0xFFFF)
        idat += payload
        if last:
            idat += _u32be((self._adler_b << 16) | self._adler_a)

        _write_chunk(f, b"IDAT", idat)
        self._y += 1

    def close(self):
        _write_chunk(self._f, b"IEND", b"")
