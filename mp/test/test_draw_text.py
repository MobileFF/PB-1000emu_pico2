"""draw_text.py -- host-testable logic (host CPython).

Covers _sw16() and the parts of draw_text() reachable without `framebuf`
(not available on host CPython -- see draw_text()'s early-return branches):
text clipping, empty-string short-circuit, and delegation to a display's
record_text() (the HDMIMirrorDisplay path, which is what real hardware
uses too whenever HDMI mirroring is active). The direct-to-SPI branch
(framebuf.FrameBuffer + set_window/write_data) needs real hardware/
MicroPython and isn't exercised here.

Run:
    python3 test_draw_text.py
"""
import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

import draw_text


class T:
    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)


t = T()


class _FakeRecordingDisplay:
    """Stands in for HDMIMirrorDisplay: has record_text(), so draw_text()
    must delegate to it instead of touching framebuf/SPI at all."""
    def __init__(self, width=320):
        self.width = width
        self.calls = []

    def record_text(self, x, y, text, fg, bg):
        self.calls.append((x, y, text, fg, bg))


def test_sw16_byte_swap():
    t.eq(draw_text._sw16(0x1234), 0x3412)
    t.eq(draw_text._sw16(0x0000), 0x0000)
    t.eq(draw_text._sw16(0x00FF), 0xFF00)
    t.eq(draw_text._sw16(0xFF00), 0x00FF)


def test_record_text_path_used_when_available():
    d = _FakeRecordingDisplay()
    draw_text.draw_text(d, 4, 4, "hello", 0xFFFF, 0x0000)
    t.eq(d.calls, [(4, 4, "hello", 0xFFFF, 0x0000)])


def test_record_text_receives_clipped_text():
    # width=40 -> max_chars = (40-4)//8 = 4
    d = _FakeRecordingDisplay(width=40)
    draw_text.draw_text(d, 4, 4, "abcdefgh", 0xFFFF)
    t.eq(d.calls[0][2], "abcd")


def test_record_text_not_called_for_empty_or_fully_clipped_text():
    d = _FakeRecordingDisplay(width=4)  # max_chars = (4-4)//8 = 0
    draw_text.draw_text(d, 4, 4, "hello", 0xFFFF)
    t.eq(d.calls, [])

    d2 = _FakeRecordingDisplay()
    draw_text.draw_text(d2, 4, 4, "", 0xFFFF)
    t.eq(d2.calls, [])


def test_non_string_text_is_coerced():
    d = _FakeRecordingDisplay()
    draw_text.draw_text(d, 4, 4, 123, 0xFFFF)
    t.eq(d.calls[0][2], "123")


# ------------------------------------------------------------------
_TESTS = [
    test_sw16_byte_swap,
    test_record_text_path_used_when_available,
    test_record_text_receives_clipped_text,
    test_record_text_not_called_for_empty_or_fully_clipped_text,
    test_non_string_text_is_coerced,
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
