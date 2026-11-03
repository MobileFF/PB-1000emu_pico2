"""
Waveshare RP2350-PiZero -- DVI Phase 2a「実VRAM内容」最小切り分け用スクリプト
(2026-10-25, RP2350-PiZero_実機認識しない問題調査.md)。

main.py経由の通常起動(HD61700 CPUコア・USBホスト・SDカード・NTP同期・
キーマップ同期など、1回の起動で40-50秒以上かかる)を一切経由せず、
lcd_c + dvi_display だけを直接初期化し、"ベゼル枠 + FuncKeyBar風の帯"を
模した固定パターンをlcd_cのVRAMへ直接書き込んでから、dvi_display.
set_live_content(True)でそれを実際にDVI出力させる。

目的: _pixel_color()の実ロジック(vram[]読み取り含む)を、CPU実行や
USBホスト等の重い処理を一切介さない、数秒で起動できる最小環境で
繰り返しテストできるようにする(フルブート+手動操作では1回の試行に
40-50秒以上かかり、ビルドサイクルのたびの実機確認が遅すぎる)。

ここで描くパターンはPB-1000実機のFuncKeyBar/ベゼルの実際の描画結果
そのものではない(あれはdisplay.fill_rect()経由でUIレイヤーに描かれる
もの -- mp/display_init.py の_DviDisplayのdocstring参照)。
あくまで「lcd_c.vram[]に実際に
書き込まれた、全ゼロではない、認識しやすい固定パターン」を素早く
用意するための代替品。

確認方法:
  mpremote connect <port> run mp/test/test_dvi_lcd_vram.py

期待される見え方(DVI信号が正常なら): 320x240の画面の中央に、
letterbox(上下黒帯)された192x32相当の領域があり、その中に
- 外周の白い枠線(ベゼルを模した枠)
- 最下段(page 3, y=24-31)を塗りつぶした白い帯(FuncKeyBarを模した帯)
が見えるはず。"No Signal"になる場合、この最小環境でも同じ問題が
再現することを意味する。
"""
import time

import lcd_c
import dvi_display

LCD_WIDTH = 192
ACTIVE_PAGES = 4
LCDC_CMD_DISPLAY_ON_OFF = 0x04


def _rev8(b):
    r = 0
    for _ in range(8):
        r = (r << 1) | (b & 1)
        b >>= 1
    return r


def _build_pattern():
    """192x32 (active_pages=4) 分のVRAMバイト列 (page-major, LCD_WIDTH列/page)
    を素のビット意味で組み立ててから、blit_reversed()が内部でもう一度
    bit-reverseすることを打ち消すため、全バイトを事前にbit-reverseして返す
    (lcd_write()のLCDC_CMD_DRAW_BITIMAGE経由と最終的に同じ並びにするための
    もの -- modlcd_controller.cのblit_reversed()自身のdocstring参照)。"""
    buf = bytearray(LCD_WIDTH * ACTIVE_PAGES)

    # 外周の枠線(ベゼル相当): 上辺(page0 bit0) / 下辺(page3 bit7)
    for col in range(LCD_WIDTH):
        buf[0 * LCD_WIDTH + col] |= 0x01
        buf[(ACTIVE_PAGES - 1) * LCD_WIDTH + col] |= 0x80
    # 左右の枠線: 全page、col=0とcol=LCD_WIDTH-1を全ビットON
    for page in range(ACTIVE_PAGES):
        buf[page * LCD_WIDTH + 0] = 0xFF
        buf[page * LCD_WIDTH + (LCD_WIDTH - 1)] = 0xFF

    # FuncKeyBar相当の帯: 最下段page(page3)を全列塗りつぶし
    bar_page = ACTIVE_PAGES - 1
    for col in range(LCD_WIDTH):
        buf[bar_page * LCD_WIDTH + col] = 0xFF

    return bytes(_rev8(b) for b in buf)


def main():
    print("[test_dvi_lcd_vram] lcd_c.init() ...")
    lcd_c.init()

    # LCDC_CMD_DISPLAY_ON_OFF (1バイトコマンド, mode=0x14=cmd|ON bit) を
    # 発行してdisplay_on=Trueにする -- lcd_init()直後はFalseのままで、
    # lcd_render_row_rgb565()がcolor_lcd_offで全行を塗って早期returnして
    # しまうため必須 (lcd_controller.c apply_lcdc_command()参照)。
    lcd_c.ctrl(0x01)
    lcd_c.write(LCDC_CMD_DISPLAY_ON_OFF | 0x10)

    # ON色(枠線/バー)を背景(黄緑)やletterbox(黒)と区別しやすい赤にする
    # -- デフォルトのON色は黒(0x0000)で、DVI_BORDER_COLOR(letterbox)も黒
    # なので、枠線やバーが描けていても背景の黒letterboxと視覚的に区別
    # できない。OFF色(黄緑系背景)はそのまま残す。
    lcd_c.set_colors(0xF800, 0xB5E6)  # fg=赤, bg=黄緑(LCD_COLOR_OFF)

    print("[test_dvi_lcd_vram] VRAMへ固定パターンを書き込みます ...")
    lcd_c.blit_reversed(_build_pattern(), 0)
    lcd_c.mark_dirty()
    print("[test_dvi_lcd_vram] is_display_on() ->", lcd_c.is_display_on())

    print("[test_dvi_lcd_vram] dvi_display.init() ...")
    ok = dvi_display.init()
    print("[test_dvi_lcd_vram] init() ->", ok)
    if not ok:
        print("[test_dvi_lcd_vram] 失敗: set_sys_clock_khz()がFalseを返しました"
              "(PB1000_BOARD_PIZEROでビルドされていない可能性)。")
        return

    dvi_display.set_live_content(True)
    print("[test_dvi_lcd_vram] live content ON。HDMI/DVIモニタの状態を"
          "確認してください(2秒おきにheartbeat/late_ctr/fault_infoを表示、"
          "Ctrl-Cで終了)。")

    prev_hb = None
    while True:
        hb, late = dvi_display.debug_info()
        occurred, cfsr, bfar, hfsr = dvi_display.fault_info()
        fdebug = dvi_display.pio_debug_info()
        txstall = (fdebug >> 24) & 0xF
        txover = (fdebug >> 16) & 0xF
        rxunder = (fdebug >> 8) & 0xF
        rxstall = fdebug & 0xF
        delta = "" if prev_hb is None else " (+%d)" % (hb - prev_hb)
        print("heartbeat=%d%s  late_ctr=%d  fault=%d cfsr=0x%08x bfar=0x%08x hfsr=0x%08x  "
              "fdebug=0x%08x txstall=0x%x txover=0x%x rxunder=0x%x rxstall=0x%x"
              % (hb, delta, late, occurred, cfsr, bfar, hfsr,
                 fdebug, txstall, txover, rxunder, rxstall))
        prev_hb = hb
        time.sleep(2)


if __name__ == "__main__":
    main()
