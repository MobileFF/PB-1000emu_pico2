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
# Imported here (top level, before main() runs and before the ROM-buffer
# pre-reservation below eats into contiguous heap) rather than deferred to
# where it's actually used (Step 5) -- unlike setup_menu.py's deliberately
# lazy import (rare, F1-only), boot_status is used on every normal boot, so
# there's no heap-avoidance benefit to deferring it, only cost: compiling it
# mid-boot, right when the profile picker/EXT loader/keymap build are
# already fighting over the tightest contiguous heap of the whole boot
# sequence, has been observed to fail with MemoryError there.
from boot_status import BootStatusOverlay
# Shared text-drawing helper for the whole boot/menu UI family (profile
# picker, setup menu, this file's own ROM-load-error screen, EMULATOR MENU,
# ...) -- see draw_text.py's module docstring. Imported here, first, so
# it's resident before any of those other modules need it; each of them
# just does the same `from draw_text import draw_text as _draw_text` and
# pays no real cost for it beyond a sys.modules lookup.
from draw_text import draw_text as _draw_text
from main_boot import (
    init_display_only,
    init_usb_keyboard_early,
    create_system,
    configure_c_keyboard,
    configure_usb_keyboard_routing,
    initialize_usb_host_and_pio,
    load_default_roms,
)

# The main_input_*.py family / main_runtime / main_actions / main_cleanup
# are NOT imported here at module level -- none of them are needed until
# well after the profile picker (and its F1 setup menu) have already run,
# and together they're substantial. Importing them up front would keep all
# of that compiled bytecode resident through exactly the part of boot with
# the least free contiguous heap (profile picker, setup_menu.py's F1
# compile, then create_system()/[ext] loader -- see boot_status.py's "Heap
# safety" docstring for the same concern applied to that module). Each is
# instead imported right before its first use below: the main_input_*.py
# family at Step 8b, main_runtime/main_actions/main_cleanup right before
# the main loop.


def _show_rom_load_error(display, failed_paths):
    """Full-screen error report for a fatal ROM load failure. Startup stops
    right after this is drawn — see the `return` at the load_default_roms()
    call site in main() — so this is the last thing shown on the LCD."""
    display.fill_rect(0, 0, display.width, display.height, 0x0000)
    _draw_text(display, 4, 4, "ROM LOAD FAILED", 0xF800)
    y = 20
    for path in failed_paths:
        _draw_text(display, 4, y, path, 0xFFE0)
        y += 12
    y += 4
    _draw_text(display, 4, y, "Check /roms/ on the SD card.", 0xFFFF)
    _draw_text(display, 4, y + 12, "Emulator startup halted.", 0xFFFF)
    if hasattr(display, "lcd_sync"):
        display.lcd_sync()


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
    try:
        _bt = hd61700.build_time()
        print(f"Firmware built: {_bt}")
    except Exception as _e:
        print(f"build_time() unavailable: {_e}")

    # Step 1: Display init (no CPU core required)
    display_ret = init_display_only()

    # Step 2: Global config (needed for timeout, default_profile)
    global_cfg = load_config()

    # Step 2a: REPL UART 制御 — boot.py が os.dupterm(uart) で有効にした UART REPL を
    # enable_repl_uart=false の場合に無効化する。USB CDC REPL (slot 0) は維持される。
    if not get_bool(global_cfg, "emulator", "enable_repl_uart"):
        try:
            import uos as _uos
            _uos.dupterm(None, 1)
            print("REPL UART disabled.")
        except Exception as _e:
            print(f"REPL UART disable failed: {_e}")
            sys.print_exception(_e)

    # Step 3: Early USB keyboard init — must precede profile UI so keys are accepted
    init_usb_keyboard_early(
        enable_usb_kbd=get_bool(global_cfg, "keyboard", "enable_usb_kbd"),
        poll_interval_ms=get_int(global_cfg, "keyboard", "poll_interval_ms"),
    )

    # Step 4: Profile selection UI
    profiles = scan_profiles()
    default_profile = get_str(global_cfg, "profile", "default_profile")
    ui_timeout_ms = get_int(global_cfg, "profile", "ui_timeout_ms")

    display = display_ret[0] if isinstance(display_ret, tuple) else display_ret

    # Optional HDMI mirroring for the profile picker (see mp/hdmi_menu_mirror.py
    # and pb1000.py's update_display() for the exclusive LCD/HDMI policy this
    # matches). This runs before create_system()/system.lcd exist, so a
    # throwaway LCDControllerC is constructed just to reach the HDMI bridge —
    # it only needs `display` (auto-discovers SPI/CS/DC from it), not any
    # profile/config state. create_system() constructs the real system.lcd
    # and re-runs init_hdmi_output() afterward regardless (idempotent).
    # Uses global_cfg (not yet profile-specific — no profile is chosen yet).
    profile_display = display
    _early_lcd = None
    if get_bool(global_cfg, "hdmi", "enable"):
        try:
            from lcd_controller_c import LCDControllerC
            _early_lcd = LCDControllerC(display, debug=False)
            _early_lcd.init_hdmi_output(
                get_int(global_cfg, "hdmi", "cs_pin"),
                get_int(global_cfg, "hdmi", "baudrate"),
            )
            # The receiver Pico2 stays powered on independently — if the
            # main unit was just reset, whatever it last showed is still
            # sitting in the receiver's framebuf. Clear it here, as early
            # in boot as HDMI can possibly be reached, before anything else
            # gets drawn (see lcd_send_hdmi_clear_screen()'s docstring for
            # why this is a dedicated packet rather than a big rect sent
            # through the normal text-cmds path).
            _early_lcd.send_hdmi_clear_screen()
            from hdmi_menu_mirror import create as _create_hdmi_mirror
            _mirror = _create_hdmi_mirror(display, _early_lcd)
            if _mirror is not None:
                profile_display = _mirror
        except Exception as _e:
            print(f"Profile picker HDMI mirror setup failed: {_e}")

    sd_mounted = display_ret[2] if isinstance(display_ret, tuple) and len(display_ret) >= 3 else False
    selected = select_profile_ui(profile_display, profiles, default_profile, ui_timeout_ms,
                                  sd_mounted=sd_mounted)
    profile_dir = get_profile_dir(selected) if selected else None
    print(f"Profile: {selected or '(none)'}")
    if _early_lcd is None:
        display.fill_rect(0, 0, display.width, display.height, 0x0000)
    # else: HDMI was the active output for the picker — physical LCD was
    # never touched, matching the exclusive-display policy; nothing to clear.

    # USB keyboard input isn't needed again until the interactive main loop
    # starts — configure_usb_keyboard_routing() (called right after
    # configure_c_keyboard(), below) already restarts the background timer
    # right before that point. Stopping it here means it stays off for the
    # whole automatic setup phase that follows (ROM/RAM/VFDD loading, NTP
    # sync, keymap sync in configure_c_keyboard()) — none of which involves
    # any USB polling — avoiding a rare interrupt/flash timing hazard we've
    # observed during that phase. No-op if the timer was never started
    # (e.g. enable_usb_kbd=false).
    try:
        import usb_host
        usb_host.stop_bg_timer()
    except Exception:
        pass

    # Step 5: Merged config (global + profile-specific override)
    cfg = load_config(profile_dir)

    # Boot status overlay (profile name / clock / mirrored last log line) --
    # only when the physical LCD is actually the active display (HDMI, if
    # enabled, took over the picker screen instead -- see the `_early_lcd`
    # branch above and boot_status.py's BootStatusOverlay docstring for why
    # the two are kept exclusive). Stopped further down, right before the
    # emulator's own screen redraw takes over -- see the matching
    # boot_status.stop() call near system.refresh_lcd_from_ledtp().
    boot_status = None
    if _early_lcd is None:
        try:
            gc.collect()  # maximize contiguous heap before the scratch-buffer allocation below
            boot_status = BootStatusOverlay(
                display, selected,
                show_profile=get_bool(cfg, "overlay", "show_profile_name"),
                show_clock=get_bool(cfg, "overlay", "show_clock"),
                show_log=get_bool(cfg, "overlay", "show_log"),
            )
            boot_status.start()
        except Exception as _e:
            print(f"Boot status overlay setup failed: {_e}")
            boot_status = None

    # Step 7: Create PB1000System with profile dir and merged config
    gc.collect()
    print("[MEM] before create_system (pb1000.py + [ext] loader): free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    system = create_system(
        display_ret,
        profile_dir=profile_dir,
        config=cfg,
    )
    gc.collect()
    print("[MEM] after  create_system (pb1000.py + [ext] loader): free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))

    # Step 8: enable_usb_kbd flag only -- Step 9 below (initialize_usb_host_and_pio(),
    # configure_c_keyboard()) needs this, but the actual input-manager
    # objects (KeyboardInputManager etc.) are constructed later, at Step 8b
    # (right after load_state()), not here -- see that comment for why:
    # building them here required importing the main_input_*.py family
    # (500+ lines combined) right in the middle of create_system()'s [ext]
    # module loader, the
    # single tightest-heap moment of the whole boot sequence, which was
    # observed to fail with MemoryError. Nothing between here and Step 8b
    # actually touches keyboard_input/touch_input/joystick_input/
    # cursor_repeat, so deferring their construction changes nothing else.
    enable_usb_kbd = get_bool(cfg, "keyboard", "enable_usb_kbd")

    # Optional HDMI bridge output (second Pico 2 + PICO-HDMI-PLUS addon).
    # Opt-in, zero impact on users without the addon — see doc/hardware_guide.md
    # §7 and pb1000.ini's [hdmi] section. Must come after create_system()
    # (system.lcd) since it reuses the LCD's already-initialized SPI1 bus.
    # Boot-time only -- there is no runtime EMULATOR MENU toggle for this
    # (removed 2026-08-24; use the F1 setup menu / pb1000.ini and reboot).
    system._hdmi_enabled = get_bool(cfg, "hdmi", "enable")
    system._hdmi_frame_skip = max(1, get_int(cfg, "hdmi", "frame_skip") or 1)
    system._hdmi_cs_pin = get_int(cfg, "hdmi", "cs_pin")
    system._hdmi_baud = get_int(cfg, "hdmi", "baudrate")
    if system._hdmi_enabled:
        system.lcd.init_hdmi_output(system._hdmi_cs_pin, system._hdmi_baud)
        print(f"HDMI bridge output enabled (CS=GP{system._hdmi_cs_pin}, "
              f"{system._hdmi_baud/1e6:.1f}MHz, frame_skip={system._hdmi_frame_skip})")
        # create_system() already called system.lcd.set_display_scale(),
        # which draws the bezel — but that happened before _hdmi_enabled
        # was known above, so it only reached the real LCD. Draw it to
        # HDMI now that HDMI is actually ready; otherwise the bezel would
        # never appear on HDMI until some later event (menu close, LCD
        # height toggle) happened to call force_full_redraw()/
        # _on_lcd_scale_change().
        system._draw_bezel_hdmi(system.lcd.scale)

    # Step 9: Hardware setup
    # Release the pre-reserved buffer to create a contiguous 32KB free region
    # for ROM file loading (each ROM is ~25KB and needs a single contiguous block).
    if _rom_reserve is not None:
        del _rom_reserve
        gc.collect()
    failed_roms = load_default_roms(system)
    if failed_roms:
        print(f"*** ROM load failed: {failed_roms} — halting startup.")
        if boot_status is not None:
            boot_status.stop()  # stop mirroring before the fatal error screen takes over
        _show_rom_load_error(display, failed_roms)
        return
    gc.collect()
    print("[MEM] after VFDD init: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    print("[MEM] before load_state: free=%d alloc=%d  banks=%s" %
          (gc.mem_free(), gc.mem_alloc(),
           ''.join(str(i) for i in range(1, 4) if system.has_bank[i])))
    # restore_cpu_state defaults to True (see load_state() in pb1000.py): boot
    # now does the same full CPU-state resume (PC/UA/registers included) as
    # the emulator menu's "RAM Load", so selecting a profile at the picker
    # resumes exactly where RAM Save left off, with no forced reset.
    #
    # This was previously hardcoded to restore_cpu_state=False specifically
    # here, out of concern that an unattended boot (picker times out with
    # nobody at the keyboard) could resume a bad/inconsistent save with no
    # way to reach the menu to recover -- see git history around 2026-08-13
    # for that reasoning if this needs revisiting. Note the separate
    # "Startup sleep detected" guard a little further below (within the
    # first 1.5s of the main loop) already provides some protection against
    # exactly that scenario regardless of this setting: it force-resets if
    # the CPU comes up stuck sleeping with KEY_INT disabled.
    system.load_state()  # Restore RAM + registers from profile dir (or default path)
    gc.collect()
    print("[MEM] after  load_state: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))

    # [display] vdp_enable=false (F1 setup menu) forces per-pixel color VRAM
    # rendering off, overriding whatever load_state() just decided (VDP
    # defaults to ON in the C core, and load_state() re-enables it whenever a
    # saved color_vram.bin is found) -- this is the config-backed replacement
    # for the EMULATOR MENU's old "Color VRAM (VDP)" runtime toggle (removed
    # 2026-08-22), for programs where the VDP render path itself is
    # undesirable regardless of what got saved.
    if not get_bool(cfg, "display", "vdp_enable") and hasattr(system.lcd, "set_vdp_enable"):
        system.lcd.set_vdp_enable(False)

    # Step 8b: Input managers -- deferred from Step 8 earlier (see the
    # comment there) to here specifically, ahead of the emulator_menu
    # preload / NTP / keymap / FuncKeyBar setup below. Those are all
    # individually try/except-wrapped and degrade gracefully if their
    # MemoryError-prone imports fail; input handling isn't optional the
    # way those are, so it goes first, right after load_state() (as fresh
    # a heap as boot ever gets past this point).
    #
    # Split across main_input_keyboard.py / _touch.py / _joystick.py /
    # _cursor.py (formerly one 535-line main_input.py) with a gc.collect()
    # before each: even after moving the combined import to this earliest
    # position, a single 535-line compile was still observed to fail with
    # MemoryError here on real hardware. Four smaller compiles (the
    # biggest, KeyboardInputManager, is ~270 lines on its own) each have
    # much better odds of finding a large-enough contiguous block than one
    # big one did, even though the total code compiled is the same.
    gc.collect()
    from main_input_keyboard import KeyboardInputManager
    keyboard_input = KeyboardInputManager(
        key_hold_ms=get_int(cfg, "keyboard", "key_hold_ms"),
        key_release_hard_timeout_ms=get_int(cfg, "keyboard", "key_release_hard_timeout_ms"),
        inter_key_gap_ms=get_int(cfg, "keyboard", "inter_key_gap_ms"),
    )

    gc.collect()
    from main_input_touch import TouchInputManager
    touch_input = TouchInputManager()

    gc.collect()
    from main_input_cursor import CursorRepeatManager
    cursor_repeat = CursorRepeatManager()

    joystick_input = None
    if get_bool(cfg, "joystick", "enable"):
        gc.collect()
        from main_input_joystick import _parse_joystick_key, JoystickInputManager
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

    # Step 8c: main loop function imports -- moved here (right after the
    # input managers, still ahead of the emulator_menu preload / NTP /
    # keymap / FuncKeyBar setup below) for the exact same reason as Step
    # 8b: these were previously imported much later (just before the
    # `while True:` loop), by which point emulator_menu/NTP/keymap/
    # FuncKeyBar had all already had a chance to fail (non-fatally, each is
    # try/except-wrapped) and fragment the heap on the way -- real hardware
    # hit an uncaught MemoryError here as a result. Unlike those, nothing
    # here degrades gracefully (the main loop cannot run without
    # run_cpu_slice() etc.), so it needs to go early, not be wrapped.
    gc.collect()
    from main_runtime import (
        run_cpu_slice,
        service_pio_uart_bridge,
        service_timer_ticks,
        service_timer_realtime,
        update_frame_if_due,
    )
    from main_actions import handle_key_status_and_capture
    from main_cleanup import dump_shutdown_state

    # Import emulator_menu now (after the ROM/RAM/VFDD one-time loads are
    # done, instead of lazily on first F7 press). emulator_menu.py only
    # imports `time` at module level (no dependency on `system`/CPU state),
    # so it's safe here. Compiling it needs a sizable contiguous
    # allocation — the category-specific handlers (Storage/Display/System)
    # were split into their own emulator_menu_*.py files (lazily imported
    # only when that category is actually opened) precisely to keep this
    # preload's own contiguous-block requirement down — and doing it lazily
    # at F7 time meant competing with whatever fragmentation has built up by
    # then. NOTE: it must NOT be imported any earlier than this (e.g. before
    # the ROM buffer reservation above) — that was tried and it ate enough
    # contiguous heap to make even the smallest ROM buffer reservation fail,
    # breaking ROM1/VFDD loading.
    # The later `from emulator_menu import show_emulator_menu` in the main
    # loop then just hits sys.modules -- IF this preload succeeded. If it
    # didn't, that later import re-attempts the same compile from inside
    # the main loop instead (see its own try/except there for why that
    # needs one too: an uncaught MemoryError there used to kill the whole
    # emulator session, not just the menu).
    gc.collect()
    print("[MEM] before emulator_menu preload: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    try:
        import emulator_menu
    except Exception as _e:
        print(f"emulator_menu preload failed: {_e}")
    print("[MEM] after  emulator_menu preload: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))

    # WiFi & NTP Synchronization
    print("[MEM] before NTP sync: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    if get_bool(cfg, "ntp", "enable"):
        ssid = get_str(cfg, "wifi", "ssid")
        password = get_str(cfg, "wifi", "password")
        if ssid:
            # Check if WiFi hardware/firmware is supported first to avoid unnecessary display clear
            has_wifi = False
            try:
                import ntp_sync
                has_wifi = ntp_sync.is_wifi_supported()
            except Exception as e:
                print(f"[NTP] Failed to check WiFi support: {e}")
                sys.print_exception(e)

            if has_wifi:
                print(f"[NTP] Sync initiating for SSID: {ssid}")
                try:
                    ntp_server = get_str(cfg, "ntp", "server") or "pool.ntp.org"
                    tz_offset = get_int(cfg, "ntp", "tz_offset_h")
                    timeout_ms = get_int(cfg, "ntp", "timeout_ms") or 15000
                    success = ntp_sync.ntp_sync_and_set(
                        ssid=ssid,
                        password=password,
                        ntp_server=ntp_server,
                        tz_offset_h=tz_offset,
                        timeout_ms=timeout_ms,
                    )
                    if success:
                        print("[NTP] Sync succeeded.")
                    else:
                        print("[NTP] Sync failed.")
                except Exception as e:
                    print(f"[NTP] Error during sync: {e}")
                    sys.print_exception(e)
            else:
                print("[NTP] WiFi hardware not supported on this board. Skipping NTP sync.")
    print("[MEM] after  NTP sync: free=%d alloc=%d" %
          (gc.mem_free(), gc.mem_alloc()))
    pio_uart_baudrate = get_int(cfg, "rs232c", "baudrate")
    pio_uart_enable = get_bool(cfg, "rs232c", "enable")
    pio_uart_tx_pin = get_int(cfg, "rs232c", "tx_pin")
    pio_uart_rx_pin = get_int(cfg, "rs232c", "rx_pin")
    initialize_usb_host_and_pio(system, enable_usb_kbd=enable_usb_kbd,
                                 pio_uart_baudrate=pio_uart_baudrate,
                                 pio_uart_enable=pio_uart_enable,
                                 pio_uart_tx_pin=pio_uart_tx_pin,
                                 pio_uart_rx_pin=pio_uart_rx_pin)
    cpu_core = configure_c_keyboard(system, enable_usb_kbd=enable_usb_kbd)
    configure_usb_keyboard_routing(get_int(cfg, "keyboard", "poll_interval_ms"))

    # KEY_INT pulse interval (see [keyboard] key_pulse_interval_ms in
    # pb1000.ini). Default 25ms; real hardware's Key/Pulse ISR runs every
    # 3.9ms. Lower values shorten how long the ROM's keyboard debounce takes
    # in wall-clock time. Can also be tuned live: hd61700.set_key_pulse_interval_ms(ms).
    if cpu_core is not None and hasattr(cpu_core, 'set_key_pulse_interval_ms'):
        _kb_pulse_ms = get_int(cfg, "keyboard", "key_pulse_interval_ms")
        if _kb_pulse_ms > 0:
            cpu_core.set_key_pulse_interval_ms(_kb_pulse_ms)

    # Debug tracing (see [debug] in pb1000.ini). Config-driven so it can be
    # toggled without a REPL Ctrl-C / mpremote session.
    if cpu_core is not None:
        _dbg_cpu = get_bool(cfg, "debug", "cpu_debug")
        _dbg_key = get_bool(cfg, "debug", "key_debug")
        _dbg_lcd = get_bool(cfg, "debug", "lcd_debug")
        if _dbg_cpu or _dbg_key or _dbg_lcd:
            cpu_core.set_debug(_dbg_cpu or _dbg_key or _dbg_lcd)
            cpu_core.set_key_debug(_dbg_key)
            cpu_core.set_lcd_debug(_dbg_lcd)
            print(f"[DEBUG] cpu={_dbg_cpu} key={_dbg_key} lcd={_dbg_lcd}")

    system.power_on()
    print(f"System initialized. PC={system.pc:#06x} UA={system.ua:#04x}")
    print("Interactive Mode: USB keyboard input enabled.")

    # Now that load_state() resumes mid-program (see the restore_cpu_state
    # comment above) instead of always resetting to PC=0x0000, the resumed
    # program isn't guaranteed to touch VRAM/the LCD control port again
    # right away -- previously, resuming from PC=0x0000 meant the ROM's own
    # boot sequence naturally redrew everything as a side effect of running
    # from the start. The LCD hardware's own pixel VRAM (as opposed to the
    # ROM's LEDTP text-screen buffer in ram0.bin, which load_state() already
    # restored) is never part of the save file at all -- see
    # refresh_lcd_from_ledtp()'s docstring -- so without this, vram would
    # still be sitting exactly as lcd_init() zeroed it, and the physical
    # screen would just stay blank until whatever the resumed program
    # happens to do next touches the display. Rebuild vram from the
    # (correctly-restored) LEDTP first, then push it out immediately.
    if boot_status is not None:
        boot_status.stop()  # startup is done -- stop mirroring prints to the LCD
    system.refresh_lcd_from_ledtp()
    system.force_full_redraw()

    # Step 10: FuncKeyBar (LCKEY..CALC image + touch)
    fkbar = None
    try:
        from funckey_bar import FuncKeyBar
        _fkbar_y = system._disp_y + int(system._lcd_height * system.lcd.scale) + 24
        fkbar = FuncKeyBar(display, _fkbar_y, x_offset=system._disp_x)
        fkbar.draw()
        print(f"FuncKeyBar drawn at x={system._disp_x} y={_fkbar_y}.")
    except Exception as _e:
        print(f"FuncKeyBar init failed: {_e}")
        sys.print_exception(_e)

    # Step 11: Main loop constants from config (run_cpu_slice etc. were
    # already imported at Step 8c, above)
    clock_overlay = None
    if get_bool(cfg, "overlay", "show_clock"):
        try:
            gc.collect()
            from clock_overlay import ClockOverlay
            clock_overlay = ClockOverlay(display)
        except Exception as _e:
            print(f"clock_overlay unavailable: {_e}")
    mem_overlay = None
    if get_bool(cfg, "overlay", "show_mem_free"):
        try:
            gc.collect()
            from mem_overlay import MemOverlay
            mem_overlay = MemOverlay(display)
        except Exception as _e:
            print(f"mem_overlay unavailable: {_e}")
    frame_interval_ms     = get_int(cfg, "emulator", "frame_interval_ms")
    sleep_poll_ms         = get_int(cfg, "emulator", "sleep_poll_ms")
    step_timer_tick_steps = get_int(cfg, "emulator", "step_timer_tick_steps")
    timer_tick_ms         = get_int(cfg, "emulator", "timer_tick_ms")
    step_chunk            = get_int(cfg, "emulator", "step_chunk")

    # active_step_count / loop_idle_ms live on `system` (not plain locals)
    # so the EMULATOR MENU's Speed controls (System > CPU Steps/Slice, Loop
    # Idle) can adjust them at runtime -- the loop below reads them fresh
    # every iteration, so a menu edit takes effect on the very next slice.
    system._active_step_count = get_int(cfg, "emulator", "active_step_count")
    system._loop_idle_ms      = get_int(cfg, "emulator", "loop_idle_ms")

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
                    and not system.is_key_input_enabled()
                    and time.ticks_diff(startup_guard_until, time.ticks_ms()) >= 0):
                startup_recovery_done = True
                print("Startup sleep detected (KEY_INT disabled); forcing cold boot recovery.")
                system.reset_emulator()
                system.power_on(force_reset=True)
                system.force_full_redraw()

            tick_step_accum += run_cpu_slice(
                system,
                active_steps=system._active_step_count,
                sleep_ms=sleep_poll_ms,
                step_chunk=step_chunk,
            )

            now = time.ticks_ms()

            if clock_overlay is not None:
                clock_overlay.poll(system, now)

            if mem_overlay is not None:
                mem_overlay.poll(system, now)

            sc = hd61700.get_last_key()
            if sc == 0xE3 or sc == 0xE7:  # LGUI or RGUI
                gui_active_until = time.ticks_add(now, 500)
            elif sc == 0x29:  # ESC / BREAK
                if time.ticks_diff(gui_active_until, now) > 0:
                    raise KeyboardInterrupt
                elif system.pio_uart is not None:
                    system.pio_uart.flush_rx()
            elif sc == 0x40:  # F7 → emulator menu
                if time.ticks_diff(gui_active_until, now) > 0:
                    gui_active_until = 0
                    gc.collect()
                    try:
                        # Normally just a sys.modules hit (see the preload
                        # comment near "import emulator_menu" earlier in
                        # main()) -- but if that preload failed, this is a
                        # full compile attempt from inside the main loop.
                        # MUST be caught here: an uncaught MemoryError would
                        # otherwise propagate out of the `while True:` loop
                        # entirely (see the outer "MAIN LOOP EXCEPTION"
                        # handler below), ending the whole emulator session
                        # just because the menu couldn't open.
                        from emulator_menu import show_emulator_menu
                        result = show_emulator_menu(system, display, fkbar, joystick_input, cfg)
                        joystick_input = result['joystick_input']
                    except MemoryError as _e:
                        print(f"EMULATOR MENU unavailable (low memory): {_e}")
                        gc.collect()
            elif sc == 0x53:  # NumLock → RESET
                system.reset_emulator()
                #system.force_full_redraw()
                #print("[reset] don't call system.reset_emulator() and system.force_full_redraw()")

            if getattr(system, '_pio_uart_eof_pending', False):
                system._pio_uart_eof_pending = False
                keyboard_input.enqueue_key((1, 1), "BRK")
                print("[AUTO-BRK] Queuing auto-BREAK after EOF")
            keyboard_input.poll(system)
            cursor_repeat.poll(system, now)
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

            _sc_mod = 8 if time.ticks_diff(gui_active_until, now) > 0 else 0
            handle_key_status_and_capture(system, sc, _sc_mod)

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

            time.sleep_ms(system._loop_idle_ms)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
    except Exception as e:
        print(f"\n*** MAIN LOOP EXCEPTION: {type(e).__name__}: {e}")
        sys.print_exception(e)
    finally:
        dump_shutdown_state(system)


if __name__ == '__main__':
    main()
