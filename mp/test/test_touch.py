import machine
import time
from xpt2046 import XPT2046
from ili9341 import ILI9341
from pb1000 import draw_bezel

# Pin Definitions (Matching pb1000.py)
SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22
T_CS_PIN = 16
T_IRQ_PIN = 17

# Touch calibration offsets (same concept as pb1000.py)
TOUCH_X_OFFSET = 0
TOUCH_Y_OFFSET = -96

def main():
    print("--- PB-1000 Touch Panel Standalone Test ---")
    
    # 1. Initialize SPI
    print(f"Initializing SPI (ID={SPI_ID}, SCK={SCK_PIN}, MOSI={MOSI_PIN}, MISO={MISO_PIN})...")
    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    
    # 2. Initialize Display (required if they share the bus, to keep LCD CS high)
    # Even if we don't draw, we must ensure LCD doesn't interfere.
    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)
    
    # Init display just to put it in a clean state (Green background)
    print("Initializing ILI9341 display and setting background to Green...")
    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    # 0x07E0 is pure green in RGB565.
    # display.fill_rect(0, 0, 320, 240, 0x07E0)

    # Draw PB-1000 bezel overlay (matching pb1000.py behavior)
    draw_bezel(display, scale=1.5, x=16, y=40)
    
    # 3. Initialize Touch Panel
    print(f"Initializing Touch Panel (T_CS={T_CS_PIN}, T_IRQ={T_IRQ_PIN})...")
    try:
        touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN)
        print("Touch Panel initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize Touch Panel: {e}")
        return

    print("\n--- Calibration Options ---")
    print("If coordinates are wrong, you can try changing the properties in xpt2046.py")
    print("For example: touch.x_inv = True, touch.swap_xy = True, etc.")
    print("---------------------------\n")

    print("Entering Touch Polling Loop. Press Ctrl+C to stop.")
    print("Please touch the screen!...")
    
    try:
        while True:
            # We poll frequently to catch touches
            if touch.is_pressed():
                print("touch.is_pressed()")
                coords = touch.get_touch()
                if coords:
                    x, y = coords
                    raw_x, raw_y = touch.read_raw()
                    
                    # Convert to logical PB-1000 Key
                    # Y axis is inverted relative to the framebuffer coordinate space.
                    t_key = "None (Out of bounds)"
                    x += TOUCH_X_OFFSET
                    y += TOUCH_Y_OFFSET
                    if 16 <= x <= 304 and 40 <= y <= 104:
                        col = (x - 16) // 72
                        row = (y - 40) // 16
                        col = max(0, min(3, col))
                        row = max(0, min(3, row))
                        row = 3 - row  # invert Y axis mapping (top 0 <-> bottom 3)
                        t_idx = row * 4 + col + 1
                        t_key = f"T{t_idx}"

                    in_bounds = (16 <= x <= 304 and 40 <= y <= 104)
                    print(f"TOUCH DETECTED -> RAW:({raw_x:4d}, {raw_y:4d}) | MAPPED:X={x:3d}, Y={y:3d} | IN_BOUNDS={in_bounds} | PB-1000 KEY: {t_key}")
                    
                    # Anti-spam delay while touched
                    time.sleep_ms(100)
                else:
                    x_raw, y_raw = touch.read_raw()
                    print(f"coords is None | RAW:({x_raw:4d}, {y_raw:4d}) | IRQ={touch.irq.value()}")
            else:
                # Small delay to prevent tight loop
                time.sleep_ms(20)
                
    except KeyboardInterrupt:
        print("\nTest stopped by user.")

if __name__ == '__main__':
    main()
