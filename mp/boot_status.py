"""
Boot-time status overlay for the physical LCD: profile name (top-left), a
clock (top-right), and the most recent line printed to the REPL (bottom).
Meant to be shown from right after profile selection (main.py, once the
screen is cleared) until the emulator's own screen takes over at the end
of main()'s startup sequence -- see BootStatusOverlay.start()/stop().

show_profile/show_clock/show_log are supplied by main.py from `[overlay]
show_profile_name`/`show_clock`/`show_log` in pb1000.ini. show_clock's key is shared
with clock_overlay.py's runtime clock (one on/off toggle spans both boot
and execution) even though the two are separate implementations -- the CPU
isn't stepping yet during boot, so there's no PB-1000 TIME$/DATE$ to read
from RAM the way clock_overlay.py does at runtime; this class reads the
Pico's own RTC instead. See clock_overlay.py's docstring for the runtime
side of this.

The log line is captured by registering a tiny io.IOBase stream on
os.dupterm() (index 0 -- this port only has one dupterm slot, see
MICROPY_PY_OS_DUPTERM=1 in mpconfigport.h). mp_hal_stdout_tx_strn() in the
rp2 port's mphalport.c fans every print()/REPL write out to whatever's
registered there, in addition to the physical UART/USB REPL, so this sees
everything printed during boot -- by main.py itself and by every helper it
calls (main_boot.py, ntp_sync.py, etc.) -- without needing print() call
sites threaded through all of them. The stream's read()/readinto() always
report "nothing available" (see _LogMirrorStream), so it can never be
typed into as a second REPL; it is purely an output tap.

Clock updates piggyback on incoming log lines rather than a timer: boot is
a synchronous, single-threaded stretch of main.py with no event loop
running yet, and driving a periodic redraw from a hardware timer IRQ here
would risk interleaving with whatever SPI transfer (LCD or SD card) is
already in flight on the same core. Log lines arrive often enough during
boot to keep the clock reasonably current without that risk.

Heap safety: this overlay is active across exactly the part of boot that's
tightest on contiguous heap -- PB1000System's [ext] module loader runs right
in this window and has been observed to fail with MemoryError under real
profiles (the incident this note was originally added for: a bytearray
sized to the drawn string, allocated fresh on every print(), was enough
extra fragmentation on top of the already-marginal heap at that point to
turn a previously-successful load into a fatal MemoryError -- back when the
now-removed UART keyboard/Serial Console feature (`[keyboard]
enable_uart_kbd`) additionally forced keymap.py's load eagerly at this same
point, tightening the margin further). Text drawing now goes through
draw_text.py's shared draw_text(), which never allocates more than one
fixed 128-byte buffer per call regardless of string length -- see that
module's docstring for why that's sufficient on its own; this file no
longer needs its own per-instance scratch buffer to get the same guarantee.
"""
import io
import os
import time

from draw_text import draw_text as _draw_text

_BG  = 0x0000
_HDR = 0x7BEF   # profile name / clock -- matches boot_session.py's footer gray
_LOG = 0x07E0   # last log line -- green, matches setup_menu.py's "on" color

_ROW_H = 10


class _LogMirrorStream(io.IOBase):
    """Write-only dupterm stream: buffers bytes until a newline, then hands
    each complete line to `on_line`. Never yields bytes back to the VM."""

    def __init__(self, on_line):
        self._on_line = on_line
        self._buf = ""

    def write(self, buf):
        try:
            text = buf.decode() if isinstance(buf, (bytes, bytearray)) else buf
        except Exception:
            return len(buf)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self._on_line(line)
        return len(buf)

    def readinto(self, b):
        return None

    def ioctl(self, op, arg):
        return 0


class BootStatusOverlay:
    """start() draws the header row and registers the log mirror; stop()
    unregisters it. Call start() only when the physical LCD is actually
    the active display (i.e. HDMI mirroring did not take over the picker
    screen -- see main.py's `_early_lcd is None` check), since the two are
    exclusive elsewhere in this codebase (pb1000.py's update_display())."""

    def __init__(self, display, profile_name, show_profile=True, show_clock=True, show_log=True):
        self._display = display
        self._profile_name = profile_name or "(none)"
        self._show_profile = show_profile
        self._show_clock = show_clock
        self._show_log = show_log
        self._mirror = None
        self._active = False

    def start(self):
        if self._show_profile:
            _draw_text(self._display, 4, 4, self._profile_name, _HDR)
        if self._show_clock:
            self._draw_clock()
        self._active = True
        if not self._show_log:
            return
        try:
            self._mirror = _LogMirrorStream(self._on_log_line)
            os.dupterm(self._mirror, 0)
        except Exception:
            self._mirror = None

    def stop(self):
        self._active = False
        if self._mirror is not None:
            try:
                os.dupterm(None, 0)
            except Exception:
                pass
            self._mirror = None

    def _draw_clock(self):
        try:
            t = time.localtime()
            text = "%04d-%02d-%02d %02d:%02d:%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])
        except Exception:
            return
        W = self._display.width
        x = max(0, W - len(text) * 8 - 4)
        _draw_text(self._display, x, 4, text, _HDR)

    def _on_log_line(self, line):
        if not self._active:
            return
        try:
            H = self._display.height
            W = self._display.width
            self._display.fill_rect(0, H - _ROW_H, W, _ROW_H, _BG)
            _draw_text(self._display, 4, H - _ROW_H + 1, line, _LOG)
            # Re-draw the header row too, not just clear+redraw the footer:
            # other boot-time code (e.g. create_system()'s bezel draw) can
            # paint over the top row in between log lines, and this is the
            # only place that redraws after start() -- so on the next log
            # line, bring both back rather than letting the profile name
            # go missing while the clock (which redraws below) keeps working.
            if self._show_profile:
                _draw_text(self._display, 4, 4, self._profile_name, _HDR)
            if self._show_clock:
                self._draw_clock()
        except Exception:
            pass
