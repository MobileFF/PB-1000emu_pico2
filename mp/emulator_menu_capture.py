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
    cvram = _lc.get_color_vram()  # 12288 B ref
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

        # ── 4. Color VRAM — PNG image (192×64, RGB332→RGB888) ──────────────
        # Streamed row-by-row straight into PngWriter (png_writer.py) --
        # no whole-image buffer, same heap footprint as the old PPM writer
        # this replaced (see that module's docstring for why PNG here is
        # uncompressed/"stored"-deflate rather than truly compressed).
        H_C = len(cvram) // W   # = 64
        def _png(f):
            from png_writer import PngWriter
            enc = PngWriter(f, W, H_C)
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
                enc.write_row(row)
            enc.close()
        _try("color_vram_%s.png" % ts, _png)

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
    as a single RGB888 PNG image, at the actual display resolution (e.g. 320x240
    or 480x320). Unlike VRAM Save (§`_do_vram_save`), this is not read back from
    the SPI display hardware — everything is recomposed in software from the same
    state the renderer uses (mono/color VRAM, bezel geometry, FuncKeyBar colors),
    since there is no full-screen framebuffer kept in RAM.

    The PNG is written row-by-row via png_writer.PngWriter, exactly like the
    PPM writer this replaced -- no whole-image buffer. See that module's
    docstring for why the IDAT data is uncompressed ("stored" deflate blocks)
    rather than truly compressed.

    Note: any transient status-bar text (e.g. the toast shown after a key
    press) is not reproduced, since it isn't part of the emulator's
    persistent state. The FuncKeyBar's key labels aren't reproduced either
    (only its per-key background colors and border, computed directly from
    funckey_bar.py's layout constants) -- funckey_bar.py draws its own
    labels via framebuf text glyphs, and re-rendering those pixel-for-pixel
    here isn't worth the extra code for a debug screenshot.

    2026-10-18 performance rewrite: an earlier version of this function built
    every one of the W*H output pixels (e.g. 480*320 = 153,600 on a 480x320
    ST7796) through a per-pixel Python-level loop, timed at around a minute
    on real hardware. The actual PB-1000 screen content only ever accounts for
    a small fraction of that (e.g. lw*lh = 288*48 at the default 1.5x scale
    and 32-dot mode -- under 10% of the total pixel count); the rest is the
    bezel's outer/mid rings, the black margin around everything, and the
    FuncKeyBar, all of which are either a single flat color or a small,
    purely-column-dependent pattern that repeats across many rows unchanged.
    This version still computes the same picture, just without redoing that
    non-content work pixel-by-pixel in Python:

      - Each row starts as a copy of a precomputed black row (one bytearray
        slice-copy, done in C, instead of a Python loop over every column);
        the bezel's outer-ring/mid-ring strips and the inner content area
        are then each written with ONE slice assignment per contiguous span
        (`_fill_span()`) rather than one Python-level iteration per pixel --
        there are at most a handful of such spans per row.
      - The FuncKeyBar (key fills, border, AND labels -- see the
        2026-10-19 note below) is rendered ONCE into an off-screen
        framebuf.FrameBuffer, the same size as the bar itself, rather than
        redrawn (or, as the labels were before 2026-10-19, simply omitted)
        on every one of its 42 rows.
      - Within the inner content area (the only part that still needs a
        per-pixel Python loop), the source-pixel column (`sx`) only depends
        on the output column, not the row -- so it's computed into a table
        ONCE up front (`_sx_table`) instead of via a multiply+divide on every
        single pixel of every single row. Consecutive output columns often
        map to the same `sx` under upscaling (e.g. the default 1.5x), so the
        loop also skips re-deriving the RGB value from VRAM/get_pixel() when
        `sx` hasn't changed since the previous column.

    None of this changes what ends up in the PNG -- only how many Python-
    level operations it takes to get there.

    2026-10-19: the FuncKeyBar portion of the capture was showing key
    fills and the border, but no labels -- true of this function ever
    since it was first written, not a regression from the rewrite above
    (the per-pixel loop it replaced never drew labels either; this
    function's own docstring used to call that out explicitly as an
    intentional cut corner "not worth the extra code for a debug
    screenshot"). Fixed by actually rendering the bar -- fills, border,
    AND labels -- through funckey_bar.py's own real drawing logic
    (`_build_fkbar_image()` below mirrors `FuncKeyBar.draw()` line for
    line, including the LCKEY nearest-neighbor-scaled font), just
    targeting a throwaway off-screen framebuf.FrameBuffer instead of the
    real display, then reading the result back pixel-by-pixel into the
    PNG's RGB888 rows. This is real per-pixel work again, but only across
    the FuncKeyBar's own fixed, small area (320x42 in the current
    layout) regardless of the overall display resolution -- nowhere near
    the cost the original whole-image rewrite above was about.
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

    from funckey_bar import (_IMG_W as FK_W, _IMG_H as FK_H,
                              _N_KEYS as FK_N_KEYS, _KEY_W as FK_KEY_W,
                              _KEY_COLORS as FK_KEY_COLORS,
                              _BORDER_COLOR as FK_BORDER_COLOR,
                              _BORDER_THICKNESS as FK_BORDER_T,
                              _FKEYS as FK_KEYS, _MEMO_IN_LINES as FK_MEMO_IN_LINES,
                              _LCKEY_CELL as FK_LCKEY_CELL, _TEXT_FG as FK_TEXT_FG)

    if fkbar is not None:
        fk_x, fk_y = fkbar._x_offset, fkbar._y_top
    else:
        # Mirrors the layout math in main.py (fkbar not always available, e.g.
        # if this is ever called before it's constructed).
        fk_x, fk_y = disp_x, disp_y + lh + 24

    display_on = system.lcd.display_on
    # Mirrors _pixel_color() in lcd_controller.c: color_vram is only used once
    # vdp_init_fill_done is set (post-boot 0xFF clear phase would otherwise
    # render as solid white). vdp_enabled alone is not enough.
    vdp_on = getattr(system.lcd, 'vdp_enabled', False)
    vdp_ready = bool(_lc.vdp_init_done())
    cvram = _lc.get_color_vram() if (vdp_on and vdp_ready) else None
    get_pixel = system.lcd.get_pixel  # hoisted: avoid re-resolving per pixel

    fg888    = _rgb565_to_888(system.lcd._color_fg)
    bgon888  = _rgb565_to_888(system.lcd._color_bg_on)
    bgoff888 = _rgb565_to_888(system.lcd._color_bg_off)
    outer888 = _rgb565_to_888(0x4228)  # draw_bezel() outer ring
    mid888   = _rgb565_to_888(0x8410)  # draw_bezel() mid ring
    outer_1px = bytes(outer888)
    mid_1px   = bytes(mid888)
    bgoff_1px = bytes(bgoff888)

    bezel_x0, bezel_x1 = disp_x - pad, disp_x + lw + pad
    bezel_y0, bezel_y1 = disp_y - pad, disp_y + lh + pad
    mid_x0, mid_x1 = disp_x - pad // 2, disp_x + lw + pad // 2
    mid_y0, mid_y1 = disp_y - pad // 2, disp_y + lh + pad // 2

    def _fill_span(row, x0, x1, color_1px):
        """Set row[x0:x1) (pixel coords) to one repeated RGB888 color via a
        single slice assignment -- clamped to [0, W), since geometry here
        is computed from config-driven offsets that could in principle
        place a span partly or fully off-screen."""
        x0 = 0 if x0 < 0 else x0
        x1 = W if x1 > W else x1
        if x1 > x0:
            row[x0 * 3:x1 * 3] = color_1px * (x1 - x0)

    # Source column (sx) depends only on the output column, never on the
    # row -- compute it once for the whole inner content span instead of
    # redoing the multiply+divide on every single pixel of every row (see
    # this function's docstring).
    _sx_table = []
    for i in range(lw):
        sx = (i * scale_den) // scale_num
        if sx >= lcd_w:
            sx = lcd_w - 1
        _sx_table.append(sx)

    def _fill_content(row, sy):
        """Inner lw-wide PB-1000 screen content for one row (sy already
        converted to source-pixel space). Reuses the previous column's
        RGB value whenever `sx` repeats (common under upscaling) instead
        of re-deriving it from VRAM/get_pixel() again.

        `i` (the index into _sx_table) is clamped to stay within row's
        own [0, W) bounds -- same reasoning as _fill_span()'s x0/x1 clamp:
        disp_x/lw come from config-driven offsets/scale that could in
        principle place part of the content span off-screen (e.g. disp_x
        < 0, or disp_x + lw > W)."""
        o0 = disp_x * 3
        i_lo = -disp_x if disp_x < 0 else 0
        i_hi = (W - disp_x) if (disp_x + lw > W) else lw
        prev_sx = -1
        r = g = b = 0
        if cvram is not None:
            base = sy * lcd_w
            for i in range(i_lo, i_hi):
                sx = _sx_table[i]
                if sx != prev_sx:
                    cb = cvram[base + sx]
                    rr = (cb >> 5) & 7; gg = (cb >> 2) & 7; bb = cb & 3
                    r = (rr << 5) | (rr << 2) | (rr >> 1)
                    g = (gg << 5) | (gg << 2) | (gg >> 1)
                    b = (bb << 6) | (bb << 4) | (bb << 2) | bb
                    prev_sx = sx
                o = o0 + i * 3
                row[o] = r; row[o + 1] = g; row[o + 2] = b
        else:
            for i in range(i_lo, i_hi):
                sx = _sx_table[i]
                if sx != prev_sx:
                    r, g, b = fg888 if get_pixel(sx, sy) else bgon888
                    prev_sx = sx
                o = o0 + i * 3
                row[o] = r; row[o + 1] = g; row[o + 2] = b

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

    path = base + "/full_%s.png" % ts

    def _blit_scaled_text(dst_fb, x, y, text, fg, bg, cell):
        """Exactly funckey_bar.py's own _draw_text_scaled(), minus the
        display-blit step: each 8x8 glyph is nearest-neighbor-sampled
        down to cell x cell (same index math), writing straight into
        `dst_fb` (an off-screen framebuf) instead of streaming to the
        real SPI panel. Used only for LCKEY, which doesn't fit _KEY_W at
        the normal 8px/char width -- see that function's own docstring."""
        import framebuf
        glyph_buf = bytearray(8 * 8 * 2)
        glyph_fb = framebuf.FrameBuffer(glyph_buf, 8, 8, framebuf.RGB565)
        cx = x
        for ch in text:
            glyph_fb.fill(bg)
            glyph_fb.text(ch, 0, 0, fg)
            for sy in range(cell):
                src_y = (sy * 8) // cell
                for sx in range(cell):
                    src_x = (sx * 8) // cell
                    dst_fb.pixel(cx + sx, y + sy, glyph_fb.pixel(src_x, src_y))
            cx += cell

    def _build_fkbar_image():
        """Renders the FuncKeyBar -- key fills, labels, AND the outer
        border -- into a throwaway off-screen framebuf.FrameBuffer the
        same size as the bar itself, by mirroring funckey_bar.py's own
        FuncKeyBar.draw() line for line against framebuf's native
        fill_rect()/text() instead of the real display -- see this
        function's 2026-10-19 docstring note for why (the per-pixel
        row-pattern approach this replaced couldn't show labels: label
        pixels vary by row *within* the bar, not just by column, since
        letter shapes differ row to row, so there's no small fixed set of
        row patterns to precompute the way the key fills/border alone
        allowed)."""
        import framebuf
        buf = bytearray(FK_W * FK_H * 2)
        fb = framebuf.FrameBuffer(buf, FK_W, FK_H, framebuf.RGB565)

        for i in range(FK_N_KEYS):
            kx = i * FK_KEY_W
            color = FK_KEY_COLORS[i]
            fb.fill_rect(kx + 1, 1, FK_KEY_W - 2, FK_H - 2, color)

            if i == 4:
                line1, line2 = FK_MEMO_IN_LINES
                gap = 2
                block_h = 8 + gap + 8
                ty1 = (FK_H - block_h) // 2
                ty2 = ty1 + 8 + gap
                for label, ty in ((line1, ty1), (line2, ty2)):
                    tw = len(label) * 8
                    tx = kx + max(0, (FK_KEY_W - tw) // 2)
                    fb.text(label, tx, ty, FK_TEXT_FG)
                continue

            label = FK_KEYS[i][1]
            ty = (FK_H - 8) // 2
            if i == 0:
                tw = len(label) * FK_LCKEY_CELL
                tx = kx + max(0, (FK_KEY_W - tw) // 2)
                _blit_scaled_text(fb, tx, ty, label, FK_TEXT_FG, color, FK_LCKEY_CELL)
            else:
                tw = len(label) * 8
                tx = kx + max(0, (FK_KEY_W - tw) // 2)
                fb.text(label, tx, ty, FK_TEXT_FG)

        t = FK_BORDER_T
        fb.fill_rect(0, 0, FK_W, t, FK_BORDER_COLOR)
        fb.fill_rect(0, FK_H - t, FK_W, t, FK_BORDER_COLOR)
        fb.fill_rect(0, 0, t, FK_H, FK_BORDER_COLOR)
        fb.fill_rect(FK_W - t, 0, t, FK_H, FK_BORDER_COLOR)
        return fb

    def _fk_row_to_rgb888(row, fk_fb, row_in_bar):
        """Converts one already-rendered FuncKeyBar row (row_in_bar, 0..
        FK_H-1) from the off-screen RGB565 framebuf into `row`'s RGB888
        bytes, at its real on-screen x position (fk_x) -- clamped to
        [0, W), same reasoning as _fill_span()'s x0/x1 clamp."""
        row[:] = black_row
        x_lo = 0 if fk_x < 0 else fk_x
        x_hi = W if fk_x + FK_W > W else fk_x + FK_W
        for x in range(x_lo, x_hi):
            r, g, b = _rgb565_to_888(fk_fb.pixel(x - fk_x, row_in_bar))
            o = x * 3
            row[o] = r; row[o + 1] = g; row[o + 2] = b

    try:
        from png_writer import PngWriter
        with open(path, "wb") as f:
            enc = PngWriter(f, W, H)
            row = bytearray(W * 3)
            black_row = bytes(W * 3)  # whole row = (0,0,0)
            fk_fb = _build_fkbar_image()
            for y in range(H):
                if fk_y <= y < fk_y + FK_H:
                    _fk_row_to_rgb888(row, fk_fb, y - fk_y)
                    enc.write_row(row)
                    continue

                if not (bezel_y0 <= y < bezel_y1):
                    enc.write_row(black_row)
                    continue

                in_mid_y = mid_y0 <= y < mid_y1
                in_inner_y = disp_y <= y < disp_y + lh

                row[:] = black_row
                if not in_mid_y:
                    _fill_span(row, bezel_x0, bezel_x1, outer_1px)
                else:
                    _fill_span(row, bezel_x0, mid_x0, outer_1px)
                    _fill_span(row, mid_x1, bezel_x1, outer_1px)
                    if not in_inner_y:
                        _fill_span(row, mid_x0, mid_x1, mid_1px)
                    else:
                        _fill_span(row, mid_x0, disp_x, mid_1px)
                        _fill_span(row, disp_x + lw, mid_x1, mid_1px)
                        if not display_on:
                            _fill_span(row, disp_x, disp_x + lw, bgoff_1px)
                        else:
                            sy = ((y - disp_y) * scale_den) // scale_num
                            if sy >= lcd_h:
                                sy = lcd_h - 1
                            _fill_content(row, sy)
                enc.write_row(row)
            enc.close()
        _gc.collect()
        return "Full capture: " + path.rsplit("/", 1)[-1]
    except Exception as e:
        _gc.collect()
        return "!! Full capture error: %s" % e
