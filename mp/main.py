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
ON_INT_PULSE_MS = 30
WAKE_TRACE_STEPS = 40
WAKE_TRACE_VECTOR_PC = 0x0032

# Input Configuration
ENABLE_USB_KBD = True
ENABLE_UART_KBD = True
UART_BAUDRATE = 115200
UART_TX_PIN = 0
UART_RX_PIN = 1

# Initialize explicit UART for keyboard
_uart_kbd = None
if ENABLE_UART_KBD:
    try:
        _uart_kbd = machine.UART(0, baudrate=UART_BAUDRATE, tx=machine.Pin(UART_TX_PIN), rx=machine.Pin(UART_RX_PIN))
        print(f"UART Keyboard enabled: GP{UART_TX_PIN}(TX)/GP{UART_RX_PIN}(RX) @ {UART_BAUDRATE}bps")
    except Exception as e:
        print(f"Failed to init UART keyboard: {e}")


def _wake_diag_snapshot(system):
    snap = {
        "pc": None,
        "flags": None,
        "ia": None,
        "ib": None,
        "ie": None,
        "ua": None,
        "sleep": None,
    }
    try:
        snap["pc"] = system.pc
    except Exception:
        pass
    try:
        if hasattr(system, "is_sleeping"):
            snap["sleep"] = 1 if system.is_sleeping else 0
    except Exception:
        pass
    try:
        import hd61700 as cpu_core
        if hasattr(cpu_core, "get_flags"):
            snap["flags"] = cpu_core.get_flags() & 0xFF
        if hasattr(cpu_core, "get_reg8"):
            snap["ib"] = cpu_core.get_reg8(2) & 0xFF
            snap["ua"] = cpu_core.get_reg8(3) & 0xFF
            snap["ia"] = cpu_core.get_reg8(4) & 0xFF
            snap["ie"] = cpu_core.get_reg8(5) & 0xFF
    except Exception:
        pass
    return snap


def _fmt_wake_diag(snap):
    def hx(v, w):
        return "--" if v is None else f"{v:0{w}X}"
    return (
        f"PC={hx(snap['pc'],4)} F={hx(snap['flags'],2)} "
        f"IA={hx(snap['ia'],2)} IB={hx(snap['ib'],2)} IE={hx(snap['ie'],2)} "
        f"UA={hx(snap['ua'],2)} SLP={snap['sleep'] if snap['sleep'] is not None else '-'}"
    )


def _wake_diag_changed(prev_snap, cur_snap):
    for key in ("pc", "flags", "ia", "ib", "ie", "ua", "sleep"):
        if prev_snap.get(key) != cur_snap.get(key):
            return True
    return False


def _keypos(row, ki_col):
    return (row, ki_col)

KEY_EXE = _keypos(10, 4)
KEY_BREAK = _keypos(1, 1)


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
_on_int_active = False
_on_int_release_at_ms = 0
_input_blocked_log_at_ms = 0


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
        system.step(n)
        ran += n
    return ran


def _pulse_on_int(system, now_ms):
    global _on_int_active, _on_int_release_at_ms
    if _on_int_active:
        return
    # print("[WAKE] BRK detected during sleep: before ON_INT pulse")
    # try:
    #     system.print_registers()
    # except Exception:
    #     pass
    try:
        # print(f"[WAKE_DIAG] before_on_int {_fmt_wake_diag(_wake_diag_snapshot(system))}")
        system.set_on_int(True)
        # print(f"[WAKE_DIAG] after_on_int  {_fmt_wake_diag(_wake_diag_snapshot(system))}")
    except Exception:
        return
    # _trace_wake_path(system, reason="on_int_assert")
    _on_int_active = True
    _on_int_release_at_ms = time.ticks_add(now_ms, ON_INT_PULSE_MS)


def _trace_wake_path(system, reason, steps=WAKE_TRACE_STEPS):
    if steps <= 0:
        return
    try:
        start_pc = system.pc
    except Exception:
        start_pc = 0
    print(f"[WAKE_TRACE] start reason={reason} pc={start_pc:04X} steps={steps}")
    saw_vector = False
    for i in range(steps):
        snap_before = _wake_diag_snapshot(system)
        pc_before = snap_before.get("pc")
        if pc_before == WAKE_TRACE_VECTOR_PC:
            saw_vector = True
        try:
            if hasattr(system, "service_input_lines"):
                system.service_input_lines()
            trace_line = system.debug_step(pause=False, trace=True, prt=True, trace_index=i + 1)
            if isinstance(trace_line, str) and f"[{WAKE_TRACE_VECTOR_PC:04X}]" in trace_line:
                saw_vector = True
        except Exception as e:
            print(f"[WAKE_TRACE] aborted at step {i + 1}: {e}")
            break
        snap_after = _wake_diag_snapshot(system)
        if _wake_diag_changed(snap_before, snap_after):
            print(
                f"[WAKE_TRACE_STATE] step={i + 1:02d} "
                f"pre=({_fmt_wake_diag(snap_before)}) "
                f"post=({_fmt_wake_diag(snap_after)})"
            )
    try:
        end_pc = system.pc
    except Exception:
        end_pc = 0
    if end_pc == WAKE_TRACE_VECTOR_PC:
        saw_vector = True
    print(f"[WAKE_TRACE] end pc={end_pc:04X} saw_vector={1 if saw_vector else 0}")

def poll_keyboard(system):
    global _active_key, _active_key_label, _active_key_candidates, _active_key_candidate_idx
    global _active_key_started, _active_keybuf_base, _release_at_ms, _release_hard_at_ms, _next_press_at_ms
    global _typed_since_enter, _on_int_active, _on_int_release_at_ms, _input_blocked_log_at_ms

    now = time.ticks_ms()
    if _on_int_active and time.ticks_diff(now, _on_int_release_at_ms) >= 0:
        try:
            # print(f"[WAKE_DIAG] before_on_int_release {_fmt_wake_diag(_wake_diag_snapshot(system))}")
            system.set_on_int(False)
            # print(f"[WAKE_DIAG] after_on_int_release  {_fmt_wake_diag(_wake_diag_snapshot(system))}")
            # print("[WAKE] ON_INT pulse released: after pulse")
            # try:
            #     system.print_registers()
            # except Exception:
            #     pass
        finally:
            _on_int_active = False

    # Poll external USB Host Keyboard if available
    if ENABLE_USB_KBD and hasattr(system, 'keyboard') and hasattr(system.keyboard, 'poll_usb_host'):
        usb_events = system.keyboard.poll_usb_host()
        # If sleeping, check for ESC (BRK) to trigger wake pulse
        if getattr(system, "is_sleeping", False) and usb_events:
            for scancode, pressed in usb_events:
                if scancode == 0x29 and pressed: # 0x29 = ESC
                    _pulse_on_int(system, now)
                    break

    if ENABLE_UART_KBD and _uart_kbd is not None:
        while _uart_kbd.any():
            try:
                char = _uart_kbd.read(1).decode('utf-8')
            except Exception:
                char = None
            if not char:
                break
            if char == "\x03":  # Ctrl+C behavior? Usually handled by REPL, but we can intercept
                print("\n[UART] Break received")
                # We could raise KeyboardInterrupt, but let's just map it to PB-1000 BRK
                char = "^" 
            if char == "\r" or char == "\n":
                if AUTO_EXE_ON_ENTER and _typed_since_enter:
                    _key_queue.append((KEY_EXE, "EXE"))
                    _typed_since_enter = False
                continue
            key, label = _map_input_char(char)
            if key is not None:
                if getattr(system, "is_sleeping", False) and label != "BRK":
                    print(f"[QUEUE] dropped during sleep: {label}")
                    continue
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
        if getattr(system, "is_sleeping", False):
            dropped = 0
            kept = []
            for item in _key_queue:
                if item[1] == "BRK":
                    kept.append(item)
                else:
                    dropped += 1
            if dropped:
                print(f"[QUEUE] dropped {dropped} non-BRK keys during sleep")
            _key_queue[:] = kept
            if not _key_queue:
                return
        pending_key, pending_label = _key_queue[0]
        allow_sleep_break = (pending_label == "BRK" and getattr(system, "is_sleeping", False))
        if (not allow_sleep_break and hasattr(system, "is_key_input_enabled")
                and not system.is_key_input_enabled()):
            # Throttled diagnostic for "typed but not accepted" cases.
            if time.ticks_diff(now, _input_blocked_log_at_ms) >= 0:
                sleep_state = 1 if getattr(system, "is_sleeping", False) else 0
                print(
                    f"[INPUT_BLOCKED] label={pending_label} "
                    f"sleep={sleep_state} key_enabled=0 queue_len={len(_key_queue)}"
                )
                _input_blocked_log_at_ms = time.ticks_add(now, 500)
            return

        key, label = _key_queue.pop(0)
        # In OFF/sleep state, map BREAK key press to ON_INT wake pulse.
        if label == "BRK" and getattr(system, "is_sleeping", False):
            _pulse_on_int(system, now)
            return
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


_active_touch_key = None

def poll_touch(system):
    global _active_touch_key
    if not hasattr(system, 'touch') or system.touch is None:
        return
        
    if system.touch.is_pressed():
        coords = system.touch.get_touch()
        if coords is not None:
            x, y = coords
            # print(f"[DEBUG_TOUCH] x={x}, y={y}")
            # Map coords to 4x4 grid within display bounds (x:16-304, y:40-104)
            if 16 <= x <= 304 and 168 <= y <= 200:
                col = (x - 16) // 72
                row = (y - 40) // 16
                # print(f"(col,row)=({col},{row}) -> ",end="")
                col = max(0, min(3, col))
                row = max(0, min(3, row))
                
                t_idx = row * 4 + col + 1
                t_key = f"TK{t_idx}"

                # print(f"(col,row)=({col},{row}) / t_idx = {t_idx}")

                if _active_touch_key != t_key:
                    if _active_touch_key is not None:
                        system.release_key(_active_touch_key)
                    system.press_key(t_key)
                    _active_touch_key = t_key
                    # print(f"[DN] {t_key}")
                return
            else:
                # print(f"[OUT] x={x}, y={y} (Range: 16-304, 40-104)")
                pass
                
    if _active_touch_key is not None:
        system.release_key(_active_touch_key)
        _active_touch_key = None

def main():
    print("PB-1000 Emulator Starting...")

    ret = init_display()
    if isinstance(ret, tuple):
        display, touch = ret
    else:
        display = ret
        touch = None
        
    try:
        draw_bezel(display)
    except Exception:
        pass

    system = PB1000System(display, debug={"sys": False, "lcd": False, "kb": False}, restore_registers=False)
    system.touch = touch
    #system.set_lcd_scale(1.5)
    
    try:
        system.load_rom('/roms/rom0.bin', slot=0)
        system.load_rom('/roms/rom1.bin', slot=1)
    except Exception as e:
        print(f"ROM load warning: {e}")

    if ENABLE_USB_KBD:
        try:
            import usb_host
            usb_host.init()
            print("USB Host initialized.")
        except Exception as e:
            print(f"Failed to init USB Host: {e}")

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
                tick_step_accum += ran
            else:
                time.sleep_ms(10)

            now = time.ticks_ms()
            poll_keyboard(system)
            poll_touch(system)

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
        for i in range(100):
            system.debug_step(pause=False,trace=True,prt=True,trace_index=i+1)
            system.print_registers()
        from workarea import peek_workarea,print_workarea,print_all_workarea
        print_all_workarea(system)
        print("dump 0x6000-0x7FFF")
        system.dump_mem_range(0x6000,0x7FFF)
        print("Saving RAM state...")
        try:
            system.save_ram()
        except Exception as e:
            print(f"Save failed: {e}")


if __name__ == '__main__':
    main()
