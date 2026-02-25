
import machine
import time
from ili9341 import ILI9341
from lcd_controller import LCDController

# ---- Hardware Pin Configuration (Same as main.py) ----
SPI_ID   = 1
SCK_PIN  = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN   = 9
DC_PIN   = 8
RST_PIN  = 7
BL_PIN   = 22

def init_hw():
    spi = machine.SPI(SPI_ID, baudrate=40_000_000,
                      sck=machine.Pin(SCK_PIN),
                      mosi=machine.Pin(MOSI_PIN),
                      miso=machine.Pin(MISO_PIN))
    cs  = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc  = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    bl  = machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)
    return display

# Simple 5x7 Micro-Font for verification
FONT = {
    'P': [0x7F, 0x09, 0x09, 0x09, 0x06],
    'B': [0x7F, 0x49, 0x49, 0x49, 0x36],
    '-': [0x08, 0x08, 0x08, 0x08, 0x08],
    '1': [0x42, 0x7F, 0x40, 0x00, 0x00],
    '0': [0x3E, 0x41, 0x41, 0x41, 0x3E],
    ' ': [0x00, 0x00, 0x00, 0x00, 0x00],
    'O': [0x3E, 0x41, 0x41, 0x41, 0x3E],
    'K': [0x7F, 0x08, 0x14, 0x22, 0x41],
    '!': [0x00, 0x00, 0x5F, 0x00, 0x00],
}

def draw_char(lcd, x, y, char):
    if char not in FONT: return
    pattern = FONT[char]
    for col_idx, col_byte in enumerate(pattern):
        for bit in range(8):
            if col_byte & (1 << bit):
                # We can use the internal LCD logic or just write to vram
                # STLM-like behavior: 
                page = y // 8
                bit_pos = y % 8
                # This is a bit complex for arbitrary y, 
                # let's assume y is page-aligned (0, 8, 16, 24)
                lcd.page = page
                lcd.column = x + col_idx
                lcd.lcd_write(col_byte)

def test_display():
    print("Initializing test...")
    display = init_hw()
    # Clear physical display once at start
    display.fill_rect(0, 0, 320, 240, 0x0000)
    
    lcd = LCDController(display)
    # lcd init clears vram
    
    # Draw Bezel
    print("Drawing Bezel...")
    display.fill_rect(12, 36, 296, 72, 0x4228)
    display.fill_rect(14, 38, 292, 68, 0x8410)
    display.fill_rect(16, 40, 288, 64, 0xB5E6)

    print("Drawing text to VRAM...")
    # Write "PB-1000 OK!"
    text = "PB-1000 OK!"
    x = 10
    for char in text:
        # Each char is 5 pixels wide, + 1 pixel gap
        draw_char(lcd, x, 8, char) # Page 1 (y=8)
        x += 6
    
    print("Drawing status line...")
    # Draw a dotted line on Page 3
    for i in range(192):
        lcd.page = 3
        lcd.column = i
        lcd.lcd_write(0x55 if i % 2 == 0 else 0xAA)
        
    print("Updating physical display...")
    lcd.render_to_display(x_offset=16, y_offset=40)
    print("Test Complete.")

if __name__ == "__main__":
    test_display()
