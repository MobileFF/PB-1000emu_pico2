"""
ST7796 (480x320) タッチパネルスタンドアロンテスト。
LCD エリア (TK1..TK16) とシートキーバー、両方のタッチ判定を確認する。

使い方:
  main.py の代わりにこのファイルを実行する。
  画面にLCDエリア(緑)とシートキーバーが描画されるので、
  各エリアをタッチして REPL のログで判定を確認する。

タッチオフセットがズレている場合は TOUCH_Y_OFFSET / FKEY_TOUCH_Y_OFFSET を調整する。
"""
import machine
import time
from st7796 import ST7796
from xpt2046 import XPT2046
from funckey_bar import FuncKeyBar, hit_test

# --- ピン定義 (pb1000.py と同一) ---
SPI_ID    = 1
SCK_PIN   = 10
MOSI_PIN  = 11
MISO_PIN  = 12
CS_PIN    = 9
DC_PIN    = 8
RST_PIN   = 7
BL_PIN    = 22
SD_CS_PIN = 15
T_CS_PIN  = 16
T_IRQ_PIN = 17

# --- 表示設定 (main_boot.py の自動センタリング計算と同一) ---
DISPLAY_W = 480
DISPLAY_H = 320
LCD_SCALE = 2.0
LCD_W     = int(192 * LCD_SCALE)            # 384
LCD_H     = int(32  * LCD_SCALE)            # 64
LCD_X     = (DISPLAY_W - LCD_W) // 2        # 48
FKEY_GAP  = 24
FBAR_H    = 42
FBAR_W    = 320
GROUP_H   = LCD_H + FKEY_GAP + FBAR_H      # 130
LCD_Y     = (DISPLAY_H - GROUP_H) // 2     # 95
FBAR_Y    = LCD_Y + LCD_H + FKEY_GAP       # 183
FBAR_X    = LCD_X                           # 48

# --- タッチオフセット ---
# 320x240 で実測した値を 480x320 にスケール。
# ズレる場合は TOUCH_Y_OFFSET / FKEY_TOUCH_Y_OFFSET を直接調整する。
TOUCH_X_OFFSET      = 8
TOUCH_Y_OFFSET      = -4
FKEY_TOUCH_X_OFFSET = 8
FKEY_TOUCH_Y_OFFSET = -8


class ProbeSystem:
    """FuncKeyBar.poll_coords() が要求する最小インタフェース。"""
    def __init__(self):
        self.funckey_touch_x_offset = FKEY_TOUCH_X_OFFSET
        self.funckey_touch_y_offset = FKEY_TOUCH_Y_OFFSET
        self._last_status = None
        self._last_key = None

    def press_key(self, key):
        if key != self._last_key:
            print("  -> press_key", key)
            self._last_key = key

    def release_key(self, key):
        print("  -> release_key", key)
        self._last_key = None

    def set_status(self, label):
        if label != self._last_status:
            print("  -> status:", label)
            self._last_status = label


def _draw_scene(display):
    """LCDエリアのベゼル (4x4 グリッド) を描画する。"""
    # 外枠
    pad = 4
    display.fill_rect(LCD_X - pad, LCD_Y - pad,
                      LCD_W + pad * 2, LCD_H + pad * 2, 0x4228)
    # 内枠
    display.fill_rect(LCD_X - 2, LCD_Y - 2, LCD_W + 4, LCD_H + 4, 0x8410)
    # LCD 本体 (淡緑)
    display.fill_rect(LCD_X, LCD_Y, LCD_W, LCD_H, 0xB5E6)
    # 4x4 グリッド線
    cw = LCD_W // 4
    rh = LCD_H // 4
    for i in range(1, 4):
        display.fill_rect(LCD_X + i * cw,     LCD_Y,     1, LCD_H, 0x39C7)
        display.fill_rect(LCD_X,     LCD_Y + i * rh, LCD_W,     1, 0x39C7)
    # 行/列ラベル代わりに左辺・上辺に細い色帯
    display.fill_rect(LCD_X - pad, LCD_Y, 2, LCD_H, 0x001F)   # 青: 列左端
    display.fill_rect(LCD_X, LCD_Y - pad, LCD_W, 2, 0xF800)   # 赤: 行上端


def _mark_touch(display, x, y, color=0xF800, size=5):
    """タッチ位置に小さなマーカーを描く (デバッグ用)。"""
    x0 = max(0, x - size // 2)
    y0 = max(0, y - size // 2)
    x0 = min(DISPLAY_W - size, x0)
    y0 = min(DISPLAY_H - size, y0)
    display.fill_rect(x0, y0, size, size, color)


def _classify_and_log(display, touch, bar, system):
    """
    タッチ座標を取得してエリア判定し、ログ出力する。
    Returns True if any area was hit.
    """
    coords = touch.get_touch()
    raw_x, raw_y = touch.read_raw()

    if coords is None:
        print("  TOUCH coords=None  RAW=({:4d},{:4d})".format(raw_x, raw_y))
        bar.release(system)
        return False

    tx, ty = coords

    # ----- LCD エリア判定 -----
    lx = tx + TOUCH_X_OFFSET
    ly = ty + TOUCH_Y_OFFSET
    lcd_key = None
    if LCD_X <= lx <= LCD_X + LCD_W and LCD_Y <= ly <= LCD_Y + LCD_H:
        col = max(0, min(3, int((lx - LCD_X) * 4 // LCD_W)))
        row = max(0, min(3, int((ly - LCD_Y) * 4 // LCD_H)))
        lcd_key = "TK{}".format(row * 4 + col + 1)

    # ----- FuncKeyBar 判定 (FuncKeyBar.poll_coords と同じオフセット) -----
    fkx = tx + FKEY_TOUCH_X_OFFSET
    fky = ty + FKEY_TOUCH_Y_OFFSET
    fk_hit = hit_test(fkx, fky, FBAR_Y, FBAR_X)

    # ----- ログ -----
    if lcd_key:
        area  = "LCD "
        detail = lcd_key
        marker_color = 0xF800   # 赤
        _mark_touch(display, lx, ly, marker_color)
    elif fk_hit:
        area  = "FBAR"
        detail = fk_hit[2]
        marker_color = 0x07FF   # シアン
        _mark_touch(display, fkx, fky, marker_color)
    else:
        area  = "NONE"
        detail = "---"
        _mark_touch(display, tx, ty, 0x7BEF)   # グレー

    print(
        "RAW=({:4d},{:4d}) MAP=({:3d},{:3d}) "
        "LCD_adj=({:3d},{:3d}) FK_adj=({:3d},{:3d}) "
        "AREA={} HIT={}".format(
            raw_x, raw_y, tx, ty,
            lx, ly, fkx, fky,
            area, detail,
        )
    )

    # FuncKeyBar のアクション呼び出し
    bar.poll_coords(system, coords)
    return lcd_key is not None or fk_hit is not None


def main():
    print("=" * 50)
    print("ST7796 Touch Test  (480x320 / scale=2.0)")
    print("LCD   : x={}..{} y={}..{}".format(LCD_X, LCD_X + LCD_W - 1, LCD_Y, LCD_Y + LCD_H - 1))
    print("FBAR  : x={}..{} y={}..{}".format(FBAR_X, FBAR_X + FBAR_W - 1, FBAR_Y, FBAR_Y + FBAR_H - 1))
    print("Y_OFS : TOUCH={:+d}  FKEY={:+d}".format(TOUCH_Y_OFFSET, FKEY_TOUCH_Y_OFFSET))
    print("Ctrl+C to stop.")
    print("=" * 50)

    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    machine.Pin(CS_PIN,    machine.Pin.OUT, value=1)
    machine.Pin(T_CS_PIN,  machine.Pin.OUT, value=1)
    machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(BL_PIN,    machine.Pin.OUT, value=1)

    display = ST7796(
        spi,
        machine.Pin(CS_PIN,  machine.Pin.OUT),
        machine.Pin(DC_PIN,  machine.Pin.OUT),
        machine.Pin(RST_PIN, machine.Pin.OUT),
        width=DISPLAY_W,
        height=DISPLAY_H,
    )
    display.fill_rect(0, 0, DISPLAY_W, DISPLAY_H, 0x0000)
    _draw_scene(display)

    bar = FuncKeyBar(display, FBAR_Y, x_offset=FBAR_X)
    bar.draw()

    touch = XPT2046(
        spi, T_CS_PIN, T_IRQ_PIN,
        width=DISPLAY_W, height=DISPLAY_H,
        swap_xy=True, x_inv=True, y_inv=True,
        y_min=325, y_max=3850,
    )
    system = ProbeSystem()

    count = 0
    was_pressed = False
    try:
        while True:
            if touch.is_pressed():
                _classify_and_log(display, touch, bar, system)
                was_pressed = True
                count = 0
                time.sleep_ms(100)
            else:
                if was_pressed:
                    bar.release(system)
                    was_pressed = False
                    # マーカーを消すため LCD/FBAR 領域を再描画
                    _draw_scene(display)
                    bar.draw()
                count += 1
                if count >= 100:   # ~2s ハートビート
                    irq = touch.irq.value() if touch.irq else "?"
                    rx, ry = touch.read_raw()
                    print("[alive] IRQ={} RAW=({:4d},{:4d})".format(irq, rx, ry))
                    count = 0
                time.sleep_ms(20)

    except KeyboardInterrupt:
        print("Test stopped.")


if __name__ == "__main__":
    main()
