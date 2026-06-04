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
    ├── __init__.py   # empty file (package declaration)
    ├── dht20.py      # DHT20 temperature/humidity sensor (included)
    └── myext.py      # add your own extensions here
```

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

The C core does not assign 0x5F00–0x5FFF to any static buffer, so accesses to this range automatically fall through to the Python callbacks (`_mem_read_impl` / `_mem_write`).

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

## Revision History

| Date | Change |
| --- | --- |
| 2026-05-14 | Initial version |
| 2026-05-14 | Removed single dispatch address; switched to direct call_hook registration per function |
| 2026-05-28 | Added `enable_call_hook` / `disable_call_hook` API |
