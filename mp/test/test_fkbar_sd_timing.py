"""
/fkbar.raw（フラッシュ）と /sd/fkbar.raw（SD）の表示崩れの切り分け用
スクリプト(2)。ファイル内容はバイト単位で完全一致していることを
test_fkbar_raw_diff.py で確認済み。

分かってきたこと:
  - DELAY_US=0: チャンク読み込み(SD)と書き込み(LCD)を交互に行うと、SDの
    画像だけ崩れる(フラッシュは常に正常)。
  - DELAY_US=10000-20000: グロスな崩れは直るが、色の異常・半行(4キー分=
    160px=320B)のシフトなど、実行ごとに違う種類の異常が残ることがある。
  - チャンクごとにPNG変換(重いCPU処理)を挟むバージョンだとズレが消えるが、
    極端に遅い。

つまり単純な「チャンクの間に時間を置く」(sleep_us)だけでは安定して直らず、
「読み込みフェーズと書き込みフェーズを完全に分離する」(読み込み中は一切
SPIへの書き込みを行わない/書き込み中は一切SDへのアクセスを行わない)方が
本質的な解決になるのではないか、という仮説を試す。

このスクリプトは3種類の描画方式を切り替えて同じ画面に並べて描画する:

  MODE_INTERLEAVED: 従来の_blit_raw()相当。512Bずつ「読む→書く」を交互に
                     繰り返す。DELAY_USだけ各書き込み後に待てる。
  MODE_PRELOADED:    SDファイルを*先に全部読み込んでメモリに確保*してから、
                     書き込みフェーズでは一切ファイルI/Oを行わず、ただ
                     バッファから512Bずつ書き込むだけ。読み込みと書き込み
                     が時間的に完全に分離される。

追加実験: 「従来方式のチャンクサイズを512Bより大きくする」とどうなるか。
  DELAY_US=0で崩れることは確認済みなので、小さいチャンクでの検証は不要と
  判断し、768B/1024Bの2種類で比較する。複数セクタをまとめて読んでから
  LCDへ書くため、SD転送とLCD書き込みの時間的近接が薄まり、改善方向に
  働くと予想される(preloadedの弱い版)。

Usage:
    mpremote connect <port> run mp/test/test_fkbar_sd_timing.py
"""
import time

DELAY_US = 6800        # MODE_INTERLEAVEDでのみ使う (例: 0, 500, 2000, 10000, 20000)
CHUNK = 512
CHUNK_BIG1 = 768       # 追加実験: 512Bより大きいチャンク(1)
CHUNK_BIG2 = 1568      # 追加実験: 512Bより大きいチャンク(2)
IMG_W = 320
IMG_H = 42
X0 = 0
GAP = 5  # 5行表示でILI9341(240px高)でも収まるように縮小

Y0_FLASH = 0
Y0_SD_INTERLEAVED = IMG_H + GAP
Y0_SD_PRELOADED = (IMG_H + GAP) * 2
Y0_SD_CHUNK_BIG1 = (IMG_H + GAP) * 3
Y0_SD_CHUNK_BIG2 = (IMG_H + GAP) * 4


def _write_chunked(d, buf, n, delay_us):
    d._deselect_others()
    d.cs.value(0)
    try:
        d.spi.write(buf if n == len(buf) else buf[:n])
    finally:
        d.cs.value(1)
    if delay_us:
        time.sleep_us(delay_us)


def blit_interleaved(display, path, y0, delay_us, chunk_size=CHUNK):
    """従来方式: chunk_sizeバイトずつ「読む→書く」を交互に行う。delay_usは
    各書き込み後の待ち時間(funckey_bar.pyの_blit_raw()にはこのdelayは無い
    -- DELAY_US=0かつchunk_size=512がそれと等価)。

    注意: chunk_sizeを512Bより小さくしても、下層のSD読み込み(sdcard.pyの
    readblocks())自体は512Bセクタ単位でしか発生しない(oofatfsの内部セクタ
    バッファがキャッシュする)。つまりchunk_sizeを変えても「SDへの実際の
    SPI転送頻度」は変わらず、変わるのは「LCDへの書き込み回数と1回あたりの
    サイズ」だけ。"""
    d = display
    d.set_window(X0, y0, X0 + IMG_W - 1, y0 + IMG_H - 1)
    d.dc.value(1)
    buf = bytearray(chunk_size)
    n_chunks = 0
    t_start = time.ticks_ms()
    try:
        with open(path, 'rb') as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                _write_chunked(d, buf, n, delay_us)
                n_chunks += 1
    except OSError as e:
        print(f"  RESULT: read error ({path}): {e}")
        return
    elapsed_ms = time.ticks_diff(time.ticks_ms(), t_start)
    print(f"  [interleaved] {path}: {n_chunks} chunks of {chunk_size}B, "
          f"{elapsed_ms}ms, delay_us={delay_us}, y={y0}")


def blit_preloaded(display, path, y0):
    """読み込みフェーズと書き込みフェーズを完全に分離する: まずファイル
    全体を1回のf.read()でメモリへ読み切ってから(この間LCDへは一切触れ
    ない)、書き込みフェーズでは一切ファイルI/Oを行わずバッファから
    512Bずつ書き込むだけ。"""
    d = display
    t_read_start = time.ticks_ms()
    with open(path, 'rb') as f:
        data = f.read()
    t_read_ms = time.ticks_diff(time.ticks_ms(), t_read_start)

    d.set_window(X0, y0, X0 + IMG_W - 1, y0 + IMG_H - 1)
    d.dc.value(1)
    t_write_start = time.ticks_ms()
    n_chunks = 0
    mv = memoryview(data)
    total = len(data)
    off = 0
    while off < total:
        n = min(CHUNK, total - off)
        _write_chunked(d, mv[off:off + n], n, 0)
        off += n
        n_chunks += 1
    t_write_ms = time.ticks_diff(time.ticks_ms(), t_write_start)
    del data
    print(f"  [preloaded]   {path}: {n_chunks} chunks, "
          f"read={t_read_ms}ms write={t_write_ms}ms, y={y0}")


def main():
    from display_init import init_display

    print("=" * 60)
    print(f"fkbar flash-vs-SD comparison (DELAY_US={DELAY_US} for interleaved)")
    print("=" * 60)

    display, touch, sd_mounted, spi = init_display()

    print("1) Flash (/fkbar.raw), interleaved, no delay:")
    blit_interleaved(display, "/fkbar.raw", Y0_FLASH, 0)

    if not sd_mounted:
        print("RESULT: SD not mounted -- cannot draw the SD rows.")
        return

    print("2) SD (/sd/fkbar.raw), interleaved (read<->write per chunk):")
    blit_interleaved(display, "/sd/fkbar.raw", Y0_SD_INTERLEAVED, DELAY_US)

    print("3) SD (/sd/fkbar.raw), preloaded (read fully, then write only):")
    blit_preloaded(display, "/sd/fkbar.raw", Y0_SD_PRELOADED)

    print(f"4) SD (/sd/fkbar.raw), interleaved, chunk={CHUNK_BIG1}B:")
    blit_interleaved(display, "/sd/fkbar.raw", Y0_SD_CHUNK_BIG1, DELAY_US,
                      chunk_size=CHUNK_BIG1)

    print(f"5) SD (/sd/fkbar.raw), interleaved, chunk={CHUNK_BIG2}B:")
    blit_interleaved(display, "/sd/fkbar.raw", Y0_SD_CHUNK_BIG2, DELAY_US,
                      chunk_size=CHUNK_BIG2)

    print("-" * 60)
    print(f"Compare on the LCD: y={Y0_FLASH} flash / y={Y0_SD_INTERLEAVED} "
          f"SD-interleaved(512B) / y={Y0_SD_PRELOADED} SD-preloaded / "
          f"y={Y0_SD_CHUNK_BIG1} SD-interleaved({CHUNK_BIG1}B) / "
          f"y={Y0_SD_CHUNK_BIG2} SD-interleaved({CHUNK_BIG2}B).")


if __name__ == "__main__":
    main()
