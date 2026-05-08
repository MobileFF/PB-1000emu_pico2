"""Unit tests for md100_dos.py using MemoryStorageBackend."""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

from fdd_storage import MemoryStorageBackend, SIZE_SECTOR
from md100_dos import (
    MD100Dos,
    SP_OCCUPIED, SP_FREE, SP_ERROR,
    DS_NO_ERROR, DS_FILE_NOT_FOUND, DS_HANDLE_IN_USE,
    SIZE_DIR_ENTRY, SIZE_FILE_NAME,
    START_DATA, SIZE_BLOCK,
    MAX_DIR_ENTRY,
)

# Default disk: 64 blocks × 4 sectors = 256 sectors = 64 KB
_DISK_SECTORS = 256


def _make_dos():
    backend = MemoryStorageBackend(_DISK_SECTORS)
    dos = MD100Dos()
    assert dos.dos_init(backend)
    assert dos.format_disk()
    return dos


def _name11(stem, ext=""):
    """Encode 8.3 filename into 11-byte padded bytes."""
    s = (stem + " " * 8)[:8]
    e = (ext  + " " * 3)[:3]
    return (s + e).encode("ascii")


class T:
    def eq(self, a, b):
        assert a == b, "%r != %r" % (a, b)

    def true(self, v):
        assert v, "expected True, got %r" % v

    def false(self, v):
        assert not v, "expected False, got %r" % v

    def ne(self, a, b):
        assert a != b, "%r == %r (expected !=)" % (a, b)


t = T()


# ------------------------------------------------------------------
# format_disk
# ------------------------------------------------------------------
def test_format_disk():
    dos = _make_dos()
    # After format, first data sector should be accessible
    n = dos.dos_sec_read(START_DATA)
    t.eq(n, SIZE_SECTOR)
    # First directory entry should be free
    sp, _ = dos.read_dir_entry(0)
    t.eq(sp, SP_FREE)


# ------------------------------------------------------------------
# create / open / close
# ------------------------------------------------------------------
def test_create_and_open():
    dos = _make_dos()
    name = _name11("HELLO", "BAS")
    idx = dos.create_disk_file(0, name, 0x10)
    t.ne(idx, -1)
    t.eq(dos.dos_status, DS_NO_ERROR)
    dos.close_disk_file(0)

    idx2 = dos.open_disk_file(0, name)
    t.ne(idx2, -1)
    t.eq(dos.dos_status, DS_NO_ERROR)
    dos.close_disk_file(0)


def test_open_nonexistent():
    dos = _make_dos()
    name = _name11("GHOST", "BAS")
    idx = dos.open_disk_file(0, name)
    t.eq(idx, -1)
    t.eq(dos.dos_status, DS_FILE_NOT_FOUND)


def test_handle_in_use():
    dos = _make_dos()
    name = _name11("FILE", "DAT")
    dos.create_disk_file(0, name, 0x24)
    idx = dos.open_disk_file(0, name)   # handle 0 already open
    t.eq(idx, -1)
    t.eq(dos.dos_status, DS_HANDLE_IN_USE)
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# write / read single record
# ------------------------------------------------------------------
def test_write_read_one_record():
    dos = _make_dos()
    name = _name11("TEST", "DAT")
    dos.create_disk_file(0, name, 0x24)

    data_out = bytes(range(256))
    n = dos.write_disk_file(0, data_out)
    t.eq(n, SIZE_SECTOR)
    dos.seek_rel_disk_file(0, 1)

    dos.seek_abs_disk_file(0, 0)
    buf = bytearray(SIZE_SECTOR)
    n = dos.read_disk_file(0, buf)
    t.eq(n, SIZE_SECTOR)
    t.eq(bytes(buf), data_out)
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# write / read multiple records (crosses sector boundary)
# ------------------------------------------------------------------
def test_write_read_multi_record():
    dos = _make_dos()
    name = _name11("MULTI", "DAT")
    dos.create_disk_file(0, name, 0x24)

    records = [bytes([i] * SIZE_SECTOR) for i in range(6)]
    for i, rec in enumerate(records):
        n = dos.write_disk_file(0, rec)
        t.eq(n, SIZE_SECTOR)
        dos.seek_rel_disk_file(0, 1)

    dos.close_disk_file(0)
    dos.open_disk_file(0, name)
    buf = bytearray(SIZE_SECTOR)
    for i, expected in enumerate(records):
        n = dos.read_disk_file(0, buf)
        t.eq(n, SIZE_SECTOR, )
        t.eq(bytes(buf), expected)
        dos.seek_rel_disk_file(0, 1)
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# size_of_disk_file
# ------------------------------------------------------------------
def test_size_of_disk_file():
    dos = _make_dos()
    name = _name11("SIZED", "DAT")
    dos.create_disk_file(0, name, 0x24)
    data = bytes(SIZE_SECTOR)
    for _ in range(3):
        dos.write_disk_file(0, data)
        dos.seek_rel_disk_file(0, 1)
    sz = dos.size_of_disk_file(0)
    t.eq(sz, 3)
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# is_end_of_disk_file
# ------------------------------------------------------------------
def test_is_end_of_disk_file():
    dos = _make_dos()
    name = _name11("EOF", "DAT")
    dos.create_disk_file(0, name, 0x24)
    data = bytes(SIZE_SECTOR)
    dos.write_disk_file(0, data)
    dos.seek_rel_disk_file(0, 1)
    t.true(dos.is_end_of_disk_file(0))
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# find_dir_entry / read_dir_entry
# ------------------------------------------------------------------
def test_find_dir_entry():
    dos = _make_dos()
    name = _name11("FIND", "ME ")
    dos.create_disk_file(0, name, 0x24)
    dos.close_disk_file(0)
    idx = dos.find_dir_entry(name)
    t.ne(idx, -1)
    sp, entry = dos.read_dir_entry(idx)
    t.eq(sp, SP_OCCUPIED)


# ------------------------------------------------------------------
# delete_disk_file
# ------------------------------------------------------------------
def test_delete_disk_file():
    dos = _make_dos()
    name = _name11("DEL", "DAT")
    dos.create_disk_file(0, name, 0x24)
    dos.close_disk_file(0)
    dos.delete_disk_file(name)
    t.eq(dos.dos_status, DS_NO_ERROR)
    idx = dos.find_dir_entry(name)
    t.eq(idx, -1)
    # free space should be restored
    free = dos.get_free_disk_space()
    t.ne(free, 0)


# ------------------------------------------------------------------
# rename_disk_file
# ------------------------------------------------------------------
def test_rename_disk_file():
    dos = _make_dos()
    old = _name11("OLD", "BAS")
    new = _name11("NEW", "BAS")
    dos.create_disk_file(0, old, 0x10)
    dos.close_disk_file(0)
    dos.rename_disk_file(old, new)
    t.eq(dos.dos_status, DS_NO_ERROR)
    t.eq(dos.find_dir_entry(old), -1)
    t.ne(dos.find_dir_entry(new), -1)


# ------------------------------------------------------------------
# get_free_disk_space
# ------------------------------------------------------------------
def test_get_free_disk_space():
    dos = _make_dos()
    free_initial = dos.get_free_disk_space()
    t.ne(free_initial, 0)
    name = _name11("SPACE", "DAT")
    dos.create_disk_file(0, name, 0x24)
    data = bytes(SIZE_SECTOR)
    for _ in range(4):   # one full block
        dos.write_disk_file(0, data)
        dos.seek_rel_disk_file(0, 1)
    dos.close_disk_file(0)
    free_after = dos.get_free_disk_space()
    t.true(free_after < free_initial)


# ------------------------------------------------------------------
# FAT chain continuity across block boundary
# ------------------------------------------------------------------
def test_fat_chain_crosses_block():
    dos = _make_dos()
    name = _name11("CHAIN", "DAT")
    dos.create_disk_file(0, name, 0x24)
    # Write 5 records: crosses the 4-sector block boundary
    data = [bytes([i] * SIZE_SECTOR) for i in range(5)]
    for rec in data:
        dos.write_disk_file(0, rec)
        dos.seek_rel_disk_file(0, 1)
    dos.seek_abs_disk_file(0, 0)
    buf = bytearray(SIZE_SECTOR)
    for expected in data:
        dos.read_disk_file(0, buf)
        t.eq(bytes(buf), expected)
        dos.seek_rel_disk_file(0, 1)
    dos.close_disk_file(0)


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------
_TESTS = [
    test_format_disk,
    test_create_and_open,
    test_open_nonexistent,
    test_handle_in_use,
    test_write_read_one_record,
    test_write_read_multi_record,
    test_size_of_disk_file,
    test_is_end_of_disk_file,
    test_find_dir_entry,
    test_delete_disk_file,
    test_rename_disk_file,
    test_get_free_disk_space,
    test_fat_chain_crosses_block,
]

if __name__ == "__main__":
    passed = failed = 0
    for fn in _TESTS:
        print("RUN %-40s" % fn.__name__, end=" ")
        try:
            fn()
            print("PASS")
            passed += 1
        except Exception as e:
            import traceback
            print("FAIL")
            traceback.print_exc()
            failed += 1
    print("-" * 50)
    print("Tests: %d  Passed: %d  Failed: %d" % (passed + failed, passed, failed))
    sys.exit(0 if failed == 0 else 1)
