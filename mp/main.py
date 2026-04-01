"""
PB-1000 Emulator - Normal Run Script
"""
import machine
import sys
import time
import uselect
from pb1000 import PB1000System,init_display,draw_bezel
import force_gc
from pio_uart import PioUart

KEY_HOLD_MS = 120
KEY_RELEASE_HARD_TIMEOUT_MS = 1200
INTER_KEY_GAP_MS = 80
STEP_SERVICE_CHUNK = 64
STEP_TIMER_TICK_STEPS = 40000
FRAME_INTERVAL_MS = 100
AUTO_EXE_ON_ENTER = False
ON_INT_PULSE_MS = 30
WAKE_TRACE_STEPS = 40
WAKE_TRACE_VECTOR_PC = 0x0032
TRACE_AFTER_PC_ENABLED = True
TRACE_AFTER_PC_FROM = 0x0000
TRACE_AFTER_PC_TO   = 0xFFFF

# Input Configuration
ENABLE_USB_KBD = True
ENABLE_UART_KBD = False
USE_C_KEYBOARD = True   # True: C-native keyboard handling (fast), False: Python-side (debug)
UART_BAUDRATE = 115200
UART_TX_PIN = 4   # UART1 TX (Console output)
UART_RX_PIN = 5   # UART1 RX (Keyboard input)

# Initialize UART1 for keyboard input + console output
_uart_kbd = None
_console_uart = None
if ENABLE_UART_KBD:
    try:
        _uart_kbd = machine.UART(1, baudrate=UART_BAUDRATE, tx=machine.Pin(UART_TX_PIN), rx=machine.Pin(UART_RX_PIN), txbuf=2048)
        _console_uart = _uart_kbd  # Same UART for both input and output
        print(f"UART1 Console I/O enabled: GP{UART_TX_PIN}(TX)/GP{UART_RX_PIN}(RX) @ {UART_BAUDRATE}bps")
    except Exception as e:
        print(f"Failed to init UART1 console: {e}")


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
_trace_after_pc_active = False
_trace_after_pc_announced = False
_trace_after_pc_index = 0


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
    global _trace_after_pc_active, _trace_after_pc_announced, _trace_after_pc_index
    ran = 0
    while ran < steps:
        if hasattr(system, "service_pio_uart"):
            system.service_pio_uart()
        if hasattr(system, "service_input_lines") and not USE_C_KEYBOARD:
            system.service_input_lines()

#         if TRACE_AFTER_PC_ENABLED and not _trace_after_pc_active:
#             try:
#                 if TRACE_AFTER_PC_FROM <= system.pc <= TRACE_AFTER_PC_TO:
#                 #if TRACE_AFTER_PC_FROM <= system.pc:
#                     _trace_after_pc_active = True
#             except Exception:
#                 pass
# 
#         if _trace_after_pc_active:
#             if not _trace_after_pc_announced:
#                 print(f"[TRACE_AFTER_PC] start pc={system.pc:04X}")
#                 _trace_after_pc_announced = True
#             _trace_after_pc_index += 1
#             
#                 
#             system.debug_step(
#                 pause=False,
# #                 trace=TRACE_AFTER_PC_FROM <= system.pc <= TRACE_AFTER_PC_TO,
# #                 prt=TRACE_AFTER_PC_FROM <= system.pc <= TRACE_AFTER_PC_TO,
#                 trace=True,
#                 prt=True,
#                 trace_index=_trace_after_pc_index,
#             )
#             if 0xE00B <= system.pc <= 0xE097 or 0xb292 <= system.pc <= 0xb2ab or 0xDCB4 <= system.pc <= 0xDCF2:
#                 print(f"[{system.pc:04X}] registers")
#                 system.print_registers()
#             ran += 1
#             if system.pc == 0xabbd:
#                 _trace_after_pc_active = False
#             continue

        n = chunk
        remain = steps - ran
        if n > remain:
            n = remain
        #if TRACE_AFTER_PC_ENABLED:
            executed = system.step(n)
            if executed is None:
                executed = n
            ran += executed
#             try:
#                 if system.pc == TRACE_AFTER_PC_FROM:
#                     _trace_after_pc_active = True
#             except Exception:
#                 pass
        else:
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
            if hasattr(system, "service_input_lines") and not USE_C_KEYBOARD:
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

    # Poll external USB Host Keyboard if available (Python mode only)
    if not USE_C_KEYBOARD and ENABLE_USB_KBD and hasattr(system, 'keyboard') and hasattr(system.keyboard, 'poll_usb_host'):
        usb_events = system.keyboard.poll_usb_host()
        # If sleeping, check for ESC (BRK) to trigger wake pulse
        if getattr(system, "is_sleeping", False) and usb_events:
            for scancode, pressed in usb_events:
                if scancode == 0x29 and pressed: # 0x29 = ESC
                    _pulse_on_int(system, now)
                    break
        
        # Check for F11 (0x44) to save state
        if usb_events:
            for scancode, pressed in usb_events:
                if scancode == 0x44 and pressed: # 0x44 = F11
                    print("F11 pressed (USB Key): Requesting save...")
                    system._save_requested = True
                    break
                if scancode == 0x42 and pressed: # 0x42 = F9
                    print("F9 pressed (USB Key): Requesting reset...")
                    system.reset_emulator()
                    break

    # Poll REPL / stdin (usually UART0)
    if _spoll.poll(0):
        try:
            chars = sys.stdin.read(1)
            if chars == "\x1b": # Escape sequence?
                # Check for F11: \x1b[23~ or similar
                # For simplicity, if we get ESC, we can also check for a specific command
                # but let's try to detect F11 sequence specifically
                # This is a bit complex for a simple read(1), but we can peek
                next_chars = sys.stdin.read(4) if _spoll.poll(10) else ""
                if "[23~" in next_chars:
                    print("F11 detected via REPL! Saving state...")
                    system._save_requested = True
                    return
                if "[20~" in next_chars:
                    print("F9 detected via REPL! Resetting...")
                    system.reset_emulator()
                    return
                # Map other characters
                for c in next_chars:
                    _key_queue.append(_map_input_char(c))
            else:
                for char in chars:
                    if char == "\x03": # Ctrl+C
                        print("\n[REPL] Break received")
                        _key_queue.append(_map_input_char("^"))
                    elif char in ("\r", "\n"):
                        if AUTO_EXE_ON_ENTER and _typed_since_enter:
                            _key_queue.append((KEY_EXE, "EXE"))
                            _typed_since_enter = False
                    else:
                        key, label = _map_input_char(char)
                        if key is not None:
                            _key_queue.append((key, label))
                            if key != KEY_EXE:
                                _typed_since_enter = True
        except Exception as e:
            print(f"REPL input error: {e}")

    if ENABLE_UART_KBD and _uart_kbd is not None:
        while _uart_kbd.any():
            try:
                char = _uart_kbd.read(1).decode('utf-8')
            except Exception:
                char = None
            if not char:
                break
            if char == "\x03":
                print("\n[UART] Break received")
                char = "^"
            if char == "\r" or char == "\n":
                if AUTO_EXE_ON_ENTER and _typed_since_enter:
                    _key_queue.append((KEY_EXE, "EXE"))
                    _typed_since_enter = False
                continue
            key, label = _map_input_char(char)
            if key is not None:
                if getattr(system, "is_sleeping", False) and label != "BRK":
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
        if hasattr(system, 'set_status'):
            system.set_status(label)

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
            # Apply manual touch calibration offset for Y (and X) drift
            x += getattr(system, 'touch_x_offset', 0)
            y += getattr(system, 'touch_y_offset', 0)

            # Map coords to fit the LCD bezel area (system._disp_x, system._disp_y)
            # The LCD area is 192x32 scaled.
            scale = getattr(system.lcd, 'scale', 1.0)
            lw = int(192 * scale)
            lh = int(32 * scale)
            lx0 = system._disp_x
            ly0 = system._disp_y
            
            if lx0 <= x <= lx0 + lw and ly0 <= y <= ly0 + lh:
                # Relative position within LCD (0.0 to 1.0)
                rx = (x - lx0) / lw
                ry = (y - ly0) / lh
                
                # PB-1000 has 4x4 touch areas (T1-T16)
                col = int(rx * 4)
                row = int(ry * 4)
                
                col = max(0, min(3, col))
                # Y-axis reverse relative to display coordinates
                row = 3 - row
                row = max(0, min(3, row))
                
                t_idx = row * 4 + col + 1
                t_key = f"TK{t_idx}"

                if _active_touch_key != t_key:
                    if _active_touch_key is not None:
                        system.release_key(_active_touch_key)
                    system.press_key(t_key)
                    _active_touch_key = t_key
                return
                
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
        
    # The bezel will be drawn automatically via callback when set_display_scale is called.
    # Initial scale 1.5 will fit the 288x48 LCD.

    print("Initializing PB1000System...")
    system = PB1000System(display, debug={"sys": False, "lcd": False, "kb": False}, restore_registers=True)
    # (PioUart init moved later)
    print("PB1000System initialized.")
    system.touch = touch
    # Attach console UART for PRINT output mirroring
    if _console_uart is not None:
        system.console_uart = _console_uart
    system.lcd.set_display_scale(1.5)
    
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
            
            # Initialize PIO UART on GP6 (TX) and GP13 (RX) for MMIO 0x0C00-0x0C03
            # AFTER usb_host.init() to avoid resource conflict. 
            # Using SM 6, 7 to further reduce conflict risk.
            try:
                pio_uart = PioUart(tx_pin=6, rx_pin=13, baudrate=4800, sm_tx=6, sm_rx=7)
                system.pio_uart = pio_uart
                print("PIO UART (GP6/GP13) initialized on SM 6/7.")
            except Exception as e:
                print(f"Failed to init PIO UART: {e}")
        except Exception as e:
            print(f"Failed to init USB Host: {e}")

    # Activate C keyboard mode if enabled
    if USE_C_KEYBOARD and ENABLE_USB_KBD:
        try:
            import hd61700 as cpu_core
            if hasattr(cpu_core, 'use_c_keyboard'):
                cpu_core.use_c_keyboard(True)
                print("C keyboard mode enabled.")
            if hasattr(cpu_core, 'set_f11_callback'):
                def _on_f11(_):
                    print("F11 pressed (Callback)")
                    system._save_requested = True
                cpu_core.set_f11_callback(_on_f11)
            if hasattr(cpu_core, 'set_f9_callback'):
                def _on_f9(_):
                    print("F9 pressed (Callback)")
                    system.reset_emulator()
                cpu_core.set_f9_callback(_on_f9)
            
            # Synchronize keyboard maps from keymap.py to C core
            import keymap
            if hasattr(cpu_core, 'keyboard_config_adv'):
                cpu_core.keyboard_config_adv(keymap.get_adv_map_list())
                print("C advanced keyboard map synchronized.")
            if hasattr(cpu_core, 'keyboard_config_base'):
                cpu_core.keyboard_config_base(keymap.get_base_map_list())
                print("C base keyboard map synchronized.")

            # MMIO UART: We use polling for TX now to avoid scheduler floods.
            # (No callback registration needed)

        except Exception as e:
            print(f"C keyboard mode init failed: {e}")

    if USE_C_KEYBOARD:
        print("Configuring C keyboard routing...")
        try:
            import usb_host
            if hasattr(usb_host, 'set_c_kb_routing'):
                usb_host.set_c_kb_routing(True)
                print("C keyboard routing enabled.")
            if hasattr(usb_host, 'start_bg_timer'):
                print("Starting USB background timer...")
                usb_host.start_bg_timer(8)
                print("USB background timer started (8ms).")
        except Exception as e:
            print(f"C keyboard routing setup failed: {e}")


    system.power_on()

    print(f"System initialized. PC={system.pc:#06x}")
    print("Interactive Mode: Type in REPL to send keys (ESC for MENU).")

    tick_step_accum = 0
    frame_time = time.ticks_ms()

    try:
        while True:
            if hasattr(system, "service_pio_uart"):
                system.service_pio_uart()
            
            # Service PIO UART (MMIO)
            if system.pio_uart:
                if not hasattr(system, 'uart_xon'):
                    system.uart_xon = True

                # 1. Pull data from C core TX FIFO and send to PIO
                if hasattr(cpu_core, 'uart_tx_get'):
                    for _ in range(32): # Max 32 bytes per cycle
                        tx_data = cpu_core.uart_tx_get()
                        if tx_data is not None:
                            # Intercept XON/XOFF for local flow control
                            if tx_data == 0x13:  # XOFF
                                system.uart_xon = False
                                # print("Local XOFF intercepted")
                            elif tx_data == 0x11: # XON
                                system.uart_xon = True
                                # print("Local XON intercepted")
                            system.pio_uart.write(tx_data)
                        else:
                            break
                            
                system.pio_uart.service_tx()
                system.pio_uart.service_rx()
                
                # 2. Push data from PIO RX to C core FIFO
                if hasattr(cpu_core, 'uart_rx_put'):
                    # Only push data if XON is true (PB-1000 is ready)
                    if system.uart_xon:
                        # Read up to 8 bytes per cycle to match PB-1000 processing speed better
                        for _ in range(8):
                            if not system.pio_uart.any():
                                break
                            data = system.pio_uart.read(1)
                            if data:
                                # DEBUG: Track bytes sent to C core to find the source of spaces
                                # print(f"-> C: {hex(data[0])}")
                                cpu_core.uart_rx_put(data[0])
                            else:
                                break

            if hasattr(system, "service_input_lines") and not USE_C_KEYBOARD:
                system.service_input_lines()

            if not system.is_sleeping:
                ran = _step_with_input_service(system, 4000)
                tick_step_accum += ran
            else:
                time.sleep_ms(10)

            now = time.ticks_ms()
            poll_keyboard(system)
            poll_touch(system)

            # Poll C-side key events for status bar (ISR-safe)
            if USE_C_KEYBOARD:
                try:
                    import hd61700
                    sc = hd61700.get_last_key()
                    if sc >= 0:
                        import keymap
                        system.set_status(keymap.get_label(sc))
                        
                        # PrintScreen key (HID 0x46) detection
                        if sc == 0x46:
                            system.set_status("CAPTURING...")
                            system.update_display(x_offset=16, y_offset=40)
                            try:
                                # 1. Capture LCD Screenshot (PBM format)
                                ts = time.localtime()
                                ts_str = "{:04}{:02}{:02}_{:02}{:02}{:02}".format(*ts[:6])
                                pbm_path = f"screenshot_{ts_str}.pbm"
                                system.lcd.save_pbm(pbm_path)
                                
                                # 2. Dump specific RAM areas (EDTOP ~&H6100, LCDTP ~&H68C8)
                                # We dump 0x6000-0x7FFF as a full VRAM context
                                ram_dump = bytearray(0x2000)
                                import hd61700 as cpu_core
                                for addr in range(0x6000, 0x8000):
                                    ram_dump[addr - 0x6000] = cpu_core.read_mem(addr)
                                
                                with open(f"vram_dump_{ts_str}.bin", "wb") as f:
                                    f.write(ram_dump)
                                
                                system.set_status("CAPTURED!", 2000)
                                print(f"Captured: screenshot_{ts_str}.txt, vram_dump_{ts_str}.bin")
                            except Exception as ex:
                                print(f"Capture failed: {ex}")
                                system.set_status("CAP ERROR!", 2000)
                except Exception:
                    pass

            if getattr(system, '_save_requested', False):
                system._save_requested = False
                system.set_status("SAVING STATE...")
                system.update_display(x_offset=16, y_offset=40)
                if ENABLE_USB_KBD:
                    try:
                        import usb_host
                        usb_host.stop_bg_timer()
                    except Exception:
                        pass
                try:
                    system.save_state()
                    system.set_status("STATE SAVED!", 2000)
                except Exception as e:
                    system.set_status("SAVE ERROR!", 3000)
                    print(f"Save state failed: {e}")
                finally:
                    if ENABLE_USB_KBD:
                        try:
                            import usb_host
                            usb_host.start_bg_timer(8)
                        except Exception:
                            pass

            if time.ticks_diff(now, frame_time) >= FRAME_INTERVAL_MS:
                system.update_display(x_offset=16, y_offset=40)
                frame_time = now

            while tick_step_accum >= STEP_TIMER_TICK_STEPS:
                system.tick_timer()
                tick_step_accum -= STEP_TIMER_TICK_STEPS

            time.sleep_ms(1)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
    except Exception as e:
        print(f"\n*** MAIN LOOP EXCEPTION: {type(e).__name__}: {e}")
        import sys
        sys.print_exception(e)
    finally:
        #for i in range(100):
        #    system.debug_step(pause=False,trace=True,prt=True,trace_index=i+1)
        #    system.print_registers()
        from workarea import peek_workarea,print_workarea,print_all_workarea
        print_all_workarea(system)
        print("dump 0x6000-0x7FFF")
        system.dump_mem_range(0x6000,0x7FFF)
        print("Saving RAM state...")
#         try:
#             system.save_ram()
#         except Exception as e:
#             print(f"Save failed: {e}")


if __name__ == '__main__':
    main()
