"""Integration tests for fdd_protocol.py + md100_dos.py via transfer()."""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

from fdd_storage import MemoryStorageBackend, SIZE_SECTOR
from md100_dos import MD100Dos, SIZE_FILE_NAME
from fdd_protocol import (
    FDDProtocol,
    MD_OK, MD_FILE_FOUND, MD_END_OF_FILE,
    MD_INVALID_COMMAND, MD_NO_DATA,
)

_DISK_SECTORS = 256


def _make_system():
    backend = MemoryStorageBackend(_DISK_SECTORS)
    dos = MD100Dos()
    dos.dos_init(backend)
    dos.format_disk()
    fdd = FDDProtocol(dos)
    fdd.fdd_open()
    return fdd, dos


def _name_bytes(stem, ext=""):
    s = (stem + " " * 8)[:8]
    e = (ext  + " " * 3)[:3]
    return (s + e).encode("ascii")


def _xfer(fdd, *values):
    results = []
    for v in values:
        results.append(fdd.transfer(v))
    return results


def _recv_block(fdd, count_lo, count_hi):
    count = count_lo | (count_hi << 8)
    return bytes(fdd.transfer(0) for _ in range(count))


class T:
    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)

    def true(self, v, msg=""):
        assert v, "expected True: %s" % msg

    def ne(self, a, b):
        assert a != b, "%r == %r (expected !=)" % (a, b)


t = T()


# ------------------------------------------------------------------
# FORMAT ($90)
# ------------------------------------------------------------------
def test_format_command():
    fdd, dos = _make_system()
    st = fdd.transfer(0x90)
    t.eq(st, MD_OK)


# ------------------------------------------------------------------
# DIR ($00) on empty disk → EndOfFile
# ------------------------------------------------------------------
def test_dir_empty():
    fdd, dos = _make_system()
    st = fdd.transfer(0x00)
    t.eq(st, MD_OK)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    payload  = _recv_block(fdd, count_lo, count_hi)
    t.eq(payload[0], MD_END_OF_FILE)


# ------------------------------------------------------------------
# OPEN-OUT ($30) + WRITE ($10) + CLOSE ($40) + DIR ($00)
# ------------------------------------------------------------------
def _do_write(fdd, handle, name11, data_bytes):
    """Helper: OPEN sequential-out, write one record, CLOSE."""
    # OPEN sequential output ($30)
    # Payload: [zero, handle, filetype, name(11), zero]  = 14 bytes
    payload = bytes([0x00, handle, 0x10]) + name11 + bytes([0x00])
    assert len(payload) == 15, len(payload)
    count = len(payload)
    st = fdd.transfer(0x30)
    t.eq(st, MD_OK, "OPEN cmd")
    fdd.transfer(count & 0xFF)
    fdd.transfer((count >> 8) & 0xFF)
    for b in payload:
        fdd.transfer(b)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    resp = _recv_block(fdd, count_lo, count_hi)
    # $30 returns count=1, buffer[0]=opstatus (no dir entry)
    t.eq(resp[0] & ~MD_FILE_FOUND, MD_OK, "OPEN $30 status")

    # WRITE sequential ($10)
    # Payload: [zero, handle, data...]  buffer[0]=zero dummy, buffer[1]=handle
    write_payload = bytes([0x00, handle]) + data_bytes
    count = len(write_payload)
    st = fdd.transfer(0x10)
    t.eq(st, MD_OK, "WRITE cmd")
    fdd.transfer(count & 0xFF)
    fdd.transfer((count >> 8) & 0xFF)
    for b in write_payload:
        fdd.transfer(b)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    resp = _recv_block(fdd, count_lo, count_hi)
    t.eq(resp[0], MD_OK, "WRITE status")

    # CLOSE ($40)
    st = fdd.transfer(0x40)
    t.eq(st, MD_OK, "CLOSE cmd")
    st = fdd.transfer(handle)
    t.eq(st, MD_OK, "CLOSE status")


def _do_read(fdd, handle, name11):
    """Helper: OPEN sequential-in ($32), read one record, CLOSE. Returns data bytes."""
    payload = bytes([0x00, handle, 0x10]) + name11 + bytes([0x00])
    count = len(payload)
    st = fdd.transfer(0x32)
    t.eq(st, MD_OK, "OPEN-IN cmd")
    fdd.transfer(count & 0xFF)
    fdd.transfer((count >> 8) & 0xFF)
    for b in payload:
        fdd.transfer(b)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    resp = _recv_block(fdd, count_lo, count_hi)
    t.true(resp[0] & MD_FILE_FOUND, "OPEN-IN file found")

    # READ sequential ($20): payload = [zero, handle]
    st = fdd.transfer(0x20)
    t.eq(st, MD_OK, "READ cmd")
    fdd.transfer(0x02)   # count_lo = 2
    fdd.transfer(0x00)   # count_hi = 0
    fdd.transfer(0x00)   # buffer[0] = dummy zero
    fdd.transfer(handle) # buffer[1] = handle
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    read_data = _recv_block(fdd, count_lo, count_hi)

    # CLOSE
    fdd.transfer(0x40)
    fdd.transfer(handle)
    return read_data


def test_write_read_roundtrip():
    fdd, dos = _make_system()
    name11 = _name_bytes("HELLO", "BAS")
    original = b"10 PRINT \"HI\"\r\n"
    _do_write(fdd, 1, name11, original)

    read_data = _do_read(fdd, 1, name11)
    # Status byte is at [0]; data starts at [1]
    t.eq(read_data[0] & ~MD_FILE_FOUND, MD_OK | MD_END_OF_FILE & 0xFF, "read status")
    payload = read_data[1:]
    t.true(payload[:len(original)] == original or original in payload, "data matches")


# ------------------------------------------------------------------
# DIR ($00) finds written file
# ------------------------------------------------------------------
def test_dir_finds_file():
    fdd, dos = _make_system()
    name11 = _name_bytes("FILE1", "BAS")
    _do_write(fdd, 1, name11, b"10 REM")

    st = fdd.transfer(0x00)
    t.eq(st, MD_OK)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    payload  = _recv_block(fdd, count_lo, count_hi)
    t.eq(payload[0], MD_FILE_FOUND, "DIR file found")
    # Name starts at byte 2 (buffer[1]=dir_entry[0]=kind, buffer[2]=name[0])
    name_from_dir = bytes(payload[2:10]).rstrip(b" ")
    t.eq(name_from_dir, b"FILE1", "DIR name")


# ------------------------------------------------------------------
# DELETE ($50)
# ------------------------------------------------------------------
def test_delete_file():
    fdd, dos = _make_system()
    name11 = _name_bytes("DEL", "DAT")
    _do_write(fdd, 1, name11, b"data")

    # DELETE ($50): payload = [zero, zero, name11]
    del_payload = bytes([0x00, 0x00]) + name11
    count = len(del_payload)
    st = fdd.transfer(0x50)
    t.eq(st, MD_OK)
    fdd.transfer(count & 0xFF)
    fdd.transfer((count >> 8) & 0xFF)
    for b in del_payload:
        fdd.transfer(b)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    resp = _recv_block(fdd, count_lo, count_hi)
    t.eq(resp[0] & MD_NO_DATA, 0, "DELETE status no error bits")

    # DIR should now be empty
    fdd.transfer(0x00)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    payload  = _recv_block(fdd, count_lo, count_hi)
    t.eq(payload[0], MD_END_OF_FILE, "DIR empty after delete")


# ------------------------------------------------------------------
# RENAME ($60)
# ------------------------------------------------------------------
def test_rename_file():
    fdd, dos = _make_system()
    old11 = _name_bytes("OLD", "BAS")
    new11 = _name_bytes("NEW", "BAS")
    _do_write(fdd, 1, old11, b"10 REM")

    # RENAME ($60): payload = [zero, zero, old11, zero, zero, zero, zero, zero, zero, zero, zero, new11]
    # Offsets: buffer[2..12]=old, buffer[19..29]=new (per fdd.pas)
    ren_payload = bytearray(30)
    ren_payload[2:2 + SIZE_FILE_NAME] = old11
    ren_payload[19:19 + SIZE_FILE_NAME] = new11
    count = len(ren_payload)
    st = fdd.transfer(0x60)
    t.eq(st, MD_OK)
    fdd.transfer(count & 0xFF)
    fdd.transfer((count >> 8) & 0xFF)
    for b in ren_payload:
        fdd.transfer(b)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    resp = _recv_block(fdd, count_lo, count_hi)
    t.true(resp[0] & MD_FILE_FOUND, "RENAME ok")

    # Verify by DIR: should find NEW not OLD
    fdd.transfer(0x00)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    payload  = _recv_block(fdd, count_lo, count_hi)
    t.eq(payload[0], MD_FILE_FOUND)
    name_from_dir = bytes(payload[2:10]).rstrip(b" ")
    t.eq(name_from_dir, b"NEW")


# ------------------------------------------------------------------
# AcceptCountHi zero check (CDA-4)
# ------------------------------------------------------------------
def test_accept_count_hi_zero():
    fdd, dos = _make_system()
    st = fdd.transfer(0x30)   # OPEN: goes to AcceptCountLo
    t.eq(st, MD_OK)
    fdd.transfer(0x00)        # count_lo = 0
    st = fdd.transfer(0x00)   # count_hi = 0 → count==0 → INVALID_COMMAND
    t.eq(st, MD_INVALID_COMMAND)
    # State machine should have reset to index=0 (SwitchCmd)
    # Next byte is treated as a new command
    st2 = fdd.transfer(0x00)  # DIR command should work
    t.eq(st2, MD_OK)


# ------------------------------------------------------------------
# GET FREE ($D0)
# ------------------------------------------------------------------
def test_get_free():
    fdd, dos = _make_system()
    st = fdd.transfer(0xD0)
    t.eq(st, MD_OK)
    count_lo = fdd.transfer(0)
    count_hi = fdd.transfer(0)
    free = count_lo | (count_hi << 8)
    t.true(free > 0, "free > 0")


# ------------------------------------------------------------------
# DIR NEXT ($01)
# ------------------------------------------------------------------
def test_dir_next():
    fdd, dos = _make_system()
    _do_write(fdd, 1, _name_bytes("FILE1", "BAS"), b"A")
    _do_write(fdd, 1, _name_bytes("FILE2", "BAS"), b"B")

    # First file
    fdd.transfer(0x00)
    lo = fdd.transfer(0); hi = fdd.transfer(0)
    p1 = _recv_block(fdd, lo, hi)
    t.eq(p1[0], MD_FILE_FOUND)
    name1 = bytes(p1[2:10]).rstrip(b" ")

    # Next file
    fdd.transfer(0x01)
    lo = fdd.transfer(0); hi = fdd.transfer(0)
    p2 = _recv_block(fdd, lo, hi)
    t.eq(p2[0], MD_FILE_FOUND)
    name2 = bytes(p2[2:10]).rstrip(b" ")

    t.ne(name1, name2)

    # One more: should be EOF
    fdd.transfer(0x01)
    lo = fdd.transfer(0); hi = fdd.transfer(0)
    p3 = _recv_block(fdd, lo, hi)
    t.eq(p3[0], MD_END_OF_FILE)


# ------------------------------------------------------------------
# ExecWriteFile mid-record flush (>256 bytes written sequentially)
# ------------------------------------------------------------------
def test_write_large_data():
    fdd, dos = _make_system()
    name11 = _name_bytes("LARGE", "DAT")
    # Write 300 bytes: crosses 256-byte sector boundary
    data = bytes(range(256)) + bytes(range(44))
    _do_write(fdd, 1, name11, data)

    # Verify with DOS directly
    dos.open_disk_file(1, name11)
    buf = bytearray(SIZE_SECTOR)
    dos.read_disk_file(1, buf)
    t.eq(bytes(buf[:256]), bytes(range(256)))
    dos.close_disk_file(1)


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------
_TESTS = [
    test_format_command,
    test_dir_empty,
    test_write_read_roundtrip,
    test_dir_finds_file,
    test_delete_file,
    test_rename_file,
    test_accept_count_hi_zero,
    test_get_free,
    test_dir_next,
    test_write_large_data,
]

if __name__ == "__main__":
    passed = failed = 0
    for fn in _TESTS:
        print("RUN %-45s" % fn.__name__, end=" ")
        try:
            fn()
            print("PASS")
            passed += 1
        except Exception as e:
            import traceback
            print("FAIL")
            traceback.print_exc()
            failed += 1
    print("-" * 55)
    print("Tests: %d  Passed: %d  Failed: %d" % (passed + failed, passed, failed))
    sys.exit(0 if failed == 0 else 1)
