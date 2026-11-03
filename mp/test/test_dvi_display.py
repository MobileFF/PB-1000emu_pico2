"""
Waveshare RP2350-PiZero — DVI出力 Phase 1 実機確認用スクリプト（2026-10-21）。

firmware_pb1000_pizero.uf2（bldfrm.sh pizero でビルド）をPiZeroに書き込んだ
あと、通常の起動シーケンス（main.py経由の自動起動）を一切経由せず、
dvi_display.init() を直接呼んでDVI信号そのものが出るかどうかだけを確認する。

このPhaseのスコープ: PB-1000の画面内容ではなく、カラーバーのテストパターンが
正しい位置・正しい色でHDMI/DVIモニタに表示されるかどうかの確認のみ。
PB-1000実際の画面・F1セットアップメニュー・起動時プロファイル選択UIをDVI上に
表示するのはPhase 2（doc/hardware_guide.mdのRP2350-PiZero節参照、未実装）。

確認方法:
  1. PiZeroをBOOTSELで接続し firmware_pb1000_pizero.uf2 を書き込む。
  2. USB-CDC REPL（このボードはPIO-USBキーボード専用配線のため、ネイティブ
     USBポートはCDC REPLとして使える — doc/hardware_guide.md参照）で接続:
       mpremote connect <port> run mp/test/test_dvi_display.py
  3. HDMI/DVIモニタに8色の縦バー（白/黄/シアン/緑/マゼンタ/赤/青/黒）+
     画面中央の横一本の白線が表示されれば、DVI信号経路は正常。
  4. 数秒おきに debug_info() の heartbeat が増え続けていることを確認する
     （増えなくなったらcore1が停止している。late_ctr が0のままなら
     パイプライン自体は健全 — 詳細は dvi_display.c のコメント参照）。

このテストはmain.py（通常起動）とは無関係に動作する。実機でまだ何も
確認していない段階のため、何が起きても壊れたままにならないよう、
このスクリプト自体はPB-1000 CPUコアや通常の起動シーケンスには一切触れない。
"""
import time

import dvi_display


def main():
    print("[test_dvi_display] dvi_display.init() を呼び出します...")
    ok = dvi_display.init()
    print("[test_dvi_display] init() ->", ok)
    if not ok:
        print("[test_dvi_display] 失敗: set_sys_clock_khz()がFalseを返しました"
              "（このビルドがPB1000_BOARD_PIZEROでコンパイルされていない"
              "可能性があります — bldfrm.sh pizero でビルドしたか確認してください）。")
        return

    print("[test_dvi_display] HDMI/DVIモニタに8色の縦バーが表示されているか"
          "確認してください。以下、2秒おきにheartbeat/late_ctrを表示します"
          "（Ctrl-Cで終了）。")
    prev_hb = None
    while True:
        hb, late = dvi_display.debug_info()
        delta = "" if prev_hb is None else " (+%d)" % (hb - prev_hb)
        print("heartbeat=%d%s  late_ctr=%d" % (hb, delta, late))
        prev_hb = hb
        time.sleep(2)


if __name__ == "__main__":
    main()
