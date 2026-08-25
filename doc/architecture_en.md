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
- Special key handling (NumLock / GUI+F7)
- Exception handling and shutdown cleanup

Not responsible for:

- Input implementation details
- USB / PIO / UART initialisation internals
- Screenshot saving
- Save-state implementation details

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

### `mp/main_input_keyboard.py` / `_touch.py` / `_joystick.py` / `_cursor.py`

Used to be one file, `main_input.py` (535 lines), until real hardware showed compiling all of
it at once -- at main.py's Step 8b, where all four managers are constructed right after
`load_state()`, the roomiest point boot ever reaches -- could still raise `MemoryError`. Split
into four files by purpose instead; `main.py` imports each separately with its own
`gc.collect()` first (`main_input_joystick.py` only when `[joystick] enable=true`).

Responsibilities:

- UART keyboard input reception
- Input queue management
- Key press/release timing control
- BRK / ON_INT control during CPU sleep
- Touch panel input → PB-1000 key mapping
- Joystick input → PB-1000 key mapping
- Cursor key auto-repeat (release/re-press synthesis)

Exported classes:

- `KeyboardInputManager` (`main_input_keyboard.py`): UART keyboard input
- `TouchInputManager` (`main_input_touch.py`): touch panel input
- `JoystickInputManager` (`main_input_joystick.py`): joystick input (default GP18–21/26/27)
- `CursorRepeatManager` (`main_input_cursor.py`): cursor key auto-repeat (synthesises release/press cycles against the ROM's KEY_INT ISR)

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

### `mp/main_cleanup.py`

Responsibilities:

- Work area output on exit
- Memory dump output

---

### `mp/emulator_menu.py`

Responsibilities:

- Runtime settings menu launched by Win+F7
- CPU stepping is implicitly paused while the menu is open
- Toggles is Beep-only now (everything else unified into `pb1000.ini`/the F1 setup menu,
  2026-08-22). Runtime actions needing to take effect immediately: FD Swap, RAM Save, VRAM Save,
  Full Capture, colour settings, Reset, Reboot, NEW ALL
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

### `mp/display_init.py`

Responsibilities:

- Boot-time bring-up of the LCD (ILI9341/ST7796), SD card, and touch panel (`init_display()`).
- Split out of `pb1000.py` because this stage runs entirely before the profile picker, where
  `PB1000System` isn't needed at all -- keeping the 2000+ line `pb1000.py` uncompiled and
  non-resident here eases heap pressure at exactly the point (the profile picker / F1 setup
  menu) where it's tightest. See the comment at the top of `mp/main_boot.py` for the history.

---

### `mp/pb1000.py`

Responsibilities:

- `PB1000System` class (board-level emulation coordinator). Mixes in
  `PB1000FddMixin` (`pb1000_fdd.py`) and `PB1000StateIOMixin`
  (`pb1000_state_io.py`) via multiple inheritance (split 2026-08-22 to fix a
  compile-time heap-fragmentation MemoryError -- see those files below)
- Memory map management (ROM / RAM / bank switching / extension work area)
- Port I/O and MMIO callbacks
- Beep (PWM) control
- Subroutine hook registration (`register_call_hook` / `unregister_call_hook` / `enable_call_hook` / `disable_call_hook`)
- Extension API loading (`_ext_load_modules`)
- Display update (`update_display` / `force_full_redraw`)

Serial Console (LCD character detection → GP4/GP5 UART output) was removed on 2026-08-22. The
detection pipeline itself still lives in the C core, but its only Python-side caller (the
`console_uart` property setter) is gone, so it's now permanently inert (see `dev_guide_en.md` §7).

---

### `mp/pb1000_fdd.py`

Responsibilities:

- `PB1000FddMixin` (mixed into `PB1000System` in `pb1000.py` via multiple inheritance)
- Virtual FDD (MD-100) control (`_handle_virtual_fdd_port_write`, `configure_virtual_fdd`,
  `swap_disk`, `discover_virtual_fdd_config`, etc.)
- Storage-path resolution helpers (`_get_storage_path`, etc.)

Split out because `pb1000.py` alone (formerly 1943 lines) hit a compile-time MemoryError on real
hardware.

---

### `mp/pb1000_state_io.py`

Responsibilities:

- `PB1000StateIOMixin` (mixed into `PB1000System` in `pb1000.py` via multiple inheritance)
- Save-state / load-state (`save_state` / `load_state`)
- CALL/memory-write hook registry (`register_call_hook`, etc.)

Split out for the same reason as `pb1000_fdd.py`.

---

### `mp/pio_uart.py`

Responsibilities:

- Software UART using RP2350 PIO state machines (virtual RS-232C port)
- Baud rate configured via `[rs232c] baudrate` in `pb1000.ini` (default 9600 bps)

---

## Dependency Direction

```text
main.py
  -> main_boot.py
  -> main_input_keyboard.py / _touch.py / _cursor.py (Step 8b, own gc.collect() each)
  -> main_input_joystick.py (Step 8b, same, only when [joystick] enable=true)
  -> main_runtime.py
  -> main_actions.py
  -> main_cleanup.py
  -> emulator_menu.py   (lazy import, only when Win+F7 pressed)

main_boot.py
  -> display_init.py     (module-level import -- needed before the profile picker)
  -> pb1000.py            (lazy import, inside create_system() -- after the picker)
     -> pb1000_fdd.py / pb1000_state_io.py (module-level, mixed into PB1000System)
  -> pio_uart.py          (lazy import, inside initialize_usb_host_and_pio())
  -> hd61700 / usb_host / keymap
  -> boot_session.py
  -> config.py

main_input_keyboard.py / _touch.py / _joystick.py / _cursor.py
  -> system object API
  -> keymap.py (main_input_joystick.py, for named-constant key names)
  -> hd61700 (main_input_cursor.py: get_held_cursor_key()/steer_next_key_int())
  -> machine.Pin (main_input_joystick.py)

main_runtime.py
  -> system object API
  -> hd61700 CPU core API

main_actions.py
  -> system object API
  -> hd61700 / usb_host / keymap

emulator_menu.py
  -> system object API
  -> funckey_bar.py
  -> main_actions.py (disk swap)

pb1000.py
  -> pb1000_fdd.py / pb1000_state_io.py (module-level, mixed in via multiple inheritance)
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
    - Special key handling (NumLock=reset / GUI+F7=emulator menu, disk swap now lives under that menu's "FD Swap" item)
    - Keyboard / touch / joystick / cursor-repeat input
    - Status handling / screenshots / save-state
    - Frame update
    - Timer tick processing
11. On exit, `main_cleanup.dump_shutdown_state()` writes a state dump.

---

## Remaining Architectural Tasks

1. `_create_input_managers()` stays in `main.py` so the normal-boot input configuration is easy
   to find at the entry point.
2. Debug-only alternate entry points should only be re-created, minimally, once the normal-path
   structure has settled.
3. If the number of helper modules keeps growing, consider migrating to a package layout like
   `mp/runtime/`, `mp/input/`, `mp/actions/`.

---

## Suggested Long-Term Package Layout

`main_input.py` has already been split into `main_input_keyboard.py`/`_touch.py`/`_cursor.py`/
`_joystick.py`, `pb1000.py` into `pb1000_fdd.py`/`pb1000_state_io.py`, and
`emulator_menu_ext.py` into `emulator_menu_ram.py`/`_capture.py`/`_debug.py` (see "Current
Module Split" above for details). A larger-scale option not yet done would be regrouping these
feature-split files into directory-level packages:

```text
mp/
  main.py
  config.py
  lcd_controller_c.py
  pio_uart.py

  boot/       # main_boot.py, boot_session.py, display_init.py
  input/      # main_input_*.py
  runtime/    # main_runtime.py
  actions/    # main_actions.py, main_cleanup.py
  pb1000/     # pb1000.py, pb1000_fdd.py, pb1000_state_io.py
  menu/       # emulator_menu*.py, funckey_bar.py
  ext/        # Extension API modules
```
