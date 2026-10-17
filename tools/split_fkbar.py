#!/usr/bin/env python3
"""
fkbar.raw（320x42, RGB565 big-endian, ヘッダ無し生バイナリ）を
ボタン単位（デフォルト8分割、funckey_bar.pyの_FKEYSに合わせる）に
分割する開発PC側ツール。

分割後の各ファイルは KEY_W x IMG_H（デフォルト 40x42）ピクセルの
RGB565 big-endianで、元画像の各行から対応する列範囲を切り出して
連結したもの。元画像の生成経路(tools/gen_fkbar.ps1)と同じピクセル
フォーマットを維持する。

Usage:
    python3 tools/split_fkbar.py mp/fkbar.raw mp/test/fkbar_split/
    python3 tools/split_fkbar.py mp/fkbar.raw mp/test/fkbar_split/ --keys 8

生成されたファイル(fkbar_key0.raw .. fkbar_key7.raw)をSDカードの
ルートにコピーし、mp/test/test_fkbar_split_display.py で表示を検証する。
"""
import argparse
import os

IMG_W = 320
IMG_H = 42
BYTES_PER_PIXEL = 2


def split(src_path, dst_dir, n_keys):
    if IMG_W % n_keys != 0:
        raise SystemExit(f"IMG_W={IMG_W} is not divisible by n_keys={n_keys}")
    key_w = IMG_W // n_keys
    row_bytes = IMG_W * BYTES_PER_PIXEL
    key_row_bytes = key_w * BYTES_PER_PIXEL

    with open(src_path, 'rb') as f:
        data = f.read()

    expected = row_bytes * IMG_H
    if len(data) != expected:
        raise SystemExit(f"unexpected size: {len(data)} bytes (expected {expected})")

    os.makedirs(dst_dir, exist_ok=True)

    for key in range(n_keys):
        col_off = key * key_row_bytes
        out = bytearray(key_row_bytes * IMG_H)
        for row in range(IMG_H):
            src_off = row * row_bytes + col_off
            dst_off = row * key_row_bytes
            out[dst_off:dst_off + key_row_bytes] = data[src_off:src_off + key_row_bytes]
        dst_path = os.path.join(dst_dir, f"fkbar_key{key}.raw")
        with open(dst_path, 'wb') as f:
            f.write(out)
        print(f"written: {dst_path} ({len(out)} bytes = {key_w}x{IMG_H})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", help="source fkbar.raw path")
    ap.add_argument("dst_dir", help="output directory for split files")
    ap.add_argument("--keys", type=int, default=8,
                     help="number of keys to split into (default: 8)")
    args = ap.parse_args()
    split(args.src, args.dst_dir, args.keys)


if __name__ == "__main__":
    main()
