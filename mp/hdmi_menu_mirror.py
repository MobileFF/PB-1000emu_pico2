"""HDMIMirrorDisplay — makes the EMULATOR MENU (emulator_menu.py) mirror to
the HDMI bridge instead of the physical LCD, while HDMI is the active
output (see pb1000.py's update_display() for the matching game-screen
policy: LCD and HDMI are exclusive, never both active at once).

The menu is composed entirely of two primitives — solid-color rectangles
(display.fill_rect()) and 8x8-font text (_draw_text(), which this module
hooks via record_text() — see emulator_menu.py) — so instead of capturing
pixels (which previously needed a buffer sized proportionally to the
physical panel's resolution: 153,600 bytes at 480x320/8bpp, confirmed to
MemoryError deep into a running session, then 19,200 bytes at a
downscaled 320x240/2bpp, which still wasn't quite enough headroom for
some heavier menu screens), this records a compact command stream — "fill
rect" and "draw text string" — and sends that to the HDMI bridge, which
renders it using its own embedded font (see
../../hdmi_bridge_receiver/main.c's PKT_TEXT_CMDS). A full menu screen's worth
of commands is typically well under 1KB, and full RGB332 color (no
palette-approximation) is used throughout since the stream is tiny
regardless. The mirror canvas is the real display's own resolution (no
downscaling needed either, for the same reason).
"""


def _rgb565_to_rgb332(c):
    r3 = (c >> 13) & 7
    g3 = (c >> 8) & 7
    b2 = (c >> 3) & 3
    return (r3 << 5) | (g3 << 2) | b2


class HDMIMirrorDisplay:
    def __init__(self, real, lcd_c, bezel=False):
        self._real = real
        self._lcd_c = lcd_c
        # bezel=True: pb1000.py's _draw_bezel_hdmi() overrides width/height/
        # scale right after construction to match the game screen's own
        # logical coordinate space (see its docstring) instead of the real
        # panel's — sent as PKT_BEZEL_CMDS (see flush_if_dirty()) so the
        # receiver tracks its window-centering independently from the
        # EMULATOR MENU mirror's much larger canvas.
        self._bezel = bezel
        self.width = real.width
        self.height = real.height
        # 640x480画面にちょうど収まる最大の整数倍率。
        self.scale = max(1, min(640 // self.width, 480 // self.height))
        self._cmds = []  # bytesオブジェクトのリスト。flush時にjoinする
        self._dirty = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def fill_rect(self, x, y, w, h, color):
        c332 = _rgb565_to_rgb332(color)
        self._cmds.append(bytes((
            0x00,  # CMD_RECT
            (x >> 8) & 0xFF, x & 0xFF,
            (y >> 8) & 0xFF, y & 0xFF,
            (w >> 8) & 0xFF, w & 0xFF,
            (h >> 8) & 0xFF, h & 0xFF,
            c332,
        )))
        self._dirty = True

    def record_text(self, x, y, text, fg, bg):
        """Called by emulator_menu.py's _draw_text() instead of the
        set_window()/write_data() pixel-blit path, when the display
        supports it (see hasattr check there)."""
        text_bytes = text.encode()
        if len(text_bytes) > 255:
            text_bytes = text_bytes[:255]
        fg332 = _rgb565_to_rgb332(fg)
        bg332 = _rgb565_to_rgb332(bg)
        self._cmds.append(bytes((
            0x01,  # CMD_TEXT
            (x >> 8) & 0xFF, x & 0xFF,
            (y >> 8) & 0xFF, y & 0xFF,
            fg332, bg332, len(text_bytes),
        )) + text_bytes)
        self._dirty = True

    def flush_if_dirty(self):
        """Send the accumulated command stream to the HDMI bridge if
        anything changed since the last flush, then reset it — each menu
        redraw starts with a full-screen fill_rect() (see
        emulator_menu.py's _draw_menu() etc.), so every flush represents
        one complete, self-contained frame, matching the game screen's own
        PKT_FRAME semantics (full frame each time, not a diff). Call this
        right before any polling-loop sleep in emulator_menu.py, matching
        where the physical LCD would otherwise already show the latest
        redraw."""
        if not self._dirty:
            return
        payload = b"".join(self._cmds)
        if self._bezel:
            self._lcd_c.send_hdmi_bezel_cmds(payload, len(payload), self.width, self.height, self.scale)
        else:
            self._lcd_c.send_hdmi_text_cmds(payload, len(payload), self.width, self.height, self.scale)
        self._cmds = []
        self._dirty = False


def create(real, lcd_c, bezel=False):
    """Factory for HDMIMirrorDisplay — returns None (instead of raising) if
    construction fails, so callers can fall back to the real display
    rather than crashing. The command-stream design keeps this object
    itself tiny, but this is kept as cheap defense-in-depth."""
    try:
        return HDMIMirrorDisplay(real, lcd_c, bezel=bezel)
    except MemoryError as e:
        print("HDMI menu mirror: allocation failed (%s), falling back to LCD" % e)
        return None


def hdmi_flush(display):
    """Safe to call on either a real display (no-op) or a HDMIMirrorDisplay
    (flushes if dirty) — avoids isinstance checks at every call site."""
    fn = getattr(display, "flush_if_dirty", None)
    if fn is not None:
        fn()
