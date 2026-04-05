import time


def handle_key_status_and_capture(system):
    try:
        import hd61700
        sc = hd61700.get_last_key()
        if sc < 0:
            return

        import keymap
        system.set_status(keymap.get_label(sc))

        if sc != 0x46:
            return

        system.set_status("CAPTURING...")
        system.update_display(x_offset=16, y_offset=40)
        try:
            ts = time.localtime()
            ts_str = "{:04}{:02}{:02}_{:02}{:02}{:02}".format(*ts[:6])

            base_dir = "/sd/screenshots" if system.sd_mounted else "/roms"
            if system.sd_mounted:
                system._ensure_dir(base_dir)

            pbm_path = f"{base_dir}/screenshot_{ts_str}.pbm"
            vram_path = f"{base_dir}/vram_dump_{ts_str}.bin"

            system.lcd.save_pbm(pbm_path)

            ram_dump = bytearray(0x2000)
            import hd61700 as cpu_core
            for addr in range(0x6000, 0x8000):
                ram_dump[addr - 0x6000] = cpu_core.read_mem(addr)

            with open(vram_path, "wb") as f:
                f.write(ram_dump)

            system.set_status("CAPTURED!", 2000)
            print(f"Captured: {pbm_path}, {vram_path}")
        except Exception as ex:
            print(f"Capture failed: {ex}")
            system.set_status("CAP ERROR!", 2000)
    except Exception:
        pass


def handle_save_state_request(system, *, enable_usb_kbd):
    if not getattr(system, '_save_requested', False):
        return

    system._save_requested = False
    system.set_status("SAVING STATE...")
    system.update_display(x_offset=16, y_offset=40)
    if hasattr(system.lcd, 'lcd_sync'):
        system.lcd.lcd_sync()

    if enable_usb_kbd:
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
        if enable_usb_kbd:
            try:
                import usb_host
                usb_host.start_bg_timer(8)
            except Exception:
                pass
