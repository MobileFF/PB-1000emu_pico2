# PB-1000 Emulator for Raspberry Pi Pico 2

A Casio PB-1000 pocket computer emulator for Raspberry Pi Pico 2 (RP2350).

## Features

- **HD61700 CPU Core**: High-performance C implementation based on MAME sources
- **ILI9341 Display**: 320x240 TFT LCD support
- **USB Keyboard**: Input via USB keyboard (host mode)
- **MicroPython**: System logic and I/O in MicroPython for flexibility

## Hardware Requirements

- Raspberry Pi Pico 2 (RP2350)
- ILI9341 320x240 TFT LCD (SPI interface)
- USB Keyboard (USB Host)
- Breadboard and jumper wires

## Wiring

### ILI9341 Display
| ILI9341 Pin | Pico 2 Pin | Note         |
| ----------- | ---------- | ------------ |
| VCC         | 3.3V       | Power        |
| GND         | GND        | Ground       |
| CS          | GP17       | Chip Select  |
| DC          | GP16       | Data/Command |
| RST         | GP20       | Reset        |
| MOSI        | GP19       | SPI0 TX      |
| SCK         | GP18       | SPI0 SCK     |

### USB Keyboard
- Connect via USB OTG adapter to Pico's USB port

## Software Setup

### 1. Build MicroPython with HD61700 Module

```powershell
# Clone MicroPython
git clone https://github.com/micropython/micropython.git
cd micropython

# Build mpy-cross
cd mpy-cross
make
cd ..

# Build for Pico 2 with custom module
cd ports/rp2
make BOARD=RPI_PICO2 USER_C_MODULES=../../../PB-1000_emu_AG2/src/micropython.cmake clean
make BOARD=RPI_PICO2 USER_C_MODULES=../../../PB-1000_emu_AG2/src/micropython.cmake
```

### 2. Flash Firmware

1. Hold BOOTSEL button on Pico 2
2. Connect to PC via USB
3. Copy `build-RPI_PICO2/firmware.uf2` to the Pico drive

### 3. Upload Python Files

```powershell
# Install mpremote if not already installed
pip install mpremote

# Upload files
cd PB-1000_emu_AG2/mp
mpremote fs cp ili9341.py :
mpremote fs cp pb1000.py :
mpremote fs cp main.py :

# Upload ROM files
cd ../roms
mpremote fs mkdir :roms
mpremote fs cp rom0.bin :roms/
mpremote fs cp rom1.bin :roms/
```

### 4. Run

```powershell
mpremote run main.py
```

Or set `main.py` to run automatically on boot by renaming it to `boot.py`.

## Project Structure

```
PB-1000_emu_AG2/
├── src/                    # C Source (HD61700 CPU Core)
│   ├── hd61700.h           # CPU header
│   ├── hd61700.c           # CPU implementation
│   ├── modhd61700.c        # MicroPython wrapper
│   └── micropython.cmake   # Build configuration
├── mp/                     # MicroPython code
│   ├── main.py             # Entry point
│   ├── pb1000.py           # System emulation
│   └── ili9341.py          # Display driver
├── roms/                   # ROM images
│   ├── rom0.bin            # Internal ROM (6KB)
│   └── rom1.bin            # External ROM (32KB)
└── README.md               # This file
```

## Current Status

### ✅ Completed
- [x] Project structure
- [x] HD61700 C core skeleton
- [x] MicroPython module wrapper
- [x] ILI9341 display driver
- [x] Basic system emulation class

### 🚧 In Progress
- [ ] Full HD61700 instruction set implementation
- [ ] Memory mapping refinement
- [ ] LCD controller emulation
- [ ] Keyboard matrix handling
- [ ] USB keyboard host driver

### 📋 TODO
- [ ] Comprehensive testing
- [ ] Performance optimization
- [ ] Debugging tools
- [ ] Save/Load state

## References

- [MAME HD61700 Source](https://github.com/mamedev/mame/blob/master/src/devices/cpu/hd61700/hd61700.cpp)
- HD61700.TXT - Assembly language manual (included)
- PB-1000 Technical Reference (PDF)

## License

This is an educational project. ROM images are not included and must be obtained separately.

## Author

Created using Google Deepmind's Antigravity AI Assistant
