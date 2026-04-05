# Usage Guide

This guide explains how to set up, start, and operate the PB-1000 emulator.

## 1. Initial Setup

### ROM Images
The emulator requires the original Casio PB-1000 ROM images to function. These are not provided with the project.
- `rom0.bin`: Internal ROM (6KB, address 0x0000-0x17FF)
- `rom1.bin`: External ROM (32KB, address 0x8000-0xFFFF)

Place these files in the `/roms/` directory on the Pico's internal flash or in the root of your SD card.

### Directory Structure (SD Card/Flash)
```text
/
├── main.py             # Entry point
├── roms/               # Fallback ROM/RAM storage
│   ├── rom0.bin
│   └── rom1.bin
└── sd/                 # Recommended storage (if SD card is used)
    ├── rom0.bin        # Overrides /roms/
    ├── rom1.bin
    ├── ram0.bin        # Saved RAM state
    ├── regs.json       # Saved Registers state
    └── screenshots/    # Captured PBM images
```

## 2. Starting the Emulator

1.  Connect your ILI9341 display and USB keyboard (via OTG).
2.  Power on the Pico 2.
3.  If `main.py` is correctly uploaded, the PB-1000 "bezel" will appear on the display, and the emulator will start.
4.  The system will attempt to restore the previous state (`ram0.bin`, `regs.json`). If not found, it performs a cold boot.

## 3. Keyboard Operation

### Special Function Keys
| PC Key | PB-1000 Function | Note |
| :--- | :--- | :--- |
| **F9** | **Reset** | Hardware reset (PC=0x0001) |
| **F11** | **Save State** | Manually save RAM and Registers |
| **PrintScreen** | **Screenshot** | Save current LCD content to SD as `.pbm` |
| **Esc / Ctrl+C** | **BREAK** | Stop BASIC program / Clear error |
| **Enter** | **EXE** | Execute command |
| **Backspace** | **BS** | Erase character |
| **Insert** | **INS** | Insert mode |
| **Arrows** | **Cursors** | Move cursor |
| **F1** - **F4** | **T1** - **T4** | Function keys (T1-T4) |
| **Alt** (L/R) | **Shift** | PB-1000 Shift key |

### Key Mapping Details
The emulator maps standard HID keyboard scancodes to the PB-1000 matrix.
- Alphabets and Numbers: Directly mapped.
- Symbols: Mapped to their PB-1000 equivalents (e.g., `Shift + 2` on PC -> `"` on PB-1000).
- **MENU**, **CAL**, **LCKEY**: Mapped to `F5`, `F6`, `F7` respectively (see `keymap.py` for exact assignments).

## 4. Touch Interface

The PB-1000's famous 16-key touch panel (T1-T16) is emulated via the LCD's touch screen.
- Tap the LCD area to trigger the corresponding touch key (T1-T16).
- The 192x32 LCD area is divided into a 4x4 grid for touch.

## 5. State Management

The emulator automatically saves its state when you press **F11**.
- **RAM**: `ram0.bin` (Internal 8KB) and `ram1.bin` (Expanded 32KB if enabled).
- **Registers**: `regs.json` stores the CPU program counter, flags, and general-purpose registers.
- **Auto-Load**: On startup, the emulator looks for these files on the SD card (or flash) and restores them to continue your session.

## 6. Screenshots

Pressing the **PrintScreen** key captures the current LCD content.
- Format: PBM (Portable BitMap, 1-bit black and white).
- Location: `/sd/screenshots/screenshot_YYYYMMDD_HHMMSS.pbm`.
- A raw VRAM dump (`vram_dump_....bin`) is also saved for debugging.
