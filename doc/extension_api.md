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
    ├── __init__.py   # 空ファイル（パッケージ宣言）
    ├── dht20.py      # DHT20 温湿度センサー（実装済み）
    └── myext.py      # 追加したい拡張をここに置く
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

## 変更履歴

| 日付 | 内容 |
| --- | --- |
| 2026-05-14 | 初版作成 |
| 2026-05-14 | 単一ディスパッチアドレス廃止。call_hook を関数ごとに直接登録する方式に変更 |
| 2026-05-28 | `enable_call_hook` / `disable_call_hook` API を追記 |
