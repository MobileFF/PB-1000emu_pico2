import time
from machine import Pin, SPI

class XPT2046:
    def __init__(self, spi, cs_pin, irq_pin=None, width=320, height=240,
                 x_min=200, y_min=200, x_max=3900, y_max=3900, baudrate=1000000,
                 swap_xy=False, x_inv=False, y_inv=False, lcd_baudrate=40000000):
        self.spi = spi
        self.cs = Pin(cs_pin, Pin.OUT)
        self.cs.value(1)

        self.irq = None
        if irq_pin is not None:
            self.irq = Pin(irq_pin, Pin.IN, Pin.PULL_UP)

        self.width = width
        self.height = height

        # Calibration defaults
        self.x_min = x_min
        self.y_min = y_min
        self.x_max = x_max
        self.y_max = y_max

        self.x_inv = x_inv
        self.y_inv = y_inv
        self.swap_xy = swap_xy
        self.baudrate = baudrate
        self.lcd_baudrate = lcd_baudrate  # Must match the display SPI baudrate

    def is_pressed(self):
        """Check if the screen is currently being touched."""
        if self.irq is not None:
            return self.irq.value() == 0
        return False

    def _transfer(self, cmd):
        # Temporarily lower SPI speed for touch controller
        self.spi.init(baudrate=self.baudrate)
        
        self.cs.value(0)
        # 1 byte cmd, 2 bytes response. Buffer for write_readinto.
        send = bytearray([cmd, 0, 0])
        recv = bytearray(3)
        self.spi.write_readinto(send, recv)
        self.cs.value(1)
        
        # Restore high speed for display (best effort, assuming shared bus)
        self.spi.init(baudrate=self.lcd_baudrate)
        
        # 12-bit result is typically across the last 2 bytes
        # Bit 7-0 of byte 1 and bit 7-4 of byte 2 (shifted down)
        return (recv[1] << 5 | recv[2] >> 3)

    def read_raw(self):
        """Read raw X, Y values in a single SPI transaction (avoids double baudrate-switch glitch)."""
        self.spi.init(baudrate=self.baudrate)
        self.cs.value(0)
        send = bytearray([0x90, 0x00, 0x00, 0xD0, 0x00, 0x00])
        recv = bytearray(6)
        self.spi.write_readinto(send, recv)
        self.cs.value(1)
        self.spi.init(baudrate=self.lcd_baudrate)
        y_raw = (recv[1] << 5 | recv[2] >> 3)
        x_raw = (recv[4] << 5 | recv[5] >> 3)
        return x_raw, y_raw

    def get_touch(self):
        """Get calibrated X, Y coordinates, or None if not touched."""
        if self.irq is not None and not self.is_pressed():
            return None
            
        x_raw, y_raw = self.read_raw()
        
        # Very low values typically indicate no touch (open circuit equivalent)
        if x_raw < 10 or y_raw < 10:
            return None
            
        if self.swap_xy:
            x_raw, y_raw = y_raw, x_raw

        x_c = self._map_val(x_raw, self.x_min, self.x_max, 0, self.width)
        y_c = self._map_val(y_raw, self.y_min, self.y_max, 0, self.height)
        
        if self.x_inv:
            x_c = self.width - x_c
        if self.y_inv:
            y_c = self.height - y_c
            
        # Clamp coordinates
        x_c = max(0, min(self.width, x_c))
        y_c = max(0, min(self.height, y_c))
        
        return x_c, y_c

    def _map_val(self, val, in_min, in_max, out_min, out_max):
        mapped = (val - in_min) * (out_max - out_min) // (in_max - in_min) + out_min
        return mapped
