# PB-1000 Emulator for Raspberry Pi Pico 2

A high-performance Casio PB-1000 pocket computer emulator optimized for Raspberry Pi Pico 2 (RP2350).

## Overview

This project implements a full emulation of the HD61700-based Casio PB-1000. It combines a high-speed C CPU core with flexible MicroPython logic for peripheral handling, providing a powerful environment for both running original software and developing new extensions.

## Key Features

- **High-Performance CPU Core**: HD61700 instruction set implemented in C, based on verified MAME sources.
- **MicroPython Framework**: PEROM and peripheral logic written in MicroPython, allowing easy customization.
- **Modern Display Support**: ILI9341 320x240 TFT LCD with scaled bezel and touch interface (XPT2046).
- **External Keyboard**: Supports HID USB keyboards (Host mode) and serial (UART) input.
- **Storage**: SD Card support for ROMs, RAM states, and screenshots.
- **State Management**: Robust Save/Load state functionality (RAM and registers).
- **Communication**: Virtual RS-232C support via PIO UART.

## Quick Start

1.  **Hardware**: Prepare a Raspberry Pi Pico 2, ILI9341 LCD, and (optional) SD card module. See [Hardware Guide](doc/hardware_guide.md) for wiring.
2.  **Build**: Compile the custom MicroPython firmware. See [Build Guide](doc/build_guide.md).
3.  **Flash**: Copy the generated `firmware.uf2` to your Pico 2.
4.  **Setup**: Upload the Python files from `mp/` and your ROM images to `/roms/` or `/sd/`. See [Usage Guide](doc/usage_guide.md).
5.  **Run**: The emulator starts automatically if `main.py` is present.

## Documentation Index

- [Build Guide](doc/build_guide.md) - How to set up the build environment and compile firmware.
- [Hardware Guide](doc/hardware_guide.md) - Wiring diagrams, BOM, and pin assignments.
- [Usage Guide](doc/usage_guide.md) - Initial setup, ROM preparation, and operation manual.
- [Development Guide](doc/dev_guide.md) - Code structure, internal APIs, and debugging tips.

## Project Structure

```text
PB-1000_emu_AG2/
├── src/                    # C Source (HD61700 CPU Core & Peripheral Wrappers)
├── mp/                     # MicroPython code (System Logic & Drivers)
├── doc/                    # Documentation & Guides
├── hardware/               # KiCad Schematic and Hardware Design
├── roms/                   # ROM images (Not included)
└── README.md               # This file
```

## Status

### ✅ Completed
- [x] HD61700 Instruction Set (C core)
- [x] LCD Controller Emulation (C-accelerated)
- [x] USB Keyboard Host Support
- [x] Touch Panel (XPT2046) Integration
- [x] SD Card (SPI) support
- [x] Save/Load State (JSON/Binary)
- [x] PIO UART (MMIO 0x0C00) support

### 📋 Future Roadmap
- [ ] RAM Bank Expansion (Bank 2/3)
- [ ] Extended Color VRAM
- [ ] WiFi Networking (Pico W)
- [ ] VGA/HDMI Output

## License

*License information is currently pending.*

## Acknowledgments

- Based on HD61700 research from the MAME project.
- Created during an AI-assisted pair programming session with Antigravity.
