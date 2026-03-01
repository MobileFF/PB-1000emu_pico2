"""
PB-1000 Emulator - Normal Run Script
"""
import machine
import sys
import time
import uselect
from pb1000 import PB1000System,init_display,draw_bezel
import force_gc

KEY_HOLD_MS = 120
KEY_RELEASE_HARD_TIMEOUT_MS = 1200
INTER_KEY_GAP_MS = 80
STEP_SERVICE_CHUNK = 64
STEP_TIMER_TICK_STEPS = 40000
FRAME_INTERVAL_MS = 1000
AUTO_EXE_ON_ENTER = False

def _keypos(row, ki_col):
    return (row, ki_col)

KEY_EXE = _keypos(10, 4)


_KEY_CANDIDATES = {
    "EXE": [KEY_EXE],
}


_spoll = uselect.poll()
_spoll.register(sys.stdin, uselect.POLLIN)
_key_queue = []
_active_key = None
_active_key_label = None
_active_key_candidates = None
_active_key_candidate_idx = 0
_active_key_started = False
_active_keybuf_base = None
_release_at_ms = 0
_release_hard_at_ms = 0
_next_press_at_ms = 0
_typed_since_enter = False

STOP_PC=0xFFFF

def _resolve_key_candidates(key, label):
    if label in _KEY_CANDIDATES:
        return _KEY_CANDIDATES[label]
    return [key]


def _map_input_char(char):
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


def _step_with_input_service(system, steps, chunk=STEP_SERVICE_CHUNK):
    ran = 0
    while ran < steps:
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        n = chunk
        remain = steps - ran
        if n > remain:
            n = remain
        system.step(n,stop_pc=STOP_PC)
        if system.pc == STOP_PC:
            print(f"system.pc = {system.pc}. log start")
            break
        ran += n
    return ran


def poll_keyboard(system):
    global _active_key, _active_key_label, _active_key_candidates, _active_key_candidate_idx
    global _active_key_started, _active_keybuf_base, _release_at_ms, _release_hard_at_ms, _next_press_at_ms
    global _typed_since_enter

    while _spoll.poll(0):
        char = sys.stdin.read(1)
        if not char:
            break
        if char == "\x03":  # Ctrl+C from Thonny
            raise KeyboardInterrupt()
        if char == "\r" or char == "\n":
            if AUTO_EXE_ON_ENTER and _typed_since_enter:
                _key_queue.append((KEY_EXE, "EXE"))
                _typed_since_enter = False
            continue
        key, label = _map_input_char(char)
        if key is not None:
            _key_queue.append((key, label))
            if key != KEY_EXE:
                _typed_since_enter = True

    now = time.ticks_ms()

    if _active_key is not None:
        st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
        chata = st["chata"] if st else 0x00
        keyin = st["keyin"] if st else 0x80

        if chata != 0x07:
            _active_key_started = True

        should_release = False
        if _active_key_started:
            if hasattr(system, "can_release_active_key"):
                should_release = system.can_release_active_key(_active_keybuf_base)
            else:
                should_release = (chata == 0x20)

        scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))
        if scan_gated and _active_key_started and (not should_release):
            if chata == 0x07 and keyin != 0x80:
                should_release = True

        timed_out = time.ticks_diff(now, _release_at_ms) >= 0
        if scan_gated and not should_release:
            timed_out = time.ticks_diff(now, _release_hard_at_ms) >= 0

        if timed_out and not should_release:
            if _active_key_candidates is not None:
                next_idx = _active_key_candidate_idx + 1
                if next_idx < len(_active_key_candidates):
                    system.release_key(_active_key)
                    _active_key_candidate_idx = next_idx
                    _active_key = _active_key_candidates[_active_key_candidate_idx]
                    print(f"Key Retry: {_active_key_label} -> {_active_key}")
                    system.press_key(_active_key)
                    _active_key_started = False
                    if hasattr(system, "get_key_buffer_state"):
                        _active_keybuf_base = system.get_key_buffer_state()
                    else:
                        _active_keybuf_base = None
                    _release_at_ms = time.ticks_add(now, KEY_HOLD_MS)
                    _release_hard_at_ms = time.ticks_add(now, KEY_RELEASE_HARD_TIMEOUT_MS)
                    return
            should_release = True

        if should_release:
            system.release_key(_active_key)
            _active_key = None
            _active_key_label = None
            _active_key_candidates = None
            _active_key_candidate_idx = 0
            _active_key_started = False
            _active_keybuf_base = None
            _next_press_at_ms = time.ticks_add(now, INTER_KEY_GAP_MS)

    if _active_key is None and _key_queue:
        if time.ticks_diff(now, _next_press_at_ms) < 0:
            return
        if hasattr(system, "is_key_input_enabled") and not system.is_key_input_enabled():
            return

        key, label = _key_queue.pop(0)
        candidates = _resolve_key_candidates(key, label)
        key = candidates[0]
        print(f"Key Press: {label}")
        system.press_key(key)

        _active_key = key
        _active_key_label = label
        _active_key_candidates = candidates
        _active_key_candidate_idx = 0
        _active_key_started = False
        if hasattr(system, "get_key_buffer_state"):
            _active_keybuf_base = system.get_key_buffer_state()
        else:
            _active_keybuf_base = None
        _release_at_ms = time.ticks_add(now, KEY_HOLD_MS)
        _release_hard_at_ms = time.ticks_add(now, KEY_RELEASE_HARD_TIMEOUT_MS)

def _all_trace_execute(system,steps=1000):
    count = 0
    while count < steps:
        system.debug_step(pause=False,trace=True,prt=True,trace_index=count)
        system.print_registers()
        count += 1

def main():
    global STOP_PC
    print("PB-1000 Emulator Starting...")
    STOP_PC = int(input("STOP PC (A652)?> "),16)
    print(f"STOP_PC is {STOP_PC:04X}")
    display = init_display()
    try:
        draw_bezel(display)
    except Exception:
        pass

    system = PB1000System(display, debug={"sys": False, "lcd": False, "kb": False}, restore_registers=False)

    try:
        system.load_rom('/roms/rom0.bin', slot=0)
        system.load_rom('/roms/rom1.bin', slot=1)
    except Exception as e:
        print(f"ROM load warning: {e}")

    system.power_on()

    print(f"System initialized. PC={system.pc:#06x}")
    print("Interactive Mode: Type in REPL to send keys (ESC for MENU).")

    tick_step_accum = 0
    frame_time = time.ticks_ms()

    try:
        while True:
            if hasattr(system, "service_input_lines"):
                system.service_input_lines()

            if not system.is_sleeping:
                ran = _step_with_input_service(system, 4000)
                if system.pc == STOP_PC:
                    _all_trace_execute(system,500)
                tick_step_accum += ran
            else:
                time.sleep_ms(10)

            now = time.ticks_ms()
            poll_keyboard(system)

            if time.ticks_diff(now, frame_time) >= FRAME_INTERVAL_MS:
                system.update_display(x_offset=16, y_offset=40)
                frame_time = now

            while tick_step_accum >= STEP_TIMER_TICK_STEPS:
                system.tick_timer()
                tick_step_accum -= STEP_TIMER_TICK_STEPS

            time.sleep_ms(1)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
    finally:
        print("Saving RAM state...")
        try:
            system.save_ram()
        except Exception as e:
            print(f"Save failed: {e}")


if __name__ == '__main__':
    main()

