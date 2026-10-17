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
  moddotds64.c            # dotds64 MicroPython モジュール（64ドット表示フィックスの高速化）
  micropython.cmake       # ビルドシステム設定

mp/
  boot.py                 # 起動前の GPIO 初期状態設定（main.py より前に実行される）
  main.py                 # エントリポイント
  draw_text.py            # LCD文字描画の共通ヘルパー（起動/メニュー系UI全体で共用。
                           # main.py がトップレベルで import し、他はそれ以降どこから
                           # importしても実質コストゼロ。詳細はモジュール docstring）
  pb1000.py               # PB1000System クラス（PB1000FDDMixin / PB1000StateIOMixin を多重継承）
  pb1000_fdd.py           # 仮想 FDD 関連（2026-08-22、コンパイル時ヒープ断片化対策で pb1000.py から分割）
  pb1000_state_io.py      # save_state()/load_state() とフックレジストリ（同上分割）
  display_init.py         # LCD/SD/タッチの起動時初期化（プロファイルピッカーより前に必要な
                           # 部分だけを pb1000.py から分離。main_boot.py がモジュールレベルで
                           # import、pb1000.py 自体は create_system() 内での遅延 import）
  lcd_controller_c.py     # lcd_c モジュールの Python ラッパー
  main_boot.py            # 起動・初期化処理
  main_input_keyboard.py  # キーボード入力マネージャ（旧 main_input.py から分割。
                           # main.py Step 8b の中で最もコンパイルコストが大きい部分）
  main_input_touch.py     # タッチパネル入力マネージャ（同上分割）
  main_input_joystick.py  # ジョイスティック入力マネージャ（同上分割、[joystick] enable時のみ import）
  main_input_cursor.py    # カーソルキーリピートマネージャ（同上分割）
  main_runtime.py         # CPU 実行ループ補助
  main_actions.py         # スクリーンショット・save-state・ディスクスワップ
  main_cleanup.py         # 終了処理・メモリダンプ
  emulator_menu.py        # GUI+F7 ランタイムメニュー（メニューエンジン本体。583行）
  emulator_menu_ram.py    # RAM Save UI（コンパイルコスト分離のため分割。RAM Load は 2026-08-22 に削除）
  emulator_menu_capture.py # VRAM Save・Full Capture（同上）
  emulator_menu_debug.py  # Hook Status・CPU Status（同上）
  emulator_menu_display_actions.py # Display カテゴリの各ハンドラ（同上）
  emulator_menu_system_actions.py  # System カテゴリの Reset/Reboot/NEW ALL/速度設定（同上）
  funckey_bar.py          # 画面下部ファンクションキーバー
  boot_session.py         # プロファイル選択 UI
  boot_status.py          # 起動ステータスオーバーレイ（起動中のみ・[overlay]）
  clock_overlay.py        # 常時時計表示（実行中のみ・[overlay] show_clock、TIME$/DATE$を直読み）
  mem_overlay.py          # 常時ヒープ空き容量表示（実行中のみ・[overlay] show_mem_free）
  config.py               # pb1000.ini 読み込み
  pio_uart.py             # PIO ソフト UART（RS-232C）
  keymap.py / keymap.map  # キーボードマッピングテーブル（keymap.mapはtools/keymap_editor.pyのJSONから生成）
  ili9341.py              # ILI9341 TFT ドライバ (320x240)
  st7796.py               # ST7796 TFT ドライバ (480x320)
  xpt2046.py              # XPT2046 タッチパネルコントローラドライバ
  sdcard.py               # SD カード SPI ドライバ
  disk_select_ui.py       # 仮想 FDD ディスクイメージ選択 UI
  fdd_protocol.py         # 仮想 FDD (MD-100) コマンド/データプロトコル
  fdd_storage.py          # 仮想 FDD ストレージバックエンド
  md100_dos.py            # MD-100 DOS 層（pb1000es dos.pas 移植）
  ntp_sync.py             # WiFi 経由 NTP 時刻同期
  debug.py                # REPL 向け CPU/レジスタデバッグ補助
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
| `get_reg8(idx)` / `set_reg8(idx, val)` | 8 ビット特殊レジスタの読み書き。idx: 0=PE, 1=PD, 2=IB, 3=UA, 4=IA, 5=IE, 6=TM, 7=予約 |
| `get_reg16(idx)` / `set_reg16(idx, val)` | 16 ビットレジスタの読み書き。idx: 0=IX, 1=IY, 2=IZ, 3=US, 4=SS, 5=KY |
| `get_sreg(idx)` / `set_sreg(idx, val)` | ステータスレジスタの読み書き |
| `set_registers(bytes)` | レジスタ一式をまとめて復元（save-state 用） |
| `load_rom(slot, data)` | ROM バイナリをロード（slot: 0=内蔵ROM, 1=拡張ROM） |
| `load_ram(slot, data)` | RAM バイナリをロード（slot: 0=標準RAM, 1..3=拡張RAMバンク） |
| `read_mem(addr)` / `write_mem(addr, val)` | 任意アドレスへの単発メモリアクセス（デバッグ用） |
| `get_ram_view()` / `get_bank_view(n)` / `get_exp_ram_view()` / `get_ext_work_view()` | 各種静的バッファへのゼロコピー `bytearray` ビューを取得 |
| `set_has_exp_ram(bool)` | Bank 1 の有無を CPU コアへ通知（後方互換名） |
| `set_bank_present(bank, bool)` | Bank 1..3 の有無を CPU コアへ通知。呼ぶと以後の `reset()` はファイルパス自動検出（`detect_all_banks()`）を行わなくなる |
| `is_sleeping()` | CPU が SLP（スリープ）状態かを返す |
| `set_port_callbacks(read_fn, write_fn)` | ポート I/O コールバック登録 |
| `set_mem_callbacks(read_fn, write_fn)` | メモリアクセスコールバック登録 |
| `set_lcd_char_callback(fn)` | LCD 文字検出コールバック登録 |
| `set_call_hook(addr, fn)` | サブルーチンフック登録 |
| `clear_call_hook(addr)` | サブルーチンフック解除 |
| `set_call_hook_enabled(addr, bool)` | フック有効・無効の切り替え |
| `set_mem_write_hook(addr, fn)` | メモリ書き込みフック登録（詳細は §6.1） |
| `clear_mem_write_hook(addr)` | メモリ書き込みフック解除 |
| `set_mem_write_hook_enabled(addr, bool)` | メモリ書き込みフックの有効・無効切り替え |
| `set_uart_repl_enabled(bool)` | GP0/GP1 のネイティブ UART0 REPL の受信(RX)割り込みだけを有効・無効切り替え(`irq_set_enabled()` のみ、排他 IRQ ハンドラの再登録はしない)。出力(print()/REPL の TX)には影響しない |
| `set_port_direct(tx, rx, beep, freq, duty)` | C ダイレクト UART・ビープ初期化 |
| `press_row_ki(row, ki)` | キーマトリクスへのキー入力 |
| `release_row_ki(row, ki)` | キーマトリクスからキーを解放（解放後にポストリリースパルスを自動設定） |
| `get_last_key()` | 最後に受信した HID スキャンコードを取得（読み出しでクリア）。PIO-USB ビルド（`USE_PIO_USB=ON`）では、このタイミングで `usb_host_core_task()`（USB ホストのサービス処理）も内部で呼び出す。`mp/` 内のキー入力ループ（起動時メニュー・Emulator Menu を含む）は全てこの関数経由でキーを読むため、ここに集約することで個々のループ側で明示的にサービス呼び出しを行う必要がない |
| `get_held_cursor_key()` | 現在物理的に押下中のカーソルキースキャンコードを返す（0=なし） |
| `steer_next_key_int(row)` | 次の KEY_INT を指定行に向け即時発火させる（カーソルリピートで使用） |
| `process_usb_key(scancode, pressed)` | キーイベントを手動で C コアへ投入するテスト用フック（実際の USB HID レポート解析は `usb_host_core.c` が C 内部で直接処理しており、このモジュールを経由しない） |
| `build_time()` | ファームウェアのビルド日時文字列を返す |

### `lcd_c` モジュール（`src/modlcd_controller.c`）

HD61830 LCD コントローラエミュレーションと SPI レンダリング。

| 関数 | 説明 |
| --- | --- |
| `init()` / `clear()` | 内部状態の初期化・VRAM/カラー VRAM のクリア |
| `ctrl(data)` / `write(data)` / `read()` | LCD コントローラプロトコルへの生アクセス（制御・データ書き込み・読み出し） |
| `setup_display(spi_id, cs, dc, scale, x, y, baudrate=0)` | SPI ディスプレイへの出力設定 |
| `render()` | dirty フラグが立っていれば SPI 経由で LCD を描画 |
| `wait_for_idle()` | 進行中の SPI 転送完了を待機 |
| `is_dirty()` / `mark_dirty()` / `clear_dirty()` | dirty フラグ管理 |
| `get_vram()` | 現在のモノクロ VRAM バイト列を取得 |
| `get_color_vram()` | per-pixel カラー VRAM（VDP）バイト列を返す |
| `get_pixel(x, y)` | 指定ピクセルの点灯状態を取得 |
| `blit_reversed(src, dst_off)` | VRAM をミラー転送 |
| `is_display_on()` | HD61830 の表示 ON/OFF 状態を返す |
| `load_charset(data)` | 文字認識用 `charset.bin` をロード |
| `set_colors(fg, bg)` / `set_bg_colors(on_bg, off_bg)` | 点灯・消灯ピクセルの RGB565 色設定 |
| `set_vdp_enable(bool)` / `get_vdp_enable()` | per-pixel カラー VRAM（VDP）の有効・無効 |
| `set_vdp_init_done(bool)` / `vdp_init_done()` | VDP「初期描画済み」フラグの強制設定・参照（§8 参照） |
| `vdp_sync_enable()` | VRAM→カラー VRAM（pages 0-3）を現在の色設定で同期し VDP を有効化 |
| `vdp_any_write()` / `vdp_write_count()` | VDP への書き込み有無・回数を取得 |
| `vdp_write(reg, data)` | VDP レジスタへの直接書き込み |
| `get_num_pages()` / `set_num_pages(n)` | 表示ページ数（64 ドット行対応）の取得・設定 |
| `set_debug(bool)` | LCD 書き込みトレースの有効・無効 |
| `set_scale(num, den=1)` | スケール設定（整数または分数） |
| `WIDTH` / `HEIGHT` | 画面サイズの定数（192 / 32） |

Python ラッパー `lcd_controller_c.py` の `LCDControllerC` クラスを通じて操作するのが標準。

### `usb_host` モジュール（`src/modusb_host.c`）

RP2350 の USB ホスト機能（TinyUSB HID）を MicroPython から操作。ネイティブホスト方式
（`USE_PIO_USB=OFF`、デフォルトでは不使用）と PIO-USB 方式（`USE_PIO_USB=ON`、デフォルト、
GP4/GP5）のどちらでも同じ API で操作できる（`src/usb_host_core.c` が内部で分岐）。

- `init()` / `probe()` / `task()`: USB ホストスタックの初期化・デバイス検出・タスク処理。
  `task()` は PIO-USB ビルドでのみ意味を持つ（`tuh_task()` を呼ぶ）。ネイティブビルドでは
  no-op（バックグラウンドタイマ経由で ISR 駆動されるため）。
- `start_bg_timer()` / `stop_bg_timer()`: バックグラウンドタスク実行用タイマの開始・停止。
  ネイティブビルドのみ有効。PIO-USB ビルドでは no-op（`tuh_task()` はメインループ・
  `hd61700.get_last_key()` から呼ばれる、下記参照）。
- **PIO-USB ビルドでのサービス呼び出しの一元化**: ISR 駆動のバックグラウンドタイマが使えない
  ため、`tuh_task()` 相当（`usb_host_core_task()`）を非ISRコンテキストから定期的に呼ぶ必要が
  ある。メインループ（`mp/main.py`）だけでなく、起動時メニュー（`boot_session.py`）や
  Emulator Menu（`emulator_menu.py`）等、全てのキー入力ループが呼ぶ `hd61700.get_last_key()`
  の内部からも呼ばれる（`src/modhd61700.c`）。**新しいキー入力ループを追加する場合は、
  必ず `hd61700.get_last_key()` 経由でキーを読むこと** — 独自に HID 状態を読むループを
  作ると、PIO-USB ビルドでは USB ホストがサービスされずキー入力が効かなくなる。
- `debug()` / `debug2()` / `debug3()` / `debug4()`: PIO-USB の実機ブリングアップ用診断関数
  （列挙イベント履歴・EP0 エラー等）。ネイティブビルドでは全て 0 を返す。
- 実際の HID レポート解析（キー押下・解放の判定）は C 内部で完結する: `src/usb_host_core.c` の `tuh_hid_report_received_cb()` が `c_kb_process_usb_key_extern()` を直接呼び出し、Python 側を経由しない。
- `hd61700.keyboard_config_adv()` / `keyboard_config_base()` とセットで使用（キーマップ設定）。
- `hd61700.process_usb_key(scancode, pressed)` は手動でキーイベントを注入するテスト用フックであり、`usb_host` モジュールではなく `hd61700` モジュールに属する。

### `dotds64` モジュール（`src/moddotds64.c`）

`mp/ext/dotds_64dot.py`（64ドット表示モードの内部フィックス、§9・`extension_api.md` 参照）専用の
ネイティブ call_hook 実装を提供する、小さな独立モジュール。DOTDS (`&H022C`) と1文字クイック表示
(`&H02BD`) の置き換え処理はホットパス（画面更新のたび／PRINT で文字を書くたびに呼ばれる）のため、
MicroPython 経由の呼び出しオーバーヘッドを避けてパフォーマンスを確保する目的でC実装している。

| 関数 | 説明 |
| --- | --- |
| `dotds_hook()` | DOTDS の置き換え処理。`mp/ext/dotds_64dot.py` の `_dotds_override()` と同一ロジック |
| `char_hook()` | 1文字クイック表示の置き換え処理。同 `_char_display_override()` と同一ロジック |

**設計方針**: この機能は `hd61700` CPU コア本体には含めていない（サブルーチンフックの実装を
コアから分離し、独立したモジュールとして構成する方針）。`hd61700` 側が公開するのは、他のモジュールが
CPU 状態を読むための汎用的で最小限の C 関数のみで、64ドット対応固有のロジックは一切持たない。
LCD 側の状態アクセスは既存の `lcd_c_get_state()`（`modlcd_controller.c`）をそのまま利用する。

| 関数（いずれも Python 非公開の extern 関数） | 説明 |
| --- | --- |
| `uint8_t hd61700_get_reg(int idx)` | 汎用レジスタ `$idx` を読む（`hd61700.get_reg()` と同等） |
| `uint8_t hd61700_mem_read(uint16_t addr)` | CPU バス経由の1バイト読み出し（`hd61700.read_mem()` と同等）。MMIO判定や UART受信割り込み/スリープ解除などの副作用を含むため、実際の「CPUが1回メモリを読む」動作を再現したい場合にのみ使う |
| `uint8_t hd61700_ram_read(uint16_t ram_offset)` | メインRAM（`0x6000`–`0x7FFF`、RAM相対オフセット指定）への副作用なし直接読み出し。バルクコピー（DOTDS の LEDTP 転送など）で `hd61700_mem_read()` を大量に繰り返し呼ぶと UART 副作用が何百回も再実行されてしまうため、その用途向けに用意 |

`mp/ext/dotds_64dot.py` の `register()` は `import dotds64` を試み、成功すれば
`dotds64.dotds_hook`/`char_hook` を call_hook として登録する。モジュールが存在しない
（古い）ファームウェアでは `ImportError` を捕捉し、Python実装（`_dotds_override()`/
`_char_display_override()`）に自動フォールバックする。

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

> **注意**: 上記は「命令フェッチ直前、PC がフック対象アドレスと一致」した場合の汎用経路の
> 動作。これとは別に、`CAL IM16`（0x70-0x77）・`JP IM16`（0x30-0x37）・`JR`（0xB0-0xB7）の
> 各オペコードは、ジャンプが実際に発生する**前**にターゲットアドレスで直接 `call_hook` を
> 呼び出す専用チェックを持っており、こちらは条件によって挙動が異なる（CAL: push を丸ごと
> スキップ／JP・JR: ジャンプだけをスキップしスタックには一切触れない）。同じ登録アドレスが
> `JP $`（レジスタ間接ジャンプ）等それ以外の経路で到達された場合は上記の汎用経路（SS から
> 2 バイト pop）にフォールバックするため、**呼び出し元の命令の種類によって、同一アドレスへの
> フックでもスタックへの副作用が異なる**。狭い一箇所だけを狙い撃ちしたい場合（呼び出し元アドレスが
> 常に固定で 1 種類しかない等）は、対象アドレスではなく**呼び出し元のアドレス**にフックを登録し、
> フック関数側で手動でスタックへ push してから本来の処理へ迂回させる、という手法が使える。

### インターセプト方式 / 素通し方式（前処理フック）の使い分け

フック関数の**戻り値**で、対象ルーチンを完全に置き換える（インターセプト）か、フック実行後に
本来の ROM コードへ処理を戻して続行させる（素通し＝前処理フック）かを選べる。

- 戻り値なし（`return` を書かない = `None`）、または真値を返す → **インターセプト**（従来通り）。
  対象アドレスの本来の命令は一切実行されず、CAL 側は push された戻り先へ RTN 相当の復帰、
  JP/JR 側はジャンプ自体がなかったことになり次の命令へ進む。
- 明示的に `False` を返す → **素通し（passthrough）**。フックの処理が終わった後、
  本来の push+jump（CAL）／ジャンプ（JP・JR）がそのまま実行され、対象アドレスの ROM コードは
  普通に走る。つまりフックは「本来のサブルーチンの直前に割り込む前処理」として使える。

```python
def my_pre_hook():
    # 本来の処理の前にログを取る・レジスタを覗く・条件によって何かする、等
    print("about to enter ROM routine")
    return False   # 本来の ROM コードへ処理を継続させる

system.register_call_hook(0x1234, my_pre_hook, owner="myext")
```

素通しを選んだ場合でも、対象アドレスに到達した瞬間にもう一度フックが呼ばれることはない
（`hd61700.c` 側で 1 回限りの抑制フラグ `hook_suppress_active` により、CAL/JP/JR 専用チェックと
汎用経路チェックが同じ着地を二重に検出しないようになっている）。

この機構は「前処理（フック→本来のコード実行）」はサポートするが、「後処理（本来のコードが
`RTN` で戻ってきた後に追加処理を挟む）」は直接はサポートしない。対象ルーチンの `RTN` 先で
別途フックを仕込む、あるいはフック関数側でスタック上の戻り先を自分で書き換えて別のトランポリン
アドレスへ差し替える、といった手動の工夫が必要になる。

### Python API

```python
# フック登録（新規エントリはデフォルトで有効）
# owner は任意のラベル文字列。省略時は fn.__name__ を使用（ラムダの場合は
# 判別できないことが多いため、拡張モジュールでは明示的に渡すことを推奨）。
system.register_call_hook(0x5E20, my_handler, owner="myext")

# フック解除
system.unregister_call_hook(0x5E20)

# 有効・無効の切り替え（解除せず一時停止）
system.disable_call_hook(0x5E20)
system.enable_call_hook(0x5E20)

# 登録済みフックの一覧を取得（アドレス, owner, enabled のタプルのリスト）
# エミュレータメニューの「Hook Status」画面もこれを使って表示している。
system.list_call_hooks()
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

### 6.1 メモリ書き込みフック（Mem Write Hook）

CALL フックとは別に、任意アドレス範囲への**メモリ書き込み**をインターセプトする仕組み。
`c_mem_direct_write()` の先頭で `c_mem_write_hook_dispatcher()` が呼ばれ、登録範囲に該当する書き込みごとに
Python 関数を `fn(addr, data, bank)` の形で呼び出す。関数が `True` を返すと、その書き込み自体をキャンセルできる。

```python
# フック登録（アドレス範囲 addr_start..addr_end、省略時は 1 バイトのみ）
# owner は call_hook と同様、任意のラベル文字列（省略時は fn.__name__）。
system.register_mem_write_hook(0x68D0, my_write_handler, addr_end=0x68D0, owner="myext")

# フック解除
system.unregister_mem_write_hook(0x68D0)

# 有効・無効の切り替え（解除せず一時停止）
system.disable_mem_write_hook(0x68D0)
system.enable_mem_write_hook(0x68D0)

# 登録済みフックの一覧を取得（addr_start, addr_end, owner, enabled のタプルのリスト）
system.list_mem_write_hooks()
```

### 6.2 Hook Status（エミュレータメニュー）

`system.list_call_hooks()` / `system.list_mem_write_hooks()` を使って、現在登録されている
call_hook・mem_write_hook をアドレス・登録元(owner)・有効/無効状態つきで一覧表示するメニュー項目。
Win+F7 のエミュレータメニューから **Hook Status** を選択すると表示される（`emulator_menu_debug.py`
の `_do_hook_status()`。CPU Status とともに、稼働中セッションでの選択時にオンデマンドで
コンパイルされる。上下キーでスクロール、BREAK で閉じる。デバッグ時に「どの拡張がどの
アドレスをフックしているか」「MENU 表示モードなどで意図通り無効化されているか」を確認する用途。

実際の使用例として `mp/ext/dotds_64dot.py` が DSPMD レジスタ（0x68D0）へのメモリ書き込みフックを登録し、
値に応じて CALL フックの有効・無効を切り替えている（64 ドット表示行対応）。ただし
`[display] lcd_height = 32`（デフォルト）の場合はこの仕組み自体が常に無効化され、
ROM 本来の実装がそのまま動作する（32 ドットモードではこのフィックスは不要かつ有害なため）。

### 6.3 ネイティブ Dirty-Range 監視（Native Dirty Watch）

`register_mem_write_hook()`（6.1節）は Python 側の `fn(addr, data, bank)` を書き込みごとに
呼び出すため、1バイトや数バイトのレジスタ監視には適している一方、**数KB規模の広い範囲を
監視すると著しい速度低下を招く**。原因は2つある。

1. `set_mem_write_hook()` を1つでも登録すると、`mem_writebyte()`/`_iz()`/`_stack()`
   （`src/hd61700.c`）は、**登録済み全フックの範囲を合算した `[min_addr, max_addr]`**
   内の書き込みについて、`ram_ptr`/`bank_ptr` への直接ポインタ代入（最速パス）を一律で
   やめ、`mem_write()` コールバック（`c_mem_direct_write()`、複数の範囲チェックを伴う）
   経由に切り替える。個々のフックの監視範囲が狭くても、**合算範囲が広ければ無関係な
   アドレスまで速度が落ちる**。
2. 合算範囲のうち、実際にそのフックの監視範囲に当たる書き込みは、1バイトごとに
   `mp_call_function_n_kw()`（MicroPythonインタープリタの関数呼び出し）を発生させる。
   これはCPUコアのネイティブな命令実行とは比較にならないコストで、対象範囲への書き込みが
   頻発する場合（例: PB-1000のファイル領域はBASICプログラムの実行ワークエリアと隣接し、
   数KB〜RAM1全体に及ぶことがある）、エミュレーション全体が体感できるレベルで遅くなる。
   （`mp/ram_autosave.py` の初期実装でこの問題が実際に発生し、本節の仕組みに切り替えた。）

この問題を避けるため、「個々の書き込みをPythonへ通知する必要はなく、**前回確認した時点
からどのバイト範囲が変化したか（min/max区間）だけ分かればよい**」というケース向けに、
C側だけで完結する軽量な代替手段を用意した。

```c
// hd61700.set_native_dirty_watch(slot, addr_start, addr_end, bank_filter=-1)
// slot: 0..NATIVE_DIRTY_WATCH_MAX-1 (4) の監視スロット番号。
// bank_filter: 省略(-1)で任意バンク、0-3を指定すると該当バンクの書き込みのみ対象
//              (0x8000-0xFFFFのバンク切り替え領域向け)。
// 同じslotへの再登録は既存設定を上書きし、保留中のdirty区間をリセットする。
hd61700.set_native_dirty_watch(0, 0x6941, 0x7FFF)
hd61700.set_native_dirty_watch(1, 0x8000, 0xFFFF, 1)  // bank==1 のみ

// hd61700.clear_native_dirty_watch(slot)
hd61700.clear_native_dirty_watch(1)

// hd61700.poll_native_dirty_watch(slot) -> (min, max) | None
// dirty区間を読み取ってクリアする（poll都度1回だけ消費）。
// メインループに1回など、低頻度で呼ぶことを想定。
rng = hd61700.poll_native_dirty_watch(0)
if rng is not None:
    lo, hi = rng

// hd61700.peek_native_dirty_watch(slot) -> bool
// dirty状態を消費せずに確認するだけ（poll_native_dirty_watch()と違い区間を
// クリアしない）。デバウンス待機中に「まだ書き込みが続いているか」だけを
// 毎周確認したい場合に使う（mp/ram_autosave.py の例、15節参照）。
if hd61700.peek_native_dirty_watch(0):
    ...  # まだdirty -- デバウンス期限を延長する、等
```

`register_mem_write_hook()` との違い:

- 監視対象の書き込みを**C側の整数比較のみ**で検出する（`native_dirty_track()`、
  `src/hd61700.c`）。Pythonの関数呼び出しは一切発生しない。
- **`ram_ptr`/`bank_ptr` の直接ポインタ代入（最速パス）を無効化しない。** 6.1節の
  `mem_write_hook_active` とは完全に独立した、並行して動く別個の仕組み。
- 書き込みをキャンセルすることはできない（`set_mem_write_hook()`の`True`返却に相当する
  機能は無い）。観測専用。
- 設定はCPUリセット（`hd61700.reset()`）後も `modhd61700.c` 側のシャドウ設定
  （`native_dirty_watch_cfg[]`）から復元される（6.1節のcall_hook/mem_write_hookと同様の
  パターン）。ただし保留中のdirty区間自体はリセットで失われる。

「広い範囲を監視するが、個々の書き込み内容そのものには興味がない」ケース（`mp/ram_autosave.py`
がその代表例、15節参照）ではこちらを使うこと。個々の書き込み内容（アドレス・データ・バンク）
をその場で判定する必要がある狭い範囲の監視（レジスタ監視等）には引き続き6.1節の
`register_mem_write_hook()` を使う。

---

## 7. LCD 文字検出（旧シリアルコンソール、Python 側配線は廃止済み）

> [!NOTE]
> この節が記述する検出パイプライン自体は C コア（`src/modhd61700.c`）に現在も残っているが、
> 2026-08-22 に Python 側の唯一の呼び出し元だった `PB1000System.console_uart` プロパティ
> セッター（`cpu_core.set_lcd_char_callback(...)` を呼んでいた）を削除したため、
> `py_lcd_char_cb` は常に `MP_OBJ_NULL` のままとなり、このパイプライン全体が恒久的に
> 無効（no-op）になっている。`py_lcd_char_cb != MP_OBJ_NULL` のガードが最初から存在する
> ため安全に無効化されており、C コア自体の変更（再ビルド）は行っていない。以下は
> 参考として残す、稼働当時の実装メモ。

### 動作原理（稼働当時）

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

`[display] vdp_enable`（`pb1000.ini`、F1 セットアップメニューから変更可）で起動時のオン/オフを設定できる
（内部的には `set_vdp_enable()` を呼ぶだけ。2026-08-22 以前はエミュレータメニューの実行時トグルだった）。

実際にカラー VRAM が描画に使われるには `set_vdp_enable(True)` に加えて `vdp_init_fill_done` フラグ
（`vdp_init_done()` で参照可能）が立っている必要がある。このフラグは通常 CPU 側の VDP ポート書き込み
（`lcd_c.vdp_write()` reg=2、0xFF 以外のデータ）で自動的に立つが、`get_color_vram()` 経由でカラー VRAM に
直接書き込む拡張（`mp/ext/vram_loader.py` 等）はこの経路を通らないため、`set_vdp_init_done(True)` を明示的に
呼ばない限り古いモノクロ VRAM 側の描画にフォールバックしてしまう。

### グローバル LCD カラー

ピクセル単位の VDP モードを使わずに、点灯/消灯ピクセルの色をグローバルに設定することもできる。
`lcd_c` モジュールの `set_colors(fg_rgb565, bg_rgb565)` は RGB565 値を受け取る。
`pb1000.ini` の `[display]` セクションではこれを RGB332 として保存する:

| ini キー | 形式 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `fg_color` | RGB332（0–255） | `0`（黒） | 点灯ピクセルの色 |
| `bg_color` | RGB332（0–255） | `180`（0xB4、青みがかったグレー） | 消灯ピクセルの色 |

`main_boot.py` が起動時に RGB332 → RGB565 へ変換し、`system.lcd.set_colors()` 経由で適用する。

---

## 9. 拡張 API（`ext/` モジュール）

BASIC から Pico 2 の周辺機能（I2C、SPI、WiFi 等）を `CALL` 命令で利用するための仕組み。

- `mp/ext/` ディレクトリにモジュールを置くだけで起動時に自動ロード。
- モジュールが `register(system)` 関数を持っていれば呼び出される。
- BASIC との値受け渡しは拡張ワークエリア（0x5F00–0x5FFF）を使用。
- `ext/` 配下のモジュール（`bank_loader.py`・`vram_loader.py` 等）はいずれも**任意**の機能であり、
  BASIC から明示的に `CALL` して初めて動作する。エミュレータ本体の基本動作には影響しない
  （`dotds_64dot.py` は例外で、64ドット表示モードの内部フィックスだが、`[display] lcd_height`
  の設定に応じて自動的に有効・無効が切り替わり、32ドットモードでは常に無効）。

詳細は `doc/extension_api.md` を参照。

---

## 10. PIO UART（RS-232C）

RP2350 の PIO ステートマシンを使ったソフト UART で仮想 RS-232C を実装。

- デフォルト: GP6（TX）/ GP13（RX）
- ピン番号は `pb1000.ini` の `[rs232c] tx_pin`/`rx_pin` で変更可能（`main_boot.py` の
  `initialize_usb_host_and_pio()` が読み取り、`PioUart(tx_pin=..., rx_pin=...)` へ渡す）。
- ボーレートは `pb1000.ini` の `[rs232c] baudrate` で設定（デフォルト 9600 bps）。
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

`mp/main_input_cursor.py` の `CursorRepeatManager` クラスが PB-1000 エミュレーション中のカーソルキー自動リピートを実装する。

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

## 15. RAM オートセーブ（`mp/ram_autosave.py`）

### 目的

ユーザーの明示的な「RAM Save」（Emulator Menu）を行わずに電源が落ちた場合、通常は
前回保存時点まで内容が失われる。実機 PB-1000 はバッテリーバックアップRAMのため、
明示保存しなくても内容が保持される。この挙動差を縮めるため、ファイル作成・更新・
削除のタイミングで `ram0.bin`（+ `ram1.bin`、拡張RAM使用時）を内部フラッシュ上の
キャッシュ（`/.autosave/<プロファイル名>/`、常に内部フラッシュのルートvfs。プロファイル
自身の保存先 `<profile_dir>` -- 通常は `/sd/rams/<名前>` -- とは無関係。後述の「キャッシュの
保存先」参照）へミラーする。

ユーザーの明示保存ファイルとは完全に分離されており、このキャッシュを一切上書きしない。

### キャッシュの保存先

`<profile_dir>` をそのまま使う（＝プロファイルの保存先と同じ場所にキャッシュを置く）
実装を当初試したが、`profile_dir` は通常 `/sd/rams/<名前>`（`boot_session.
PROFILE_ROOT`）であり、SD カード上になる。これには2つの問題がある。

1. このモジュールのデバウンス時間（後述）は内部フラッシュの実測レイテンシ
   （`mp/test/bench_ram_autosave_write.py`、ram0のみ約130ms・ram1込みで約480ms）を
   基準にしているが、SD カードのI/O特性はこれとは異なる。
2. SD カードへの書き込み中、LCD表示にSDカードアクセス時と同様のちらつきが見える
   （SPIバスをLCDと共有しているため、SDへのアクセスがLCD側のSPI通信と衝突する）。
   また、そもそも「不意の電源断からの保護」という目的上、取り外し可能なSDカードの
   有無や状態に依存するのは本来望ましくない。

このため `RamAutosave._compute_cache_dir()` はプロファイルの保存先を一切参照せず、
常に内部フラッシュのルートvfs配下 `/.autosave/<プロファイル名>/`（プロファイル名は
`profile_dir` の末尾のディレクトリ名。複数プロファイルのキャッシュが衝突しないための
名前空間分け。プロファイル未選択時は固定で `_default`）に書き込む。

### 検出方法

PB-1000 のファイルシステムは `references/sysvars.txt` に記載の固定番地ワークエリア
（`HIMEM`/`BASEN`/`MEMEN`/`DATDI`/`BASDI`/`DIREN`、`&H6941`-`&H694C`）でファイル領域の
境界を管理している。性質の異なる2種類の監視を組み合わせて検出する。

1. **境界ポインタフック**（固定, `&H6941`-`&H694C`、`register_mem_write_hook()`,
   6.1節）: ファイルの新規作成・削除・サイズ変更（領域境界の移動）を検出。発火時は
   ②の監視範囲を再計算し、キャッシュへの反映を行う（詳細は後述の「差分フラッシュ」
   節）。この範囲はわずか12バイトで書き込み頻度も低いため、Pythonコールバック方式の
   コストは無視できる。
2. **ファイル本体のネイティブ Dirty-Range 監視**（動的, `HIMEM`..`DIREN`,
   `hd61700.set_native_dirty_watch()`、6.3節）: 同一サイズの上書き（固定フォーマット
   のゲームのセーブデータ等、①では検出できないケース）を検出。書き込みアドレスの
   最小〜最大区間をC側だけで追跡し（Python呼び出しは発生しない）、デバウンス期限が
   切れた時点で `poll_native_dirty_watch()` により区間を取得し、その区間だけをキャッシュへ
   部分書き込み（seek+write）する。ファイル全体の再書き込みは発生しない。

   当初は②も `register_mem_write_hook()`（6.1節）で実装していたが、PB-1000のファイル
   領域はBASICプログラムの実行ワークエリアと隣接し数KB〜RAM1全体に及ぶため、
   書き込みごとのPython関数呼び出しコストと、合算監視範囲全体でCPUコアの直接ポインタ
   書き込み（最速パス）が無効化される副作用が重なり、エミュレーション全体が体感できる
   レベルで遅くなった。6.3節のネイティブ機構へ切り替えてこれを解消している。

   さらに注意が必要なのは、`HIMEM`..`DIREN` は「ファイルの保存済みバイト列」だけでは
   なく、**そこに読み込まれている実行中プログラムの作業用メモリでもある**という点。
   機械語ゲームはスコア・スプライト座標・タイマー等の作業変数を自分自身のロード領域
   （＝ファイル領域の一部）に持つしかないため、SAVE/LOAD/KILLを一切経由しない通常の
   プレイ中にも②は継続的にdirtyになる。このため②の発火も①と同じデバウンス（後述）に
   かけている。即座にフラッシュしていた初期実装では、ゲームプレイ中にファイル操作が
   一切無いにもかかわらずメインループのほぼ毎周でフラッシュが走り、体感できる速度低下と、
   起動直後から残るキャッシュ（＝次回起動時の復元確認が頻発する）の両方の原因になった。

`DIREN`（ファイル領域の終端）が `0x7FFF` を超える場合、ファイル領域が拡張RAM
（bank1, `0x8000`-`0xFFFF`）側に広がっていると判断し、②の監視範囲をそちらにも拡張する
（`bank==1` の書き込みのみ対象。`ram2`/`ram3` はスコープ外）。この際、bank1側の監視範囲の
下限は `max(HIMEM, 0x8000)` であり、常に `0x8000` 固定ではない。`HIMEM` 自体が `0x8000`
以上（拡張RAM側）まで上がっているプロファイルも有効な配置であり、その場合
`0x8000`〜`HIMEM`未満の区間はファイル内容ではなく、BASIC自身の数値・文字列変数
テーブル（`FORSK`/`GOSSK`/`TONDT`/`DTTB`/`TOSDT`/`PTSDT`、`references/sysvars.txt`）。
下限を`HIMEM`まで正しく上げないと、この区間がファイル領域と誤認識され、ファイル操作が
一切無い通常のBASIC実行中でも変数が更新されるたびに②がdirty化し、不要なフラッシュが
継続的に発生する。

①のコールバックはCPU命令実行中に呼ばれるため、軽量な範囲追跡のみを行い、実際の
ファイルI/Oは行わない。②はそもそもPythonコールバックを経由しない。実際のキャッシュ
書き込みは `RamAutosave.poll()`（メインループから1周に1回呼ばれる）で行う。

### フラッシュのデバウンス

①②どちらも、発火のたびに即座に同期フラッシュしていると体感できる速度低下を招く
（内部フラッシュへの実書き込みで ram0 のみでも約130ms、ram1 込みで約480ms —
`mp/test/bench_ram_autosave_write.py` の実機ベンチマーク値）。理由はそれぞれ異なる。

- ①（境界ポインタフック）: `BASEN`（BASICファイル領域の終端）は、ファイルの新規作成・
  削除だけでなく、**プログラムの1行を入力・編集して確定するたび**（トークン化後の
  バイト数が変わるたび）にも動く。つまりプログラム入力中はENTERを押すたびに発火する。
- ②（ネイティブ Dirty-Range 監視）: 前節の通り、機械語ゲームの実行中は作業変数の
  書き換えそのものが継続的に②をdirty化させる。ファイル操作が一切無い通常のプレイ中も
  ほぼ毎メインループ周でdirtyになりうる。

このため、両方の発火から実際のフラッシュ書き込みまでを**共通のタイマーでデバウンス**
している（`RamAutosave.poll()`、`_FULL_FLUSH_DEBOUNCE_MS` / `_FULL_FLUSH_MAX_WAIT_MS`）。
①が発火した直後は②の監視範囲再計算（ファイルI/Oなし、軽量）だけを行い、実際の
`_flush_pending()` は「①の発火または②のdirty状態（`peek_native_dirty_watch()`で消費
せずに確認）が `_FULL_FLUSH_DEBOUNCE_MS`（デフォルト800ms）の間どちらも起きなかった
時点」まで遅延する。連続して書き込みが続いている間は遅延時刻が先延ばしされ、活動が
落ち着いた時点でまとめて1回だけフラッシュされる。ただし無制限に先延ばしすると不意の
電源断に対する保護が弱まるため、`_FULL_FLUSH_MAX_WAIT_MS`（デフォルト5000ms）を上限と
し、ゲームプレイ等で活動が続いている間も最低この間隔ではフラッシュされる。

### 差分フラッシュ

デバウンスが効いても、発火した際に毎回 `ram0`（最大8KB）／`ram1`（最大32KB）全体を
書き直していては、1回のフラッシュ自体のコスト（実測 ram0のみで約130ms、ram1込みで
約480ms）は変わらない。デバウンス期限が切れた際に実行される `_flush_pending()` も、
以下の理由により**差分書き込みだけで十分**なことが分かるため、そのように実装している。

ファイル領域の操作で変化しうるバイトは、必ず次のいずれかに分類できる。

1. 境界ポインタ自身（`&H6941`-`&H694C`、12バイト、`HIMEM`より下の固定領域）
2. `HIMEM`以降の本体内容（ディレクトリエントリのシフトなどを含む）
3. `HIMEM`より下のワークエリア（SAVE/LOAD用の`IOBUF`等、ファイル状態そのものとは
   無関係な使い捨て領域）

(2)は②（ネイティブ Dirty-Range 監視）が①の発火要因とは無関係に常時捕捉しているため、
境界ポインタフック発火時に改めて全体を見直す必要はない。(1)は②の監視範囲外（`HIMEM`
より下）にあるため、①がこのデバウンス窓の中で一度でも発火していた場合（`RamAutosave.
_pending_include_ptr`）に限り、固定12バイトとして無条件にキャッシュへ書き込む
（①が発火していない窓では②だけが変化しており、ポインタは動いていないので書く必要が
ない）。(3)はファイルの正しさに影響しないため無視してよい。つまり「このデバウンス窓で
①が発火していれば固定12バイト」∪「②が捕捉した差分区間」の和集合だけをキャッシュへ
書き込めば、ファイル新規作成・削除・サイズ変更のいずれについても取り落としなく反映できる。

これが成立しない例外が2つあり、その場合のみ1回だけ全体書き込みへフォールバックする
（`_need_full_ram0`/`_need_full_ram1`）。

- `HIMEM` 自体が動いた場合（`CLEAR`文等）: ②（slot 0）の監視範囲が切り替わる直前に
  書かれたバイトを取り落とす可能性があるため。
- `DIREN` が `0x7FFF` を初めて超え、bank1監視（②のslot 1）が新規に有効化された場合:
  同様に、有効化される直前にbank1へ書かれたバイトを取り落とす可能性があるため。

いずれも頻度の低い遷移イベントなので、その回だけ全体書き込みのコストを払っても
全体としての影響は小さい。

### 既知の制限

- プロファイル自身の明示保存に `ram1.bin` が無い状態（＝RAM1用バッファが
  `PB1000System.__init__()` 時点で未確保）で、オートセーブキャッシュ側にのみ
  `ram1.bin` がある場合、起動時の復元ではRAM1側のキャッシュは復元されない
  （警告ログを出してスキップする）。先に一度明示的な RAM Save を行っていれば
  発生しない。
- `ram2.bin`/`ram3.bin`/`color_vram.bin` はキャッシュ対象外（スコープ外）。

### 実行中のON/OFF切り替え（Win+F8）

デバウンス・差分フラッシュ適用後も、ソフトによっては flush 自体の頻度がまだ気になる
場合がある（例: 短い間隔でファイル内容を書き換え続けるゲーム）ため、`main.py` に
**Win+F8** でのランタイム切り替えを用意した。`RamAutosave.enabled`（デフォルト
`True`）を反転するだけで、`[emulator] ram_autosave` の設定（コンストラクト自体を
行うかどうか）とは独立。`poll()` の先頭で `enabled` を見て即 return するだけなので、
OFF中も①の境界ポインタフックと②のネイティブ Dirty-Range 監視そのものは動作し続ける
（どちらもPythonコールバック1バイトごとの呼び出しが無い/軽量なので、動かし続けること
自体のコストは無視できる）。実際に止まるのはフラッシュ書き込みI/O（本機能の実コスト、
1回あたり約130-480ms）だけ。OFFにしていた間の変更は保護されない（電源断からの保護を
明示的に手放す操作であり、設定でOFFにする場合と同じ）。ONに戻すと、OFF中に蓄積した
dirty区間を次のデバウンス完了時にまとめて1回フラッシュする（キャッチアップ）。

トグル時は `system.set_status()`（ゲーム画面下の専用バー、`pb1000.py`の
`_render_status_bar()`）に "Autosave: ON"/"Autosave: OFF" を約2秒間表示する。この
バーは "SYSTEM RESET" やPrtScnキャプチャ結果等でも使われる既存の共通領域で、
`boot_status.py`が描画するプロファイル名表示（画面左上）とは別の場所にあるため

### フラッシュ時のログ出力（`[emulator] ram_autosave_log`）

`pb1000.ini`の`[emulator] ram_autosave_log`（デフォルト`false`）をtrueにすると、
フラッシュ発生ごとに`[ram_autosave] flush #N slot=S NbytesB full=F Nms`形式の1行を
シリアルへ出力する。デフォルトで無効なのは、アクティブに書き込みが続くプロファイルでは
1秒間に複数回フラッシュが発生し得るため（ログ自体の出力コストに加え、f-string形式だと
整形のたびに小さなヒープ確保が発生する — そのため実装は`print(..., sep="")`による
素のフォーマットで、f-stringは使っていない）。
競合しない。

### キャッシュ統計表示（Win+PageUp）

**Win+PageUp** で、起動からの累積フラッシュ回数・書き込みバイト数・平均所要時間を
同じステータスバーに表示する（`RamAutosave.stats_summary_line()`）。Win+F8と同じ
`system.set_status()`を使う。カウンタは `stat_flush_count`/`stat_bytes_written`/
`stat_total_flush_ms`等（`_stat_record()`が各フラッシュ成功後に更新、ログ出力は
しない）で、このブートセッション中のみ保持（再起動でリセット、ディスクには保存しない）。
あるソフトでフラッシュがどの程度発生しているかを目安として確認する用途。

### 起動時の復元（`restore_from_cache()`）

キャッシュファイルは全体が常に最新というわけではない（差分フラッシュ節を参照 --
`_flush_full()`によるフルリライトはキャッシュ新規作成時と `HIMEM`/`DIREN`自身の
監視範囲が切り替わった直後のみで、それ以外は`_flush_partial()`による部分書き込み）。
そのため`restore_from_cache()`は、ロード済みのram0/ram1全体をキャッシュで単純に
上書きするのではなく、**境界ポインタ12バイト＋キャッシュ自身に記録されているHIMEM
（＝キャッシュ作成/最終リライト時点の値ではなく、その12バイトとして最後に書かれた
値）からDIRENまでの内容範囲だけ**を重ね書きする。それ以外（OS作業変数等）は
`load_state()`がロードした値のまま変更しない。

これは2026-10-14に見つかった不具合の修正結果でもある: 以前のバージョンは
`f.readinto()`でファイル全体を一括上書きしており、追跡範囲外（キャッシュ生成後
更新されていない可能性がある区間）の古いバイト列で、実際には正しいライブRAM
内容を上書きしてしまっていた。復元直後にキー入力は効くが内部動作が異常になる
（リセットしても直らない — RAM内容の破損であり、リセットはRAM内容を変更しないため）
という症状で発覚した。
