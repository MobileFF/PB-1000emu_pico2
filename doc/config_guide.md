# 設定ファイルガイド（pb1000.ini）

このガイドでは、`pb1000.ini` の全セクション・全キーをリファレンス形式で説明します。
セットアップの概要や個別機能の説明は `usage_guide.md` を参照してください。

---

## 1. 読み込み優先順位とマージ規則

以下の順（低→高）でファイルを読み込み、**セクション単位ではなくキー単位**でマージします。
記述したキーのみが上書きされ、省略したキーは下位の値（最終的には内蔵デフォルト）が使われます。

```
内蔵デフォルト（mp/config.py の _DEFAULTS）
  < /pb1000.ini            （Pico フラッシュのルート）
  < /sd/pb1000.ini          （SD カード）
  < <プロファイル>/pb1000.ini（/sd/rams/<name>/pb1000.ini）
```

実装: `mp/config.py` の `load_config()`。コメントは `;` または `#` で開始します。

### 例外: 起動時にのみ・内蔵フラッシュからのみ読まれるキー

一部のキーはディスプレイ／タッチパネルのハードウェア初期化に使われるため、
**SD カードがマウントされる前**に読み込まれます。そのため上記の優先順位には従わず、
`/pb1000.ini` と `/roms/pb1000.ini`（内蔵フラッシュ側）のみが有効です。
`/sd/pb1000.ini` やプロファイル別 ini に書いても反映されません。

- `[display]` の `driver` / `spi_baudrate` / `rotation`
- `[touch]` の `swap_xy` / `x_inv` / `y_inv`（ドライバ別プレフィックス付きキーも含む）

これら以外の `[display]` キー（`scale` / `lcd_height` / `x_offset` / `y_offset` /
`fg_color` / `bg_color`）や `[touch]` の座標オフセット系キー（`x_offset` /
`y_offset` / `funckey_x_offset` / `funckey_y_offset`）は通常どおり SD カード・
プロファイル別 ini でも上書きできます。

実装: `mp/pb1000.py` の `_read_early_ini_sections()` / `init_display()`。

### 例外2: セクション丸ごとフラッシュ限定(`[hdmi]`)

`[hdmi]` セクションは**丸ごと**、内蔵フラッシュの `/pb1000.ini` でのみ有効です。
`/sd/pb1000.ini` やプロファイル別 ini に `[hdmi]` を書いても、`load_config()`が
マージの時点で無視します(§1冒頭の優先順位マージには**従いません**)。固定のハードウェア
配線に関する設定であり、SDカードやプロファイルごとに変わる性質のものではないための
制限です。EMULATOR MENU の HDMI トグルも常に `/pb1000.ini` へ書き戻します
(`mp/emulator_menu.py` の `_save_hdmi_enable()`)。

上記の「例外1」(`[display]`/`[touch]` の一部キー)とは実装が異なります。あちらは
SDカードがマウントされる**前**に読む(`_read_early_ini_sections()`)ことで自然に
SD/プロファイルiniの影響を受けない仕組みですが、`[hdmi]` の値はSDマウント後
(プロファイル選択前後)に必要になるため、そのタイミングでは既にSDが読める状態です。
そのため代わりに `mp/config.py` の `load_config()` 内で `[hdmi]` セクションを
明示的にスキップする(`_FLASH_ONLY_SECTIONS`)方式を取っています。

実装: `mp/config.py` の `load_config()` / `_FLASH_ONLY_SECTIONS`。

---

## 2. `[display]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `driver` | `ILI9341` | `ILI9341`（320×240）または `ST7796`（480×320、MSP4021等）。`display =` も同じ意味のエイリアスとして受け付ける。※内蔵フラッシュ限定キー |
| `spi_baudrate` | ILI9341: `26000000` / ST7796: `40000000` | LCD SPI のボーレート（Hz）。※内蔵フラッシュ限定キー |
| `scale` | ILI9341: `1.5` / ST7796: `2.0` | エミュレータ画面の拡大倍率（物理ディスプレイ幅が480px以上ならST7796扱いのデフォルトになる） |
| `lcd_height` | `32` | `32`＝PB-1000オリジナル、`64`＝拡張モード（rowアドレスBit\<2\>を有効化し pages 4-7／行32-63に書き込み可能にする）。32/64以外を指定した場合は32にフォールバック |
| `x_offset` | 自動（水平センタリング） | LCD左端のX座標（ピクセル）。省略時は `(表示幅 - 192*scale) / 2` で自動計算 |
| `y_offset` | 自動（垂直センタリング） | LCD＋シートキーバー全体の上端Y座標（ピクセル）。省略時はグループ全体を垂直センタリング。値を小さくするほど上寄りになる |
| `rotation` | `0` | `0`＝通常、`180`＝上下反転（基板の実装向きに合わせる）。タッチパネル座標も自動的に反転される。0/180以外は0にフォールバック。※内蔵フラッシュ限定キー |
| `fg_color` | `0` | 前景色（点灯ピクセル）。RGB332形式 0–255。エミュレータメニューの Foreground Color から変更すると `/sd/pb1000.ini`（無ければ `/pb1000.ini`）に自動で書き戻される |
| `bg_color` | `180` | 背景色（消灯ピクセル）。RGB332形式 0–255。書き戻し挙動は `fg_color` と同じ |

RGB332形式（8ビット）: ビット7-5=R(3bit)、ビット4-2=G(3bit)、ビット1-0=B(2bit)。
代表値: `0`=黒、`255`=白、`180`(0xB4)=やや青みがかった灰、`7`=青。

実装: `mp/pb1000.py` の `init_display()`（driver/spi_baudrate/rotation）、
`mp/main_boot.py` の `create_system()`（scale/lcd_height/x_offset/y_offset/fg_color/bg_color）。

---

## 3. `[keyboard]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable_usb_kbd` | `true` | USBキーボードを有効にする |
| `enable_uart_kbd` | `false` | UARTキーボード（GP4/GP5等）を有効にする |
| `uart_baudrate` | `9600` | UARTキーボードのボーレート |
| `uart_tx_pin` | `4` | UARTキーボードのTXピン（GPIO番号） |
| `uart_rx_pin` | `5` | UARTキーボードのRXピン（GPIO番号） |
| `uart_enter_always_exe` | `true` | UARTキーボードのEnterキーを常にEXEキーとして扱う |
| `key_pulse_interval_ms` | `25` | KEY_INTパルス間隔（ms）。実機のKey/Pulse ISRは3.9ms(256Hz)周期。値を小さくするほどキー確定までの体感時間が短くなる。カーソルキーリピート等、他の時間ベース調整（`dev_guide.md` §13）もこの間隔を前提に実測チューニングされているため、変更後は通常のタイピング・カーソルリピート動作も要確認。REPLからも変更可: `hd61700.set_key_pulse_interval_ms(ms)` |
| `key_hold_ms` | `120` | キー押下継続時間（ms） |
| `key_release_hard_timeout_ms` | `1200` | キーリリース強制タイムアウト（ms） |
| `inter_key_gap_ms` | `80` | キー間のギャップ（ms） |

実装: `mp/main.py`（起動処理）、`mp/config.py` の `_DEFAULTS["keyboard"]`。

---

## 4. `[emulator]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable_repl_uart` | `true` | `false`にすると `boot.py` がGP0/GP1接続のUART0 REPLを停止する（USB CDC REPLには影響しない） |
| `frame_interval_ms` | `33` | 表示更新間隔（ms）。33ms≒30fps |
| `active_step_count` | `12000` | 1スライスあたりのCPUステップ数 |
| `sleep_poll_ms` | `10` | スリープ中のポーリング間隔（ms） |
| `step_timer_tick_steps` | `40000` | タイマーティック換算ステップ数 |
| `timer_tick_ms` | `1000` | リアルタイムタイマーティックの間隔（ms）。`0`以下でこのティック処理自体を無効化 |
| `loop_idle_ms` | `0` | メインループのアイドル待機（ms） |
| `step_chunk` | `2048` | CPU実行スライスの内部チャンクサイズ（ステップ数）。UARTキーボード受信バッファのドレインやPIO UARTブリッジのサービス頻度にも使われる |

実装: `mp/main.py`（メインループ定数の読み込み・使用箇所）、`mp/main_runtime.py`（`run_cpu_slice`）。

---

## 5. `[disk]`（仮想FDD）

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enabled` | `false` | 仮想FDDを有効にする |
| `backend` | `raw` | ストレージバックエンド |
| `path` | （空） | ディスクイメージファイルのパス（例: `/sd/disks/disk1.img`） |
| `readonly` | `false` | 読み取り専用にする |

実装: `mp/pb1000.py`（`PB1000System` の仮想FDD初期化、`self._config["disk"]` を直接参照）。

---

## 6. `[profile]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `default_profile` | `default` | 起動時のデフォルトプロファイル名（`/sd/rams/<name>/`） |
| `ui_timeout_ms` | `30000` | プロファイル選択UIのタイムアウト（ms）。プロファイルが1つしか無い場合はUI自体がスキップされる |

実装: `mp/main.py`、`mp/boot_session.py`（`select_profile_ui`）。

---

## 7. `[joystick]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable` | `false` | Atari準拠9ピンジョイスティック（PULL_UP入力）を有効にする |
| `enable_fire2` | `true` | FIRE2ボタンを有効にする |
| `debounce_ms` | `20` | デバウンス時間（ms） |
| `poll_interval_ms` | `10` | ポーリング間隔（ms） |
| `key_up` / `key_down` / `key_left` / `key_right` / `key_fire1` / `key_fire2` | （空＝内蔵デフォルト） | 各ボタンが送出するPB-1000キー。名前指定（`exe`, `ans`, `shift`, `up`, `down`, `left`, `right`, `bs`, `ins`, `brk`, `newall`, `menu`, `cal`, `cls`, `kana`, `a`-`z`, `0`-`9`）または座標直接指定（`row,col` 例: `10,4`）。省略時のデフォルトはUP=カーソル上, DOWN=カーソル下, LEFT=カーソル左, RIGHT=カーソル右, FIRE1=EXE, FIRE2=SHIFT |

ピンアサイン（GP18/19/20/21/26/27）はiniでは変更できません。変更する場合は
`mp/main_input.py` の `JoystickInputManager.DEFAULT_PIN_MAP` を編集してください。

実装: `mp/main.py`（`_parse_joystick_key`）、`mp/main_input.py`（`JoystickInputManager`）。

---

## 8. `[beep]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable` | `true` | ビープ音を有効にする |
| `gpio_pin` | `14` | ビープ出力ピン（GPIO番号） |
| `freq_hz` | `1000` | ビープ周波数（Hz） |
| `duty` | `50` | PWMデューティ比（%） |

実装: `mp/pb1000.py`（`_beep_set` 等）、`mp/emulator_menu.py`（メニューからのfreq/duty変更）。

---

## 9. `[touch]`（XPT2046タッチパネル）

タッチフィルムの取り付け向きがLCDモジュールごとに異なるため、軸の入れ替え・反転や
座標オフセットの調整が必要になる場合があります。ILI9341用・ST7796用の設定を
**同じ `[touch]` セクションに共存**させられます。キーの先頭に `ili9341.` または
`st7796.` を付けると、`[display] driver` で選択されているドライバのときだけ適用されます。
プレフィックス無しのキーはどちらのドライバでも（ドライバ別の値で上書きされなければ）適用されます。

| キー（`ili9341.` / `st7796.` プレフィックス対応） | ILI9341既定値 | ST7796既定値 | 説明 | SD/プロファイルiniでの上書き |
| --- | --- | --- | --- | --- |
| `swap_xy` | `true` | `true` | タッチ座標のX/Y軸を入れ替える | 不可（内蔵フラッシュ限定） |
| `x_inv` | `false` | `true` | X軸を反転する | 不可（内蔵フラッシュ限定） |
| `y_inv` | `false` | `true` | Y軸を反転する | 不可（内蔵フラッシュ限定） |
| `x_offset` | `0` | `8` | LCDタッチエリア（TK1..16）用のX座標補正オフセット（ピクセル） | 可 |
| `y_offset` | `-10` | `0` | LCDタッチエリア用のY座標補正オフセット（ピクセル） | 可 |
| `funckey_x_offset` | `0` | `2` | シートキーバー（FuncKeyBar）用のX座標補正オフセット | 可 |
| `funckey_y_offset` | `24` | `-8` | シートキーバー用のY座標補正オフセット | 可 |

設定例（ST7796のY方向オフセットだけを上書きする場合）:

```ini
[touch]
st7796.y_offset = -4
```

**注意点:**

- `swap_xy`/`x_inv`/`y_inv` は起動時（SDカードマウント前）に内蔵フラッシュの
  `/pb1000.ini`（および `/roms/pb1000.ini`）からのみ読み込まれます。§1参照。
- `y_inv`/`x_inv` を変更すると座標の意味が反転するため、`x_offset`/`y_offset` 系の
  補正値も再調整が必要になることがあります（符号反転を起点に実機で微調整）。
- LCDタッチエリア（TK1..16）の判定範囲は、`[display] lcd_height` の設定に関わらず
  **常に32ドット×`scale`固定**です。実機の物理タッチパッドが常に32ドット分の高さしか
  無いための仕様で、64ドット拡張モードにしても判定エリアは広がりません。

実装: `mp/pb1000.py`（`init_display()`、`_read_early_ini_sections`、`_early_bool`）、
`mp/main_boot.py`（`_setup_touch_offsets()`）、`mp/main_input.py`
（`TouchInputManager.poll_coords()`）。

---

## 10. `[pio_uart]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `baudrate` | `9600` | PIO UART（RS-232C、GP6=TX / GP13=RX）のボーレート |

実装: `mp/main.py`、`mp/pio_uart.py`。

---

## 11. `[wifi]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `ssid` | （空） | WiFiのSSID。空欄の場合はNTP同期をスキップする |
| `password` | （空） | WiFiのパスワード |

実装: `mp/main.py`（`[ntp] enable=true` の場合のみ参照）、`mp/ntp_sync.py`。

---

## 12. `[ntp]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable` | `true` | 起動時のNTP同期を有効にする（WiFi接続が必要） |
| `server` | `pool.ntp.org` | NTPサーバのアドレス |
| `tz_offset_h` | `9` | タイムゾーンの時差（JSTは9） |
| `timeout_ms` | `15000` | 接続タイムアウト時間（ミリ秒） |

実装: `mp/main.py`、`mp/ntp_sync.py`。

---

## 13. `[debug]`

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `cpu_debug` | `false` | CPU命令トレース（特定PCブレークポイントでのみ出力） |
| `key_debug` | `false` | キー入力トレース（KEYSCAN GRE等）。ROMのキースキャンループを常時追うため出力が非常に多い |
| `lcd_debug` | `false` | LCD書き込みトレース |
| `newall_debug` | `false` | NEW ALLキー（Win+F12）の押下/解放のみをトレース |

いずれかを`true`にすると `[HD61700] ...` 形式のトレース行がシリアルコンソールに出力されます。
詳細は `dev_guide.md` §11「デバッグとトレース」を参照してください。

実装: `mp/main.py`。

---

## 14. `[hdmi]`

第2の Raspberry Pi Pico 2 ＋ HDMI出力アドオンを使った、オプションのHDMIミラー出力機能の設定です。
アドオンを持たない場合は `enable = false`（デフォルト）のままで一切影響しません。
配線・受信側ファームウェアの詳細は [hardware_guide.md](hardware_guide.md) §9 を参照してください。

> [!IMPORTANT]
> **このセクションは丸ごと内蔵フラッシュ限定です**（§1「例外2」参照）。`/sd/pb1000.ini` や
> プロファイル別 ini に `[hdmi]` を書いても無視されます。EMULATOR MENU からの保存も常に
> `/pb1000.ini` へ書き戻されます。

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `enable` | `false` | HDMIミラー出力を有効にする。EMULATOR MENU（Display > HDMI）からも切り替え・保存可能 |
| `cs_pin` | `28` | 受信側との通信に使う追加SPI1 CSピン（GPIO番号）。GP28固定を推奨（唯一の空きGPIO、§7参照） |
| `baudrate` | `10000000` | 受信側とのSPI通信速度（Hz）。配線がしっかりしていれば上げられる |
| `frame_skip` | `1` | 何フレームに1回HDMI側へ送信するか。`1`＝毎フレーム。PB-1000の転送量は元々小さいため通常は`1`のままで問題ない |

**LCDとHDMIは排他出力**です（同時に両方へは描画されません）。`enable=true`のとき、物理LCDへの
描画は行われず、ゲーム画面・EMULATOR MENU・起動時のプロファイル選択画面のすべてがHDMI側に
表示されます。詳細は [usage_guide.md](usage_guide.md) §11。

実装: `mp/main.py`（起動時の初期化）、`mp/emulator_menu.py`（`_do_hdmi_toggle`、ON/OFFの
即時切り替えと `pb1000.ini` への保存）、`mp/pb1000.py`（`update_display()`、LCD/HDMI排他制御）、
`src/lcd_controller.c`（`lcd_init_hdmi_output()`/`lcd_render_to_hdmi()`）。

---

## 参考

- 個別機能の使い方: `usage_guide.md`
- タッチパネル・FuncKeyBarの動作説明: `usage_guide.md` §4
- エミュレータメニューからの設定変更: `emulator_menu_guide.md`
- デバッグとトレース: `dev_guide.md` §11
