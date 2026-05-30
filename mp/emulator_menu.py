"""
PB-1000 Emulator runtime menu.
Triggered from main.py via GUI+F7.

CPU stepping is implicitly paused during menu execution because the main loop
blocks on this function.  All changes take effect immediately at runtime;
persistence (writing back to pb1000.ini) is intentionally out of scope here.

Dangerous operations:
  RAM Load  — loads state then forces reset+power_on
  vFDD off  — refused while FDD interface is powered
  RS-232C   — warns but allows toggle while mid-transfer is unlikely
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
_MAX_VIS = 12      # rows visible (320×240, header=26px, footer=14px)
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
    tw = len(text) * 8
    buf = bytearray(tw * 8 * 2)
    fb = framebuf.FrameBuffer(buf, tw, 8, framebuf.RGB565)
    fb.fill(_sw16(bg))
    fb.text(text, 0, 0, _sw16(fg))
    display.set_window(x, y, x + tw - 1, y + 7)
    display.write_data(buf)


# ── Item list builder ──────────────────────────────────────────────────────────

def _badge(on):
    """Return (badge_str, color) for a boolean state."""
    return (" ON", _S_ON) if on else ("OFF", _S_OFF)


def _build_items(system, state):
    """Build menu item list from current system + state.

    state dict keys:
      joystick_input   — current joystick manager or None
      cfg              — merged config dict
    """
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

    items.append({'type': 'separator'})

    # Storage
    items.append({'id': 'fd_swap',  'label': 'FD Swap'})
    items.append({'id': 'ram_save', 'label': 'RAM Save'})
    items.append({'id': 'ram_load', 'label': 'RAM Load  [!!]'})
    items.append({'id': 'vram_save','label': 'VRAM Save'})

    items.append({'type': 'separator'})

    # Display
    fg_hex = "{:04X}".format(system.lcd._color_fg)
    bg_hex = "{:04X}".format(system.lcd._color_bg_on)
    items.append({'id': 'fg_color', 'label': 'Foreground Color', 'badge': fg_hex, 'badge_color': _FG})
    items.append({'id': 'bg_color', 'label': 'Background Color', 'badge': bg_hex, 'badge_color': _FG})

    items.append({'type': 'separator'})
    items.append({'id': 'exit',     'label': 'Exit'})

    return items


# ── Menu renderer ──────────────────────────────────────────────────────────────

def _draw_menu(display, items, cursor, scroll, msg=""):
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)

    _draw_text(display, 4,  4, "==  EMULATOR MENU  ==", _HDR)
    _draw_text(display, 4, 14, "GUI+F7:open  EXE:select  BRK:exit", _FTR)

    y = _HDR_H
    vis_end = min(len(items), scroll + _MAX_VIS)

    for i in range(scroll, vis_end):
        item = items[i]
        if item.get('type') == 'separator':
            display.fill_rect(0, y + 4, W, 1, _SEP)
            y += _ROW_H
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


# ── Color picker sub-menu ─────────────────────────────────────────────────────

_COLORS = [
    ("Black",   0x0000), ("White",   0xFFFF), ("Green",   0x07E0),
    ("Amber",   0xFD20), ("Blue",    0x001F), ("Cyan",    0x07FF),
    ("Magenta", 0xF81F), ("Yellow",  0xFFE0), ("DkGreen", 0x0210),
    ("DkGrey",  0x8410), ("LtGrey",  0xC618), ("Navy",    0x000F),
]

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


def _pick_color(display, title, current):
    """Color picker sub-menu. Returns chosen RGB565 value or current on cancel."""
    import hd61700
    W, H = display.width, display.height

    cursor = 0
    for i, (_, c) in enumerate(_COLORS):
        if c == current:
            cursor = i
            break

    def _redraw_picker():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4,    title,                 _HDR)
        _draw_text(display, 4, H-10, "EXE:ok  BRK:cancel",  _FTR)
        y = 20
        for i, (name, col) in enumerate(_COLORS):
            is_cur = (i == cursor)
            bg_r = _SEL_BG if is_cur else _BG
            fg_r = _SEL_FG if is_cur else _FG
            display.fill_rect(4,       y, 18, 8, col)     # color swatch
            display.fill_rect(24,      y, W - 24, 8, bg_r)
            pfx = "> " if is_cur else "  "
            _draw_text(display, 24, y, pfx + name, fg_r, bg_r)
            y += _ROW_H

    _redraw_picker()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x52 and cursor > 0:
                cursor -= 1; _redraw_picker()
            elif sc == 0x51 and cursor < len(_COLORS) - 1:
                cursor += 1; _redraw_picker()
            elif sc == 0x28:
                return _COLORS[cursor][1]
            elif sc == 0x29:
                return current
        time.sleep_ms(30)


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


# ── RAM save folder picker ─────────────────────────────────────────────────────

def _pick_save_dir(display, dirs, current_dir):
    """Folder picker for RAM save.
    items[0] = New folder option; items[1..] = existing dir names.
    Returns ('existing', path) | ('new', None) | (None, None) on cancel."""
    import hd61700
    W, H = display.width, display.height
    max_vis = (H - _HDR_H - _FTR_H) // _ROW_H

    all_items = [None] + dirs   # None sentinel = "New folder"

    # Default cursor: current profile match, else first existing dir, else New
    cursor = 1 if dirs else 0
    for i, name in enumerate(dirs, 1):
        if _RAM_BASE + "/" + name == current_dir:
            cursor = i
            break
    scroll = max(0, cursor - max_vis + 1)

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4,  4, "== RAM SAVE ==", _HDR)
        _draw_text(display, 4, 14, "EXE:save  BRK:cancel", _FTR)
        y = _HDR_H
        for i in range(scroll, min(len(all_items), scroll + max_vis)):
            is_cur = (i == cursor)
            bg_r = _SEL_BG if is_cur else _BG
            fg_r = _SEL_FG if is_cur else _FG
            display.fill_rect(0, y - 1, W, _ROW_H, bg_r)
            pfx = "> " if is_cur else "  "
            if all_items[i] is None:
                col = _WARN if is_cur else _FTR
                _draw_text(display, 4, y, pfx + "[ New folder... ]", col, bg_r)
            else:
                name = all_items[i]
                mark = " *" if (_RAM_BASE + "/" + name == current_dir) else ""
                _draw_text(display, 4, y, pfx + name + mark, fg_r, bg_r)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x52 and cursor > 0:
                cursor -= 1
                if cursor < scroll: scroll = cursor
                _redraw()
            elif sc == 0x51 and cursor < len(all_items) - 1:
                cursor += 1
                if cursor >= scroll + max_vis: scroll = cursor - max_vis + 1
                _redraw()
            elif sc == 0x28:
                if all_items[cursor] is None:
                    return 'new', None
                return 'existing', _RAM_BASE + "/" + all_items[cursor]
            elif sc == 0x29:
                return None, None
        time.sleep_ms(30)


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


def _do_fd_swap(system, display, fkbar):
    from main_actions import handle_disk_swap
    handle_disk_swap(system, display, fkbar)
    return "FD swap done"


def _do_ram_save(system, display):
    import os
    try:
        entries = sorted(os.listdir(_RAM_BASE))
    except OSError:
        entries = []
    dirs = []
    for name in entries:
        try:
            if os.stat(_RAM_BASE + "/" + name)[0] & 0x4000:
                dirs.append(name)
        except OSError:
            pass

    current_dir = getattr(system, 'profile_dir', None)
    kind, path = _pick_save_dir(display, dirs, current_dir)

    if kind is None:
        return ""   # cancelled at folder picker

    if kind == 'new':
        name = _text_input(display, "New folder name:")
        if name is None:
            return ""   # cancelled at text input
        path = _RAM_BASE + "/" + name

    try:
        system.save_state(path=path)
        return "RAM saved: " + path.rsplit("/", 1)[-1]
    except Exception as e:
        return f"!! RAM save error: {e}"


_RAM_BASE = "/sd/rams"

def _pick_ram_dir(display, dirs, current_dir):
    """Scrollable folder picker for /sd/rams/. Returns full path or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    max_vis = (H - _HDR_H - _FTR_H) // _ROW_H

    # Set initial cursor to current profile if present
    cursor = 0
    for i, name in enumerate(dirs):
        if _RAM_BASE + "/" + name == current_dir:
            cursor = i
            break
    scroll = max(0, cursor - max_vis + 1)

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4,  4, "== RAM LOAD ==", _HDR)
        _draw_text(display, 4, 14, "EXE:load  BRK:cancel", _FTR)
        y = _HDR_H
        for i in range(scroll, min(len(dirs), scroll + max_vis)):
            name = dirs[i]
            is_cur = (i == cursor)
            bg_r = _SEL_BG if is_cur else _BG
            fg_r = _SEL_FG if is_cur else _FG
            display.fill_rect(0, y - 1, W, _ROW_H, bg_r)
            pfx = "> " if is_cur else "  "
            mark = " *" if (_RAM_BASE + "/" + name == current_dir) else ""
            _draw_text(display, 4, y, pfx + name + mark, fg_r, bg_r)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x52 and cursor > 0:              # UP
                cursor -= 1
                if cursor < scroll:
                    scroll = cursor
                _redraw()
            elif sc == 0x51 and cursor < len(dirs) - 1: # DOWN
                cursor += 1
                if cursor >= scroll + max_vis:
                    scroll = cursor - max_vis + 1
                _redraw()
            elif sc == 0x28:                            # EXE
                return _RAM_BASE + "/" + dirs[cursor]
            elif sc == 0x29:                            # BRK
                return None
        time.sleep_ms(30)


def _do_ram_load(system, display):
    import os
    # Collect subdirectories of /sd/rams
    try:
        entries = sorted(os.listdir(_RAM_BASE))
    except OSError:
        return "!! " + _RAM_BASE + " not found"
    dirs = []
    for name in entries:
        try:
            if os.stat(_RAM_BASE + "/" + name)[0] & 0x4000:
                dirs.append(name)
        except OSError:
            pass
    if not dirs:
        return "!! No RAM saves found in " + _RAM_BASE

    current_dir = getattr(system, 'profile_dir', None)
    selected = _pick_ram_dir(display, dirs, current_dir)
    if selected is None:
        return ""  # cancelled — caller will redraw menu, no break
    try:
        system.load_state(path=selected)
        system.reset_emulator()
        system.power_on(force_reset=True)
        return "RAM loaded: " + selected.rsplit("/", 1)[-1]
    except Exception as e:
        return f"!! RAM load error: {e}"


def _do_vram_save(system):
    import lcd_c as _lc
    import os as _os
    W = 192

    vram = bytes(system.lcd.vram)                           # 768 B mono (copy)
    cvram = _lc.get_color_vram() if hasattr(_lc, 'get_color_vram') else None  # 12288 B ref

    # Determine writable base directory (/sd preferred)
    try:
        _os.listdir("/sd")
        base = "/sd"
    except OSError:
        base = ""

    saved = []
    errors = []

    def _try(name, fn):
        try:
            with open(base + "/" + name, "wb") as f:
                fn(f)
            saved.append(name)
        except Exception:
            errors.append(name)

    # ── 1. Mono VRAM — raw binary (768 bytes) ───────────────────────────────
    _try("vram.bin", lambda f: f.write(vram))

    # ── 2. Mono VRAM — PBM image (P4 binary, 192×32) ────────────────────────
    def _pbm(f):
        f.write(("P4\n%d %d\n" % (W, 32)).encode())
        row = bytearray(W // 8)   # 24 bytes per row
        for y in range(32):
            page, bit = y >> 3, y & 7
            for i in range(W // 8):
                p = 0
                for j in range(8):
                    p = (p << 1) | ((vram[page * W + i * 8 + j] >> bit) & 1)
                row[i] = p
            f.write(row)
    _try("vram.pbm", _pbm)

    if cvram is not None:
        # ── 3. Color VRAM — raw binary (12,288 bytes) ───────────────────────
        _try("color_vram.bin", lambda f: f.write(cvram))

        # ── 4. Color VRAM — PPM image (P6 binary, 192×64, RGB332→RGB888) ───
        H_C = len(cvram) // W   # = 64
        def _ppm(f):
            f.write(("P6\n%d %d\n255\n" % (W, H_C)).encode())
            row = bytearray(W * 3)
            for y in range(H_C):
                for x in range(W):
                    b = cvram[y * W + x]
                    r = (b >> 5) & 7
                    g = (b >> 2) & 7
                    bl = b & 3
                    row[x*3]   = (r << 5) | (r << 2) | (r >> 1)
                    row[x*3+1] = (g << 5) | (g << 2) | (g >> 1)
                    row[x*3+2] = (bl << 6) | (bl << 4) | (bl << 2) | bl
                f.write(row)
        _try("color_vram.ppm", _ppm)

    if not saved:
        return "!! VRAM save: no files written"
    msg = "VRAM saved: %d file%s" % (len(saved), "s" if len(saved) > 1 else "")
    if errors:
        msg += " (%d err)" % len(errors)
    return msg


def _do_fg_color(system, display):
    new = _pick_color(display, "Foreground Color", system.lcd._color_fg)
    system.lcd.set_colors(new, system.lcd._color_bg_on)
    return "FG: {:04X}".format(new)


def _do_bg_color(system, display):
    new = _pick_color(display, "Background Color", system.lcd._color_bg_on)
    system.lcd.set_bg_colors(new, system.lcd._color_bg_off)
    return "BG: {:04X}".format(new)


# ── Cursor navigation helpers ─────────────────────────────────────────────────

def _next_cursor(items, cur, direction):
    n = cur + direction
    while 0 <= n < len(items):
        if items[n].get('type') != 'separator':
            return n
        n += direction
    return cur


# ── Main entry point ─────────────────────────────────────────────────────────

def show_emulator_menu(system, display, fkbar, joystick_input, cfg):
    """
    Show the emulator runtime menu.  Returns a dict:
      {'joystick_input': <new value or unchanged>}

    CPU stepping is paused implicitly because main() blocks here.
    """
    import hd61700

    state = {
        'joystick_input': joystick_input,
        '_joy_saved': None,
    }
    msg = ""
    items = _build_items(system, state)

    # Start cursor on first non-separator item
    cursor = _next_cursor(items, -1, 1)
    scroll = 0
    prev_sc = -1

    _draw_menu(display, items, cursor, scroll, msg)

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
                _draw_menu(display, items, cursor, scroll, msg)

        elif sc == 0x51: # DOWN
            new = _next_cursor(items, cursor, 1)
            if new != cursor:
                cursor = new
                if cursor >= scroll + _MAX_VIS:
                    scroll = cursor - _MAX_VIS + 1
                _draw_menu(display, items, cursor, scroll, msg)

        elif sc == 0x28: # EXE — activate item
            item_id = items[cursor].get('id', '')

            if item_id == 'exit':
                break
            elif item_id == 'console':
                msg = _do_console(system)
            elif item_id == 'rs232':
                msg = _do_rs232(system)
            elif item_id == 'vfdd':
                msg = _do_vfdd(system)
            elif item_id == 'beep':
                msg = _do_beep(system, cfg)
            elif item_id == 'joystick':
                msg = _do_joystick(state)
            elif item_id == 'vdp':
                new_state = not getattr(system.lcd, 'vdp_enabled', True)
                system.lcd.set_vdp_enable(new_state)
                msg = "Color VRAM: " + ("ON" if new_state else "OFF (global color)")
                system.lcd.dirty = True
            elif item_id == 'fd_swap':
                msg = _do_fd_swap(system, display, fkbar)
            elif item_id == 'ram_save':
                msg = _do_ram_save(system, display)
            elif item_id == 'ram_load':
                msg = _do_ram_load(system, display)
                if msg and not msg.startswith("!!"):
                    # Load succeeded — exit menu so main loop re-syncs after reset
                    break
            elif item_id == 'vram_save':
                msg = _do_vram_save(system)
            elif item_id == 'fg_color':
                msg = _do_fg_color(system, display)
            elif item_id == 'bg_color':
                msg = _do_bg_color(system, display)

            items = _build_items(system, state)  # refresh badges
            _draw_menu(display, items, cursor, scroll, msg)

        elif sc == 0x29: # BREAK — exit
            break

    # Restore display: clear menu area, then redraw bezel + LCD + FuncKeyBar
    display.fill_rect(0, 0, display.width, display.height, 0x0000)
    system.force_full_redraw()
    if fkbar is not None:
        try:
            fkbar.draw()
        except Exception:
            pass

    return {'joystick_input': state['joystick_input']}
