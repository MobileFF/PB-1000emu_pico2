"""md100_volume.py -- MD-100 file-system layer unit test (host CPython).

Run:
    python3 mp/test/test_md100_volume.py
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


class ReadOnlyStorage(fdd_storage.MemoryStorageBackend):
    def write_raw(self, sector, data):
        return False


class FailAfterStorage(fdd_storage.MemoryStorageBackend):
    """Writes succeed until fail is set."""
    fail = False

    def write_raw(self, sector, data):
        if self.fail:
            return False
        return fdd_storage.MemoryStorageBackend.write_raw(self, sector, data)


def _name(s):
    b = bytearray(11)
    for i, ch in enumerate(s.encode()[:11]):
        b[i] = ch
    return bytes(b)


def _vol(sectors=256, cls=fdd_storage.MemoryStorageBackend):
    st = cls(sectors)
    v = Md100Volume()
    assert v.attach(st)
    assert v.format()
    return v, st


def _ae(st, blk):
    raw = st.read_raw(0) + st.read_raw(1) + st.read_raw(2) + st.read_raw(3)
    return (raw[blk * 2] << 8) | raw[blk * 2 + 1]


def _rec(n, fill=None):
    return bytes([(fill if fill is not None else n) & 0xFF]) * 256


# ------------------------------------------------------------------
def test_attach_ready():
    v = Md100Volume()
    assert not v.is_ready()
    assert v.attach(None) is False
    assert not v.is_ready()
    assert v.attach(fdd_storage.MemoryStorageBackend(64))
    assert v.is_ready()


def test_format_layout():
    v, st = _vol(64)
    s0 = st.read_raw(0)
    assert s0[:8] == b"\xff" * 8 and s0[8:] == bytes(248)
    for b in range(4):
        assert _ae(st, b) == 0xFFFF
    assert v.free_blocks() == 16 - 4
    ro = Md100Volume()
    ro.attach(ReadOnlyStorage(64))
    assert ro.format() is False


def test_format_limit_2048():
    v, st = _vol(4096)
    assert v.free_blocks() == 512 - 4


def test_create_open_close():
    v, st = _vol()
    d = v.create(3, _name("HELLO   BAS"), 0x42)
    assert d == 0 and v.status == V.ST_OK
    s, e = v.dir_entry(0)
    assert s == V.DIR_USED
    assert e[0] == 0x42 and e[1:12] == _name("HELLO   BAS")
    assert (e[13] << 8 | e[14]) == 4
    assert _ae(st, 4) == 0xC004
    assert v.record_count(3) == 1
    assert v.at_last_record(3)
    # busy
    assert v.open(3, _name("HELLO   BAS")) == -1 and v.status == V.ST_HANDLE_BUSY
    # same file on another handle
    assert v.open(4, _name("HELLO   BAS")) == 0 and v.status == V.ST_OK
    assert v.open(5, _name("NOPE")) == -1 and v.status == V.ST_NOT_FOUND
    assert v.open(16, _name("HELLO   BAS")) == -1 and v.status == V.ST_BAD_HANDLE
    v.close(3)
    assert v.status == V.ST_OK
    v.close(3)
    assert v.status == V.ST_NOT_OPEN
    v.close(16)
    assert v.status == V.ST_BAD_HANDLE
    v.close(0x80)
    assert v.status == V.ST_OK and v.record_count(4) == 0
    assert v.dir_entry(192)[0] == V.DIR_INVALID
    assert v.dir_entry(-1)[0] == V.DIR_INVALID
    assert v.dir_entry(1)[0] == V.DIR_FREE


def test_create_replaces_and_ignores_busy():
    v, st = _vol()
    n = _name("A")
    v.create(0, n, 1)
    v.write_record(0, _rec(1))
    v.seek(0, 5)
    v.write_record(0, _rec(5))
    assert v.record_count(0) == 6
    free_before = v.free_blocks()
    # handle 0 still open: create does not check
    d = v.create(0, n, 2)
    assert d == 0 and v.status == V.ST_OK
    assert v.record_count(0) == 1
    assert v.free_blocks() == free_before + 1
    assert v.dir_entry(0)[1][0] == 2
    assert v.create(16, n, 1) == -1 and v.status == V.ST_BAD_HANDLE


def test_dir_full():
    v, st = _vol(2048)
    for i in range(192):
        assert v.create(0, _name("F%03d" % i), 0) == i
    assert v.create(1, _name("X"), 0) == -1
    assert v.status == V.ST_NO_ROOM


def test_disk_full_create_and_write():
    v, st = _vol(32)          # 8 blocks: 4 system + 4 data
    for i in range(4):
        assert v.create(i, _name("F%d" % i), 0) >= 0
    assert v.free_blocks() == 0
    assert v.create(5, _name("G"), 0) == -1 and v.status == V.ST_NO_ROOM
    v.seek(0, 3)
    assert v.write_record(0, _rec(3)) == 256
    v.seek(0, 4)
    assert v.write_record(0, _rec(4)) == 0
    assert v.status == V.ST_NO_DATA


def test_read_write_records():
    v, st = _vol()
    v.create(0, _name("D"), 0)
    buf = bytearray(256)
    assert v.read_record(0, buf) == 256 and buf == bytearray(256)
    assert v.write_record(0, _rec(7)) == 256
    assert v.read_record(0, buf) == 256 and bytes(buf) == _rec(7)
    # next record unchanged by read/write
    v.seek(0, 1)
    assert v.read_record(0, buf) == 0 and v.status == V.ST_NO_DATA
    assert v.read_record(1, buf) == 0 and v.status == V.ST_NOT_OPEN
    assert v.write_record(1, buf) == 0 and v.status == V.ST_NOT_OPEN
    assert v.read_record(20, buf) == 0 and v.status == V.ST_BAD_HANDLE


def test_growth_across_blocks():
    v, st = _vol()
    v.create(0, _name("BIG"), 0)
    for r in range(10):
        v.seek(0, r)
        assert v.write_record(0, _rec(r)) == 256
        assert v.record_count(0) == r + 1
        assert v.at_last_record(0)
    # chain: 4 -> 5 -> 6 (last, pos 1)
    assert _ae(st, 4) == 0x8005
    assert _ae(st, 5) == 0x8006
    assert _ae(st, 6) == 0xC000 | 0x1000 | 6
    buf = bytearray(256)
    for r in range(10):
        v.seek(0, r)
        assert v.read_record(0, buf) == 256 and bytes(buf) == _rec(r)
    v.seek(0, 3)
    v.read_record(0, buf)
    assert not v.at_last_record(0)
    # sparse growth: jump to record 15
    v.seek(0, 15)
    assert v.write_record(0, _rec(15)) == 256
    assert v.record_count(0) == 16
    assert _ae(st, 7) == 0xC000 | 0x3000 | 7


def test_skip_seek():
    v, st = _vol()
    v.create(0, _name("S"), 0)
    v.seek(0, 3)
    v.skip(0, -5)
    v.skip(0, -3)
    v.write_record(0, _rec(0))
    assert v.record_count(0) == 1
    v.skip(0, 2)
    v.write_record(0, _rec(2))
    assert v.record_count(0) == 3
    v.seek(5, 3)   # closed handle: no-op, no crash
    assert v.record_count(5) == 0 and not v.at_last_record(5)
    assert v.record_count(99) == 0


def test_delete():
    v, st = _vol()
    v.create(0, _name("A"), 0)
    for r in range(6):
        v.seek(0, r)
        v.write_record(0, _rec(r))
    v.create(1, _name("B"), 0)
    v.open(2, _name("A"))
    free = v.free_blocks()
    v.status = V.ST_IO_ERROR
    v.delete(_name("A"))
    assert v.status == V.ST_OK
    assert v.free_blocks() == free + 2
    assert _ae(st, 4) == 0x0005 and _ae(st, 5) == 0x0005
    assert v.record_count(0) == 0 and v.record_count(2) == 0
    assert v.record_count(1) == 1
    s, e = v.dir_entry(0)
    assert s == V.DIR_FREE and e == bytes(16)
    v.delete(_name("A"))
    assert v.status == V.ST_NOT_FOUND
    # freed block reused first
    assert v.create(3, _name("C"), 0) == 0
    assert (v.dir_entry(0)[1][13] << 8 | v.dir_entry(0)[1][14]) == 4


def test_rename():
    v, st = _vol()
    v.create(0, _name("OLD"), 9)
    v.create(1, _name("OTHER"), 0)
    v.rename(_name("OLD"), _name("OTHER"))
    assert v.status == V.ST_TARGET_EXISTS
    v.rename(_name("OLD"), _name("OLD"))
    assert v.status == V.ST_TARGET_EXISTS
    v.rename(_name("MISSING"), _name("X"))
    assert v.status == V.ST_NOT_FOUND
    v.rename(_name("OLD"), _name("NEW"))
    assert v.status == V.ST_OK
    s, e = v.dir_entry(0)
    assert e[0] == 9 and e[1:12] == _name("NEW") and e[14] == 4
    # still open, still usable
    assert v.write_record(0, _rec(1)) == 256


def test_raw_sectors_and_cache():
    v, st = _vol()
    v.create(0, _name("A"), 0)
    buf = bytearray(256)
    assert v.read_sector(4, buf) and buf[1] == ord("A")
    # wipe directory sector by raw write -> file vanishes
    assert v.write_sector(4, bytes(256))
    assert v.dir_entry(0)[0] == V.DIR_FREE
    assert v.open(1, _name("A")) == -1
    assert not v.read_sector(256, buf)
    assert not v.read_sector(-1, buf)
    assert not v.write_sector(256, buf)
    st2 = v.status
    v.read_sector(0, buf)
    assert v.status == st2


def test_readonly_io_error():
    st = fdd_storage.MemoryStorageBackend(64)
    v = Md100Volume(st)
    v.format()
    v.create(0, _name("A"), 0)
    ro = ReadOnlyStorage(64)
    ro._data[:] = st._data
    v.attach(ro)
    assert v.open(0, _name("A")) == 0
    assert v.write_record(0, _rec(1)) == 0 and v.status == V.ST_IO_ERROR
    assert v.create(1, _name("B"), 0) == -1 and v.status == V.ST_IO_ERROR
    v.delete(_name("A"))
    assert v.status == V.ST_IO_ERROR
    v.rename(_name("A"), _name("Z"))
    assert v.status == V.ST_IO_ERROR
    assert not v.write_sector(10, bytes(256))


def test_mode_and_free_handle():
    v, st = _vol()
    assert v.get_mode(3) == 0
    v.set_mode(3, 0x31)
    v.set_mode(16, 1)
    assert v.get_mode(3) == 0x31 and v.get_mode(16) == 0
    assert v.find_free_handle() == 15
    v.create(15, _name("A"), 0)
    assert v.find_free_handle() == 14
    v.close(15)
    assert v.get_mode(3) == 0x31
    for h in range(16):
        v.create(h, _name("H%d" % h), 0)
    assert v.find_free_handle() == -1


def test_out_of_range_storage_not_called():
    class Strict(fdd_storage.MemoryStorageBackend):
        def read_into(self, sector, buf):
            assert 0 <= sector < self.sector_count
            return fdd_storage.MemoryStorageBackend.read_into(self, sector, buf)

        def write_raw(self, sector, data):
            assert 0 <= sector < self.sector_count
            return fdd_storage.MemoryStorageBackend.write_raw(self, sector, data)

    v = Md100Volume(Strict(16))     # only system area
    assert v.format()
    assert v.free_blocks() == 0
    assert v.create(0, _name("A"), 0) == -1 and v.status == V.ST_NO_ROOM
    assert not v.read_sector(16, bytearray(256))


class CountingStorage(fdd_storage.MemoryStorageBackend):
    reads = 0

    def read_into(self, sector, buf):
        self.reads += 1
        return fdd_storage.MemoryStorageBackend.read_into(self, sector, buf)


def test_sequential_read_storage_reads():
    st = CountingStorage(2048)
    v = Md100Volume(st)
    assert v.format()
    v.create(0, _name("BIG"), 0)
    for r in range(800):
        assert v.write_record(0, _rec(r)) == 256
        v.skip(0, 1)
    assert v.record_count(0) == 800
    v.close(0)
    v.open(0, _name("BIG"))
    st.reads = 0
    buf = bytearray(256)
    n = 0
    last = False
    while v.read_record(0, buf) == 256:
        assert buf[0] == n & 0xFF
        last = v.at_last_record(0)
        v.skip(0, 1)
        n += 1
    assert n == 800 and last
    assert st.reads <= 2 * n, st.reads
    # sequential write must not re-walk / re-read either
    v.seek(0, 0)
    st.reads = 0
    for r in range(800):
        v.write_record(0, _rec(r + 1))
        v.skip(0, 1)
    assert st.reads <= 2 * 800, st.reads


def test_two_handles_and_raw_fat_write():
    v, st = _vol()
    v.create(0, _name("A"), 0)
    v.open(1, _name("A"))
    buf = bytearray(256)
    v.read_record(1, buf)
    assert v.at_last_record(1)
    # handle 0 grows the file across a block boundary
    for r in range(6):
        v.seek(0, r)
        v.write_record(0, _rec(r))
    assert not v.at_last_record(1)
    assert v.record_count(1) == 6
    v.seek(1, 5)
    assert v.read_record(1, buf) == 256 and buf[0] == 5
    # raw write truncating the chain in the allocation table
    s0 = bytearray(st.read_raw(0))
    s0[8:10] = b"\xc0\x04"                # block 4: last, pos 0
    assert v.write_sector(0, s0)
    assert v.record_count(1) == 1
    v.seek(1, 1)
    assert v.read_record(1, buf) == 0 and v.status == V.ST_NO_DATA
    assert v.free_blocks() == 256 // 4 - 4 - 2  # block 5 still marked used


# ------------------------------------------------------------------
_TESTS = [
    test_attach_ready,
    test_format_layout,
    test_format_limit_2048,
    test_create_open_close,
    test_create_replaces_and_ignores_busy,
    test_dir_full,
    test_disk_full_create_and_write,
    test_read_write_records,
    test_growth_across_blocks,
    test_skip_seek,
    test_delete,
    test_rename,
    test_raw_sectors_and_cache,
    test_readonly_io_error,
    test_mode_and_free_handle,
    test_out_of_range_storage_not_called,
    test_sequential_read_storage_reads,
    test_two_handles_and_raw_fat_write,
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
