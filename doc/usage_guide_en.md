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
| **Win (GUI) + Esc** | Quit Emulator | Return to MicroPython REPL |
| **Esc** | BREAK | Stop a BASIC program / clear error |
| **Enter** | EXE | Execute command |
| **Backspace** | BS | Erase character |
| **Insert** | INS | Toggle insert mode |
| **Arrow keys** | Cursor move | ↑↓←→ (auto-repeat when held) |
| **Alt (L/R)** | Shift | PB-1000 Shift key |
| **F1 – F4** | T13 – T16 | Function keys |

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

### Touch Panel (T1–T16)

The PB-1000's 16-key touch panel is emulated via the LCD touchscreen.

- Tapping the LCD display area (192×32 pixel region) fires the corresponding touch key (T1–T16).
- The LCD area is divided into a 4×4 grid for touch coordinate mapping.

### FuncKeyBar

A bar showing LCKEY, MENU, CAL and CALC keys is permanently displayed at the bottom of the screen. Tapping the bar fires the corresponding key.

---

## 5. Emulator Menu (Win + F7)

Opens a settings menu that pauses the main loop. Changes take effect immediately.

| Item | Function |
| :--- | :--- |
| **Serial Console** | Toggle LCD character detection → UART output |
| **RS-232C (PIO)** | Toggle PIO UART (virtual RS-232C) |
| **vFDD** | Toggle virtual floppy drive |
| **Beep** | Toggle beep sound (mute/unmute) |
| **Joystick** | Toggle joystick input |
| **Color VRAM** | Toggle per-pixel colour display (VDP) |
| **FD Swap** | Switch the virtual FDD disk image |
| **RAM Save** | Snapshot current RAM to `/sd/rams/` |
| **RAM Load** | Restore a RAM snapshot from `/sd/rams/` |
| **VRAM Save** | Save current LCD VRAM to files (PBM + binary) |
| **Foreground Color** | Change the colour of lit LCD pixels (RGB332) |
| **Background Color** | Change the colour of unlit LCD pixels (RGB332) |

Use Up/Down keys to navigate, Enter/EXE to activate, BREAK to close.

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
- After a successful RAM Load, a reset and boot sequence run automatically.

### Auto-Load

On startup, the emulator loads the state files from the selected profile directory automatically. If no files are present, a cold boot is performed.

---

## 9. Screenshots

Press **PrintScreen** to capture the current LCD content.

- Format: PBM (Portable BitMap, 1-bit monochrome)
- Location: `/sd/screenshots/screenshot_YYYYMMDD_HHMMSS.pbm`
  - Falls back to `/roms/` if the SD card is not mounted
- A raw VRAM dump (`vram_dump_....bin`) is saved at the same time

---

## 10. Configuration File (pb1000.ini)

Behaviour can be customised with an INI configuration file.

```ini
[keyboard]
enable_uart_kbd = true
uart_baudrate   = 115200
uart_tx_pin     = 4
uart_rx_pin     = 5

[emulator]
frame_interval_ms  = 33     ; display refresh interval (ms)
active_step_count  = 12000  ; CPU steps per execution slice

[disk]
enabled  = true
path     = /sd/disks/disk1.img

[profile]
default_profile = default
ui_timeout_ms   = 30000    ; profile selection timeout (ms)

[joystick]
enable = true

[beep]
enable   = true
gpio_pin = 14
freq_hz  = 4470
duty     = 30

[pio_uart]
baudrate = 9600

[display]
fg_color = 0               ; foreground (lit pixel) colour, RGB332 format 0–255
bg_color = 180             ; background (unlit pixel) colour, RGB332 format 0–255
```

**RGB332 Colour Format (`[display]` section)**

`fg_color` / `bg_color` use the same **RGB332 (8-bit)** format as the colour VRAM.
Values can be changed interactively from **Foreground Color** / **Background Color** in the emulator menu and are written back to `pb1000.ini` automatically.

| Bits | 7–5 | 4–2 | 1–0 |
| --- | --- | --- | --- |
| Content | R (3 bit) | G (3 bit) | B (2 bit) |

Typical values: `0` = black, `255` = white, `180` (0xB4) = bluish grey, `7` = blue
