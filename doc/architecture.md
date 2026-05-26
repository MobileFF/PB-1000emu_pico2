# PB-1000 Emulator Architecture

## Purpose

この文書は、現在の PB-1000 エミュレータの Python 側アーキテクチャを整理し、各モジュールの責務と依存関係を明確にするためのものである。

特に `mp/main.py` のリファクタリング後の責務分割を基準化し、今後の機能追加やドキュメント整備の土台にすることを目的とする。

## Design Goals

- `main.py` をフロー制御の入口に限定する
- 入力、起動、実行ループ補助、保存処理、診断処理を責務単位で分ける
- `PB1000System` と CPU コアへのアクセスをできるだけ局所化する
- デバッグ補助を通常実行フローから分離し、整理しやすくする
- 将来の拡張機能を追加する位置が分かる構造にする

## Current Module Split

### `mp/main.py`

役割:

- 実行フローの入口
- 起動順序の組み立て
- メインループでの各ヘルパー呼び出し
- 例外と終了時クリーンアップの管理

持たせないもの:

- 入力詳細の実装
- USB / PIO / UART 初期化詳細
- スクリーンショット保存処理
- save-state 処理詳細
- wake trace 診断

### `mp/main_boot.py`

役割:

- UART コンソール初期化
- ディスプレイと `PB1000System` の初期化
- 標準 ROM 読み込み
- USB Host / PIO UART の初期化
- C キーボードモードの設定

依存先:

- `pb1000.py`
- `pio_uart.py`
- `usb_host`
- `hd61700`
- `keymap.py`

### `mp/main_input.py`

役割:

- UART からの入力受信
- 入力キュー管理
- キー押下 / 解放タイミング制御
- sleep 時の `BRK` / `ON_INT` 制御
- タッチパネル入力の PB-1000 キー変換

主な公開クラス:

- `KeyboardInputManager`
- `TouchInputManager`

### `mp/main_runtime.py`

役割:

- PIO UART MMIO ブリッジ処理
- CPU ステップ実行補助
- フレーム更新タイミング判定
- タイマ tick の集中管理

主な公開関数:

- `service_pio_uart_bridge()`
- `run_cpu_slice()`
- `update_frame_if_due()`
- `service_timer_ticks()`

### `mp/main_actions.py`

役割:

- キーステータス表示
- PrintScreen 相当のスクリーンショット保存
- VRAM ダンプ
- save-state 要求処理

### `mp/main_diag.py`

役割:

- wake trace 用スナップショット生成
- 診断文字列整形
- wake path トレース

備考:

- 通常実行フローからはほぼ独立した診断補助モジュールである

### `mp/main_cleanup.py`

役割:

- 終了時のワークエリア出力
- メモリダンプ出力

## Dependency Direction

基本的な依存方向は次の通り。

```text
main.py
  -> main_boot.py
  -> main_input.py
  -> main_runtime.py
  -> main_actions.py
  -> main_cleanup.py

main_boot.py
  -> pb1000.py
  -> pio_uart.py
  -> hd61700 / usb_host / keymap

main_input.py
  -> system object API

main_runtime.py
  -> system object API
  -> hd61700 CPU core API

main_actions.py
  -> system object API
  -> hd61700 / usb_host / keymap

main_diag.py
  -> system object API
  -> hd61700 CPU core API
```

## Runtime Flow

1. `main.py` が UART コンソールを準備する
2. `main_boot.initialize_system()` がディスプレイと `PB1000System` を初期化する
3. `main_boot.load_default_roms()` が標準 ROM を読み込む
4. `main_boot.initialize_usb_host_and_pio()` が USB / PIO UART を準備する
5. `main_boot.configure_c_keyboard()` が C コア側のキーボード受け口を設定する
6. メインループで以下を順に実行する
7. PIO UART bridge
8. CPU slice 実行
9. キー入力 / タッチ入力
10. ステータス処理 / スクリーンショット / save-state
11. フレーム更新
12. タイマ tick 処理
13. 終了時は `main_cleanup.dump_shutdown_state()` がダンプを出力する

## Why This Split Matters

- 起動とランタイム責務が分離され、初期化調整の影響範囲を局所化できる
- 入力系の拡張を `main_input.py` 側で受けやすい
- save-state やスクリーンショットのような副作用の大きい処理を分離できる
- wake trace のような開発向け機能を通常実行から切り離しやすい
- `main.py` を読めば現在の実行順序を短時間で把握できる

## Remaining Architectural Tasks

1. `_create_input_managers()` は通常起動時の入力構成を入口で把握しやすくするため、`main.py` に残す
2. デバッグ用の派生エントリポイントは、通常系の構成が固まった後に必要最小限で再作成する
3. helper モジュールが増えたら `mp/runtime/` `mp/input/` `mp/actions/` のようなパッケージ構成への移行を検討する
4. 現在のモジュール境界を基にして、バンク RAM 拡張、CALL フック、SD プロファイル選択などの受け口を設計する

## Suggested Long-Term Package Layout

```text
mp/
  main.py
  main_boot.py
  main_input.py
  main_runtime.py
  main_actions.py
  main_diag.py
  main_cleanup.py

  # future option
  boot/
  input/
  runtime/
  actions/
  diag/
```
