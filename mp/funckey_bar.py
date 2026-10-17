"""
FuncKeyBar - Draws the LCKEY..CALC key row below the LCD and handles touch input.

Drawn entirely from code (fill_rect() + draw_text(), via draw_text.py's
framebuf-based 8x8 font) -- no image file is read for this anymore. This
replaces the previous /fkbar.raw (320x42 RGB565 big-endian) bitmap blit.

Why: reading /fkbar.raw from the SD card (as a fallback when it isn't on
flash) was found to intermittently corrupt the displayed image -- SD SPI
bus activity during the chunked read/write loop appears to electrically
couple into the LCD's shift-register/column-counter state even with CS
nominally deasserted (see 調査用/ for the investigation). Drawing the bar
from code sidesteps the problem by construction: there is no file I/O on
this path at all, so there is no SD-vs-LCD SPI interleaving to corrupt
anything. See tools/split_fkbar.py / mp/test/test_fkbar_*.py for the
investigation scripts that led here.

Colors are derived from a real-device reference photo (調査用/IMG_2532.heic):
the photo's near-white pastel button colors were reduced to their hue only,
then rebuilt at S=55%/V=90% (HSV) to get a visibly distinct, saturated set
instead of the muted real-device pastel (plain hue-preserving brightness
scaling just produced darker grays, not more saturated colors, since
scaling R/G/B by a constant factor doesn't change the max/min ratio that
defines saturation). The real device has 4 color groups:
  LCKEY             = yellow-green-gray
  MENU, CAL         = lavender
  MEMO, MEMO IN     = pink
  IN, OUT, CALC     = blue-gray
The outer border/divider color and the (black) text color are also taken
from that photo. The font itself is framebuf's built-in 8x8 bitmap font,
not a match for the real device's print -- there's no suitable alternative
font available in MicroPython, and matching glyph shapes exactly isn't the
point (fitting inside each button legibly is).
"""

from draw_text import draw_text

_IMG_W = 320
_IMG_H = 42
_TOUCH_X_OFFSET = 0
_TOUCH_Y_OFFSET = 24

_N_KEYS = 8
_KEY_W = _IMG_W // _N_KEYS  # 40
_LCKEY_CELL = 7  # LCKEY needs a slightly-smaller-than-8px font to fit all 5 chars

# 8 keys left-to-right as they appear on the PB-1000 face
_FKEYS = [
    ((6, 11), 'LCKEY'),
    ((5, 11), 'MENU'),
    ((4, 11), 'CAL'),
    ((3, 11), 'MEMO'),
    ((2, 11), 'MEMO IN'),
    ((2, 10), 'IN'),
    ((3, 10), 'OUT'),
    ((4, 10), 'CALC'),
]
_MEMO_IN_LINES = ('MEMO', 'IN')  # drawn as 2 centered lines instead of 1

# Hue-only values from the reference photo, rebuilt at S=55%/V=90%.
_COLOR_LCKEY = 0xDF2C     # yellow-green-gray
_COLOR_LAVENDER = 0x833C  # MENU, CAL
_COLOR_PINK = 0xE334      # MEMO, MEMO IN
_COLOR_BLUEGRAY = 0x647C  # IN, OUT, CALC
_KEY_COLORS = [_COLOR_LCKEY, _COLOR_LAVENDER, _COLOR_LAVENDER,
               _COLOR_PINK, _COLOR_PINK,
               _COLOR_BLUEGRAY, _COLOR_BLUEGRAY, _COLOR_BLUEGRAY]
_TEXT_FG = 0x0000  # black

_BORDER_COLOR = 0x6B6E  # measured from the reference photo
_BORDER_THICKNESS = 2


def hit_test(x, y, y_top, x_left=0):
    if x_left <= x < x_left + _IMG_W and y_top <= y < y_top + _IMG_H:
        col = ((x - x_left) * 8) // _IMG_W
        key_coord, label = _FKEYS[col]
        return col, key_coord, label
    return None


def _sw16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


# LCKEY-only scaled-glyph scratch buffers (lazily created, reused forever --
# same "fixed buffer, never reallocated" discipline as draw_text.py).
_big_buf = None
_big_fb = None
_small_buf = None


def _draw_text_scaled(display, x, y, text, fg, bg, cell):
    """Like draw_text(), but shrinks each 8x8 glyph to cell x cell by
    nearest-neighbor sampling first (framebuf has no scaled blit). Only
    used for LCKEY, which doesn't fit in _KEY_W at the normal 8px/char
    width. Kept local here rather than added to the shared draw_text.py,
    which several other screens (profile picker, setup menu, etc.) use as
    unscaled 8px text only."""
    import framebuf

    global _big_buf, _big_fb, _small_buf
    if _big_buf is None:
        _big_buf = bytearray(8 * 8 * 2)
        _big_fb = framebuf.FrameBuffer(_big_buf, 8, 8, framebuf.RGB565)
        _small_buf = bytearray(cell * cell * 2)
    big_buf, big_fb, small_buf = _big_buf, _big_fb, _small_buf

    cx = x
    for ch in text:
        big_fb.fill(_sw16(bg))
        big_fb.text(ch, 0, 0, _sw16(fg))
        for sy in range(cell):
            src_y = (sy * 8) // cell
            row_off = src_y * 8 * 2
            dst_row_off = sy * cell * 2
            for sx in range(cell):
                src_x = (sx * 8) // cell
                src_off = row_off + src_x * 2
                dst_off = dst_row_off + sx * 2
                small_buf[dst_off] = big_buf[src_off]
                small_buf[dst_off + 1] = big_buf[src_off + 1]
        display.set_window(cx, y, cx + cell - 1, y + cell - 1)
        display.write_data(small_buf)
        cx += cell


class FuncKeyBar:
    def __init__(self, display, y_top, x_offset=0):
        self._display = display
        self._y_top = y_top
        disp_w = getattr(display, 'width', _IMG_W)
        self._x_offset = max(0, min(x_offset, disp_w - _IMG_W))
        self._active_key = None

    def set_y_top(self, y):
        """Reposition the bar (e.g. after an LCD height toggle changes the
        LCD area's on-screen height). Caller is responsible for clearing the
        old area and calling draw() again."""
        self._y_top = y

    def draw(self):
        """Draw the bar from code: filled key rectangles + labels + an
        outer border. No file I/O is involved (see module docstring)."""
        d = self._display
        x0 = self._x_offset
        y0 = self._y_top

        for i in range(_N_KEYS):
            kx = x0 + i * _KEY_W
            color = _KEY_COLORS[i]
            # Leave a 1px gap so adjacent keys read as visually separate.
            d.fill_rect(kx + 1, y0 + 1, _KEY_W - 2, _IMG_H - 2, color)

            if i == 4:
                # MEMO IN: doesn't fit on one line, so split into two,
                # each centered, matching how the real device prints it.
                line1, line2 = _MEMO_IN_LINES
                gap = 2
                block_h = 8 + gap + 8
                ty1 = y0 + (_IMG_H - block_h) // 2
                ty2 = ty1 + 8 + gap
                for label, ty in ((line1, ty1), (line2, ty2)):
                    tw = len(label) * 8
                    tx = kx + max(0, (_KEY_W - tw) // 2)
                    draw_text(d, tx, ty, label, _TEXT_FG, bg=color)
                continue

            label = _FKEYS[i][1]
            ty = y0 + (_IMG_H - 8) // 2
            if i == 0:
                # LCKEY: 5 chars don't fit at 8px/char, use the shrunk font.
                tw = len(label) * _LCKEY_CELL
                tx = kx + max(0, (_KEY_W - tw) // 2)
                _draw_text_scaled(d, tx, ty, label, _TEXT_FG, color,
                                   _LCKEY_CELL)
            else:
                tw = len(label) * 8
                tx = kx + max(0, (_KEY_W - tw) // 2)
                draw_text(d, tx, ty, label, _TEXT_FG, bg=color)

        self._draw_outer_border(d, x0, y0)

    def _draw_outer_border(self, d, x0, y0):
        t = _BORDER_THICKNESS
        d.fill_rect(x0, y0, _IMG_W, t, _BORDER_COLOR)                    # top
        d.fill_rect(x0, y0 + _IMG_H - t, _IMG_W, t, _BORDER_COLOR)       # bottom
        d.fill_rect(x0, y0, t, _IMG_H, _BORDER_COLOR)                    # left
        d.fill_rect(x0 + _IMG_W - t, y0, t, _IMG_H, _BORDER_COLOR)       # right

    def release(self, system):
        if self._active_key is not None:
            system.release_key(self._active_key)
            self._active_key = None

    def poll_coords(self, system, coords):
        if coords is None:
            self.release(system)
            return False

        x, y = coords
        x += getattr(system, 'funckey_touch_x_offset', _TOUCH_X_OFFSET)
        y += getattr(system, 'funckey_touch_y_offset', _TOUCH_Y_OFFSET)

        hit = hit_test(x, y, self._y_top, self._x_offset)
        if hit is not None:
            _, key_coord, label = hit
            if self._active_key != key_coord:
                if self._active_key is not None:
                    system.release_key(self._active_key)
                system.press_key(key_coord)
                if hasattr(system, 'set_status'):
                    system.set_status(label)
                self._active_key = key_coord
            return True

        self.release(system)
        return False

    def poll(self, system):
        if not hasattr(system, 'touch') or system.touch is None:
            return

        if system.touch.is_pressed():
            coords = system.touch.get_touch()
            if self.poll_coords(system, coords):
                return

        self.release(system)
