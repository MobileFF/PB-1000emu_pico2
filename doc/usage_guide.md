# 利用ガイド

このガイドでは、PB-1000 エミュレータのセットアップ方法、起動方法、および操作方法について説明します。

---

## 1. 初期設定

### ROM イメージ

エミュレータが動作するためには、オリジナルの CASIO PB-1000 ROM イメージが必要です。これらはプロジェクトに含まれていません。

- `rom0.bin`: 内部 ROM（6KB、アドレス 0x0000–0x17FF）
- `rom1.bin`: システム ROM（32KB、アドレス 0x8000–0xFFFF、Bank 0）
- `charset.bin`（`/roms/` に配置、省略可）: シリアルコンソール機能（§6）が文字認識に使用。存在しない場合はエラーにならず、単に文字コード判定が行われない

### ディレクトリ構造（SD カード / フラッシュ）

```text
/                          # Pico フラッシュルート
├── main.py                # エントリポイント
├── pb1000.ini             # グローバル設定（省略可）
└── roms/                  # ROM / RAM フォールバック置き場
    ├── rom0.bin
    └── rom1.bin

/sd/                       # SD カード（推奨）
├── pb1000.ini             # グローバル設定上書き（省略可）
├── rams/                  # プロファイルディレクトリ（起動時選択 & RAM セーブ/ロード兼用）
│   ├── default/           # "default" プロファイル
│   │   ├── pb1000.ini     # プロファイル個別設定（省略可）
│   │   ├── rom0.bin       # プロファイル個別 ROM（省略可）
│   │   ├── ram0.bin       # 保存された標準 RAM
│   │   ├── ram1.bin       # 保存された拡張 RAM1（省略可）
│   │   ├── ram2.bin       # 保存された拡張 RAM2（Bank2、省略可）
│   │   ├── ram3.bin       # 保存された拡張 RAM3（Bank3、省略可）
│   │   └── regs.json      # 保存された CPU レジスタ
│   └── bench/             # 追加プロファイルの例
├── disks/                 # 仮想 FDD ディスクイメージ
│   └── disk1.img
└── screenshots/           # スクリーンショット保存先
```

設定の優先順位（低→高）: フラッシュ `/pb1000.ini` → `/sd/pb1000.ini` → `<プロファイル>/pb1000.ini`

---

## 2. 起動フロー

1. Pico 2 に電源を入れる。
2. **プロファイル選択 UI** が表示される。SD カード上の `/sd/rams/` を走査してリストアップ。
   - 上下キーまたはタッチでプロファイルを選択し EXE / タッチで決定。
   - タイムアウト（デフォルト 30 秒）で `default_profile` の設定値が自動選択される。
3. 選択したプロファイルのステートが自動ロードされ、PB-1000 LCD ベゼルが表示される。
4. ステートファイルが見つからない場合はコールドブート（新規起動）。

---

## 3. キーボード操作

### 特殊機能キー

| PC キー | 機能 | 備考 |
| :--- | :--- | :--- |
| **NumLock** | リセット | ハードウェアリセット（PC=0x0000） |
| **F11** | Save State | RAM・レジスタを現在のプロファイルに保存 |
| **PrintScreen** | スクリーンショット | LCD 内容を `/sd/screenshots/` に `.pbm` として保存 |
| **Win（GUI）+ F7** | エミュレータメニュー | 各種機能の切り替えメニューを開く |
| **Win（GUI）+ F6** | ディスクスワップ | 仮想 FDD のディスクイメージを切り替える |
| **Win（GUI）+ Esc** | エミュレータ終了 | MicroPython REPL に戻る |
| **Esc** | BREAK | BASIC プログラムの停止 / エラー解除 |
| **Enter** | EXE | コマンドの実行 |
| **Backspace** | BS | 文字の削除 |
| **Insert** | INS | 挿入モード |
| **矢印キー** | カーソル移動 | ↑↓←→（長押しでキーリピート） |
| **Alt（L/R）** | Shift | PB-1000 の Shift キー |
| **F1 – F4** | T13 – T16 | ファンクションキー |

### カーソルキーリピート

矢印キー（↑↓←→）を長押しすると、約 400ms の初期遅延の後、カーソルが自動的に繰り返し移動します。
これはエミュレータ側で ROM の KEY_INT ISR に対してリリース／再押下サイクルを合成することで実現しています。

### キーマッピング

エミュレータは HID スキャンコードを PB-1000 キーマトリクス（13 行 × 12 列）に変換します。

- アルファベット・数字: 直接マッピング
- 記号: PB-1000 相当キーにマッピング（例: PC の `Shift+2` → PB-1000 の `"`）
- **LCKEY / MENU / CAL**: それぞれ F5 / F6 / F7 にマッピング
- 詳細なマッピングテーブルは `mp/keymap.py` / `mp/keymap.json` を参照

---

## 4. タッチインターフェースと FuncKeyBar

### タッチパネル（T1–T16）

PB-1000 の 16 キータッチパネルは LCD のタッチスクリーンでエミュレートされます。

- LCD 表示領域（192×32 ピクセル相当エリア）をタップすると、対応するタッチキー（T1–T16）が発火。
- LCD 領域を 4×4 のグリッドに分割してタッチ座標を変換。

### FuncKeyBar

画面下部に LCKEY・MENU・CAL・CALC の 4 キー（またはそれに対応する画像）が常時表示されます。
タッチパネルでこのバーをタップすると対応キーが押下されます。

---

## 5. エミュレータメニュー（Win + F7）

メインループを一時停止して設定を変更できるメニューです。変更はリアルタイムに反映されます。

| 項目 | 機能 |
| :--- | :--- |
| **Serial Console** | LCD 文字検出 → UART 出力機能のオン/オフ |
| **RS-232C (PIO)** | PIO UART（仮想 RS-232C）のオン/オフ |
| **vFDD** | 仮想フロッピードライブのオン/オフ |
| **Beep** | ビープ音のオン/オフ（ミュート切り替え） |
| **Joystick** | ジョイスティック入力のオン/オフ |
| **Color VRAM** | per-pixel カラー表示（VDP）のオン/オフ |
| **FD Swap** | 仮想 FDD のディスクイメージを切り替える |
| **RAM Save** | 現在の RAM を `/sd/rams/` にスナップショット保存 |
| **RAM Load** | `/sd/rams/` から RAM スナップショットを復元 |
| **VRAM Save** | 現在の LCD VRAM を 4 ファイル（PBM/バイナリ）として保存 |
| **Foreground Color** | LCD 点灯ピクセルの色を変更 |
| **Background Color** | LCD 消灯ピクセルの色を変更 |

上下キーでカーソル移動、EXE で実行、BREAK でメニューを閉じます。

---

## 6. シリアル機能

### シリアルコンソール（Serial Console）

LCD の表示内容を文字として認識し、コンソール UART（GP4/GP5）にリアルタイム出力します。

- PB-1000 の文字セット（charset.bin）と LCD VRAM を照合して文字コードを判定。
- 画面の行末に到達すると改行（CRLF）を出力。
- エミュレータメニューの **Serial Console** 項目でオン/オフ切り替え可能。

### RS-232C（PIO UART）

PIO ソフト UART（デフォルト GP6 TX / GP13 RX）で CASIO PB-1000 の RS-232C インターフェースをエミュレートします。

- MMIO アドレス 0x0C00–0x0C03（SIO レジスタ）に接続。
- ボーレートは `pb1000.ini` の `[pio_uart] baudrate` で設定（デフォルト 9600 bps）。
- エミュレータメニューの **RS-232C (PIO)** 項目でオン/オフ切り替え可能。
- EOF 受信（0x1A バイト）で自動 BREAK を発行する機能あり。

---

## 7. ジョイスティック

直結方式（PULL_UP 入力）のジョイスティックに対応しています。

| ボタン | デフォルトピン | デフォルト PB-1000 キー |
| :--- | :--- | :--- |
| UP | GP18 | カーソル上 |
| DOWN | GP19 | カーソル下 |
| LEFT | GP20 | カーソル左 |
| RIGHT | GP21 | カーソル右 |
| FIRE1 | GP26 | EXE |
| FIRE2 | GP27 | SHIFT |

- `pb1000.ini` の `[joystick]` セクションでキーマッピングを変更可能。
- エミュレータメニューの **Joystick** 項目でオン/オフ切り替え可能。
- ピンアサインの変更は `mp/main_input.py` の `JoystickInputManager.DEFAULT_PIN_MAP` を編集。

---

## 8. ステート管理

### Save State（F11）

**F11** を押すと、現在のセッション状態をプロファイルディレクトリに保存します。

- `ram0.bin`: 内部 RAM（8 KB）
- `ram1.bin`: 拡張 RAM1（有効時のみ）
- `ram2.bin` / `ram3.bin`: 拡張 RAM2 / RAM3（Bank2 / Bank3、有効時のみ）
- `regs.json`: CPU レジスタ（PC・フラグ・汎用レジスタ）

### RAM セーブ/ロード（エミュレータメニュー）

**RAM Save** / **RAM Load** でスナップショット単位の保存・復元ができます。

- 保存先は `/sd/rams/<フォルダ名>/`。
- RAM Load 成功後はリセット＋起動シーケンスが自動実行されます。

### 自動ロード

起動時に選択したプロファイルディレクトリ内のステートファイルを自動ロードします。
ファイルが存在しない場合はコールドブート。

---

## 9. スクリーンショット

**PrintScreen** キーで現在の LCD 内容をキャプチャします。

- 形式: PBM（Portable BitMap、1 bit 白黒）
- 保存先: `/sd/screenshots/screenshot_YYYYMMDD_HHMMSS.pbm`
  - SD カード非接続時は `/roms/` に保存
- 生 VRAM ダンプ（`vram_dump_....bin`）も同時保存

---

## 10. 設定ファイル（pb1000.ini）

INI 形式の設定ファイルで動作をカスタマイズできます。

```ini
[keyboard]
enable_uart_kbd = true
uart_baudrate   = 115200
uart_tx_pin     = 4
uart_rx_pin     = 5

[emulator]
frame_interval_ms  = 33     ; 表示更新間隔（ms）
active_step_count  = 12000  ; 1スライスあたりの CPU ステップ数

[disk]
enabled  = true
path     = /sd/disks/disk1.img

[profile]
default_profile = default
ui_timeout_ms   = 30000    ; プロファイル選択タイムアウト（ms）

[joystick]
enable = true

[beep]
enable   = true
gpio_pin = 14
freq_hz  = 4470
duty     = 30

[pio_uart]
baudrate = 9600

[display]
fg_color = 0               ; 前景色（点灯ピクセル）RGB332 形式 0–255
bg_color = 180             ; 背景色（消灯ピクセル）RGB332 形式 0–255
```

**RGB332 カラー形式（`[display]` セクション）**

`fg_color` / `bg_color` はカラー VRAM と同じ **RGB332（8 ビット）** 形式で指定します。
エミュレータメニューの **Foreground Color** / **Background Color** 項目から変更でき、
設定値は `pb1000.ini` に書き戻されます。

| ビット | 7–5 | 4–2 | 1–0 |
| --- | --- | --- | --- |
| 内容 | R (3 bit) | G (3 bit) | B (2 bit) |

代表的な値: `0` = 黒, `255` = 白, `180` = 0xB4 = やや青みがかった灰, `7` = 青

設定の詳細はファイル内のコメントを参照してください。
