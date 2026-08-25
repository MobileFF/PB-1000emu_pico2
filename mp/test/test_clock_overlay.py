"""clock_overlay.py -- pure BCD-decoding logic test (host CPython).

Covers only _from_bcd(); the rest of the module needs hd61700/framebuf and
isn't exercised here.

Run:
    python3 test_clock_overlay.py
"""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

import clock_overlay


class T:
    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)


t = T()


def test_from_bcd_basic():
    t.eq(clock_overlay._from_bcd(0x00), 0)
    t.eq(clock_overlay._from_bcd(0x09), 9)
    t.eq(clock_overlay._from_bcd(0x10), 10)
    t.eq(clock_overlay._from_bcd(0x23), 23)
    t.eq(clock_overlay._from_bcd(0x59), 59)


def test_from_bcd_matches_ntp_syncs_to_bcd():
    # ntp_sync.py's _to_bcd() is the inverse of this -- round-trip check
    # against a hand-rolled copy (ntp_sync.py itself isn't host-importable,
    # it needs `machine`).
    def _to_bcd(val):
        return ((val // 10) << 4) | (val % 10)

    for val in (0, 1, 9, 10, 23, 30, 45, 59, 99):
        t.eq(clock_overlay._from_bcd(_to_bcd(val)), val, "round-trip %d" % val)


# ------------------------------------------------------------------
_TESTS = [
    test_from_bcd_basic,
    test_from_bcd_matches_ntp_syncs_to_bcd,
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
