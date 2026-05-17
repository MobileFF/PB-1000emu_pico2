import time


def handle_disk_swap(system, display):
    """
    GUI+F6 で呼ばれるディスク差し替えハンドラ。
    CPU スライスはメインループが UI に入ることで自然に停止する。
    """
    try:
        from disk_select_ui import list_disk_images, select_disk_ui
    except ImportError as e:
        print(f"[DiskSwap] disk_select_ui not available: {e}")
        system.set_status("NO DISK UI", 2000)
        return

    images = list_disk_images(system)
    current = (system.virtual_fdd_config or {}).get("path")

    result = select_disk_ui(display, images, current)

    if result is False:
        # キャンセル — 何もしない
        system.set_status("DISK:CANCEL", 1500)
    elif result is None:
        # イジェクト
        system.swap_disk(None)
        system.set_status("DISK EJECTED", 2000)
    else:
        # 新しいディスクをマウント
        ok = system.swap_disk(result)
        if ok:
            name = result.split("/")[-1]
            system.set_status(f"DISK:{name[:12]}", 2000)
        else:
            system.set_status("DISK ERR!", 3000)

    # 元の LCD 表示に戻す
    try:
        # 1. 全画面を黒でクリア（ディスクUI の残像を消す）
        display.fill_rect(0, 0, display.width, display.height, 0x0000)
        # 2. PB-1000 LCD エリアを強制再描画
        if hasattr(system.lcd, 'mark_dirty'):
            system.lcd.mark_dirty()
        system.update_display(x_offset=16, y_offset=40)
    except Exception:
        pass


def handle_key_status_and_capture(system, sc=-1):
    try:
        import hd61700
        if sc < 0:
            sc = hd61700.get_last_key()
        if sc < 0:
            return

        import keymap
        system.set_status(keymap.get_label(sc))

        if sc == 0x42:
            system.reset_emulator()
            return

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
