# PB-1000 Emulator Architecture

## Purpose

This document describes the Python-side architecture of the PB-1000 emulator, clarifying the responsibility and dependencies of each module.

## Design Goals

- Restrict `main.py` to flow control only
- Separate input, boot, execution loop helpers, save/cleanup, and diagnostics into discrete responsibility modules
- Localise access to `PB1000System` and the CPU core as much as possible
- Isolate debug helpers from the normal execution flow
- Make it clear where future features should be added

---

## Current Module Split

### `mp/main.py`

Responsibilities:

- Execution flow entry point
- Boot sequence assembly
- Main loop dispatch to each helper
- Special key handling (NumLock / GUI+F6 / GUI+F7)
- Exception handling and shutdown cleanup

Not responsible for:

- Input implementation details
- USB / PIO / UART initialisation internals
- Screenshot saving
- Save-state implementation details
- Wake trace diagnostics

---

### `mp/main_boot.py`

Responsibilities:

- UART console initialisation
- Display and `PB1000System` initialisation
- Default ROM loading
- USB Host / PIO UART initialisation
- C keyboard mode configuration (including F11 callback registration)

Dependencies:

- `pb1000.py`
- `pio_uart.py`
- `usb_host`, `hd61700`, `keymap`
- `boot_session.py`
- `config.py`

---

### `mp/main_input.py`

Responsibilities:

- UART keyboard input reception
- Input queue management
- Key press/release timing control
- BRK / ON_INT control during CPU sleep
- Touch panel input → PB-1000 key mapping
- Joystick input → PB-1000 key mapping
- Cursor key auto-repeat (release/re-press synthesis)

Exported classes:

- `KeyboardInputManager`: UART keyboard input
- `TouchInputManager`: touch panel input
- `JoystickInputManager`: joystick input (default GP18–21/26/27)
- `CursorRepeatManager`: cursor key auto-repeat (synthesises release/press cycles against the ROM's KEY_INT ISR)

---

### `mp/main_runtime.py`

Responsibilities:

- PIO UART MMIO bridge
- CPU step execution helpers
- Frame update timing
- Timer tick management

Exported functions:

- `service_pio_uart_bridge()`
- `run_cpu_slice()`
- `update_frame_if_due()`
- `service_timer_realtime(system, last_tick_ms, *, ms_per_tick)`: wall-clock (`time.ticks_ms()`) based timer tick. This is the main timer path used whenever `timer_tick_ms > 0` (the default). Unlike the step-based timer, it keeps advancing even while the CPU is in SLP (sleep) state, so TIME$ no longer stalls.
- `service_timer_ticks()`: legacy step-count based timer. Only used as a fallback when `timer_tick_ms == 0` (e.g. debug trace scripts).

---

### `mp/main_actions.py`

Responsibilities:

- Screenshot save (PBM + VRAM dump) on PrintScreen
- VRAM dump output
- Save-state request handling
- Disk swap delegation

---

### `mp/main_diag.py`

Responsibilities:

- Wake trace snapshot generation
- Diagnostic string formatting
- Wake-path tracing

Notes:

- Largely independent of the normal execution flow; intended as a diagnostic helper only.

---

### `mp/main_cleanup.py`

Responsibilities:

- Work area output on exit
- Memory dump output

---

### `mp/emulator_menu.py`

Responsibilities:

- Runtime settings menu launched by Win+F7
- CPU stepping is implicitly paused while the menu is open
- Runtime toggling of: Serial Console, RS-232C, vFDD, beep, joystick, colour VDP, RAM save/load, VRAM save, foreground/background colour
- After closing, `system.force_full_redraw()` restores the bezel and LCD

---

### `mp/funckey_bar.py`

Responsibilities:

- Permanent on-screen touch bar for LCKEY / MENU / CAL / CALC keys
- Renders by blitting the `.fkbar.raw` sprite
- Hit-tests touch coordinates and fires the corresponding key

Exported class:

- `FuncKeyBar`

---

### `mp/boot_session.py`

Responsibilities:

- Scan and enumerate the `/sd/rams/` directory
- Display the profile selection UI (with timeout)
- Resolve profile directory paths

Exported functions:

- `scan_profiles()`
- `get_profile_dir(name)`
- `select_profile_ui(display, profiles, default, timeout_ms)`

---

### `mp/config.py`

Responsibilities:

- Load INI-format `pb1000.ini` files
- Section/key-level access via `get_bool()`, `get_int()`, `get_str()`
- Merge global and profile-specific configurations

---

### `mp/lcd_controller_c.py`

Responsibilities:

- Python wrapper for the `lcd_c` C extension module (`modlcd_controller.c`)
- Dirty flag management (`mark_dirty()` / `clear_dirty()` / `is_dirty()`)
- Synchronise scale and colour settings to the C side
- Toggle VDP (per-pixel colour VRAM) on/off
- Fallback path (Python rendering when SPI is not attached)

Exported class:

- `LCDControllerC`

---

### `mp/pb1000.py`

Responsibilities:

- `PB1000System` class (board-level emulation coordinator)
- Memory map management (ROM / RAM / bank switching / extension work area)
- Port I/O and MMIO callbacks
- Virtual FDD (`_handle_virtual_fdd_port_write`)
- Beep (PWM) control
- Save-state / load-state
- Serial console (LCD character detection → UART output: `_on_lcd_char_output`)
- Subroutine hook registration (`register_call_hook` / `unregister_call_hook` / `enable_call_hook` / `disable_call_hook`)
- Extension API loading (`_ext_load_modules`)
- Display update (`update_display` / `force_full_redraw`)

---

### `mp/pio_uart.py`

Responsibilities:

- Software UART using RP2350 PIO state machines (virtual RS-232C port)
- Baud rate configured via `[pio_uart] baudrate` in `pb1000.ini` (default 9600 bps)

---

## Dependency Direction

```text
main.py
  -> main_boot.py
  -> main_input.py
  -> main_runtime.py
  -> main_actions.py
  -> main_cleanup.py
  -> emulator_menu.py   (lazy import, only when Win+F7 pressed)

main_boot.py
  -> pb1000.py
  -> pio_uart.py
  -> hd61700 / usb_host / keymap
  -> boot_session.py
  -> config.py

main_input.py
  -> system object API

main_runtime.py
  -> system object API
  -> hd61700 CPU core API

main_actions.py
  -> system object API
  -> hd61700 / usb_host / keymap

main_diag.py
  -> system object API
  -> hd61700 CPU core API

emulator_menu.py
  -> system object API
  -> funckey_bar.py
  -> main_actions.py (disk swap)

pb1000.py
  -> lcd_controller_c.py (LCDControllerC)
  -> hd61700 (CPU core C module)
  -> lcd_c   (LCD controller C module)
```

---

## Runtime Flow

1. `main.py` loads configuration files and prepares the UART console.
2. `boot_session.select_profile_ui()` presents the profile selection screen.
3. `main_boot.init_display_only()` initialises the display.
4. `main_boot.create_system()` initialises `PB1000System`.
5. `main_boot.load_default_roms()` loads ROM binaries.
6. `main_boot.initialize_usb_host_and_pio()` prepares USB Host and PIO UART.
7. `main_boot.configure_c_keyboard()` configures the C-side keyboard handler (including F11 callback).
8. `FuncKeyBar` is drawn at the bottom of the screen.
9. `system.power_on()` starts the emulator.
10. The main loop executes in order:
    - PIO UART bridge
    - CPU execution slice
    - Special key handling (NumLock=reset / GUI+F6=disk swap / GUI+F7=emulator menu)
    - Keyboard / touch / joystick / cursor-repeat input
    - Status handling / screenshots / save-state
    - Frame update
    - Timer tick processing
11. On exit, `main_cleanup.dump_shutdown_state()` writes a state dump.

---

## Suggested Long-Term Package Layout

```text
mp/
  main.py
  main_boot.py
  main_input.py
  main_runtime.py
  main_actions.py
  main_diag.py
  main_cleanup.py
  emulator_menu.py
  funckey_bar.py
  boot_session.py
  config.py
  lcd_controller_c.py
  pb1000.py
  pio_uart.py

  # future option
  boot/
  input/
  runtime/
  actions/
  diag/
  ext/        # Extension API modules
```
