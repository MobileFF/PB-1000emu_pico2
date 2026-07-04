# 開発ガイド

このガイドは、PB-1000 エミュレータの内部アーキテクチャを理解したい開発者向けに作成されました。

---

## 1. システム・アーキテクチャ

パフォーマンスと柔軟性のために C と MicroPython のハイブリッド構成を採用しています。

- **CPU コア (C)**: `hd61700.c` が HD61700 命令デコード・レジスタ管理・基本タイミングを処理。
- **MicroPython フレームワーク**: 高レベルシステムロジック・周辺機器エミュレーション・メインループ。
- **C モジュール（ブリッジ）**: カスタム MicroPython モジュールが CPU コントロールや周辺機能を Python に公開。

---

## 2. ディレクトリ構造

```text
src/
  hd61700.c / .h          # CPU エミュレーションコア
  modhd61700.c            # hd61700 MicroPython モジュール
  lcd_controller.c / .h   # C 言語による高速 LCD レンダリング
  modlcd_controller.c     # lcd_c MicroPython モジュール
  usb_host_core.c / .h    # USB ホストドライバコア
  modusb_host.c           # usb_host MicroPython モジュール
  micropython.cmake       # ビルドシステム設定

mp/
  main.py                 # エントリポイント
  pb1000.py               # PB1000System クラス
  lcd_controller_c.py     # lcd_c モジュールの Python ラッパー
  main_boot.py            # 起動・初期化処理
  main_input.py           # 入力マネージャ（キーボード・タッチ・ジョイスティック）
  main_runtime.py         # CPU 実行ループ補助
  main_actions.py         # スクリーンショット・save-state・ディスクスワップ
  main_cleanup.py         # 終了処理・メモリダンプ
  emulator_menu.py        # GUI+F7 ランタイムメニュー
  funckey_bar.py          # 画面下部ファンクションキーバー
  boot_session.py         # プロファイル選択 UI
  config.py               # pb1000.ini 読み込み
  pio_uart.py             # PIO ソフト UART（RS-232C）
  keymap.py / keymap.json # キーボードマッピングテーブル
  ili9341.py              # ILI9341 TFT ドライバ
  ext/                    # 拡張 API モジュール（自動ロード）

hardware/
  pb1000_emulator.kicad_sch
```

---

## 3. C モジュール詳細

### `hd61700` モジュール（`src/modhd61700.c`）

CPU コアの制御と周辺 I/O 全般を担う中心モジュール。主な API:

| 関数 | 説明 |
| --- | --- |
| `reset(debug)` | CPU をリセット（オプションでデバッグフラグ有効化） |
| `execute(cycles, stop_pc)` | 最大 cycles サイクル実行 |
| `get_pc()` / `set_pc(addr)` | プログラムカウンタの読み書き |
| `get_reg(idx)` / `set_reg(idx, val)` | 汎用レジスタの読み書き（整数インデックス） |
| `get_reg8(idx)` / `set_reg8(idx, val)` | 8 ビット特殊レジスタ（IA/IB/IE/UA）の読み書き |
| `load_rom(data, slot)` | ROM バイナリをロード |
| `load_ram(data, slot)` | RAM バイナリをロード |
| `set_port_callbacks(read_fn, write_fn)` | ポート I/O コールバック登録 |
| `set_mem_callbacks(read_fn, write_fn)` | メモリアクセスコールバック登録 |
| `set_lcd_char_callback(fn)` | LCD 文字検出コールバック登録 |
| `set_call_hook(addr, fn)` | サブルーチンフック登録 |
| `clear_call_hook(addr)` | サブルーチンフック解除 |
| `set_call_hook_enabled(addr, bool)` | フック有効・無効の切り替え |
| `set_port_direct(tx, rx, beep, freq, duty)` | C ダイレクト UART・ビープ初期化 |
| `press_row_ki(row, ki)` | キーマトリクスへのキー入力 |
| `release_row_ki(row, ki)` | キーマトリクスからキーを解放（解放後にポストリリースパルスを自動設定） |
| `get_last_key()` | 最後に受信した HID スキャンコードを取得（読み出しでクリア） |
| `get_held_cursor_key()` | 現在物理的に押下中のカーソルキースキャンコードを返す（0=なし） |
| `steer_next_key_int(row)` | 次の KEY_INT を指定行に向け即時発火させる（カーソルリピートで使用） |

### `lcd_c` モジュール（`src/modlcd_controller.c`）

HD61830 LCD コントローラエミュレーションと SPI レンダリング。

| 関数 | 説明 |
| --- | --- |
| `setup_display(spi, cs, dc, scale, x, y)` | SPI ディスプレイへの出力設定 |
| `render()` | dirty フラグが立っていれば SPI 経由で LCD を描画 |
| `is_dirty()` / `mark_dirty()` / `clear_dirty()` | dirty フラグ管理 |
| `get_vram()` | 現在の VRAM バイト列を返す |
| `set_colors(fg, bg)` | 点灯・消灯ピクセルの RGB565 色設定 |
| `set_vdp_enable(bool)` | per-pixel カラー VRAM（VDP）の有効・無効 |
| `set_vdp_init_done(bool)` / `vdp_init_done()` | VDP「初期描画済み」フラグの強制設定・参照（§8 参照） |
| `set_scale(num, den)` | スケール設定（整数または分数） |

Python ラッパー `lcd_controller_c.py` の `LCDControllerC` クラスを通じて操作するのが標準。

### `usb_host` モジュール（`src/modusb_host.c`）

RP2350 の USB ホスト機能（TinyUSB HID）を MicroPython から操作。

- `process_usb_key(hid_report)`: HID レポートを解析して C コア側へキーイベントを投入。
- `hd61700.keyboard_config_adv()` / `keyboard_config_base()` とセットで使用。

---

## 4. CPU コアの詳細

### メモリマッピング

`hd61700` モジュールは 2 つのメモリモードをサポートしています:

1. **C-Managed（デフォルト）**: `rom0_buf`・`ram_buf`・`bank*_buf` などの静的バッファに直接アクセス。最高パフォーマンス。
2. **Python-Managed（デバッグ）**: メモリアクセスごとに Python コールバックが呼ばれる。大幅に低速。

詳細なメモリマップは `doc/memory_map.md` を参照。

### 周辺機器 I/O

- **ポート I/O**: HD61700 の P0–P7 ポートは `set_port_callbacks()` で Python コールバックにマップ。
  - C ダイレクトモードでは `set_port_direct()` で C 側が直接 UART TX・ビープ PWM を処理。
- **LCD（HD61830）**: `lcd_c` モジュールが C 側でエミュレート。SPI ディスプレイオブジェクトを C 側に渡す。
- **SIO MMIO（0x0C00–0x0C03）**: PIO UART との仲介ブリッジ。受信データのキューイングと TX 送信を処理。

---

## 5. SD カードプロファイルシステム

### プロファイルディレクトリ

`/sd/rams/<name>/` が 1 つのプロファイル。起動時に `boot_session.scan_profiles()` が列挙し、選択 UI で選ぶ。
`/sd/rams/` ディレクトリはエミュレータメニューの RAM Save / Load でも共用される。

```text
/sd/rams/
  default/
    pb1000.ini    # プロファイル個別設定（省略可）
    rom0.bin      # プロファイル個別 ROM（省略可、なければグローバルを使用）
    ram0.bin      # 保存された標準 RAM
    ram1.bin      # 保存された拡張 RAM1（省略可）
    regs.json     # 保存された CPU レジスタ
```

### 設定マージ

フラッシュ `/pb1000.ini` → `/sd/pb1000.ini` → `<profile>/pb1000.ini` の順でロードされ、後者が前者を上書きする。`config.py` の `load_config(profile_dir)` が返すマージ済み dict を使う。

### ファイル検索優先順位

`_get_storage_path()` は以下の順でファイルを探す:

1. `profile_dir/` （指定時）
2. `/sd/`
3. `/roms/`
4. `/`（ルート）

---

## 6. サブルーチンフック（Call Hook）機能

BASIC の `CALL` 文（内部的には push+JP）で到達する任意アドレスをインターセプトし、Python / C ネイティブ関数を呼び出す仕組み。

### 動作原理

`hd61700_execute()` のループ先頭（命令フェッチ前）に PC チェックを挿入。PC が登録済みアドレスに一致した場合:

1. Python / C ネイティブ関数を呼び出す（引数なし）
2. スタックから戻り先アドレスを pop×2 で取り出し、`+1`（ワードアドレス繰り上げ）して PC をセット（RTN シミュレーション）
3. 15 サイクルを消費して次の命令へ

CAL 命令を使わない BASIC の CALL 文（push+JP 経路）にも対応できる理由はこのため。

### Python API

```python
# フック登録（新規エントリはデフォルトで有効）
system.register_call_hook(0x5E20, my_handler)

# フック解除
system.unregister_call_hook(0x5E20)

# 有効・無効の切り替え（解除せず一時停止）
system.disable_call_hook(0x5E20)
system.enable_call_hook(0x5E20)
```

### C ネイティブフック

`MP_DEFINE_CONST_FUN_OBJ_0` で定義した native 関数を登録するとネイティブ速度で動作する:

```c
STATIC mp_obj_t hook_my_func(void) {
    /* hd61700 API 経由でレジスタ読み書き */
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(hook_my_func_obj, hook_my_func);
```

詳細は `doc/extension_api.md` と `references/PR2_CB_on_Unused_addr_implementation_plan.md` を参照。

---

## 7. シリアルコンソール（LCD 文字検出）

### 動作原理

LCD VRAM への書き込みを `c_lcd_direct_write()` がフックし、6 列ピクセルが揃うたびに `charset.bin`（0x20–0x7E）と照合して文字コードを判定する。

```text
CPU が LCD VRAM に書き込み
  → c_lcd_direct_write() が 6 列ピクセルを蓄積
  → cdet_match_charset() で charset.bin と照合
  → py_lcd_char_cb（Python コールバック）を呼び出し
  → PB1000System._on_lcd_char_output(code)
  → console_uart.write(bytes([code]))  ← UART に出力
```

### スペース文字の処理

スペース（0x20）のグリフはすべてゼロ（空白 LCD と同一）。空白エリアをスペースと誤認識して出力しないよう、`_cdet_row_has_text[4]` フラグを設け、その行で非スペース文字が検出された後のみスペースを出力する。

---

## 8. カラー表示（VDP）

`lcd_c` モジュールの per-pixel カラー VRAM を有効にすると、単色 2 値表示から任意色の per-pixel カラーに切り替えられる。

```python
system.lcd.set_vdp_enable(True)   # カラー VRAM 有効
system.lcd.set_vdp_enable(False)  # グローバル色設定に戻す
```

MMIO アドレス 0x0C20–0x0C24 で BASIC / マシン語から色 VRAM を操作できる（詳細は `doc/memory_map.md` 参照）。

エミュレータメニューの **Color VRAM** 項目でオン/オフ切り替え可能（内部的には `set_vdp_enable()` を呼ぶだけ）。

実際にカラー VRAM が描画に使われるには `set_vdp_enable(True)` に加えて `vdp_init_fill_done` フラグ
（`vdp_init_done()` で参照可能）が立っている必要がある。このフラグは通常 CPU 側の VDP ポート書き込み
（`lcd_c.vdp_write()` reg=2、0xFF 以外のデータ）で自動的に立つが、`get_color_vram()` 経由でカラー VRAM に
直接書き込む拡張（`mp/ext/vram_loader.py` 等）はこの経路を通らないため、`set_vdp_init_done(True)` を明示的に
呼ばない限り古いモノクロ VRAM 側の描画にフォールバックしてしまう。

---

## 9. 拡張 API（`ext/` モジュール）

BASIC から Pico 2 の周辺機能（I2C、SPI、WiFi 等）を `CALL` 命令で利用するための仕組み。

- `mp/ext/` ディレクトリにモジュールを置くだけで起動時に自動ロード。
- モジュールが `register(system)` 関数を持っていれば呼び出される。
- BASIC との値受け渡しは拡張ワークエリア（0x5F00–0x5FFF）を使用。

詳細は `doc/extension_api.md` を参照。

---

## 10. PIO UART（RS-232C）

RP2350 の PIO ステートマシンを使ったソフト UART で仮想 RS-232C を実装。

- デフォルト: GP6（TX）/ GP13（RX）
- ボーレートは `pb1000.ini` の `[pio_uart] baudrate` で設定（デフォルト 9600 bps）。
- `pio_uart.py` の `PioUart` クラスが実装。
- `service_pio_uart_bridge()` がメインループで SIO MMIO（0x0C00–0x0C03）と PioUart をブリッジ。

---

## 11. デバッグとトレース

### C 言語側のデバッグ

詳細な CPU トレースを有効にするには Python から呼び出す:

```python
import hd61700
hd61700.set_debug(True)     # CPU 命令トレース
hd61700.set_key_debug(True) # キー入力トレース
hd61700.set_lcd_debug(True) # LCD 書き込みトレース
```

### wake trace

`mp/main_diag.py` に wake 診断補助関数があり、スリープ復帰経路のトレースに使用する。通常実行フローからは独立。

---

## 12. ビルドシステム

`src/micropython.cmake` が USER_C_MODULES として MicroPython ビルドシステムに取り込まれる。
ビルド手順の詳細は `doc/build_guide.md` を参照。

---

## 13. カーソルキーリピート（`CursorRepeatManager`）

`mp/main_input.py` の `CursorRepeatManager` クラスが PB-1000 エミュレーション中のカーソルキー自動リピートを実装する。

### 動作原理

PB-1000 ROM の KEY_INT ISR はキーマトリクスの特定行を繰り返しスキャンし、
同一キーが「押されたまま」の場合はリピートしない（エッジ検出）。
カーソルキーのリピートは「意図的なリリース→再押下サイクル」を合成することで実現する。

```text
ARMED（押下検出）
  → 400ms 後に FIRE: release_key() + steer_next_key_int(row)
  → 175ms の RELEASE フェーズ（ROM が row-3 を空スキャンするのを待つ）
  → press_key() + steer_next_key_int(row)
  → 100ms の PRESS フェーズ
  → release_key() + steer_next_key_int(row)
  → RELEASE → PRESS サイクルを繰り返す
```

### RELEASE_MS のチューニング

ROM の KEY_INT ISR は row 0 と cursor row を交互にスキャンする（25ms 間隔）。
「hold state のクリア」には **7 回以上連続** して cursor row の KY = 0 が必要と判明。
175ms は最小動作確認値（150ms = 失敗）。

| パラメータ | デフォルト | 意味 |
| --- | --- | --- |
| `_DELAY_MS` | 400 ms | 初期押下からリピート開始までの遅延 |
| `_RELEASE_MS` | 175 ms | キーをマトリクスから離す時間（ROM デバウンス待ち） |
| `_INTERVAL_MS` | 100 ms | キーをマトリクスに入れる時間 |

### 依存 C API

| API | 説明 |
| --- | --- |
| `hd61700.get_held_cursor_key()` | 物理的に保持中のカーソルスキャンコードを返す |
| `hd61700.steer_next_key_int(row)` | `c_kb_ia_select` を指定行に設定し `c_kb_next_pulse_ms=0` で即時 KEY_INT 発火 |
| `hd61700.release_row_ki(row, ki)` | マトリクスを解放し `c_kb_post_release_pulses_remaining` を設定 |

---

## 14. UTF-8 の運用ルール

このプロジェクトでは、テキストファイルの文字コードを UTF-8 に統一する。

- Markdown、Python、設定ファイルは UTF-8 を使用する
- Python ソースは UTF-8 BOM なしを推奨する
- Windows PowerShell から直接保存する場合は文字化けに注意する

### Python での推奨ファイル操作

```python
from pathlib import Path

text = Path("doc/example.md").read_text(encoding="utf-8")
Path("doc/example.md").write_text(text, encoding="utf-8")
```

- PowerShell の `Set-Content` は環境によって BOM やコードページの影響を受けることがある
- 既存ファイルの再保存時は `utf-8-sig` で読み込み、`utf-8` で保存すると BOM 除去に使える
