"""
PB-1000 Emulator runtime menu — heavier, less-frequently-used features.

Split out of emulator_menu.py so that opening the menu (and its common
items) doesn't have to pay the compile cost of these larger, less-often-used
handlers (RAM save/load UI, VRAM Save, Full Capture, Hook Status). Each is
lazily imported from emulator_menu.py's show_emulator_menu() only when its
specific menu item is actually selected.
"""

import time

from emulator_menu import (
    _draw_text, _confirm, _text_input,
    _BG, _FG, _HDR, _FTR, _SEL_BG, _SEL_FG, _WARN, _S_ON, _S_OFF,
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
            if sc == 0x52 and dirs:                     # UP (wraps to bottom)
                cursor = cursor - 1 if cursor > 0 else len(dirs) - 1
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + max_vis:
                    scroll = max(0, cursor - max_vis + 1)
                _redraw()
            elif sc == 0x51 and dirs:                   # DOWN (wraps to top)
                cursor = cursor + 1 if cursor < len(dirs) - 1 else 0
                if cursor >= scroll + max_vis:
                    scroll = cursor - max_vis + 1
                elif cursor < scroll:
                    scroll = 0
                _redraw()
            elif sc == 0x28:                            # EXE
                return _RAM_BASE + "/" + dirs[cursor]
            elif sc == 0x29:                            # BRK
                return None
        hdmi_flush(display)
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
        # load_state() already restores the full saved CPU state (PC included —
        # see its comment in pb1000.py). Do NOT call reset_emulator() or
        # power_on(force_reset=True) here: both force PC back to 0x0000,
        # which is exactly what used to make "RAM Load" restart the game from
        # boot instead of resuming from where RAM Save was taken. Plain
        # power_on() (no force_reset/force_power_on) re-runs the LCD control
        # port power-on sequence and SW input line without touching PC.
        system.power_on()
        # vram (the LCD hardware's own pixel buffer) isn't part of the save
        # file and load_state() never touches it -- see
        # refresh_lcd_from_ledtp()'s docstring in pb1000.py. Without this,
        # whatever was already on screen before RAM Load just stays there
        # (masking the gap here, unlike at boot, since there's no reset to
        # make it obvious) until the resumed program's own logic happens to
        # redraw something.
        system.refresh_lcd_from_ledtp()
        system.force_full_redraw()
        return "RAM loaded: " + selected.rsplit("/", 1)[-1]
    except Exception as e:
        return f"!! RAM load error: {e}"


def _do_vram_save(system):
    import lcd_c as _lc
    import os as _os
    import utime as _utime
    import gc as _gc
    W = 192

    # Free heap before allocating VRAM snapshots
    _gc.collect()

    num_pages = _lc.get_num_pages()  # 4 (32-dot) or 8 (64-dot)
    H = num_pages * 8                # 32 or 64 pixel rows

    vram_active = system.lcd.vram[:num_pages * W]            # active mono VRAM bytes only
    cvram = _lc.get_color_vram() if hasattr(_lc, 'get_color_vram') else None  # 12288 B ref
    edtop = bytes(system.ram[0x0100:0x0200])                 # 256 B EDTOP VRAM (0x6100-0x61FF)

    # Determine writable base directory (/sd/screenshots preferred)
    try:
        _os.listdir("/sd")
        base = "/sd/screenshots"
    except OSError:
        base = "/screenshots"
    try:
        _os.mkdir(base)
    except OSError:
        pass  # already exists

    # Timestamp suffix: YYYYMMDD_HHMMSS
    try:
        t = _utime.localtime()
        ts = "%04d%02d%02d_%02d%02d%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])
    except Exception:
        ts = "000000_000000"

    saved = []
    errors = []

    def _try(name, fn):
        try:
            with open(base + "/" + name, "wb") as f:
                fn(f)
            saved.append(name)
        except Exception:
            errors.append(name)

    # ── 1. Mono VRAM — raw binary (num_pages * W bytes) ─────────────────────
    _try("vram_%s.bin" % ts, lambda f: f.write(vram_active))

    # ── 2. Mono VRAM — PBM image (P4 binary, 192 × H) ───────────────────────
    def _pbm(f):
        f.write(("P4\n%d %d\n" % (W, H)).encode())
        row = bytearray(W // 8)   # 24 bytes per row
        for y in range(H):
            page, bit = y >> 3, y & 7
            for i in range(W // 8):
                p = 0
                for j in range(8):
                    p = (p << 1) | ((vram_active[page * W + i * 8 + j] >> bit) & 1)
                row[i] = p
            f.write(row)
    _try("vram_%s.pbm" % ts, _pbm)

    # ── 3. EDTOP VRAM — raw binary (256 bytes, 0x6100-0x61FF) ───────────────
    _try("edtop_%s.bin" % ts, lambda f: f.write(edtop))

    if cvram is not None:
        # ── 3. Color VRAM — raw binary (12,288 bytes) ───────────────────────
        _try("color_vram_%s.bin" % ts, lambda f: f.write(cvram))

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
        _try("color_vram_%s.ppm" % ts, _ppm)

    if not saved:
        return "!! VRAM save: no files written"
    msg = "VRAM saved: %d file%s" % (len(saved), "s" if len(saved) > 1 else "")
    if errors:
        msg += " (%d err)" % len(errors)
    return msg


def _rgb565_to_888(c):
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    return ((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2))


def _do_full_capture(system, display, fkbar=None):
    """Captures the *entire* physical LCD (bezel + PB-1000 screen + FuncKeyBar)
    as a single RGB888 PPM image, at the actual display resolution (e.g. 320x240
    or 480x320). Unlike VRAM Save (§`_do_vram_save`), this is not read back from
    the SPI display hardware — everything is recomposed in software from the same
    state the renderer uses (mono/color VRAM, bezel geometry, FuncKeyBar image),
    since there is no full-screen framebuffer kept in RAM.

    Note: any transient status-bar text (e.g. the toast shown after a key press)
    is not reproduced, since it isn't part of the emulator's persistent state.
    """
    import lcd_c as _lc
    import os as _os
    import utime as _utime
    import gc as _gc

    _gc.collect()

    W = getattr(display, 'width', 320)
    H = getattr(display, 'height', 240)

    scale_num = getattr(system.lcd, '_scale_num', 3)
    scale_den = getattr(system.lcd, '_scale_den', 2)
    disp_x = system._disp_x
    disp_y = system._disp_y
    lcd_w = 192
    lcd_h = getattr(system, '_lcd_height', 32)
    lw = (lcd_w * scale_num) // scale_den
    lh = (lcd_h * scale_num) // scale_den
    pad = 4

    from funckey_bar import _IMG_W as FK_W, _IMG_H as FK_H, _IMG_PATHS as FK_PATHS

    if fkbar is not None:
        fk_x, fk_y = fkbar._x_offset, fkbar._y_top
    else:
        # Mirrors the layout math in main.py (fkbar not always available, e.g.
        # if this is ever called before it's constructed).
        fk_x, fk_y = disp_x, disp_y + lh + 24

    # Resolve the FuncKeyBar image path without reading it into RAM — its
    # 26,880 bytes (320x42x2) are read one row at a time in the main loop
    # below instead, since holding the whole file in memory alongside the
    # PPM output buffers was enough to trigger MemoryError on this heap.
    fk_path = None
    for p in FK_PATHS:
        try:
            _os.stat(p)
            fk_path = p
            break
        except OSError:
            continue

    display_on = system.lcd.display_on
    # Mirrors _pixel_color() in lcd_controller.c: color_vram is only used once
    # vdp_init_fill_done is set (post-boot 0xFF clear phase would otherwise
    # render as solid white). vdp_enabled alone is not enough.
    vdp_on = getattr(system.lcd, 'vdp_enabled', False)
    vdp_ready = bool(_lc.vdp_init_done()) if hasattr(_lc, 'vdp_init_done') else True
    cvram = _lc.get_color_vram() if (vdp_on and vdp_ready and hasattr(_lc, 'get_color_vram')) else None

    fg888    = _rgb565_to_888(system.lcd._color_fg)
    bgon888  = _rgb565_to_888(system.lcd._color_bg_on)
    bgoff888 = _rgb565_to_888(system.lcd._color_bg_off)
    outer888 = _rgb565_to_888(0x4228)  # draw_bezel() outer ring
    mid888   = _rgb565_to_888(0x8410)  # draw_bezel() mid ring

    bezel_x0, bezel_x1 = disp_x - pad, disp_x + lw + pad
    bezel_y0, bezel_y1 = disp_y - pad, disp_y + lh + pad
    mid_x0, mid_x1 = disp_x - pad // 2, disp_x + lw + pad // 2
    mid_y0, mid_y1 = disp_y - pad // 2, disp_y + lh + pad // 2

    # Determine writable base directory (/sd/screenshots preferred), same
    # convention as VRAM Save.
    try:
        _os.listdir("/sd")
        base = "/sd/screenshots"
    except OSError:
        base = "/screenshots"
    try:
        _os.mkdir(base)
    except OSError:
        pass

    try:
        t = _utime.localtime()
        ts = "%04d%02d%02d_%02d%02d%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])
    except Exception:
        ts = "000000_000000"

    path = base + "/full_%s.ppm" % ts

    fk_row_buf = bytearray(FK_W * 2) if fk_path is not None else None
    fk_f = open(fk_path, 'rb') if fk_path is not None else None
    try:
        with open(path, "wb") as f:
            f.write(("P6\n%d %d\n255\n" % (W, H)).encode())
            row = bytearray(W * 3)
            black_row = bytes(W * 3)  # whole row = (0,0,0)
            for y in range(H):
                if fk_f is not None and fk_y <= y < fk_y + FK_H:
                    fk_f.seek((y - fk_y) * FK_W * 2)
                    fk_f.readinto(fk_row_buf)
                    for x in range(W):
                        if fk_x <= x < fk_x + FK_W:
                            i = (x - fk_x) * 2
                            c565 = (fk_row_buf[i] << 8) | fk_row_buf[i + 1]
                            r, g, b = _rgb565_to_888(c565)
                        else:
                            r = g = b = 0
                        o = x * 3
                        row[o] = r; row[o + 1] = g; row[o + 2] = b
                    f.write(row)
                    continue

                if not (bezel_y0 <= y < bezel_y1):
                    f.write(black_row)
                    continue

                in_mid_y = mid_y0 <= y < mid_y1
                in_inner_y = disp_y <= y < disp_y + lh
                for x in range(W):
                    if not (bezel_x0 <= x < bezel_x1):
                        r, g, b = 0, 0, 0
                    elif not (in_mid_y and mid_x0 <= x < mid_x1):
                        r, g, b = outer888
                    elif not (in_inner_y and disp_x <= x < disp_x + lw):
                        r, g, b = mid888
                    elif not display_on:
                        r, g, b = bgoff888
                    else:
                        sx = ((x - disp_x) * scale_den) // scale_num
                        sy = ((y - disp_y) * scale_den) // scale_num
                        if sx >= lcd_w: sx = lcd_w - 1
                        if sy >= lcd_h: sy = lcd_h - 1
                        if cvram is not None:
                            cb = cvram[sy * lcd_w + sx]
                            rr = (cb >> 5) & 7; gg = (cb >> 2) & 7; bb = cb & 3
                            r = (rr << 5) | (rr << 2) | (rr >> 1)
                            g = (gg << 5) | (gg << 2) | (gg >> 1)
                            b = (bb << 6) | (bb << 4) | (bb << 2) | bb
                        else:
                            r, g, b = fg888 if system.lcd.get_pixel(sx, sy) else bgon888
                    o = x * 3
                    row[o] = r; row[o + 1] = g; row[o + 2] = b
                f.write(row)
        _gc.collect()
        return "Full capture: " + path.rsplit("/", 1)[-1]
    except Exception as e:
        _gc.collect()
        return "!! Full capture error: %s" % e
    finally:
        if fk_f is not None:
            fk_f.close()


def _do_hook_status(system, display):
    """Read-only diagnostic screen listing every registered call_hook and
    mem_write_hook: address, owning module ("owner" passed to
    register_call_hook()/register_mem_write_hook()), and current enabled state.
    Scroll with UP/DOWN, BRK to close."""
    import hd61700

    call_hooks = system.list_call_hooks() if hasattr(system, 'list_call_hooks') else []
    mem_hooks = system.list_mem_write_hooks() if hasattr(system, 'list_mem_write_hooks') else []

    lines = []  # list of (text, color)
    lines.append(("-- Call Hooks --", _HDR))
    if call_hooks:
        for addr, owner, en in call_hooks:
            badge = "ON " if en else "OFF"
            color = _S_ON if en else _S_OFF
            lines.append(("&H%04X  %-13s %s" % (addr, owner[:13], badge), color))
    else:
        lines.append(("(none)", _FTR))

    lines.append(("", _FG))
    lines.append(("-- Mem Write Hooks --", _HDR))
    if mem_hooks:
        for addr, addr_end, owner, en in mem_hooks:
            badge = "ON " if en else "OFF"
            color = _S_ON if en else _S_OFF
            rng = ("&H%04X-%04X" % (addr, addr_end)) if addr_end != addr else ("&H%04X" % addr)
            lines.append(("%-11s %-13s %s" % (rng, owner[:13], badge), color))
    else:
        lines.append(("(none)", _FTR))

    W, H = display.width, display.height
    max_vis = max(1, (H - _HDR_H - _FTR_H) // _ROW_H)
    scroll = 0

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, "==  HOOK STATUS  ==", _HDR)
        _draw_text(display, 4, 14, "UP/DOWN:scroll  BRK:close", _FTR)
        y = _HDR_H
        vis_end = min(len(lines), scroll + max_vis)
        for i in range(scroll, vis_end):
            text, color = lines[i]
            if text:
                _draw_text(display, 4, y, text, color)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x29:  # BRK — close
                break
            elif sc == 0x52 and scroll > 0:               # UP
                scroll -= 1
                _redraw()
            elif sc == 0x51 and scroll + max_vis < len(lines):  # DOWN
                scroll += 1
                _redraw()
        hdmi_flush(display)


def _do_cpu_status(system, display):
    """Read-only diagnostic screen: PC/flags/UA/IA/IB/IE/irq_status/state,
    all 32 main registers, IX/IY/IZ/US/SS/KY, SX/SY/SZ, and a raw hex dump
    around PC.

    2026-08-11: added specifically so CPU state can be inspected via the menu
    when the game itself is hung (frozen display, no key input) — the menu
    is still reachable in that case since CPU stepping is paused while it's
    open (see module docstring in emulator_menu.py), and mpremote/REPL
    access can be unavailable if the device is deep in a tight C-level loop
    (Ctrl-C doesn't reliably interrupt hd61700.execute()). Also prints
    everything to the REPL (prefixed [CPU_STATUS]) in case a log capture is
    running, since screen space is limited even with scrolling. Scroll with
    UP/DOWN, BRK to close.

    2026-08-13: originally also included a disassembly section (debug.py's
    decode_basic()), dropped because importing debug.py on a long-running,
    fragmented heap could itself raise a MemoryError — the raw hex dump below
    covers the same "what's actually at PC" need without that import."""
    import hd61700
    c = system.cpu

    pc = c.get_pc()
    flags = c.get_flags()
    f_str = "".join((
        "Z" if flags & 0x80 else "-",
        "C" if flags & 0x40 else "-",
        "L" if flags & 0x20 else "-",
        "U" if flags & 0x10 else "-",
        "S" if flags & 0x08 else "-",
        "A" if flags & 0x04 else "-",
    ))
    ua = c.get_reg8(3)
    ia = c.get_reg8(4)
    ib = c.get_reg8(2)
    ie = c.get_reg8(5)
    have_irq_api = hasattr(c, 'get_irq_status') and hasattr(c, 'get_state')
    irq_status = c.get_irq_status() if have_irq_api else 0
    cpu_flow_state = c.get_state() if have_irq_api else 0

    lines = []  # list of (text, color)
    lines.append(("-- CPU Status --", _HDR))
    lines.append(("PC=&H%04X  UA=&H%02X" % (pc, ua), _FG))
    lines.append(("FLAGS=%s (&H%02X)" % (f_str, flags), _FG))
    lines.append(("IA=&H%02X IB=&H%02X IE=&H%02X" % (ia, ib, ie), _FG))
    if have_irq_api:
        lines.append(("IRQ_STATUS=&H%02X STATE=&H%02X" % (irq_status, cpu_flow_state), _FG))
    else:
        lines.append(("IRQ_STATUS=?? (old firmware)", _WARN))

    reg16 = [c.get_reg16(i) for i in range(6)]
    names16 = ("IX", "IY", "IZ", "US", "SS", "KY")
    lines.append(("", _FG))
    lines.append(("-- 16-bit Regs --", _HDR))
    # 3-per-line (IX IY IZ / US SS KY / SX SY SZ) so the whole screen
    # (16-bit regs + main regs) fits without scrolling — requested 2026-08-11.
    lines.append(("%s=%04X %s=%04X %s=%04X" % (
        names16[0], reg16[0], names16[1], reg16[1], names16[2], reg16[2]), _FG))
    lines.append(("%s=%04X %s=%04X %s=%04X" % (
        names16[3], reg16[3], names16[4], reg16[4], names16[5], reg16[5]), _FG))

    if hasattr(c, 'get_sreg'):
        sx, sy, sz = c.get_sreg(0), c.get_sreg(1), c.get_sreg(2)
        lines.append(("SX=%02X SY=%02X SZ=%02X" % (sx, sy, sz), _FG))

    lines.append(("", _FG))
    lines.append(("-- Main Regs $0-$31 --", _HDR))
    regs = [c.get_reg(i) for i in range(32)]
    for i in range(0, 32, 4):
        lines.append(("$%-2d=%02X $%-2d=%02X $%-2d=%02X $%-2d=%02X" % (
            i, regs[i], i + 1, regs[i + 1], i + 2, regs[i + 2], i + 3, regs[i + 3]), _FG))

    # Plain hex dump around PC — the ground-truth raw bytes actually in RAM
    # right now, for manually cross-checking against static .pbf analysis
    # (e.g. confirming a resumed PC really is sitting on the instruction
    # boundary static analysis predicted, or catching self-modified-code
    # divergence from the static image). Requested 2026-08-11 during the
    # RAM-Load-resume hang investigation.
    lines.append(("", _FG))
    lines.append(("-- Raw bytes @ PC-4..PC+15 --", _HDR))
    try:
        bank0 = ua & 0x03
        base_addr = (pc - 4) & 0xFFFF
        # Internal ROM (addr < 0x0C00) is fetched word-aligned — the CPU's
        # own fetch_addr is (addr<<1) in that range (see hd61700.c
        # read_internal_rom_byte()/hd61700.h fetch_addr comment) — and
        # hd61700.read_mem()'s addressing matches that same byte-offset
        # convention (c_mem_direct_read() in modhd61700.c indexes
        # rom0_buf[offset] directly for offset<0x2000). Above that boundary
        # addr and the byte offset are the same value, so this only matters
        # for low-ROM PCs.
        if base_addr < 0x0C00:
            byte_base = (base_addr << 1) & 0xFFFF
        else:
            byte_base = base_addr
        raw_bytes = [c.read_mem((byte_base + k) & 0xFFFF, bank0) for k in range(20)]
        for row in range(0, 20, 8):
            addr = (base_addr + row) & 0xFFFF
            chunk = raw_bytes[row:row + 8]
            marker = " <-PC" if addr <= pc < addr + 8 else ""
            lines.append(("&H%04X: %s%s" % (
                addr, " ".join("%02X" % b for b in chunk), marker), _FG))
    except Exception as e:
        lines.append(("raw dump failed: %s" % e, _WARN))

    print("[CPU_STATUS] ---- dump start ----")
    for text, _color in lines:
        print("[CPU_STATUS]", text)
    print("[CPU_STATUS] ---- dump end ----")

    W, H = display.width, display.height
    max_vis = max(1, (H - _HDR_H - _FTR_H) // _ROW_H)
    scroll = 0

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, "==  CPU STATUS  ==", _HDR)
        _draw_text(display, 4, 14, "UP/DOWN:scroll  BRK:close", _FTR)
        y = _HDR_H
        vis_end = min(len(lines), scroll + max_vis)
        for i in range(scroll, vis_end):
            text, color = lines[i]
            if text:
                _draw_text(display, 4, y, text, color)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x29:  # BRK — close
                break
            elif sc == 0x52 and scroll > 0:               # UP
                scroll -= 1
                _redraw()
            elif sc == 0x51 and scroll + max_vis < len(lines):  # DOWN
                scroll += 1
                _redraw()
        hdmi_flush(display)
        time.sleep_ms(30)
