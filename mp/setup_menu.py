"""
PB-1000 BIOS-style setup menu.

Triggered from boot_session.select_profile_ui() via F1, i.e. before
PB1000System or any ROM/RAM state exists -- the roomiest point in the boot
sequence for heap. Intentionally self-contained: does NOT import
emulator_menu.py (which has grown large) so a normal boot that never presses
F1 never pays for this module's heap at all.

Edits are held in memory (`pending`) and only written to the chosen ini file
when the user picks "Save & Exit", which then calls machine.reset() so the
whole boot sequence re-reads the new config from scratch. "Discard & Back"
(or BRK with no pending edits) returns normally to the caller instead.
"""
import os
import time

from hdmi_menu_mirror import hdmi_flush
from config import load_ini

PROFILE_ROOT = "/sd/rams"

# ── Palette / layout (mirrors emulator_menu.py's palette for visual consistency) ──
_BG     = 0x0000
_FG     = 0xFFFF
_SEL_BG = 0x0210
_SEL_FG = 0x07E0
_HDR    = 0xFFE0
_FTR    = 0x7BEF
_S_ON   = 0x07E0
_S_OFF  = 0xF800
_WARN   = 0xFD20
_SEP    = 0x528A
_HEADER_FG = 0x7BEF

_ROW_H     = 14
_SEP_ROW_H = 6
_MAX_VIS   = 15
_HDR_H     = 26
_FTR_H     = 14

_UNSET = object()  # sentinel: "no pending edit -- show the raw file value"


# ── Key schema ──────────────────────────────────────────────────────────────
# (section, key, kind, extra, curated, flash_only)
#   kind:  "bool" | "enum" | "int" | "str"
#   extra: enum -> list of choices (str); int -> (min, max); else None
#   curated: shown by default (Show ALL keys OFF); non-curated needs ALL=ON
#   flash_only: matches doc/config_guide.md Sec.1 -- only /pb1000.ini takes
#               effect for these, so hide them when editing SD/profile files
SCHEMA = [
    # display
    ("display", "driver",            "enum", ["ILI9341", "ST7796"], True,  True),
    ("display", "spi_baudrate",      "int",  (1000000, 62500000),   False, True),
    ("display", "rotation",          "enum", ["0", "180"],          True,  True),
    ("display", "scale",             "str",  None,                  True,  False),
    ("display", "lcd_height",        "enum", ["32", "64"],          True,  False),
    ("display", "x_offset",          "int",  (0, 480),               False, False),
    ("display", "y_offset",          "int",  (0, 320),               False, False),
    ("display", "fg_color",          "int",  (0, 255),               True,  False),
    ("display", "bg_color",          "int",  (0, 255),               True,  False),

    # keyboard
    ("keyboard", "enable_usb_kbd",              "bool", None,        True,  False),
    ("keyboard", "enable_uart_kbd",              "bool", None,       True,  False),
    ("keyboard", "uart_baudrate",                "int",  (300, 921600), False, False),
    ("keyboard", "uart_tx_pin",                  "int",  (0, 28),     False, False),
    ("keyboard", "uart_rx_pin",                  "int",  (0, 28),     False, False),
    ("keyboard", "uart_enter_always_exe",        "bool", None,        False, False),
    ("keyboard", "key_pulse_interval_ms",        "int",  (1, 1000),   False, False),
    ("keyboard", "key_hold_ms",                  "int",  (1, 5000),   False, False),
    ("keyboard", "key_release_hard_timeout_ms",  "int",  (1, 10000),  False, False),
    ("keyboard", "inter_key_gap_ms",              "int",  (0, 5000),  False, False),

    # emulator
    ("emulator", "enable_repl_uart",       "bool", None,       True,  False),
    ("emulator", "frame_interval_ms",      "int",  (1, 1000),  True,  False),
    ("emulator", "active_step_count",      "int",  (1, 65535), True,  False),
    ("emulator", "sleep_poll_ms",          "int",  (0, 1000),  False, False),
    ("emulator", "step_timer_tick_steps",  "int",  (1, 200000), False, False),
    ("emulator", "timer_tick_ms",          "int",  (0, 60000), False, False),
    ("emulator", "loop_idle_ms",           "int",  (0, 1000),  False, False),
    ("emulator", "step_chunk",             "int",  (64, 65535), False, False),

    # disk
    ("disk", "enabled",  "bool", None,               True,  False),
    ("disk", "backend",  "enum", ["raw"],             False, False),
    ("disk", "path",     "str",  None,                True,  False),
    ("disk", "readonly", "bool", None,                True,  False),

    # profile
    ("profile", "default_profile", "str", None,        True, False),
    ("profile", "ui_timeout_ms",   "int", (0, 300000),  True, False),

    # joystick
    ("joystick", "enable",          "bool", None,        True,  False),
    ("joystick", "enable_fire2",    "bool", None,         False, False),
    ("joystick", "debounce_ms",     "int",  (0, 1000),    False, False),
    ("joystick", "poll_interval_ms","int",  (1, 1000),    False, False),
    ("joystick", "key_up",    "str", None, False, False),
    ("joystick", "key_down",  "str", None, False, False),
    ("joystick", "key_left",  "str", None, False, False),
    ("joystick", "key_right", "str", None, False, False),
    ("joystick", "key_fire1", "str", None, False, False),
    ("joystick", "key_fire2", "str", None, False, False),

    # beep
    ("beep", "enable",   "bool", None,        True,  False),
    ("beep", "gpio_pin", "int",  (0, 28),      False, False),
    ("beep", "freq_hz",  "int",  (20, 20000),  True,  False),
    ("beep", "duty",     "int",  (0, 100),     True,  False),

    # pio_uart
    ("pio_uart", "baudrate", "int", (300, 921600), False, False),

    # wifi
    ("wifi", "ssid",     "str", None, True, False),
    ("wifi", "password", "str", None, True, False),

    # ntp
    ("ntp", "enable",       "bool", None,        True,  False),
    ("ntp", "server",       "str",  None,         False, False),
    ("ntp", "tz_offset_h",  "int",  (-12, 14),    True,  False),
    ("ntp", "timeout_ms",   "int",  (0, 120000),  False, False),

    # debug
    ("debug", "cpu_debug",    "bool", None, False, False),
    ("debug", "key_debug",    "bool", None, False, False),
    ("debug", "lcd_debug",    "bool", None, False, False),
    ("debug", "newall_debug", "bool", None, False, False),

    # hdmi (flash-only -- see config.py's _FLASH_ONLY_SECTIONS: SD/profile
    # pb1000.ini overrides for this section are ignored at load time, so
    # hide these when editing anything but the flash-root file)
    ("hdmi", "enable",     "bool", None,             True,  True),
    ("hdmi", "cs_pin",     "int",  (0, 28),           False, True),
    ("hdmi", "baudrate",   "int",  (1000000, 62500000), True, True),
    ("hdmi", "frame_skip", "int",  (1, 60),           False, True),

    # touch (flash-only calibration flags + SD/profile-editable offsets)
    ("touch", "ili9341.swap_xy", "bool", None, False, True),
    ("touch", "ili9341.x_inv",   "bool", None, False, True),
    ("touch", "ili9341.y_inv",   "bool", None, False, True),
    ("touch", "ili9341.x_offset", "int", (-100, 100), False, False),
    ("touch", "ili9341.y_offset", "int", (-100, 100), False, False),
    ("touch", "ili9341.funckey_x_offset", "int", (-100, 100), False, False),
    ("touch", "ili9341.funckey_y_offset", "int", (-100, 100), False, False),
    ("touch", "st7796.swap_xy",  "bool", None, False, True),
    ("touch", "st7796.x_inv",    "bool", None, False, True),
    ("touch", "st7796.y_inv",    "bool", None, False, True),
    ("touch", "st7796.x_offset", "int", (-100, 100), False, False),
    ("touch", "st7796.y_offset", "int", (-100, 100), False, False),
    ("touch", "st7796.funckey_x_offset", "int", (-100, 100), False, False),
    ("touch", "st7796.funckey_y_offset", "int", (-100, 100), False, False),
]


# ── Low-level display helpers (self-contained -- no emulator_menu import) ──

def _sw16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


def _draw_text(display, x, y, text, fg, bg=_BG):
    text = str(text)
    max_chars = max(0, (display.width - x) // 8)
    text = text[:max_chars]
    if not text:
        return
    record = getattr(display, 'record_text', None)
    if record is not None:
        record(x, y, text, fg, bg)
        return
    import framebuf
    buf = bytearray(8 * 8 * 2)
    fb = framebuf.FrameBuffer(buf, 8, 8, framebuf.RGB565)
    cx = x
    for ch in text:
        fb.fill(_sw16(bg))
        fb.text(ch, 0, 0, _sw16(fg))
        display.set_window(cx, y, cx + 7, y + 7)
        display.write_data(buf)
        cx += 8


# USB HID scancode -> printable char. Covers lowercase letters, digits, and
# a handful of unshifted symbols useful for paths/ssid/password. Shift state
# is not exposed to Python by hd61700.get_last_key(), so uppercase and
# shifted symbols (e.g. '_', '@', '!') cannot be typed here -- edit the ini
# file directly (mpremote etc.) if those characters are required.
_SC_TEXT = {
    0x04: 'a', 0x05: 'b', 0x06: 'c', 0x07: 'd', 0x08: 'e', 0x09: 'f',
    0x0A: 'g', 0x0B: 'h', 0x0C: 'i', 0x0D: 'j', 0x0E: 'k', 0x0F: 'l',
    0x10: 'm', 0x11: 'n', 0x12: 'o', 0x13: 'p', 0x14: 'q', 0x15: 'r',
    0x16: 's', 0x17: 't', 0x18: 'u', 0x19: 'v', 0x1A: 'w', 0x1B: 'x',
    0x1C: 'y', 0x1D: 'z',
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5',
    0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
    0x2C: ' ', 0x2D: '-', 0x37: '.', 0x38: '/',
}

_SC_DIGIT = {
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5',
    0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
}


def _edit_text(display, title, initial="", max_len=48):
    """Free-form text entry. Returns the edited string, or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    text = list(str(initial))[:max_len]

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, title, _HDR)
        _draw_text(display, 4, 16, "a-z 0-9 - . / space  BS:del", _FTR)
        _draw_text(display, 4, H - 12, "EXE:ok  BRK:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(text) + "_", _FG)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc in _SC_TEXT and len(text) < max_len:
                text.append(_SC_TEXT[sc])
                _redraw()
            elif sc == 0x2A and text:   # Backspace
                text.pop()
                _redraw()
            elif sc == 0x28:            # EXE -- confirm (empty allowed)
                return "".join(text)
            elif sc == 0x29:            # BRK -- cancel
                return None
        hdmi_flush(display)
        time.sleep_ms(30)


def _edit_int(display, title, initial, min_v, max_v):
    """Integer entry within [min_v, max_v]. Returns int, or None on cancel."""
    import hd61700
    W, H = display.width, display.height
    neg = min_v < 0
    buf = list(str(initial))
    max_digits = max(len(str(abs(min_v))), len(str(abs(max_v)))) + (1 if neg else 0)

    def _redraw(warn=""):
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, title, _HDR)
        _draw_text(display, 4, 16, "range: %d..%d" % (min_v, max_v), _FTR)
        _draw_text(display, 4, H - 12, "EXE:ok  BRK:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(buf) + "_", _FG)
        if warn:
            _draw_text(display, 4, H // 2 + 10, warn, _WARN)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc in _SC_DIGIT and len(buf) < max_digits:
                buf.append(_SC_DIGIT[sc])
                _redraw()
            elif sc == 0x2D and neg and not buf:   # '-' as first char
                buf.append('-')
                _redraw()
            elif sc == 0x2A and buf:               # Backspace
                buf.pop()
                _redraw()
            elif sc == 0x28:                       # EXE
                try:
                    val = int("".join(buf)) if buf else 0
                except ValueError:
                    val = None
                if val is not None and min_v <= val <= max_v:
                    return val
                _redraw("!! out of range")
            elif sc == 0x29:                       # BRK
                return None
        hdmi_flush(display)
        time.sleep_ms(30)


def _confirm(display, lines):
    """Yes/No prompt. Returns True (EXE) or False (BRK)."""
    import hd61700
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)
    y = H // 2 - 8 * len(lines)
    for line in lines:
        _draw_text(display, 4, y, line, _WARN)
        y += 12
    _draw_text(display, 4, H - 12, "EXE:yes  BRK:no", _FTR)
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x28:
                return True
            elif sc == 0x29:
                return False
        hdmi_flush(display)
        time.sleep_ms(30)


# ── Generic scrollable list menu (adapted from emulator_menu._run_menu) ────

def _next_cursor(items, cur, direction):
    n = len(items)
    for _ in range(n):
        cur = (cur + direction) % n
        if items[cur].get('type') not in ('separator', 'header'):
            return cur
    return cur


def _draw_list(display, title, hint, items, cursor, scroll, msg=""):
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)
    _draw_text(display, 4, 4, title, _HDR)
    _draw_text(display, 4, 14, hint, _FTR)

    y = _HDR_H
    vis_end = min(len(items), scroll + _MAX_VIS)
    for i in range(scroll, vis_end):
        item = items[i]
        kind = item.get('type')
        if kind == 'separator':
            display.fill_rect(0, y + _SEP_ROW_H // 2, W, 1, _SEP)
            y += _SEP_ROW_H
            continue
        if kind == 'header':
            _draw_text(display, 2, y, item['label'], _HEADER_FG)
            y += _ROW_H
            continue

        is_cur = (i == cursor)
        bg_r = _SEL_BG if is_cur else _BG
        fg_r = _SEL_FG if is_cur else _FG
        display.fill_rect(0, y - 1, W, _ROW_H, bg_r)
        prefix = "> " if is_cur else "  "
        _draw_text(display, 4, y, prefix + item.get('label', ''), fg_r, bg_r)

        badge = item.get('badge', '')
        if badge:
            bw = len(badge) * 8
            bx = W - bw - 4
            _draw_text(display, bx, y, badge, item.get('badge_color', fg_r), bg_r)
        y += _ROW_H

    if msg:
        msg_trunc = msg[: (W - 8) // 8]
        display.fill_rect(0, H - _FTR_H, W, _FTR_H, _BG)
        _draw_text(display, 4, H - _FTR_H + 3, msg_trunc, _WARN)


def _run_list(display, title, hint, build_items_fn, dispatch_fn):
    """Run one list level until it closes.

    dispatch_fn(item_id, items, cursor) returns (msg, close):
      close=True  -- unwind this level (its return value bubbles up)
      close=False -- refresh badges and keep looping here
    Left/Right arrow presses are forwarded to dispatch_fn as pseudo item_ids
    ('__left__'/'__right__') for in-place enum cycling.

    Returns whatever dispatch_fn returned as its 3rd element on close (or
    None if BRK closed this level without going through dispatch_fn).
    """
    import hd61700

    msg = ""
    items = build_items_fn()
    cursor = _next_cursor(items, -1, 1)
    scroll = 0
    prev_sc = -1

    _draw_list(display, title, hint, items, cursor, scroll, msg)

    while True:
        sc = hd61700.get_last_key()
        if sc == prev_sc:
            hdmi_flush(display)
            time.sleep_ms(30)
            continue
        prev_sc = sc

        if sc == 0x52:   # UP
            new = _next_cursor(items, cursor, -1)
            if new != cursor:
                cursor = new
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + _MAX_VIS:
                    scroll = max(0, cursor - _MAX_VIS + 1)
                _draw_list(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x51:  # DOWN
            new = _next_cursor(items, cursor, 1)
            if new != cursor:
                cursor = new
                if cursor >= scroll + _MAX_VIS:
                    scroll = cursor - _MAX_VIS + 1
                elif cursor < scroll:
                    scroll = 0
                _draw_list(display, title, hint, items, cursor, scroll, msg)

        elif sc in (0x4F, 0x50):  # RIGHT / LEFT -- in-place enum cycling
            pseudo = '__right__' if sc == 0x4F else '__left__'
            item_id = items[cursor].get('id', '')
            msg, close, ret = dispatch_fn(pseudo, item_id, items, cursor)
            if close:
                return ret
            items = build_items_fn()
            _draw_list(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x28:  # EXE
            item_id = items[cursor].get('id', '')
            msg, close, ret = dispatch_fn('__exe__', item_id, items, cursor)
            if close:
                return ret
            items = build_items_fn()
            _draw_list(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x2A:  # Backspace -- clear override on a key row
            item_id = items[cursor].get('id', '')
            msg, close, ret = dispatch_fn('__clear__', item_id, items, cursor)
            if close:
                return ret
            items = build_items_fn()
            _draw_list(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x29:  # BREAK -- back one level
            msg, close, ret = dispatch_fn('__back__', None, items, cursor)
            if close:
                return ret
            items = build_items_fn()
            _draw_list(display, title, hint, items, cursor, scroll, msg)


# ── ini read/write (self-contained; does not import emulator_menu.py) ──────

def _apply_ini_changes(path, changes):
    """Apply {section: {key: value_or_None}} to an ini file, preserving
    comments/other content. value=None deletes that key's line entirely.
    Sections/keys not mentioned in `changes` are left untouched."""
    lines = []
    try:
        with open(path, "r") as f:
            for line in f:
                lines.append(line)
    except OSError:
        pass

    for section, kv in changes.items():
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
                else:
                    new_block.append(line)
        for k, v in kv.items():
            if v is None:
                continue  # deletion: simply not re-emitted
            new_block.append(k + " = " + str(v) + "\n")
        new_block.append("\n")

        if sec_start >= 0:
            lines = lines[:sec_start] + new_block + lines[sec_end:]
        else:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines += new_block

    with open(path, "w") as f:
        for line in lines:
            f.write(line)


# ── Target file picker (screen 1) ───────────────────────────────────────────

def _list_targets(sd_mounted, profiles):
    """Returns [(label, path, kind), ...]."""
    targets = [("Flash: /pb1000.ini", "/pb1000.ini", "flash")]
    if sd_mounted:
        targets.append(("SD: /sd/pb1000.ini", "/sd/pb1000.ini", "sd"))
    for name in profiles:
        path = PROFILE_ROOT + "/" + name + "/pb1000.ini"
        targets.append(("RAM profile: " + name, path, "profile"))
    return targets


def _pick_target(display, sd_mounted, profiles):
    """Returns (path, kind) or None if the user backed out."""
    targets = _list_targets(sd_mounted, profiles)

    def _build():
        items = [{'id': i, 'label': label} for i, (label, _p, _k) in enumerate(targets)]
        items.append({'type': 'separator'})
        items.append({'id': 'back', 'label': 'Back (boot)'})
        return items

    def _dispatch(action, item_id, items, cursor):
        if action == '__back__':
            return "", True, None
        if action != '__exe__':
            return "", False, None
        if item_id == 'back':
            return "", True, None
        label, path, kind = targets[item_id]
        return "", True, (path, kind)

    return _run_list(display, "=== PB-1000 SETUP ===", "UP/DN:move  EXE:select  BRK:back",
                      _build, _dispatch)


# ── Key editor (screen 2) ───────────────────────────────────────────────────

def _fmt_val(v):
    if v is None:
        return "(unset)"
    return str(v)


def _cycle(choices, current):
    try:
        idx = choices.index(current)
    except ValueError:
        idx = -1
    return choices[(idx + 1) % len(choices)]


def _edit_target(display, path, kind):
    """Edit one ini file's keys. Returns True if the caller should reset
    (i.e. changes were saved), False otherwise."""
    raw = load_ini(path)
    pending = {}   # {section: {key: value_or_None}}
    show_all = [False]  # mutable box so closures can flip it

    def _effective(section, key):
        ov = pending.get(section, {}).get(key, _UNSET)
        if ov is not _UNSET:
            return ov
        return raw.get(section, {}).get(key)

    def _rows():
        rows = []
        last_section = None
        for section, key, kw, extra, curated, flash_only in SCHEMA:
            if not show_all[0] and not curated:
                continue
            if flash_only and kind != "flash":
                continue
            if section != last_section:
                rows.append({'type': 'header', 'label': "[" + section + "]"})
                last_section = section
            val = _effective(section, key)
            rows.append({
                'id': (section, key, kw, extra),
                'label': key,
                'badge': _fmt_val(val),
                'badge_color': _FG if val is not None else _FTR,
            })
        return rows

    def _build():
        items = [{
            'id': 'toggle_all',
            'label': 'Show ALL keys',
            'badge': 'ON' if show_all[0] else 'OFF',
            'badge_color': _S_ON if show_all[0] else _S_OFF,
        }]
        items.append({'type': 'separator'})
        items += _rows()
        items.append({'type': 'separator'})
        n_pending = sum(len(kv) for kv in pending.values())
        items.append({'id': 'save', 'label': 'Save & Exit (reset)',
                      'badge': str(n_pending) if n_pending else '',
                      'badge_color': _S_ON})
        items.append({'id': 'discard', 'label': 'Discard & Back'})
        return items

    def _dispatch(action, item_id, items, cursor):
        if action == '__back__':
            if any(pending.values()):
                if not _confirm(display, ["Discard unsaved changes?"]):
                    return "", False, None
            return "", True, False

        if item_id == 'toggle_all':
            if action == '__exe__':
                show_all[0] = not show_all[0]
            return "", False, None

        if item_id == 'discard':
            if action != '__exe__':
                return "", False, None
            if any(pending.values()):
                if not _confirm(display, ["Discard unsaved changes?"]):
                    return "", False, None
            return "", True, False

        if item_id == 'save':
            if action != '__exe__':
                return "", False, None
            if not any(pending.values()):
                return "Nothing to save.", False, None
            if not _confirm(display, ["Save and reboot now?"]):
                return "", False, None
            try:
                _apply_ini_changes(path, pending)
            except Exception as e:
                return "Save failed: %s" % e, False, None
            display.fill_rect(0, 0, display.width, display.height, _BG)
            _draw_text(display, 4, display.height // 2 - 4, "Saved. Rebooting...", _S_ON)
            hdmi_flush(display)
            time.sleep_ms(600)
            return "", True, True

        # A key row: item_id == (section, key, kind, extra)
        if not isinstance(item_id, tuple):
            return "", False, None
        section, key, kw, extra = item_id
        cur = _effective(section, key)

        if action == '__clear__':
            pending.setdefault(section, {})[key] = None
            return "", False, None

        if kw == 'bool' and action == '__exe__':
            cur_b = str(cur).strip().lower() in ("1", "true", "yes", "on") if cur is not None else False
            pending.setdefault(section, {})[key] = "false" if cur_b else "true"
            return "", False, None

        if kw == 'enum' and action in ('__exe__', '__left__', '__right__'):
            choices = extra
            base = cur if cur in choices else choices[0]
            if action == '__left__':
                new_val = choices[(choices.index(base) - 1) % len(choices)]
            else:
                new_val = _cycle(choices, base)
            pending.setdefault(section, {})[key] = new_val
            return "", False, None

        if kw == 'int' and action == '__exe__':
            min_v, max_v = extra
            start = cur if cur is not None else "0"
            try:
                start_i = int(start)
            except (ValueError, TypeError):
                start_i = min_v
            result = _edit_int(display, "[%s] %s" % (section, key), start_i, min_v, max_v)
            if result is not None:
                pending.setdefault(section, {})[key] = str(result)
            return "", False, None

        if kw == 'str' and action == '__exe__':
            result = _edit_text(display, "[%s] %s" % (section, key), cur or "")
            if result is not None:
                pending.setdefault(section, {})[key] = result
            return "", False, None

        return "", False, None

    return _run_list(display, "SETUP: " + path,
                      "UP/DN:move EXE:edit L/R:cycle BS:clear BRK:back",
                      _build, _dispatch)


# ── Entry point ──────────────────────────────────────────────────────────────

def run_setup_menu(display, sd_mounted, profiles):
    """Blocks until the user backs all the way out (returns normally) or
    saves changes, in which case it calls machine.reset() and never returns."""
    while True:
        picked = _pick_target(display, sd_mounted, profiles)
        if picked is None:
            return
        path, kind = picked
        did_save = _edit_target(display, path, kind)
        if did_save:
            import machine
            machine.reset()
            return  # unreachable
