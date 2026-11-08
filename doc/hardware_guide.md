# ハードウェア・ガイド

このガイドでは、PB-1000 エミュレータを構築するために必要なハードウェアコンポーネントと配線について説明します。

## 部品表 (BOM)

- **マイクロコントローラ**: Raspberry Pi Pico 2 (RP2350) または Pico (RP2040)。パフォーマンスの面から Pico 2 を推奨します。
- **ディスプレイ**: ILI9341 320x240 または ST7796 480x320 の TFT LCD (SPI インターフェース)。`pb1000.ini` の `[display] driver` で切り替え可能。タッチパネル (XPT2046) 搭載モデルをサポートしています。
- **ストレージ**: Micro SD カードモジュール (SPI インターフェース)。
- **USB キーボード接続方式（いずれか1つ）**:
  - **PIO-USB 方式（デフォルト）**: USB キーボードを GP4/GP5 に直結（配線は下記「8. USB キーボード」参照）。
    Pico 本体の USB-C ポートは空くため、CDC デバイスとして `mpremote` が直接使えます。
  - **ネイティブ USB ホスト方式（`USE_PIO_USB=OFF` ビルド時）**: USB OTG (On-The-Go) アダプタ
    (Micro-USB から USB-A メス) で Pico 本体の USB ポートにキーボードを接続。この場合 Pico 本体の
    USB ポートはキーボード専用になるため、REPL には別途 USB-シリアル変換アダプタが必要。
- **USB-シリアル変換アダプタ（ネイティブ USB ホスト方式を使う場合は必須）**: MicroPython REPL への接続用
  （GP0/GP1 に配線）。詳細は下記「UART およびシリアル」参照。PIO-USB 方式（デフォルト）では USB-C ポートの
  CDC で代替できるため不要。
- **電源**: USB 電源 (5V)。
- **その他**: ブレッドボード、ジャンパーワイヤー、(任意) 100 オーム抵抗器 (バックライト PWM 用)。
  PIO-USB 方式では USB コネクタと 22Ω 抵抗 2 本も必要（下記「8. USB キーボード」参照）。

> [!IMPORTANT]
> ファームウェアのデフォルトビルド（`USE_PIO_USB=ON`）では、USB キーボードホストを GP4/GP5 の
> PIO-USB に移し、Pico 本体の USB-C ポートは CDC デバイスとして動作します。`mpremote connect
> /dev/ttyACM0`（Windows は `COMx`）のように USB-C ケーブル1本で REPL・ファイル転送に接続できます。
> `USE_PIO_USB=OFF` でビルドした場合は、Pico の USB ポートが **USB ホスト専用**になり、本体の
> USB ポート経由で MicroPython REPL に接続することはできません。その場合は **GP0/GP1 に配線した
> UART 経由のみ**アクセス可能です。詳細は下記「UART およびシリアル」の「REPL (UART0)」の項を参照してください。

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
| **REPL (UART0)** | **GP0 (TX), GP1 (RX)** | **MicroPython REPL / ファイル転送 (`mpremote`)** | デフォルト（`USE_PIO_USB=ON`）ビルドでは USB-C ポートの CDC で代替できるため配線不要。`USE_PIO_USB=OFF` ビルドでは **必須**（Pico の USB ポートがキーボードホスト専用になるため）。接続する場合は USB-シリアル変換アダプタ（3.3V TTL）を使い、PC 側では `mpremote connect /dev/ttyUSB0`（Windows は `COMx`）のように接続します |
| **USB キーボードホスト (PIO-USB)** | **GP4 (D+), GP5 (D-)** | **USB キーボード接続** | デフォルト（`USE_PIO_USB=ON`）ビルドで使用。配線は下記「8. USB キーボード」参照 |
| PIO UART | GP6 (TX), GP13 (RX)（デフォルト） | 仮想 RS-232C | 任意。ピン番号・ボーレートともに `pb1000.ini` の `[rs232c] tx_pin`/`rx_pin`/`baudrate`（デフォルト 9600 bps）で変更可。他機能と重複しない空きピンであれば変更可能 |

> **配線（ネイティブ USB ホスト方式で REPL に UART を使う場合）**: USB-シリアル変換アダプタの TXD を Pico の **GP1 (RX)** へ、RXD を **GP0 (TX)** へ、GND 同士を接続します（TX↔RX のクロス接続に注意）。

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
ピンアサイン自体は `main_input_joystick.py` の `JoystickInputManager.DEFAULT_PIN_MAP` で定義されており、コード変更で変えられます。  
74HC148 プライオリティエンコーダを使った 3-bit 接続回路の詳細は `references/memo/joystick_3bit_encoding_circuit.md` を参照してください。

**ジョイスティック有効時の GPIO 空き状況**

ジョイスティック（GP18–21, GP26–27）をすべて使用した場合、外部デバイス用に自由に使える GPIO は
デフォルト（`USE_PIO_USB=ON`）ビルドでは **GP28** のみ、`USE_PIO_USB=OFF` ビルドでは **GP4, GP5,
GP28** です（GP28 は ADC2 としても使用可能）。その他のピンは下表の機能を無効化した場合に解放できます。

| GPIO | 用途 | 解放条件 |
| :--- | :--- | :--- |
| GP0, GP1 | **REPL (UART0)** | デフォルトビルドでは USB-C ポートの CDC で代替可能なため解放可。`USE_PIO_USB=OFF` ビルドでは**解放不可（常時必須）** |
| GP2, GP3 | I2C1（予約、PR6 未実装） | 現状 `mp/ext/` に I2C を使うモジュールは搭載されておらず未使用。将来 `sample/mp/ext/dht20.py` 等の I2C 拡張を `mp/ext/` に導入する場合に使用 |
| GP4, GP5 | **USB キーボードホスト (PIO-USB)**。デフォルト（`USE_PIO_USB=ON`）ビルドで使用 | `USE_PIO_USB=OFF` ビルド（ネイティブ USB ホスト方式）の場合のみ常時使用可能 |
| GP6, GP13 | PIO UART（RS-232C、デフォルト） | RS-232C 不使用時、または `pb1000.ini` の `[rs232c] tx_pin`/`rx_pin` で他のピンへ変更した場合 |
| GP14 | BEEP PWM | `pb1000.ini`: `[beep] enable=false` |
| **GP28** | **空き（ADC2）** | **常時使用可能** |

### 7. 外部 SPI デバイス（GP28 CS 利用）

SPI1 バスはすでに LCD・SD・タッチパネルが CS ピンで共有しており、同じ方式で追加デバイスを接続できます。ジョイスティックを含む標準構成では **GP28** を追加デバイスの CS ピンとして使用することを推奨します（`USE_PIO_USB=OFF` ビルドでは GP4/GP5 も空きますが、GP28 は ADC2 としても使えるため、より汎用的な既定選択です）。

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

接続方式はビルド時の `USE_PIO_USB` オプションで決まります（ビルド手順は
[build_guide.md](build_guide.md) 参照）。

**PIO-USB 方式（デフォルト、`USE_PIO_USB=ON`）**

USB キーボードを GP4/GP5 に直結します。USB OTG アダプタは不要ですが、USB-A コネクタ（または
USB ケーブルを切った先）と抵抗が必要です。

| 信号 | Pico ピン | 備考 |
| :--- | :--- | :--- |
| D+ | GP4 | 22Ω 直列抵抗を介して接続 |
| D- | GP5 | 22Ω 直列抵抗を介して接続 |
| VBUS | 5V (VBUS) | キーボード側の電源 |
| GND | GND | |

- **外付けのプルアップ・プルダウン抵抗は不要**（RP2350 内蔵のプルダウンのみで動作します）。
  外付けの 15kΩ プルダウン等を追加すると、SETUP（制御転送）は成功するのに長いデータ転送が
  CRC エラーになる、といった不安定な列挙失敗の原因になることが確認されています。
- ESD 保護ダイオード（D+/D- から GND へのバイダイレクショナル ESD ダイオード、例:
  ESD5302F）は任意ですが、実機接続では推奨します。
- **配線品質が重要**: ブレッドボード＋ジャンパーワイヤーでの仮組みでは、SETUP は成功するのに
  後続のデータ転送で CRC エラーが出て列挙が失敗することが確認されています。ユニバーサル基板への
  はんだ付け等、しっかりした配線にしてください。
- **既知の制限**: 一部の USB キーボード（ワイヤレスキーボードの受信機を含む）は、GP4/GP5 に
  直接つなぐと認識しません。その場合は **USB ハブを間に挟む**と認識することがあります
  （ハブ 1 台の先に、ハブに内蔵された機器も含めて 4 台まで扱えます）。ハブを挟んでも認識しない
  場合は、別のキーボードをお試しください。原因の切り分けには `mp/test/usb_kbd_diag.py` が使えます。

**ネイティブ USB ホスト方式（`USE_PIO_USB=OFF` ビルド時）**

- USB キーボードを **USB OTG アダプタ** (Micro-USB から USB-A メス) を介して Pico の USB ポートに
  接続します。この場合 Pico 本体の USB ポートはキーボード専用になるため、REPL には GP0/GP1 の
  UART 配線が必須です（上記「4. UART およびシリアル」参照）。


### 9. HDMI ミラー出力（オプション、第2 Pico 2 使用）

本体の LCD 表示内容を、第2の Raspberry Pi Pico 2 (RP2350) ＋ HDMI 出力アドオン
（PICO-HDMI-PLUS 等）を使ってHDMIモニターへミラー表示できます。§7 の「外部 SPI デバイス」の
仕組みをそのまま使い、SPI1 バスに新設の CS ピン（GP28）を追加するだけで接続できます。
アドオンを持たない場合、`pb1000.ini` の `[hdmi] enable=false`（デフォルト）のままにしておけば
一切影響しません。

> [!NOTE]
> 受信側（第2 Pico 2）は本プロジェクトに含まれない独立プロジェクトです。ファームウェアの
> 入手方法・ビルド方法・受信側自体の配線（HSTX 出力 → HDMI アドオン）は
> [`hdmi_bridge_receiver`](https://github.com/MobileFF/hdmi_bridge_receiver) を参照してください。
> **受信側は Pico 2 (RP2350) が必須です**（HSTX ペリフェラルが RP2350 専用のため、無印 Pico /
> Pico W では動作しません）。

**配線（本体側 Pico 2 W → 受信側 Pico 2）**

§7 の表と同じ SPI1 バスを共有し、CS のみ新設の GP28 を使います。

| 信号 | 本体側 Pico 2 W | 受信側 Pico 2 | 備考 |
| :--- | :--- | :--- | :--- |
| SCK | GP10 | GP2 | SPI1 共有 |
| MOSI | GP11 | GP0 | SPI1 共有（受信側からは SPI0 RX として見える） |
| CS | **GP28**（新設） | GP1 | 受信側専用 CS。LCD/SD/タッチと衝突しない専用ピンを新設 |
| GND | GND | GND | 共通グランド |

受信側の HSTX 出力（GP12–19）→ HDMI アドオン間の配線は受信側プロジェクトのドキュメントを
参照してください。

**設定**: `pb1000.ini` の `[hdmi]` セクション（詳細は [config_guide.md](config_guide.md) §14）、
または起動時の F1 セットアップメニューから ON/OFF を切り替えます（保存後 MCU リブートで
反映）。**LCD と HDMI は排他出力**で、HDMI 有効時は物理 LCD への描画が止まり、画面はすべて
HDMI 側に表示されます（[usage_guide.md](usage_guide.md) §11 参照）。

### 10. Waveshare RP2350-PiZero（オンボードDVI出力、実験的）

**実機は Pico 2 / Pico 2 W とは別の専用ボード**（RP2350B、48GPIO、SPI LCDなし、
オンボードDVI/microSD）で、物理LCD・タッチパネルを一切使わず、**HDMI/DVI出力のみ**で
動作させることを目的としたポートです。姉妹プロジェクト（MSX_emu_pico2）の同一ボード向け
ポートで実機確認済みのボード定義・DVI出力ライブラリ（`src/libdvi/`、BSD-3-Clause、
[THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md)参照）を移植しています。

**動作状況**: `pb1000.ini`（このボード用の雛形は `mp/pb1000_pizero.ini`）で
`[display] driver=DVI` を設定して通常起動（`main.py`）すると、次の動作を実機で確認しています。

- エミュレータ画面（PB-1000の液晶）、ベゼル、FunKeyBar、ステータス表示、常時時計・
  メモリ表示（`[overlay]`）のDVI表示
- 起動時のプロファイル選択画面、Win+F7エミュレータメニューのDVI表示
- USBキーボード（ボードのUSB-PIOコネクタ、PIO-USB）
- オンボードmicroSD（`/sd`）の読み書き
- RAMプロファイル保存などのフラッシュ書き込み中も、DVI表示は止まらずに継続

RS-232C（PIO UART）は初期化まで確認済みで、実際の送受信は未確認です。

**画面の仕様**:

- 出力は 640×480 / 60Hz 固定です。内部では 320×240 の画面を、縦横2倍に拡大して出力します
  （1ドットがモニタ上の2×2画素になります）。
- 液晶部分は、エミュレータの液晶VRAMを走査線ごとに直接読み取って描画します。メニュー・
  プロファイル選択画面・FunKeyBar等は、液晶の上に重ねる320×240の別の画面（同時に16色まで）に
  描画します。
- `[display] scale` は、320ドット幅に収まる範囲で指定してください。`scale = 1.0` のとき、
  液晶の1ドットがちょうどモニタ上の2×2画素になります。収まらない値（例: `2.0` = 384ドット幅）を
  指定した場合は、起動時に収まる最大の倍率（320/192 ≒ 1.67）へ自動的に縮小します。整数倍で
  ないため、ドットの幅がわずかに不揃いになります。

**PIOの割り当て**: pio0 = DVI出力、pio1 = PIO-USB（USBキーボード）、pio2 = RS-232C
（PIO UART、ステートマシン8/9、TX=GP6・RX=GP13）。Pico 2 / Pico 2 W とは割り当てが異なります。

**microSD**: オンボードスロットはSPI1（SCK=GP30、MOSI=GP31、MISO=GP40、CS=GP43）に
配線されており、Pico 2 / Pico 2 W のSDカードモジュールとはピンが異なります。DVI設定時は
自動的にこちらを使います。

**ビルド**: `bash bldfrm.sh pizero`（`/home/flex/projects/micropython.pb1000/ports/rp2/`。
通常の`bash bldfrm.sh`とは別の明示的な引数が必要）。出力は
`firmware/firmware_pb1000_pizero.uf2`。ビルド手順には、このボード特有のパッチが含まれます。

- `memmap_mp_rp2350.ld`: core0のCスタック（16KB）を通常のSRAMへ移し、SCRATCH_X/Yを
  core1（DVI）専用にする
- `CMakeLists.txt`: スタックサイズの調整
- `MICROPY_C_HEAP_SIZE` の明示指定（MicroPythonのrp2ポートは標準でCヒープが0バイトで、
  このボードのDVI初期化が内部で使う`calloc()`呼び出しが不正なポインタを返す原因になるため必須）

これらはPico 2/2W向けビルドとは自動的に切り替え・復元されます。

**接続**: 書き込み後、`mpremote connect <port>`で接続できます
（ネイティブUSB-CポートのCDC REPL、またはUART0 (GP0/GP1) いずれでも接続可）。このボードの
USB-Cポートは本体キーボード入力用のネイティブUSBホストとして使えない配線のため、
キーボードはUSB-PIOコネクタ（PIO-USB、GP28/29）に接続します（詳細は
`src/usb_host_core.c`の`PB1000_PIO_USB_DP_PIN`コメント参照）。

**40ピンヘッダからUSB-Aコネクタへ配線する場合（GP22版）**: GP28/29は40ピンヘッダに出て
いないため、ヘッダにUSB-Aコネクタを外付けする場合は、PIO-USBをGP22/GP23に割り当てた
`firmware/firmware_pb1000_pizero_gp22.uf2`（`bash bldfrm.sh pizero gp22`）を使います。
このファームウェアではボードのUSB-PIOコネクタは使えません。それ以外の機能は通常版と同じです。

| USB-A | GPIO | ヘッダのピン番号 |
|---|---|---|
| D+ | GP22（22Ωを直列に挿入） | 15 |
| D- | GP23（22Ωを直列に挿入） | 16 |
| VBUS | 5V | 2 または 4 |
| GND | GND | 14 など |

配線の確認には `mp/test/pio_usb_gp22_test.py` を使います（`mpremote run mp/test/pio_usb_gp22_test.py`）。
D+/D-の電圧からキーボードの接続を、GPIOの機能割り当てから書き込まれているファームウェアが
GP22版かどうかを確認し、押したキーの名前を表示します。

**拡張モジュール**: `mp/ext/` は他のボードと同じくファームウェアにフリーズされないため、
使う場合は内蔵フラッシュの `/ext/` または `/sd/ext/` へコピーしてください。64ドットモードで
全8行を表示する `dotds_64dot.py` もここに含まれます。

**DVI信号そのものの確認（トラブルシュート用）**: 通常起動（`main.py`）ではなく
`mp/test/test_dvi_display.py`を直接実行します:

```
mpremote connect <port> run mp/test/test_dvi_display.py
```

HDMI/DVIモニタに8色の縦バー（白/黄/シアン/緑/マゼンタ/赤/青/黒）と画面中央の横白線が
表示されれば、DVI信号経路（クロック・PIO・DMA・core1起動）は正常です。

**既知の制約**:

- WiFiを搭載していないため、NTPによる時刻同期は行いません。PB-1000本体の時計は、
  RAMプロファイルに保存された時刻から進みます。
- メニュー等の重ね表示は同時に16色までです。それを超える色は、近い色で表示します。

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
