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
You need to point `USER_C_MODULES` to the `src/micropython.cmake` file in this repository.

> [!IMPORTANT]
> Use an **absolute path** for `USER_C_MODULES`.

**Example (Linux/WSL2):**
```bash
export USER_C_MODULES="/path/to/PB-1000_emu_AG2/src/micropython.cmake"
make BOARD=RPI_PICO2 USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2 USER_C_MODULES="$USER_C_MODULES" -j$(nproc)
```

**Example (PowerShell):**
```powershell
$USER_C_MODULES = "G:/path/to/PB-1000_emu_AG2/src/micropython.cmake"
make BOARD=RPI_PICO2 USER_C_MODULES=$USER_C_MODULES clean
make BOARD=RPI_PICO2 USER_C_MODULES=$USER_C_MODULES -j4
```

The output firmware will be located at `build-RPI_PICO2/firmware.uf2`.

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
