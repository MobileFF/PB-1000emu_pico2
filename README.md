# PB-1000 エミュレータ for Raspberry Pi Pico 2

Raspberry Pi Pico 2 (RP2350) に最適化された、カシオ PB-1000 ポケットコンピュータの高精度エミュレータです。

## 概要

このプロジェクトは、HD61700 CPUを搭載したカシオ PB-1000 を完全にエミュレートします。高速なC言語によるCPUコアと、周辺機器処理のための柔軟なMicroPythonロジックを組み合わせることで、実機のソフトウェア実行から独自拡張の開発まで、強力な環境を提供します。

## 主な特徴

- **高性能 CPU コア**: 検証済みの MAME ソースに基づく HD61700 命令セットを C 言語で実装。
- **MicroPython フレームワーク**: 周辺ロジックを MicroPython で記述しており、カスタマイズが容易。
- **モダンな表示サポート**: ILI9341 320x240 TFT LCD に対応。専用ベゼル表示とタッチインターフェース (XPT2046) を統合。
- **外部キーボード対応**: HID USB キーボード (Host モード) およびシリアル (UART) 入力をサポート。
- **ストレージ**: SD カードをサポート。ROM、RAM 状態の保存、スクリーンショットの保存が可能。
- **状態管理**: RAM およびレジスタの状態を保存・復元するステートセーブ機能を搭載。
- **通信**: PIO UART による仮想 RS-232C (MMIO 0x0C00) をサポート。

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
├── roms/                   # ROM イメージ (本プロジェクトには含まれません)
└── README.md               # 本ファイル
```

## ステータス

### ✅ 完了済み
- [x] HD61700 命令セット (C core)
- [x] LCD コントローラ・エミュレーション (C 言語による高速化)
- [x] USB キーボード・ホスト・サポート
- [x] タッチパネル (XPT2046) 統合
- [x] SD カード (SPI) サポート
- [x] ステートセーブ (JSON/Binary)
- [x] PIO UART (MMIO 0x0C00) サポート

### 📋 ロードマップ
- [ ] RAM バンク拡張 (Bank 2/3)
- [ ] 256色 VRAM 拡張
- [ ] WiFi ネットワーク (Pico W)
- [ ] VGA/HDMI 出力

## ライセンス

*ライセンス情報は現在保留中です。*

## 謝辞

- MAME プロジェクトによる HD61700 の研究成果に基づいています。
- Google Deepmind の AI アシスタント Antigravity とのペアプログラミングによって作成されました。
