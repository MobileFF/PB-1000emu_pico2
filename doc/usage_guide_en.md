# Usage Guide

This guide explains how to set up, start, and operate the PB-1000 emulator.

---

## 1. Initial Setup

### ROM Images

The emulator requires the original Casio PB-1000 ROM images to function. These are **not** provided with the project.

- `rom0.bin`: Internal ROM (6 KB, addresses 0x0000–0x17FF)
- `rom1.bin`: System ROM (32 KB, addresses 0x8000–0xFFFF, Bank 0)
- `charset.bin` (placed under `/roms/`, optional): used by the Serial Console feature (§6) for character recognition. If missing, no error occurs — character detection simply does not run

### Directory Structure (SD Card / Flash)

```text
/                          # Pico internal flash root
├── main.py                # Entry point
├── pb1000.ini             # Global settings (optional)
└── roms/                  # Fallback ROM/RAM storage
    ├── rom0.bin
    └── rom1.bin

/sd/                       # SD card (recommended)
├── pb1000.ini             # Global settings override (optional)
├── rams/                  # Profile directories (boot selection & RAM save/load)
│   ├── default/           # "default" profile
│   │   ├── pb1000.ini     # Profile-specific settings (optional)
│   │   ├── rom0.bin       # Profile-specific ROM (optional)
│   │   ├── ram0.bin       # Saved standard RAM
│   │   ├── ram1.bin       # Saved expanded RAM1 (optional)
│   │   ├── ram2.bin       # Saved expanded RAM2 (Bank2, optional)
│   │   ├── ram3.bin       # Saved expanded RAM3 (Bank3, optional)
│   │   └── regs.json      # Saved CPU registers
│   └── bench/             # Additional profile example
├── disks/                 # Virtual FDD disk images
│   └── disk1.img
└── screenshots/           # Screenshot output
```

Configuration priority (low → high): flash `/pb1000.ini` → `/sd/pb1000.ini` → `<profile>/pb1000.ini`

---

## 2. Boot Flow

1. Power on the Pico 2.
2. The **profile selection UI** appears, listing subdirectories found under `/sd/rams/`.
   - Use Up/Down arrow keys or touch to select a profile, then press Enter/EXE to confirm.
   - After a 30-second timeout the `default_profile` value is selected automatically.
3. The selected profile's saved state is loaded automatically, and the PB-1000 LCD bezel appears.
4. If no state files are found, a cold boot (fresh start) is performed.

---

## 3. Keyboard Operation

### Special Function Keys

| PC Key | Function | Notes |
| :--- | :--- | :--- |
| **NumLock** | Reset | Hardware reset (PC = 0x0000) |
| **F11** | Save State | Save RAM and registers to the current profile |
| **PrintScreen** | Screenshot | Save LCD content to `/sd/screenshots/` as `.pbm` |
| **Win (GUI) + F7** | Emulator Menu | Open the runtime settings menu |
| **Win (GUI) + F6** | Disk Swap | Switch the virtual FDD disk image |
| **Win (GUI) + Esc** | Quit Emulator | Return to MicroPython REPL (via the UART REPL wired to GP0/GP1 — see the [Hardware Guide](hardware_guide_en.md)) |
| **Esc** | BREAK | Stop a BASIC program / clear error |
| **Enter** | EXE | Execute command |
| **Backspace** | BS | Erase character |
| **Insert** | INS | Toggle insert mode |
| **Arrow keys** | Cursor move | ↑↓←→ (auto-repeat when held) |
| **Alt (L/R)** | Shift | PB-1000 Shift key |
| **F1 – F4** | TK13 – TK16 | Function keys |

### Key Mapping

The emulator maps USB HID scancodes to the PB-1000 13×12 key matrix.

- Letters and digits: direct mapping
- Symbols: mapped to PB-1000 equivalents (e.g. PC `Shift+2` → PB-1000 `"`)
- **LCKEY / MENU / CAL**: mapped to F5 / F6 / F7 respectively
- Full mapping tables are in `mp/keymap.py` and `mp/keymap.json`

### Cursor Key Auto-Repeat

Holding an arrow key triggers automatic cursor movement after an initial delay of approximately 400 ms. The emulator synthesises release/re-press cycles against the ROM's KEY_INT ISR to produce the repeat effect.

---

## 4. Touch Interface and FuncKeyBar

### Touch Panel (TK1–TK16)

The PB-1000's 16-key touch panel is emulated via the LCD touchscreen.

- Tapping the LCD display area (192×32 pixel region) fires the corresponding touch key (TK1–TK16).
- The LCD area is divided into a 4×4 grid for touch coordinate mapping.
- This hit-test area always uses the physical touch pad's fixed height (32 dots × `scale`),
  regardless of `[display] lcd_height = 64` (extended mode) — the real hardware has no touch
  pad covering the extended rows, so the hit-test area does not grow with it.

### FuncKeyBar

A bar showing LCKEY, MENU, CAL and CALC keys is permanently displayed at the bottom of the screen. Tapping the bar fires the corresponding key. Its on-screen position is computed automatically from `[display] lcd_height` and `scale`.

### Touch Panel Calibration (XPT2046)

The XPT2046 touch controller chip may need axis swap/invert and pixel-offset tuning per LCD
module, since the touch film is mounted differently on each panel. Adjust these in the
`[touch]` section of `pb1000.ini`.

- `swap_xy` / `x_inv` / `y_inv`: swap the X/Y touch axes and/or invert each axis. Built-in
  defaults are `swap_xy=true, x_inv=false, y_inv=false` for ILI9341 and
  `swap_xy=true, x_inv=true, y_inv=true` for ST7796. These are read only from the
  internal-flash `/pb1000.ini` (and `/roms/pb1000.ini`) at boot, before the SD card is
  mounted, so SD-card or per-profile ini files cannot override them.
- `x_offset` / `y_offset` (for the LCD touch area) and `funckey_x_offset` /
  `funckey_y_offset` (for the FuncKeyBar): pixel-space coordinate correction. These can be
  overridden from the SD card or a per-profile ini as well.
- Settings for ILI9341 and ST7796 can coexist in the same `[touch]` section: prefix a key
  with `ili9341.` or `st7796.` to scope it to whichever driver is active via
  `[display] driver` (e.g. `st7796.y_offset = -4`). An unprefixed key applies to either
  driver unless overridden by a driver-scoped one.
- See the comments in `pb1000.ini` for each item's built-in default and example usage.

---

## 5. Emulator Menu (Win + F7)

Opens a settings menu that pauses the main loop. Changes take effect immediately. It's organized
as a hierarchy of four categories — Toggles / Storage / Display / System — see
`emulator_menu_guide_en.md` for the full item-by-item reference and usage notes.

---

## 6. Serial Features

### Serial Console

Detects characters on the PB-1000 LCD and outputs them in real time over the console UART (GP4/GP5).

- The character set (`charset.bin`) is matched against the LCD VRAM to identify character codes.
- A newline (CRLF) is output at the end of each screen row.
- Toggle with **Serial Console** in the emulator menu.

### RS-232C (PIO UART)

Emulates the PB-1000's RS-232C interface using a PIO software UART (default GP6 TX / GP13 RX).

- Connected to MMIO addresses 0x0C00–0x0C03 (SIO registers).
- Baud rate is configured via `[pio_uart] baudrate` in `pb1000.ini` (default 9600 bps).
- Toggle with **RS-232C (PIO)** in the emulator menu.
- Receiving an EOF byte (0x1A) automatically issues a BREAK.

---

## 7. Joystick

Supports a direct-wired joystick (active-LOW with PULL_UP inputs).

| Button | Default Pin | Default PB-1000 Key |
| :--- | :--- | :--- |
| UP | GP18 | Cursor Up |
| DOWN | GP19 | Cursor Down |
| LEFT | GP20 | Cursor Left |
| RIGHT | GP21 | Cursor Right |
| FIRE1 | GP26 | EXE |
| FIRE2 | GP27 | SHIFT |

- Key mapping can be changed in the `[joystick]` section of `pb1000.ini`.
- Toggle with **Joystick** in the emulator menu.
- Pin assignments can be changed by editing `JoystickInputManager.DEFAULT_PIN_MAP` in `mp/main_input.py`.

---

## 8. State Management

### Save State (F11)

Press **F11** to save the current session state to the active profile directory.

- `ram0.bin`: Standard RAM (8 KB)
- `ram1.bin`: Expanded RAM1 (when enabled)
- `ram2.bin` / `ram3.bin`: Expanded RAM2 / RAM3 (Bank2 / Bank3, when enabled)
- `regs.json`: CPU registers (PC, flags, general-purpose registers)

### RAM Save / Load (Emulator Menu)

**RAM Save** / **RAM Load** save and restore snapshot sets independently of the profile.

- Target directory: `/sd/rams/<folder name>/`
- **RAM Load does not reset the CPU — execution resumes from the exact PC/registers captured
  by RAM Save.** Since PC is not forced back to 0x0000, a game or program continues right
  where it left off.
- This full-state resume only happens when RAM Load is explicitly chosen from the EMULATOR
  MENU. Auto-Load (below) intentionally stays conservative in case a save turns out corrupt.

### Auto-Load

On startup, the emulator loads the state files from the selected profile directory
automatically. If no files are present, a cold boot is performed. This path only restores RAM
contents and always resets to PC=0x0000 (unlike the emulator menu's RAM Load), so an
unattended boot can never get stuck resuming a bad save with no way to reach the menu.

---

## 9. Screenshots

Press **PrintScreen**, or use **VRAM Save** in the emulator menu, to capture the emulated
PB-1000 screen (192×32 or 192×64, monochrome VRAM + colour VRAM). Both trigger the same
underlying routine (`_do_vram_save()`), so the result is identical either way.

- Location: `/sd/screenshots/` (falls back to `/screenshots/` if the SD card is not mounted)
- Files saved (each with a `YYYYMMDD_HHMMSS` timestamp suffix):
  - `vram_<timestamp>.bin` / `.pbm`: raw monochrome VRAM data / PBM image
  - `edtop_<timestamp>.bin`: raw EDTOP buffer data
  - `color_vram_<timestamp>.bin` / `.ppm`: colour VRAM (VDP) data / PPM image, only when VDP is enabled

To capture the **entire physical screen**, including the bezel and function key bar, use
**Full Capture** in the emulator menu instead (see below).

### Full-Screen Capture (Full Capture)

**Full Capture** in the emulator menu saves the entire physical display (e.g. 320×240 or
480×320) — bezel, PB-1000 screen, and function key bar included — as a single PPM image.

- Location: `/sd/screenshots/full_<timestamp>.ppm` (same directory as VRAM Save)
- This does not read the screen back over SPI; it recomposes the full image in software
  from the emulator's own VRAM, bezel layout, and function key bar image. As a result, any
  transient status message shown briefly after a keypress is not captured.

---

## 10. Configuration File (pb1000.ini)

Behaviour can be customised with an INI configuration file. For a complete
reference of every section and key (default values, whether it can be
overridden from the SD card or a per-profile ini, etc.), see
**[config_guide_en.md](config_guide_en.md)**.

```ini
[display]
driver   = ILI9341          ; ILI9341 (320x240) or ST7796 (480x320)
scale    = 1.5               ; on-screen magnification (ILI9341=1.5, ST7796=2.0 recommended)
lcd_height = 32              ; 32=original / 64=extended mode
rotation = 0                ; 0=normal / 180=upside down (match how the board is mounted)

[touch]
ili9341.y_offset = -10      ; driver-scoped key prefix (see §4 and config_guide_en.md §9)

[disk]
enabled  = true
path     = /sd/disks/disk1.img
```

See the comments in `pb1000.ini` and [config_guide_en.md](config_guide_en.md)
for what each key means, its default value, and usage examples.

| Bits | 7–5 | 4–2 | 1–0 |
| --- | --- | --- | --- |
| Content | R (3 bit) | G (3 bit) | B (2 bit) |

Typical values: `0` = black, `255` = white, `180` (0xB4) = bluish grey, `7` = blue
