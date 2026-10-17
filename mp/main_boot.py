# init_display() lives in display_init.py (not pb1000.py) specifically so
# it can be imported here, at module level, without dragging in PB1000System
# and everything pb1000.py needs for it (~2000 lines) -- that class isn't
# actually needed until create_system() below, which runs well after the
# profile picker (and its F1 setup menu) have already had their chance to
# run on the least-fragmented heap of the whole boot sequence. See
# display_init.py's module docstring. `from pb1000 import PB1000System` is
# therefore deferred to inside create_system() itself, and PioUart's import
# deferred similarly to inside initialize_usb_host_and_pio() -- see
# main.py's own note about main_input/main_runtime/etc. for the same
# reasoning applied at the main.py level.
from display_init import init_display

_usb_host_initialized = False


def init_usb_keyboard_early(*, enable_usb_kbd, poll_interval_ms=8):
    """Initialize USB host + C keyboard routing before system creation.
    Called before select_profile_ui() so the selection UI accepts keyboard input.

    poll_interval_ms: how often usb_host_core.c's background hardware timer
    calls tuh_task() to service the USB HID keyboard (see `[keyboard]
    poll_interval_ms` in pb1000.ini). Lower = more responsive key input, at
    the cost of more frequent USB host servicing; the TinyUSB/pico-pio-usb
    stack itself is the practical lower bound, not any check here.
    """
    global _usb_host_initialized
    if not enable_usb_kbd:
        return
    try:
        import usb_host
        usb_host.init()
        _usb_host_initialized = True
        print("USB Host initialized (early).")
    except Exception as e:
        print(f"USB Host early init failed: {e}")
        return
    try:
        import usb_host
        usb_host.start_bg_timer(poll_interval_ms)
        print(f"USB keyboard routing enabled (poll={poll_interval_ms}ms).")
    except Exception as e:
        print(f"USB keyboard routing failed: {e}")


def init_display_only():
    """Initialize display only, without creating PB1000System.
    Returns the raw value from init_display() — (display, touch, ...) or display.
    """
    ret = init_display()
    display = ret[0] if isinstance(ret, tuple) else ret
    if hasattr(display, "lcd_sync"):
        display.lcd_sync()
    return ret


def _setup_touch_offsets(system, dw, dh, config=None):
    """Apply touch offset settings from config, falling back to built-in defaults.

    Keys may be scoped to one driver with a "ili9341."/"st7796." prefix (e.g.
    st7796.y_offset) so both panels' calibrations can live in the same ini
    at once; an unprefixed key applies to whichever driver is active."""
    touch_cfg = (config or {}).get("touch", {})
    _prefix = "st7796" if dw >= 480 else "ili9341"

    def _gi(key, default):
        for k in (_prefix + "." + key, key):
            if k in touch_cfg:
                try:
                    return int(touch_cfg[k])
                except (ValueError, TypeError):
                    break
        return default

    if dw >= 480:
        system.touch_x_offset         = _gi("x_offset",         8)
        system.touch_y_offset         = _gi("y_offset",         0)
        system.funckey_touch_x_offset = _gi("funckey_x_offset",  2)
        system.funckey_touch_y_offset = _gi("funckey_y_offset", -8)
    else:
        _ty = dh / 240.0
        system.touch_x_offset         = _gi("x_offset",         0)
        system.touch_y_offset         = _gi("y_offset",         round(-10 * _ty))
        system.funckey_touch_x_offset = _gi("funckey_x_offset",  0)
        system.funckey_touch_y_offset = _gi("funckey_y_offset",  round(24  * _ty))


def create_system(display_ret, profile_dir=None, config=None):
    """Create PB1000System with the given profile directory and merged config."""
    from pb1000 import PB1000System
    display = display_ret[0] if isinstance(display_ret, tuple) else display_ret
    touch = display_ret[1] if isinstance(display_ret, tuple) and len(display_ret) >= 2 else None

    print("Initializing PB1000System...")
    system = PB1000System(
        display_ret,
        debug={"sys": False, "lcd": False, "kb": True},
        restore_registers=False,
        profile_dir=profile_dir,
        config=config,
    )
    print("PB1000System initialized.")
    system.touch = touch
    disp_cfg = (config or {}).get("display", {})
    display_obj = display_ret[0] if isinstance(display_ret, tuple) else display_ret
    dw = getattr(display_obj, "width", 320)
    dh = getattr(display_obj, "height", 240)
    default_scale = 2.0 if dw >= 480 else 1.5
    scale = float(disp_cfg.get("scale", str(default_scale)))
    lcd_height = int(disp_cfg.get("lcd_height", "32"))
    if lcd_height not in (32, 64):
        lcd_height = 32
    print(f"LCD height: {lcd_height} dots")
    # Apply LCD height mode to controller
    if hasattr(system.lcd, "set_num_pages"):
        system.lcd.set_num_pages(lcd_height // 8)
    system._lcd_height = lcd_height
    # x/y_offset: explicit INI value, or auto-center on the display
    auto_x = max(0, (dw - int(192 * scale)) // 2)
    # Center the whole group (LCD + gap + fkbar) vertically
    _lcd_h = int(lcd_height * scale)
    _group_h = _lcd_h + 24 + 42  # 24=gap, 42=fkbar height
    auto_y = max(0, (dh - _group_h) // 2)
    disp_x = int(disp_cfg.get("x_offset", str(auto_x)))
    disp_y = int(disp_cfg.get("y_offset", str(auto_y)))
    system._disp_x = max(0, min(disp_x, dw - int(192 * scale)))
    system._disp_y = disp_y
    system.lcd.set_display_scale(scale)
    _setup_touch_offsets(system, dw, dh, config)
    if config:
        from config import get_int as _gi
        fg_c = _gi(config, "display", "fg_color")
        bg_c = _gi(config, "display", "bg_color")
        # Was its own local closure with the exact same RGB332->RGB565
        # logic as emulator_menu.py's/hdmi_menu_mirror.py's (now-removed)
        # duplicates -- see modlcd_controller.c's comment.
        import lcd_c as _lc
        fg565, bg565 = _lc.rgb332_to_rgb565(fg_c), _lc.rgb332_to_rgb565(bg_c)
        system.lcd.set_colors(fg565, bg565)
    return system


def load_default_roms(system):
    """Load rom0.bin/rom1.bin from /roms/ (internal flash), falling back to
    /sd/roms/ (SD card) if the flash copy is missing. 2026-10-17: SD
    fallback added at the user's request, reversing the earlier flash-only
    decision (see [[project_rom_loading_sd_limitation]]) -- the on-screen
    "ROM LOAD FAILED" error text in main_entry.py was updated to match.
    Returns a list of the paths that failed to load (empty list means both
    loaded successfully)."""
    import gc
    failed = []
    # discover_virtual_fdd_config() is called here, once, up front -- not
    # via boot_virtual_fdd() below, which would call it again (same config,
    # same "[VFDD] Config from pb1000.ini: ..." line printed twice, purely
    # cosmetic but avoidable) -- its result is reused directly via
    # activate_pending_virtual_fdd() instead.
    #
    # 2026-10-17: this used to also decide whether load_rom() kept a
    # redundant Python-side copy for VFDD's callback path (keep_copy=...,
    # removed along with pb1000_fdd.py's _ensure_rom_copies() -- see
    # pb1000.py's load_rom() docstring and
    # [[project_rom_loading_sd_limitation]]). No remaining consumer needed
    # that copy, so there's nothing left for this function's result to
    # feed into here besides the activation call below.
    _vfdd_cfg = None
    if hasattr(system, "discover_virtual_fdd_config"):
        try:
            _vfdd_cfg = system.discover_virtual_fdd_config()
        except Exception:
            _vfdd_cfg = None
    for fname, slot in (('rom0.bin', 0), ('rom1.bin', 1)):
        flash_path = '/roms/' + fname
        sd_path = '/sd/roms/' + fname
        try:
            gc.collect()
            ok = system.load_rom(flash_path, slot=slot)
            if not ok:
                print(f"ROM not found on flash ({flash_path}), trying SD ({sd_path})")
                gc.collect()
                ok = system.load_rom(sd_path, slot=slot)
            if not ok:
                failed.append(flash_path)
        except MemoryError as e:
            print(f"ROM load warning ({flash_path}): {e}")
            failed.append(flash_path)
        except Exception as e:
            print(f"ROM load error ({flash_path}): {e}")
            failed.append(flash_path)
    try:
        if _vfdd_cfg is not None and hasattr(system, "activate_pending_virtual_fdd"):
            if _vfdd_cfg.get("enabled"):
                system.activate_pending_virtual_fdd()
        elif hasattr(system, "boot_virtual_fdd"):
            # discover_virtual_fdd_config() wasn't available above (very old
            # firmware) -- fall back to the combined discovery+activation
            # entry point instead.
            system.boot_virtual_fdd()
    except Exception as e:
        print(f"VFDD init warning: {e}")
    return failed


def initialize_usb_host_and_pio(system, *, enable_usb_kbd, pio_uart_baudrate=9600,
                                 pio_uart_enable=True, pio_uart_tx_pin=6, pio_uart_rx_pin=13):
    if enable_usb_kbd:
        try:
            import usb_host
            if not _usb_host_initialized:
                usb_host.init()
                print("USB Host initialized.")
        except Exception as e:
            print(f"Failed to init USB Host: {e}")

    if not pio_uart_enable:
        # RS-232C off ([rs232c] enable=false in pb1000.ini, editable via the
        # F1 setup menu). system.pio_uart stays None -- every caller already
        # guards on that (service_pio_uart_bridge(), main.py's flush_rx()
        # branch, PB1000System.service_pio_uart()), so simply not constructing
        # it here is enough; no other code needs to know it was skipped.
        print("PIO UART (RS-232C) disabled via config.")
        return

    try:
        from pio_uart import PioUart
        pio_uart = PioUart(tx_pin=pio_uart_tx_pin, rx_pin=pio_uart_rx_pin,
                            baudrate=pio_uart_baudrate, sm_tx=6, sm_rx=7)
        system.pio_uart = pio_uart
        print(f"PIO UART (GP{pio_uart_tx_pin}/GP{pio_uart_rx_pin}) initialized on SM 6/7 @ {pio_uart_baudrate}bps.")
        import gc
        print(f"[MEM] after PIO init: free={gc.mem_free()} alloc={gc.mem_alloc()}")
    except Exception as e:
        print(f"Failed to init PIO UART: {e}")


def configure_c_keyboard(system, *, enable_usb_kbd):
    import gc
    if not enable_usb_kbd:
        return None
    gc.collect()
    try:
        import hd61700 as cpu_core
        # NOTE: this is the first real `import keymap` in the boot sequence
        # (main_actions.py now imports it lazily, inside the one function
        # that uses it, specifically so this is where keymap.json actually
        # gets searched for and loaded — not earlier, at main.py's own
        # module-load time). The disable_irq() guard that used to be here
        # was a no-op left over from when this WAS just a cache hit; the
        # real fix for the flash/IRQ timing hazard is stopping the USB
        # background timer earlier, in main.py right after profile
        # selection (see the comment there).
        import keymap
        adv = keymap.get_adv_map_list()
        cpu_core.keyboard_config_adv(adv)
        del adv
        gc.collect()
        print("C advanced keyboard map synchronized.")
        base = keymap.get_base_map_list()
        cpu_core.keyboard_config_base(base)
        del base
        gc.collect()
        print("C base keyboard map synchronized.")
        keymap.release_maps()
        gc.collect()
        # TEMPORARY diagnostic (2026-10-16, bank3-static-reservation heap
        # budget investigation -- see 調査用/heap_budget_bank3.md): this
        # whole stretch (PIO/USB init through FuncKeyBar/ram_autosave) had
        # no [MEM] checkpoints at all, so a real-hardware MemoryError
        # inside it (e.g. FuncKeyBar/ram_autosave both failing on a tight
        # 2-bank profile) couldn't be attributed to any specific step.
        # TO REMOVE once the keymap-loading share of that stretch is
        # understood well enough not to need this any more.
        print("[MEM] after keymap sync: free=%d alloc=%d" %
              (gc.mem_free(), gc.mem_alloc()))
        return cpu_core
    except Exception as e:
        import sys
        print(f"C keyboard mode init failed: {type(e).__name__}: {e}")
        sys.print_exception(e)
        return None


def configure_usb_keyboard_routing(poll_interval_ms=8):
    print("Configuring C keyboard routing...")
    try:
        import usb_host
        # Keep the bg timer that was started in init_usb_keyboard_early() running.
        # On Pico 2W, CYW43+BTstack+LwIP consume alarm pool slots; stop+start
        # after NTP causes add_repeating_timer_ms() to fail and kills keyboard input.
        usb_host.start_bg_timer(poll_interval_ms)  # no-op if already active
        print(f"USB background timer active ({poll_interval_ms}ms).")
    except Exception as e:
        print(f"C keyboard routing setup failed: {e}")
