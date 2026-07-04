# ── Ctrl+C でエミュレータを停止してから入力 ──────────────────────────

import machine, time
from st7796 import ST7796

# 既存のハードウェアピンをそのまま再利用
spi = machine.SPI(1, baudrate=40_000_000,
                  sck=machine.Pin(10), mosi=machine.Pin(11), miso=machine.Pin(12))
cs  = machine.Pin(9,  machine.Pin.OUT, value=1)
dc  = machine.Pin(8,  machine.Pin.OUT)
rst = machine.Pin(7,  machine.Pin.OUT, value=1)
machine.Pin(22, machine.Pin.OUT, value=1)   # バックライト ON

# --- テスト ---
# ST7796() を呼ぶと内部で:
#   reset()        → RST Low(100ms) → High(100ms)
#   init_display() → SWRESET → SLPOUT → COLMOD/MADCTL/etc → fill_rect(0,0,480,320,0x0000) → DISPON
#
# 観察ポイント:
#   ① reset() で RST が Low になった瞬間、画面が全白になるか？
#   ② init_display() の fill_rect(黒) で黒に戻るか？
#   ③ その後の draw_bezel() でベゼルが描画できるか？

print("ST7796.__init__ 呼び出し前")
time.sleep_ms(3000)

display = ST7796(spi, cs, dc, rst, width=480, height=320)
print("ST7796.__init__ 完了 (画面は黒になっているはず)")
time.sleep_ms(3000)

# ベゼルを描いて正常動作を確認
from pb1000 import draw_bezel
draw_bezel(display, scale=2.0, x=48, y=64, lcd_height=32)
print("draw_bezel 完了")

time.sleep_ms(3000)

print("reset")
display.reset()
