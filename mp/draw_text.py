"""
Shared LCD text-drawing helper for the boot/menu UI family: the profile
picker (boot_session.py), the BIOS-style setup menu (setup_menu.py), the
boot status overlay and live clock overlay (boot_status.py/
clock_overlay.py), the disk hot-swap UI (disk_select_ui.py), the EMULATOR
MENU and its split-out screens (emulator_menu*.py), and main.py's own
fatal ROM-load-error screen.

Draws one 8x8 glyph cell at a time using a single fixed 128-byte buffer,
reused for every character AND across every call (module-level, created
once on first use) -- never a buffer sized to the string being drawn, and
never reallocated call to call. That matters here specifically because
several of these callers redraw on a tight, long-running cadence (the
profile picker on every keypress; boot_status.py/clock_overlay.py roughly
once a second, potentially for a whole session) -- a buffer proportional to
string length allocated fresh each time is exactly the pattern that
fragmented the heap badly enough to cause a real MemoryError in
boot_status.py before it was rewritten to avoid it. A fixed-size buffer
sidesteps that class of bug entirely, regardless of call frequency, so
nothing here needs boot_status.py's old per-instance scratch-buffer
workaround either. (An earlier version of this function only reused the
buffer *within* one call, across its characters, but still allocated a
fresh bytearray+FrameBuffer on every call -- small on its own, but real
real-hardware [MEM_OVERLAY] logs during a 2026-08 investigation showed the
dominant per-character cost was actually one layer down, in the display
driver's set_window()/write_cmd() doing their own struct.pack()/bytearray()
allocations every call -- see ili9341.py/st7796.py. Both are now fixed the
same way: persistent buffers, filled in place.)

This used to be duplicated, with small inconsistent variations (some
callers' copies didn't do the HDMI record_text check below; boot_session.py
and disk_select_ui.py's copies used the risky proportional-buffer
approach), across six separate files. Consolidated here instead so there's
exactly one implementation to get right.

Import this once, early -- main.py does so as one of its own top-level
imports, so it's resident from the very start of boot, before the profile
picker or anything else in this family needs it. Every other module in the
family just does `from draw_text import draw_text as _draw_text`; none of
them pay any real import cost for that beyond a sys.modules lookup.
"""


def _sw16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


_buf = None  # lazily created 128-byte one-character cell, reused forever
_fb = None


def draw_text(display, x, y, text, fg, bg=0x0000):
    text = str(text)
    max_chars = max(0, (display.width - x) // 8)
    text = text[:max_chars]
    if not text:
        return
    record = getattr(display, 'record_text', None)
    if record is not None:
        # HDMIMirrorDisplay: record a compact text command instead of
        # rasterizing to pixels (see hdmi_menu_mirror.py).
        record(x, y, text, fg, bg)
        return
    global _buf, _fb
    if _buf is None:
        import framebuf
        _buf = bytearray(8 * 8 * 2)  # 128 B: one 8x8 character cell
        _fb = framebuf.FrameBuffer(_buf, 8, 8, framebuf.RGB565)
    buf = _buf
    fb = _fb
    cx = x
    for ch in text:
        fb.fill(_sw16(bg))
        fb.text(ch, 0, 0, _sw16(fg))
        display.set_window(cx, y, cx + 7, y + 7)
        display.write_data(buf)
        cx += 8
