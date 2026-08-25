# Build Instructions

> [!IMPORTANT]
> The real hardware is a **Raspberry Pi Pico 2 W**, and the board target below must always be
> **`RPI_PICO2_W`**, together with the PIO-USB TinyUSB `CFLAGS` shown in step 4. Building for
> plain `RPI_PICO2` uses different TinyUSB CFLAGS and the resulting firmware's USB keyboard will
> not work, even though the build itself succeeds. For the authoritative, actively-maintained
> version of these instructions see [doc/build_guide_en.md](doc/build_guide_en.md) -- this file
> covers Windows/WSL2/MSYS2 environment setup in more detail, but always cross-check the actual
> `make`/CFLAGS commands against that guide.

## Prerequisites

### Windows (PowerShell)

1. **Install ARM GCC Toolchain**
   - Download from [ARM Developer](https://developer.arm.com/downloads/-/gnu-rm)
   - Add to PATH

2. **Install CMake**
   ```powershell
   winget install Kitware.CMake
   ```

3. **Install Python**
   ```powershell
   winget install Python.Python.3.11
   ```

4. **Install Git**
   ```powershell
   winget install Git.Git
   ```

### Linux/macOS

```bash
# Ubuntu/Debian
sudo apt install cmake gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential git python3

# macOS
brew install cmake gcc-arm-embedded python3
```

### WSL2 (Ubuntu)

WSL2 is the recommended environment for Windows users who want a faster and more standard Linux build experience.

> [!TIP]
> **Performance Tip**: Do not clone the repository into the Windows file system (e.g., `/mnt/c/...`). This will be extremely slow. Always use the WSL home directory (e.g., `~/projects/...`).

1. **Install Dependencies**
   ```bash
   sudo apt update
   sudo apt install -y cmake gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential git python3
   ```

2. **Setup Workspace**
   ```bash
   cd ~
   mkdir -p projects/pico
   cd projects/pico
   ```

3. **Transferring Files to Windows**
   From Windows File Explorer, you can access your WSL files at `\\wsl$\Ubuntu\home\<username>\projects\pico`.

### MSYS2 (MinGW 64-bit)

MSYS2 provides a Unix-like environment on Windows without the overhead of a full virtual machine.

1. **Open MSYS2 MinGW 64-bit Shell**
   Make sure to use the **MinGW 64-bit** version.

2. **Install Dependencies**
   ```bash
   pacman -Syu
   pacman -S mingw-w64-x86_64-arm-none-eabi-gcc \
             mingw-w64-x86_64-cmake \
             make \
             git \
             python3 \
             base-devel
   ```

3. **Setup Workspace**
   ```bash
   mkdir -p /c/projects/pico
   cd /c/projects/pico
   ```

4. **Cloning and Building**
   The steps are identical to the WSL2 section, but paths will look like `/g/マイドライブ/...`.

## Build Steps

### 1. Clone MicroPython

**In WSL2/Linux:**
```bash
cd ~/projects/pico
git clone https://github.com/micropython/micropython.git
cd micropython
git submodule update --init --recursive
```

**In PowerShell:**
```powershell
cd g:\マイドライブ\RaspberryPiPicoW
git clone https://github.com/micropython/micropython.git
cd micropython
git submodule update --init --recursive
```

### 2. Build mpy-cross

**In WSL2/Linux:**
```bash
make -C mpy-cross
```

**In PowerShell:**
```powershell
make -C mpy-cross
```

### 3. Prepare Pico SDK

**In WSL2/Linux:**
```bash
cd ports/rp2
make submodules
```

**In PowerShell:**
```powershell
cd ports/rp2
make submodules
```

### 4. Build with HD61700 Module

**In WSL2/Linux:**
> [!IMPORTANT]
> If your project is on the G: drive (Windows), you can access it via `/mnt/g/`.
> Adjust the path below to match your setup. Windows-native builds are not recommended because
> of the CFLAGS below -- run this from WSL2.

```bash
# Example if project is in G:\マイドライブ\RaspberryPiPicoW\PB-1000_emu_AG2
export USER_C_MODULES="/mnt/g/マイドライブ/RaspberryPiPicoW/PB-1000_emu_AG2/src/micropython.cmake"

# If you copied the project to WSL home:
# export USER_C_MODULES="$HOME/projects/pico/PB-1000_emu_AG2/src/micropython.cmake"

# Required for TinyUSB's PIO-USB host mode -- do NOT build without this, and never add
# -DDEBUG_SKIP_CORE_INIT (it silently disables tuh_init(), so USB keyboards stop working
# even though the build and boot both still succeed).
export CFLAGS='-Wno-error=unused-parameter -Wno-error=unused-variable
  -DCFG_TUSB_MCU=OPT_MCU_RP2350
  -DCFG_TUSB_OS=OPT_OS_PICO
  -DCFG_TUH_ENABLED=1
  -DCFG_TUD_ENABLED=0
  -DCFG_TUSB_RHPORT1_MODE=(OPT_MODE_HOST|0x0100)
  -DMICROPY_HW_USB_CDC=0
  -DMICROPY_HW_USB_MSC=0
  -DMICROPY_HW_USB_HID=0
  -DMICROPY_PY_PIO_USB=1
  -I<path to your synced src/ copy>'

# Clean and Build
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
```

The output will be in `build-RPI_PICO2_W/firmware.uf2`.

### 5. Flash to Pico 2

1. **Connect Pico 2 W**: Hold the **BOOTSEL** button and connect the USB cable to your PC.
2. **Mount/Access Drive**:
   - **Windows**: It will appear as `RPI-RP2`.
   - **WSL2**: Usually, it's easier to copy the file from WSL to the Windows host first.
     ```bash
     cp build-RPI_PICO2_W/firmware.uf2 /mnt/c/Users/<YourUsername>/Desktop/firmware_pb1000.uf2
     ```
     (Renaming to `firmware_pb1000.uf2` avoids confusion if you have other Pico projects' UF2s
     around, but the drag-and-drop step works with any filename.)
3. **Copy File**: Drag and drop (or copy) the UF2 into the `RPI-RP2` drive.
4. **Reboot**: The Pico will reboot automatically once copying is complete.

### 6. Verify Installation

```powershell
# Install mpremote
pip install mpremote

# Connect and test
mpremote
```

In the REPL:
```python
>>> import hd61700
>>> hd61700.reset()
>>> print("HD61700 module loaded successfully")
```

## Troubleshooting

### Build Errors

**Error: arm-none-eabi-gcc not found**
- Ensure ARM GCC is installed and in PATH
- Restart terminal after installation

**Error: SDK not found**
- Run `make submodules` in `ports/rp2`
- Ensure git submodules were initialized

**Error: micropython.cmake not found**
- Check the absolute path to `micropython.cmake`
- Use forward slashes or properly escaped backslashes

### Flash Issues

**Pico not appearing as drive**
- Try a different USB cable (data cable, not charge-only)
- Hold BOOTSEL while connecting USB
- Try a different USB port

**Firmware not running**
- Check serial output: `mpremote connect /dev/ttyACM0` (Linux) or `mpremote` (Windows)
- Flash the stock MicroPython first to verify hardware

## Development Workflow

### Incremental Changes

When modifying C code (from WSL2/Linux, with the CFLAGS from step 4 above still exported):
```bash
cd ~/projects/pico/micropython/ports/rp2
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
```

When modifying Python code only:
```powershell
cd g:\マイドライブ\RaspberryPiPicoW\PB-1000_emu_AG2\mp
mpremote fs cp main.py :
mpremote fs cp pb1000.py :
```

### Debugging

Enable debug output in `modhd61700.c`:
```c
#define DEBUG 1
```

View serial output:
```powershell
mpremote
```

## Next Steps

After successful build:
1. Upload Python files (see README.md)
2. Upload ROM images
3. Run the emulator
