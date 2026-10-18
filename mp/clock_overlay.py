"""
Live TIME$/DATE$ clock overlay, shown in the top-right corner of the
physical LCD while the emulator is actually running. This is now the ONLY
clock this codebase ever draws (2026-10-18 -- see below); there is no
separate boot-time clock any more.

Reads the values directly from PB-1000 system variable RAM -- the same
addresses ntp_sync.py's set_pb1000_time() writes (DATE$ at 0x6BAD, TIME$
at 0x6BB0, seconds in REG_TM's low 6 bits) -- rather than the Pico's own
RTC, so what's shown here always matches what TIME$/DATE$ would print from
BASIC, including if the user POKEs those addresses directly, or NTP is
disabled and the clock was never synced to real time at all.

Drawn in the blank margin above the bezel (system._disp_y), which
update_display()/force_full_redraw() never touch (see pb1000.py -- they
only repaint the LCD/bezel/status-bar region), so this survives normal
frame updates without needing to redraw on every one. It does NOT survive
the EMULATOR MENU (which paints over the whole screen while open) -- the
next poll() after the menu closes repaints it, since redraws are
unconditional on a timer rather than gated on the text actually changing.

Toggled via `[overlay] show_clock` in pb1000.ini (default off -- this stays
on screen for the entire session, so it's opt-in rather than on-by-default;
it may not fit cleanly above the bezel in every scale/y_offset
combination). This class is only constructed/polled once main_entry.py's
whole boot sequence (including `[ntp] enable=true`'s sync, if enabled) has
finished -- see main_entry.py's call sites -- so the very first time a
clock is ever visible on screen, it's already past NTP sync, by
construction.

2026-10-18: boot_status.py's BootStatusOverlay used to ALSO draw a clock,
during boot, reading the Pico's own RTC (the CPU isn't stepping yet during
boot, so there's no PB-1000 TIME$/DATE$ to read). It was removed after
real-hardware feedback that showing a clock before NTP sync has even run is
close to meaningless -- for however long WiFi connect + the NTP request
takes, the displayed time was simply whatever the Pico's RTC already held
(stale or outright wrong), with no indication it hadn't been corrected yet.
`[overlay] show_clock` now means "clock visible once the emulator has
finished starting" end to end, which this class already satisfied on its
own -- see boot_status.py's docstring for the removal.

Drawing goes through draw_text.py's shared draw_text(), which never
allocates more than one fixed 128-byte buffer per call regardless of
string length -- safe to call once a second for a whole session without
its own scratch buffer; see that module's docstring.
"""
import time

from draw_text import draw_text as _draw_text

_BG = 0x0000
_FG = 0x07E0

_REDRAW_INTERVAL_MS = 1000

_ADDR_DATE = 0x6BAD   # 3 bytes: YY(raw binary), MM(BCD), DD(BCD)
_ADDR_TIME = 0x6BB0   # 2 bytes: MM(BCD) at +0, HH(BCD) at +1


def _from_bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


class ClockOverlay:
    def __init__(self, display):
        self._display = display
        self._next_redraw_ms = 0

    def poll(self, system, now):
        if getattr(system, "_hdmi_enabled", False):
            return  # physical LCD is untouched while HDMI is the active output
        if time.ticks_diff(now, self._next_redraw_ms) < 0:
            return
        self._next_redraw_ms = time.ticks_add(now, _REDRAW_INTERVAL_MS)
        text = self._read_text()
        if text is not None:
            self._draw(text)

    def _read_text(self):
        try:
            import hd61700
            yy = hd61700.read_mem(_ADDR_DATE)
            mm = _from_bcd(hd61700.read_mem(_ADDR_DATE + 1))
            dd = _from_bcd(hd61700.read_mem(_ADDR_DATE + 2))
            minute = _from_bcd(hd61700.read_mem(_ADDR_TIME))
            hour = _from_bcd(hd61700.read_mem(_ADDR_TIME + 1))
            second = hd61700.get_reg8(6) & 0x3F
        except Exception:
            return None
        return "%02d/%02d/%02d %02d:%02d:%02d" % (yy, mm, dd, hour, minute, second)

    def _draw(self, text):
        try:
            W = self._display.width
            # text is always "YY/MM/DD HH:MM:SS" (17 chars, every field
            # %02d), so a clear sized to this call's own text is always the
            # same width every redraw -- no other renderer shares this
            # corner any more (boot_status.py no longer draws a clock at
            # all, see its own docstring), so there's no stray-pixel risk
            # from a differently-sized previous draw to guard against here.
            clear_w = len(text) * 8
            x_clear = max(0, W - clear_w - 4)
            self._display.fill_rect(x_clear, 4, clear_w, 10, _BG)
            x = x_clear
            _draw_text(self._display, x, 4, text, _FG, _BG)
        except Exception:
            pass
