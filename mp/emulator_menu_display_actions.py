"""
EMULATOR MENU — Display category action handlers (fg/bg color, LCD height,
HDMI toggle).

Split out of the former monolithic emulator_menu.py so that opening the
menu doesn't have to pay the compile cost of every category's handlers up
front -- each category file is lazily imported only when a specific item
in that category is actually selected. Lazily imported from
emulator_menu.py's _dispatch_display() only when a specific item is
actually selected.

_number_input()/_show_color_preview() and the INI-persistence helpers are
only used by this category (System duplicates its own copies for
step_count/loop_idle -- see emulator_menu_system_actions.py), so they live
here rather than staying in the always-resident engine (emulator_menu.py).
"""
import time

from emulator_menu import (
    _draw_text, _BG, _FG, _HDR, _FTR, _WARN,
    _rgb565_to_rgb332, _rgb332_to_rgb565,
)
from hdmi_menu_mirror import hdmi_flush

_SC_DIGIT = {
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5',
    0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
}


def _number_input(display, title, current=0, min_v=0, max_v=255):
    """Integer input within [min_v, max_v] (default 0-255, unchanged for the
    existing fg/bg color callers). Returns int or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    max_digits = len(str(max_v))
    buf = list(str(max(min_v, min(max_v, current))))
    range_hint = "%d-%d" % (min_v, max_v)

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4,  4, title, _HDR)
        _draw_text(display, 4, 16, "0-9  BS:del  EXE:ok  BRK:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(buf) + "_", _FG)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc in _SC_DIGIT and len(buf) < max_digits:
                buf.append(_SC_DIGIT[sc])
                _redraw()
            elif sc == 0x2A and buf:        # Backspace
                buf.pop()
                _redraw()
            elif sc == 0x28 and buf:        # EXE
                val = int("".join(buf))
                if min_v <= val <= max_v:
                    return val
                display.fill_rect(0, H // 2 - 6, W, 20, _BG)
                _draw_text(display, 4, H // 2 - 4, "!! %s only" % range_hint, _WARN)
            elif sc == 0x29:                # BRK
                return None
        hdmi_flush(display)
        time.sleep_ms(30)


def _show_color_preview(display, title, rgb332):
    """Show color swatch for an RGB332 value and confirm.
    Returns True (apply) or False (cancel)."""
    import hd61700
    W, H = display.width, display.height
    rgb565 = _rgb332_to_rgb565(rgb332)
    sw_w, sw_h = 160, 60
    sw_x = (W - sw_w) // 2
    sw_y = 28

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, title, _HDR)
        display.fill_rect(sw_x, sw_y, sw_w, sw_h, rgb565)
        _draw_text(display, 4, sw_y + sw_h + 6,
                   "RGB332: %d (0x%02X)" % (rgb332, rgb332), _FG)
        _draw_text(display, 4, sw_y + sw_h + 18,
                   "RGB565: %04X" % rgb565, _FTR)
        _draw_text(display, 4, H - 12, "EXE:apply  BRK:cancel", _FTR)

    _redraw()
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


def _update_ini(path, section, kv):
    """Update [section] keys in an INI file, preserving all other content."""
    lines = []
    try:
        with open(path, "r") as f:
            for line in f:
                lines.append(line)
    except OSError:
        pass

    sec_header = "[" + section + "]"
    sec_start = -1
    sec_end = len(lines)
    for i in range(len(lines)):
        stripped = lines[i].strip()
        if stripped.lower() == sec_header.lower():
            sec_start = i
        elif sec_start >= 0 and i > sec_start and stripped.startswith("[") and stripped.endswith("]"):
            sec_end = i
            break

    new_block = [sec_header + "\n"]
    if sec_start >= 0:
        for line in lines[sec_start + 1:sec_end]:
            s = line.strip()
            if not s or s[0] in ("#", ";"):
                new_block.append(line)
                continue
            if "=" in s:
                k = s.split("=", 1)[0].strip().lower()
                if k not in kv:
                    new_block.append(line)
    for k, v in kv.items():
        new_block.append(k + " = " + str(v) + "\n")
    new_block.append("\n")

    if sec_start >= 0:
        out = lines[:sec_start] + new_block + lines[sec_end:]
    else:
        out = lines
        if out and out[-1].strip():
            out.append("\n")
        out += new_block

    with open(path, "w") as f:
        for line in out:
            f.write(line)


def _save_display_colors(fg_color, bg_color):
    """Write display color settings (RGB332) to pb1000.ini. Returns save path or error string."""
    import os
    try:
        os.listdir("/sd")
        path = "/sd/pb1000.ini"
    except OSError:
        path = "/pb1000.ini"
    kv = {"fg_color": str(fg_color), "bg_color": str(bg_color)}
    try:
        _update_ini(path, "display", kv)
        return path
    except Exception as e:
        return "ERR:" + str(e)


def _do_fg_color(system, display):
    cur = _rgb565_to_rgb332(system.lcd._color_fg)
    val = _number_input(display, "Foreground Color (RGB332 0-255)", cur)
    if val is None:
        return ""
    if not _show_color_preview(display, "Foreground Color", val):
        return ""
    system.lcd.set_colors(_rgb332_to_rgb565(val), system.lcd._color_bg_on)
    result = _save_display_colors(val, _rgb565_to_rgb332(system.lcd._color_bg_on))
    if result.startswith("ERR:"):
        return "FG:0x%02X save %s" % (val, result)
    return "FG:0x%02X -> %s" % (val, result)


def _do_bg_color(system, display):
    cur = _rgb565_to_rgb332(system.lcd._color_bg_on)
    val = _number_input(display, "Background Color (RGB332 0-255)", cur)
    if val is None:
        return ""
    if not _show_color_preview(display, "Background Color", val):
        return ""
    system.lcd.set_bg_colors(_rgb332_to_rgb565(val), system.lcd._color_bg_off)
    result = _save_display_colors(_rgb565_to_rgb332(system.lcd._color_fg), val)
    if result.startswith("ERR:"):
        return "BG:0x%02X save %s" % (val, result)
    return "BG:0x%02X -> %s" % (val, result)


def _do_lcd_height(system, fkbar):
    """Toggle 32-dot <-> 64-dot LCD mode at runtime.

    Updates system._lcd_height itself (not just the page count) so the new
    mode survives a later BASIC-program-triggered reset_emulator(), which
    always resets the page count back to system._lcd_height.

    Session-only: does not persist to pb1000.ini (matches Serial Console /
    RS-232C / VDP / Beep / Joystick — only fg/bg color are saved to ini).

    Rows 4-7 will show whatever is currently sitting in VRAM until the
    running program redraws them — this matches how a real PB-1000 behaves
    right after entering 64-dot mode, not a bug in this toggle.
    """
    new_height = 64 if system._lcd_height == 32 else 32
    system._lcd_height = new_height
    system.lcd.set_num_pages(new_height // 8)

    # Re-sync the DOTDS/02BD 64-dot row-4+ fix (see mp/ext/dotds_64dot.py) —
    # its enable state does not otherwise track a live page-count change.
    try:
        import dotds_64dot
        if hasattr(dotds_64dot, 'set_mode'):
            dotds_64dot.set_mode(new_height == 64)
    except ImportError:
        pass

    if fkbar is not None:
        new_fkbar_y = system._disp_y + int(new_height * system.lcd.scale) + 24
        fkbar.set_y_top(new_fkbar_y)

    return "LCD Height: %d dot" % new_height


