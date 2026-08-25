from machine import Pin, SPI
import time

# ILI9341 Command Constants
SWRESET = 0x01
SLPOUT  = 0x11
DISPON  = 0x29
CASET   = 0x2A
PASET   = 0x2B
RAMWR   = 0x2C
MADCTL  = 0x36
PIXFMT  = 0x3A

class ILI9341:
    def __init__(self, spi, cs, dc, rst, width=320, height=240, r=0, rotation=0, sd_cs=None, t_cs=None):
        self.spi = spi
        self.cs = cs
        self.dc = dc
        self.rst = rst
        self.width = width
        self.height = height
        # See ST7796's __init__ docstring note: the SD card and touch
        # controller share this SPI bus, and lcd_controller.c's C-side
        # renderer already re-deasserts both before every transaction for
        # exactly this reason ("SPI Bus Arbitration").
        self._sd_cs = sd_cs
        self._t_cs = t_cs
        # 0 = normal landscape, 180 = physically flipped (board mounted upside down)
        self.rotation = 180 if rotation == 180 else 0

        self.cs.init(self.cs.OUT, value=1)
        self.dc.init(self.dc.OUT, value=0)
        self.rst.init(self.rst.OUT, value=1)

        # Reused across every write_cmd()/set_window() call instead of
        # allocating a fresh bytearray/struct.pack() result each time --
        # set_window() is called once per character by draw_text.py's
        # shared text helper, so on a per-character-cost basis this was a
        # real, measurable contributor to real-hardware heap churn (see
        # project memory: clock_overlay/mem_overlay [MEM_OVERLAY] logs).
        self._cmd_buf = bytearray(1)
        self._win_buf = bytearray(4)
        # fill_rect()'s solid-color chunk buffer, likewise persistent --
        # the old code built a fresh 1024-byte buffer (color_bytes * 512)
        # on every single call regardless of how many pixels were actually
        # needed, so a caller doing many tiny fills (PB1000System._draw_text()'s
        # hand-rolled 5x7 font plots one 1x1 fill_rect() per lit pixel) paid
        # a full 1024-byte allocation per pixel -- confirmed via real-hardware
        # [MEM_OVERLAY] logs as a much bigger cost than set_window()/write_cmd()
        # (see project memory). Refilled only when the color actually changes.
        self._fill_buf = bytearray(1024)
        self._fill_color = None

        self.reset()
        self.init_display()

    def reset(self):
        self.rst.value(0)
        time.sleep_ms(50)
        self.rst.value(1)
        time.sleep_ms(50)

    def _deselect_others(self):
        """Force the SD card's and touch controller's CS pins high before
        this driver asserts its own -- see the note in __init__."""
        if self._sd_cs is not None:
            self._sd_cs.value(1)
        if self._t_cs is not None:
            self._t_cs.value(1)

    def write_cmd(self, cmd):
        self._deselect_others()
        self.dc.value(0)
        self.cs.value(0)
        self._cmd_buf[0] = cmd
        self.spi.write(self._cmd_buf)
        self.cs.value(1)

    def write_data(self, data):
        self._deselect_others()
        self.dc.value(1)
        self.cs.value(0)
        self.spi.write(data)
        self.cs.value(1)

    def init_display(self):
        self.write_cmd(SWRESET)
        time.sleep_ms(150)
        self.write_cmd(SLPOUT)
        time.sleep_ms(255)
        
        self.write_cmd(PIXFMT)
        self.write_data(bytearray([0x55])) # 16-bit RGB565
        
        self.write_cmd(MADCTL)
        # 0x28 = Landscape (Column/Row swap), BGR color
        # 0xE8 = same landscape orientation with MX+MY also set -> physically
        #        rotated 180 degrees (matches a board mounted upside down).
        madctl = 0xE8 if self.rotation == 180 else 0x28
        self.write_data(bytearray([madctl]))
        self.width, self.height = 320, 240
        
        self.write_cmd(DISPON)

    def set_window(self, x0, y0, x1, y1):
        buf = self._win_buf
        self.write_cmd(CASET)
        buf[0] = (x0 >> 8) & 0xFF; buf[1] = x0 & 0xFF
        buf[2] = (x1 >> 8) & 0xFF; buf[3] = x1 & 0xFF
        self.write_data(buf)
        self.write_cmd(PASET)
        buf[0] = (y0 >> 8) & 0xFF; buf[1] = y0 & 0xFF
        buf[2] = (y1 >> 8) & 0xFF; buf[3] = y1 & 0xFF
        self.write_data(buf)
        self.write_cmd(RAMWR)

    def fill_rect(self, x, y, w, h, color):
        self.set_window(x, y, x+w-1, y+h-1)
        buf = self._fill_buf
        chunk_size = len(buf)
        total_pixels = w * h
        if color != self._fill_color:
            hi = (color >> 8) & 0xFF
            lo = color & 0xFF
            for i in range(0, chunk_size, 2):
                buf[i] = hi
                buf[i + 1] = lo
            self._fill_color = color

        self._deselect_others()
        self.dc.value(1)
        self.cs.value(0)
        for i in range(0, total_pixels, chunk_size // 2):
            write_len = min(chunk_size, (total_pixels - i)*2)
            self.spi.write(memoryview(buf)[:write_len])
        self.cs.value(1)

    def clear(self, color=0):
        self.fill_rect(0, 0, self.width, self.height, color)
