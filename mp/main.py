"""
PB-1000 Emulator - Normal Run ScriptCL
"""
import machine
import hd61700
import gc

import sys
import time
from config import load_config, get_bool, get_int, get_str
from boot_session import scan_profiles, get_profile_dir, select_profile_ui
from main_boot import (
    init_display_only,
    init_usb_keyboard_early,
    create_system,
    configure_c_keyboard,
    configure_usb_keyboard_routing,
    create_console_uart,
    initialize_usb_host_and_pio,
    load_default_roms,
)
from main_input import KeyboardInputManager, TouchInputManager, JoystickInputManager, _parse_joystick_key
from main_runtime import (
    run_cpu_slice,
    service_pio_uart_bridge,
    service_timer_ticks,
    service_timer_realtime,
    update_frame_if_due,
)
from main_actions import handle_key_status_and_capture, handle_save_state_request, handle_disk_swap
from main_cleanup import dump_shutdown_state


def main():
    # Pre-reserve a contiguous ROM-sized block before heap fragmentation.
    # Released just before load_default_roms() so the freed region can
    # absorb the large f.read() allocation (~25KB per ROM file).
    gc.collect()
    _rom_reserve = None
    for _sz in (40960, 36864, 33792, 28672, 24576, 20480, 16384):
        try:
            _rom_reserve = bytearray(_sz)
            break
        except MemoryError:
            pass
    if _rom_reserve is None:
        print("Warning: ROM buffer reservation failed (very low memory)")

    print("PB-1000 Emulator Starting...")

    # Step 1: Display init (no CPU core required)
    display_ret = init_display_only()

    # Step 2: Global config (needed for timeout, default_profile)
    global_cfg = load_config()

    # Step 3: Early USB keyboard init — must precede profile UI so keys are accepted
    init_usb_keyboard_early(enable_usb_kbd=get_bool(global_cfg, "keyboard", "enable_usb_kbd"))

    # Step 4: Profile selection UI
    profiles = scan_profiles()
    default_profile = get_str(global_cfg, "profile", "default_profile")
    ui_timeout_ms = get_int(global_cfg, "profile", "ui_timeout_ms")

    display = display_ret[0] if isinstance(display_ret, tuple) else display_ret
    selected = select_profile_ui(display, profiles, default_profile, ui_timeout_ms)
    profile_dir = get_profile_dir(selected) if selected else None
    print(f"Profile: {selected or '(none)'}")
    display.fill_rect(0, 0, display.width, display.height, 0x0000)

    # Step 5: Merged config (global + profile-specific override)
    cfg = load_config(profile_dir)

    # Step 6: UART init (uses config values)
    enable_uart_kbd = get_bool(cfg, "keyboard", "enable_uart_kbd")
    uart_baudrate   = get_int(cfg, "keyboard", "uart_baudrate")
    uart_tx_pin     = get_int(cfg, "keyboard", "uart_tx_pin")
    uart_rx_pin     = get_int(cfg, "keyboard", "uart_rx_pin")

    _uart_kbd, _console_uart = create_console_uart(
        machine,
        enable_uart_kbd=enable_uart_kbd,
        baudrate=uart_baudrate,
        tx_pin=uart_tx_pin,
        rx_pin=uart_rx_pin,
    )

    # Step 7: Create PB1000System with profile dir and merged config
    system = create_system(
        display_ret,
        profile_dir=profile_dir,
        config=cfg,
        console_uart=_console_uart,
    )

    # Step 8: Input managers
    enable_usb_kbd = get_bool(cfg, "keyboard", "enable_usb_kbd")
    keyboard_input = KeyboardInputManager(
        uart_kbd=_uart_kbd,
        enable_uart_kbd=enable_uart_kbd,
        uart_enter_always_exe=get_bool(cfg, "keyboard", "uart_enter_always_exe"),
        key_hold_ms=get_int(cfg, "keyboard", "key_hold_ms"),
        key_release_hard_timeout_ms=get_int(cfg, "keyboard", "key_release_hard_timeout_ms"),
        inter_key_gap_ms=get_int(cfg, "keyboard", "inter_key_gap_ms"),
        on_int_pulse_ms=30,
    )
    touch_input = TouchInputManager()
    joystick_input = None
    if get_bool(cfg, "joystick", "enable"):
        _joy_key_map = dict(JoystickInputManager.DEFAULT_KEY_MAP)
        for _btn, _cfg_key in (
            ("up",    "key_up"),
            ("down",  "key_down"),
            ("left",  "key_left"),
            ("right", "key_right"),
            ("fire1", "key_fire1"),
            ("fire2", "key_fire2"),
        ):
            _parsed = _parse_joystick_key(get_str(cfg, "joystick", _cfg_key))
            if _parsed is not None:
                _joy_key_map[_btn] = _parsed
        joystick_input = JoystickInputManager(
            debounce_ms=get_int(cfg, "joystick", "debounce_ms"),
            poll_interval_ms=get_int(cfg, "joystick", "poll_interval_ms"),
            enable_fire2=get_bool(cfg, "joystick", "enable_fire2"),
            key_map=_joy_key_map,
        )
        print("Joystick input enabled.")

    # Step 9: Hardware setup
    # Release the pre-reserved buffer to create a contiguous 32KB free region
    # for ROM file loading (each ROM is ~25KB and needs a single contiguous block).
    if _rom_reserve is not None:
        del _rom_reserve
        gc.collect()
    load_default_roms(system)
    gc.collect()
    print("[MEM] after VFDD init: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    print("[MEM] before load_state: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    system.load_state()  # Restore RAM + registers from profile dir (or default path)
    pio_uart_baudrate = get_int(cfg, "pio_uart", "baudrate")
    initialize_usb_host_and_pio(system, enable_usb_kbd=enable_usb_kbd,
                                 pio_uart_baudrate=pio_uart_baudrate)
    cpu_core = configure_c_keyboard(system, enable_usb_kbd=enable_usb_kbd)
    configure_usb_keyboard_routing()
    system.power_on()
    print(f"System initialized. PC={system.pc:#06x}")
    print("Interactive Mode: USB keyboard input enabled.")

    # Step 10: FuncKeyBar (LCKEY..CALC image + touch)
    fkbar = None
    try:
        from funckey_bar import FuncKeyBar
        _fkbar_y = system._disp_y + int(32 * system.lcd.scale) + 24
        fkbar = FuncKeyBar(display, _fkbar_y)
        fkbar.draw()
        print(f"FuncKeyBar drawn at y={_fkbar_y}.")
    except Exception as _e:
        print(f"FuncKeyBar init failed: {_e}")

    # Step 11: Main loop constants from config
    frame_interval_ms     = get_int(cfg, "emulator", "frame_interval_ms")
    active_step_count     = get_int(cfg, "emulator", "active_step_count")
    sleep_poll_ms         = get_int(cfg, "emulator", "sleep_poll_ms")
    step_timer_tick_steps = get_int(cfg, "emulator", "step_timer_tick_steps")
    timer_tick_ms         = get_int(cfg, "emulator", "timer_tick_ms")
    loop_idle_ms          = get_int(cfg, "emulator", "loop_idle_ms")
    step_chunk            = get_int(cfg, "emulator", "step_chunk")

    tick_step_accum = 0
    frame_time = time.ticks_ms()
    last_timer_tick_ms = frame_time
    startup_guard_until = time.ticks_add(frame_time, 1500)
    startup_recovery_done = False
    gui_active_until = 0
    _touch = getattr(system, 'touch', None)

    try:
        while True:
            service_pio_uart_bridge(system, cpu_core)

            if (not startup_recovery_done
                    and system.is_sleeping
                    and time.ticks_diff(startup_guard_until, time.ticks_ms()) >= 0):
                startup_recovery_done = True
                print("Startup sleep detected; forcing cold boot recovery.")
                system.reset_emulator()
                system.power_on(force_reset=True)

            tick_step_accum += run_cpu_slice(
                system,
                active_steps=active_step_count,
                sleep_ms=sleep_poll_ms,
                step_chunk=step_chunk,
            )

            now = time.ticks_ms()
            sc = hd61700.get_last_key()
            if sc == 0xE3 or sc == 0xE7:  # LGUI or RGUI
                gui_active_until = time.ticks_add(now, 500)
            elif sc == 0x29:  # ESC / BREAK
                if time.ticks_diff(gui_active_until, now) > 0:
                    raise KeyboardInterrupt
                elif system.pio_uart is not None:
                    system.pio_uart.flush_rx()
            elif sc == 0x3F:  # F6 → disk swap
                if time.ticks_diff(gui_active_until, now) > 0:
                    gui_active_until = 0
                    handle_disk_swap(system, display, fkbar)
            elif sc == 0x40:  # F7 → emulator menu
                if time.ticks_diff(gui_active_until, now) > 0:
                    gui_active_until = 0
                    from emulator_menu import show_emulator_menu
                    result = show_emulator_menu(system, display, fkbar, joystick_input, cfg)
                    joystick_input = result['joystick_input']
            elif sc == 0x53:  # NumLock → RESET
                system.reset_emulator()
            if getattr(system, '_pio_uart_eof_pending', False):
                system._pio_uart_eof_pending = False
                keyboard_input.enqueue_key((1, 1), "BRK")
                print("[AUTO-BRK] Queuing auto-BREAK after EOF")
            keyboard_input.poll(system)
            if _touch is not None and _touch.is_pressed():
                touch_coords = _touch.get_touch()
                handled_touch = False
                if fkbar is not None:
                    handled_touch = fkbar.poll_coords(system, touch_coords)
                if handled_touch:
                    touch_input.release(system)
                else:
                    if fkbar is not None:
                        fkbar.release(system)
                    touch_input.poll_coords(system, touch_coords)
            else:
                touch_input.release(system)
                if fkbar is not None:
                    fkbar.release(system)
            if joystick_input is not None:
                joystick_input.poll(system)

            handle_key_status_and_capture(system, sc)
            handle_save_state_request(system, enable_usb_kbd=enable_usb_kbd)

            frame_time = update_frame_if_due(
                system,
                now,
                frame_time,
                frame_interval_ms=frame_interval_ms,
            )

            if timer_tick_ms > 0:
                last_timer_tick_ms = service_timer_realtime(
                    system,
                    last_timer_tick_ms,
                    ms_per_tick=timer_tick_ms,
                )
            else:
                tick_step_accum = service_timer_ticks(
                    system,
                    tick_step_accum,
                    timer_tick_steps=step_timer_tick_steps,
                )

            time.sleep_ms(loop_idle_ms)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
    except Exception as e:
        print(f"\n*** MAIN LOOP EXCEPTION: {type(e).__name__}: {e}")
        sys.print_exception(e)
    finally:
        dump_shutdown_state(system)


if __name__ == '__main__':
    main()
