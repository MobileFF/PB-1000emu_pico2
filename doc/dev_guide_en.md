# Development Guide

This guide is intended for developers who want to understand the internal architecture of the PB-1000 emulator or contribute to its development.

## 1. System Architecture

The emulator uses a hybrid C/Python architecture to balance performance and flexibility.

- **CPU Core (C)**: The HD61700 emulator core is written in C for maximum execution speed. It handles instruction decoding, register management, and basic timing.
- **MicroPython Framework**: High-level system logic, peripheral emulation (LCD, Keyboard, SD Card), and the main execution loop are implemented in MicroPython.
- **C-Module (Bridge)**: A custom MicroPython module (`hd61700`) acts as the bridge, exposing CPU controls and memory access to Python.

## 2. Directory Structure

- `src/`: C source code.
  - `hd61700.c / .h`: Core CPU emulation logic.
  - `modhd61700.c`: MicroPython module wrapper. Handles memory mapping and interrupt routing.
  - `lcd_controller.c / .h`: C-accelerated LCD rendering logic.
- `mp/`: MicroPython source code.
  - `main.py`: Entry point, initializes the system and runs the main loop.
  - `pb1000.py`: The `PB1000System` class, managing the board-level emulation.
  - `ili9341.py`: Low-level SPI driver for the TFT display.
  - `keymap.py`: Keyboard translation tables.
- `hardware/`: KiCad schematic files.

## 3. CPU Core Intervals

### Memory Mapping
The `hd61700` module supports two memory modes:
1.  **C-Managed (Default)**: Memory buffers (`rom0_buf`, `ram_buf`, etc.) are allocated in C. This provides the best performance as the CPU core accesses them directly without crossing the Python boundary.
2.  **Python-Managed (Debug)**: Memory access triggers a Python callback. This is useful for debugging specific memory cycles but is significantly slower.

### Peripheral I/O
- **Port I/O**: The HD61700 `P0-P7` ports are mapped to Python callbacks via `set_port_callbacks()`.
- **LCD (HD61830)**: Emulated as a C-submodule. The Python side provides the physical SPI display object to the C-side for rendering.
- **UART (MMIO)**: The 0x0C00 range is trapped in C. It uses internal FIFOs that the Python side services to talk to the physical PIO UART.

## 4. Key Implementation Details

### PIO UART
Since the Raspberry Pi Pico 2 has limited hardware UARTs (one of which is used for the console), we use **Programmable I/O (PIO)** to implement a software-based bit-banged UART at 4800 baud. This ensures that timing-sensitive serial communication doesn't block the main CPU emulation.

### Keyboard Matrix
The PB-1000 uses a 13-row x 12-column matrix.
- `keymap.py` defines the coordinates.
- For USB Host mode, scancodes are captured by the `usb_host` module and sent to `hd61700.press_row_ki()` in the C core.

## 5. Debugging and Tracing

### C-Side Debugging
To enable verbose CPU tracing, edit `src/modhd61700.c`:
```c
#define DEBUG 1
```
Or call from Python:
```python
import hd61700
hd61700.reset(True) # Enable debug flags
```

### Trace Hooks
The C core supports specific watchpoints for memory areas like `PROG_TRACE_START` (0xB5D6) to help debug programs like `PBFTOBIN` or assembly loaders.

## 6. Build System

The project uses MicroPython's `USER_C_MODULES` feature. The `src/micropython.cmake` file tells the MicroPython build system which C files to compile and how to link them.


## 7. UTF-8 Conventions

This project standardizes text files on UTF-8.

- Use UTF-8 for Markdown, Python source files, and configuration files
- Prefer UTF-8 without BOM for Python source files
- Be careful when writing files directly from Windows PowerShell because encoding defaults can vary
- Always specify `utf-8` explicitly when the API allows it

### Recommended Settings

- Set VS Code `files.encoding` to `utf8`
- Disable `files.autoGuessEncoding` unless it is actually needed
- After saving documents that contain Japanese text, verify that no mojibake has appeared

### Recommended File Handling in Python

```python
from pathlib import Path

text = Path("doc/example.md").read_text(encoding="utf-8")
Path("doc/example.md").write_text(text, encoding="utf-8")
```

### Operational Notes

- PowerShell `Set-Content` may be affected by BOM handling or the active code page depending on the environment
- To reduce encoding issues, prefer Python `read_text` / `write_text` for Codex-driven file updates
- When normalizing existing files, reading with `utf-8-sig` and writing with `utf-8` is a practical way to remove a BOM
