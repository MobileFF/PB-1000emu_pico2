"""
PB-1000 LCD Controller - C Module Wrapper
Thin Python wrapper around the 'lcd_c' C module.
"""
import lcd_c
import machine

class LCDControllerC:
    """
    Drop-in replacement for LCDController backed by C module.
    VRAM management, command parsing, and character rendering run in C.
    """

    # Display dimensions (mirror the Python class)
    WIDTH = lcd_c.WIDTH
    HEIGHT = lcd_c.HEIGHT

    # Legacy direct-control commands
    CMD_DISPLAY_ON = 0x39
    CMD_DISPLAY_OFF = 0x38
    CMD_SET_PAGE = 0xB8
    CMD_SET_COL = 0x00
    CMD_SET_START = 0xC0

    # LCD.s command IDs
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
        self.display = display
        self.debug = bool(debug)
        self._pixel_size = 1
        self._spi_rendering = False
        self._hw_params = None  # (spi_id, cs, dc)
        self._current_cfg = (None, None, None)  # (x, y, scale)
        self._last_display_on = None

        # Initialize C module core
        lcd_c.init()
        lcd_c.set_debug(self.debug)

        # Load charset into C-side buffer
        self._load_charset_data()

        # Try automatic hardware discovery
        if self.display:
            if self._auto_setup_hw():
                if self.debug:
                    print("LCD C: Auto-setup hardware success")

    def _load_charset_data(self):
        """Loads charset.bin into C module."""
        for p in ("/roms/charset.bin", "roms/charset.bin"):
            try:
                with open(p, "rb") as f:
                    data = f.read()
                if len(data) >= 2048:
                    lcd_c.load_charset(data[:2048])
                    return
            except OSError:
                pass

    def _auto_setup_hw(self):
        """Extracts hardware pins from self.display and calls setup_display."""
        try:
            # Parse Machine.Pin(X) or Pin(X)
            def get_pin_num(pin_obj):
                s = str(pin_obj)
                # Handle "Pin(X)" or "Pin(GPIOX)" or "Pin(X, ...)"
                import re
                m = re.search(r'Pin\((\d+)', s)
                if m: return int(m.group(1))
                m = re.search(r'GPIO(\d+)', s)
                if m: return int(m.group(1))
                # Fallback: very basic split
                return int(s.split('(')[1].split(',')[0].strip())

            cs_pin = get_pin_num(self.display.cs)
            dc_pin = get_pin_num(self.display.dc)
            
            spi_str = str(self.display.spi)
            spi_id = 1 if 'SPI(1' in spi_str else 0
            
            self._hw_params = (spi_id, cs_pin, dc_pin)
            # Apply initial setup (render_to_display will update offsets)
            lcd_c.setup_display(spi_id, cs_pin, dc_pin, self._pixel_size, 0, 0)
            self._current_cfg = (0, 0, self._pixel_size)
            self._spi_rendering = True
            return True
        except Exception as e:
            if self.debug:
                print(f"LCD C: HW discovery failed: {e}")
            return False

    def render_to_display(self, x_offset=0, y_offset=0):
        """Refreshes the physical display. Synchronizes offsets to C-side first."""
        disp_on = bool(lcd_c.is_display_on())
        dirty = bool(lcd_c.is_dirty())
        if self._last_display_on is None or self._last_display_on != disp_on:
            print(f"[LCD_STATE] display_on={1 if disp_on else 0} dirty={1 if dirty else 0}")
            self._last_display_on = disp_on
        if not dirty:
            if not disp_on:
                print("[LCD_OFF] skip redraw: display_on=0 dirty=0")
            return

        # Synchronize offsets to C module if we have hardware control
        if self.display and self._hw_params:
            if (x_offset, y_offset, self._pixel_size) != self._current_cfg:
                sid, cs, dc = self._hw_params
                lcd_c.setup_display(sid, cs, dc, self._pixel_size, x_offset, y_offset)
                self._current_cfg = (x_offset, y_offset, self._pixel_size)
                self._spi_rendering = True

        # Optimized C-side rendering
        if self._spi_rendering:
            lcd_c.render()
            return

        # Fallback slow path (Headless or HW discovery failed)
        if not self.display: return
        if not disp_on:
            print("[LCD_OFF] fill background in Python fallback path")
            ps = self._pixel_size
            self.display.fill_rect(
                x_offset,
                y_offset,
                self.WIDTH * ps,
                self.HEIGHT * ps,
                0x8410,
            )
            lcd_c.clear_dirty()
            return
        ps = self._pixel_size
        for page in range(4):
            for col in range(self.WIDTH):
                byte = lcd_c.get_vram_byte(page * self.WIDTH + col)
                for bit in range(8):
                    if byte & (1 << bit):
                        self.display.fill_rect(x_offset + col*ps, y_offset + (page*8+bit)*ps, ps, ps, 0x0000)
                    else:
                        self.display.fill_rect(x_offset + col*ps, y_offset + (page*8+bit)*ps, ps, ps, 0xB5E6)
        lcd_c.clear_dirty()

    def lcd_ctrl(self, data): lcd_c.ctrl(data)
    def lcd_write(self, data): lcd_c.write(data)
    def lcd_read(self): return lcd_c.read()
    def clear(self): lcd_c.clear()
    def get_pixel(self, x, y): return lcd_c.get_pixel(x, y)
    
    @property
    def vram(self): return lcd_c.get_vram()
    @property
    def dirty(self): return lcd_c.is_dirty()
    @dirty.setter
    def dirty(self, v): 
        if not v: lcd_c.clear_dirty()
    @property
    def display_on(self): return lcd_c.is_display_on()
    @property
    def page(self): return 0
    @page.setter
    def page(self, v): lcd_c.set_page(v)
    @property
    def column(self): return 0
    @column.setter
    def column(self, v): lcd_c.set_column(v)

    def setup_display(self, spi_id, cs_pin, dc_pin, scale=1, x_offset=0, y_offset=0):
        """Manual/Override configuration."""
        self._hw_params = (spi_id, cs_pin, dc_pin)
        self._pixel_size = scale
        lcd_c.setup_display(spi_id, cs_pin, dc_pin, scale, x_offset, y_offset)
        self._current_cfg = (x_offset, y_offset, scale)
        self._spi_rendering = True

    def save_pbm(self, path):
        with open(path, "w", encoding="ascii") as f:
            f.write(f"P1\n{self.WIDTH} {self.HEIGHT}\n")
            for y in range(self.HEIGHT):
                row = ["1" if lcd_c.get_pixel(x, y) else "0" for x in range(self.WIDTH)]
                f.write(" ".join(row) + "\n")

    def dump_vram(self, start_addr=0x6201):
        vram = lcd_c.get_vram()
        print(f"LCD VRAM DUMP {start_addr:04X}- ({len(vram)} bytes)")
        for i in range(0, len(vram), 16):
            vals = " ".join(f"{vram[j]:02X}" for j in range(i, min(i+16, len(vram))))
            print(f"{start_addr + i:04X}: {vals}")
