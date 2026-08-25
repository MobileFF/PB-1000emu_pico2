import hd61700
# NOTE: keymap is intentionally NOT imported here at module level.
# It's used only inside handle_key_status_and_capture() below, and this
# file is itself imported at main.py's module load time (before main()
# runs). Importing keymap here would force its module-level
# keymap.json search/load to happen that early too. Deferring to a
# local import means the first real `import keymap` in the whole boot
# sequence happens in main_boot.py's configure_c_keyboard(), right
# where the "before import keymap" debug log is — matching where the
# keymap data is actually first needed.


def handle_disk_swap(system, display, fkbar=None):
    """
    ディスク差し替えハンドラ。emulator_menu.py の "FD Swap" 項目(GUI+F7)から呼ばれる。
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
        system.update_display()
        # 3. FuncKeyBar を再描画（全画面クリアで消えるため）。fkbar は常に実LCD
        #    へ直接描画する(display引数を経由しない)ため、HDMI排他表示中は
        #    実LCDに触れないという方針(pb1000.py参照)に合わせてスキップする。
        if fkbar is not None and not getattr(system, "_hdmi_enabled", False):
            fkbar.draw()
    except Exception:
        pass


def handle_key_status_and_capture(system, sc=-1, mod=0):
    try:
        if sc < 0:
            sc = hd61700.get_last_key()
        if sc < 0:
            return

        import keymap
        system.set_status(keymap.get_label(sc, mod))

        if sc != 0x46:
            return

        system.set_status("CAPTURING...")
        system.update_display()
        try:
            from emulator_menu_capture import _do_vram_save
            msg = _do_vram_save(system)
            import gc; gc.collect()
            print(f"PrtScr: {msg}")
            system.set_status(msg, 2000)
        except Exception as ex:
            import gc; gc.collect()
            print(f"Capture failed: {ex}")
            system.set_status("CAP ERROR!", 2000)
    except Exception:
        pass
