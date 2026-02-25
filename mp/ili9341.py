from machine import Pin, SPI
import time
import struct

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
    def __init__(self, spi, cs, dc, rst, width=320, height=240, r=0):
        self.spi = spi
        self.cs = cs
        self.dc = dc
        self.rst = rst
        self.width = width
        self.height = height
        
        self.cs.init(self.cs.OUT, value=1)
        self.dc.init(self.dc.OUT, value=0)
        self.rst.init(self.rst.OUT, value=1)
        
        self.reset()
        self.init_display()

    def reset(self):
        self.rst.value(0)
        time.sleep_ms(50)
        self.rst.value(1)
        time.sleep_ms(50)

    def write_cmd(self, cmd):
        self.dc.value(0)
        self.cs.value(0)
        self.spi.write(bytearray([cmd]))
        self.cs.value(1)

    def write_data(self, data):
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
        self.write_data(bytearray([0x28])) 
        self.width, self.height = 320, 240
        
        self.write_cmd(DISPON)

    def set_window(self, x0, y0, x1, y1):
        self.write_cmd(CASET)
        self.write_data(struct.pack(">HH", x0, x1))
        self.write_cmd(PASET)
        self.write_data(struct.pack(">HH", y0, y1))
        self.write_cmd(RAMWR)

    def fill_rect(self, x, y, w, h, color):
        self.set_window(x, y, x+w-1, y+h-1)
        chunk_size = 1024
        total_pixels = w * h
        # RGB565 color (high byte, low byte)
        color_bytes = struct.pack(">H", color)
        # Create a buffer
        buf = color_bytes * (chunk_size // 2)
        
        self.dc.value(1)
        self.cs.value(0)
        for i in range(0, total_pixels, chunk_size // 2):
            write_len = min(len(buf), (total_pixels - i)*2)
            self.spi.write(buf[:write_len])
        self.cs.value(1)

    def clear(self, color=0):
        self.fill_rect(0, 0, self.width, self.height, color)
