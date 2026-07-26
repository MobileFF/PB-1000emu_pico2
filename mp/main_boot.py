from pb1000 import PB1000System, init_display
from pio_uart import PioUart

_usb_host_initialized = False


def init_usb_keyboard_early(*, enable_usb_kbd):
    """Initialize USB host + C keyboard routing before system creation.
    Called before select_profile_ui() so the selection UI accepts keyboard input.
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
        if hasattr(usb_host, 'start_bg_timer'):
            usb_host.start_bg_timer(8)
        print("USB keyboard routing enabled.")
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


def create_system(display_ret, profile_dir=None, config=None, *, console_uart=None):
    """Create PB1000System with the given profile directory and merged config."""
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
    if console_uart is not None:
        system._console_uart_hw = console_uart  # store hw ref; console starts OFF by default
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
        def _rgb332_to_rgb565(c):
            r3=(c>>5)&7; g3=(c>>2)&7; b2=c&3
            return (((r3<<2)|(r3>>1))<<11)|(((g3<<3)|g3)<<5)|((b2<<3)|(b2<<1)|(b2>>1))
        system.lcd.set_colors(_rgb332_to_rgb565(fg_c), _rgb332_to_rgb565(bg_c))
    return system


def create_console_uart(machine, *, enable_uart_kbd, baudrate, tx_pin, rx_pin):
    uart_kbd = None
    console_uart = None
    if enable_uart_kbd:
        try:
            uart_kbd = machine.UART(
                1,
                baudrate=baudrate,
                tx=machine.Pin(tx_pin),
                rx=machine.Pin(rx_pin),
                txbuf=2048,
                # Explicit rxbuf (default is a small fixed size on the rp2
                # port): outer-loop UART polling cadence can lag behind
                # incoming bytes, so give the RX side enough headroom to
                # ride that out instead of silently overflowing.
                rxbuf=2048,
            )
            console_uart = uart_kbd
            print(f"UART1 Console I/O enabled: GP{tx_pin}(TX)/GP{rx_pin}(RX) @ {baudrate}bps")
        except Exception as e:
            print(f"Failed to init UART1 console: {e}")
    return uart_kbd, console_uart


def load_default_roms(system):
    """Load rom0.bin/rom1.bin from /roms/. Returns a list of the paths that
    failed to load (empty list means both loaded successfully)."""
    import gc
    failed = []
    for path, slot in (('/roms/rom0.bin', 0), ('/roms/rom1.bin', 1)):
        try:
            gc.collect()
            if not system.load_rom(path, slot=slot):
                failed.append(path)
        except MemoryError as e:
            print(f"ROM load warning ({path}): {e}")
            failed.append(path)
        except Exception as e:
            print(f"ROM load error ({path}): {e}")
            failed.append(path)
    try:
        if hasattr(system, "boot_virtual_fdd"):
            system.boot_virtual_fdd()
    except Exception as e:
        print(f"VFDD init warning: {e}")
    return failed


def initialize_usb_host_and_pio(system, *, enable_usb_kbd, pio_uart_baudrate=9600):
    if enable_usb_kbd:
        try:
            import usb_host
            if not _usb_host_initialized:
                usb_host.init()
                print("USB Host initialized.")
        except Exception as e:
            print(f"Failed to init USB Host: {e}")

    try:
        pio_uart = PioUart(tx_pin=6, rx_pin=13, baudrate=pio_uart_baudrate, sm_tx=6, sm_rx=7)
        system.pio_uart = pio_uart
        print(f"PIO UART (GP6/GP13) initialized on SM 6/7 @ {pio_uart_baudrate}bps.")
        import gc
        print(f"[MEM] after PIO init: free={gc.mem_free()} alloc={gc.mem_alloc()}")
    except Exception as e:
        print(f"Failed to init PIO UART: {e}")


def configure_c_keyboard(system, *, enable_usb_kbd):
    import gc
    if not enable_usb_kbd:
        return None
    print("[DEBUG boot] configure_c_keyboard: before gc.collect()")
    gc.collect()
    print("[DEBUG boot] configure_c_keyboard: after gc.collect()")
    try:
        import hd61700 as cpu_core
        print("[DEBUG boot] configure_c_keyboard: hd61700 imported")
        print("[DEBUG boot] configure_c_keyboard: before import keymap")
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
        print("[DEBUG boot] configure_c_keyboard: keymap imported")
        if hasattr(cpu_core, 'keyboard_config_adv'):
            adv = keymap.get_adv_map_list()
            print("[DEBUG boot] configure_c_keyboard: got adv map list")
            cpu_core.keyboard_config_adv(adv)
            print("[DEBUG boot] configure_c_keyboard: keyboard_config_adv done")
            del adv
            gc.collect()
            print("C advanced keyboard map synchronized.")
        if hasattr(cpu_core, 'keyboard_config_base'):
            base = keymap.get_base_map_list()
            print("[DEBUG boot] configure_c_keyboard: got base map list")
            cpu_core.keyboard_config_base(base)
            print("[DEBUG boot] configure_c_keyboard: keyboard_config_base done")
            del base
            gc.collect()
            print("C base keyboard map synchronized.")
        return cpu_core
    except Exception as e:
        import sys
        print(f"C keyboard mode init failed: {type(e).__name__}: {e}")
        sys.print_exception(e)
        return None


def configure_usb_keyboard_routing():
    print("Configuring C keyboard routing...")
    try:
        import usb_host
        # Keep the bg timer that was started in init_usb_keyboard_early() running.
        # On Pico 2W, CYW43+BTstack+LwIP consume alarm pool slots; stop+start
        # after NTP causes add_repeating_timer_ms() to fail and kills keyboard input.
        if hasattr(usb_host, 'start_bg_timer'):
            usb_host.start_bg_timer(8)  # no-op if already active
            print("USB background timer active (8ms).")
    except Exception as e:
        print(f"C keyboard routing setup failed: {e}")
