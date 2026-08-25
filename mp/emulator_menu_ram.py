"""
PB-1000 Emulator runtime menu — RAM Save UI.

Split out of the former monolithic emulator_menu_ext.py (itself split
from emulator_menu.py) so that opening RAM Save doesn't have to pay
the compile cost of the unrelated VRAM Save / Full Capture / Hook Status /
CPU Status handlers too (now emulator_menu_capture.py / emulator_menu_
debug.py). Lazily imported from emulator_menu.py's _dispatch_storage()
only when 'ram_save' is actually selected.

RAM Load used to live here too (with a picker across every /sd/rams/
profile folder, and PB1000System.switch_profile() to hot-swap a different
profile's own ext/ modules and re-merge its pb1000.ini without a reboot).
Removed 2026-08-22: reaching a different profile's saved state now just
means System > Reboot Emulator (MCU), then picking it at the boot-time
profile picker -- that picker already calls load_state() for whichever
profile is chosen, so it's the same outcome without carrying
switch_profile()'s cross-profile ext/config hot-swap complexity in this
menu (switch_profile() itself was removed from pb1000.py along with its
only caller here).
"""

import time

from emulator_menu import (
    _draw_text, _confirm, _text_input,
    _BG, _FG, _HDR, _FTR, _SEL_BG, _SEL_FG, _WARN,
    _HDR_H, _FTR_H, _ROW_H,
)
from hdmi_menu_mirror import hdmi_flush

_RAM_BASE = "/sd/rams"


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
            if sc == 0x52:                                        # UP (wraps to bottom)
                cursor = cursor - 1 if cursor > 0 else len(all_items) - 1
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + max_vis:
                    scroll = max(0, cursor - max_vis + 1)
                _redraw()
            elif sc == 0x51:                                      # DOWN (wraps to top)
                cursor = cursor + 1 if cursor < len(all_items) - 1 else 0
                if cursor >= scroll + max_vis:
                    scroll = cursor - max_vis + 1
                elif cursor < scroll:
                    scroll = 0
                _redraw()
            elif sc == 0x28:
                if all_items[cursor] is None:
                    return 'new', None
                return 'existing', _RAM_BASE + "/" + all_items[cursor]
            elif sc == 0x29:
                return None, None
        hdmi_flush(display)
        time.sleep_ms(30)


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
    elif kind == 'existing':
        folder = path.rsplit("/", 1)[-1]
        if not _confirm(display, "Overwrite?", folder):
            return ""   # cancelled at confirmation

    try:
        system.save_state(path=path)
        return "RAM saved: " + path.rsplit("/", 1)[-1]
    except Exception as e:
        return f"!! RAM save error: {e}"
