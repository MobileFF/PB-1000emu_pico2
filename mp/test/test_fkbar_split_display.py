"""
fkbar.rawを8分割（ボタン単位、funckey_bar.pyの_FKEYSと同じ40px幅x8枚）
し、各ボタン画像を個別に「全部読む→全部書く」(preloaded)サイクルで
描画した場合に、単一の大きい画像をpreloadedで描画した場合と同じ表示
品質・速度になるかを検証するテストスクリプト。

分割ファイルの生成（開発PC側、1回だけ）:
    python3 tools/split_fkbar.py mp/fkbar.raw mp/test/fkbar_split/
  -> fkbar_key0.raw .. fkbar_key7.raw (各 40x42 RGB565 big-endian, 3360B)
  が生成される。これをSDカードのルートに置いてから
  (フラッシュには置かない -- このテストはSD側の検証が目的)、本スクリプト
  を実機で実行する。

比較内容（画面に3段並べて表示）:
  1) flash (/fkbar.raw):             基準。単一ファイル、preloaded。
  2) sd    (/sd/fkbar.raw):          単一ファイル、preloaded。
                                      (test_fkbar_sd_timing.pyで動作確認済み)
  3) sd    (/sd/fkbar_key0..7.raw):  8分割ファイル。各ファイルごとに
                                      read全体→write全体のサイクルを
                                      8回繰り返す(今回の検証対象)。

3)が1)/2)と同じ見え方なら、「read→writeサイクルを複数回重ねても崩れは
再発しない」という見立てが実証される。

Usage:
    mpremote connect <port> run mp/test/test_fkbar_split_display.py
"""
import time

IMG_W = 320
IMG_H = 42
N_KEYS = 8
KEY_W = IMG_W // N_KEYS   # 40
CHUNK = 512
X0 = 0
GAP = 5  # 3行表示でILI9341(240px高)でも収まるように

Y0_FLASH = 0
Y0_SD_SINGLE = IMG_H + GAP
Y0_SD_SPLIT = (IMG_H + GAP) * 2

FLASH_SINGLE_PATH = "/fkbar.raw"
SD_SINGLE_PATH = "/sd/fkbar.raw"
SD_KEY_PATH_FMT = "/sd/fkbar_key{}.raw"


def _write_chunked(d, buf, n):
    d._deselect_others()
    d.cs.value(0)
    try:
        d.spi.write(buf if n == len(buf) else buf[:n])
    finally:
        d.cs.value(1)


def blit_preloaded_single(display, path, y0):
    """単一ファイルを全部読み込んでから全部書き込む(既存のpreloaded方式)。"""
    d = display
    t0 = time.ticks_ms()
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        print(f"  RESULT: read error ({path}): {e}")
        return
    t_read = time.ticks_diff(time.ticks_ms(), t0)

    d.set_window(X0, y0, X0 + IMG_W - 1, y0 + IMG_H - 1)
    d.dc.value(1)
    t1 = time.ticks_ms()
    mv = memoryview(data)
    total = len(data)
    off = 0
    n_chunks = 0
    while off < total:
        n = min(CHUNK, total - off)
        _write_chunked(d, mv[off:off + n], n)
        off += n
        n_chunks += 1
    t_write = time.ticks_diff(time.ticks_ms(), t1)
    del data
    print(f"  [single-preloaded] {path}: {n_chunks} chunks, "
          f"read={t_read}ms write={t_write}ms, y={y0}")


def blit_preloaded_key(display, path, x0, y0):
    """1ボタン分の画像ファイルを全部読み込んでから全部書き込む。呼び出し
    元が複数回繰り返すことで、「read→writeサイクルをN回重ねる」状況を
    作る。1ファイルの中では読み込みフェーズと書き込みフェーズが完全に
    分離されている点はblit_preloaded_single()と同じ。"""
    d = display
    with open(path, 'rb') as f:
        data = f.read()

    d.set_window(x0, y0, x0 + KEY_W - 1, y0 + IMG_H - 1)
    d.dc.value(1)
    mv = memoryview(data)
    total = len(data)
    off = 0
    while off < total:
        n = min(CHUNK, total - off)
        _write_chunked(d, mv[off:off + n], n)
        off += n
    del data


KEY_SLEEP_MS = 10  # 各ボタン描画後の待ち時間(ms) -- サイクル間隔を空けた場合も
                    # 崩れが再発しないかの確認用


def blit_split(display, path_fmt, y0):
    """N_KEYS個の分割ファイルを左から順にpreloaded方式で描画する。各ボタン
    の描画後にKEY_SLEEP_MSだけ待つ(読み込み→書き込みサイクル同士の間隔を
    空けても崩れが再発しないかを見るため)。"""
    t_start = time.ticks_ms()
    for i in range(N_KEYS):
        path = path_fmt.format(i)
        x0 = X0 + i * KEY_W
        try:
            blit_preloaded_key(display, path, x0, y0)
        except OSError as e:
            print(f"  RESULT: key {i} read error ({path}): {e}")
            return
        if KEY_SLEEP_MS:
            time.sleep_ms(KEY_SLEEP_MS)
    elapsed_ms = time.ticks_diff(time.ticks_ms(), t_start)
    print(f"  [split-preloaded]  {path_fmt.format('N')}: {N_KEYS} files, "
          f"{elapsed_ms}ms total (incl. {KEY_SLEEP_MS}ms/key sleep), y={y0}")


def main():
    from display_init import init_display

    print("=" * 60)
    print("fkbar split-image preloaded-cycle test")
    print("=" * 60)

    display, touch, sd_mounted, spi = init_display()

    print("1) Flash (/fkbar.raw), single file, preloaded:")
    blit_preloaded_single(display, FLASH_SINGLE_PATH, Y0_FLASH)

    if not sd_mounted:
        print("RESULT: SD not mounted -- cannot draw the SD rows.")
        return

    print("2) SD (/sd/fkbar.raw), single file, preloaded (known-good):")
    blit_preloaded_single(display, SD_SINGLE_PATH, Y0_SD_SINGLE)

    print(f"3) SD ({SD_KEY_PATH_FMT.format('0..7')}), {N_KEYS} files, "
          f"preloaded cycle x{N_KEYS}:")
    blit_split(display, SD_KEY_PATH_FMT, Y0_SD_SPLIT)

    print("-" * 60)
    print(f"Compare on the LCD: y={Y0_FLASH} flash / y={Y0_SD_SINGLE} "
          f"SD-single-preloaded / y={Y0_SD_SPLIT} SD-split-preloaded"
          f"(x{N_KEYS}).")
    print("Row 3 should look identical to row 1/2 if repeating the "
          "read-then-write cycle is safe.")


if __name__ == "__main__":
    main()
