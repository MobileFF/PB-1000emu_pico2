"""
Disk image hot-swap UI for PB-1000 emulator.
Presents a list of .img files on the ILI9341 display during emulation.
"""
import os
import time


# ---- helpers (mirrored from boot_session.py) ---------------------------------

def _swap16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


def _draw_text(display, x, y, text, fg, bg=0x0000):
    import framebuf
    W = display.width
    max_chars = (W - x) // 8
    text = text[:max_chars]
    if not text:
        return
    tw = len(text) * 8
    buf = bytearray(tw * 8 * 2)
    fb = framebuf.FrameBuffer(buf, tw, 8, framebuf.RGB565)
    fb.fill(_swap16(bg))
    fb.text(text, 0, 0, _swap16(fg))
    display.set_window(x, y, x + tw - 1, y + 7)
    display.write_data(buf)


# ---- disk image scanner ------------------------------------------------------

def list_disk_images(system):
    """
    Scan profile_dir, /sd/disks/, /sd/ for .img files.
    Returns list of (display_name, full_path, size_bytes).
    """
    dirs = []
    if getattr(system, 'profile_dir', None):
        dirs.append(system.profile_dir)
    if getattr(system, 'sd_mounted', False):
        dirs.append("/sd/disks")
        dirs.append("/sd")

    seen = set()
    results = []
    for d in dirs:
        try:
            for entry in os.listdir(d):
                if not entry.lower().endswith(".img"):
                    continue
                full = d.rstrip("/") + "/" + entry
                if full in seen:
                    continue
                seen.add(full)
                try:
                    size = os.stat(full)[6]
                except OSError:
                    size = 0
                results.append((entry, full, size))
        except OSError:
            pass
    return results


# ---- UI renderer -------------------------------------------------------------

_ROW_H   = 14
_MAX_VIS = 11   # rows visible at once (320x240 display, header=32px, footer=12px)

_COL_HL_BG  = 0x0210   # dark green — selected row background
_COL_HL_FG  = 0x07E0   # bright green — selected row text
_COL_FG     = 0xFFFF   # white — normal row text
_COL_BG     = 0x0000   # black — background
_COL_FOOTER = 0x7BEF   # light grey — footer text
_COL_HEADER = 0xFFFF   # white — header
_COL_CUR    = 0x07E0   # green — current disk name


def _draw_disk_ui(display, entries, cursor, scroll, current_name):
    W = display.width
    H = display.height

    display.fill_rect(0, 0, W, H, _COL_BG)

    _draw_text(display, 4, 2,  "=== DISK SELECT ===", _COL_HEADER)
    cur_label = ("Now:" + current_name)[:36] if current_name else "Now:(none)"
    _draw_text(display, 4, 13, cur_label, _COL_CUR)

    y = 30
    vis = min(_MAX_VIS, len(entries) - scroll)
    for i in range(vis):
        idx = scroll + i
        label, _ = entries[idx]
        if idx == cursor:
            display.fill_rect(0, y - 1, W, _ROW_H, _COL_HL_BG)
            _draw_text(display, 4, y, "> " + label, _COL_HL_FG, _COL_HL_BG)
        else:
            _draw_text(display, 4, y, "  " + label, _COL_FG, _COL_BG)
        y += _ROW_H

    _draw_text(display, 2, H - 10, "UP/DN:move  EXE:ok  BREAK:cancel", _COL_FOOTER)


# ---- main entry point --------------------------------------------------------

def select_disk_ui(display, images, current_path):
    """
    Interactive disk selection UI.
    Returns:
      str  — selected image path
      None — user chose EJECT
      False — user cancelled
    """
    # Build entry list: [EJECT] + image files
    entries = [("[EJECT]", None)]
    for name, path, size in images:
        kb = size // 1024
        label = f"{name}  {kb}KB"
        entries.append((label, path))

    # Pre-select current disk if mounted
    cursor = 0
    for i, (_, p) in enumerate(entries):
        if p == current_path:
            cursor = i
            break

    scroll = max(0, cursor - _MAX_VIS + 1)

    current_name = current_path.split("/")[-1] if current_path else None
    _draw_disk_ui(display, entries, cursor, scroll, current_name)

    try:
        import hd61700
        prev_sc = -1
        while True:
            sc = hd61700.get_last_key()

            # Edge detection: act only on new key press
            if sc != prev_sc:
                prev_sc = sc
                if sc == 0x52:      # UP
                    if cursor > 0:
                        cursor -= 1
                        if cursor < scroll:
                            scroll = cursor
                        _draw_disk_ui(display, entries, cursor, scroll, current_name)
                elif sc == 0x51:    # DOWN
                    if cursor < len(entries) - 1:
                        cursor += 1
                        if cursor >= scroll + _MAX_VIS:
                            scroll = cursor - _MAX_VIS + 1
                        _draw_disk_ui(display, entries, cursor, scroll, current_name)
                elif sc == 0x28:    # EXE — confirm
                    _, path = entries[cursor]
                    return path     # None = eject, str = new path
                elif sc == 0x29:    # BREAK — cancel
                    return False

            time.sleep_ms(30)

    except Exception as e:
        print(f"[DiskUI] error: {e}")
        return False
