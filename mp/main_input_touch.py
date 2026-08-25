"""
PB-1000 touch panel input manager. Split out of the former monolithic
main_input.py -- see main_input_keyboard.py's module docstring for why.
"""


class TouchInputManager:
    def __init__(self):
        self._active_touch_key = None

    def release(self, system):
        if self._active_touch_key is not None:
            system.release_key(self._active_touch_key)
            self._active_touch_key = None

    def poll_coords(self, system, coords):
        if coords is None:
            self.release(system)
            return False

        x, y = coords
        x += getattr(system, "touch_x_offset", 0)
        y += getattr(system, "touch_y_offset", 0)

        scale = getattr(system.lcd, "scale", 1.0)
        # TK1..16 corresponds to the physical touch pad, which always covers
        # only the original 32-dot LCD area — it does not grow with
        # [display] lcd_height (64-dot mode is VRAM/graphics-only, the real
        # hardware never had a taller touch pad).
        lw = int(192 * scale)
        lh = int(32 * scale)
        lx0 = system._disp_x
        ly0 = system._disp_y

        if lx0 <= x <= lx0 + lw and ly0 <= y <= ly0 + lh:
            rx = (x - lx0) / lw
            ry = (y - ly0) / lh

            col = max(0, min(3, int(rx * 4)))
            row = max(0, min(3, int(ry * 4)))

            t_idx = row * 4 + col + 1
            t_key = f"TK{t_idx}"

            if self._active_touch_key != t_key:
                if self._active_touch_key is not None:
                    system.release_key(self._active_touch_key)
                system.press_key(t_key)
                self._active_touch_key = t_key
            return True

        self.release(system)
        return False

    def poll(self, system):
        if not hasattr(system, "touch") or system.touch is None:
            return

        if system.touch.is_pressed():
            coords = system.touch.get_touch()
            if self.poll_coords(system, coords):
                return

        self.release(system)
