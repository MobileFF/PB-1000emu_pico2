# Build Guide

This guide explains how to set up the build environment and compile the custom MicroPython firmware for the PB-1000 emulator.

## Prerequisites

### 1. Toolchain and Dependencies

#### Windows (PowerShell)
We recommend using **WSL2** for the fastest and most reliable build experience. However, native Windows builds are also possible.
```powershell
# Install CMake, Python, Git
winget install Kitware.CMake Python.Python.3.11 Git.Git
# Download and install ARM GCC Toolchain from:
# https://developer.arm.com/downloads/-/gnu-rm
```

#### Linux (Ubuntu/Debian) / WSL2
```bash
sudo apt update
sudo apt install -y cmake gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential git python3
```

#### macOS
```bash
brew install cmake gcc-arm-embedded python3
```

## Build Steps

### 1. Clone MicroPython
It is recommended to use the latest stable version of MicroPython.
```bash
git clone https://github.com/micropython/micropython.git
cd micropython
git submodule update --init --recursive
```

### 2. Build mpy-cross
The MicroPython cross-compiler is required for the build.
```bash
make -C mpy-cross
```

### 3. Prepare Pico SDK
Ensure the Pico SDK and its submodules are initialized within the MicroPython tree.
```bash
cd ports/rp2
make submodules
```

### 4. Build with PB-1000 Module

> [!IMPORTANT]
> The real hardware is a **Raspberry Pi Pico 2 W**, so the board target must always be
> **`RPI_PICO2_W`**. Building for plain `RPI_PICO2` uses different CFLAGS for TinyUSB's PIO-USB
> host configuration, and firmware built that way will not work correctly (e.g. USB keyboard input)
> even though it flashes without error.

Rather than pointing `USER_C_MODULES` directly at this repository's `src/micropython.cmake`, this
project's standard workflow syncs the C sources to a separate build-copy directory first (e.g.
`~/projects/hd61700/src/`) and points `USER_C_MODULES` at the **absolute path** of the
`micropython.cmake` inside that copy (see `.claude/rules/firmware-source-location.md` in this
repository for why — the copy exists so the master source in this repo is never accidentally
overwritten by the build).

```bash
# 1. Sync src/ from this repo into the build copy
cp -r /path/to/PB-1000_emu_AG2/src/* ~/projects/hd61700/src/
```

Then build with the CFLAGS required for TinyUSB PIO-USB host mode:

**Example (Linux/WSL2):**
```bash
cd ports/rp2
export USER_C_MODULES="/home/<user>/projects/hd61700/src/micropython.cmake"
export CFLAGS='-Wno-error=unused-parameter -Wno-error=unused-variable
  -DCFG_TUSB_MCU=OPT_MCU_RP2350
  -DCFG_TUSB_OS=OPT_OS_PICO
  -DCFG_TUH_ENABLED=1
  -DCFG_TUD_ENABLED=0
  -DCFG_TUSB_RHPORT1_MODE=(OPT_MODE_HOST|0x0100)
  -DMICROPY_HW_USB_CDC=0
  -DMICROPY_HW_USB_MSC=0
  -DMICROPY_HW_USB_HID=0
  -DDEBUG_SKIP_CORE_INIT
  -DMICROPY_PY_PIO_USB=1
  -I/home/<user>/projects/hd61700/src'
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
```

Native Windows builds are not recommended given the CFLAGS above — use WSL2 with the commands shown.

The output firmware will be located at `build-RPI_PICO2_W/firmware.uf2`.

> See `/home/flex/projects/micropython/ports/rp2/bldfrm.sh` for this project's actual (environment-specific) build script.

## Flashing

1.  **Enter BOOTSEL mode**: Hold the BOOTSEL button on your Pico 2 while connecting it to your PC via USB.
2.  **Mount**: The Pico 2 will appear as a USB mass storage device named `RPI-RP2`.
3.  **Copy**: Drag and drop `firmware.uf2` onto the `RPI-RP2` drive. The Pico 2 will reboot automatically.

## Post-Build Setup

Once the firmware is flashed, you need to upload the Python logic and ROM files.

1.  **Install mpremote**:
    ```bash
    pip install mpremote
    ```
2.  **Upload Python files**:
    ```bash
    cd PB-1000_emu_AG2/mp
    mpremote fs cp * :
    ```
3.  **Upload ROMs**:
    ```bash
    # Create roms directory on Pico
    mpremote fs mkdir :roms
    # Upload ROM files (rom0.bin, rom1.bin)
    cd ../roms
    mpremote fs cp *.bin :roms/
    ```

## Troubleshooting

- **"micropython.cmake not found"**: Double-check the absolute path in `USER_C_MODULES`.
- **"arm-none-eabi-gcc not found"**: Ensure the toolchain is in your `PATH`.
- **Build hangs (WSL2)**: Ensure you are building on the Linux file system (`~/...`), not on a mounted Windows drive (`/mnt/c/...`), as the latter is much slower and can cause issues with git submodules.
