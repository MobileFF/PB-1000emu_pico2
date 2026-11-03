"""カラーバー経路(set_live_content を呼ばない)でのfdebug比較用。
test_dvi_lcd_vram.pyと同じfdebug読み取りを、実VRAM内容を使わない
既に動作確認済みの経路で行い、txstall/txoverが起動時の一時的な
ものか、実コンテンツ経路特有のものかを切り分ける。"""
import time
import dvi_display

print("[fdebug_colorbar] dvi_display.init() ...")
ok = dvi_display.init()
print("[fdebug_colorbar] init() ->", ok)

prev_hb = None
for _ in range(8):
    hb, late = dvi_display.debug_info()
    fdebug = dvi_display.pio_debug_info()
    txstall = (fdebug >> 24) & 0xF
    txover = (fdebug >> 16) & 0xF
    delta = "" if prev_hb is None else " (+%d)" % (hb - prev_hb)
    print("heartbeat=%d%s  late_ctr=%d  fdebug=0x%08x txstall=0x%x txover=0x%x"
          % (hb, delta, late, fdebug, txstall, txover))
    prev_hb = hb
    time.sleep(2)
