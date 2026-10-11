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
    if v in _km.NAMED_KEYS:
        return _km.NAMED_KEYS[v]
    coord = getattr(_km, "KEY_" + v.upper(), None)
    if coord is not None:
        return coord
    return None


def _parse_combo_config(cfg):
    """Parse pb1000.ini's [joystick_combo] section into a list of
    (frozenset(button_names), key_coords) tuples, largest combo first:

        [joystick_combo]
        fire1+fire2 = exe
        up+fire1    = brk

    Each key is 2 or more button names (matching JoystickInputManager's
    pin_map keys: up/down/left/right/fire1/fire2) joined by "+"; each
    value is anything _parse_joystick_key() accepts. Sorting largest-first
    means a bigger combo is checked (and wins) before a smaller one that's
    a subset of it -- see JoystickInputManager._apply_state()."""
    combos = []
    for buttons_str, key_str in cfg.get("joystick_combo", {}).items():
        buttons = frozenset(b.strip() for b in buttons_str.split("+") if b.strip())
        if len(buttons) < 2:
            continue
        key = _parse_joystick_key(key_str)
        if key is None:
            continue
        combos.append((buttons, key))
    combos.sort(key=lambda c: -len(c[0]))
    return combos


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
        "fire1": (10, 4),   # KEY_EXE
        "fire2": (11, 2),   # KEY_SHIFT
    }

    def __init__(
        self,
        *,
        pin_map=None,
        key_map=None,
        combos=None,
        debounce_ms=20,
        poll_interval_ms=10,
        enable_fire2=True,
    ):
        from machine import Pin

        pin_map = pin_map if pin_map is not None else self.DEFAULT_PIN_MAP
        self._key_map = key_map if key_map is not None else dict(self.DEFAULT_KEY_MAP)
        # (frozenset(button_names), key_coords), largest combo first -- see
        # _parse_combo_config()/_apply_state(). Empty by default: with no
        # combos configured, behaviour is identical to the pre-combo code
        # (one button -> its own key_map entry, nothing more).
        self._combos = list(combos) if combos else []
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
        self._active_keys = set()  # PB-1000 keys currently held down by us

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

        changed = False
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
            changed = True

        if changed:
            self._apply_state(system)

    def _apply_state(self, system):
        """Recompute the desired PB-1000 key set from the current physical
        button state plus self._combos (largest combo wins when several
        overlap, e.g. an up+fire1+fire2 combo pre-empts a smaller up+fire1
        one), then press/release only the keys that actually changed.
        This is what lets N buttons held together map to one PB-1000 key
        distinct from any individual button's own mapping."""
        pressed = {b for b, on in self._confirmed_state.items() if on}
        consumed = set()
        desired = {}  # key -> status-bar label
        for buttons, key in self._combos:
            if buttons <= pressed and not (buttons & consumed):
                desired[key] = "+".join(b.upper() for b in sorted(buttons))
                consumed |= buttons
        for btn in pressed - consumed:
            key = self._key_map.get(btn)
            if key is not None:
                desired.setdefault(key, btn.upper())

        desired_keys = set(desired.keys())
        for key in self._active_keys - desired_keys:
            system.release_key(key)
        newly_pressed = desired_keys - self._active_keys
        for key in newly_pressed:
            system.press_key(key)
        if newly_pressed and hasattr(system, "set_status"):
            system.set_status(desired[next(iter(newly_pressed))])
        self._active_keys = desired_keys
