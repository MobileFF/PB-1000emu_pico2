# ハードウェア・ガイド

このガイドでは、PB-1000 エミュレータを構築するために必要なハードウェアコンポーネントと配線について説明します。

## 部品表 (BOM)

- **マイクロコントローラ**: Raspberry Pi Pico 2 (RP2350) または Pico (RP2040)。パフォーマンスの面から Pico 2 を推奨します。
- **ディスプレイ**: ILI9341 320x240 または ST7796 480x320 の TFT LCD (SPI インターフェース)。`pb1000.ini` の `[display] driver` で切り替え可能。タッチパネル (XPT2046) 搭載モデルをサポートしています。
- **ストレージ**: Micro SD カードモジュール (SPI インターフェース)。
- **USB ホスト**: USB OTG (On-The-Go) アダプタ (Micro-USB から USB-A メス) キーボード接続用。
- **USB-シリアル変換アダプタ（必須）**: MicroPython REPL への接続用（GP0/GP1 に配線）。詳細は下記「UART およびシリアル」参照。
- **電源**: USB 電源 (5V)。
- **その他**: ブレッドボード、ジャンパーワイヤー、(任意) 100 オーム抵抗器 (バックライト PWM 用)。

> [!IMPORTANT]
> 本プロジェクトのファームウェアは Pico の USB ポートを **USB ホスト専用**（キーボード接続用、
> `CFG_TUD_ENABLED=0` / `MICROPY_HW_USB_CDC=0`）としてビルドしているため、Pico 本体の USB ポート経由で
> MicroPython REPL（`mpremote` 等）に接続することはできません。ファイル転送・設定変更・デバッグに
> 必要な REPL は **GP0/GP1 に配線した UART 経由のみ**アクセス可能です。詳細は下記「UART およびシリアル」の
> 「REPL (UART0)」の項を参照してください。

## ピンアサイン

コンソール UART と USB を除き、すべてのコンポーネントは同じ SPI バス (SPI1) を共有します。

### 1. LCD ディスプレイ (ILI9341 / ST7796 共通、SPI1)

ILI9341・ST7796 のどちらも同じピン役割で接続できます。使用するドライバは
`pb1000.ini` の `[display] driver`（`ILI9341` または `ST7796`）で選択します。

| LCD ピン | Pico ピン | 機能 | 備考 |
| :--- | :--- | :--- | :--- |
| VCC | 3.3V / 5V | 電源 | モジュールの仕様を確認してください |
| GND | GND | グランド | |
| CS | GP9 | チップセレクト | |
| DC / RS | GP8 | データ/コマンド | |
| RST | GP7 | リセット | |
| SDI (MOSI) | GP11 | SPI1 TX | |
| SCK | GP10 | SPI1 SCK | |
| SDO (MISO) | GP12 | SPI1 RX | |
| LED (BL) | GP22 | バックライト | 抵抗を介して 3.3V に接続可能 |

> **ドライバごとの違い**: ST7796 (480x320) は ILI9341 (320x240) より解像度が大きいため、
> `pb1000.ini` の `[display] scale` の推奨値が異なります（コメント付きでファイル内に記載）。
> また SPI ボーレートのデフォルトも ILI9341=26MHz、ST7796=40MHz とドライバ側で自動的に切り替わります。

### 2. SD カードモジュール (SPI1)

| SD ピン | Pico ピン | 機能 | 備考 |
| :--- | :--- | :--- | :--- |
| VCC | 3.3V / 5V | 電源 | |
| GND | GND | グランド | |
| CS | GP15 | チップセレクト | |
| MOSI | GP11 | SPI1 TX | 共有 |
| SCK | GP10 | SPI1 SCK | 共有 |
| MISO | GP12 | SPI1 RX | 共有 |

### 3. タッチパネル (XPT2046) (SPI1)

| タッチピン | Pico ピン | 機能 | 備考 |
| :--- | :--- | :--- | :--- |
| T_CS | GP16 | チップセレクト | |
| T_CLK | GP10 | SPI1 SCK | 共有 |
| T_DIN | GP11 | SPI1 TX | 共有 |
| T_DO | GP12 | SPI1 RX | 共有 |
| T_IRQ | GP17 | 割り込み | |

### 4. UART およびシリアル

| デバイス | Pico ピン | 機能 | 備考 |
| :--- | :--- | :--- | :--- |
| **REPL (UART0)** | **GP0 (TX), GP1 (RX)** | **MicroPython REPL / ファイル転送 (`mpremote`)** | **必須。** Pico の USB ポートはキーボードホスト専用のため、ここに USB-シリアル変換アダプタ（3.3V TTL）を接続しないと REPL に一切アクセスできません。PC 側では `mpremote connect /dev/ttyUSB0`（Windows は `COMx`）のように接続します |
| コンソール (UART1) | GP4 (TX), GP5 (RX) | デバッグ / REPL（サブ） | 任意。上記 REPL (UART0) とは別系統のデバッグ出力。`pb1000.ini` の `uart_tx_pin` / `uart_rx_pin` で変更可 |
| PIO UART | GP6 (TX), GP13 (RX) | 仮想 RS-232C | 任意。デフォルト 9600 bps（`pb1000.ini` の `[pio_uart] baudrate` で変更可） |

> **配線**: USB-シリアル変換アダプタの TXD を Pico の **GP1 (RX)** へ、RXD を **GP0 (TX)** へ、GND 同士を接続します（TX↔RX のクロス接続に注意）。

### 5. ビープ (PWM)

| 機能 | Pico ピン | 備考 |
| :--- | :--- | :--- |
| ビープ音出力 | **GP14** | PWM 出力。`pb1000.ini` の `[beep] gpio_pin` で変更可 |

ピンを 100 Ω 程度の抵抗を介して圧電ブザーまたは小型スピーカーに接続します。

### 6. ジョイスティック（任意）

直結方式（PULL_UP 入力、アクティブ LOW）で接続します。

| ボタン | デフォルト Pico ピン | `pb1000.ini` キー | デフォルトの PB-1000 キー |
| :--- | :--- | :--- | :--- |
| UP | GP18 | `key_up` | カーソル上 |
| DOWN | GP19 | `key_down` | カーソル下 |
| LEFT | GP20 | `key_left` | カーソル左 |
| RIGHT | GP21 | `key_right` | カーソル右 |
| FIRE1 | GP26 | `key_fire1` | EXE |
| FIRE2 | GP27 | `key_fire2` | SHIFT |

`pb1000.ini` の `[joystick]` セクションで各ボタンに割り当てる PB-1000 キーを変更できます。空文字の場合はデフォルトマップが使われます。  
ピンアサイン自体は `main_input.py` の `JoystickInputManager.DEFAULT_PIN_MAP` で定義されており、コード変更で変えられます。  
74HC148 プライオリティエンコーダを使った 3-bit 接続回路の詳細は `references/memo/joystick_3bit_encoding_circuit.md` を参照してください。

**ジョイスティック有効時の GPIO 空き状況**

ジョイスティック（GP18–21, GP26–27）をすべて使用した場合、外部デバイス用に自由に使える GPIO は **GP28** のみです（ADC2 として使用可能）。その他のピンは下表の機能を無効化した場合に解放できます。

| GPIO | 用途 | 解放条件 |
| :--- | :--- | :--- |
| GP0, GP1 | **REPL (UART0)** | **解放不可（常時必須）。** USB ポートがキーボードホスト専用のため、これが唯一の REPL 経路 |
| GP2, GP3 | I2C1（予約、PR6 未実装） | 現状 `mp/ext/` に I2C を使うモジュールは搭載されておらず未使用。将来 `sample/mp/ext/dht20.py` 等の I2C 拡張を `mp/ext/` に導入する場合に使用 |
| GP4, GP5 | UART1（コンソール KBD） | `pb1000.ini`: `enable_uart_kbd=false` |
| GP6, GP13 | PIO UART（RS-232C） | RS-232C 不使用時 |
| GP14 | BEEP PWM | `pb1000.ini`: `[beep] enable=false` |
| **GP28** | **空き（ADC2）** | **常時使用可能** |

### 7. 外部 SPI デバイス（GP28 CS 利用）

SPI1 バスはすでに LCD・SD・タッチパネルが CS ピンで共有しており、同じ方式で追加デバイスを接続できます。ジョイスティックを含む標準構成では **GP28 が唯一の空き GPIO** であるため、追加デバイスの CS ピンとして使用することを推奨します。

| 信号 | Pico ピン | 備考 |
| :--- | :--- | :--- |
| SCK | GP10 | SPI1 共有 |
| MOSI | GP11 | SPI1 共有 |
| MISO | GP12 | SPI1 共有 |
| CS（追加デバイス） | **GP28** | 専用 CS |

**利用方法**: `ext/` ディレクトリに拡張モジュールを置き、`register(system)` 内で `system.spi` を参照します。テンプレートは `sample/mp/ext/spi_sample.py` を参照してください（`mp/ext/` へコピーしてから使用）。

```python
# ext/my_device.py の例
import machine

MY_CS_PIN = 28
LCD_BAUD  = 40_000_000

def register(system):
    cs = machine.Pin(MY_CS_PIN, machine.Pin.OUT, value=1)
    system.register_call_hook(0x5E30, lambda: _callback(system, cs))

def _callback(system, cs):
    spi = system.spi
    spi.init(baudrate=1_000_000)   # デバイスのボーレートに変更
    cs.value(0)
    # ... 送受信処理 ...
    cs.value(1)
    spi.init(baudrate=LCD_BAUD)    # LCD 用に戻す
```

> **注意**: コールバック終了前に必ず `spi.init(baudrate=40_000_000)` で LCD 用ボーレートに戻してください。戻し忘れると LCD 描画が乱れます。

### 8. USB キーボード

- USB キーボードを **USB OTG アダプタ** を介して Pico の Micro-USB ポートに接続します。

## 配線上の注意点

- **SPI 共有**: LCD、SD、タッチパネルの CS (チップセレクト) ピンは独立している必要があります。バスの衝突を避けるために、起動時にすべての CS ピンを HIGH に設定してください。
- **電力消費**: LCD（ILI9341 / ST7796 いずれも）のバックライトはかなりの電流を消費します。Pico が再起動したり画面がちらついたりする場合は、外部 3.3V レギュレータを使用するか、モジュールが対応している場合は VBUS (5V) ピンから電力を供給してください。
- **ロジックレベル**: すべてのピンは 3.3V ロジックです。5V の信号を Pico のピンに直接接続しないでください。
- **基板の実装向き**: 筐体の都合等で LCD／タッチパネルを含む基板を上下逆に実装する場合は、配線を変更する代わりに `pb1000.ini` の `[display] rotation = 180` を設定してください。画面表示とタッチパネル座標の両方が自動的に反転されます（詳細は [Usage Guide](usage_guide.md) 参照）。

## 回路図

KiCad 形式の回路図ファイルが以下にあります:
- `hardware/pb1000_emulator.kicad_sch`

> [!TIP]
> 基板上のすべての電気的接続を確認するには、KiCad 7.0 以降でこのファイルを開いてください。
