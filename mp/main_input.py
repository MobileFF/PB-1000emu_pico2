import sys
import time
import uselect


def _keypos(row, ki_col):
    return (row, ki_col)


KEY_EXE = _keypos(10, 4)


class KeyboardInputManager:
    def __init__(
        self,
        *,
        uart_kbd=None,
        enable_uart_kbd=False,
        auto_exe_on_enter=False,
        key_hold_ms=120,
        key_release_hard_timeout_ms=1200,
        inter_key_gap_ms=80,
        on_int_pulse_ms=30,
    ):
        self._uart_kbd = uart_kbd
        self._enable_uart_kbd = enable_uart_kbd
        self._auto_exe_on_enter = auto_exe_on_enter
        self._key_hold_ms = key_hold_ms
        self._key_release_hard_timeout_ms = key_release_hard_timeout_ms
        self._inter_key_gap_ms = inter_key_gap_ms
        self._on_int_pulse_ms = on_int_pulse_ms

        self._key_candidates = {
            "EXE": [KEY_EXE],
        }

        self._stdin_poll = uselect.poll()
        self._stdin_poll.register(sys.stdin, uselect.POLLIN)

        self._key_queue = []
        self._active_key = None
        self._active_key_label = None
        self._active_key_candidates = None
        self._active_key_candidate_idx = 0
        self._active_key_started = False
        self._active_keybuf_base = None
        self._release_at_ms = 0
        self._release_hard_at_ms = 0
        self._next_press_at_ms = 0
        self._typed_since_enter = False
        self._on_int_active = False
        self._on_int_release_at_ms = 0
        self._input_blocked_log_at_ms = 0

    def _resolve_key_candidates(self, key, label):
        if label in self._key_candidates:
            return self._key_candidates[label]
        return [key]

    def _map_input_char(self, char):
        if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char in " .+-*/=":
            return char, char
        if char == "!":
            return KEY_EXE, "EXE"
        if char == "@":
            return _keypos(5, 11), "MODE"
        if char == "[":
            return _keypos(6, 11), "LCKEY"
        if char == "]":
            return _keypos(4, 11), "CAL"
        if char == "`":
            return _keypos(7, 9), "&HFC"
        if char == "{":
            return _keypos(8, 9), "&HFD"
        if char == "|":
            return _keypos(9, 9), "&HFE"
        if char == "^":
            return _keypos(1, 1), "BRK"
        if char == "}":
            return _keypos(6, 6), "NEWALL"
        return None, None

    def _pulse_on_int(self, system, now_ms):
        if self._on_int_active:
            return
        try:
            system.set_on_int(True)
        except Exception:
            return
        self._on_int_active = True
        self._on_int_release_at_ms = time.ticks_add(now_ms, self._on_int_pulse_ms)

    def _poll_repl_input(self, system):
        if not self._stdin_poll.poll(0):
            return
        try:
            chars = sys.stdin.read(1)
            if chars == "\x1b":
                next_chars = sys.stdin.read(4) if self._stdin_poll.poll(10) else ""
                if "[23~" in next_chars:
                    print("F11 detected via REPL! Saving state...")
                    system._save_requested = True
                    return
                if "[20~" in next_chars:
                    print("F9 detected via REPL! Resetting...")
                    system.reset_emulator()
                    return
                for char in next_chars:
                    self._key_queue.append(self._map_input_char(char))
            else:
                for char in chars:
                    if char == "\x03":
                        print("\n[REPL] Break received")
                        self._key_queue.append(self._map_input_char("^"))
                    elif char in ("\r", "\n"):
                        if self._auto_exe_on_enter and self._typed_since_enter:
                            self._key_queue.append((KEY_EXE, "EXE"))
                            self._typed_since_enter = False
                    else:
                        key, label = self._map_input_char(char)
                        if key is not None:
                            self._key_queue.append((key, label))
                            if key != KEY_EXE:
                                self._typed_since_enter = True
        except Exception as e:
            print(f"REPL input error: {e}")

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
                char = "^"
            if char in ("\r", "\n"):
                if self._auto_exe_on_enter and self._typed_since_enter:
                    self._key_queue.append((KEY_EXE, "EXE"))
                    self._typed_since_enter = False
                continue
            key, label = self._map_input_char(char)
            if key is not None:
                if getattr(system, "is_sleeping", False) and label != "BRK":
                    continue
                self._key_queue.append((key, label))
                if key != KEY_EXE:
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
            self._active_key = None
            self._active_key_label = None
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
            dropped = 0
            kept = []
            for item in self._key_queue:
                if item[1] == "BRK":
                    kept.append(item)
                else:
                    dropped += 1
            if dropped:
                print(f"[QUEUE] dropped {dropped} non-BRK keys during sleep")
            self._key_queue[:] = kept
            if not self._key_queue:
                return

        pending_label = self._key_queue[0][1]
        allow_sleep_break = (pending_label == "BRK" and getattr(system, "is_sleeping", False))
        if (not allow_sleep_break and hasattr(system, "is_key_input_enabled")
                and not system.is_key_input_enabled()):
            if time.ticks_diff(now, self._input_blocked_log_at_ms) >= 0:
                sleep_state = 1 if getattr(system, "is_sleeping", False) else 0
                print(
                    f"[INPUT_BLOCKED] label={pending_label} "
                    f"sleep={sleep_state} key_enabled=0 queue_len={len(self._key_queue)}"
                )
                self._input_blocked_log_at_ms = time.ticks_add(now, 500)
            return

        key, label = self._key_queue.pop(0)
        if label == "BRK" and getattr(system, "is_sleeping", False):
            self._pulse_on_int(system, now)
            return

        candidates = self._resolve_key_candidates(key, label)
        key = candidates[0]
        print(f"Key Press: {label}")
        system.press_key(key)
        if hasattr(system, "set_status"):
            system.set_status(label)

        self._active_key = key
        self._active_key_label = label
        self._active_key_candidates = candidates
        self._active_key_candidate_idx = 0
        self._active_key_started = False
        if hasattr(system, "get_key_buffer_state"):
            self._active_keybuf_base = system.get_key_buffer_state()
        else:
            self._active_keybuf_base = None
        self._release_at_ms = time.ticks_add(now, self._key_hold_ms)
        self._release_hard_at_ms = time.ticks_add(now, self._key_release_hard_timeout_ms)

    def poll(self, system):
        now = time.ticks_ms()
        if self._on_int_active and time.ticks_diff(now, self._on_int_release_at_ms) >= 0:
            try:
                system.set_on_int(False)
            finally:
                self._on_int_active = False

        self._poll_repl_input(system)
        self._poll_uart_input(system)

        now = time.ticks_ms()
        self._release_active_on_timeout_or_scan(system, now)
        self._press_next_queued_key(system, now)


class TouchInputManager:
    def __init__(self):
        self._active_touch_key = None

    def poll(self, system):
        if not hasattr(system, "touch") or system.touch is None:
            return

        if system.touch.is_pressed():
            coords = system.touch.get_touch()
            if coords is not None:
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
                    return

        if self._active_touch_key is not None:
            system.release_key(self._active_touch_key)
            self._active_touch_key = None
