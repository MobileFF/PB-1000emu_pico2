import time

# USB HID scancode → PB-1000 key matrix (row, ki_col) for cursor keys
_CURSOR_COORDS = {
    0x4F: (3,  9),   # RIGHT
    0x50: (5, 10),   # LEFT
    0x51: (4,  9),   # DOWN
    0x52: (5,  9),   # UP
}


def _keypos(row, ki_col):
    return (row, ki_col)


KEY_EXE = _keypos(10, 4)


class KeyboardInputManager:
    def __init__(
        self,
        *,
        uart_kbd=None,
        enable_uart_kbd=False,
        uart_enter_always_exe=True,
        key_hold_ms=120,
        key_release_hard_timeout_ms=1200,
        inter_key_gap_ms=80,
        on_int_pulse_ms=30,
    ):
        self._uart_kbd = uart_kbd
        self._enable_uart_kbd = enable_uart_kbd
        self._uart_enter_always_exe = uart_enter_always_exe
        self._key_hold_ms = key_hold_ms
        self._key_release_hard_timeout_ms = key_release_hard_timeout_ms
        self._inter_key_gap_ms = inter_key_gap_ms
        self._on_int_pulse_ms = on_int_pulse_ms

        self._key_candidates = {
            "EXE": [KEY_EXE],
        }

        self._key_queue = []
        self._active_key = None
        self._active_key_label = None
        self._active_chord = []
        self._active_key_candidates = None
        self._active_key_candidate_idx = 0
        self._active_key_started = False
        self._active_keybuf_base = None
        self._release_at_ms = 0
        self._release_hard_at_ms = 0
        self._next_press_at_ms = 0
        self._typed_since_enter = False
        self._last_was_cr = False
        self._on_int_active = False
        self._on_int_release_at_ms = 0
        self._input_blocked_log_at_ms = 0
        self._uart_char_map = self._build_uart_char_map()

    def _resolve_key_candidates(self, key, label):
        if label in self._key_candidates:
            return self._key_candidates[label]
        return [key]

    def _build_uart_char_map(self):
        """Build {char: (primary_coord, label, chord_list)} from keymap.
        Chord list contains keys that must be held simultaneously with the primary key."""
        try:
            import keymap as _km
        except ImportError:
            return {}
        result = {}
        # Base keys: single-char labels (upper and lower map to same physical key)
        for sc, entry in _km.USB_MAP.items():
            coord, label = entry[0], entry[1]
            if len(label) == 1:
                result[label.lower()] = (coord, label, [])
                result[label.upper()] = (coord, label, [])
        # Space (label is 'SPACE', not a single char)
        if 0x2C in _km.USB_MAP:
            result[' '] = (_km.USB_MAP[0x2C][0], 'SPACE', [])
        # Backspace terminal codes → BS key
        if 0x2A in _km.USB_MAP:
            bs_coord = _km.USB_MAP[0x2A][0]
            result['\x08'] = (bs_coord, 'BS', [])
            result['\x7F'] = (bs_coord, 'BS', [])
        # Advanced keys: chord sequences (e.g. SHIFT+D_QUOTE for '!')
        for key_pair, entry in _km.ADV_MAP.items():
            coords, label = entry[0], entry[1]
            if len(label) == 1 and len(coords) >= 1:
                primary = coords[-1]
                chord = list(coords[:-1])
                result[label] = (primary, label, chord)
        return result

    def _pulse_on_int(self, system, now_ms):
        if self._on_int_active:
            return
        try:
            system.set_on_int(True)
        except Exception:
            return
        self._on_int_active = True
        self._on_int_release_at_ms = time.ticks_add(now_ms, self._on_int_pulse_ms)

    def _poll_uart_input(self, system):
        if not self._enable_uart_kbd or self._uart_kbd is None:
            return
        while self._uart_kbd.any():
            try:
                char = self._uart_kbd.read(1).decode("utf-8")
            except Exception:
                char = None
            if not char:
                break
            if char == "\x03":
                print("\n[UART] Break received")
                self._key_queue.append(((1, 1), "BRK", []))
                continue
            if char in ("\r", "\n"):
                if char == "\n" and self._last_was_cr:
                    self._last_was_cr = False
                    continue
                self._last_was_cr = (char == "\r")
                if self._uart_enter_always_exe or self._typed_since_enter:
                    self._key_queue.append((KEY_EXE, "EXE", []))
                    self._typed_since_enter = False
                continue
            self._last_was_cr = False
            entry = self._uart_char_map.get(char)
            if entry is not None:
                key, label, chord = entry
                self._key_queue.append((key, label, chord))
                if label != "EXE":
                    self._typed_since_enter = True

    def _release_active_on_timeout_or_scan(self, system, now):
        if self._active_key is None:
            return

        st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
        chata = st["chata"] if st else 0x00
        keyin = st["keyin"] if st else 0x80

        if chata != 0x07:
            self._active_key_started = True

        if hasattr(system, "can_release_active_key"):
            should_release = system.can_release_active_key(self._active_keybuf_base)
        else:
            should_release = self._active_key_started and (chata == 0x20)

        scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))
        if scan_gated and self._active_key_started and (not should_release):
            if chata == 0x07 and keyin != 0x80:
                should_release = True

        timed_out = time.ticks_diff(now, self._release_at_ms) >= 0
        if scan_gated and not should_release:
            timed_out = time.ticks_diff(now, self._release_hard_at_ms) >= 0

        if timed_out and not should_release:
            if self._active_key_candidates is not None:
                next_idx = self._active_key_candidate_idx + 1
                if next_idx < len(self._active_key_candidates):
                    system.release_key(self._active_key)
                    self._active_key_candidate_idx = next_idx
                    self._active_key = self._active_key_candidates[self._active_key_candidate_idx]
                    print(f"Key Retry: {self._active_key_label} -> {self._active_key}")
                    system.press_key(self._active_key)
                    self._active_key_started = False
                    if hasattr(system, "get_key_buffer_state"):
                        self._active_keybuf_base = system.get_key_buffer_state()
                    else:
                        self._active_keybuf_base = None
                    self._release_at_ms = time.ticks_add(now, self._key_hold_ms)
                    self._release_hard_at_ms = time.ticks_add(now, self._key_release_hard_timeout_ms)
                    return
            should_release = True

        if should_release:
            system.release_key(self._active_key)
            for ck in self._active_chord:
                system.release_key(ck)
            self._active_key = None
            self._active_key_label = None
            self._active_chord = []
            self._active_key_candidates = None
            self._active_key_candidate_idx = 0
            self._active_key_started = False
            self._active_keybuf_base = None
            self._next_press_at_ms = time.ticks_add(now, self._inter_key_gap_ms)

    def _press_next_queued_key(self, system, now):
        if self._active_key is not None or not self._key_queue:
            return
        if time.ticks_diff(now, self._next_press_at_ms) < 0:
            return
        if getattr(system, "is_sleeping", False):
            # CPU is sleeping: keep keys queued and wait.
            # run_cpu_slice now services c_kb_service_input_lines() even
            # during sleep, so KEY_INT will fire and clear CPU_SLP shortly.
            return

        pending_label = self._key_queue[0][1]
        # BRK always bypasses is_key_input_enabled: it must interrupt the ROM
        # even during RECE or other busy states (sleep is already handled above).
        is_brk = pending_label in ("BRK", "BREAK")
        if (not is_brk and hasattr(system, "is_key_input_enabled")
                and not system.is_key_input_enabled()):
            if time.ticks_diff(now, self._input_blocked_log_at_ms) >= 0:
                print(
                    f"[INPUT_BLOCKED] label={pending_label} "
                    f"sleep=0 key_enabled=0 queue_len={len(self._key_queue)}"
                )
                self._input_blocked_log_at_ms = time.ticks_add(now, 500)
            return

        key, label, chord = self._key_queue.pop(0)

        if label in ("BRK", "BREAK"):
            pio = getattr(system, 'pio_uart', None)
            if pio is not None and hasattr(pio, 'flush_rx'):
                pio.flush_rx()
                print("[BRK] PIO UART RX flushed")

        candidates = self._resolve_key_candidates(key, label)
        key = candidates[0]
        print(f"Key Press: {label}")
        # Press chord keys (e.g. SHIFT) before the primary key
        for ck in chord:
            system.press_key(ck)
        system.press_key(key)
        if hasattr(system, "set_status"):
            system.set_status(label)

        self._active_key = key
        self._active_key_label = label
        self._active_chord = chord
        self._active_key_candidates = candidates
        self._active_key_candidate_idx = 0
        self._active_key_started = False
        if hasattr(system, "get_key_buffer_state"):
            self._active_keybuf_base = system.get_key_buffer_state()
        else:
            self._active_keybuf_base = None
        self._release_at_ms = time.ticks_add(now, self._key_hold_ms)
        self._release_hard_at_ms = time.ticks_add(now, self._key_release_hard_timeout_ms)

    def enqueue_key(self, key, label):
        """Inject a key press from an external event (e.g. auto-BREAK on EOF)."""
        self._key_queue.append((key, label, []))

    def poll(self, system):
        now = time.ticks_ms()
        if self._on_int_active and time.ticks_diff(now, self._on_int_release_at_ms) >= 0:
            try:
                system.set_on_int(False)
            finally:
                self._on_int_active = False

        self._poll_uart_input(system)

        now = time.ticks_ms()
        self._release_active_on_timeout_or_scan(system, now)
        self._press_next_queued_key(system, now)


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
        lw = int(192 * scale)
        lh = int(32 * scale)
        lx0 = system._disp_x
        ly0 = system._disp_y

        if lx0 <= x <= lx0 + lw and ly0 <= y <= ly0 + lh:
            rx = (x - lx0) / lw
            ry = (y - ly0) / lh

            col = max(0, min(3, int(rx * 4)))
            row = 3 - int(ry * 4)
            row = max(0, min(3, row))

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


class CursorRepeatManager:
    """Key repeat for cursor keys in PB-1000 emulation.

    Periodically releases then re-presses the held cursor key in the HD61700
    key matrix, making the ROM treat each repeat as a fresh key press.

    Requires hd61700.get_held_cursor_key() in the C module.
    """

    _DELAY_MS    = 400  # initial hold before first repeat
    _RELEASE_MS  =  35  # release gap (must exceed one KEY_INT cycle = 25ms)
    _INTERVAL_MS = 100  # interval between repeats

    _IDLE    = 0
    _ARMED   = 1  # cursor held, waiting for initial delay
    _RELEASE = 2  # key released from matrix, waiting for ROM to notice
    _PRESS   = 3  # key re-pressed, waiting for next interval

    def __init__(self):
        import hd61700 as _hd
        self._hd        = _hd
        self._available = hasattr(_hd, 'get_held_cursor_key')
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
                # Different cursor key: just (re)start ARMED state.
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
                    self._phase = self._RELEASE
                    self._next  = time.ticks_add(now, self._RELEASE_MS)
            elif self._phase == self._RELEASE:
                if time.ticks_diff(now, self._next) >= 0:
                    system.press_key(self._coord)
                    self._phase = self._PRESS
                    self._next  = time.ticks_add(now, self._INTERVAL_MS)
            elif self._phase == self._PRESS:
                if time.ticks_diff(now, self._next) >= 0:
                    system.release_key(self._coord)
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
