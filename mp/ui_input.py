import time

# USB HID scancodes for cursor keys
_CURSOR_KEYS = frozenset((0x4F, 0x50, 0x51, 0x52))  # RIGHT LEFT DOWN UP


class KeyRepeat:
    """Edge-detection + key-repeat helper for USB HID polling loops.

    Call poll(sc) each iteration with the result of hd61700.get_last_key().
    Returns sc if an action should fire, -1 otherwise.

    All keys fire once on first press. Cursor keys (UP/DOWN/LEFT/RIGHT)
    additionally repeat after delay_ms, then every interval_ms while held.
    sc == 0 (no key pressed) returns 0 once on release; callers skip it with
    ``if action > 0:``.
    """
    __slots__ = ('_delay', '_interval', '_prev', '_held_since', '_last_repeat')

    def __init__(self, delay_ms=400, interval_ms=100):
        self._delay = delay_ms
        self._interval = interval_ms
        self._prev = -1
        self._held_since = 0
        self._last_repeat = 0

    def poll(self, sc):
        now = time.ticks_ms()
        if sc != self._prev:
            self._prev = sc
            if sc in _CURSOR_KEYS:
                self._held_since = now
                self._last_repeat = now
            return sc
        if sc in _CURSOR_KEYS and sc:
            if (time.ticks_diff(now, self._held_since) >= self._delay and
                    time.ticks_diff(now, self._last_repeat) >= self._interval):
                self._last_repeat = now
                return sc
        return -1
