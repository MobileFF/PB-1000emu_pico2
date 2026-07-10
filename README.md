# PB-1000 エミュレータ for Raspberry Pi Pico 2

Raspberry Pi Pico 2 (RP2350) で動作する、カシオ PB-1000 ポケットコンピュータのエミュレータです。

## 概要

HD61700 CPUを搭載したカシオのポケットコンピュータ PB-1000 をエミュレートします。高速なC言語によるCPUコアと、周辺機器処理のための柔軟なMicroPythonロジックを組み合わせることで、実機のソフトウェア実行から独自拡張の開発まで、強力な環境を提供します。

## 主な特徴

- **高性能 CPU コア**: HD61700 CPUの命令セットを C 言語で実装。
- **MicroPython フレームワーク**: 周辺ロジックを MicroPython で記述しており、カスタマイズが容易。
- **モダンな表示サポート**: ILI9341 320x240 TFT LCD に対応。タッチインターフェース (XPT2046) を統合。
- **外部キーボード対応**: HID USB キーボード (Host モード) および、シリアルコンソール (UART) による入力をサポート。
- **ストレージ**: SD カードをサポート。RAM 状態の保存、復元、スクリーンショットの保存、仮想FDDのディスクイメージ操作が可能。
- **状態管理**: RAM およびレジスタの状態を保存・復元するステートセーブ機能を搭載。
- **通信**: PIO UART による仮想 RS-232Cの通信をサポート。
- **サブルーチンフック**: 特定のアドレスにPCが到達すると、任意のPython/Cの関数をコールバックさせることができる。
- **ジョイスティックサポート**: ATARI9ピンのジョイスティックの接続をサポート、方向キー、A/Bボタンを任意のキーに割り当て可能。
- **カラー表示サポート**: PB-1000との互換性を保ちつつ、カラー表示を可能に。
- **最大104KBのRAM搭載可能**:ページ1(0x8000〜0xFFFF)のバンク2/3にRAMを搭載可能にし、最大104KBのRAMを利用可能。
 
## クイックスタート

1.  **ハードウェア**: Raspberry Pi Pico 2、ILI9341 LCD、(任意) SD カードモジュールを用意します。詳細は [Hardware Guide](doc/hardware_guide.md) を参照してください。
2.  **ビルド**: カスタム MicroPython ファームウェアをコンパイルします。詳細は [Build Guide](doc/build_guide.md) を参照してください。
3.  **書き込み**: 生成された `firmware.uf2` を Pico 2 にコピーします。
4.  **セットアップ**: `mp/` ディレクトリの Python ファイルと ROM イメージを `/roms/` または `/sd/` にアップロードします。詳細は [Usage Guide](doc/usage_guide.md) を参照してください。
5.  **実行**: `main.py` が存在すれば、エミュレータは自動的に起動します。

## ドキュメント・インデックス

- [Build Guide](doc/build_guide.md) - ビルド環境の構築とファームウェアのコンパイル方法
- [Hardware Guide](doc/hardware_guide.md) - 配線図、部品表 (BOM)、ピンアサイン
- [Usage Guide](doc/usage_guide.md) - 初期設定、ROM の準備、操作マニュアル
- [Development Guide](doc/dev_guide.md) - コード構造、内部 API、デバッグのヒント

*(英語版ドキュメントは `_en.md` サフィックスのファイルを参照してください)*

## プロジェクト構造

```text
PB-1000_emu_AG2/
├── src/                    # C言語ソース (HD61700 CPU コア & Periperal ラッパー)
├── mp/                     # MicroPython コード (システムロジック & ドライバ)
├── doc/                    # ドキュメント & ガイド
├── hardware/               # KiCad 回路図およびハードウェア設計
└── README.md               # 本ファイル
```

## ライセンス

本プロジェクト（`src/`・`mp/` 以下の独自コード、ドキュメント一式）は **GNU General Public License v3.0 (GPL-3.0)** の下で公開されています。全文は [LICENSE](LICENSE) を参照してください。

```
PB-1000 emulator for Raspberry Pi Pico 2
Copyright (C) 2026  MobileFF

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

### 対象範囲・注意事項

- **PB-1000 の ROM イメージは含まれません**。ROM は CASIO 社に著作権があり、本リポジトリでは配布していません。実機から吸い出したご自身の ROM イメージをお使いください（詳細は [Usage Guide](doc/usage_guide.md) 参照）。
- **HD61700 CPU コア（`src/hd61700.c`/`.h`）は MAME プロジェクトの `hd61700.cpp`（Sandro Ronco 氏作、BSD-3-Clause license）の C言語移植です。** パーミッシブライセンスのため GPL-3.0 への組み込みに問題はありませんが、著作権表示・条件・免責事項の全文を [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) に記載しています。
- 「謝辞」に記載の Piotr Piatek 氏の PB-1000 エミュレータについては、ソースコード（`pb1000es/`、参考資料として同梱・ビルド対象外）を本プロジェクトの実装と比較した結果、直接転用は確認されませんでした。詳細は [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) を参照してください。
- 本プロジェクトは **MicroPython**（MIT License）、**Raspberry Pi Pico SDK**（BSD-3-Clause）、**TinyUSB**（MIT License）をビルド時にリンクしています。これらの詳細も [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) に記載しています。

## 謝辞

- CPUの実装/エミュレータ動作の実装には、以下の資料を参考とさせていただきました。ありがとうございます。
  - CASIO社「TECHNICAL MANUAL PB-1000」（英語版）
  - あお氏による「HD61700アセンブリ言語簡易マニュアル(Ver 0.29 2008-05-05)」および「HD61700 INSTRUCTION SET」
  - Piotr Piatek氏による[PB-1000エミュレータ](https://www.pisi.com.pl/piotr433/pb1000ee.htm)のソースコード
  - Jun Amano氏による「[CASIO PB-1000/C FOREVER!](http://www.lsigame.com/pb-1000/pb-1000.htm)」の各種技術記事
  - MAME プロジェクトによる HD61700 のソースコード
- その他、PB-1000に関する情報を発信されているすべての皆様に感謝いたします。
- ソースコードの作成には主に以下のAIエージェントツールを利用しています。
  - Claude Code
  - OpenAI Codex
  - Google Antigravity

以上