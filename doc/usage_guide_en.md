# Usage Guide

This guide explains how to set up, start, and operate the PB-1000 emulator.

---

## 1. Initial Setup

### ROM Images

The emulator requires the original Casio PB-1000 ROM images to function. These are **not** provided with the project.

- `rom0.bin`: Internal ROM (6 KB, addresses 0x0000–0x17FF)
- `rom1.bin`: System ROM (32 KB, addresses 0x8000–0xFFFF, Bank 0)
- `charset.bin` (placed under `/roms/`, optional): glyph data used to render character codes as actual pixels in DRAW_CHAR mode. If missing, no error occurs, but characters drawn via DRAW_CHAR render as blank

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
     If there are more profiles than fit on screen, it scrolls automatically.
   - After a 30-second timeout the `default_profile` value is selected automatically.
   - **F1**: opens the BIOS-style setup menu (see below).
   - **F12**: after a confirmation prompt, exits to the MicroPython REPL without booting (see
     below).
3. The selected profile's saved state is loaded automatically, and the PB-1000 LCD bezel appears.
4. If no state files are found, a cold boot (fresh start) is performed.

### BIOS-Style Setup Menu (F1)

Pressing **F1** on the profile selection screen opens a BIOS-style setup menu
(`mp/setup_menu.py`) that lets you edit `pb1000.ini` directly on the device's own screen. It
runs at the safest possible point — before any ROM/RAM loading or CPU startup — and is a
separate, independent menu from the EMULATOR MENU (Win+F7, only reachable while the emulator is
running).

1. **Pick a target file**: the internal flash's `/pb1000.ini`, the SD card's `/sd/pb1000.ini`
   (shown only if the SD card is mounted), or any RAM profile's `pb1000.ini`.
2. **Edit keys**: Up/Down selects a row, EXE edits it (bool keys toggle ON/OFF with EXE; enum
   keys cycle through choices with EXE or Left/Right; int/str keys open a numeric/text entry
   screen via EXE). BS clears that key's setting (unset = falls back to the default).
   - Only the more commonly-used keys are shown by default (toggle `Show ALL keys` to ON to see
     everything).
   - Flash-only keys — such as everything under `[hdmi]` (see [config_guide_en.md](config_guide_en.md)
     §1) — are hidden while editing an SD-card or per-profile ini, since they'd be ignored at
     load time anyway.
3. **Save & Exit (reset)**: writes the changes to the chosen ini file and, after a confirmation
   prompt, calls `machine.reset()` to reboot the device. The boot sequence that follows reads
   the new configuration from scratch.
4. **Discard & Back**: discards any changes and returns to the profile selection screen (a
   confirmation prompt appears if there are unsaved changes).

### F12: Exit to REPL (Development/Recovery)

Pressing **F12** on the profile selection screen brings up a confirmation prompt; choosing Yes
safely aborts `main.py`'s execution and returns to the MicroPython REPL (no ROM/RAM loading or
CPU startup happens at all).

This is a development/recovery shortcut. Since `main.py` auto-runs on every boot, a bug lurking
later in boot or in the interactive loop could freeze or crash there and block REPL access
entirely — recovery would then require a full wipe via BOOTSEL mode + `flash_nuke.uf2`. F12
provides an "escape hatch" that's always safe to use as long as you can still reach this screen
(before any ROM/RAM loading or CPU startup).

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

> [!NOTE]
> This section used to also cover **Serial Console** (detecting characters on the PB-1000 LCD
> and outputting them in real time over the console UART, GP4/GP5; toggled via `[keyboard]
> enable_uart_kbd`), removed on 2026-08-22. The UART keyboard input feature (typing PB-1000
> keys from a serial terminal), which shared the same GP4/GP5 UART1, was removed at the same
> time. RS-232C (PIO UART, GP6/GP13) below is unrelated to that removal and remains available.

### RS-232C (PIO UART)

Emulates the PB-1000's RS-232C interface using a PIO software UART (default GP6 TX / GP13 RX).

- Connected to MMIO addresses 0x0C00–0x0C03 (SIO registers).
- TX/RX pins are changeable via `[rs232c] tx_pin`/`rx_pin` in `pb1000.ini` (default GP6/GP13).
- Baud rate is configured via `[rs232c] baudrate` in `pb1000.ini` (default 9600 bps).
- Toggle it in the F1 boot-time setup menu (`[rs232c] enable`); it takes effect after saving
  and the resulting MCU reboot.
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
- Toggle it in the F1 boot-time setup menu (`[joystick] enable`); it takes effect after saving
  and the resulting MCU reboot.
- Pin assignments can be changed by editing `JoystickInputManager.DEFAULT_PIN_MAP` in `mp/main_input_joystick.py`.

---

## 8. State Management

### Save State (F11)

Press **F11** to save the current session state to the active profile directory.

- `ram0.bin`: Standard RAM (8 KB)
- `ram1.bin`: Expanded RAM1 (when enabled)
- `ram2.bin` / `ram3.bin`: Expanded RAM2 / RAM3 (Bank2 / Bank3, when enabled)
- `regs.json`: CPU registers (PC, flags, general-purpose registers)
- `color_vram.bin`: Color VRAM (VDP, 192x64 = 12,288 B; only saved when the program actually
  uses VDP)

### RAM Save (Emulator Menu)

**RAM Save** saves a snapshot set independently of the profile.

- Target directory: `/sd/rams/<folder name>/`

To load a saved snapshot back, use **System > Reboot Emulator (MCU)** and pick it again at the
boot-time profile picker (see Auto-Load below) — the EMULATOR MENU's old **RAM Load** item was
removed on 2026-08-22 in favor of this single, unified path.

### Auto-Load

On startup, the emulator loads the state files from the selected profile directory
automatically. If no files are present, a cold boot is performed. **This does not reset the
CPU — execution resumes from the exact PC/registers that were saved.**

> [!NOTE]
> In earlier versions, Auto-Load alone stayed conservative — restoring RAM contents only and
> always resetting to PC=0x0000 — specifically to avoid an unattended boot (the profile picker
> timing out with nobody at the keyboard) getting stuck resuming a bad/inconsistent save with no
> way to reach the menu. It's now unified into a single full-resume behavior. As a safety net for
> the unattended case, a separate mechanism still exists: within the first 1.5 seconds of the
> main loop, if the CPU comes up stuck sleeping with KEY_INT disabled, it force-resets (the
> "Startup sleep detected" guard in `mp/main.py`).

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

---

## 11. HDMI Mirror Output (Optional)

Connecting a second Raspberry Pi Pico 2 plus an HDMI output addon (e.g. PICO-HDMI-PLUS) lets you
mirror the main unit's screen to an HDMI monitor in real time. Has no effect if you don't have
the addon. See [hardware_guide_en.md](hardware_guide_en.md) §9 for wiring and
[config_guide_en.md](config_guide_en.md) §14 for the settings.

**Enabling it**: set `[hdmi] enable = true` in `pb1000.ini`, or toggle it from the boot-time F1
setup menu. Takes effect after saving and an MCU reboot (there is no runtime toggle).

**LCD and HDMI are exclusive outputs.** While HDMI is enabled, nothing is drawn to the physical
LCD (or its status bar) at all — the following are shown exclusively on HDMI instead:

- The game screen (PB-1000's LCD VRAM / the Color VRAM extension) and the bezel
- The boot-time RAM profile picker
- The EMULATOR MENU

On the HDMI side, the game screen, bezel, menu, and profile picker are each centered as
independent layers, so switching between these differently-sized screens never causes them to
drift out of position relative to each other.

> [!NOTE]
> For how to obtain/build the receiver firmware, see the independent project
> [`hdmi_bridge_receiver`](https://github.com/MobileFF/hdmi_bridge_receiver).

---

## 12. Always-On Clock (Optional)

While the emulator is running, the current time can be shown continuously in the top-right
corner of the screen. Enable it with `[overlay] show_clock = true` in `pb1000.ini` (off by
default). See [config_guide_en.md](config_guide_en.md) §15 for details.

This shares its config key (`show_clock`) with §2's boot-time status overlay clock, but is a
separate implementation: that one only shows before boot finishes, reading the Pico's built-in
RTC. This one, shown while the emulator is running, reads **`TIME$`/`DATE$` directly from
PB-1000's own system variable RAM**, so it always matches what `PRINT TIME$` would show from
BASIC — including when NTP isn't used, or the program has changed `TIME$` directly. It redraws
once per second, in the margin above the bezel (never overlapping the game screen or
FuncKeyBar). Not shown when `[hdmi] enable = true`, per §11's exclusivity policy.

---

## 13. Always-On Memory Usage Display (Optional)

While the emulator is running, the Pico 2's free heap can be shown continuously at the
**top-center** of the screen. Enable it with `[overlay] show_mem_free = true` in `pb1000.ini`
(off by default). See [config_guide_en.md](config_guide_en.md) §15 for details.

Same mechanism as the always-on clock above (runtime-only, redraws once per second), but a
separate feature with a different position and content. No `gc.collect()` is called before
reading the value, so it shows the actual free-memory pressure/fragmentation happening in real
time. On narrower screens, enabling this together with the always-on clock can put the top-right
and top-center text close together or overlapping. Not shown when `[hdmi] enable = true`, per
§11's exclusivity policy.
