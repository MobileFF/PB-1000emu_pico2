# PB-1000 エミュレータ 拡張 API 仕様

## 概要

拡張 API は、PB-1000 上の BASIC プログラムから Pico 2 の周辺機能（I2C、SPI、WiFi 等）を
`POKE` / `PEEK` / `CALL` という標準命令だけで呼び出す仕組みである。

既存の call_hook 機構を直接利用する。各拡張関数は独自のアドレスに call_hook として登録され、
BASIC は `CALL <アドレス>` で直接その関数を呼び出す。
単一ディスパッチアドレスや関数コード体系は持たない。

---

## メモリ割り当て

| アドレス | サイズ | 種別 | 内容 |
| --- | --- | --- | --- |
| `0x5F00–0x5FFF` | 256 B | RAM (R/W) | パラメータ・結果ワークエリア |

`0x5E00–0x5EFF` は拡張関数の call_hook アドレスとして慣習的に利用できる空き領域だが、
アドレスの選択は実装者に委ねる。予約された固定アドレスは存在しない。

---

## ワークエリア (`0x5F00–0x5FFF`)

パラメータと結果の受け渡しに使う 256 バイトの RAM 領域。Pico 2 側の `bytearray` が実体。

```text
オフセット   慣習
─────────────────────────────────────────────────────
0x00         [OUT] 結果コード（CALL 後に PEEK で読む）
0x01–0xFF    [IN]  入力パラメータ / [OUT] 出力データ
             （レイアウトは関数ごとに定義する）
─────────────────────────────────────────────────────
```

### 結果コード

| 値 | 定数名 | 意味 |
| --- | --- | --- |
| `0x00` | `EXT_OK` | 正常終了 |
| `0xFF` | `EXT_ERR_GENERAL` | 一般エラー |

その他のエラーコードは関数ごとに定義してよい。

---

## BASIC プログラミングガイド

### 基本パターン

```basic
' 1. パラメータをワークエリアにセット
POKE &5F01, <パラメータ1>
POKE &5F02, <パラメータ2>

' 2. 関数アドレスを直接 CALL
CALL &5Exx

' 3. 結果コードを確認（0=OK）
IF PEEK(&5F00)<>0 THEN PRINT "Error": GOTO <error_handler>

' 4. 戻りデータを読む
RESULT = PEEK(&5F01)
```

関数コードの POKE は不要。パラメータは `0x5F01` から始める（`0x5F00` は結果コード用に空ける）。

---

## Python 拡張ガイド

`pb1000.py` には手を加えない。拡張モジュールを `ext/` ディレクトリに置くだけで
自動ロードされる。

### ディレクトリ構成

```text
mp/
└── ext/
    ├── __init__.py       # 空ファイル（パッケージ宣言）
    ├── dht20.py          # DHT20 温湿度センサー（実装済み）
    ├── ram_test.py       # BANK2/3 RAM テスト + バンク切替（実装済み）
    ├── vram_loader.py    # カラーVRAM イメージローダー（実装済み）
    └── myext.py          # 追加したい拡張をここに置く
```

Pico 2 上では `/ext/` または `/sd/ext/` に配置する（SD カード優先）。

### 自動ロードの仕組み

起動時に `_ext_load_modules()` が `ext/` を走査し、各ファイルを `__import__` で読み込む。
モジュールが `register(system)` 関数を持っていれば呼び出す。それだけ。

### 新しい拡張モジュールの作り方

`mp/ext/myext.py` を作成し、`register(system)` を定義する。

```python
# mp/ext/myext.py

CALL_ADDR = 0x5E20   # CALL &5E20 に割り当て

def register(system):
    try:
        # 必要なハードウェア初期化をここで行う
        system.register_call_hook(CALL_ADDR, lambda: _handler(system))
        print(f"myext: registered at {CALL_ADDR:#06x}")
    except Exception as e:
        print(f"myext: init failed: {e}")

def _handler(system):
    w = system._ext_work
    # w[0]: 結果コード (書き込み必須)
    # w[1..]: パラメータ / 結果データ
    try:
        w[0] = system.EXT_OK
    except Exception:
        w[0] = system.EXT_ERR_GENERAL
```

#### BASIC 側の呼び出し

```basic
POKE &5F01, <パラメータ>
CALL &5E20
IF PEEK(&5F00)<>0 THEN PRINT "Error"
RESULT = PEEK(&5F01)
```

### ワークエリアへの Python 直接アクセス

```python
system._ext_work[0]     # 結果コードの読み書き
system._ext_work[1:N]   # パラメータ / 結果データ
```

---

## 実装詳細

| 項目 | 値 |
| --- | --- |
| クラス定数 `EXT_WORK_BASE` | `0x5F00` |
| クラス定数 `EXT_WORK_SIZE` | `0x100` (256 B) |
| クラス定数 `EXT_OK` | `0x00` |
| クラス定数 `EXT_ERR_GENERAL` | `0xFF` |
| Pico 2 側バッファ | `bytearray(256)` (`self._ext_work`) |
| 初期化メソッド | `_ext_init()` |

`_ext_init()` は `PB1000System.__init__` 内で `_beep_init()` の直後に呼ばれる。

### C ダイレクトモードでの動作

C コアは `0x5F00–0x5FFF` をどの静的バッファにも割り当てていないため、
このアドレス範囲へのアクセスは自動的に Python コールバック
(`_mem_read_impl` / `_mem_write`) へフォールスルーする。

---

## フック有効・無効の切り替え

登録済みフックを解除せずに一時的に有効・無効を切り替えられる。

```python
system.disable_call_hook(CALL_ADDR)  # 無効化（登録は維持）
system.enable_call_hook(CALL_ADDR)   # 再有効化
```

C 側低レベル API（`hd61700` モジュール直接）:

```python
import hd61700
hd61700.set_call_hook_enabled(CALL_ADDR, False)  # 無効化
hd61700.set_call_hook_enabled(CALL_ADDR, True)   # 有効化
```

| Python ラッパー | C API | 説明 |
| --- | --- | --- |
| `system.enable_call_hook(addr)` | `hd61700.set_call_hook_enabled(addr, True)` | フックを有効化 |
| `system.disable_call_hook(addr)` | `hd61700.set_call_hook_enabled(addr, False)` | フックを無効化（登録維持） |
| `system.register_call_hook(addr, fn)` | `hd61700.set_call_hook(addr, fn)` | フックを登録（デフォルト有効） |
| `system.unregister_call_hook(addr)` | `hd61700.clear_call_hook(addr)` | フックを解除 |

---

## 登録済み拡張モジュール

### CALL アドレス一覧

| アドレス | モジュール | 機能 |
| --- | --- | --- |
| `0x5E10` | `dht20.py` | DHT20 温湿度センサー読み取り |
| `0x5E20` | `vram_loader.py` | SD/フラッシュファイル → バンク RAM → カラー VRAM 転送 |
| `0x5E21` | `vram_loader.py` | 仮想FDDイメージ内ファイル → バンク RAM → カラー VRAM 転送 |
| `0x5E30` | `ram_test.py` | BANK2/3 RAM 全テスト実行 (Python 直接 R/W) |
| `0x5E41` | `ram_test.py` | UA データバンクビット → BANK2 (BASIC PEEK/POKE 用) |
| `0x5E51` | `ram_test.py` | UA データバンクビット → BANK3 (BASIC PEEK/POKE 用) |
| `0x5E61` | `ram_test.py` | UA データバンクビット → 復元 (bank0) |

---

### `vram_loader.py` — カラーVRAM イメージローダー

**CALL &H5E20**: SD/フラッシュファイル → バンク RAM → カラー VRAM  
**CALL &H5E21**: 仮想FDDイメージ内ファイル → バンク RAM → カラー VRAM

ファイルをバンク RAM（1/2/3）に読み込みつつ、カラー VRAM にも反映する。
ロード後はバンク RAM にデータが残るため、DMA MMIO (`0x0C30-0x0C37`) で
ファイル I/O なしの高速再転送が可能。

#### ext_work レイアウト（共通）

| オフセット | 方向 | 内容 |
| --- | --- | --- |
| `0x5F00` | OUT | 結果コード: `0`=OK / `1`=未発見 / `2`=読取エラー / `3`=バンク未割当 / `4`=範囲外 / `5`=FDD未マウント |
| `0x5F01` | IN | ファイル名バイト長 |
| `0x5F02–` | IN | ファイル名 ASCII |
| `0x5F42` | IN | 中継バンク番号 (1/2/3、デフォルト=2) |
| `0x5F43` | IN | 転送先オフセット lo (color_vram 内) |
| `0x5F44` | IN | 転送先オフセット hi |
| `0x5F45` | IN | 転送バイト数 lo (0=ファイル全体) |
| `0x5F46` | IN | 転送バイト数 hi |
| `0x5F47` | OUT | 実転送バイト数 lo |
| `0x5F48` | OUT | 実転送バイト数 hi |

#### ファイル名の指定形式

| フック | 最大長 | 形式 | 検索 |
| --- | --- | --- | --- |
| `&H5E20` (SD) | 64 chars | 絶対パス or ファイル名のみ | `/sd/images/` → `/sd/screenshots/` → `/sd/` → `/` |
| `&H5E21` (FDD) | 12 chars | `NAME.EXT` or `NAME    EXT` (8.3形式) | 現在マウント中の FDD イメージ内を検索 |

#### 処理フロー

```
CALL &H5E20 (SD)                    CALL &H5E21 (FDD)
  │                                   │
  ├─ ファイルパスを解決               ├─ FDD マウント確認 (is_ready)
  ├─ open() で読み込み                ├─ name11 に変換 (8.3形式)
  │                                   ├─ open_disk_file(handle, name11)
  │                                   ├─ read_disk_file() × セクタ数
  │                                   └─ close_disk_file(handle)
  ├─ bank_ram[slot][0:n] = data  ←── 共通: バンクRAM に書き込み
  ├─ color_vram[dst:dst+n] = bank_ram[slot][0:n]  ←── VRAM 転送
  └─ 結果・実転送長を ext_work に書き込み
```

#### ロード後の DMA 再転送（BASIC）

```basic
REM bank2 に格納済みのデータを color_vram に再転送（ファイルI/Oなし）
POKE &H0C30,2:POKE &H0C31,0:POKE &H0C32,0
POKE &H0C33,0:POKE &H0C34,0
POKE &H0C35,0:POKE &H0C36,&H30
POKE &H0C37,0
IF PEEK(&H0C37) AND 1 THEN PRINT "DMA ERR"
```

---

## 変更履歴

| 日付 | 内容 |
| --- | --- |
| 2026-05-14 | 初版作成 |
| 2026-05-14 | 単一ディスパッチアドレス廃止。call_hook を関数ごとに直接登録する方式に変更 |
| 2026-05-28 | `enable_call_hook` / `disable_call_hook` API を追記 |
| 2026-06-11 | `ram_test.py` バンク切替フック、`vram_loader.py` を追加 |
