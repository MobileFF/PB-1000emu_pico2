"""
fkbar.raw（画像ファイル）を一切使わず、fill_rect()とdraw_text()だけで
ファンクションキーバーと同等の見た目を描画するテストスクリプト。

画像ファイルの読み込み(フラッシュ/SD)が一切発生しないため、SD/LCDの
SPI共有バスに起因する表示崩れの問題自体をそもそも回避できる -- これは
「崩れを直す」ではなく「崩れが起きる経路を使わない」という、本番fkbar.raw
方式に対する代替実装案が実用に耐えるかどうかの確認用テスト。

ボタン内のテキストはdraw_text.py(framebuf内蔵の8x8固定フォント)を使う
ため、フォントの見た目は元のビットマップ画像(references/face.bmp由来)
とは異なるが、各ボタンの枠内に収まっていれば良い、との方針に基づく。
実機の印字体に相当するフォントはMicroPython側に無いため、フォントの
見た目自体は実機に寄せず、既存の8x8固定フォントのままにしている。
ラベルはfuncky_bar.pyの_FKEYSが元。

配色は実機の写真(調査用/IMG_2532.heic)から実測した値(色相のみ)を
使っている。実機は4系統の配色に分かれている:
  LCKEY             = 黄緑がかったグレー
  MENU, CAL         = ラベンダー
  MEMO, MEMO IN     = ピンク
  IN, OUT, CALC     = ブルーグレー
外枠・区切り線の色、文字色(黒)も同じ写真を参考にした。

実測値そのまま(彩度5〜15%程度)だと画面上では薄すぎたため、HSVの
彩度(S)・明度(V)を振った3パターンを横並びではなく縦に3段スタックして
比較できるようにしている(PATTERNS参照)。

  - LCKEYボタンだけ、5文字("LCKEY")をKEY_W=40px幅に収めるため、8x8の
    グリフをピクセル単位で縮小した専用フォント(_draw_text_scaled())を
    使う。draw_text.py自体は他の多くの画面(プロファイル選択・BIOS風
    設定メニュー等)で共有されているため、ここでは変更せずローカルの
    縮小描画関数を別途用意している。
  - MEMO INボタンは1行に収めず、"MEMO"/"IN"の2行に分けてそれぞれ
    中央寄せで表示する。
  - キーバー全体(320x42)の外周にBORDER_THICKNESS pxの外枠を付ける。
    fill_rect()しか使えないため、上下左右4本の細い矩形で枠を描く。

Usage:
    mpremote connect <port> run mp/test/test_fkbar_code_draw.py
"""
import time

from draw_text import draw_text

IMG_W = 320
IMG_H = 42
N_KEYS = 8
KEY_W = IMG_W // N_KEYS  # 40
X0 = 0
GAP = 5  # 3段表示でILI9341(240px高)でも収まるように

LCKEY_CELL = 7  # LCKEYボタンだけ使う縮小フォントの1文字あたりのセルサイズ(px)

# funckey_bar.py の _FKEYS ラベル。index 0(LCKEY)は縮小フォント、
# index 4(MEMO IN)は2行描画で特別扱いするため、ここでは通常の
# draw_text()で描画するラベルだけを並べている(該当indexは使われない)。
_LABELS = ['LCKEY', 'MENU', 'CAL', 'MEMO', None, 'IN', 'OUT', 'CALC']
_MEMO_IN_LINES = ('MEMO', 'IN')

# 実機写真(調査用/IMG_2532.heic)のボタン中央部RGB平均値から取り出した
# 色相(Hue)。4系統: LCKEY=黄緑 / MENU,CAL=紫(ラベンダー) /
# MEMO,MEMO IN=ピンク / IN,OUT,CALC=青(ブルーグレー)。
#
# 実測値の彩度(5〜15%程度)をそのまま使うと画面上では薄すぎ、かつ
# RGB各chを一律に係数倍して暗くする(=彩度を変えずに明度だけ下げる)
# やり方では「暗くなるだけで濃くならない」ため、HSVの彩度(S)・明度(V)
# を振った3パターンを比較できるよう、色相(Hue)だけを使って組み直して
# いる。各パターンのRGB565値は、下のHUESとこの(S,V)からオフライン
# (python3のcolorsys)で計算したもの。
_HUES = (66.0, 255.0, 332.6, 221.1)  # LCKEY, LAVENDER, PINK, BLUEGRAY

# (ラベル, {LCKEY, LAVENDER, PINK, BLUEGRAY}のRGB565値, S, V)
PATTERNS = [
    ('A: S=55% V=90%', (0xDF2C, 0x833C, 0xE334, 0x647C), 0.55, 0.90),
    ('B: S=40% V=80%', (0xC66F, 0x8BD9, 0xCBD3, 0x7CB9), 0.40, 0.80),
    ('C: S=35% V=90%', (0xDF32, 0xACBC, 0xE4B7, 0x957C), 0.35, 0.90),
]

Y0_LIST = [i * (IMG_H + GAP) for i in range(len(PATTERNS))]

_TEXT_FG = 0x0000  # 黒

BORDER_COLOR = 0x6B6E      # 実機の枠・区切り線の色を実測(RGB(107,111,113))
BORDER_THICKNESS = 2       # キーバー全体の外枠の太さ(px)


def _sw16(c):
    return ((c & 0xFF) << 8) | (c >> 8)


# LCKEYボタン専用の縮小フォント描画バッファ(遅延生成、再利用)。
# draw_text.pyと同じ「固定バッファを使い回す」方針を踏襲する。
_big_buf = None
_big_fb = None
_small_buf = None


def _draw_text_scaled(display, x, y, text, fg, bg, cell):
    """draw_text()相当だが、各文字を8x8フォントからcell x cellへ
    ニアレストネイバーで縮小してから描画する。framebufは拡大縮小blit
    を持たないため、ピクセル単位で間引く。cell<8の場合のみ縮小効果が
    ある。LCKEYボタンでのみ使う、このテストスクリプト専用の実装。"""
    import framebuf

    global _big_buf, _big_fb, _small_buf
    if _big_buf is None:
        _big_buf = bytearray(8 * 8 * 2)
        _big_fb = framebuf.FrameBuffer(_big_buf, 8, 8, framebuf.RGB565)
        _small_buf = bytearray(cell * cell * 2)
    big_buf, big_fb, small_buf = _big_buf, _big_fb, _small_buf

    text = str(text)
    cx = x
    for ch in text:
        big_fb.fill(_sw16(bg))
        big_fb.text(ch, 0, 0, _sw16(fg))
        for sy in range(cell):
            src_y = (sy * 8) // cell
            row_off = src_y * 8 * 2
            dst_row_off = sy * cell * 2
            for sx in range(cell):
                src_x = (sx * 8) // cell
                src_off = row_off + src_x * 2
                dst_off = dst_row_off + sx * 2
                small_buf[dst_off] = big_buf[src_off]
                small_buf[dst_off + 1] = big_buf[src_off + 1]
        display.set_window(cx, y, cx + cell - 1, y + cell - 1)
        display.write_data(small_buf)
        cx += cell


def _draw_outer_border(display, x0, y0, w, h, color, thickness):
    """キーバー全体(w x h)の外周に太さthicknessの枠を付ける。fill_rect()
    しか使えないため、上下左右4本の細い矩形を描いて枠にする。"""
    display.fill_rect(x0, y0, w, thickness, color)                      # 上
    display.fill_rect(x0, y0 + h - thickness, w, thickness, color)      # 下
    display.fill_rect(x0, y0, thickness, h, color)                      # 左
    display.fill_rect(x0 + w - thickness, y0, thickness, h, color)      # 右


def draw_funckey_bar_code(display, x0, y0, colors):
    """画像ファイルを一切使わず、矩形+テキストだけでキーバーを描画する。
    colorsは(LCKEY, LAVENDER, PINK, BLUEGRAY)のRGB565値4要素タプル。"""
    key_colors = [colors[0], colors[1], colors[1],
                  colors[2], colors[2],
                  colors[3], colors[3], colors[3]]
    for i in range(N_KEYS):
        kx = x0 + i * KEY_W
        color = key_colors[i]
        # 1pxの枠を残して塗る(_draw_fallback()と同じやり方)
        display.fill_rect(kx + 1, y0 + 1, KEY_W - 2, IMG_H - 2, color)

        if i == 4:
            # MEMO IN: "MEMO"/"IN"の2行に分けて、それぞれ中央寄せ表示
            line1, line2 = _MEMO_IN_LINES
            gap = 2
            block_h = 8 + gap + 8
            ty1 = y0 + (IMG_H - block_h) // 2
            ty2 = ty1 + 8 + gap
            for label, ty in ((line1, ty1), (line2, ty2)):
                tw = len(label) * 8
                tx = kx + max(0, (KEY_W - tw) // 2)
                draw_text(display, tx, ty, label, _TEXT_FG, bg=color)
            continue

        label = _LABELS[i]
        ty = y0 + (IMG_H - 8) // 2
        if i == 0:
            # LCKEY: 5文字をKEY_W=40pxに収めるため縮小フォントを使う
            tw = len(label) * LCKEY_CELL
            tx = kx + max(0, (KEY_W - tw) // 2)
            _draw_text_scaled(display, tx, ty, label, _TEXT_FG, color,
                               LCKEY_CELL)
        else:
            tw = len(label) * 8
            tx = kx + max(0, (KEY_W - tw) // 2)
            draw_text(display, tx, ty, label, _TEXT_FG, bg=color)

    _draw_outer_border(display, x0, y0, IMG_W, IMG_H, BORDER_COLOR,
                        BORDER_THICKNESS)


def main():
    from display_init import init_display

    print("=" * 60)
    print("fkbar code-drawn (no image file) test -- 3 S/V patterns stacked")
    print("=" * 60)

    display, touch, sd_mounted, spi = init_display()

    for (label, colors, s, v), y0 in zip(PATTERNS, Y0_LIST):
        t0 = time.ticks_ms()
        draw_funckey_bar_code(display, X0, y0, colors)
        elapsed_ms = time.ticks_diff(time.ticks_ms(), t0)
        print(f"  [{label}] {N_KEYS} keys, {elapsed_ms}ms, y={y0}")

    print("-" * 60)
    for (label, colors, s, v), y0 in zip(PATTERNS, Y0_LIST):
        print(f"y={y0}: {label}")


if __name__ == "__main__":
    main()
