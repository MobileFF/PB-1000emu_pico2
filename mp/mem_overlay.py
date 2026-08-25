"""
Live free-heap-memory overlay, shown at the top-center of the physical LCD
while the emulator is actually running -- same lifecycle/family as
clock_overlay.py (runtime-only, distinct from boot_status.py's boot-time
overlay; see that module's docstring for why boot-time and runtime each
get their own separate overlay rather than one that spans both).

Shows gc.mem_free() as-is, without forcing a gc.collect() first -- doing
that would hide exactly the kind of transient fragmentation/pressure this
overlay exists to make visible (the whole point is a live read of actual
steady-state free heap during normal operation, not an artificially
cleaned-up number).

Drawn in the blank margin above the bezel (same region clock_overlay.py
uses on the right; this uses top-center), which update_display()/
force_full_redraw() never touch, so it survives normal frame updates
without needing to redraw on every one. Like clock_overlay.py, it does NOT
survive the EMULATOR MENU (which paints over the whole screen while open)
-- the next poll() after the menu closes repaints it, since redraws are
unconditional on a timer rather than gated on the text actually changing.

Toggled via `[overlay] show_mem_free` in pb1000.ini (default off -- same
reasoning as clock_overlay.py: a permanent screen fixture for the whole
session, not a one-time splash, and it may visually overlap
clock_overlay.py's top-right text on narrower displays if both are
enabled at once -- both are opt-in, so that's left to the user to avoid).

Drawing goes through draw_text.py's shared draw_text(), which never
allocates more than one fixed 128-byte buffer per call regardless of
string length -- safe to call once a second for a whole session without
its own scratch buffer; see that module's docstring.
"""
import gc
import time

from draw_text import draw_text as _draw_text

_BG = 0x0000
_FG = 0x07E0  # matches boot_status.py/clock_overlay.py's convention

_REDRAW_INTERVAL_MS = 1000


class MemOverlay:
    def __init__(self, display):
        self._display = display
        self._next_redraw_ms = 0

    def poll(self, system, now):
        if getattr(system, "_hdmi_enabled", False):
            return  # physical LCD is untouched while HDMI is the active output
        if time.ticks_diff(now, self._next_redraw_ms) < 0:
            return
        self._next_redraw_ms = time.ticks_add(now, _REDRAW_INTERVAL_MS)
        self._draw(system)

    def _draw(self, system):
        try:
            free = gc.mem_free()
            # Space-padded to 6 digits (up to 999999B, comfortably above the
            # ~280KB heap) so the string length -- and therefore x below --
            # never changes between redraws. A varying digit count would
            # shift x each time, leaving stray pixels from the previous
            # (wider) draw uncleared and the new (narrower) one overlapping
            # them -- garbled, smeared-looking text.
            text = "FREE:%6d" % free
            W = self._display.width
            x = max(0, (W - len(text) * 8) // 2)
            _draw_text(self._display, x, 4, text, _FG, _BG)
        except Exception:
            pass
