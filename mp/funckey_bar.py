"""
FuncKeyBar - Displays the LCKEY..CALC key image below the LCD and handles touch input.

Image file: /fkbar.raw (320x42 pixels, RGB565 big-endian)
Generated from references/face.bmp via tools/gen_fkbar.ps1.
"""

_IMG_W = 320
_IMG_H = 42
_IMG_PATHS = ('/fkbar.raw', '/sd/fkbar.raw')
_TOUCH_X_OFFSET = 0
_TOUCH_Y_OFFSET = 24

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


def hit_test(x, y, y_top):
    if 0 <= x < _IMG_W and y_top <= y < y_top + _IMG_H:
        col = (x * 8) // _IMG_W
        key_coord, label = _FKEYS[col]
        return col, key_coord, label
    return None


class FuncKeyBar:
    def __init__(self, display, y_top):
        self._display = display
        self._y_top = y_top
        self._active_key = None

    def draw(self):
        """Blit the image to the display; fall back to plain boxes if file missing."""
        for path in _IMG_PATHS:
            try:
                self._blit_raw(path)
                return
            except OSError:
                continue
        self._draw_fallback()

    def _blit_raw(self, path):
        d = self._display
        d.set_window(0, self._y_top, _IMG_W - 1, self._y_top + _IMG_H - 1)
        d.dc.value(1)
        d.cs.value(0)
        buf = bytearray(512)
        try:
            with open(path, 'rb') as f:
                while True:
                    n = f.readinto(buf)
                    if not n:
                        break
                    d.spi.write(buf if n == len(buf) else buf[:n])
        finally:
            d.cs.value(1)

    def _draw_fallback(self):
        d = self._display
        w = _IMG_W // 8
        # Gray for navigation keys, pink-ish for MEMO/MEMO-IN
        colors = [0x7BEF, 0x7BEF, 0x7BEF, 0xD8BF, 0xD8BF, 0x7BEF, 0x7BEF, 0x7BEF]
        for i, color in enumerate(colors):
            d.fill_rect(i * w + 1, self._y_top + 1, w - 2, _IMG_H - 2, color)

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

        hit = hit_test(x, y, self._y_top)
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
