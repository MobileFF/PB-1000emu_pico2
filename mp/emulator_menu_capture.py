"""
PB-1000 Emulator runtime menu — VRAM Save / Full Capture.

Split out of the former monolithic emulator_menu_ext.py (itself split
from emulator_menu.py) so that a screenshot doesn't have to pay the
compile cost of the unrelated RAM Save/Load (emulator_menu_ram.py) /
Hook Status / CPU Status (emulator_menu_debug.py) handlers too. Lazily
imported from
emulator_menu.py's _dispatch_storage() only when 'vram_save'/'full_capture'
is actually selected.

Unlike the other split-out menu modules, neither handler here draws to the
screen directly (each just writes files and returns a status string for
the caller to show), so this file has no dependency on emulator_menu.py's
draw helpers/palette or hdmi_menu_mirror at all.
"""


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
