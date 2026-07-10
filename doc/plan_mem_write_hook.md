# メモリ書き込みフック拡張 — 実装プラン

ステータス: **実装済み・実機確認済み（2026-07-09 時点）**

本プランは完了し、現在は `mp/ext/dotds_64dot.py` が DSPMD (`&H68D0`) 監視に本機能を
実運用している（64 ドット表示モード対応、詳細は `doc/extension_api.md` の
`dotds_64dot.py` の項および `doc/dev_guide.md` §6.1 を参照）。本書は経緯・設計記録として残す。

> 実装差分: Step 3/4 のディスパッチャは `offset`(uint32_t)からのバンク抽出を
> 自前で行わず、`c_mem_direct_write()` 冒頭で既に計算済みの `bank`
> (`normalize_bank(segment)` の戻り値、0–3) と `logical_addr` をそのまま
> 引数で受け取る形にした(`c_mem_write_hook_dispatcher(uint16_t addr, uint8_t data, uint8_t bank)`)。
> 挙動・Python側APIは本プラン通りで変更なし。

---

## 概要

特定アドレス（または範囲）への書き込み時に Python/C コールバックを呼ぶ仕組みを追加する。
同時に実験的な RAM ロック機構を削除する。

---

## 現状整理

| 仕組み | 場所 | 特徴 |
|---|---|---|
| CAL フック | `modhd61700.c` `c_call_hook_dispatcher()` | CAL 命令時に PC アドレスで照合 |
| RAM ロック | `hd61700.c` `apply_ram_locks()` | 書き込み値を上書き固定、最大 4 件、**削除対象** |
| `mem_write` CB | `cpu_state.mem_write` → `c_mem_direct_write()` | 全書き込みのエントリポイント（ここに追加） |

---

## 設計決定

### コールバックシグネチャ

```python
fn(addr: int, data: int, bank: int) -> bool | None
```

- `addr`: 0x0000–0xFFFF（論理アドレス）
- `data`: 0x00–0xFF（書き込まれる値）
- `bank`: 0–3（バンク番号。メイン RAM / MMIO は常に 0）
- 戻り値 `True`: 書き込みキャンセル
- 戻り値 `False` / `None`: 書き込み通過

### 発火タイミング：pre-write（書き込み前）

`c_mem_direct_write()` の先頭で発火するため、コールバック内で `cpu_core.read_mem(addr)` を呼べば書き込み前の値を取得できる。

```python
def on_write(addr, data, bank):
    old = cpu_core.read_mem(addr)   # 書き込み前の値
    print(f"{addr:04X}: {old:02X} -> {data:02X}  (bank {bank})")
    # return True  # キャンセルする場合
```

### バンク情報

バンク固有の監視は Python 側のコールバック内で `bank` を見て判断する。
C 側の登録 API にはバンクフィルタを持たせない（データ構造をシンプルに保つ）。

---

## 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/modhd61700.c` | データ構造・ディスパッチャ・Python API 追加、RAM ロック削除 |
| `mp/pb1000.py` | 高レベルラッパーメソッド追加 |
| `src/hd61700.h` | RAM ロック関連フィールド削除 |
| `src/hd61700.c` | `apply_ram_locks()` 削除 |

---

## 実装ステップ

### Step 1 — RAM ロック削除 【実施済み】

**`src/hd61700.h`**
- `locked_addrs[]`, `locked_vals[]`, `n_locked`, `HD61700_RAM_LOCK_MAX` を削除

**`src/hd61700.c`**
- `apply_ram_locks()` 関数を削除
- `mem_writebyte()`, `mem_writebyte_iz()` 内の `apply_ram_locks()` 呼び出しを削除

**`src/modhd61700.c`**
- `mod_lock_ram()`, `mod_unlock_ram()` 関数を削除
- モジュールテーブルから `lock_ram`, `unlock_ram` エントリを削除

---

### Step 2 — データ構造追加（`modhd61700.c` 上部、call_hook 配列の直下）

```c
#define MEM_WRITE_HOOK_MAX 16

static uint16_t mem_write_hook_start[MEM_WRITE_HOOK_MAX];
static uint16_t mem_write_hook_end[MEM_WRITE_HOOK_MAX];
static mp_obj_t  mem_write_hook_fns[MEM_WRITE_HOOK_MAX];
static bool      mem_write_hook_enabled[MEM_WRITE_HOOK_MAX];
static int        mem_write_hook_count = 0;
```

単一アドレスの場合は `start == end` とする。

---

### Step 3 — ディスパッチャ追加（`modhd61700.c`）

```c
// 戻り値 true = 書き込みキャンセル
static bool c_mem_write_hook_dispatcher(uint32_t offset, uint8_t data, uint8_t bank) {
    uint16_t addr = (uint16_t)offset;
    bool cancel = false;
    for (int i = 0; i < mem_write_hook_count; i++) {
        if (mem_write_hook_enabled[i]
                && addr >= mem_write_hook_start[i]
                && addr <= mem_write_hook_end[i]) {
            mp_obj_t args[3] = {
                MP_OBJ_NEW_SMALL_INT(addr),
                MP_OBJ_NEW_SMALL_INT(data),
                MP_OBJ_NEW_SMALL_INT(bank)
            };
            mp_obj_t ret = mp_call_function_n_kw(mem_write_hook_fns[i], 3, 0, args);
            if (ret == mp_const_true) cancel = true;
        }
    }
    return cancel;
}
```

---

### Step 4 — `c_mem_direct_write()` への組み込み（`modhd61700.c`）

関数の先頭（既存ロジックより前）に追加する。
バンク番号は `c_mem_direct_write()` 内で既に行っている segment の解釈と同じ方法で抽出する。

```c
static void c_mem_direct_write(void *ctx, uint8_t segment, uint32_t offset, uint8_t data) {
    // ▼ 追加
    if (mem_write_hook_count > 0) {
        uint8_t bank = (offset >= 0x8000) ? ((segment >> 4) & 0x03) : 0;
        if (c_mem_write_hook_dispatcher(offset, data, bank)) return;  // キャンセル
    }
    // ▲ 追加ここまで
    // 以下既存コード...
```

> **注意**: バンク抽出のビットシフトは `c_mem_direct_write()` 内のバンク RAM ロジックと合わせて実装時に確認すること。

---

### Step 5 — Python API 関数追加（`modhd61700.c`）

| 関数 | 引数 | 動作 |
|---|---|---|
| `set_mem_write_hook(addr, fn)` | 2 args | 単一アドレスにフック登録 |
| `set_mem_write_hook(start, end, fn)` | 3 args | 範囲にフック登録 |
| `clear_mem_write_hook(addr_start)` | 1 arg | `addr_start` で登録削除（swap-with-last） |
| `set_mem_write_hook_enabled(addr_start, bool)` | 2 args | 有効/無効切り替え |

```c
// 2〜3 引数対応
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_set_mem_write_hook_obj, 2, 3, mod_set_mem_write_hook);
MP_DEFINE_CONST_FUN_OBJ_1(mod_clear_mem_write_hook_obj, mod_clear_mem_write_hook);
MP_DEFINE_CONST_FUN_OBJ_2(mod_set_mem_write_hook_enabled_obj, mod_set_mem_write_hook_enabled);
```

モジュールテーブルへの追記:
```c
{ MP_ROM_QSTR(MP_QSTR_set_mem_write_hook),         MP_ROM_PTR(&mod_set_mem_write_hook_obj) },
{ MP_ROM_QSTR(MP_QSTR_clear_mem_write_hook),       MP_ROM_PTR(&mod_clear_mem_write_hook_obj) },
{ MP_ROM_QSTR(MP_QSTR_set_mem_write_hook_enabled), MP_ROM_PTR(&mod_set_mem_write_hook_enabled_obj) },
```

---

### Step 6 — 高レベル Python ラッパー追加（`mp/pb1000.py`）

```python
def register_mem_write_hook(self, addr_start, fn, addr_end=None):
    """fn(addr, data, bank) をメモリ書き込み時に呼ぶ。
    addr_end 省略で単一アドレス監視。True を返すと書き込みキャンセル。"""
    if addr_end is None:
        addr_end = addr_start
    if not hasattr(self, "_mem_write_hook_refs"):
        self._mem_write_hook_refs = {}
    self._mem_write_hook_refs[addr_start] = fn  # GC アンカー
    cpu_core.set_mem_write_hook(addr_start, addr_end, fn)

def unregister_mem_write_hook(self, addr_start):
    if hasattr(self, "_mem_write_hook_refs"):
        self._mem_write_hook_refs.pop(addr_start, None)
    cpu_core.clear_mem_write_hook(addr_start)

def enable_mem_write_hook(self, addr_start):
    cpu_core.set_mem_write_hook_enabled(addr_start, True)

def disable_mem_write_hook(self, addr_start):
    cpu_core.set_mem_write_hook_enabled(addr_start, False)
```

---

## 使用例

```python
# 単一アドレス監視（書き込みログ）
def on_write(addr, data, bank):
    old = cpu_core.read_mem(addr)
    print(f"[WATCH] {addr:04X}: {old:02X} -> {data:02X}  bank={bank}")

system.register_mem_write_hook(0x6100, on_write)

# アドレス範囲監視
system.register_mem_write_hook(0x6000, on_write, addr_end=0x6FFF)

# 書き込みキャンセル（特定値への変化を阻止する例）
def guard_write(addr, data, bank):
    if data == 0xFF:
        return True  # キャンセル

system.register_mem_write_hook(0x6200, guard_write)
```

---

## 備考

- `MP_OBJ_NEW_SMALL_INT` はアロケーション不要（addr: 16bit、data: 8bit、bank: 2bit はすべて小整数範囲内）
- 複数フックが同一アドレスにマッチする場合、いずれか 1 つが `True` を返せばキャンセルになる
- バンク RAM (0x8000–0xFFFF) において、バンク 0 は通常 ROM 領域のため書き込みは発生しない