"""
PB-1000 Emulator boot profile selection.
Scans /sd/rams/ for profile directories and presents a selection UI on LCD.
"""
import os
import time

from hdmi_menu_mirror import hdmi_flush
from draw_text import draw_text as _draw_text
from config import load_ini

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


def save_default_profile(name):
    """Persists `name` as [profile] default_profile, so the *next* boot's
    profile picker starts on whatever profile was actually used last time
    instead of a fixed config value. Call once, right after
    select_profile_ui() returns a non-None selection (main.py) -- covers
    every way it can return (explicit Enter, F1 setup-menu round-trip,
    or the auto-select timeout) since they all funnel through that one
    call site.

    Writes to whichever of /pb1000.ini (flash root) / /sd/pb1000.ini
    already has a [profile] default_profile line, matching
    config.py's load_config() priority order (.../sd/pb1000.ini
    overrides flash-root) -- writing to the lower-priority file would
    silently have no effect if the higher one already sets this key.
    Falls back to the flash-root file (always present/writable,
    regardless of SD card state) when neither does yet.

    Deliberately NOT setup_menu.py's _apply_ini_changes() -- that module
    is kept a lazy, popped-after-use import specifically because it's
    large enough that compiling it costs noticeably (see this file's F1
    handler below), and pulling it in unconditionally on every single
    boot just to rewrite one line would defeat that. This is scoped to
    exactly one section/key in exactly one file, and skips the write
    entirely once the stored value already matches (no point wearing
    flash to re-write the same line after every boot where the same
    profile gets picked again)."""
    target = "/sd/pb1000.ini" if "default_profile" in load_ini("/sd/pb1000.ini").get("profile", {}) else "/pb1000.ini"

    # readlines()/writelines() aren't available on MicroPython's file
    # objects (confirmed on real hardware: AttributeError) -- iterate/
    # write line-by-line instead, same as setup_menu.py's own
    # _apply_ini_changes() already does for the same reason.
    lines = []
    try:
        with open(target, "r") as f:
            for line in f:
                lines.append(line)
    except OSError:
        pass

    sec_start = -1
    sec_end = len(lines)
    key_line = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.lower() == "[profile]":
            sec_start = i
        elif sec_start >= 0 and i > sec_start:
            if s.startswith("[") and s.endswith("]"):
                sec_end = i
                break
            if "=" in s and s.split("=", 1)[0].strip().lower() == "default_profile":
                key_line = i

    new_line = "default_profile = %s\n" % name
    if key_line >= 0:
        if lines[key_line] == new_line:
            return  # already up to date -- avoid a no-op flash write
        lines[key_line] = new_line
    elif sec_start >= 0:
        lines.insert(sec_end, new_line)
    else:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append("[profile]\n")
        lines.append(new_line)

    # Write to a temp file and rename over the original, rather than
    # open(target, "w") directly -- opening a file in "w" mode truncates
    # it to empty immediately, before a single byte of the new content is
    # written. 2026-10-16 real-hardware incident: an early version of this
    # function hit an unrelated AttributeError (f.writelines() isn't on
    # MicroPython's file objects) right after that truncation, which left
    # /pb1000.ini permanently empty -- every other section ([display],
    # [keyboard], etc.) was lost before the exception even propagated, not
    # recoverable by later fixing that bug (the damage was already done by
    # the time the fix shipped). os.rename() only replaces `target` once
    # the complete new content is already safely on disk under `tmp_path`,
    # so *any* failure while building/writing it (this specific bug or a
    # future, different one) leaves the original file completely
    # untouched instead of truncated.
    tmp_path = target + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            for line in lines:
                f.write(line)
        os.rename(tmp_path, target)
    except OSError as e:
        print(f"[Boot] failed to save default_profile to {target}: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def select_profile_ui(display, profiles, default, timeout_ms=30000, sd_mounted=False):
    """
    Show profile selection UI on LCD.
    Up/Down keys navigate, Enter confirms. Auto-selects default after timeout.
    F1 enters the BIOS-style setup menu (mp/setup_menu.py) to edit pb1000.ini
    on flash/SD/per-profile; that menu either machine.reset()s (on save) or
    returns here (on discard), in which case the picker just redraws.
    F2 enters PC Link mode (mp/pc_link.py) for SD file transfer with the PC
    tools; the profile list is re-scanned when it returns.
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
                old_sel, old_scroll = sel, scroll
                sel = (sel - 1) % len(profiles)
                if sel < scroll:
                    scroll = sel
                elif sel >= scroll + max_vis:        # wrapped to bottom
                    scroll = max(0, sel - max_vis + 1)
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _show_profile_cursor_move(display, profiles, sel, timeout_ms,
                                          scroll, old_sel, old_scroll)
            elif sc == 0x51:  # Down arrow
                old_sel, old_scroll = sel, scroll
                sel = (sel + 1) % len(profiles)
                if sel >= scroll + max_vis:
                    scroll = sel - max_vis + 1
                elif sel < scroll:                   # wrapped to top
                    scroll = 0
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _show_profile_cursor_move(display, profiles, sel, timeout_ms,
                                          scroll, old_sel, old_scroll)
            elif sc == 0x28:  # Enter
                print(f"[Boot] Selected profile: {profiles[sel]}")
                return profiles[sel]
            elif sc == 0x3A:  # F1 — BIOS-style setup menu
                import sys, gc
                # setup_menu.py has grown large enough that compiling it
                # needs a sizable contiguous block, right at the point in
                # boot (SD/USB host already up, ROM buffer pre-reservation
                # already held -- see main.py) where free heap is most
                # fragmented. Collect first to give the import its best shot
                # at finding that block, and catch MemoryError specifically
                # (not via the outer except Exception below, which would
                # otherwise silently fall through to auto-selecting a
                # profile instead of letting the user retry) so a low-memory
                # moment just reports itself and returns to the picker.
                gc.collect()
                try:
                    import setup_menu
                    setup_menu.run_setup_menu(display, sd_mounted, profiles)
                except MemoryError as e:
                    print(f"[Boot] Setup menu unavailable (low memory): {e}")
                # Only reached on Discard (Save triggers machine.reset()) or
                # on the MemoryError above. Fully unload the module (not
                # just the local name) so its heap is freed for the rest of
                # boot (ROM loading etc. needs large contiguous blocks) —
                # this menu is meant to cost nothing unless F1 is actually
                # used.
                sys.modules.pop('setup_menu', None)
                gc.collect()
                deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
                _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
            elif sc == 0x3B:  # F2 — PC Link (SD file transfer, mp/pc_link.py)
                # Lazy import + unload afterward, same as F1's setup menu
                # above: only pays for the module's heap when actually used.
                import sys, gc
                gc.collect()
                try:
                    import pc_link
                    pc_link.run(display)
                except MemoryError as e:
                    print(f"[Boot] PC Link unavailable (low memory): {e}")
                sys.modules.pop('pc_link', None)
                gc.collect()
                # The PC may have uploaded or deleted profiles: re-scan, and
                # keep the cursor on the same profile if it still exists.
                cur = profiles[sel]
                found = scan_profiles()
                if found:
                    profiles[:] = found
                sel = profiles.index(cur) if cur in profiles else 0
                scroll = sel - max_vis + 1 if sel >= max_vis else 0
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
#
# _draw_text is imported from draw_text.py (see the import at the top of
# this file) rather than defined here. The copy that used to live in this
# file allocated a buffer sized to the full string on every call -- this
# screen redraws on every Up/Down keypress, which is exactly the repeated-
# variable-size-allocation pattern that caused a real MemoryError elsewhere
# in this codebase (see boot_status.py's "Heap safety" history) before
# being replaced with the fixed-size-buffer approach draw_text.py now uses
# everywhere.


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


# Like emulator_menu.py's renderer (2026-10-25), nothing here clears the
# screen before drawing: every function paints each pixel of its own area
# exactly once (text cells carry their own background, the gaps around them
# are filled with the background color), so a redraw replaces the previous
# content in place instead of flashing to black first. _draw_profile_ui()
# covers the whole screen this way -- which also lets it draw straight over
# the setup menu / PC Link / confirmation screens when they return -- and
# cursor moves only repaint the two affected rows.

_ROW_H = 14
_ROW_Y0 = 20   # text line of the first visible row (its band starts 2px above)


def _text_band(display, x, y, text, fg, bg, W):
    """Draw text at (x, y) and fill the rest of its 8px band (0..W) with bg."""
    display.fill_rect(0, y, x, 8, bg)
    _draw_text(display, x, y, text, fg, bg)
    # Same clipping as draw_text(): whole characters up to the display edge.
    end = x + min(len(text), max(0, (W - x) // 8)) * 8
    if end < W:
        display.fill_rect(end, y, W - end, 8, bg)


def _draw_profile_row(display, name, y, is_sel):
    """Paint one row's whole band (y-2 .. y-2+_ROW_H)."""
    W = display.width
    bg = 0x0210 if is_sel else 0x0000   # dark green highlight
    display.fill_rect(0, y - 2, W, 2, bg)
    if is_sel:
        _text_band(display, 4, y, "> " + name, 0x07E0, bg, W)
    else:
        _text_band(display, 4, y, "  " + name, 0xFFFF, bg, W)
    display.fill_rect(0, y + 8, W, _ROW_H - 10, bg)


def _show_profile_cursor_move(display, profiles, sel, timeout_ms, scroll,
                              old_sel, old_scroll):
    """Same scroll position: only the rows losing and gaining the highlight
    change, so repaint just those two; otherwise redraw the whole list."""
    if scroll != old_scroll:
        _draw_profile_ui(display, profiles, sel, timeout_ms, scroll)
        return
    try:
        _draw_profile_row(display, profiles[old_sel],
                          _ROW_Y0 + (old_sel - scroll) * _ROW_H, False)
        _draw_profile_row(display, profiles[sel],
                          _ROW_Y0 + (sel - scroll) * _ROW_H, True)
    except Exception as e:
        print(f"[Boot] Draw error: {e}")


def _draw_profile_ui(display, profiles, sel, timeout_ms, scroll=0):
    """Render profile list on LCD using the common ILI9341/ST7796 drawing API (set_window/fill_rect).
    Only profiles[scroll:scroll+visible_rows] are drawn -- select_profile_ui() keeps `scroll`
    tracking `sel` (see its Up/Down handling, mirroring emulator_menu.py's _run_menu()) so the
    highlighted row stays on screen even when there are more profiles than fit vertically."""
    try:
        W = display.width
        H = display.height

        # Title
        display.fill_rect(0, 0, W, 4, 0x0000)
        _text_band(display, 4, 4, "Select Profile:", 0xFFFF, 0x0000, W)
        display.fill_rect(0, 12, W, _ROW_Y0 - 2 - 12, 0x0000)

        # Profile list
        y = _ROW_Y0
        max_vis = _visible_profile_rows(H)
        vis_end = min(len(profiles), scroll + max_vis)
        for i in range(scroll, vis_end):
            _draw_profile_row(display, profiles[i], y, i == sel)
            y += _ROW_H

        # Blank body below the last row, down to the footer
        foot_y = H - 12
        if y - 2 < foot_y:
            display.fill_rect(0, y - 2, W, foot_y - (y - 2), 0x0000)

        # Footer
        secs = (timeout_ms + 999) // 1000
        _text_band(display, 4, foot_y, f"Enter F1:Setup F2:PC Link F12:Exit {secs}s",
                   0x7BEF, 0x0000, W)
        display.fill_rect(0, foot_y + 8, W, H - (foot_y + 8), 0x0000)

    except Exception as e:
        print(f"[Boot] Draw error: {e}")


def confirm_restore_autosave(display, timeout_ms=10000):
    """RAM Autosave's boot-time prompt (see mp/ram_autosave.py): asks
    whether to restore unsaved progress found in the autosave cache.
    Enter=yes, ESC=no; auto-selects yes (restore) on timeout, matching the
    real PB-1000's battery-backed RAM behaviour (power on -> pick up
    exactly where you left off) more closely than defaulting to no."""
    import hd61700
    W, H = display.width, display.height
    y0 = H // 2 - 14
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    prev_sc = -1
    prev_secs = -1
    while True:
        remaining = time.ticks_diff(deadline, time.ticks_ms())
        if remaining <= 0:
            print("[Boot] Autosave restore prompt timed out -- restoring.")
            return True
        secs = (remaining + 999) // 1000
        if secs != prev_secs:
            prev_secs = secs
            display.fill_rect(0, 0, W, H, 0x0000)
            _draw_text(display, 4, y0 - 10, "Unsaved progress found.", 0xFFE0)
            _draw_text(display, 4, y0 + 2, "Restore it?", 0xFFE0)
            _draw_text(display, 4, y0 + 16, "Enter:yes  ESC:no  Auto:%ds" % secs, 0x7BEF)
            hdmi_flush(display)
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x28:   # Enter
                return True
            elif sc == 0x29:  # ESC
                return False
        time.sleep_ms(30)


def _confirm_exit_to_repl(display):
    """F12's Yes/No prompt (Enter=yes, ESC=no). Blocking, like setup_menu.py's
    _confirm() -- kept as a separate minimal copy here rather than importing
    setup_menu (which this screen otherwise only loads lazily on F1, see its
    comment about not paying for that module's heap unless actually used)."""
    import hd61700
    W, H = display.width, display.height
    y0 = H // 2 - 14
    display.fill_rect(0, 0, W, H, 0x0000)
    _draw_text(display, 4, y0 + 4, "Exit to REPL without booting?", 0xFFE0)
    _draw_text(display, 4, y0 + 16, "Enter:yes  ESC:no", 0x7BEF)
    hdmi_flush(display)
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x28:   # Enter
                return True
            elif sc == 0x29: # ESC
                return False
        hdmi_flush(display)
        time.sleep_ms(30)
