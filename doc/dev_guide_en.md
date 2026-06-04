# Development Guide

This guide is intended for developers who want to understand the internal architecture of the PB-1000 emulator or contribute to its development.

---

## 1. System Architecture

The emulator uses a hybrid C/Python architecture to balance performance and flexibility.

- **CPU Core (C)**: `hd61700.c` handles HD61700 instruction decoding, register management, and basic timing at maximum execution speed.
- **MicroPython Framework**: High-level system logic, peripheral emulation, and the main loop are implemented in MicroPython.
- **C-Module Bridge**: Custom MicroPython C modules expose CPU control and peripheral functions to Python.

---

## 2. Directory Structure

```text
src/
  hd61700.c / .h          # CPU emulation core
  modhd61700.c            # hd61700 MicroPython module
  lcd_controller.c / .h   # C-accelerated LCD rendering
  modlcd_controller.c     # lcd_c MicroPython module
  usb_host_core.c / .h    # USB host driver core
  modusb_host.c           # usb_host MicroPython module
  micropython.cmake       # Build system configuration

mp/
  main.py                 # Entry point
  pb1000.py               # PB1000System class
  lcd_controller_c.py     # Python wrapper for lcd_c module
  main_boot.py            # Boot and initialisation
  main_input.py           # Input managers (keyboard, touch, joystick, cursor repeat)
  main_runtime.py         # CPU execution loop helpers
  main_actions.py         # Screenshots, save-state, disk swap
  main_cleanup.py         # Shutdown and memory dump
  emulator_menu.py        # Win+F7 runtime menu
  funckey_bar.py          # On-screen function key bar
  boot_session.py         # Profile selection UI
  config.py               # pb1000.ini loading
  pio_uart.py             # PIO software UART (RS-232C)
  keymap.py / keymap.json # Keyboard mapping tables
  ili9341.py              # ILI9341 TFT driver
  ext/                    # Extension API modules (auto-loaded)

hardware/
  pb1000_emulator.kicad_sch
```

---

## 3. C Module Reference

### `hd61700` module (`src/modhd61700.c`)

The central module for CPU control and all peripheral I/O.

| Function | Description |
| --- | --- |
| `reset(debug)` | Reset the CPU (optionally enable debug flags) |
| `execute(cycles, stop_pc)` | Execute up to `cycles` CPU cycles |
| `get_pc()` / `set_pc(addr)` | Read/write the program counter |
| `get_reg(idx)` / `set_reg(idx, val)` | Read/write a 16-bit register |
| `get_reg8(idx)` / `set_reg8(idx, val)` | Read/write an 8-bit status register |
| `load_rom(data, slot)` | Load a ROM binary into the specified slot |
| `load_ram(data, slot)` | Load a RAM binary into the specified slot |
| `set_port_callbacks(read_fn, write_fn)` | Register port I/O callbacks |
| `set_mem_callbacks(read_fn, write_fn)` | Register memory access callbacks |
| `set_lcd_char_callback(fn)` | Register LCD character detection callback |
| `set_call_hook(addr, fn)` | Register a subroutine hook at an address |
| `clear_call_hook(addr)` | Remove a subroutine hook |
| `set_call_hook_enabled(addr, bool)` | Enable or disable a hook without removing it |
| `set_port_direct(tx, rx, beep, freq, duty)` | Initialise C-direct UART and beep PWM |
| `press_row_ki(row, ki)` | Assert a key in the keyboard matrix |
| `release_row_ki(row, ki)` | Release a key from the matrix (also sets post-release KEY_INT pulses) |
| `get_last_key()` | Return the last received HID scancode (read-and-clear) |
| `get_held_cursor_key()` | Return the physically-held cursor key scancode (0 = none) |
| `steer_next_key_int(row)` | Steer the next KEY_INT to a specific row and fire it immediately |
| `set_debug(bool)` | Enable/disable CPU instruction trace |
| `set_key_debug(bool)` | Enable/disable key input trace |
| `set_lcd_debug(bool)` | Enable/disable LCD write trace |

### `lcd_c` module (`src/modlcd_controller.c`)

Emulates the HD61830 LCD controller and drives the SPI display.

| Function | Description |
| --- | --- |
| `setup_display(spi, cs, dc, scale, x, y)` | Attach a physical SPI display |
| `render()` | Render to SPI if the dirty flag is set |
| `is_dirty()` / `mark_dirty()` / `clear_dirty()` | Manage the dirty flag |
| `get_vram()` | Return the current VRAM as bytes |
| `set_colors(fg, bg)` | Set lit/unlit pixel colours in RGB565 |
| `set_vdp_enable(bool)` | Enable/disable per-pixel colour VRAM (VDP) |
| `set_scale(num, den)` | Set the display scale factor |

Use the `LCDControllerC` wrapper class in `lcd_controller_c.py` in normal code.

### `usb_host` module (`src/modusb_host.c`)

Exposes RP2350 USB host (TinyUSB HID) to MicroPython.

- `process_usb_key(hid_report)`: parse an HID report and inject key events into the C core.
- Used together with `hd61700.keyboard_config_adv()` / `keyboard_config_base()`.

---

## 4. CPU Core Details

### Memory Mapping

The `hd61700` module supports two memory modes:

1. **C-Managed (Default)**: Static C buffers (`rom0_buf`, `ram_buf`, `bank*_buf`) are accessed directly by the CPU core. Best performance.
2. **Python-Managed (Debug)**: Every memory access triggers a Python callback. Useful for debugging but much slower.

See `doc/memory_map.md` for the full address layout.

### Peripheral I/O

- **Port I/O**: HD61700 P0–P7 ports are mapped to Python callbacks via `set_port_callbacks()`. C-direct mode uses `set_port_direct()` for UART TX and beep PWM.
- **LCD (HD61830)**: Emulated by the `lcd_c` C module.
- **SIO MMIO (0x0C00–0x0C03)**: Bridge between the PB-1000's SIO registers and the PIO UART.

---

## 5. SD Card Profile System

### Profile Directory

Each subdirectory of `/sd/rams/` is one profile. `boot_session.scan_profiles()` enumerates them at startup.

```text
/sd/rams/
  default/
    pb1000.ini    # Profile-specific settings (optional)
    rom0.bin      # Profile-specific ROM (optional; global ROM used otherwise)
    ram0.bin      # Saved standard RAM
    ram1.bin      # Saved expanded RAM1 (optional)
    regs.json     # Saved CPU registers
```

The same `/sd/rams/` directory is also used by the emulator menu RAM Save/Load feature.

### Configuration Merge

Files are loaded in order: flash `/pb1000.ini` → `/sd/pb1000.ini` → `<profile>/pb1000.ini`. Later entries override earlier ones. `config.py`'s `load_config(profile_dir)` returns the merged dict.

### File Search Priority

`_get_storage_path()` searches in this order:

1. `profile_dir/` (if specified)
2. `/sd/`
3. `/roms/`
4. `/` (root)

---

## 6. Subroutine Hook (Call Hook) Feature

Intercepts any address reachable from BASIC's `CALL` statement (internally push+JP) and calls a Python or C-native function instead.

### How It Works

A PC check is inserted at the top of the `hd61700_execute()` loop (before instruction fetch). When the PC matches a registered address:

1. The Python or C-native function is called (no arguments).
2. The return address is popped from the stack (×2) and incremented by 1 (word-address carry); the PC is set to that value (simulating RTN).
3. 15 cycles are consumed and execution continues.

### Python API

```python
# Register a hook (enabled by default)
system.register_call_hook(0x5E20, my_handler)

# Remove a hook
system.unregister_call_hook(0x5E20)

# Temporarily disable / re-enable without removing
system.disable_call_hook(0x5E20)
system.enable_call_hook(0x5E20)
```

### C-Native Hooks

Hooks defined as `MP_DEFINE_CONST_FUN_OBJ_0` run at native C speed:

```c
STATIC mp_obj_t hook_my_func(void) {
    /* read/write registers via hd61700 API */
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(hook_my_func_obj, hook_my_func);
```

See `doc/extension_api.md` for further details.

---

## 7. Serial Console (LCD Character Detection)

LCD VRAM writes are intercepted by `c_lcd_direct_write()`. When 6 columns of pixels are accumulated they are matched against `charset.bin` (0x20–0x7E) to identify the character code.

```text
CPU writes to LCD VRAM
  → c_lcd_direct_write() accumulates 6 pixel columns
  → cdet_match_charset() matches against charset.bin
  → py_lcd_char_cb (Python callback) is called
  → PB1000System._on_lcd_char_output(code)
  → console_uart.write(bytes([code]))   ← output over UART
```

Space characters (0x20) have an all-zero glyph identical to a blank LCD area. A per-row `_cdet_row_has_text` flag prevents blank areas from being misidentified as spaces.

---

## 8. Colour Display (VDP)

Enabling the per-pixel colour VRAM in `lcd_c` switches the display from monochrome to arbitrary per-pixel colour.

```python
system.lcd.set_vdp_enable(True)   # enable colour VRAM
system.lcd.set_vdp_enable(False)  # revert to global colour settings
```

MMIO addresses 0x0C20–0x0C24 allow BASIC programs or machine code to write colour VRAM directly (see `doc/memory_map.md`).

The **Color VRAM** entry in the emulator menu toggles this feature at runtime.

### Global LCD Colours

The lit and unlit pixel colours can also be configured globally without using per-pixel VDP mode.
`set_colors(fg_rgb565, bg_rgb565)` on the `lcd_c` module accepts RGB565 values.
The `[display]` section in `pb1000.ini` stores these as RGB332:

| INI key | Format | Default | Description |
| --- | --- | --- | --- |
| `fg_color` | RGB332 (0–255) | `0` (black) | Colour of lit pixels |
| `bg_color` | RGB332 (0–255) | `180` (0xB4, bluish grey) | Colour of unlit pixels |

`main_boot.py` converts RGB332 → RGB565 at startup and applies the values via `system.lcd.set_colors()`.

---

## 9. Cursor Key Auto-Repeat (`CursorRepeatManager`)

`mp/main_input.py` implements automatic cursor key repeat for PB-1000 emulation.

### How It Works

The PB-1000 ROM KEY_INT ISR uses edge detection: it processes a cursor key only when the key transitions from absent to present in the matrix scan. Holding a key does not produce repeat events.

The `CursorRepeatManager` synthesises a release/re-press cycle against the ISR:

```text
ARMED (key held for 400 ms)
  → FIRE: release_key() + steer_next_key_int(row)
  → RELEASE phase (175 ms): ROM scans the cursor row and sees KY = 0
  → press_key() + steer_next_key_int(row)
  → PRESS phase (100 ms): ROM scans and sees the cursor key
  → release_key() + steer_next_key_int(row)
  → cycle repeats
```

The ISR alternates between scanning row 0 and the cursor key row at 25 ms intervals. Clearing the ROM's hold state requires approximately 7 consecutive empty scans of the cursor row, hence `_RELEASE_MS = 175 ms`.

### Timing Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `_DELAY_MS` | 400 ms | Delay before first repeat fires |
| `_RELEASE_MS` | 175 ms | Duration key is absent from the matrix |
| `_INTERVAL_MS` | 100 ms | Duration key is present in the matrix |

### Required C APIs

| API | Description |
| --- | --- |
| `hd61700.get_held_cursor_key()` | Returns the scancode of the physically-held cursor key |
| `hd61700.steer_next_key_int(row)` | Sets `c_kb_ia_select` to the given row and fires KEY_INT immediately |
| `hd61700.release_row_ki(row, ki)` | Clears the matrix bit and sets `c_kb_post_release_pulses_remaining` |

---

## 10. Extension API (`ext/` Modules)

Allows BASIC programs to call Pico 2 peripherals (I2C, SPI, WiFi, etc.) via the `CALL` instruction.

- Place a module in `mp/ext/` (on the device: `/ext/` or `/sd/ext/`).
- If the module defines `register(system)`, it is called automatically on startup.
- Parameters and results are exchanged via the extension work area (0x5F00–0x5FFF).

See `doc/extension_api.md` for the full specification.

---

## 11. PIO UART (RS-232C)

A software UART implemented with RP2350 PIO state machines emulates the PB-1000's RS-232C port.

- Default pins: GP6 (TX) / GP13 (RX)
- Baud rate: configured via `[pio_uart] baudrate` in `pb1000.ini` (default 9600 bps)
- `pio_uart.py` contains the `PioUart` class
- `service_pio_uart_bridge()` in `main_runtime.py` bridges the SIO MMIO (0x0C00–0x0C03) and `PioUart` in the main loop

---

## 12. Debugging and Tracing

### C-Side Debugging

Enable verbose tracing from Python:

```python
import hd61700
hd61700.set_debug(True)     # CPU instruction trace
hd61700.set_key_debug(True) # Key input trace
hd61700.set_lcd_debug(True) # LCD write trace
```

**Warning**: full CPU tracing generates very high UART output volume. Use only for short test sequences; otherwise the UART buffer will stall the emulator.

### Wake Trace

`mp/main_diag.py` provides wake-path diagnostic helpers. It is independent of the normal execution flow.

---

## 13. Build System

`src/micropython.cmake` is included as a `USER_C_MODULES` target in the MicroPython build.
See `doc/build_guide.md` for the complete build procedure.

---

## 14. UTF-8 Conventions

All text files in this project use UTF-8 encoding.

- Markdown, Python source files, and configuration files: UTF-8
- Python source: UTF-8 without BOM (recommended)
- When saving from Windows PowerShell, verify no encoding issues occur

### Recommended File Handling in Python

```python
from pathlib import Path

text = Path("doc/example.md").read_text(encoding="utf-8")
Path("doc/example.md").write_text(text, encoding="utf-8")
```

- PowerShell `Set-Content` may be affected by BOM handling or the active code page
- To strip a BOM from an existing file: read with `utf-8-sig`, write back with `utf-8`
