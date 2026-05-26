import machine
import time

from funckey_bar import FuncKeyBar, hit_test
from ili9341 import ILI9341
from xpt2046 import XPT2046


SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22
SD_CS_PIN = 15
T_CS_PIN = 16
T_IRQ_PIN = 17

DISPLAY_W = 320
DISPLAY_H = 240
LCD_X = 16
LCD_Y = 40
LCD_SCALE = 1.5
FUNCKEY_TOUCH_X_OFFSET = 0
FUNCKEY_TOUCH_Y_OFFSET = 24
FKEY_GAP = 24


class ProbeSystem:
    def __init__(self, touch):
        self.touch = touch
        self.funckey_touch_x_offset = FUNCKEY_TOUCH_X_OFFSET
        self.funckey_touch_y_offset = FUNCKEY_TOUCH_Y_OFFSET
        self._last_status = None

    def press_key(self, key):
        print("ACTION press_key", key)

    def release_key(self, key):
        print("ACTION release_key", key)

    def set_status(self, label):
        if label != self._last_status:
            print("STATUS", label)
            self._last_status = label


def _draw_lcd_bezel(display):
    lw = int(192 * LCD_SCALE)
    lh = int(32 * LCD_SCALE)
    padding = 4
    display.fill_rect(LCD_X - padding, LCD_Y - padding, lw + padding * 2, lh + padding * 2, 0x4228)
    display.fill_rect(LCD_X - padding // 2, LCD_Y - padding // 2, lw + padding, lh + padding, 0x8410)
    display.fill_rect(LCD_X, LCD_Y, lw, lh, 0xB5E6)


def _log_touch(touch, y_top):
    coords = touch.get_touch()
    raw_x, raw_y = touch.read_raw()
    if coords is None:
        print("TOUCH coords=None RAW=({:4d},{:4d})".format(raw_x, raw_y))
        return None

    x0, y0 = coords
    x = x0 + FUNCKEY_TOUCH_X_OFFSET
    y = y0 + FUNCKEY_TOUCH_Y_OFFSET
    hit = hit_test(x, y, y_top)

    if hit is None:
        print(
            "TOUCH RAW=({:4d},{:4d}) MAP0=({:3d},{:3d}) MAP=({:3d},{:3d}) "
            "FKBAR=False".format(raw_x, raw_y, x0, y0, x, y)
        )
        return coords

    col, key_coord, label = hit
    print(
        "TOUCH RAW=({:4d},{:4d}) MAP0=({:3d},{:3d}) MAP=({:3d},{:3d}) "
        "FKBAR=True COL={} LABEL={} KEY={}".format(raw_x, raw_y, x0, y0, x, y, col, label, key_coord)
    )
    return coords


def main():
    print("--- FuncKeyBar standalone touch test ---")
    print("Ctrl+C to stop.")
    print("funckey offset x={} y={}".format(FUNCKEY_TOUCH_X_OFFSET, FUNCKEY_TOUCH_Y_OFFSET))

    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )

    machine.Pin(CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(T_CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(
        spi,
        machine.Pin(CS_PIN, machine.Pin.OUT),
        machine.Pin(DC_PIN, machine.Pin.OUT),
        machine.Pin(RST_PIN, machine.Pin.OUT),
        width=DISPLAY_W,
        height=DISPLAY_H,
    )
    display.fill_rect(0, 0, DISPLAY_W, DISPLAY_H, 0x0000)
    _draw_lcd_bezel(display)

    y_top = LCD_Y + int(32 * LCD_SCALE) + FKEY_GAP
    bar = FuncKeyBar(display, y_top)
    bar.draw()

    print("FuncKeyBar y={}..{} x=0..319".format(y_top, y_top + 42 - 1))
    print("Columns: 0=LCKEY 1=MENU 2=CAL 3=MEMO 4=MEMO IN 5=IN 6=OUT 7=CALC")

    touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN, x_min=325, x_max=3850)
    system = ProbeSystem(touch)

    count = 0
    was_pressed = False
    try:
        while True:
            if touch.is_pressed():
                coords = _log_touch(touch, y_top)
                bar.poll_coords(system, coords)
                was_pressed = True
                count = 0
                time.sleep_ms(100)
            else:
                if was_pressed:
                    bar.release(system)
                    was_pressed = False
                count += 1
                if count >= 100:
                    irq = touch.irq.value() if touch.irq else "?"
                    raw_x, raw_y = touch.read_raw()
                    print("[alive] IRQ={} RAW=({:4d},{:4d})".format(irq, raw_x, raw_y))
                    count = 0
                time.sleep_ms(20)
    except KeyboardInterrupt:
        print("Test stopped.")


if __name__ == "__main__":
    main()
