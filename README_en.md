# PB-1000 Emulator for Raspberry Pi Pico 2

A Casio PB-1000 pocket computer emulator running on Raspberry Pi Pico 2 (RP2350).

## Overview

This project emulates the Casio PB-1000 pocket computer, which is equipped with the HD61700 CPU. It combines a high-speed C CPU core with flexible MicroPython logic for peripheral handling, providing a powerful environment for both running original software and developing custom extensions.

## Key Features

- **High-Performance CPU Core**: HD61700 instruction set implemented in C.
- **MicroPython Framework**: Peripheral logic written in MicroPython, allowing easy customization.
- **Modern Display Support**: ILI9341 320x240 TFT LCD with touch interface (XPT2046).
- **External Keyboard**: Supports HID USB keyboards (Host mode) and serial (UART) input.
- **Storage**: SD Card support for saving/restoring RAM states, screenshots, and virtual FDD disk image operations.
- **State Management**: Save/Load state functionality for RAM and registers.
- **Communication**: Virtual RS-232C support via PIO UART.
- **Subroutine Hooks**: Arbitrary Python/C functions can be triggered as callbacks when the PC reaches a specified address.
- **Joystick Support**: Supports ATARI 9-pin joystick connections; directions and A/B buttons can be mapped to any key.
- **Color Display Support**: Enables color display while maintaining compatibility with the PB-1000.
- **Up to 104KB RAM**: Banks 2/3 of Page 1 (0x8000–0xFFFF) can be equipped with RAM, enabling up to 104KB of usable RAM.

## Quick Start

1.  **Hardware**: Prepare a Raspberry Pi Pico 2, ILI9341 LCD, and (optional) SD card module. See [Hardware Guide](doc/hardware_guide_en.md) for details.
2.  **Build**: Compile the custom MicroPython firmware. See [Build Guide](doc/build_guide_en.md).
3.  **Flash**: Copy the generated `firmware.uf2` to your Pico 2.
4.  **Setup**: Upload the Python files from `mp/` and your ROM images to `/roms/` or `/sd/`. See [Usage Guide](doc/usage_guide_en.md).
5.  **Run**: The emulator starts automatically if `main.py` is present.

## Documentation Index

- [Build Guide](doc/build_guide_en.md) - How to set up the build environment and compile firmware.
- [Hardware Guide](doc/hardware_guide_en.md) - Wiring diagrams, BOM, and pin assignments.
- [Usage Guide](doc/usage_guide_en.md) - Initial setup, ROM preparation, and operation manual.
- [Development Guide](doc/dev_guide_en.md) - Code structure, internal APIs, and debugging tips.

*(Japanese documentation is available in files without the `_en` suffix)*

## Project Structure

```text
PB-1000_emu_AG2/
├── src/                    # C Source (HD61700 CPU Core & Peripheral Wrappers)
├── mp/                     # MicroPython code (System Logic & Drivers)
├── doc/                    # Documentation & Guides
├── hardware/               # KiCad Schematic and Hardware Design
└── README_en.md            # This file
```

## License

This project (the original code under `src/` and `mp/`, and the documentation) is released under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE) for the full text.

```
PB-1000 emulator for Raspberry Pi Pico 2
Copyright (C) 2026  MobileFF

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

### Scope and Notes

- **PB-1000 ROM images are not included.** The ROM is copyrighted by CASIO and is not distributed in this repository. Use a ROM image you have dumped from your own hardware (see the [Usage Guide](doc/usage_guide_en.md)).
- **The HD61700 CPU core (`src/hd61700.c`/`.h`) is a C port of the MAME project's `hd61700.cpp`** (by Sandro Ronco, BSD-3-Clause license). Being a permissive license, incorporating it into this GPL-3.0 project is not a problem; the full copyright notice, conditions, and disclaimer are reproduced in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
- Regarding Piotr Piatek's PB-1000 emulator mentioned in "Acknowledgments": its source (`pb1000es/`, bundled purely as reference material and not part of the build) was compared against this project's implementation, and no direct code reuse was found. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for details.
- This project links **MicroPython** (MIT License), the **Raspberry Pi Pico SDK** (BSD-3-Clause), and **TinyUSB** (MIT License) at build time; details are also in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Acknowledgments

- The following resources were referenced for the CPU implementation and emulator behavior. Many thanks to all of them.
  - CASIO "TECHNICAL MANUAL PB-1000" (English edition).
  - "HD61700 Assembly Language Quick Manual (Ver 0.29 2008-05-05)" and "HD61700 INSTRUCTION SET" by Ao.
  - Source code of the [PB-1000 emulator](https://www.pisi.com.pl/piotr433/pb1000ee.htm) by Piotr Piatek.
  - Various technical articles from "[CASIO PB-1000/C FOREVER!](http://www.lsigame.com/pb-1000/pb-1000.htm)" by Jun Amano.
  - HD61700 source code from the MAME project.
- Thanks also to everyone who has shared information about the PB-1000.
- The source code was primarily developed using the following AI agent tools:
  - Claude Code
  - OpenAI Codex
  - Google Antigravity
