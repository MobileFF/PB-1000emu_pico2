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
- 特殊キー（NumLock / GUI+F7）の処理ディスパッチ
- 例外と終了時クリーンアップの管理

持たせないもの:

- 入力詳細の実装
- USB / PIO / UART 初期化詳細
- スクリーンショット保存処理
- save-state 処理詳細

---

### `mp/main_boot.py`

役割:

- UART コンソール初期化
- ディスプレイと `PB1000System` の初期化
- 標準 ROM 読み込み
- USB Host / PIO UART の初期化
- C キーボードモードの設定（F11 / F9 コールバック登録含む）

依存先:

- `display_init.py`（`init_display()` -- モジュールレベルで import。ピッカーより前に必要なため、
  `PB1000System` を丸ごと引き込む `pb1000.py` からは分離されている）
- `pb1000.py`（`PB1000System` -- `create_system()` 内でのローカル import。プロファイル
  ピッカー／F1セットアップメニューが終わるまでヒープを消費しないよう遅延している）
- `pio_uart.py`（`PioUart` -- `initialize_usb_host_and_pio()` 内でのローカル import）
- `usb_host`
- `hd61700`
- `keymap.py`

---

### `mp/main_input_keyboard.py` / `_touch.py` / `_joystick.py` / `_cursor.py`

かつては `main_input.py` という1ファイル（535行）だったが、`main.py` の Step 8b
（`load_state()` 直後、起動中で最もヒープに余裕がある地点で4つのマネージャを生成する箇所）
で、一度に535行をコンパイルすると実機で `MemoryError` になる事例が観測されたため、
用途ごとに4ファイルへ分割した。`main.py` はそれぞれを個別の `gc.collect()` を挟んで
importする（`main_input_joystick.py` は `[joystick] enable=true` の時のみ）。

役割:

- UART からの入力受信
- 入力キュー管理
- キー押下 / 解放タイミング制御
- sleep 時の `BRK` / `ON_INT` 制御
- タッチパネル入力の PB-1000 キー変換
- ジョイスティック入力の PB-1000 キー変換

主な公開クラス:

- `KeyboardInputManager`（`main_input_keyboard.py`）: UART キーボード入力管理
- `TouchInputManager`（`main_input_touch.py`）: タッチパネル入力管理
- `JoystickInputManager`（`main_input_joystick.py`）: ジョイスティック入力管理（デフォルト GP18–21/26/27）
- `CursorRepeatManager`（`main_input_cursor.py`）: カーソルキー自動リピート（ROM の KEY_INT ISR に release/press サイクルを合成）

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
- `service_timer_realtime(system, last_tick_ms, *, ms_per_tick)`: 実時間 (`time.ticks_ms()`) ベースのタイマ tick 処理。`timer_tick_ms > 0`（デフォルト）の場合に使用されるメインのタイマ経路。CPU が SLP（スリープ）状態でもステップ数に依存せず進むため、TIME$ が停止しない。
- `service_timer_ticks()`: ステップ数ベースの旧タイマ処理。`timer_tick_ms == 0` の場合のみ使われるレガシーなフォールバック（デバッグトレース等向け）。

---

### `mp/main_actions.py`

役割:

- PrintScreen によるスクリーンショット保存（PBM + VRAM ダンプ）
- VRAM ダンプ出力
- save-state 要求処理
- ディスクスワップ処理委譲

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
- Toggles はビープのみ（他は `pb1000.ini`/F1 セットアップメニューに統一、2026-08-22）。
  FD Swap / RAM セーブ / VRAM セーブ / Full Capture / 色設定 / Reset / Reboot / NEW ALL など
  即時反映が必要な操作をリアルタイムに実行
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

- `/sd/rams/` ディレクトリの走査・列挙
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

### `mp/display_init.py`

役割:

- LCD（ILI9341/ST7796）・SDカード・タッチパネルの起動時初期化（`init_display()`）
- `PB1000System`（`pb1000.py`）が一切不要な、プロファイルピッカーより前の段階でだけ使う処理
  なので、`pb1000.py` から分離されている（2000行超の `pb1000.py` を丸ごとコンパイル・常駐
  させずに済む -- ヒープが最も逼迫するプロファイルピッカー／F1セットアップメニューの区間を
  楽にするための分割。経緯は `mp/main_boot.py` 冒頭のコメント参照）

---

### `mp/pb1000.py`

役割:

- `PB1000System` クラス（ボードレベルエミュレーション統括）。`PB1000FddMixin`
  （`pb1000_fdd.py`）と `PB1000StateIOMixin`（`pb1000_state_io.py`）を多重継承で
  取り込む（2026-08-22、コンパイル時のヒープ断片化対策で分割。両ファイルの説明は下記）
- メモリマップ管理（ROM / RAM / バンク切り替え / 拡張ワークエリア）
- ポート I/O / MMIO コールバック
- ビープ（PWM）制御
- サブルーチンフック登録（`register_call_hook` / `unregister_call_hook` / `enable_call_hook` / `disable_call_hook`）
- 拡張 API ロード（`_ext_load_modules`）
- 表示更新（`update_display` / `force_full_redraw`）

シリアルコンソール（LCD 文字検出 → GP4/GP5 UART 出力）は 2026-08-22 に廃止された。
検出パイプライン自体は C コア側に残るが、Python 側の唯一の呼び出し元
（`console_uart` プロパティセッター）を削除したため恒久的に無効（詳細は `dev_guide.md` §7）。

---

### `mp/pb1000_fdd.py`

役割:

- `PB1000FddMixin`（`pb1000.py` の `PB1000System` に多重継承で統合）
- 仮想 FDD（MD-100）制御（`_handle_virtual_fdd_port_write` / `configure_virtual_fdd` /
  `swap_disk` / `discover_virtual_fdd_config` 等）
- ストレージパス解決ヘルパー（`_get_storage_path` 等）

`pb1000.py` 単体（旧 1943 行）が実機でコンパイル時 MemoryError を起こしたため split。

---

### `mp/pb1000_state_io.py`

役割:

- `PB1000StateIOMixin`（`pb1000.py` の `PB1000System` に多重継承で統合）
- save-state / load-state（`save_state` / `load_state`）
- CALL・メモリ書き込みフックレジストリ（`register_call_hook` 等）

`pb1000_fdd.py` と同じ理由で split。

---

### `mp/pio_uart.py`

役割:

- RP2350 PIO ステートマシンを利用したソフト UART（RS-232C 仮想ポート）
- ボーレートは `pb1000.ini` の `[rs232c] baudrate` で設定（デフォルト 9600 bps）

---

## Dependency Direction

```text
main.py
  -> main_boot.py
  -> main_input_keyboard.py / _touch.py / _cursor.py (Step 8b, module-level gc.collect() each)
  -> main_input_joystick.py (Step 8b, same, only when [joystick] enable=true)
  -> main_runtime.py
  -> main_actions.py
  -> main_cleanup.py
  -> emulator_menu.py   (lazy import, GUI+F7 時のみ)

main_boot.py
  -> display_init.py     (module-level import -- needed before the profile picker)
  -> pb1000.py            (lazy import, inside create_system() -- after the picker)
     -> pb1000_fdd.py / pb1000_state_io.py (module-level, mixed into PB1000System)
  -> pio_uart.py          (lazy import, inside initialize_usb_host_and_pio())
  -> hd61700 / usb_host / keymap
  -> boot_session.py
  -> config.py

main_input_keyboard.py / _touch.py / _joystick.py / _cursor.py
  -> system object API
  -> keymap.py (main_input_joystick.py, for named-constant key names)
  -> hd61700 (main_input_cursor.py: get_held_cursor_key()/steer_next_key_int())
  -> machine.Pin (main_input_joystick.py)

main_runtime.py
  -> system object API
  -> hd61700 CPU core API

main_actions.py
  -> system object API
  -> hd61700 / usb_host / keymap

emulator_menu.py
  -> system object API
  -> funckey_bar.py
  -> main_actions.py (disk swap)

pb1000.py
  -> pb1000_fdd.py / pb1000_state_io.py (module-level, mixed in via multiple inheritance)
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
    - 特殊キー処理（NumLock=リセット / GUI+F7=エミュレータメニュー、ディスクスワップは同メニューの "FD Swap" から）
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

`main_input.py` は既に `main_input_keyboard.py`/`_touch.py`/`_cursor.py`/`_joystick.py` に、
`pb1000.py` は既に `pb1000_fdd.py`/`pb1000_state_io.py` に、`emulator_menu_ext.py` は既に
`emulator_menu_ram.py`/`_capture.py`/`_debug.py` に分割済み（詳細は上記「Current Module
Split」参照）。まだ実施していない、より大きな単位の将来オプションとしては、これらの
機能別ファイル群をディレクトリ単位のパッケージへまとめ直すことが考えられる:

```text
mp/
  main.py
  config.py
  lcd_controller_c.py
  pio_uart.py

  boot/       # main_boot.py, boot_session.py, display_init.py
  input/      # main_input_*.py
  runtime/    # main_runtime.py
  actions/    # main_actions.py, main_cleanup.py
  pb1000/     # pb1000.py, pb1000_fdd.py, pb1000_state_io.py
  menu/       # emulator_menu*.py, funckey_bar.py
  ext/        # 拡張 API モジュール
```
