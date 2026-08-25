"""
PB-1000 cursor-key repeat manager. Split out of the former monolithic
main_input.py -- see main_input_keyboard.py's module docstring for why.
"""
import time

# USB HID scancode -> PB-1000 key matrix (row, ki_col) for cursor keys
_CURSOR_COORDS = {
    0x4F: (3,  9),   # RIGHT
    0x50: (5, 10),   # LEFT
    0x51: (4,  9),   # DOWN
    0x52: (5,  9),   # UP
}


class CursorRepeatManager:
    """Key repeat for cursor keys in PB-1000 emulation.

    Periodically releases then re-presses the held cursor key in the HD61700
    key matrix, making the ROM treat each repeat as a fresh key press.

    Requires hd61700.get_held_cursor_key() in the C module.
    """

    _DELAY_MS    = 400  # initial hold before first repeat
    # ROM requires ~7 consecutive row-3 empty KEY_INT scans (25ms each) to clear
    # its cursor-key hold state. 175ms confirmed minimum; 150ms insufficient.
    _RELEASE_MS  = 175  # release gap (key absent from matrix)
    _INTERVAL_MS = 100  # key present in matrix per repeat cycle

    _IDLE    = 0
    _ARMED   = 1  # cursor held, waiting for initial delay
    _RELEASE = 2  # key released from matrix, waiting for ROM to notice
    _PRESS   = 3  # key re-pressed, waiting for next interval

    def __init__(self):
        import hd61700 as _hd
        self._hd        = _hd
        self._available = hasattr(_hd, 'get_held_cursor_key')
        self._steer     = getattr(_hd, 'steer_next_key_int', None)
        self._coord  = None
        self._sc     = 0
        self._phase  = self._IDLE
        self._next   = 0

    def poll(self, system, now):
        if not self._available:
            return
        sc = self._hd.get_held_cursor_key()

        if sc and sc in _CURSOR_COORDS:
            coord = _CURSOR_COORDS[sc]
            if sc != self._sc:
                # Different cursor key: restart ARMED state.
                # The C module handles releasing the previous key from the
                # matrix when its USB release event arrives — do NOT call
                # system.press_key() here, that would cause phantom presses.
                self._sc    = sc
                self._coord = coord
                self._phase = self._ARMED
                self._next  = time.ticks_add(now, self._DELAY_MS)
            elif self._phase == self._ARMED:
                if time.ticks_diff(now, self._next) >= 0:
                    system.release_key(self._coord)
                    if self._steer:
                        self._steer(self._coord[0])
                    self._phase = self._RELEASE
                    self._next  = time.ticks_add(now, self._RELEASE_MS)
            elif self._phase == self._RELEASE:
                if time.ticks_diff(now, self._next) >= 0:
                    system.press_key(self._coord)
                    if self._steer:
                        self._steer(self._coord[0])
                    self._phase = self._PRESS
                    self._next  = time.ticks_add(now, self._INTERVAL_MS)
            elif self._phase == self._PRESS:
                if time.ticks_diff(now, self._next) >= 0:
                    system.release_key(self._coord)
                    if self._steer:
                        self._steer(self._coord[0])
                    self._phase = self._RELEASE
                    self._next  = time.ticks_add(now, self._RELEASE_MS)
        else:
            # Physical cursor key released (c_kb_held_cursor == 0).
            # The C module already cleared the matrix bit via the USB release
            # event — do NOT call system.press_key(), that would leave the
            # key stuck in the matrix.
            self._sc    = 0
            self._coord = None
            self._phase = self._IDLE
