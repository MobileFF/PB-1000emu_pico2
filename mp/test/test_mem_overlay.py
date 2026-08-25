"""mem_overlay.py -- host-testable logic (host CPython).

Covers MemOverlay.poll()'s gating (HDMI skip, redraw-interval throttling)
and the top-center positioning math, via draw_text.py's record_text()
delegation path (see test_draw_text.py) so no MicroPython `framebuf` is
needed.

Run:
    python3 test_mem_overlay.py
"""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

# MicroPython's time.ticks_diff()/ticks_add() (wraparound-safe millisecond
# arithmetic) and gc.mem_free() don't exist on host CPython -- shim minimal
# equivalents here (fine for these tests: small values, no wraparound).
import time as _time
if not hasattr(_time, "ticks_diff"):
    _time.ticks_diff = lambda a, b: a - b
if not hasattr(_time, "ticks_add"):
    _time.ticks_add = lambda a, b: a + b

import gc as _gc
if not hasattr(_gc, "mem_free"):
    _gc.mem_free = lambda: 123456

import mem_overlay


class T:
    def true(self, v, msg=""):
        assert v, "expected True: %s" % msg

    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)


t = T()


class _FakeRecordingDisplay:
    def __init__(self, width=320):
        self.width = width
        self.calls = []

    def record_text(self, x, y, text, fg, bg):
        self.calls.append((x, y, text, fg, bg))


class _FakeSystem:
    def __init__(self, hdmi_enabled=False):
        self._hdmi_enabled = hdmi_enabled


def test_draws_on_first_poll():
    d = _FakeRecordingDisplay()
    ov = mem_overlay.MemOverlay(d)
    ov.poll(_FakeSystem(), 0)
    t.eq(len(d.calls), 1)
    t.true(d.calls[0][2].startswith("FREE:"), d.calls[0][2])


def test_throttled_within_interval():
    d = _FakeRecordingDisplay()
    ov = mem_overlay.MemOverlay(d)
    ov.poll(_FakeSystem(), 0)
    ov.poll(_FakeSystem(), 500)  # still within the 1000ms interval
    t.eq(len(d.calls), 1, "should not redraw before the interval elapses")
    ov.poll(_FakeSystem(), 1000)
    t.eq(len(d.calls), 2, "should redraw once the interval elapses")


def test_skipped_when_hdmi_enabled():
    d = _FakeRecordingDisplay()
    ov = mem_overlay.MemOverlay(d)
    ov.poll(_FakeSystem(hdmi_enabled=True), 0)
    t.eq(d.calls, [])


def test_text_is_centered_top():
    d = _FakeRecordingDisplay(width=320)
    ov = mem_overlay.MemOverlay(d)
    ov.poll(_FakeSystem(), 0)
    x, y, text, _fg, _bg = d.calls[0]
    t.eq(y, 4)
    expected_x = max(0, (320 - len(text) * 8) // 2)
    t.eq(x, expected_x)


# ------------------------------------------------------------------
_TESTS = [
    test_draws_on_first_poll,
    test_throttled_within_interval,
    test_skipped_when_hdmi_enabled,
    test_text_is_centered_top,
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
