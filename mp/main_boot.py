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
        import hd61700 as _cpu
        if hasattr(_cpu, 'use_c_keyboard'):
            _cpu.use_c_keyboard(True)
    except Exception as e:
        print(f"C keyboard early enable failed: {e}")
    try:
        import usb_host
        if hasattr(usb_host, 'set_c_kb_routing'):
            usb_host.set_c_kb_routing(True)
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


def create_system(display_ret, profile_dir=None, config=None, *, console_uart=None):
    """Create PB1000System with the given profile directory and merged config."""
    display = display_ret[0] if isinstance(display_ret, tuple) else display_ret
    touch = display_ret[1] if isinstance(display_ret, tuple) and len(display_ret) >= 2 else None

    print("Initializing PB1000System...")
    system = PB1000System(
        display_ret,
        debug={"sys": False, "lcd": False, "kb": False},
        restore_registers=False,
        profile_dir=profile_dir,
        config=config,
    )
    print("PB1000System initialized.")
    system.touch = touch
    if console_uart is not None:
        system.console_uart = console_uart
    system.lcd.set_display_scale(1.5)
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
            )
            console_uart = uart_kbd
            print(f"UART1 Console I/O enabled: GP{tx_pin}(TX)/GP{rx_pin}(RX) @ {baudrate}bps")
        except Exception as e:
            print(f"Failed to init UART1 console: {e}")
    return uart_kbd, console_uart


def initialize_system(*, console_uart=None):
    ret = init_display()
    if isinstance(ret, tuple) and len(ret) >= 2:
        display = ret[0]
        touch = ret[1]
    else:
        display = ret
        touch = None
    #display.fill_rect(0, 0, 320, 240, 0xC618)

    if hasattr(display, 'lcd_sync'):
        display.lcd_sync()

    print("Initializing PB1000System...")
    system = PB1000System(ret, debug={"sys": False, "lcd": False, "kb": False}, restore_registers=False)
    print("PB1000System initialized.")
    system.touch = touch
    if console_uart is not None:
        system.console_uart = console_uart
    system.lcd.set_display_scale(1.5)
    return system


def load_default_roms(system):
    try:
        system.load_rom('/roms/rom0.bin', slot=0)
        system.load_rom('/roms/rom1.bin', slot=1)
        if hasattr(system, "boot_virtual_fdd"):
            system.boot_virtual_fdd()
    except Exception as e:
        print(f"ROM load warning: {e}")


def initialize_usb_host_and_pio(system, *, enable_usb_kbd):
    if not enable_usb_kbd:
        return
    try:
        import usb_host
        if not _usb_host_initialized:
            usb_host.init()
            print("USB Host initialized.")
        try:
            pio_uart = PioUart(tx_pin=6, rx_pin=13, baudrate=4800, sm_tx=6, sm_rx=7)
            system.pio_uart = pio_uart
            print("PIO UART (GP6/GP13) initialized on SM 6/7.")
        except Exception as e:
            print(f"Failed to init PIO UART: {e}")
    except Exception as e:
        print(f"Failed to init USB Host: {e}")


def configure_c_keyboard(system, *, enable_usb_kbd):
    if not enable_usb_kbd:
        return None
    try:
        import hd61700 as cpu_core
        if hasattr(cpu_core, 'use_c_keyboard'):
            cpu_core.use_c_keyboard(True)
            print("C keyboard mode enabled.")
        if hasattr(cpu_core, 'set_f11_callback'):
            def _on_f11(_):
                print("F11 pressed (Callback)")
                system._save_requested = True
            # Keep a reference to the handler on the system object to prevent GC
            system._f11_handler = _on_f11
            cpu_core.set_f11_callback(system._f11_handler)
        import keymap
        if hasattr(cpu_core, 'keyboard_config_adv'):
            cpu_core.keyboard_config_adv(keymap.get_adv_map_list())
            print("C advanced keyboard map synchronized.")
        if hasattr(cpu_core, 'keyboard_config_base'):
            cpu_core.keyboard_config_base(keymap.get_base_map_list())
            print("C base keyboard map synchronized.")
        return cpu_core
    except Exception as e:
        print(f"C keyboard mode init failed: {e}")
        return None


def configure_usb_keyboard_routing():
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
