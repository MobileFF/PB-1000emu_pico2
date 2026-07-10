# PB-1000 Emulator Extension API Specification

## Overview

The Extension API lets BASIC programs running on the PB-1000 call Pico 2 peripherals (I2C, SPI, WiFi, etc.) using only the standard `POKE`, `PEEK`, and `CALL` instructions.

It is built directly on the call_hook mechanism. Each extension function is registered as a call_hook at its own address, and BASIC calls it directly with `CALL <address>`. There is no single dispatch address or function-code scheme.

---

## Memory Allocation

| Address | Size | Type | Contents |
| --- | --- | --- | --- |
| `0x5F00–0x5FFF` | 256 B | RAM (R/W) | Parameter and result work area |

`0x5E00–0x5EFF` is an unmapped region that is available by convention for extension function call_hook addresses, but address selection is left to the implementer. No fixed addresses are reserved.

---

## Work Area (`0x5F00–0x5FFF`)

A 256-byte RAM region used for passing parameters and results. The actual storage is a Pico 2-side `bytearray`.

```text
Offset   Convention
─────────────────────────────────────────────────────
0x00     [OUT] Result code (read with PEEK after CALL)
0x01–0xFF [IN] Input parameters / [OUT] Output data
          (layout is defined per function)
─────────────────────────────────────────────────────
```

### Result Codes

| Value | Constant | Meaning |
| --- | --- | --- |
| `0x00` | `EXT_OK` | Success |
| `0xFF` | `EXT_ERR_GENERAL` | General error |

Additional error codes may be defined per function.

---

## BASIC Programming Guide

### Basic Pattern

```basic
' 1. Set parameters in the work area
POKE &5F01, <param1>
POKE &5F02, <param2>

' 2. Call the function address directly
CALL &5Exx

' 3. Check the result code (0 = OK)
IF PEEK(&5F00)<>0 THEN PRINT "Error": GOTO <error_handler>

' 4. Read return data
RESULT = PEEK(&5F01)
```

There is no need to POKE a function code. Parameters start at `0x5F01` (leaving `0x5F00` for the result code).

---

## Python Extension Guide

No changes to `pb1000.py` are needed. Place an extension module in the `ext/` directory and it will be loaded automatically on startup.

### Directory Layout

```text
mp/
└── ext/
    ├── __init__.py       # empty file (package declaration)
    ├── bank_loader.py    # bank RAM loader (included)
    ├── vram_loader.py    # colour VRAM image loader (included)
    ├── dotds_64dot.py    # DOTDS / single-char display SCTOP-independence fix (included, internal)
    └── myext.py          # add your own extensions here
```

`sample/mp/ext/` contains ready-to-use sample extensions (`dht20.py` — DHT20 temperature/humidity
sensor, `ram_test.py`, etc.), but these are **not** auto-loaded unless copied into `mp/ext/`
(`_ext_load_modules()` only scans `/ext/` and `/sd/ext/`; `sample/` is never scanned).

On the Pico 2, place files under `/ext/` or `/sd/ext/` (SD card takes priority).

### Auto-Load Mechanism

At startup, `_ext_load_modules()` scans the `ext/` directory and imports each file with `__import__`. If a module defines `register(system)`, that function is called. That is all.

### Creating a New Extension Module

Create `mp/ext/myext.py` and define `register(system)`:

```python
# mp/ext/myext.py

CALL_ADDR = 0x5E20   # assigned to CALL &5E20

def register(system):
    try:
        # Perform any required hardware initialisation here
        system.register_call_hook(CALL_ADDR, lambda: _handler(system))
        print(f"myext: registered at {CALL_ADDR:#06x}")
    except Exception as e:
        print(f"myext: init failed: {e}")

def _handler(system):
    w = system._ext_work
    # w[0]: result code (must be written)
    # w[1..]: parameter / result data
    try:
        w[0] = system.EXT_OK
    except Exception:
        w[0] = system.EXT_ERR_GENERAL
```

#### BASIC-side Call

```basic
POKE &5F01, <parameter>
CALL &5E20
IF PEEK(&5F00)<>0 THEN PRINT "Error"
RESULT = PEEK(&5F01)
```

### Direct Python Access to the Work Area

```python
system._ext_work[0]     # read/write the result code
system._ext_work[1:N]   # parameter / result data
```

---

## Implementation Details

| Item | Value |
| --- | --- |
| Class constant `EXT_WORK_BASE` | `0x5F00` |
| Class constant `EXT_WORK_SIZE` | `0x100` (256 B) |
| Class constant `EXT_OK` | `0x00` |
| Class constant `EXT_ERR_GENERAL` | `0xFF` |
| Pico 2 buffer | `bytearray(256)` (`self._ext_work`) |
| Initialisation method | `_ext_init()` |

`_ext_init()` is called inside `PB1000System.__init__` immediately after `_beep_init()`.

### Behaviour in C-Direct Mode

The C core maps `ext_work_buf` (a 256-byte static buffer, `src/modhd61700.c`) to `0x5F00–0x5FFF` and
handles both reads and writes entirely in C (there is no Python-callback fallthrough).
`hd61700.get_ext_work_view()` returns a zero-copy `bytearray` view onto this buffer, and
`PB1000System._ext_init()` (`mp/pb1000.py`) uses that view directly as `self._ext_work` when available,
so there is no copy between the C core and Python. A plain `bytearray(256)` not backed by the C buffer
is used only as a fallback for compatibility with older core builds that lack `get_ext_work_view()`.

---

## Hook Enable / Disable

Registered hooks can be temporarily enabled and disabled without being removed.

```python
system.disable_call_hook(CALL_ADDR)  # disable (registration kept)
system.enable_call_hook(CALL_ADDR)   # re-enable
```

Low-level C API (direct `hd61700` module):

```python
import hd61700
hd61700.set_call_hook_enabled(CALL_ADDR, False)  # disable
hd61700.set_call_hook_enabled(CALL_ADDR, True)   # enable
```

| Python wrapper | C API | Description |
| --- | --- | --- |
| `system.enable_call_hook(addr)` | `hd61700.set_call_hook_enabled(addr, True)` | Enable a hook |
| `system.disable_call_hook(addr)` | `hd61700.set_call_hook_enabled(addr, False)` | Disable a hook (registration kept) |
| `system.register_call_hook(addr, fn)` | `hd61700.set_call_hook(addr, fn)` | Register a hook (enabled by default) |
| `system.unregister_call_hook(addr)` | `hd61700.clear_call_hook(addr)` | Remove a hook |

---

## Registered Extension Modules

### CALL Address Table

| Address | Module | Function |
| --- | --- | --- |
| `0x5E10` | `dht20.py` (sample, not shipped) | Read DHT20 temperature/humidity sensor |
| `0x5E20` | `vram_loader.py` | SD/flash file → bank RAM → colour VRAM transfer |
| `0x5E21` | `vram_loader.py` | Virtual FDD image file → bank RAM → colour VRAM transfer |
| `0x5E41`/`0x5E51`/`0x5E61`/`0x5E71` | `ram_test.py` (sample, not shipped) | Various RAM tests |
| `0x5E81` | `bank_loader.py` | Load SD/flash file into bank RAM |
| `0x5E91` | `bank_loader.py` | Load virtual FDD image file into bank RAM |
| `0x022C` | `dotds_64dot.py` (internal fix) | DOTDS: bulk LEDTP → monochrome VRAM transfer (SCTOP-independent) |
| `0x02BD` | `dotds_64dot.py` (internal fix) | Single-char quick display: direct EDCSR write (SCTOP-independent) |

`dht20.py` / `ram_test.py` live under `sample/mp/ext/` and do not run on a stock device unless
copied into `mp/ext/` (marked "sample, not shipped" above). `dotds_64dot.py` is not a general-purpose
BASIC extension but an internal fix module for 64-dot display mode, and it always ships as part of
`mp/ext/`. When adding your own extensions, avoid colliding with its `0x022C`/`0x02BD` call addresses.

---

### `vram_loader.py` — Colour VRAM Image Loader

**CALL &H5E20**: SD/flash file → bank RAM → colour VRAM  
**CALL &H5E21**: Virtual FDD image file → bank RAM → colour VRAM

Loads a file into bank RAM (1/2/3) and simultaneously writes it to the colour VRAM. The data remains in bank RAM after loading, enabling fast re-transfer via DMA MMIO (`0x0C30–0x0C37`) without further file I/O.

After the transfer, the handler calls `set_vdp_enable(True)`, `set_vdp_init_done(True)`, and `mark_dirty()` so the newly loaded image is picked up on the next frame. The write to `color_vram` bypasses `lcd_c.vdp_write()` (the CPU's VDP port), so `vdp_init_fill_done` is never set automatically — without the explicit `set_vdp_init_done(True)` call the renderer would keep falling back to the monochrome VRAM and the loaded image would never appear on screen.

#### ext_work Layout (common)

| Offset | Dir | Contents |
| --- | --- | --- |
| `0x5F00` | OUT | Result code: `0`=OK / `1`=not found / `2`=read error / `3`=bank not assigned / `4`=out of range / `5`=FDD not mounted |
| `0x5F01` | IN | Filename byte length |
| `0x5F02–0x5F41` | IN | Filename ASCII (CALL &H5E20: up to 64 chars; CALL &H5E21: up to 12 chars, 8.3 format) |
| `0x5F42` | IN | Relay bank number (1/2/3, default=2) |
| `0x5F43` | IN | Destination offset lo (within colour VRAM) |
| `0x5F44` | IN | Destination offset hi |
| `0x5F45` | IN | Transfer byte count lo (0=whole file) |
| `0x5F46` | IN | Transfer byte count hi |
| `0x5F47` | OUT | Actual bytes transferred lo |
| `0x5F48` | OUT | Actual bytes transferred hi |
| `0x5F49` | IN | Leading skip byte count lo (default=0; BSAVE header = 4) |
| `0x5F4A` | IN | Leading skip byte count hi |

---

### `bank_loader.py` — Bank RAM Loader

**CALL &H5E81**: SD/flash file → bank RAM  
**CALL &H5E91**: Virtual FDD image file → bank RAM

Loads binary data into bank RAM (1/2/3) without writing to colour VRAM. Combine with DMA MMIO (`0x0C30–0x0C37`) for on-demand VRAM transfer.

#### ext_work Layout (CALL &H5E81 — SD load)

| Offset | Dir | Contents |
| --- | --- | --- |
| `0x5F00` | IN | Bank number (1/2/3) |
| `0x5F01` | IN | Destination offset hi |
| `0x5F02` | IN | Destination offset lo |
| `0x5F03` | IN | File skip offset hi (0=start of file) |
| `0x5F04` | IN | File skip offset lo |
| `0x5F05` | IN | Max transfer byte count hi (0=all that fits) |
| `0x5F06` | IN | Max transfer byte count lo |
| `0x5F07–` | IN | File path, null-terminated ASCII (e.g. `/sd/game.bin`) |
| `0x5F00` | OUT | Result code: `0x00`=OK / `0x01`=bank not present / `0x02`=file error / `0xFF`=other |
| `0x5F01` | OUT | Bytes loaded hi |
| `0x5F02` | OUT | Bytes loaded lo |

#### ext_work Layout (CALL &H5E91 — FDD load)

| Offset | Dir | Contents |
| --- | --- | --- |
| `0x5F00` | IN | Bank number (1/2/3) |
| `0x5F01` | IN | Destination offset hi |
| `0x5F02` | IN | Destination offset lo |
| `0x5F03` | IN | Records to skip (0=from beginning) |
| `0x5F04–0x5F0E` | IN | Filename 11 bytes (8.3 format, space-padded) |
| `0x5F00` | OUT | Result code: `0x00`=OK / `0x01`=bank not present / `0x02`=file not found / `0x03`=FDD not ready / `0xFF`=other |
| `0x5F01` | OUT | Bytes loaded hi |
| `0x5F02` | OUT | Bytes loaded lo |

---

### `dotds_64dot.py` — 64-Dot Display Fix (Internal Module)

Not a BASIC-facing CALL extension, but an internal fix so DOTDS (`&H022C`) and single-char quick
display (`&H02BD`) render all 8 rows correctly in 64-dot display mode.

- **call_hook `&H022C` (DOTDS override)**: bulk-transfers `lcd_c.get_num_pages() * 192` bytes from the
  LEDTP buffer (`&H6201`~) into monochrome VRAM (4 rows in 32-dot mode, 8 rows in 64-dot mode).
- **call_hook `&H02BD` (single-char quick display override)**: computes row/column from the raw EDCSR
  (ignoring SCTOP) and writes the 6 bytes pushed by the caller in registers `$2`/`$3` directly to VRAM.
- **mem_write_hook `&H68D0` (DSPMD watch)**: watches writes to DSPMD and enables the two call_hooks
  above only in normal display mode (`DSPMD == 0`), disabling them (e.g. in MENU mode, `DSPMD == 3`)
  so the ROM's original behaviour is restored.

`register(system)` calls `system.register_mem_write_hook(0x68D0, ...)`, making this a real-world
usage example of the memory write hook API described in dev_guide.md §6.1.

---

## Revision History

| Date | Change |
| --- | --- |
| 2026-05-14 | Initial version |
| 2026-05-14 | Removed single dispatch address; switched to direct call_hook registration per function |
| 2026-05-28 | Added `enable_call_hook` / `disable_call_hook` API |
| 2026-06-11 | Added `vram_loader.py` and `bank_loader.py` |
| 2026-07-04 | `vram_loader.py`: now calls `set_vdp_init_done(True)` after transfer so a direct colour-VRAM write is picked up by the renderer immediately |
| 2026-07-09 | Corrected discrepancies vs. implementation: noted `dht20.py`/`ram_test.py` are unshipped samples, documented `dotds_64dot.py` (a real mem_write_hook usage example), and updated the ext work area's C-direct description (`get_ext_work_view()`) |
