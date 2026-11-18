"""md100_link.py -- MD-100 byte protocol layer unit test (host CPython).

Run:
    python3 mp/test/test_md100_link.py
"""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

import fdd_storage
import md100_volume as V
from md100_volume import Md100Volume
from md100_link import Md100Link


class ReadOnlyStorage(fdd_storage.MemoryStorageBackend):
    def write_raw(self, sector, data):
        return False


def _name(s):
    b = bytearray(11)
    for i, ch in enumerate(s.encode()[:11]):
        b[i] = ch
    return bytes(b)


def _setup(sectors=256):
    st = fdd_storage.MemoryStorageBackend(sectors)
    vol = Md100Volume(st)
    assert vol.format()
    return Md100Link(vol), vol, st


def _xfer(link, seq):
    return [link.exchange(b) for b in seq]


def _lencmd(link, cmd, payload):
    """cmd, L0, L1, payload. Returns (accept, len replies, payload replies)."""
    L = len(payload)
    r = _xfer(link, [cmd, L & 0xFF, L >> 8] + list(payload))
    return r[0], r[1:3], r[3:]


def _result(link):
    """Exec dummy -> N lo, dummy -> N hi, then N bytes."""
    lo = link.exchange(0)
    hi = link.exchange(0)
    n = lo | (hi << 8)
    return bytes(_xfer(link, [0] * n))


def _open(link, cmd, h, name, kind=0x24):
    a, lr, pr = _lencmd(link, cmd, [0, h, kind] + list(_name(name)))
    assert a == 0 and lr == [0, 0] and all(x == 0 for x in pr)
    return _result(link)


def _write(link, h, data, rec=None):
    if rec is None:
        payload = [0, h] + list(data)
        cmd = 0x10
    else:
        payload = [0, h, rec & 0xFF, rec >> 8] + list(data)
        cmd = 0x11
    a, lr, pr = _lencmd(link, cmd, payload)
    assert a == 0 and lr == [0, 0]
    assert all(x == 0 for x in pr), pr
    r = _xfer(link, [0, 0, 0])
    assert r[0] == 0x01 and r[1] == 0x00
    return r[2]


def _read(link, h, rec=None):
    if rec is None:
        _lencmd(link, 0x20, [0, h])
    else:
        _lencmd(link, 0x21, [0, h, rec & 0xFF, rec >> 8])
    return _result(link)


def _close(link, h):
    assert link.exchange(0x40) == 0
    return link.exchange(h)


def _idle_ok(link):
    """Link is back in the idle state: unknown command answers 0x20."""
    assert link.exchange(0xEE) == 0x20


# ------------------------------------------------------------------
def test_no_volume_and_unknown():
    link = Md100Link()
    assert link.exchange(0x00) == 0x04
    assert link.exchange(0xEE) == 0x04
    vol = Md100Volume()
    link.attach(vol)
    assert link.exchange(0x30) == 0x04
    link, vol, st = _setup()
    assert link.exchange(0xEE) == 0x20
    assert link.exchange(0x41) == 0x20
    link.attach(None)
    assert link.exchange(0x40) == 0x04


def test_format_cmd():
    link, vol, st = _setup()
    vol.create(0, _name("A"), 0)
    assert link.exchange(0x90) == 0x00
    assert vol.dir_entry(0)[0] == V.DIR_FREE
    _idle_ok(link)
    ro = Md100Volume(ReadOnlyStorage(64))
    link.attach(ro)
    assert link.exchange(0x90) == 0x04


def test_zero_length():
    link, vol, st = _setup()
    assert _xfer(link, [0x30, 0, 0]) == [0, 0, 0x20]
    _idle_ok(link)
    assert _xfer(link, [0x70, 0, 0]) == [0, 0, 0x20]
    _idle_ok(link)


def test_open_create_variants():
    link, vol, st = _setup()
    # 0x32 on missing file -> NOT_FOUND -> 0x20, N=1
    assert _open(link, 0x32, 1, "NONE") == bytes([0x20])
    assert vol.get_mode(1) == 0x32
    # 0x30 create: N=1, status OK
    assert _open(link, 0x30, 2, "NEW     DAT") == bytes([0x00])
    assert vol.get_mode(2) == 0x30
    # 0x32 on busy handle -> 0x20
    assert _open(link, 0x32, 2, "NEW     DAT") == bytes([0x20])
    # 0x33 open existing: dir entry returned
    r = _open(link, 0x33, 0x13, "NEW     DAT")   # high nibble ignored
    assert len(r) == 17 and r[0] == 0x10
    assert r[1] == 0x24 and r[2:13] == _name("NEW     DAT")
    assert vol.get_mode(3) == 0x33
    # 0x31 on missing -> creates
    r = _open(link, 0x31, 4, "MADE")
    assert r[0] == 0x10 and len(r) == 17
    assert vol.dir_entry(1)[0] == V.DIR_USED
    assert link.exchange(0xEE) == 0x20


def test_open_append_trimming():
    link, vol, st = _setup()
    vol.create(0, _name("APP"), 0)
    vol.write_record(0, bytes(256))
    vol.seek(0, 1)
    vol.write_record(0, b"ABC\x1a" + bytes(252))
    vol.close(0)
    r = _open(link, 0x34, 5, "APP")
    assert len(r) == 20
    assert r[0] == 0x10 and r[17:] == b"ABC"
    assert vol.record_count(5) == 2
    # a single 0x1A only, trailing zeros only
    vol.seek(5, 1)
    vol.write_record(5, b"X\x1a\x1a" + bytes(253))
    vol.close(5)
    r = _open(link, 0x34, 5, "APP")
    assert r[17:] == b"X\x1a"
    # record full of zeros: nothing before offset 17 is trimmed
    vol.seek(5, 1)
    vol.write_record(5, bytes(256))
    vol.close(5)
    r = _open(link, 0x34, 5, "APP")
    assert len(r) == 17 and r[0] == 0x10
    # missing file with 0x34
    assert _open(link, 0x34, 6, "NOFILE") == bytes([0x20])


def test_open_append_busy_handle_no_leak():
    link, vol, st = _setup()
    vol.create(0, _name("APP"), 0)
    vol.write_record(0, b"MINE" + bytes(252))
    vol.close(0)
    vol.create(5, _name("SECRET"), 0)
    vol.write_record(5, b"LEAK" + bytes(252))
    # handle 5 busy with another file: open fails, nothing returned
    assert _open(link, 0x34, 5, "APP") == bytes([0x20])
    _idle_ok(link)


def test_write_exact_256_skips():
    link, vol, st = _setup()
    _open(link, 0x30, 0, "X")
    vol.seek(0, 2)
    vol.write_record(0, bytes(256))          # 3 records
    # exactly 256 bytes into record 1 (not last record -> no 0x1A)
    assert _write(link, 0, bytes([0x42]) * 256, rec=1) == 0x00
    assert _write(link, 0, b"Z") == 0x00      # lands on record 2
    buf = bytearray(256)
    vol.seek(0, 0)
    vol.read_record(0, buf)
    assert buf == bytearray([0x42]) * 256
    vol.seek(0, 1)
    vol.read_record(0, buf)
    # last access was record 0 (not last) -> no 0x1A appended
    assert buf[0] == ord("Z") and buf[1] == 0x00
    # 255 bytes on the last record + 0x1A = exactly 256 -> skip as well
    vol.seek(0, 2)
    vol.read_record(0, buf)                  # last access = last record
    assert _write(link, 0, bytes([0x43]) * 255) == 0x00
    assert _write(link, 0, b"Q") == 0x00
    assert vol.record_count(0) == 4
    vol.seek(0, 2)
    vol.read_record(0, buf)
    assert buf[254] == 0x43 and buf[255] == 0x1A
    vol.seek(0, 3)
    vol.read_record(0, buf)
    assert buf[0] == ord("Q") and buf[1] == 0x1A
    # partially filled record: position stays on it
    assert _write(link, 0, b"ab") == 0x00
    vol.seek(0, 3)
    vol.read_record(0, buf)
    assert buf[:3] == b"ab\x1a"


def test_directory_listing():
    link, vol, st = _setup()
    vol.create(0, _name("A"), 1)
    vol.create(0, _name("B"), 2)
    vol.create(0, _name("C"), 3)
    vol.delete(_name("B"))
    assert link.exchange(0x00) == 0
    r = _result(link)
    assert r[0] == 0x10 and len(r) == 17 and r[2] == ord("A")
    assert link.exchange(0x01) == 0
    r = _result(link)
    assert r[2] == ord("C")                 # free entry 1 skipped
    assert link.exchange(0x01) == 0
    assert _result(link) == bytes([0x01])
    assert link.exchange(0x02) == 0
    r = _result(link)
    assert r[2] == ord("A")                 # backward from 2-1, skipping 1
    assert link.exchange(0x02) == 0
    assert _result(link) == bytes([0x01])   # -1 is invalid
    link.reset()
    assert link.exchange(0x01) == 0
    assert _result(link)[2] == ord("C")
    _idle_ok(link)


def test_close_cmd():
    link, vol, st = _setup()
    _open(link, 0x30, 3, "F")
    assert _close(link, 3) == 0x00
    assert vol.status == V.ST_OK
    assert _close(link, 3) == 0x00          # NOT_OPEN -> 0x00
    assert _close(link, 0x13) == 0x20       # not masked -> BAD_HANDLE
    _open(link, 0x30, 1, "F")
    assert _close(link, 0xFF) == 0x00       # close all
    assert vol.record_count(1) == 0
    _idle_ok(link)


def test_write_and_read_short():
    link, vol, st = _setup()
    _open(link, 0x30, 1, "T")
    assert _write(link, 1, b"HELLO") == 0x00
    raw = bytearray(256)
    vol.seek(1, 0)
    vol.read_record(1, raw)
    assert bytes(raw[:6]) == b"HELLO\x1a" and raw[6:] == bytearray(250)
    # read back via protocol: last record -> trimmed, status EOF
    vol.seek(1, 0)
    r = _read(link, 1)
    assert r == b"\x01HELLO"
    # past end -> NO_DATA 0x04
    r = _read(link, 1)
    assert r == bytes([0x04])
    # closed handle
    r = _read(link, 2)
    assert r == bytes([0x80])
    _idle_ok(link)


def test_write_multi_record_and_read_by_number():
    link, vol, st = _setup()
    _open(link, 0x30, 0, "M")
    data = bytes((i * 7) & 0xFF for i in range(600))
    data = bytes(b if b not in (0, 0x1A) else 1 for b in data)
    assert _write(link, 0, data) == 0x00
    # 600 bytes + 0x1A? at_last_record checked at the end: after two full
    # records the last access is record 1 and file has 2 records -> append.
    assert vol.record_count(0) == 3
    # read record 1 and 2 via 0x21 (1-based)
    r = _read(link, 0, 1)
    assert r[0] == 0x00 and r[1:] == data[:256]
    r = _read(link, 0, 3)
    assert r[0] == 0x01 and r[1:] == data[512:]
    r = _read(link, 0, 0)                   # 0 treated as 1
    assert r[1:] == data[:256]
    _idle_ok(link)


def test_write_257_bytes_two_records():
    link, vol, st = _setup()
    _open(link, 0x30, 0, "E")
    data = bytes([0x41]) * 256
    # exactly 256 bytes of data, file has 1 record, last access 0 -> 0x1A
    # appended giving 257 bytes -> 2 records
    assert _write(link, 0, data) == 0x00
    assert vol.record_count(0) == 2
    buf = bytearray(256)
    vol.seek(0, 1)
    vol.read_record(0, buf)
    assert buf[0] == 0x1A and buf[1:] == bytearray(255)
    # next record stays on the last written record (1)
    assert _write(link, 0, b"Z") == 0x00
    vol.seek(0, 1)
    vol.read_record(0, buf)
    assert buf[0] == ord("Z") and buf[1] == 0x1A


def test_write_0x11_record_number():
    link, vol, st = _setup()
    _open(link, 0x30, 2, "R")
    assert _write(link, 2, b"third", rec=3) == 0x00
    assert vol.record_count(2) == 3
    r = _read(link, 2, 3)
    assert r == b"\x01third"
    # middle record untouched (not initialised -> zeros here)
    r = _read(link, 2, 2)
    assert r == b"\x00" + bytes(256)
    # short payload: no data stage, nothing written
    assert _write(link, 2, b"", rec=1) == 0x00
    a, lr, pr = _lencmd(link, 0x10, [0, 2])
    assert _xfer(link, [0, 0, 0]) == [1, 0, 0]
    _idle_ok(link)


def test_write_not_open_and_readonly():
    link, vol, st = _setup()
    assert _write(link, 7, b"abc") == 0x80
    _open(link, 0x30, 1, "P")
    ro = ReadOnlyStorage(256)
    ro._data[:] = st._data
    vol.attach(ro)
    assert _open(link, 0x32, 1, "P")[0] == 0x10
    assert _write(link, 1, b"abc") == 0x02
    assert _write(link, 1, bytes([0x33]) * 300) == 0x02
    _idle_ok(link)


def test_write_disk_full():
    link, vol, st = _setup(20)              # 5 blocks: 1 data block
    _open(link, 0x30, 0, "F")
    assert _write(link, 0, b"x", rec=4) == 0x00
    assert _write(link, 0, b"y", rec=5) == 0x04
    _idle_ok(link)


def test_delete_cmd():
    link, vol, st = _setup()
    vol.create(0, _name("GONE"), 0)
    payload = [0, 0] + list(_name("GONE"))
    a, lr, pr = _lencmd(link, 0x50, payload)
    assert a == 0 and pr == [0] * len(payload)
    assert _xfer(link, [0, 0, 0]) == [1, 0, 0x10]
    _lencmd(link, 0x50, payload)
    assert _xfer(link, [0, 0, 0]) == [1, 0, 0x00]     # missing -> 0x00
    _idle_ok(link)


def test_rename_cmd():
    link, vol, st = _setup()
    vol.create(0, _name("OLD"), 0)
    vol.create(1, _name("OTHER"), 0)

    def ren(a, b):
        p = [0, 0] + list(_name(a)) + [0] * 6 + list(_name(b))
        _lencmd(link, 0x60, p)
        return _xfer(link, [0, 0, 0])

    assert ren("OLD", "OTHER") == [1, 0, 0x18]
    assert ren("NOPE", "X") == [1, 0, 0x00]
    assert ren("OLD", "NEW") == [1, 0, 0x10]
    assert vol.dir_entry(0)[1][1:12] == _name("NEW")
    _idle_ok(link)


def test_sector_read_write():
    link, vol, st = _setup()
    data = bytes((i ^ 0x5A) & 0xFF for i in range(256))
    # write track 1, sector 3 -> absolute 18
    p = [0, 1, 3] + list(data)
    a, lr, pr = _lencmd(link, 0x70, p)
    assert a == 0 and pr == [0] * len(p)
    assert st.read_raw(18) == data
    _idle_ok(link)
    # read it back
    assert _xfer(link, [0x80, 1, 3]) == [0, 0, 0]
    got = bytes(_xfer(link, [0] * 256))
    assert got == data
    _idle_ok(link)
    # out of range -> 0x20, no data
    assert _xfer(link, [0x80, 0, 0]) == [0, 0, 0x20]
    _idle_ok(link)
    assert _xfer(link, [0x80, 200, 1]) == [0, 0, 0x20]
    _idle_ok(link)
    # write out of range -> last byte answers 0x02
    a, lr, pr = _lencmd(link, 0x70, [0, 200, 1] + list(data))
    assert pr[:-1] == [0] * 258 and pr[-1] == 0x02
    _idle_ok(link)
    # short payload: rest comes from the shared buffer
    a, lr, pr = _lencmd(link, 0x70, [0, 0, 6])
    assert pr == [0, 0, 0]
    assert st.read_raw(5)[:3] == data[:3] and st.read_raw(5)[3:] == data[3:]
    # read-only storage
    ro = ReadOnlyStorage(256)
    vol.attach(ro)
    a, lr, pr = _lencmd(link, 0x70, [0, 1, 1] + list(data))
    assert pr[-1] == 0x02


def test_size_and_free():
    link, vol, st = _setup()
    free0 = vol.free_blocks()
    assert _xfer(link, [0xD0, 0, 0, 0]) == [0, 0, free0 & 0xFF, free0 >> 8]
    _open(link, 0x30, 1, "S")
    _write(link, 1, b"a", rec=6)
    assert _xfer(link, [0xC0, 1, 0, 0, 0]) == [0, 0, 0, 6, 0]
    _close(link, 1)
    _open(link, 0x33, 1, "S")               # bit 0 set -> size-1
    assert _xfer(link, [0xC0, 0x21, 0, 0, 0]) == [0, 0, 0, 5, 0]
    assert _xfer(link, [0xC0, 9, 0, 0, 0]) == [0, 0, 0x80, 0, 0]
    assert _xfer(link, [0xD0, 0, 0, 0]) == [0, 0, (free0 - 2) & 0xFF, 0]
    _idle_ok(link)


def test_buffer_overflow_and_repeat():
    link, vol, st = _setup()
    # 0x70 with 1100 bytes: excess discarded, no crash
    p = [0, 0, 6] + [0x77] * 1097
    a, lr, pr = _lencmd(link, 0x70, p)
    assert len(pr) == 1100
    _idle_ok(link)
    # 0x34 result reads at most 273 bytes; check the 1023 clamp directly
    link._buf[1023] = 0x99
    link._n = 1030
    link._phase = 6      # N hi phase
    link.exchange(0)
    out = _xfer(link, [0] * 1030)
    assert out[1023:] == [0x99] * 7
    _idle_ok(link)


def test_shutdown_reset():
    link, vol, st = _setup()
    _open(link, 0x30, 1, "Q")
    link.exchange(0x20)                     # mid-command
    link.reset()
    _idle_ok(link)
    link.shutdown()
    assert vol.record_count(1) == 0
    Md100Link().shutdown()


# ------------------------------------------------------------------
_TESTS = [
    test_no_volume_and_unknown,
    test_format_cmd,
    test_zero_length,
    test_open_create_variants,
    test_open_append_trimming,
    test_open_append_busy_handle_no_leak,
    test_write_exact_256_skips,
    test_directory_listing,
    test_close_cmd,
    test_write_and_read_short,
    test_write_multi_record_and_read_by_number,
    test_write_257_bytes_two_records,
    test_write_0x11_record_number,
    test_write_not_open_and_readonly,
    test_write_disk_full,
    test_delete_cmd,
    test_rename_cmd,
    test_sector_read_write,
    test_size_and_free,
    test_buffer_overflow_and_repeat,
    test_shutdown_reset,
]

if __name__ == "__main__":
    passed = failed = 0
    for fn in _TESTS:
        print("RUN %-45s" % fn.__name__, end=" ")
        try:
            fn()
            print("PASS")
            passed += 1
        except Exception:
            import traceback
            print("FAIL")
            traceback.print_exc()
            failed += 1
    print("-" * 55)
    print("Tests: %d  Passed: %d  Failed: %d" % (passed + failed, passed, failed))
    sys.exit(0 if failed == 0 else 1)
