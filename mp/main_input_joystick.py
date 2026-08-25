"""
PB-1000 joystick input manager. Split out of the former monolithic
main_input.py -- see main_input_keyboard.py's module docstring for why.
"""
import time


def _parse_joystick_key(value):
    """
    Resolve a config string to a PB-1000 key coordinate.
    Accepts: named constant without prefix ("exe", "up", "ans", "shift", ...),
             single-char KEY_MAP label ("a"-"z", "0"-"9"),
             or raw "row,col" (e.g. "10,4").
    Returns a (row, ki_col) tuple, or None if unresolvable.
    """
    if not value:
        return None
    v = value.strip().lower()
    if "," in v:
        parts = v.split(",", 1)
        try:
            return (int(parts[0].strip()), int(parts[1].strip()))
        except ValueError:
            return None
    import keymap as _km
    if v in _km.KEY_MAP:
        return _km.KEY_MAP[v]
    coord = getattr(_km, "KEY_" + v.upper(), None)
    if coord is not None:
        return coord
    return None


class JoystickInputManager:
    DEFAULT_PIN_MAP = {
        "up":    18,
        "down":  19,
        "left":  20,
        "right": 21,
        "fire1": 26,
        "fire2": 27,
    }

    DEFAULT_KEY_MAP = {
        "up":    (5,  9),   # KEY_UP
        "down":  (4,  9),   # KEY_DOWN
        "left":  (5, 10),   # KEY_LEFT
        "right": (3,  9),   # KEY_RIGHT
        "fire1": (10, 1),   # KEY_EXE
        "fire2": (10, 4),   # KEY_SHIFT
    }

    def __init__(
        self,
        *,
        pin_map=None,
        key_map=None,
        debounce_ms=20,
        poll_interval_ms=10,
        enable_fire2=True,
    ):
        from machine import Pin

        pin_map = pin_map if pin_map is not None else self.DEFAULT_PIN_MAP
        self._key_map = key_map if key_map is not None else dict(self.DEFAULT_KEY_MAP)
        self._debounce_ms = debounce_ms
        self._poll_interval_ms = poll_interval_ms

        buttons = list(pin_map.keys())
        if not enable_fire2 and "fire2" in buttons:
            buttons.remove("fire2")

        self._pins = {}
        self._raw_state = {}
        self._confirmed_state = {}
        self._debounce_deadline = {}
        self._next_poll_at = 0

        for btn in buttons:
            try:
                self._pins[btn] = Pin(pin_map[btn], Pin.IN, Pin.PULL_UP)
                self._raw_state[btn] = 1
                self._confirmed_state[btn] = False
                self._debounce_deadline[btn] = 0
            except Exception as e:
                print(f"Joystick: {btn} skip: {e}")

    def poll(self, system):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._next_poll_at) < 0:
            return
        self._next_poll_at = time.ticks_add(now, self._poll_interval_ms)

        for btn, pin in self._pins.items():
            raw = pin.value()
            if raw != self._raw_state[btn]:
                self._raw_state[btn] = raw
                self._debounce_deadline[btn] = time.ticks_add(now, self._debounce_ms)

            if time.ticks_diff(now, self._debounce_deadline[btn]) < 0:
                continue

            new_on = (self._raw_state[btn] == 0)
            if new_on == self._confirmed_state[btn]:
                continue

            self._confirmed_state[btn] = new_on
            key = self._key_map.get(btn)
            if key is None:
                continue
            if new_on:
                system.press_key(key)
                if hasattr(system, "set_status"):
                    system.set_status(btn.upper())
            else:
                system.release_key(key)
