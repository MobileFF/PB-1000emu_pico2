# PB-1000 エミュレータ メモリマップ

## 概要

HD61700 CPU は **16-bit オフセット**に **UA レジスタ（segment）の bits 5-4** を組み合わせた
**実効 18-bit アドレス空間**を持つ。

```text
物理アドレス = (UA[5:4] << 16) | offset_16bit
→ bank = (UA >> 4) & 0x03   (0–3)
```

アドレス空間は大きく「バンク共通領域（0x0000–0x7FFF）」と
「バンク切り替え領域（0x8000–0xFFFF）」に分かれる。

---

## メモリマップ（16-bit オフセット視点）

```text
オフセット         サイズ    種別        内容
──────────────────────────────────────────────────────────────────────
0x0000 - 0x0BFF   3 KB    ROM (R)    ROM0 コード領域
0x0C00 - 0x0C07   8 B     MMIO       SIO / VFDD / プリンタ I/O ポート
0x0C08 - 0x0C1F  24 B     -          (未使用 I/O ページ内空間)
0x0C20 - 0x0C24   5 B     MMIO       VDP レジスタ (PR7 拡張, 計画中)
0x0C25 - 0x0CFF  219 B    -          (未使用 I/O ページ内空間)
0x0D00 - 0x17FF   2.75 KB ROM (R)    ROM0 データ領域（フォント・テーブル）
0x1800 - 0x5EFF  ~27 KB   -          未マップ（読み出し 0xFF / 書き込み無効）
                           ※ 0x5E00–0x5EFF は拡張 call_hook 慣習アドレス帯
0x5F00 - 0x5FFF  256 B    RAM (R/W)  拡張 API ワークエリア（_ext_work bytearray）
0x6000 - 0x7FFF   8 KB    RAM (R/W)  標準 RAM（ram_buf / ram0.bin）
  0x6100 - 0x61FF   256 B   (VRAM)   EDTOP：LCD 画面バッファ
  0x6201 - 0x6850   1616 B  (VRAM)   LEDTP：LCD ドットマトリクスバッファ
0x8000 - 0xFFFF  32 KB    バンク切り替え領域（バンク番号 = UA[5:4]）
  Bank 0:                   ROM (R)  System ROM (ROM1, rom1_buf / rom1.bin)
  Bank 1:                   RAM (R/W) 拡張 RAM1 (bank1_buf / ram1.bin)
  Bank 2:                   RAM (R/W) 拡張 RAM2 (bank2_buf / ram2.bin)
  Bank 3:                   RAM (R/W) 拡張 RAM3 (bank3_buf / ram3.bin)
──────────────────────────────────────────────────────────────────────
```

> **ROM0 境界**: ハードウェア・デコードにより `0x0C00–0x0CFF` 全体が I/O ページとなる。
> ROM0 バイナリのサイズは 0x1800 バイト（0x0000–0x17FF）。

---

## バンク切り替え（0x8000–0xFFFF）

```text
UA レジスタ bits 5-4   bank 番号   対象バッファ     読み書き   ファイル
─────────────────────────────────────────────────────────────
  00                    0           rom1_buf         R only     rom1.bin
  01                    1           bank1_buf        R/W        ram1.bin
  10                    2           bank2_buf        R/W        ram2.bin
  11                    3           bank3_buf        R/W        ram3.bin
─────────────────────────────────────────────────────────────
```

- **Bank 0**（ROM1）は常に存在（`has_bank[0] = true`）。
- **Bank 1–3**（拡張 RAM）は、対応する `ramN.bin` ファイルが存在する場合のみ有効化される
  （`has_bank[N] = true`）。ファイルが存在しなければ未マップと同様（読み出し 0xFF）。
- バンク選択式: `bank = (REG_UA >> 4) & 0x03` （C 側 `hd61700.c`、Python 側ともに統一）。

---

## MMIO レジスタ（0x0C00–0x0C07）

オリジナル PB-1000 の I/O ポートをエミュレートする。

| アドレス | 方向 | 用途 | エミュレータでの扱い |
| --- | --- | --- | --- |
| `0x0C00` | R/W | SIO ステータスレジスタ | `_io_rd_regs[0]`（LB/FM ビットマスク済み） |
| `0x0C01` | R/W | SIO コントロールレジスタ | `_io_rd_regs[1]`（TX/RX Ready フラグ） |
| `0x0C02` | R | SIO 受信データレジスタ | PIO UART 受信データ |
| `0x0C03` | R/W | SIO TX / VFDD 読み取り | 読み: VFDD データレジスタ / 書き: PIO UART TX |
| `0x0C04` | R/W | VFDD 書き込みデータ / プリンタステータス | VFDD 書き込みデータ (`_io_wr_regs[4]`) |
| `0x0C05` | W | プリンタデータポート | `_io_wr_regs[5]` |
| `0x0C06` | W | プリンタコントロールポート | `_io_wr_regs[6]` |
| `0x0C07` | - | 未使用 | - |

MMIO 範囲 `0x0C00–0x0CFF` は C コア (`hd61700.c`) が最優先でチェックし、
`io_read` / `io_write` コールバック（`_fdd_read_bridge_fn` / `_fdd_write_bridge_fn`）に委譲する。

---

## MMIO 拡張領域（エミュレータ独自）

| アドレス | 用途 | 状態 |
| --- | --- | --- |
| `0x0C20` | VDP アドレスレジスタ Low (R/W) | PR7 計画中 |
| `0x0C21` | VDP アドレスレジスタ High (R/W) | PR7 計画中 |
| `0x0C22` | VDP データレジスタ (R/W) | PR7 計画中 |
| `0x0C23` | VDP 前景色レジスタ（RGB332）(R/W) | PR7 計画中 |
| `0x0C24` | VDP 背景色レジスタ（RGB332）(R/W) | PR7 計画中 |
| `0x0C08–0x0C1F` | 未使用 | 拡張用予約 |
| `0x0C25–0x0CFF` | 未使用 | 拡張用予約 |

> `hd61700.c` の `io_read`/`io_write` フック範囲は `0x0C00–0x0CFF` であるため、
> C ダイレクトモードでも `_fdd_read/write_bridge_fn` 経由で VDP 読み書きが正しく機能する。

---

## C 側静的バッファ（`modhd61700.c`）

| バッファ名 | サイズ | 対応アドレス | 内容 |
| --- | --- | --- | --- |
| `rom0_buf` | 8 KB | 0x0000–0x17FF | Internal ROM (ROM0) |
| `ram_buf` | 8 KB | 0x6000–0x7FFF | 標準 RAM |
| `rom1_buf` | 32 KB | 0x8000–0xFFFF (Bank 0) | System ROM (ROM1) |
| `bank1_buf` | 32 KB | 0x8000–0xFFFF (Bank 1) | 拡張 RAM1 |
| `bank2_buf` | 32 KB | 0x8000–0xFFFF (Bank 2) | 拡張 RAM2 |
| `bank3_buf` | 32 KB | 0x8000–0xFFFF (Bank 3) | 拡張 RAM3 |

`has_bank[4]` フラグで各バンクの有効/無効を管理する。
`bank_ptr[4]` / `bank_is_ram[4]` は `hd61700_state_t` の CPU ステートに格納される。

---

## Python 側定数（`pb1000.py`）

| 定数 | 値 | 意味 |
| --- | --- | --- |
| `INT_ROM_LIMIT` | `0x2000` | Python コールバック経路での内部 ROM 上限チェック値 |
| `RAM_START` | `0x6000` | 標準 RAM 開始アドレス |
| `RAM_SIZE` | `0x2000` (8 KB) | 標準 RAM サイズ |
| `SYS_ROM_START` | `0x8000` | バンク切り替え領域開始 |
| `EXP_RAM_SIZE` | `0x8000` (32 KB) | 拡張 RAM 1 バンク分のサイズ |
| `EXT_WORK_BASE` | `0x5F00` | 拡張 API ワークエリア開始アドレス |
| `EXT_WORK_SIZE` | `0x100` (256 B) | 拡張 API ワークエリアサイズ |

---

## メモリアクセス経路

C ダイレクトモード（通常動作 `use_c_memory=True`）では以下の順でアクセスが解決される。

```text
CPU メモリアクセス（hd61700.c: mem_readbyte / mem_writebyte）
  │
  ├─ 0x0C00–0x0CFF?  → io_read / io_write コールバック（Python: _fdd_*_bridge_fn）
  │                         ↳ VFDD / SIO / プリンタ / VDP MMIO 処理
  │
  ├─ offset < 0x1800?  → rom0_ptr（rom0_buf）: ROM0 コード + データ
  │
  ├─ 0x6000 ≤ offset < 0x8000?  → ram_ptr（ram_buf）: 標準 RAM
  │
  ├─ offset ≥ 0x8000?
  │     → bank = (UA >> 4) & 0x03
  │     → bank_ptr[bank]: ROM1 or 拡張 RAM 1/2/3
  │           bank_is_ram[bank] が false のバンクへの書き込みは無効
  │
  └─ 上記いずれも該当しない場合
        → mem_read / mem_write コールバック（Python フォールバック）
              ↳ 0x5F00–0x5FFF: 拡張 API ワークエリア（_ext_work bytearray）
```

Python コールバック経路（`use_c_memory=False` / デバッグモード）では
`_mem_read_impl` が上記と同等のロジックを Python で実行する。

---

## 割り込みベクタ

| 割り込み名 | ベクタアドレス | 説明 |
| --- | --- | --- |
| `ON_INT` | `0x0032` | 電源 ON キー割り込み |
| `TIMER_INT` | `0x0042` | 1 分タイマ割り込み |
| `INT2` | `0x0052` | 外部割り込み 2 |
| `KEY_INT` | `0x0062` | キー / パルス割り込み |
| `INT1` | `0x0072` | 外部割り込み 1 |

---

## ファイルとアドレスの対応

| ファイル | 格納先 | 対応アドレス |
| --- | --- | --- |
| `rom0.bin` | C: `rom0_buf` | 0x0000–0x17FF |
| `rom1.bin` | C: `rom1_buf` | 0x8000–0xFFFF (Bank 0) |
| `ram0.bin` | C: `ram_buf` | 0x6000–0x7FFF |
| `ram1.bin` | C: `bank1_buf` | 0x8000–0xFFFF (Bank 1) |
| `ram2.bin` | C: `bank2_buf` | 0x8000–0xFFFF (Bank 2) |
| `ram3.bin` | C: `bank3_buf` | 0x8000–0xFFFF (Bank 3) |

ファイルの検索優先順位（`_get_storage_path`）:
1. `profile_dir/` (指定時)
2. `/sd/` (SD カードマウント時)
3. `/roms/`
4. `/` (ルート)

---

## 関連ソースファイル

| ファイル | 役割 |
| --- | --- |
| [src/hd61700.c](../src/hd61700.c) | CPU コア: `mem_readbyte` / `mem_writebyte` / バンク切り替え |
| [src/hd61700.h](../src/hd61700.h) | CPU ステート定義: `bank_ptr[]` / `bank_is_ram[]` / `rom0_ptr` / `ram_ptr` |
| [src/modhd61700.c](../src/modhd61700.c) | C バッファ宣言: `rom0_buf` / `ram_buf` / `rom1_buf` / `bank1–3_buf` / `has_bank[]` |
| [mp/pb1000.py](../mp/pb1000.py) | Python システム: `_mem_read_impl` / `_mem_write` / `has_bank[]` / `_bank_ram[]` |
| [references/pb1000_mmio_map.md](pb1000_mmio_map.md) | MMIO レジスタ詳細（原典解析） |
| [doc/extension_api.md](extension_api.md) | 拡張 API 仕様: ワークエリア / call_hook 登録方法 |
