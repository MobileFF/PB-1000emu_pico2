# PB-1000 Emulator Memory Map

## Overview

The HD61700 CPU uses a **16-bit offset** combined with **bits 5–4 of the UA register (segment)** to form an effective **18-bit address space**.

```text
Physical address = (UA[5:4] << 16) | offset_16bit
→ bank = (UA >> 4) & 0x03   (0–3)
```

The address space is divided into a **bank-common region (0x0000–0x7FFF)** and a **bank-switched region (0x8000–0xFFFF)**.

---

## Memory Map (16-bit offset view)

```text
Offset             Size    Type        Contents
──────────────────────────────────────────────────────────────────────
0x0000 - 0x0BFF   3 KB    ROM (R)    ROM0 code region
0x0C00 - 0x0C07   8 B     MMIO       SIO / VFDD / printer I/O ports
0x0C08 - 0x0C1F  24 B     -          (unused I/O page space)
0x0C20 - 0x0C24   5 B     MMIO       VDP registers (emulator extension)
0x0C25 - 0x0C2F  11 B     -          (unused I/O page space)
0x0C30 - 0x0C37   8 B     MMIO       DMA registers (bank RAM → colour VRAM transfer)
0x0C38 - 0x0CFF  200 B    -          (unused I/O page space)
0x0D00 - 0x17FF  ~2.75 KB ROM (R)    ROM0 data region (font, tables)
0x1800 - 0x5EFF  ~27 KB   -          Unmapped (reads 0xFF / writes ignored)
                           Note: 0x5E00–0x5EFF is conventionally used for extension call_hook addresses
0x5F00 - 0x5FFF  256 B    RAM (R/W)  Extension API work area (_ext_work bytearray)
0x6000 - 0x7FFF   8 KB    RAM (R/W)  Standard RAM (ram_buf / ram0.bin)
  0x6100 - 0x61FF   256 B   (VRAM)   EDTOP: LCD screen buffer
  0x6201 - 0x6850  1616 B   (VRAM)   LEDTP: LCD dot matrix buffer
0x8000 - 0xFFFF  32 KB    Bank-switched (bank = UA[5:4])
  Bank 0:                   ROM (R)  System ROM (ROM1, rom1_buf / rom1.bin)
  Bank 1:                   RAM (R/W) Expanded RAM1 (bank1_buf / ram1.bin)
  Bank 2:                   RAM (R/W) Expanded RAM2 (bank2_buf / ram2.bin)
  Bank 3:                   RAM (R/W) Expanded RAM3 (bank3_buf / ram3.bin)
──────────────────────────────────────────────────────────────────────
```

> **ROM0 boundary**: Hardware decoding makes 0x0C00–0x0CFF an I/O page in its entirety.
> The ROM0 binary is 0x1800 bytes (0x0000–0x17FF).

---

## Bank Switching (0x8000–0xFFFF)

```text
UA bits 5-4   Bank   Buffer       Access    File
─────────────────────────────────────────────────────────────
  00           0      rom1_buf     R only    rom1.bin
  01           1      bank1_buf    R/W       ram1.bin
  10           2      bank2_buf    R/W       ram2.bin
  11           3      bank3_buf    R/W       ram3.bin
─────────────────────────────────────────────────────────────
```

- **Bank 0** (ROM1) is always present (`has_bank[0] = true`).
- **Banks 1–3** (expanded RAM) are enabled only when the corresponding `ramN.bin` file exists (`has_bank[N] = true`). If the file is absent, the bank behaves as unmapped (reads 0xFF).
- Bank selection formula: `bank = (REG_UA >> 4) & 0x03` (consistent in both C and Python).

---

## MMIO Registers (0x0C00–0x0C07)

Emulates the I/O ports of the original PB-1000.

| Address | Dir | Purpose | Emulator handling |
| --- | --- | --- | --- |
| `0x0C00` | R/W | SIO status register | `_io_rd_regs[0]` (LB/FM bits masked) |
| `0x0C01` | R/W | SIO control register | `_io_rd_regs[1]` (TX/RX Ready flags) |
| `0x0C02` | R | SIO receive data | PIO UART receive data |
| `0x0C03` | R/W | SIO TX / VFDD read | Read: VFDD data register / Write: PIO UART TX |
| `0x0C04` | R/W | VFDD write data / printer status | VFDD write data (`_io_wr_regs[4]`) |
| `0x0C05` | W | Printer data port | `_io_wr_regs[5]` |
| `0x0C06` | W | Printer control port | `_io_wr_regs[6]` |
| `0x0C07` | — | Unused | — |

The full MMIO range 0x0C00–0x0CFF is checked first by the C core (`hd61700.c`) and delegated to `io_read` / `io_write` callbacks (`_fdd_read_bridge_fn` / `_fdd_write_bridge_fn`).

---

## Extended MMIO (Emulator-specific)

| Address | Purpose | Status |
| --- | --- | --- |
| `0x0C20` | VDP address register low (R/W) | Implemented |
| `0x0C21` | VDP address register high (R/W) | Implemented |
| `0x0C22` | VDP data register (R/W) | Implemented |
| `0x0C23` | VDP foreground colour register (RGB332) (R/W) | Implemented |
| `0x0C24` | VDP background colour register (RGB332) (R/W) | Implemented |
| `0x0C08–0x0C1F` | Unused | Reserved for expansion |
| `0x0C25–0x0C2F` | Unused | Reserved for expansion |
| `0x0C30` | DMA source bank number (W) | Implemented |
| `0x0C31` | DMA source offset bit[7:0] (W) | Implemented |
| `0x0C32` | DMA source offset bit[14:8] (W) | Implemented |
| `0x0C33` | DMA destination offset bit[7:0] (W) | Implemented |
| `0x0C34` | DMA destination offset bit[13:8] (W) | Implemented |
| `0x0C35` | DMA transfer byte count bit[7:0] (W) | Implemented |
| `0x0C36` | DMA transfer byte count bit[13:8] (W) | Implemented |
| `0x0C37` | DMA trigger (W) / status (R, bit0=error) | Implemented |
| `0x0C38–0x0CFF` | Unused | Reserved for expansion |

### DMA Registers (0x0C30–0x0C37)

Performs a synchronous block transfer from bank RAM (Bank 1/2/3) to the colour VRAM (192×64 = 12,288 B) entirely in C — no Python callbacks are involved.

```
Source:      bankN_buf[dma_src_addr .. dma_src_addr + dma_len - 1]
Destination: color_vram[dma_dst_addr .. dma_dst_addr + dma_len - 1]
```

| Constraint | Value |
| --- | --- |
| Source bank | 1 / 2 / 3 (`has_bank[N]` must be true) |
| Source offset upper limit | src_addr + len ≤ 0x8000 |
| Destination offset upper limit | dst_addr + len ≤ 12,288 (LCD_COLOR_VRAM_SIZE) |
| Transfer length 0 | Treated as error |

BASIC example (transfer all of Bank 2 to colour VRAM):
```basic
POKE &H0C30,2      : REM bank=2
POKE &H0C31,0      : REM src offset = 0x0000
POKE &H0C32,0
POKE &H0C33,0      : REM dst offset = 0x0000
POKE &H0C34,0
POKE &H0C35,0      : REM length = 0x3000 = 12288
POKE &H0C36,&H30
POKE &H0C37,0      : REM FIRE
IF PEEK(&H0C37) AND 1 THEN PRINT "DMA ERROR"
```

> **Known concern (unverified)**: the DMA execution path in `modhd61700.c` only
> `memcpy()`s into `color_vram` — it never calls `lcd_c`'s `mark_dirty()`.
> Since `lcd_render_to_display()` returns immediately when the `dirty` flag
> isn't set, a DMA re-transfer performed with no other LCD write in between
> (nothing else setting `dirty`) may not actually appear on screen.
> `vram_loader.py` (the initial CALL-based load) works fine because
> `_finish_transfer()` explicitly calls `mark_dirty()`, but this standalone
> DMA-MMIO re-transfer path has not been confirmed on real hardware. Needs
> verification.

> Because the `io_read`/`io_write` hook range in `hd61700.c` covers 0x0C00–0x0CFF, VDP reads and writes work correctly even in C-direct mode via `_fdd_read/write_bridge_fn`.

---

## C-Side Static Buffers (`modhd61700.c`)

| Buffer | Size | Address range | Contents |
| --- | --- | --- | --- |
| `rom0_buf` | 8 KB | 0x0000–0x17FF | Internal ROM (ROM0) |
| `ext_work_buf` | 256 B | 0x5F00–0x5FFF | Extension API work area (shared with Python `_ext_work`) |
| `ram_buf` | 8 KB | 0x6000–0x7FFF | Standard RAM |
| `rom1_buf` | 32 KB | 0x8000–0xFFFF (Bank 0) | System ROM (ROM1) |
| `bank1_buf` | 32 KB | 0x8000–0xFFFF (Bank 1) | Expanded RAM1 |
| `bank2_buf` | 32 KB | 0x8000–0xFFFF (Bank 2) | Expanded RAM2 |
| `bank3_buf` | 32 KB | 0x8000–0xFFFF (Bank 3) | Expanded RAM3 |

`has_bank[4]` flags manage which banks are active.
`bank_ptr[4]` / `bank_is_ram[4]` are stored in the `hd61700_state_t` CPU state struct.

---

## Python-Side Constants (`pb1000.py`)

| Constant | Value | Meaning |
| --- | --- | --- |
| `INT_ROM_LIMIT` | `0x2000` | Internal ROM upper limit check in the Python callback path |
| `RAM_START` | `0x6000` | Standard RAM start address |
| `RAM_SIZE` | `0x2000` (8 KB) | Standard RAM size |
| `SYS_ROM_START` | `0x8000` | Bank-switched region start |
| `EXP_RAM_SIZE` | `0x8000` (32 KB) | Size of one expanded RAM bank |
| `EXT_WORK_BASE` | `0x5F00` | Extension API work area base address |
| `EXT_WORK_SIZE` | `0x100` (256 B) | Extension API work area size |

---

## Memory Access Path

In C-direct mode (normal operation, `use_c_memory=True`) accesses are resolved in this order:

```text
CPU memory access (hd61700.c: mem_readbyte / mem_writebyte)
  │
  ├─ 0x0C00–0x0CFF?  → io_read / io_write callback (Python: _fdd_*_bridge_fn)
  │                        ↳ VFDD / SIO / printer / VDP MMIO handling
  │
  ├─ offset < 0x1800?  → rom0_ptr (rom0_buf): ROM0 code + data
  │
  ├─ 0x6000 ≤ offset < 0x8000?  → ram_ptr (ram_buf): standard RAM
  │
  ├─ offset ≥ 0x8000?
  │     → bank = (UA >> 4) & 0x03
  │     → bank_ptr[bank]: ROM1 or expanded RAM 1/2/3
  │           writes to banks with bank_is_ram[bank]=false are ignored
  │
  └─ none of the above
        → mem_read / mem_write callback (Python fallback)
              ↳ 0x5F00–0x5FFF: extension API work area (_ext_work bytearray)
```

In Python-managed mode (`use_c_memory=False` / debug mode), `_mem_read_impl` executes the equivalent logic in Python.

---

## Interrupt Vectors

| Interrupt | Vector address | Description |
| --- | --- | --- |
| `ON_INT` | `0x0032` | Power ON key interrupt |
| `TIMER_INT` | `0x0042` | 1-minute timer interrupt |
| `INT2` | `0x0052` | External interrupt 2 |
| `KEY_INT` | `0x0062` | Key / pulse interrupt |
| `INT1` | `0x0072` | External interrupt 1 |

---

## File-to-Address Mapping

| File | C buffer | Address range |
| --- | --- | --- |
| `rom0.bin` | `rom0_buf` | 0x0000–0x17FF |
| `rom1.bin` | `rom1_buf` | 0x8000–0xFFFF (Bank 0) |
| `ram0.bin` | `ram_buf` | 0x6000–0x7FFF |
| `ram1.bin` | `bank1_buf` | 0x8000–0xFFFF (Bank 1) |
| `ram2.bin` | `bank2_buf` | 0x8000–0xFFFF (Bank 2) |
| `ram3.bin` | `bank3_buf` | 0x8000–0xFFFF (Bank 3) |

File search priority (`_get_storage_path`):
1. `profile_dir/` (if specified)
2. `/sd/`
3. `/roms/`
4. `/` (root)

---

## Related Source Files

| File | Role |
| --- | --- |
| [src/hd61700.c](../src/hd61700.c) | CPU core: `mem_readbyte` / `mem_writebyte` / bank switching |
| [src/hd61700.h](../src/hd61700.h) | CPU state definition: `bank_ptr[]` / `bank_is_ram[]` / `rom0_ptr` / `ram_ptr` |
| [src/modhd61700.c](../src/modhd61700.c) | C buffer declarations: `rom0_buf` / `ram_buf` / `rom1_buf` / `bank1–3_buf` / `has_bank[]` |
| [mp/pb1000.py](../mp/pb1000.py) | Python system: `_mem_read_impl` / `_mem_write` / `has_bank[]` / `_bank_ram[]` |
| [doc/extension_api.md](extension_api.md) | Extension API spec: work area / call_hook registration |
