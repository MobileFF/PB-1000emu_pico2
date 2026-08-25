"""boot_status.py -- pure line-buffering logic test (host CPython).

Covers only _LogMirrorStream's newline-splitting (the part that has no
hardware dependency); BootStatusOverlay itself needs a real display/os
module and isn't exercised here.

Run:
    python3 test_boot_status.py
"""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

import boot_status


class T:
    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)


t = T()


def test_single_write_single_line():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"hello world\n")
    t.eq(lines, ["hello world"])


def test_line_split_across_multiple_writes():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"hel")
    s.write(b"lo ")
    t.eq(lines, [], "no newline yet -- nothing emitted")
    s.write(b"world\n")
    t.eq(lines, ["hello world"])


def test_multiple_lines_in_one_write():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"line1\nline2\nline3\n")
    t.eq(lines, ["line1", "line2", "line3"])


def test_partial_trailing_line_held_back():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"done\nin progress")
    t.eq(lines, ["done"])
    s.write("...\n")
    t.eq(lines, ["done", "in progress..."])


def test_crlf_normalized():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"line with crlf\r\n")
    t.eq(lines, ["line with crlf"])


def test_empty_lines_dropped():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write(b"\n\nnonempty\n\n")
    t.eq(lines, ["nonempty"])


def test_str_input_accepted():
    lines = []
    s = boot_status._LogMirrorStream(lines.append)
    s.write("plain str line\n")
    t.eq(lines, ["plain str line"])


def test_readinto_and_ioctl_never_yield_data():
    s = boot_status._LogMirrorStream(lambda line: None)
    buf = bytearray(8)
    t.eq(s.readinto(buf), None)
    t.eq(s.ioctl(3, 0), 0)


# ------------------------------------------------------------------
_TESTS = [
    test_single_write_single_line,
    test_line_split_across_multiple_writes,
    test_multiple_lines_in_one_write,
    test_partial_trailing_line_held_back,
    test_crlf_normalized,
    test_empty_lines_dropped,
    test_str_input_accepted,
    test_readinto_and_ioctl_never_yield_data,
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
