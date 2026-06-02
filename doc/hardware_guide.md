# ハードウェア・ガイド

このガイドでは、PB-1000 エミュレータを構築するために必要なハードウェアコンポーネントと配線について説明します。

## 部品表 (BOM)

- **マイクロコントローラ**: Raspberry Pi Pico 2 (RP2350) または Pico (RP2040)。パフォーマンスの面から Pico 2 を推奨します。
- **ディスプレイ**: ILI9341 320x240 TFT LCD (SPI インターフェース)。タッチパネル (XPT2046) 搭載モデルをサポートしています。
- **ストレージ**: Micro SD カードモジュール (SPI インターフェース)。
- **USB ホスト**: USB OTG (On-The-Go) アダプタ (Micro-USB から USB-A メス) キーボード接続用。
- **電源**: USB 電源 (5V)。
- **その他**: ブレッドボード、ジャンパーワイヤー、(任意) 100 オーム抵抗器 (バックライト PWM 用)。

## ピンアサイン

コンソール UART と USB を除き、すべてのコンポーネントは同じ SPI バス (SPI1) を共有します。

### 1. ILI9341 ディスプレイ (SPI1)

| ILI9341 ピン | Pico ピン | 機能 | 備考 |
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
| コンソール (UART1) | GP4 (TX), GP5 (RX) | デバッグ / REPL | 任意。`pb1000.ini` の `uart_tx_pin` / `uart_rx_pin` で変更可 |
| PIO UART | GP6 (TX), GP13 (RX) | 仮想 RS-232C | 任意。デフォルト 9600 bps（`pb1000.ini` の `[pio_uart] baudrate` で変更可） |

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
74HC148 プライオリティエンコーダを使った 3-bit 接続回路の詳細は `references/joystick_3bit_encoding_circuit.md` を参照してください。

**ジョイスティック有効時の GPIO 空き状況**

ジョイスティック（GP18–21, GP26–27）をすべて使用した場合、外部デバイス用に自由に使える GPIO は **GP28** のみです（ADC2 として使用可能）。その他のピンは下表の機能を無効化した場合に解放できます。

| GPIO | 用途 | 解放条件 |
| :--- | :--- | :--- |
| GP2, GP3 | I2C1（dht20 ext モジュール） | `ext/dht20.py` を削除 |
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

**利用方法**: `ext/` ディレクトリに拡張モジュールを置き、`register(system)` 内で `system.spi` を参照します。テンプレートは `mp/ext/spi_sample.py` を参照してください。

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
- **電力消費**: ILI9341 のバックライトはかなりの電流を消費します。Pico が再起動したり画面がちらついたりする場合は、外部 3.3V レギュレータを使用するか、モジュールが対応している場合は VBUS (5V) ピンから電力を供給してください。
- **ロジックレベル**: すべてのピンは 3.3V ロジックです。5V の信号を Pico のピンに直接接続しないでください。

## 回路図

KiCad 形式の回路図ファイルが以下にあります:
- `hardware/pb1000_emulator.kicad_sch`

> [!TIP]
> 基板上のすべての電気的接続を確認するには、KiCad 7.0 以降でこのファイルを開いてください。
