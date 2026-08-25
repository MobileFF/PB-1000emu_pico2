"""
PB-1000 keyboard input manager.

Split out of the former monolithic main_input.py (535 lines) into one file
per input source (this one, main_input_touch.py, main_input_joystick.py,
main_input_cursor.py) so that main.py's Step 8b -- constructing all four
managers right after load_state(), the freshest heap boot has left --
doesn't have to compile all four in one shot. KeyboardInputManager alone
is still the biggest single piece (it's the one every profile actually
needs), but ~270 lines is a meaningfully smaller single compile than 535,
which was still observed to fail with MemoryError at that point even
after being moved to the earliest heap-safe position in boot (see
main.py's Step 8b comment for that history).
"""
import time

# USB HID scancode -> PB-1000 key matrix (row, ki_col) for cursor keys is
# in main_input_cursor.py (CursorRepeatManager's own concern, not this one's).


def _keypos(row, ki_col):
    return (row, ki_col)


KEY_EXE = _keypos(10, 4)

# Safety net for _press_next_queued_key: if is_key_input_enabled() stays
# False this long for the same queued key, press it anyway rather than
# blocking forever. The ROM's KEY_INT enable bit (REG_IE bit 6) is pure
# software state (see check_irqs() in hd61700.c, which never touches it),
# so a stuck debounce/ISR state on the ROM side can otherwise deadlock
# input indefinitely.
_INPUT_BLOCKED_TIMEOUT_MS = 1000


class KeyboardInputManager:
    def __init__(
        self,
        *,
        key_hold_ms=120,
        key_release_hard_timeout_ms=1200,
        inter_key_gap_ms=80,
    ):
        self._key_hold_ms = key_hold_ms
        self._key_release_hard_timeout_ms = key_release_hard_timeout_ms
        self._inter_key_gap_ms = inter_key_gap_ms

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
        self._input_blocked_log_at_ms = 0
        self._input_blocked_since_ms = None

    def _resolve_key_candidates(self, key, label):
        if label in self._key_candidates:
            return self._key_candidates[label]
        return [key]

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
            self._input_blocked_since_ms = None
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
        blocked = (not is_brk and hasattr(system, "is_key_input_enabled")
                   and not system.is_key_input_enabled())
        if blocked:
            if self._input_blocked_since_ms is None:
                self._input_blocked_since_ms = now
            waited_ms = time.ticks_diff(now, self._input_blocked_since_ms)
            if waited_ms < _INPUT_BLOCKED_TIMEOUT_MS:
                if time.ticks_diff(now, self._input_blocked_log_at_ms) >= 0:
                    pc = getattr(system, "pc", -1)
                    print(
                        f"[INPUT_BLOCKED] label={pending_label} "
                        f"sleep={getattr(system, 'is_sleeping', False)} "
                        f"key_enabled={system.is_key_input_enabled()} "
                        f"pc={pc:#06x} queue_len={len(self._key_queue)}"
                    )
                    self._input_blocked_log_at_ms = time.ticks_add(now, 500)
                return
            # Gave up waiting for the ROM to re-enable KEY_INT: press anyway
            # rather than blocking forever (mirrors the BRK bypass above).
            print(f"[INPUT_BLOCKED] label={pending_label} timed out after "
                  f"{waited_ms}ms waiting for key_enabled; pressing anyway")
        self._input_blocked_since_ms = None

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
        self._release_active_on_timeout_or_scan(system, now)
        self._press_next_queued_key(system, now)
