"""
PB-1000 LCD Controller Emulation
Emulates the HD44102/HD61830 LCD controller used in the PB-1000.
The PB-1000 has a 192x32 pixel monochrome LCD (driven by multiple controllers).
"""
class LCDController:
    """
    PB-1000 LCD controller emulation.
    The display is 192x32 pixels organized as 6 columns x 4 pages (8 rows per page).
    Each controller handles 50x32 pixels; there are 4 IC chips.

    CPU communicates via:
     - lcd_ctrl(data): Set control register (RS=0)
     - lcd_write(data): Write display data (RS=1)
     - lcd_read() -> data: Read display data (RS=1)
    """

    # Display dimensions
    WIDTH = 192
    HEIGHT = 32

    # Legacy direct-control commands kept for compatibility with existing tests.
    CMD_DISPLAY_ON = 0x39
    CMD_DISPLAY_OFF = 0x38
    CMD_SET_PAGE = 0xB8   # | page (0-3)
    CMD_SET_COL = 0x00    # | column
    CMD_SET_START = 0xC0  # | start line

    # LCD.s command IDs (mode low nibble).
    LCDC_CMD_READ = 0x01
    LCDC_CMD_DRAW_BITIMAGE = 0x02
    LCDC_CMD_DRAW_CHAR = 0x03
    LCDC_CMD_DISPLAY_ON_OFF = 0x04
    LCDC_CMD_SPRITE_DEF_GRAPHIC = 0x06
    LCDC_CMD_SPRITE_DEF_CHAR = 0x07
    LCDC_CMD_SET_ROW_TOP_AND_WIDTH = 0x08
    LCDC_CMD_SPRITE_ON_OFF = 0x09
    LCDC_CMD_USER_CHAR_DEF = 0x0B
    LCDC_CMD_CONTRAST = 0x0C
    LCDC_CMD_CURSOR_FLASH = 0x0D
    LCDC_CMD_SET_SPRITE_POS = 0x0E

    def __init__(self, display=None, debug=False):
        """
        Args:
            display: ILI9341 display driver instance (or None for headless)
        """
        # VRAM: 192 columns x 4 pages = 768 bytes
        self.vram = bytearray(self.WIDTH * (self.HEIGHT // 8))
        self.page = 0           # Current page (0-3)
        self.column = 0         # Current column (0-191)
        self.start_line = 0     # Display start line
        self.display_on = False
        self.ctrl_reg = 0       # Last control register value
        self.display = display  # ILI9341 driver
        self.debug = bool(debug)
        self._color_bg_on = 0xB5E6
        self._color_bg_off = 0x8410
        self._scale_num = 1
        self._scale_den = 1
        self.clear()            # Ensure VRAM is empty on start
        self.dirty = True       # Flag: VRAM changed, needs redraw
        self._pixel_size = 1    # Scale factor for ILI9341 (1:1 for 192x32)
        self._chip_state = [
            {"x": 0, "y": 0, "mode": self.LCDC_CMD_DRAW_BITIMAGE},
            {"x": 0, "y": 0, "mode": self.LCDC_CMD_DRAW_BITIMAGE},
        ]
        # Horizontal write-direction correction toggle for DRAW_BITIMAGE path.
        # Default off; enable only if panel appears mirrored.
        self._x_mirror = False
        self._active_chip = 0
        self._char_width = 6
        self._draw_bitimage_reverse = False
        self._selected_ce = 0x00
        self._op_command = False
        self._cmd_buf = []
        self._cmd_expected = 0
        self._legacy_mode = False
        self._charset = self._load_charset()

    def _set_display_on(self, enabled):
        val = bool(enabled)
        if self.display_on == val:
            return
        self.display_on = val
        # Force full redraw when LCD ON/OFF state changes.
        self.dirty = True

    def set_bg_colors(self, on_bg, off_bg):
        """Set LCD background colors (RGB565): ON-state and OFF-state."""
        self._color_bg_on = int(on_bg) & 0xFFFF
        self._color_bg_off = int(off_bg) & 0xFFFF
        self.dirty = True

    def set_display_scale(self, scale):
        """Set display scale. Supported: 1.0 and 1.5."""
        if scale == 1.5:
            self._scale_num = 3
            self._scale_den = 2
        else:
            self._scale_num = 1
            self._scale_den = 1
        self.dirty = True

    def _load_charset(self):
        # Preferred path in this project layout.
        for p in ("/roms/charset.bin", "roms/charset.bin"):
            try:
                with open(p, "rb") as f:
                    data = f.read()
                if len(data) >= 2048:
                    return data[:2048]
            except OSError:
                pass
        return None

    def lcd_ctrl(self, data):
        """CPU writes PPO value (LCD port control register)."""
        if self.debug:
            op = 1 if (data & 0x01) else 0
            ce1 = 1 if (data & 0x02) else 0
            ce2 = 1 if (data & 0x04) else 0
            print(f"LCD CTRL: 0x{data:02X} (OP={op} CE1={ce1} CE2={ce2})")
        # Keep legacy direct-control behavior used by older tests/tools.
        if data == self.CMD_DISPLAY_ON:
            self._set_display_on(True)
            self._legacy_mode = True
            return
        if data == self.CMD_DISPLAY_OFF:
            self._set_display_on(False)
            self._legacy_mode = True
            return
        if (data & 0xF8) == self.CMD_SET_PAGE:
            self.page = data & 0x03
            self._legacy_mode = True
            return
        if data < 0x40:
            self.column = data % self.WIDTH
            self._legacy_mode = True
            return

        self.ctrl_reg = data
        self._selected_ce = (data >> 1) & 0x03
        self._op_command = bool(data & 0x01)
        self._cmd_buf = []
        self._cmd_expected = 0
        self._legacy_mode = False

    def _command_length(self, mode_byte):
        cmd = mode_byte & 0x0F
        if cmd in (
            self.LCDC_CMD_READ,
            self.LCDC_CMD_DRAW_BITIMAGE,
            self.LCDC_CMD_DRAW_CHAR,
            self.LCDC_CMD_SPRITE_DEF_GRAPHIC,
            self.LCDC_CMD_SPRITE_DEF_CHAR,
            self.LCDC_CMD_USER_CHAR_DEF,
            self.LCDC_CMD_CONTRAST,
            self.LCDC_CMD_SET_SPRITE_POS,
        ):
            return 3
        return 1

    def _mode_name(self, mode):
        names = {
            self.LCDC_CMD_READ: "READ",
            self.LCDC_CMD_DRAW_BITIMAGE: "DRAW_BITIMAGE",
            self.LCDC_CMD_DRAW_CHAR: "DRAW_CHAR",
            self.LCDC_CMD_DISPLAY_ON_OFF: "DISPLAY_ON_OFF",
            self.LCDC_CMD_SPRITE_DEF_GRAPHIC: "SPRITE_DEF_GRAPHIC",
            self.LCDC_CMD_SPRITE_DEF_CHAR: "SPRITE_DEF_CHAR",
            self.LCDC_CMD_SET_ROW_TOP_AND_WIDTH: "SET_ROW_TOP_AND_WIDTH",
            self.LCDC_CMD_SPRITE_ON_OFF: "SPRITE_ON_OFF",
            self.LCDC_CMD_USER_CHAR_DEF: "USER_CHAR_DEF",
            self.LCDC_CMD_CONTRAST: "CONTRAST",
            self.LCDC_CMD_CURSOR_FLASH: "CURSOR_FLASH",
            self.LCDC_CMD_SET_SPRITE_POS: "SET_SPRITE_POS",
        }
        return names.get(mode & 0x0F, "UNKNOWN")

    def _mode_to_chip(self, mode_byte):
        return 1 if (mode_byte & 0x10) else 0

    def _apply_lcdc_command(self, cmd):
        mode = cmd[0]
        cmd_id = mode & 0x0F
        chip = self._mode_to_chip(mode)
        self._active_chip = chip
        st = self._chip_state[chip]
        st["mode"] = cmd_id
        if self.debug:
            print(
                f"LCD CMD: mode=0x{mode:02X}({self._mode_name(mode)}) "
                f"cmd=0x{cmd_id:02X}({self._mode_name(cmd_id)}) chip={chip}"
            )

        if cmd_id == self.LCDC_CMD_DISPLAY_ON_OFF:
            self._set_display_on(mode & 0x10)
            return

        if cmd_id == self.LCDC_CMD_SET_ROW_TOP_AND_WIDTH:
            width_sel = (mode >> 4) & 0x03
            # 00:8px, 01:7px, 10:6px(default), 11:5px
            self._char_width = (8, 7, 6, 5)[width_sel]
            return

        if cmd_id == self.LCDC_CMD_CURSOR_FLASH:
            return

        if len(cmd) >= 3:
            col = cmd[1] & 0xFF
            row = cmd[2] & 0x03
            block_off = 48 if (col & 0x80) else 0
            col7 = col & 0x7F
            st["y"] = row
            if cmd_id == self.LCDC_CMD_DRAW_CHAR:
                st["x"] = block_off + ((col7 // 16) * self._char_width)
            else:
                st["x"] = block_off + (col7 // 2)
            if self.debug:
                print(f"LCD CMD ADDR: chip={chip} x={st['x']} y={st['y']} raw_col={col} raw_row={row}")

    def _write_vram_pixel_byte(self, chip, x_local, y_page, data):
        if not (0 <= y_page < 4):
            return
        if not (0 <= x_local < 96):
            return
        if self._x_mirror:
            x_local = 95 - x_local
        x = x_local + (96 if chip else 0)
        off = y_page * self.WIDTH + x
        if 0 <= off < len(self.vram):
            self.vram[off] = data
            self.dirty = True

    def _read_vram_pixel_byte(self, chip, x_local, y_page):
        if not (0 <= y_page < 4):
            return 0xFF
        if not (0 <= x_local < 96):
            return 0xFF
        if self._x_mirror:
            x_local = 95 - x_local
        x = x_local + (96 if chip else 0)
        off = y_page * self.WIDTH + x
        if 0 <= off < len(self.vram):
            return self.vram[off]
        return 0xFF

    def _advance_xy(self, st, draw_char=False, reverse=False):
        step = self._char_width if draw_char else 1
        if reverse and not draw_char:
            st["x"] -= step
            if st["x"] < 0:
                st["x"] = 95
                st["y"] = (st["y"] + 1) & 0x03
        else:
            st["x"] += step
            if st["x"] >= 96:
                st["x"] = 0
                st["y"] = (st["y"] + 1) & 0x03

    def _reverse_bits8(self, v):
        v &= 0xFF
        r = 0
        for _ in range(8):
            r = (r << 1) | (v & 1)
            v >>= 1
        return r

    def _char_code_to_bitmap(self, data):

        # PB-1000 character data is 4-bit swapped in many BIOS paths.
        code = ((data & 0x0F) << 4) | (data >> 4)
        width = self._char_width
        if width < 5:
            width = 5
        if width > 8:
            width = 8

        if self._charset:
            base = (code & 0xFF) * 8
            g = self._charset[base:base + 8]
            # charset.bin appears to be 8-byte glyph rows with 1-col margins.
            if width <= 6:
                cols = list(g[1:1 + width])
            else:
                cols = list(g[:width])
            # PB-1000 glyph orientation in charset.bin needs vertical bit flip for current VRAM mapping.
            cols = [self._reverse_bits8(c) for c in cols]
            while len(cols) < width:
                cols.append(0)
            return cols

        # Fallback if charset is unavailable.
        return [0] * width

    def lcd_write(self, data):
        """CPU writes via STL. Behavior depends on PPO OP bit."""
        if self.debug:
            print(
                f"LCD WRITE: 0x{data:02X} "
                f"(legacy P{self.page}:C{self.column}, OP={1 if self._op_command else 0}, CE=0x{self._selected_ce:01X})"
            )
        if self._legacy_mode:
            if self.page < 4 and self.column < self.WIDTH:
                offset = self.page * self.WIDTH + self.column
                if offset < len(self.vram):
                    self.vram[offset] = data
                    self.dirty = True
            self.column = (self.column + 1) % self.WIDTH
            return

        if self._op_command:
            if not self._cmd_buf:
                self._cmd_expected = self._command_length(data)
            self._cmd_buf.append(data)
            if len(self._cmd_buf) >= self._cmd_expected:
                self._apply_lcdc_command(self._cmd_buf[:self._cmd_expected])
                self._cmd_buf = []
                self._cmd_expected = 0
            return

        # Data-RAM mode (OP=0): route to the chip selected by the last LCDC command.
        # CE bits still gate the physical write path.
        active = False
        chip = self._active_chip
        if self._selected_ce & (1 << chip):
            st = self._chip_state[chip]
            mode = st["mode"]
            if self.debug:
                print(
                    f"LCD DATA ROUTE: chip={chip} mode=0x{mode:02X}"
                    f"({self._mode_name(mode)}) x={st['x']} y={st['y']}"
                )
            if mode == self.LCDC_CMD_DRAW_CHAR:
                if self.debug:
                    print("LCD DATA MODE: DRAW_CHAR")
                glyph = self._char_code_to_bitmap(data)
                for i, col_byte in enumerate(glyph):
                    self._write_vram_pixel_byte(chip, st["x"] + i, st["y"], col_byte)
                self._advance_xy(st, draw_char=True)
                active = True
            else:
                pixel_byte = data
                if mode == self.LCDC_CMD_DRAW_BITIMAGE:
                    # DRAW_BITIMAGE uses opposite vertical bit order on the PB-1000 LCD path.
                    pixel_byte = self._reverse_bits8(data)
                self._write_vram_pixel_byte(chip, st["x"], st["y"], pixel_byte)
                self._advance_xy(
                    st,
                    draw_char=False,
                    reverse=(mode == self.LCDC_CMD_DRAW_BITIMAGE and self._draw_bitimage_reverse),
                )
                active = True

        if active:
            return

        # Legacy fallback.
        if self.page < 4 and self.column < self.WIDTH:
            offset = self.page * self.WIDTH + self.column
            if offset < len(self.vram):
                self.vram[offset] = data
                self.dirty = True
        self.column = (self.column + 1) % self.WIDTH

    def lcd_read(self):
        """CPU reads via LDL (typically in data-RAM mode after READ command)."""
        chip = self._active_chip
        if self._selected_ce & (1 << chip):
            st = self._chip_state[chip]
            raw = self._read_vram_pixel_byte(chip, st["x"], st["y"])
            self._advance_xy(
                st,
                draw_char=False,
                reverse=(st["mode"] == self.LCDC_CMD_DRAW_BITIMAGE and self._draw_bitimage_reverse),
            )
            # LCD.s notes readback nibble swap.
            return ((raw & 0x0F) << 4) | ((raw >> 4) & 0x0F)

        if self.page < 4 and self.column < self.WIDTH:
            offset = self.page * self.WIDTH + self.column
            if offset < len(self.vram):
                val = self.vram[offset]
                self.column = (self.column + 1) % self.WIDTH
                return val
        return 0xFF

    def get_pixel(self, x, y):
        """Get pixel state at (x, y). Returns True if pixel is on."""
        if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
            page = y // 8
            bit = y % 8
            offset = page * self.WIDTH + x
            # HD44102/HD61830: Bit 0 is usually the top pixel of the page
            return bool(self.vram[offset] & (1 << bit))
        return False

    def render_to_display(self, x_offset=0, y_offset=0):
        """Render VRAM to ILI9341 display."""
        if not self.display or not self.dirty:
            return

        out_w = (self.WIDTH * self._scale_num) // self._scale_den
        out_h = (self.HEIGHT * self._scale_num) // self._scale_den
        color_on  = 0x0000  # Black pixels (LCD on)
        color_off = self._color_bg_on
        color_lcd_off = self._color_bg_off
        if not self.display_on:
            self.display.fill_rect(
                x_offset,
                y_offset,
                out_w,
                out_h,
                color_lcd_off,
            )
            self.dirty = False
            return

        for dy in range(out_h):
            sy = (dy * self._scale_den) // self._scale_num
            page = sy >> 3
            bit = sy & 0x07
            base = page * self.WIDTH
            for dx in range(out_w):
                sx = (dx * self._scale_den) // self._scale_num
                byte = self.vram[base + sx]
                pixel_on = bool(byte & (1 << bit))
                color = color_on if pixel_on else color_off
                self.display.fill_rect(x_offset + dx, y_offset + dy, 1, 1, color)

        self.dirty = False

    def clear(self):
        """Clear all VRAM."""
        for i in range(len(self.vram)):
            self.vram[i] = 0
        self.page = 0
        self.column = 0
        self.dirty = True

    def set_x_mirror(self, enabled):
        """Enable/disable horizontal mirroring in controller memory mapping."""
        self._x_mirror = bool(enabled)

    def set_draw_bitimage_reverse(self, enabled):
        """Enable/disable reverse X stepping for DRAW_BITIMAGE mode."""
        self._draw_bitimage_reverse = bool(enabled)

    def dump_vram(self, start_addr=0x6201, bytes_per_line=16):
        """Dump internal LCD VRAM in the same hex-line style as PB-1000 RAM dumps."""
        total = len(self.vram)
        end_addr = start_addr + total - 1
        print(f"LCD VRAM DUMP {start_addr:04X}-{end_addr:04X} ({total} bytes)")

        offset = 0
        while offset < total:
            line_vals = []
            line_end = offset + bytes_per_line
            if line_end > total:
                line_end = total
            i = offset
            while i < line_end:
                line_vals.append(f"{self.vram[i]:02X}")
                i += 1
            print(f"{start_addr + offset:04X}: {' '.join(line_vals)}")
            offset += bytes_per_line

    def to_pbm_text(self):
        """Return current LCD image as ASCII PBM (P1) text."""
        lines = [f"P1\n{self.WIDTH} {self.HEIGHT}"]
        y = 0
        while y < self.HEIGHT:
            row = []
            x = 0
            while x < self.WIDTH:
                row.append("1" if self.get_pixel(x, y) else "0")
                x += 1
            lines.append(" ".join(row))
            y += 1
        return "\n".join(lines) + "\n"

    def save_pbm(self, path):
        """Save current LCD image as an ASCII PBM (P1) file."""
        with open(path, "w", encoding="ascii") as f:
            f.write(self.to_pbm_text())

    def to_xpm_text(self):
        """Return current LCD image as XPM text."""
        lines = [
            "/* XPM */",
            "static char *pb1000_lcd_xpm[] = {",
            f"\"{self.WIDTH} {self.HEIGHT} 2 1\",",
            "\". c #FFFFFF\",",
            "\"X c #000000\",",
        ]

        y = 0
        while y < self.HEIGHT:
            row = []
            x = 0
            while x < self.WIDTH:
                row.append("X" if self.get_pixel(x, y) else ".")
                x += 1
            suffix = "," if y != (self.HEIGHT - 1) else ""
            lines.append(f"\"{''.join(row)}\"{suffix}")
            y += 1

        lines.append("};")
        return "\n".join(lines) + "\n"

    def save_xpm(self, path):
        """Save current LCD image as an XPM file."""
        with open(path, "w", encoding="ascii") as f:
            f.write(self.to_xpm_text())
