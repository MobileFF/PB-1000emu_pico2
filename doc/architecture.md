# PB-1000 Emulator Architecture

## Purpose

この文書は、PB-1000 エミュレータの Python 側アーキテクチャを整理し、各モジュールの責務と依存関係を明確にするためのものである。

## Design Goals

- `main.py` をフロー制御の入口に限定する
- 入力、起動、実行ループ補助、保存処理、診断処理を責務単位で分ける
- `PB1000System` と CPU コアへのアクセスをできるだけ局所化する
- デバッグ補助を通常実行フローから分離し、整理しやすくする
- 将来の拡張機能を追加する位置が分かる構造にする

---

## Current Module Split

### `mp/main.py`

役割:

- 実行フローの入口
- 起動順序の組み立て
- メインループでの各ヘルパー呼び出し
- 特殊キー（NumLock / GUI+F6 / GUI+F7）の処理ディスパッチ
- 例外と終了時クリーンアップの管理

持たせないもの:

- 入力詳細の実装
- USB / PIO / UART 初期化詳細
- スクリーンショット保存処理
- save-state 処理詳細
- wake trace 診断

---

### `mp/main_boot.py`

役割:

- UART コンソール初期化
- ディスプレイと `PB1000System` の初期化
- 標準 ROM 読み込み
- USB Host / PIO UART の初期化
- C キーボードモードの設定（F11 / F9 コールバック登録含む）

依存先:

- `pb1000.py`
- `pio_uart.py`
- `usb_host`
- `hd61700`
- `keymap.py`

---

### `mp/main_input.py`

役割:

- UART からの入力受信
- 入力キュー管理
- キー押下 / 解放タイミング制御
- sleep 時の `BRK` / `ON_INT` 制御
- タッチパネル入力の PB-1000 キー変換
- ジョイスティック入力の PB-1000 キー変換

主な公開クラス:

- `KeyboardInputManager`: UART キーボード入力管理
- `TouchInputManager`: タッチパネル入力管理
- `JoystickInputManager`: ジョイスティック入力管理（デフォルト GP18–21/26/27）

---

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

---

### `mp/main_actions.py`

役割:

- PrintScreen によるスクリーンショット保存（PBM + VRAM ダンプ）
- VRAM ダンプ出力
- save-state 要求処理
- ディスクスワップ処理委譲

---

### `mp/main_diag.py`

役割:

- wake trace 用スナップショット生成
- 診断文字列整形
- wake path トレース

備考:

- 通常実行フローからはほぼ独立した診断補助モジュールである

---

### `mp/main_cleanup.py`

役割:

- 終了時のワークエリア出力
- メモリダンプ出力

---

### `mp/emulator_menu.py`

役割:

- GUI+F7 で起動するランタイム設定メニュー
- メニュー中は CPU ステッピングが暗黙的に一時停止
- シリアルコンソール / RS-232C / vFDD / ビープ / ジョイスティック / カラー VDP / RAM セーブ/ロード / VRAM セーブ / 色設定などをリアルタイムに切り替え
- メニュー終了後に `system.force_full_redraw()` でベゼル＋LCD を復元

---

### `mp/funckey_bar.py`

役割:

- 画面下部に常時表示される LCKEY/MENU/CAL/CALC のタッチバー
- `.fkbar.raw` スプライトをブリットして描画
- タッチ座標のヒットテストを行い対応キーを発火

主な公開クラス:

- `FuncKeyBar`

---

### `mp/boot_session.py`

役割:

- `/sd/profiles/` ディレクトリの走査・列挙
- プロファイル選択 UI（タイムアウト付き）の表示
- プロファイルディレクトリパスの解決

主な公開関数:

- `scan_profiles()`
- `get_profile_dir(name)`
- `select_profile_ui(display, profiles, default, timeout_ms)`

---

### `mp/config.py`

役割:

- `pb1000.ini` の INI 形式読み込み
- `get_bool()` / `get_int()` / `get_str()` によるセクション・キー単位のアクセス
- グローバル設定とプロファイル設定のマージ

---

### `mp/lcd_controller_c.py`

役割:

- C 拡張モジュール `lcd_c`（`modlcd_controller.c`）の Python ラッパー
- LCD dirty フラグ管理（`mark_dirty()` / `clear_dirty()` / `is_dirty()`）
- スケール設定・色設定の C 側への同期
- VDP（per-pixel カラー VRAM）の有効・無効切り替え
- フォールバックパス（SPI 非接続時の Python 描画）

主な公開クラス:

- `LCDControllerC`

---

### `mp/pb1000.py`

役割:

- `PB1000System` クラス（ボードレベルエミュレーション統括）
- メモリマップ管理（ROM / RAM / バンク切り替え / 拡張ワークエリア）
- ポート I/O / MMIO コールバック
- 仮想 FDD（`_handle_virtual_fdd_port_write`）
- ビープ（PWM）制御
- save-state / load-state
- シリアルコンソール（LCD 文字検出 → UART 出力: `_on_lcd_char_output`）
- サブルーチンフック登録（`register_call_hook` / `unregister_call_hook` / `enable_call_hook` / `disable_call_hook`）
- 拡張 API ロード（`_ext_load_modules`）
- 表示更新（`update_display` / `force_full_redraw`）

---

### `mp/pio_uart.py`

役割:

- RP2350 PIO ステートマシンを利用したソフト UART（RS-232C 仮想ポート）
- ボーレートは `pb1000.ini` の `[pio_uart] baudrate` で設定（デフォルト 9600 bps）

---

## Dependency Direction

```text
main.py
  -> main_boot.py
  -> main_input.py
  -> main_runtime.py
  -> main_actions.py
  -> main_cleanup.py
  -> emulator_menu.py   (lazy import, GUI+F7 時のみ)

main_boot.py
  -> pb1000.py
  -> pio_uart.py
  -> hd61700 / usb_host / keymap
  -> boot_session.py
  -> config.py

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

emulator_menu.py
  -> system object API
  -> funckey_bar.py
  -> main_actions.py (disk swap)

pb1000.py
  -> lcd_controller_c.py (LCDControllerC)
  -> hd61700 (CPU core C module)
  -> lcd_c   (LCD controller C module)
```

---

## Runtime Flow

1. `main.py` が設定ファイルをロードし、UART コンソールを準備する
2. `boot_session.select_profile_ui()` でプロファイルを選択する
3. `main_boot.init_display_only()` がディスプレイを初期化する
4. `main_boot.create_system()` が `PB1000System` を初期化する
5. `main_boot.load_default_roms()` が ROM をロードする
6. `main_boot.initialize_usb_host_and_pio()` が USB Host / PIO UART を準備する
7. `main_boot.configure_c_keyboard()` が C コア側のキーボード受け口を設定する（F11 コールバック含む）
8. `FuncKeyBar` を画面下部に描画する
9. `system.power_on()` でエミュレータを起動する
10. メインループで以下を順に実行する:
    - PIO UART bridge
    - CPU slice 実行
    - 特殊キー処理（NumLock=リセット / GUI+F6=ディスクスワップ / GUI+F7=エミュレータメニュー）
    - キーボード入力 / タッチ入力 / ジョイスティック入力
    - ステータス処理 / スクリーンショット / save-state
    - フレーム更新
    - タイマ tick 処理
11. 終了時は `main_cleanup.dump_shutdown_state()` がダンプを出力する

---

## Remaining Architectural Tasks

1. `_create_input_managers()` は通常起動時の入力構成を入口で把握しやすくするため、`main.py` に残す
2. デバッグ用の派生エントリポイントは、通常系の構成が固まった後に必要最小限で再作成する
3. helper モジュールが増えたら `mp/runtime/` `mp/input/` `mp/actions/` のようなパッケージ構成への移行を検討する

---

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
  emulator_menu.py
  funckey_bar.py
  boot_session.py
  config.py
  lcd_controller_c.py
  pb1000.py
  pio_uart.py

  # future option
  boot/
  input/
  runtime/
  actions/
  diag/
  ext/        # 拡張 API モジュール
```
