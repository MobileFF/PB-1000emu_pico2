"""
PB-1000 Emulator runtime menu.
Triggered from main.py via GUI+F7.

CPU stepping is implicitly paused during menu execution because the main loop
blocks on this function.  All changes take effect immediately at runtime;
persistence (writing back to pb1000.ini) is intentionally out of scope here.

Dangerous operations:
  Reboot Emulator (MCU) — full machine.reset(); any progress not already
                          written out via RAM Save is lost
  NEW ALL               — erases all user memory; requires confirmation
"""

import time
import gc
import lcd_c as _lc

from hdmi_menu_mirror import create as _create_hdmi_mirror, hdmi_flush
from draw_text import draw_text as _draw_text

# ── Color palette ─────────────────────────────────────────────────────────────
_BG     = 0x0000   # background
_FG     = 0xFFFF   # normal text
_SEL_BG = 0x0210   # selected row background (dark green)
_SEL_FG = 0x07E0   # selected row text (bright green)
_HDR    = 0xFFE0   # header (yellow)
_FTR    = 0x7BEF   # footer (light grey)
_S_ON   = 0x07E0   # status ON (green)
_S_OFF  = 0xF800   # status OFF (red)
_WARN   = 0xFD20   # warning (orange)
_SEP    = 0x528A   # separator (mid grey)

_ROW_H   = 14
_SEP_ROW_H = 6     # separator row height (13×14 + 3×6 = 200px = available body)
_MAX_VIS = 20      # larger than total items; all fit without scrolling
_HDR_H   = 26
_FTR_H   = 14


# _draw_text is imported from draw_text.py above (identical implementation --
# fixed-size-buffer, HDMI record_text check included -- this file's former
# copy was the reference draw_text.py was consolidated from).


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
    # Serial Console / RS-232C / vFDD / Joystick / VDP toggles were removed
    # (2026-08-22): all of them correspond 1:1 to a pb1000.ini setting now
    # editable at boot via the F1 setup menu (setup_menu.py) with a
    # machine.reset() to apply it, which covers the same need without this
    # menu having to carry a live-toggle implementation for each. Beep is
    # kept because muting it mid-session (without losing running state via
    # a reboot) is a real, distinct need the setup menu can't cover.
    b, bc = _badge(not getattr(system, '_menu_beep_muted', False))
    return [{'id': 'beep', 'label': 'Beep', 'badge': b, 'badge_color': bc}]


def _build_storage_items():
    # RAM Load was removed (2026-08-22): reaching a different profile's
    # saved state now means System > Reboot Emulator (MCU), then picking
    # it at the boot-time profile picker -- that picker already calls
    # load_state() for whichever profile is chosen, so it's the same
    # outcome without this menu having to carry switch_profile()'s
    # cross-profile ext/config hot-swap logic (see pb1000.py history).
    # Loading the *current* profile's own last save is also just "reboot,
    # then pick it again" -- the picker's default_profile/last-selected
    # behavior makes that a couple of keypresses, not a big loss.
    return [
        {'id': 'fd_swap',      'label': 'FD Swap'},
        {'id': 'ram_save',     'label': 'RAM Save'},
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


def _build_system_items(system):
    return [
        {'id': 'cpu_status',  'label': 'CPU Status'},
        {'id': 'hook_status', 'label': 'Hook Status'},
        {'id': 'step_count',  'label': 'CPU Steps/Slice',
         'badge': str(getattr(system, '_active_step_count', '?')), 'badge_color': _FG},
        {'id': 'loop_idle',   'label': 'Loop Idle (ms)',
         'badge': str(getattr(system, '_loop_idle_ms', '?')), 'badge_color': _FG},
        {'id': 'reset',       'label': 'Reset'},
        {'id': 'newall',      'label': 'NEW ALL (clear memory)'},
        {'id': 'reboot_mcu',  'label': 'Reboot Emulator (MCU)'},
    ]


# ── Menu renderer ──────────────────────────────────────────────────────────────
#
# Nothing here clears the screen before drawing (2026-10-25). Every function
# paints each pixel of its own area exactly once -- text cells carry their own
# background, and the gaps around them are filled with the background color --
# so a redraw replaces the previous content in place instead of flashing to
# black first. _draw_menu() covers the whole screen this way, which is what
# lets it draw straight over a sub-screen (CPU Status, color picker, ...) or
# the game screen when the menu opens. Cursor moves only repaint the two
# affected rows (see _run_menu()).

def _text_band(display, x, y, text, fg, bg, right):
    """Draw text at (x, y) and fill the rest of its 8px band from 0 to right
    with bg (the left margin and the space after the text)."""
    display.fill_rect(0, y, x, 8, bg)
    _draw_text(display, x, y, text, fg, bg)
    # Same clipping as draw_text(): whole characters up to the display edge.
    end = x + min(len(text), max(0, (display.width - x) // 8)) * 8
    if end < right:
        display.fill_rect(end, y, right - end, 8, bg)


def _row_h(item):
    return _SEP_ROW_H if item.get('type') == 'separator' else _ROW_H


def _row_y(items, scroll, idx):
    """Top text line of items[idx] when the list is scrolled to scroll."""
    y = _HDR_H
    for i in range(scroll, idx):
        y += _row_h(items[i])
    return y


def _draw_row(display, item, y, is_cur):
    """Paint one row's whole band (y-1 .. y-1+row height)."""
    W = display.width
    if item.get('type') == 'separator':
        line_y = y + _SEP_ROW_H // 2
        display.fill_rect(0, y - 1, W, line_y - (y - 1), _BG)
        display.fill_rect(0, line_y, W, 1, _SEP)
        display.fill_rect(0, line_y + 1, W, (y - 1 + _SEP_ROW_H) - (line_y + 1), _BG)
        return

    bg_r = _SEL_BG if is_cur else _BG
    fg_r = _SEL_FG if is_cur else _FG
    display.fill_rect(0, y - 1, W, 1, bg_r)

    prefix = "> " if is_cur else "  "
    label = prefix + item.get('label', '')
    badge = item.get('badge', '')
    if badge:
        bx = W - len(badge) * 8 - 4
        _text_band(display, 4, y, label, fg_r, bg_r, bx)
        _draw_text(display, bx, y, badge, item.get('badge_color', fg_r), bg_r)
        display.fill_rect(W - 4, y, 4, 8, bg_r)
    else:
        _text_band(display, 4, y, label, fg_r, bg_r, W)

    display.fill_rect(0, y + 8, W, _ROW_H - 9, bg_r)


def _draw_footer(display, msg):
    W, H = display.width, display.height
    top = H - _FTR_H
    display.fill_rect(0, top, W, 3, _BG)
    _text_band(display, 4, top + 3, msg[: (W - 8) // 8], _WARN, _BG, W)
    display.fill_rect(0, top + 11, W, _FTR_H - 11, _BG)


def _draw_menu(display, title, hint, items, cursor, scroll, msg=""):
    W, H = display.width, display.height

    display.fill_rect(0, 0, W, 4, _BG)
    _text_band(display, 4,  4, title, _HDR, _BG, W)
    display.fill_rect(0, 12, W, 2, _BG)
    _text_band(display, 4, 14, hint, _FTR, _BG, W)
    display.fill_rect(0, 22, W, _HDR_H - 23, _BG)

    y = _HDR_H
    vis_end = min(len(items), scroll + _MAX_VIS)

    for i in range(scroll, vis_end):
        _draw_row(display, items[i], y, i == cursor)
        y += _row_h(items[i])

    # Blank body below the last row, down to the footer
    body_end = H - _FTR_H
    if y - 1 < body_end:
        display.fill_rect(0, y - 1, W, body_end - (y - 1), _BG)

    _draw_footer(display, msg)


# USB HID scancode → printable char (a-z, 0-9, hyphen only — for folder
# names): hd61700.scancode_to_alpha() (src/modhd61700.c), a flash-resident
# table lookup -- used to be a 34-entry dict here, holding both a live hash
# table and the bytecode to build it on the GC heap for the whole session
# once emulator_menu.py is preloaded, even though text input is only used
# for occasional folder naming.

# The actual conversion lives in lcd_c (src/modlcd_controller.c) -- these
# two names exist only so every other module keeps importing
# `_rgb565_to_rgb332`/`_rgb332_to_rgb565` from here unchanged
# (emulator_menu_display_actions.py does). 2026-10-16: no hasattr()
# fallback (see 調査用/heap_reduction_hasattr_removal.md) -- requires
# rebuilt firmware, same as every other lcd_c/cpu_core export in this
# codebase now.
_rgb565_to_rgb332 = _lc.rgb565_to_rgb332
_rgb332_to_rgb565 = _lc.rgb332_to_rgb565


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
        _draw_text(display, 4, H - 12, "Enter:ok  ESC:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(text) + "_", _FG)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            ch = hd61700.scancode_to_alpha(sc)
            if ch is not None and len(text) < max_len:
                text.append(ch)
                _redraw()
            elif sc == 0x2A and text:   # Backspace
                text.pop()
                _redraw()
            elif sc == 0x28 and text:   # Enter — confirm (reject empty)
                return "".join(text)
            elif sc == 0x29:            # ESC — cancel
                return None
        hdmi_flush(display)
        time.sleep_ms(30)


def _confirm(display, title, detail=""):
    """Yes/No confirmation dialog. Returns True (Enter) or False (ESC)."""
    import hd61700
    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)
    _draw_text(display, 4,  4, title,  _WARN)
    if detail:
        _draw_text(display, 4, 18, detail, _FG)
    _draw_text(display, 4, H - 10, "Enter:yes  ESC:no", _FTR)
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


def _do_fd_swap(system, display, fkbar):
    from main_actions import handle_disk_swap
    handle_disk_swap(system, display, fkbar)
    return "FD swap done"


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

def _run_menu(display, title, hint, build_items_fn, dispatch_fn, esc_exits=True):
    """Run one menu level until it closes.

    build_items_fn() -> item list, called on entry and after every Enter (so
    badges stay current).

    dispatch_fn(item_id, items, cursor) is called on Enter and must return
    (msg, close_all):
      close_all=True  — unwind every nested menu level back to the caller of
                         show_emulator_menu (Exit, Reset, Reboot Emulator
                         (MCU), or a successful NEW ALL — anything that needs
                         the main loop to resume CPU stepping right away).
      close_all=False — show msg, refresh the item list, keep looping here.

    Returns True if the whole menu system should close, False if ESC was
    pressed and this level should just return to its caller ("back").

    esc_exits=False makes ESC a no-op at this level instead of returning
    "back" (2026-10-20). Used only for the top-level call in
    show_emulator_menu(): ESC is bound to the PB-1000's BRK key, and if the
    physical key is still held the instant this function returns and CPU
    stepping resumes, that held BRK would be forwarded into the running
    BASIC program as a real BREAK. Submenus keep esc_exits=True since
    backing out of one just returns to the still-open top-level menu loop,
    never straight into resumed CPU stepping.
    """
    import hd61700

    msg = ""
    items = build_items_fn()
    cursor = _next_cursor(items, -1, 1)
    scroll = 0
    prev_sc = -1

    _draw_menu(display, title, hint, items, cursor, scroll, msg)

    def _show_cursor_move(old, old_scroll):
        # Same scroll position: only the rows losing and gaining the
        # highlight change, so repaint just those two.
        if scroll != old_scroll:
            _draw_menu(display, title, hint, items, cursor, scroll, msg)
            return
        _draw_row(display, items[old], _row_y(items, scroll, old), False)
        _draw_row(display, items[cursor], _row_y(items, scroll, cursor), True)

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
                old, old_scroll = cursor, scroll
                cursor = new
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + _MAX_VIS:        # wrapped to bottom
                    scroll = max(0, cursor - _MAX_VIS + 1)
                _show_cursor_move(old, old_scroll)

        elif sc == 0x51: # DOWN
            new = _next_cursor(items, cursor, 1)
            if new != cursor:
                old, old_scroll = cursor, scroll
                cursor = new
                if cursor >= scroll + _MAX_VIS:
                    scroll = cursor - _MAX_VIS + 1
                elif cursor < scroll:                    # wrapped to top
                    scroll = 0
                _show_cursor_move(old, old_scroll)

        elif sc == 0x28: # Enter — activate item
            item_id = items[cursor].get('id', '')
            msg, close_all = dispatch_fn(item_id, items, cursor)
            if close_all:
                return True
            items = build_items_fn()  # refresh badges
            _draw_menu(display, title, hint, items, cursor, scroll, msg)

        elif sc == 0x29: # ESC — back one level
            if esc_exits:
                return False
            # Ignored here (esc_exits=False, top level only) -- see this
            # function's docstring. Select Exit instead.


# ── Per-category dispatch ─────────────────────────────────────────────────────

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


def _dispatch_toggles(item_id, system, state, cfg):
    if item_id == 'beep':
        return _do_beep(system, cfg), False
    return "", False


def _dispatch_storage(item_id, system, display, fkbar):
    if item_id == 'fd_swap':
        return _do_fd_swap(system, display, fkbar), False
    if item_id == 'ram_save':
        gc.collect()  # on-demand compile needs contiguous heap
        from emulator_menu_ram import _do_ram_save
        return _do_ram_save(system, display), False
    if item_id == 'vram_save':
        gc.collect()
        from emulator_menu_capture import _do_vram_save
        return _do_vram_save(system), False
    if item_id == 'full_capture':
        gc.collect()
        from emulator_menu_capture import _do_full_capture
        return _do_full_capture(system, display, fkbar), False
    return "", False


def _dispatch_display(item_id, system, display, fkbar):
    if item_id == 'fg_color':
        gc.collect()  # on-demand compile needs contiguous heap
        from emulator_menu_display_actions import _do_fg_color
        return _do_fg_color(system, display), False
    if item_id == 'bg_color':
        gc.collect()
        from emulator_menu_display_actions import _do_bg_color
        return _do_bg_color(system, display), False
    if item_id == 'lcd_height':
        gc.collect()
        from emulator_menu_display_actions import _do_lcd_height
        # close_all=True: the physical bezel/LCD/FuncKeyBar layout changed
        # size, so unwind to show_emulator_menu()'s tail redraw (full clear +
        # force_full_redraw + fkbar.draw()) rather than trying to patch just
        # the submenu area.
        return _do_lcd_height(system, fkbar), True
    return "", False


def _dispatch_system(item_id, system, display):
    if item_id == 'hook_status':
        gc.collect()  # on-demand compile needs contiguous heap
        from emulator_menu_debug import _do_hook_status
        _do_hook_status(system, display)
        return "", False
    if item_id == 'cpu_status':
        gc.collect()
        from emulator_menu_debug import _do_cpu_status
        _do_cpu_status(system, display)
        return "", False
    if item_id == 'step_count':
        gc.collect()  # on-demand compile needs contiguous heap
        from emulator_menu_system_actions import _do_step_count
        return _do_step_count(system, display), False
    if item_id == 'loop_idle':
        gc.collect()
        from emulator_menu_system_actions import _do_loop_idle
        return _do_loop_idle(system, display), False
    if item_id == 'reset':
        gc.collect()
        from emulator_menu_system_actions import _do_reset
        # main loop must resume stepping from PC=0
        return _do_reset(system), True
    if item_id == 'reboot_mcu':
        gc.collect()
        from emulator_menu_system_actions import _do_reboot_mcu
        return _do_reboot_mcu(display), True
    if item_id == 'newall':
        gc.collect()
        from emulator_menu_system_actions import _do_newall
        msg = _do_newall(system, display)
        # PC now points at the ROM's NEW ALL handler -- exit menu so the
        # main loop resumes CPU stepping from there.
        return msg, bool(msg) and not msg.startswith("!!")
    return "", False


def _dispatch_top(item_id, system, display, fkbar, cfg, state):
    if item_id == 'exit':
        return "", True
    if item_id == 'cat_toggles':
        closed = _run_menu(
            display, "-- TOGGLES --", "Enter:select  ESC:back",
            lambda: _build_toggle_items(system, state),
            lambda iid, items, cur: _dispatch_toggles(iid, system, state, cfg),
        )
        return "", closed
    if item_id == 'cat_storage':
        closed = _run_menu(
            display, "-- STORAGE --", "Enter:select  ESC:back",
            _build_storage_items,
            lambda iid, items, cur: _dispatch_storage(iid, system, display, fkbar),
        )
        return "", closed
    if item_id == 'cat_display':
        closed = _run_menu(
            display, "-- DISPLAY --", "Enter:select  ESC:back",
            lambda: _build_display_items(system),
            lambda iid, items, cur: _dispatch_display(iid, system, display, fkbar),
        )
        return "", closed
    if item_id == 'cat_system':
        closed = _run_menu(
            display, "-- SYSTEM --", "Enter:select  ESC:back",
            lambda: _build_system_items(system),
            lambda iid, items, cur: _dispatch_system(iid, system, display),
        )
        return "", closed
    return "", False


# ── Main entry point ─────────────────────────────────────────────────────────

def show_emulator_menu(system, display, fkbar, joystick_input, cfg, boot_status=None):
    """
    Show the emulator runtime menu.  Returns a dict:
      {'joystick_input': <new value or unchanged>}

    CPU stepping is paused implicitly because main() blocks here.

    The top level lists categories (Toggles / Storage / Display / System);
    each opens as its own sub-menu via _run_menu(), ESC going back one level.
    At the top level, ESC is intentionally disabled (2026-10-20, esc_exits=
    False below) -- only selecting Exit closes the whole menu back to the
    emulator. Reason: ESC is bound to the PB-1000's BRK key by default, and
    the main loop resumes CPU stepping the instant this function returns;
    if the user is still physically holding the ESC/BRK key at that exact
    moment (as naturally happens when using it to close the menu), that
    held key would be forwarded into the running BASIC program as a real
    BREAK. Submenus are unaffected (esc_exits defaults to True there) since
    backing out of one only returns to this still-open top-level loop.

    LCD and HDMI are exclusive (see pb1000.py's update_display()): while
    HDMI is the active output, the menu draws into an offscreen mirror
    (HDMIMirrorDisplay) instead of the physical LCD, and the physical LCD
    is left untouched — matching the game screen's own behavior.

    boot_status (main.py's BootStatusOverlay, or None if disabled/HDMI-
    only at boot) is optional only for callers that genuinely have none --
    main.py's own F7 call site always has one when the physical LCD is in
    play. Used solely to redraw the profile-name/clock header after the
    raw full-screen clear below (see that clear's comment): BootStatusOverlay
    paints that corner once at boot and never again on its own, so any
    full-screen clear after boot -- this one, or RAM Autosave's restore-
    prompt clear in main.py -- needs to explicitly repaint it afterward, per
    pb1000.ini's show_profile_name/show_clock settings (2026-10-14 bug
    report: profile name stayed blank after returning from this menu).
    """
    state = {
        'joystick_input': joystick_input,
        '_joy_saved': None,
    }

    hdmi_mirror = None
    if getattr(system, '_hdmi_enabled', False):
        hdmi_mirror = _create_hdmi_mirror(display, system.lcd)
        if hdmi_mirror is not None:
            display = hdmi_mirror
        # else: allocation failed (see hdmi_menu_mirror.create()) — fall
        # back to the real display for this session, same as HDMI disabled.

    try:
        _run_menu(
            display, "==  EMULATOR MENU  ==", "GUI+F7:open  Enter:select  Exit:close",
            _build_top_items,
            lambda iid, items, cur: _dispatch_top(iid, system, display, fkbar, cfg, state),
            esc_exits=False,
        )
    except MemoryError as e:
        # A submenu (e.g. System > CPU Status, which on-demand-compiles
        # emulator_menu_debug.py) can hit a MemoryError deep in a long-running
        # session — worse odds while the HDMI mirror buffer is also
        # resident. Close the menu rather than letting this propagate up
        # and crash the whole main loop (see main.py's MAIN LOOP EXCEPTION
        # handler — that's a last resort, not a substitute for handling
        # this here). emulator_menu_ram.py/emulator_menu_capture.py/
        # emulator_menu_debug.py (split from the former emulator_menu_ext.py
        # specifically to shrink this failure mode -- each lazy handler now
        # only compiles its own ~200-270 lines instead of all ~700 at once)
        # can hit the same thing.
        print("EMULATOR MENU: closing after MemoryError (%s)" % e)
        gc.collect()

    if hdmi_mirror is not None:
        # HDMI受信側は種別(ゲーム画面/メニュー/ベゼル)ごとに直前の表示領域を
        # 独立して覚えており、ゲーム画面側のフレームはメニュー側の領域を
        # 自動ではクリアしない(互いに重なって共存するレイヤーとして扱って
        # いるため — ../../hdmi_bridge_receiver/main.c参照)。そのため、メニューを
        # 閉じる際はここで明示的に全面黒のフレームを送り、メニューの残像が
        # 残らないようにする(_dirtyフラグに関わらず強制送信)。
        # 必ずforce_full_redraw()より先に送ること — メニューのキャンバスは
        # ベゼル/ゲーム画面より大きく、後から送ると黒塗りがベゼル/ゲーム画面
        # を上書きして消してしまう。
        hdmi_mirror.fill_rect(0, 0, hdmi_mirror.width, hdmi_mirror.height, 0x0000)
        hdmi_mirror.flush_if_dirty()
    else:
        # Restore display: clear menu area, then redraw bezel + LCD + FuncKeyBar
        display.fill_rect(0, 0, display.width, display.height, 0x0000)
        if fkbar is not None:
            try:
                fkbar.draw()
            except Exception:
                pass
        if boot_status is not None:
            try:
                boot_status.redraw_header()
            except Exception:
                pass

    system.force_full_redraw()

    return {'joystick_input': state['joystick_input']}
