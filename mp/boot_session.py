"""
PB-1000 Emulator boot profile selection.
Scans /sd/rams/ for profile directories and presents a selection UI on LCD.
"""
import os
import time

from hdmi_menu_mirror import hdmi_flush

PROFILE_ROOT = "/sd/rams"


def scan_profiles():
    """
    Scan /sd/rams/ for profile directories.
    Returns sorted list of profile names. Returns [] if unavailable.
    """
    try:
        entries = os.listdir(PROFILE_ROOT)
    except OSError:
        return []
    profiles = []
    for name in entries:
        try:
            os.stat(PROFILE_ROOT + "/" + name + "/.")
            profiles.append(name)
        except OSError:
            pass
    return sorted(profiles)


def get_profile_dir(name):
    """Return absolute path for a profile name."""
    return PROFILE_ROOT + "/" + name


def select_profile_ui(display, profiles, default, timeout_ms=30000, sd_mounted=False):
    """
    Show profile selection UI on LCD.
    Up/Down keys navigate, Enter confirms. Auto-selects default after timeout.
    F1 enters the BIOS-style setup menu (mp/setup_menu.py) to edit pb1000.ini
    on flash/SD/per-profile; that menu either machine.reset()s (on save) or
    returns here (on discard), in which case the picker just redraws.
    Returns selected profile name, or None if profiles is empty.

    Always shows the picker (even for a single profile) so there is a chance
    to press F1 before the timeout auto-selects; a single-profile list just
    has one row.
    """
    if not profiles:
        return None

    try:
        sel = profiles.index(default)
    except ValueError:
        sel = 0

    # Scroll offset (index of the first visible row) -- kept in lockstep with
    # `sel` below the same way emulator_menu.py's _run_menu() does, so the
    # highlighted row stays on screen even when there are more profiles than
    # fit vertically (including right here: default_profile could itself be
    # past the first page if it sorts late alphabetically).
    max_vis = _visible_profile_rows(display.height)
    scroll = sel - max_vis + 1 if sel >= max_vis else 0

    _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
    hdmi_flush(display)
    if getattr(display, "flush_if_dirty", None) is not None:
        # HDMI経由の初回フレームは、送信側のCSピンをこの直前に初期化した
        # ばかりの状態(main.pyのHDMIMirrorDisplay構築時)で送ることになる。
        # ここでのGPIO初期化が受信側にごく短いノイズパルスとして誤って
        # ヘッダーの一部と解釈されてしまうと、受信側は次のパケットが来る
        # まで(hdmi_rx_watchdog_cbが自己修復するまで約150ms)ヘッダー待ちの
        # まま止まり、初回フレームが表示されないことがある。その修復時間を
        # 確実に過ぎてから同じ内容を再送することで、キー入力を待たずに
        # 初回から確実に表示されるようにする。
        time.sleep_ms(250)
        _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
        hdmi_flush(display)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

    try:
        import hd61700
        while True:
            remaining = time.ticks_diff(deadline, time.ticks_ms())
            if remaining <= 0:
                print(f"[Boot] Timeout — auto-selected: {profiles[sel]}")
                return profiles[sel]

            sc = hd61700.get_last_key()
            if sc == 0x52:  # Up arrow
                sel = (sel - 1) % len(profiles)
                if sel < scroll:
                    scroll = sel
                elif sel >= scroll + max_vis:        # wrapped to bottom
                    scroll = max(0, sel - max_vis + 1)
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
            elif sc == 0x51:  # Down arrow
                sel = (sel + 1) % len(profiles)
                if sel >= scroll + max_vis:
                    scroll = sel - max_vis + 1
                elif sel < scroll:                   # wrapped to top
                    scroll = 0
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
            elif sc == 0x28:  # Enter
                print(f"[Boot] Selected profile: {profiles[sel]}")
                return profiles[sel]
            elif sc == 0x3A:  # F1 — BIOS-style setup menu
                import sys, gc
                import setup_menu
                setup_menu.run_setup_menu(display, sd_mounted, profiles)
                # Only reached on Discard (Save triggers machine.reset()).
                # Fully unload the module (not just the local name) so its
                # heap is freed for the rest of boot (ROM loading etc. needs
                # large contiguous blocks) — this menu is meant to cost
                # nothing unless F1 is actually used.
                sys.modules.pop('setup_menu', None)
                gc.collect()
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
            elif sc == 0x45:  # F12 — bail out to the REPL without booting
                # Recovery escape hatch: a freshly-flashed main.py could have a
                # bug that hangs later in boot or in the interactive loop,
                # which would otherwise leave no way back to the REPL short of
                # a BOOTSEL + flash_nuke.uf2 wipe (main.py auto-runs on every
                # boot, so a hang there blocks any mpremote reconnect). This
                # screen runs before ROM/RAM loading or CPU startup, so it's
                # the safest point to offer a clean way out. Raises
                # SystemExit (not caught by the `except Exception` below, or
                # by main()'s own try/except — see its docstring) so it
                # unwinds straight out of main() to the REPL, running this
                # function's `finally` block (HDMI clear) on the way.
                if _confirm_exit_to_repl(display):
                    print("[Boot] F12: exiting to REPL without booting.")
                    import sys
                    sys.exit()
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)

            hdmi_flush(display)
            time.sleep_ms(50)
    except Exception as e:
        print(f"[Boot] UI error: {e}, using: {profiles[sel]}")
        return profiles[sel]
    finally:
        # 受信側は種別(ゲーム画面/メニュー/ベゼル)ごとに直前の表示領域を
        # 独立して覚えており、ゲーム画面側のフレームはこの画面(メニューと
        # 同じ種別)の領域を自動ではクリアしない(../../hdmi_bridge_receiver/
        # main.c参照、emulator_menu.pyの同種の修正と同じ理由)。そのため、
        # ここを抜ける(=プロファイル確定)際に明示的に全面黒のフレームを
        # 送り、文字が残ったままにならないようにする。
        if getattr(display, "flush_if_dirty", None) is not None:
            display.fill_rect(0, 0, display.width, display.height, 0x0000)
            hdmi_flush(display)


# ---- rendering helpers -------------------------------------------------------

def _swap16(c):
    """Swap bytes of an RGB565 color value.
    framebuf.RGB565 stores pixels little-endian; the LCD panel (ILI9341/ST7796) expects big-endian.
    """
    return ((c & 0xFF) << 8) | (c >> 8)


def _draw_text(display, x, y, text, fg, bg=0x0000):
    """Draw a text string using the built-in 8x8 framebuf font."""
    W = display.width
    max_chars = (W - x) // 8
    text = text[:max_chars]
    if not text:
        return
    record = getattr(display, 'record_text', None)
    if record is not None:
        # HDMIMirrorDisplay: record a compact text command instead of
        # rasterizing to pixels (see hdmi_menu_mirror.py).
        record(x, y, text, fg, bg)
        return
    import framebuf
    tw = len(text) * 8
    buf = bytearray(tw * 8 * 2)
    fb = framebuf.FrameBuffer(buf, tw, 8, framebuf.RGB565)
    fb.fill(_swap16(bg))
    fb.text(text, 0, 0, _swap16(fg))
    display.set_window(x, y, x + tw - 1, y + 7)
    # Send pixel data in 1024-byte chunks (same as fill_rect) to avoid SPI transfer issues
    mv = memoryview(buf)
    display.dc.value(1)
    display.cs.value(0)
    for i in range(0, len(buf), 1024):
        display.spi.write(mv[i:i + 1024])
    display.cs.value(1)


def _visible_profile_rows(H):
    """How many profile rows fit between the title and footer, for a given
    display height H -- mirrors _draw_profile_ui()'s own row layout exactly
    (20px top margin, 14px row pitch, stops once a row's text would overlap
    the 16px-tall footer) so scroll math in select_profile_ui() always
    agrees with what actually gets drawn."""
    n = 0
    y = 20
    while y + 8 <= H - 16:
        n += 1
        y += 14
    return max(1, n)


def _draw_profile_ui(display, profiles, sel, timeout_ms, scroll=0):
    """Render profile list on LCD using the common ILI9341/ST7796 drawing API (set_window/fill_rect).
    Only profiles[scroll:scroll+visible_rows] are drawn -- select_profile_ui() keeps `scroll`
    tracking `sel` (see its Up/Down handling, mirroring emulator_menu.py's _run_menu()) so the
    highlighted row stays on screen even when there are more profiles than fit vertically."""
    try:
        W = display.width
        H = display.height

        # Clear screen
        display.fill_rect(0, 0, W, H, 0x0000)

        # Title
        _draw_text(display, 4, 4, "Select Profile:", 0xFFFF)

        # Profile list
        row_h = 14
        y = 20
        max_vis = _visible_profile_rows(H)
        vis_end = min(len(profiles), scroll + max_vis)
        for i in range(scroll, vis_end):
            name = profiles[i]
            if i == sel:
                display.fill_rect(0, y - 2, W, row_h, 0x0210)  # dark green highlight
                _draw_text(display, 4, y, "> " + name, 0x07E0, 0x0210)
            else:
                _draw_text(display, 4, y, "  " + name, 0xFFFF, 0x0000)
            y += row_h

        # Footer
        secs = (timeout_ms + 999) // 1000
        _draw_text(display, 4, H - 12, f"UP/DN+ENTER F1:Setup F12:Exit Auto:{secs}s", 0x7BEF)

    except Exception as e:
        print(f"[Boot] Draw error: {e}")


def _confirm_exit_to_repl(display):
    """F12's Yes/No prompt (EXE=yes, BRK=no). Blocking, like setup_menu.py's
    _confirm() -- kept as a separate minimal copy here rather than importing
    setup_menu (which this screen otherwise only loads lazily on F1, see its
    comment about not paying for that module's heap unless actually used)."""
    import hd61700
    W, H = display.width, display.height
    y0 = H // 2 - 14
    display.fill_rect(0, 0, W, H, 0x0000)
    _draw_text(display, 4, y0 + 4, "Exit to REPL without booting?", 0xFFE0)
    _draw_text(display, 4, y0 + 16, "EXE:yes  BRK:no", 0x7BEF)
    hdmi_flush(display)
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x28:   # EXE
                return True
            elif sc == 0x29: # BRK
                return False
        hdmi_flush(display)
        time.sleep_ms(30)
