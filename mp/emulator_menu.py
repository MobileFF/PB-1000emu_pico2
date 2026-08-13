"""
PB-1000 Emulator runtime menu.
Triggered from main.py via GUI+F7.

CPU stepping is implicitly paused during menu execution because the main loop
blocks on this function.  All changes take effect immediately at runtime;
persistence (writing back to pb1000.ini) is intentionally out of scope here.

Dangerous operations:
  RAM Load  — restores the full saved CPU state (RAM + PC/registers) and
              resumes execution from there; does not reset to PC=0x0000
  vFDD off  — refused while FDD interface is powered
  RS-232C   — warns but allows toggle while mid-transfer is unlikely
  NEW ALL   — erases all user memory; requires confirmation
"""

import time

# ── Color palette ─────────────────────────────────────────────────────────────
_BG     = 0x0000   # background
_FG     = 0xFFFF   # normal text
_SEL_BG = 0x0210   # selected row background (dark green)
_SEL_FG = 0x07E0   # selected row text (bright green)
_HDR    = 0xFFE0   # header (yellow)
_FTR    = 0x7BEF   # footer (light grey)
_S_ON   = 0x07E0   # status ON (green)
_S_OFF  = 0xF800   # status OFF (red)
_S_NA   = 0x7BEF   # status N/A (grey)
_WARN   = 0xFD20   # warning (orange)
_SEP    = 0x528A   # separator (mid grey)

_ROW_H   = 14
_SEP_ROW_H = 6     # separator row height (13×14 + 3×6 = 200px = available body)
_MAX_VIS = 20      # larger than total items; all fit without scrolling
_HDR_H   = 26
_FTR_H   = 14


# ── Low-level display helper ───────────────────────────────────────────────────

def _sw16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


def _draw_text(display, x, y, text, fg, bg=_BG):
    import framebuf
    text = str(text)
    max_chars = max(0, (display.width - x) // 8)
    text = text[:max_chars]
    if not text:
        return
    buf = bytearray(8 * 8 * 2)  # 128 B: one 8x8 character cell
    fb = framebuf.FrameBuffer(buf, 8, 8, framebuf.RGB565)
    cx = x
    for ch in text:
        fb.fill(_sw16(bg))
        fb.text(ch, 0, 0, _sw16(fg))
        display.set_window(cx, y, cx + 7, y + 7)
        display.write_data(buf)
        cx += 8


# ── Item list builder ──────────────────────────────────────────────────────────

def _badge(on):
    """Return (badge_str, color) for a boolean state."""
    return (" ON", _S_ON) if on else ("OFF", _S_OFF)


def _build_top_items():
    """Top-level menu: one row per category, plus Exit."""
    return [
        {'id': 'cat_toggles', 'label': 'Toggles', 'badge': '>', 'badge_color': _FTR},
        {'id': 'cat_storage', 'label': 'Storage', 'badge': '>', 'badge_color': _FTR},
        {'id': 'cat_display', 'label': 'Display', 'badge': '>', 'badge_color': _FTR},
        {'id': 'cat_system',  'label': 'System',  'badge': '>', 'badge_color': _FTR},
        {'type': 'separator'},
        {'id': 'exit', 'label': 'Exit'},
    ]


def _build_toggle_items(system, state):
    items = []

    # Serial Console
    b, bc = _badge(system.console_uart is not None)
    items.append({'id': 'console',  'label': 'Serial Console', 'badge': b, 'badge_color': bc})

    # RS-232C (PIO UART)
    b, bc = _badge(system.pio_uart is not None)
    items.append({'id': 'rs232',    'label': 'RS-232C (PIO)',  'badge': b, 'badge_color': bc})

    # vFDD
    has_fdd = system.has_virtual_fdd()
    if has_fdd:
        b, bc = _badge(not getattr(system, '_menu_vfdd_disabled', False))
    else:
        b, bc = "N/A", _S_NA
    items.append({'id': 'vfdd',     'label': 'vFDD',           'badge': b, 'badge_color': bc})

    # Beep
    b, bc = _badge(not getattr(system, '_menu_beep_muted', False))
    items.append({'id': 'beep',     'label': 'Beep',           'badge': b, 'badge_color': bc})

    # Joystick
    b, bc = _badge(state['joystick_input'] is not None)
    items.append({'id': 'joystick', 'label': 'Joystick',       'badge': b, 'badge_color': bc})

    # VDP (per-pixel color VRAM)
    b, bc = _badge(getattr(system.lcd, 'vdp_enabled', True))
    items.append({'id': 'vdp',      'label': 'Color VRAM (VDP)', 'badge': b, 'badge_color': bc})

    return items


def _build_storage_items():
    return [
        {'id': 'fd_swap',      'label': 'FD Swap'},
        {'id': 'ram_save',     'label': 'RAM Save'},
        {'id': 'ram_load',     'label': 'RAM Load'},
        {'id': 'vram_save',    'label': 'VRAM Save'},
        {'id': 'full_capture', 'label': 'Full Capture'},
    ]


def _build_display_items(system):
    fg_c = _rgb565_to_rgb332(system.lcd._color_fg)
    bg_c = _rgb565_to_rgb332(system.lcd._color_bg_on)
    return [
        {'id': 'fg_color', 'label': 'Foreground Color', 'badge': "%02X" % fg_c, 'badge_color': _FG},
        {'id': 'bg_color', 'label': 'Background Color', 'badge': "%02X" % bg_c, 'badge_color': _FG},
        {'id': 'lcd_height', 'label': 'LCD Height', 'badge': "%d dot" % system._lcd_height, 'badge_color': _FG},
    ]


def _build_system_items():
    return [
        {'id': 'cpu_status',  'label': 'CPU Status'},
        {'id': 'hook_status', 'label': 'Hook Status'},
        {'id': 'reset',       'label': 'Reset'},
        {'id': 'newall',      'label': 'NEW ALL (clear memory)'},
    ]


# ── Menu renderer ──────────────────────────────────────────────────────────────

def _draw_menu(display, title, hint, items, cursor, scroll, msg=""):
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)

    _draw_text(display, 4,  4, title, _HDR)
    _draw_text(display, 4, 14, hint, _FTR)

    y = _HDR_H
    vis_end = min(len(items), scroll + _MAX_VIS)

    for i in range(scroll, vis_end):
        item = items[i]
        if item.get('type') == 'separator':
            display.fill_rect(0, y + _SEP_ROW_H // 2, W, 1, _SEP)
            y += _SEP_ROW_H
            continue

        is_cur = (i == cursor)
        bg_r = _SEL_BG if is_cur else _BG
        fg_r = _SEL_FG if is_cur else _FG
        display.fill_rect(0, y - 1, W, _ROW_H, bg_r)

        label = item.get('label', '')
        prefix = "> " if is_cur else "  "
        _draw_text(display, 4, y, prefix + label, fg_r, bg_r)

        badge = item.get('badge', '')
        if badge:
            bw = len(badge) * 8
            bx = W - bw - 4
            _draw_text(display, bx, y, badge, item.get('badge_color', fg_r), bg_r)

        y += _ROW_H

    # Footer message
    if msg:
        msg_trunc = msg[: (W - 8) // 8]
        display.fill_rect(0, H - _FTR_H, W, _FTR_H, _BG)
        _draw_text(display, 4, H - _FTR_H + 3, msg_trunc, _WARN)


# USB HID scancode → printable char (a-z, 0-9, hyphen only — for folder names)
_SC_ALPHA = {
    0x04:'a', 0x05:'b', 0x06:'c', 0x07:'d', 0x08:'e', 0x09:'f',
    0x0A:'g', 0x0B:'h', 0x0C:'i', 0x0D:'j', 0x0E:'k', 0x0F:'l',
    0x10:'m', 0x11:'n', 0x12:'o', 0x13:'p', 0x14:'q', 0x15:'r',
    0x16:'s', 0x17:'t', 0x18:'u', 0x19:'v', 0x1A:'w', 0x1B:'x',
    0x1C:'y', 0x1D:'z',
    0x1E:'1', 0x1F:'2', 0x20:'3', 0x21:'4', 0x22:'5',
    0x23:'6', 0x24:'7', 0x25:'8', 0x26:'9', 0x27:'0',
    0x2D:'-',
}

_SC_DIGIT = {
    0x1E:'1', 0x1F:'2', 0x20:'3', 0x21:'4', 0x22:'5',
    0x23:'6', 0x24:'7', 0x25:'8', 0x26:'9', 0x27:'0',
}


def _rgb565_to_rgb332(c):
    """Convert RGB565 to nearest RGB332 (color VRAM format)."""
    r3 = (c >> 13) & 7
    g3 = (c >> 8)  & 7
    b2 = (c >> 3)  & 3
    return (r3 << 5) | (g3 << 2) | b2


def _rgb332_to_rgb565(c):
    """Convert RGB332 (color VRAM format) to RGB565."""
    r3 = (c >> 5) & 7
    g3 = (c >> 2) & 7
    b2 = c & 3
    r5 = (r3 << 2) | (r3 >> 1)
    g6 = (g3 << 3) | g3
    b5 = (b2 << 3) | (b2 << 1) | (b2 >> 1)
    return (r5 << 11) | (g6 << 5) | b5


# ── Text input sub-menu ────────────────────────────────────────────────────────

def _text_input(display, title, max_len=16):
    """One-line text entry via USB HID (a-z, 0-9, hyphen).
    Returns the entered string, or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    text = []

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4,  4, title, _HDR)
        _draw_text(display, 4, 16, "a-z  0-9  -  BS:del", _FTR)
        _draw_text(display, 4, H - 12, "EXE:ok  BRK:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(text) + "_", _FG)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc in _SC_ALPHA and len(text) < max_len:
                text.append(_SC_ALPHA[sc])
                _redraw()
            elif sc == 0x2A and text:   # Backspace
                text.pop()
                _redraw()
            elif sc == 0x28 and text:   # EXE — confirm (reject empty)
                return "".join(text)
            elif sc == 0x29:            # BRK — cancel
                return None
        time.sleep_ms(30)


# ── Numeric input & color preview ─────────────────────────────────────────────

def _number_input(display, title, current=0):
    """Integer input (0-255). Returns int or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    buf = list(str(max(0, min(255, current))))

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
            if sc in _SC_DIGIT and len(buf) < 3:
                buf.append(_SC_DIGIT[sc])
                _redraw()
            elif sc == 0x2A and buf:        # Backspace
                buf.pop()
                _redraw()
            elif sc == 0x28 and buf:        # EXE
                val = int("".join(buf))
                if 0 <= val <= 255:
                    return val
                display.fill_rect(0, H // 2 - 6, W, 20, _BG)
                _draw_text(display, 4, H // 2 - 4, "!! 0-255 only", _WARN)
            elif sc == 0x29:                # BRK
                return None
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
        time.sleep_ms(30)


# ── INI persistence helpers ───────────────────────────────────────────────────

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


# ── Individual action handlers ─────────────────────────────────────────────────

def _do_console(system):
    if system.console_uart is not None:
        system.console_uart = None
        return "Serial Console: OFF"
    hw = getattr(system, '_console_uart_hw', None)
    if hw is not None:
        system.console_uart = hw
        return "Serial Console: ON"
    return "Serial Console: not configured"


def _do_rs232(system):
    try:
        import hd61700
    except ImportError:
        hd61700 = None
    if system.pio_uart is not None:
        system._menu_pio_saved = system.pio_uart
        system.pio_uart = None
        if hd61700 and hasattr(hd61700, 'uart_clear_rx_signal'):
            hd61700.uart_clear_rx_signal()
        return "RS-232C: OFF"
    saved = getattr(system, '_menu_pio_saved', None)
    if saved is not None:
        system.pio_uart = saved
        return "RS-232C: ON"
    return "RS-232C: not configured"


def _do_vfdd(system):
    if not system.has_virtual_fdd():
        return "vFDD: not available"
    if getattr(system, '_virtual_fdd_interface_powered', False):
        return "!! vFDD is active — cannot toggle now"
    if getattr(system, '_menu_vfdd_disabled', False):
        system._menu_vfdd_disabled = False
        return "vFDD: ON (next FDD power cycle)"
    system._menu_vfdd_disabled = True
    try:
        system.virtual_fdd_controller.close()
    except Exception:
        pass
    return "vFDD: OFF"


def _do_beep(system, cfg):
    import hd61700 as cpu_core
    if not system._c_port_active:
        return "Beep: no hardware"

    from config import get_int
    freq = get_int(cfg, "beep", "freq_hz") or 1000
    duty = get_int(cfg, "beep", "duty") or 50

    if getattr(system, '_menu_beep_muted', False):
        # Restore: re-call set_port_direct with real beep pin
        real_pin = getattr(system, '_menu_beep_pin', 14)
        try:
            cpu_core.set_port_direct(6, 13, real_pin, freq, duty)
        except Exception as e:
            return f"Beep restore failed: {e}"
        system._menu_beep_muted = False
        return "Beep: ON"
    else:
        # Mute: save real pin, set to -1
        beep_pin = getattr(system, '_menu_beep_pin', None)
        if beep_pin is None:
            from config import get_int as _gi, get_bool as _gb
            if _gb(cfg, "beep", "enable"):
                beep_pin = _gi(cfg, "beep", "gpio_pin") or 14
            else:
                beep_pin = 14
            system._menu_beep_pin = beep_pin
        try:
            cpu_core.set_port_direct(6, 13, -1, freq, duty)
        except Exception as e:
            return f"Beep mute failed: {e}"
        system._menu_beep_muted = True
        return "Beep: OFF (muted)"


def _do_joystick(state):
    if state['joystick_input'] is not None:
        state['_joy_saved'] = state['joystick_input']
        state['joystick_input'] = None
        return "Joystick: OFF"
    saved = state.get('_joy_saved')
    if saved is not None:
        state['joystick_input'] = saved
        return "Joystick: ON"
    return "Joystick: not configured"


def _confirm(display, title, detail=""):
    """Yes/No confirmation dialog. Returns True (EXE) or False (BRK)."""
    import hd61700
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)
    _draw_text(display, 4,  4, title,  _WARN)
    if detail:
        _draw_text(display, 4, 18, detail, _FG)
    _draw_text(display, 4, H - 10, "EXE:yes  BRK:no", _FTR)
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x28:   # EXE
                return True
            elif sc == 0x29: # BRK
                return False
        time.sleep_ms(30)


def _do_reset(system):
    system.reset_emulator()
    return "Reset executed"


def _do_newall(system, keyboard_input, display):
    """Queue the NEW ALL matrix key (Win+F12 on real HW) after confirmation.

    Must go through the keyboard queue rather than a direct press/release:
    CPU stepping is paused while the menu is open, so the ROM can only see
    the key transition once the main loop resumes and services it.
    """
    import keymap
    entry = keymap.ADV_MAP.get((0x45, 8))
    if entry is None:
        return "!! NEW ALL: not mapped in keymap"
    coord = entry[0][0]
    if not _confirm(display, "NEW ALL: erase all memory?", "This cannot be undone"):
        return ""
    keyboard_input.enqueue_key(coord, 'NEWALL')
    return "NEW ALL queued"


def _do_fd_swap(system, display, fkbar):
    from main_actions import handle_disk_swap
    handle_disk_swap(system, display, fkbar)
    return "FD swap done"


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


# ── Cursor navigation helpers ─────────────────────────────────────────────────

def _next_cursor(items, cur, direction):
    """Move cursor by direction with wraparound, skipping separators."""
    n = len(items)
    for _ in range(n):
        cur = (cur + direction) % n
        if items[cur].get('type') != 'separator':
            return cur
    return cur


# ── Generic menu loop (shared by top level and every category submenu) ───────

def _run_menu(display, title, hint, build_items_fn, dispatch_fn):
    """Run one menu level until it closes.

    build_items_fn() -> item list, called on entry and after every EXE (so
    badges stay current).

    dispatch_fn(item_id, items, cursor) is called on EXE and must return
    (msg, close_all):
      close_all=True  — unwind every nested menu level back to the caller of
                         show_emulator_menu (Exit, Reset, a successful RAM
                         Load or NEW ALL — anything that needs the main loop
                         to resume CPU stepping right away).
      close_all=False — show msg, refresh the item list, keep looping here.

    Returns True if the whole menu system should close, False if BRK was
    pressed and this level should just return to its caller ("back").
    """
    import hd61700

    msg = ""
    items = build_items_fn()
    cursor = _next_cursor(items, -1, 1)
    scroll = 0
    prev_sc = -1

    _draw_menu(display, title, hint, items, cursor, scroll, msg)

    while True:
        sc = hd61700.get_last_key()
        if sc == prev_sc:
            time.sleep_ms(30)
            continue
        prev_sc = sc

        if sc == 0x52:   # UP
            new = _next_cursor(items, cursor, -1)
            if new != cursor:
                cursor = new
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + _MAX_VIS:        # wrapped to bottom
                    scroll = max(0, cursor - _MAX_VIS + 1)
                _draw_menu(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x51: # DOWN
            new = _next_cursor(items, cursor, 1)
            if new != cursor:
                cursor = new
                if cursor >= scroll + _MAX_VIS:
                    scroll = cursor - _MAX_VIS + 1
                elif cursor < scroll:                    # wrapped to top
                    scroll = 0
                _draw_menu(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x28: # EXE — activate item
            item_id = items[cursor].get('id', '')
            msg, close_all = dispatch_fn(item_id, items, cursor)
            if close_all:
                return True
            items = build_items_fn()  # refresh badges
            _draw_menu(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x29: # BREAK — back one level
            return False


# ── Per-category dispatch ─────────────────────────────────────────────────────

def _dispatch_toggles(item_id, system, state, cfg):
    if item_id == 'console':
        return _do_console(system), False
    if item_id == 'rs232':
        return _do_rs232(system), False
    if item_id == 'vfdd':
        return _do_vfdd(system), False
    if item_id == 'beep':
        return _do_beep(system, cfg), False
    if item_id == 'joystick':
        return _do_joystick(state), False
    if item_id == 'vdp':
        new_state = not getattr(system.lcd, 'vdp_enabled', True)
        system.lcd.set_vdp_enable(new_state)
        system.lcd.dirty = True
        return "Color VRAM: " + ("ON" if new_state else "OFF (global color)"), False
    return "", False


def _dispatch_storage(item_id, system, display, fkbar):
    if item_id == 'fd_swap':
        return _do_fd_swap(system, display, fkbar), False
    if item_id == 'ram_save':
        from emulator_menu_ext import _do_ram_save
        return _do_ram_save(system, display), False
    if item_id == 'ram_load':
        from emulator_menu_ext import _do_ram_load
        msg = _do_ram_load(system, display)
        # Load succeeded — exit menu so main loop re-syncs after reset
        return msg, bool(msg) and not msg.startswith("!!")
    if item_id == 'vram_save':
        from emulator_menu_ext import _do_vram_save
        return _do_vram_save(system), False
    if item_id == 'full_capture':
        from emulator_menu_ext import _do_full_capture
        return _do_full_capture(system, display, fkbar), False
    return "", False


def _dispatch_display(item_id, system, display, fkbar):
    if item_id == 'fg_color':
        return _do_fg_color(system, display), False
    if item_id == 'bg_color':
        return _do_bg_color(system, display), False
    if item_id == 'lcd_height':
        # close_all=True: the physical bezel/LCD/FuncKeyBar layout changed
        # size, so unwind to show_emulator_menu()'s tail redraw (full clear +
        # force_full_redraw + fkbar.draw()) rather than trying to patch just
        # the submenu area.
        return _do_lcd_height(system, fkbar), True
    return "", False


def _dispatch_system(item_id, system, display, keyboard_input):
    if item_id == 'hook_status':
        from emulator_menu_ext import _do_hook_status
        _do_hook_status(system, display)
        return "", False
    if item_id == 'cpu_status':
        from emulator_menu_ext import _do_cpu_status
        _do_cpu_status(system, display)
        return "", False
    if item_id == 'reset':
        # main loop must resume stepping from PC=0
        return _do_reset(system), True
    if item_id == 'newall':
        msg = _do_newall(system, keyboard_input, display)
        # Queued — exit menu so main loop resumes CPU stepping and the
        # keyboard manager can press/release the key.
        return msg, bool(msg) and not msg.startswith("!!")
    return "", False


def _dispatch_top(item_id, system, display, fkbar, keyboard_input, cfg, state):
    if item_id == 'exit':
        return "", True
    if item_id == 'cat_toggles':
        closed = _run_menu(
            display, "-- TOGGLES --", "EXE:select  BRK:back",
            lambda: _build_toggle_items(system, state),
            lambda iid, items, cur: _dispatch_toggles(iid, system, state, cfg),
        )
        return "", closed
    if item_id == 'cat_storage':
        closed = _run_menu(
            display, "-- STORAGE --", "EXE:select  BRK:back",
            _build_storage_items,
            lambda iid, items, cur: _dispatch_storage(iid, system, display, fkbar),
        )
        return "", closed
    if item_id == 'cat_display':
        closed = _run_menu(
            display, "-- DISPLAY --", "EXE:select  BRK:back",
            lambda: _build_display_items(system),
            lambda iid, items, cur: _dispatch_display(iid, system, display, fkbar),
        )
        return "", closed
    if item_id == 'cat_system':
        closed = _run_menu(
            display, "-- SYSTEM --", "EXE:select  BRK:back",
            _build_system_items,
            lambda iid, items, cur: _dispatch_system(iid, system, display, keyboard_input),
        )
        return "", closed
    return "", False


# ── Main entry point ─────────────────────────────────────────────────────────

def show_emulator_menu(system, display, fkbar, keyboard_input, joystick_input, cfg):
    """
    Show the emulator runtime menu.  Returns a dict:
      {'joystick_input': <new value or unchanged>}

    CPU stepping is paused implicitly because main() blocks here.

    The top level lists categories (Toggles / Storage / Display / System);
    each opens as its own sub-menu via _run_menu(), BRK going back one level.
    BRK or Exit at the top level closes the whole menu.
    """
    state = {
        'joystick_input': joystick_input,
        '_joy_saved': None,
    }

    _run_menu(
        display, "==  EMULATOR MENU  ==", "GUI+F7:open  EXE:select  BRK:exit",
        _build_top_items,
        lambda iid, items, cur: _dispatch_top(iid, system, display, fkbar, keyboard_input, cfg, state),
    )

    # Restore display: clear menu area, then redraw bezel + LCD + FuncKeyBar
    display.fill_rect(0, 0, display.width, display.height, 0x0000)
    system.force_full_redraw()
    if fkbar is not None:
        try:
            fkbar.draw()
        except Exception:
            pass

    return {'joystick_input': state['joystick_input']}
