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
> The real hardware is supported in **two variants**: **Raspberry Pi Pico 2 W (`RPI_PICO2_W`)**
> and **plain Raspberry Pi Pico 2 (`RPI_PICO2`)**. Pick the `BOARD=` value and output filename
> (`firmware_pb1000_pico2w.uf2` / `firmware_pb1000_pico2.uf2`) matching your hardware; CFLAGS and
> `USER_C_MODULES` are identical for both. The `RPI_PICO2` build excludes WiFi/Bluetooth code
> (the CYW43 driver, etc.), so it boots correctly on hardware without a CYW43439 chip instead of
> hanging at `cyw43_init()`.

If you're building multiple Pico projects on the same machine, we strongly recommend giving each
one its own MicroPython clone rather than sharing one. `ports/rp2/build-<BOARD>/` holds build
caches (including `USER_C_MODULES` in `CMakeCache.txt`), and sharing a checkout across projects
risks one project silently reusing a cache configured for another, pulling in the wrong C modules.

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

Then build with the CFLAGS required for TinyUSB's host mode:

**Example (Linux/WSL2):**
```bash
cd ports/rp2
export USER_C_MODULES="/home/<user>/projects/hd61700/src/micropython.cmake"
export CFLAGS="-Wno-error=unused-parameter -Wno-error=unused-variable -DCFG_TUH_ENABLED=1 -DCFG_TUD_ENABLED=0 -DMICROPY_HW_USB_CDC=0 -DMICROPY_HW_USB_MSC=0 -DMICROPY_HW_USB_HID=0 -DMICROPY_PY_PIO_USB=1 -I/home/<user>/projects/hd61700/src"
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
# For the plain Pico 2, just swap in BOARD=RPI_PICO2
```

Keep `CFLAGS` on a single line as shown above. A multi-line value (with embedded newlines) breaks
cmake's initial compiler-check Makefile generation on a completely fresh build (no existing
`build-<BOARD>/`), failing with a `missing separator` error.

**Do not include `-DCFG_TUSB_MCU=...` or `-DCFG_TUSB_RHPORT1_MODE=(OPT_MODE_HOST|0x0100)` in
CFLAGS.** These are leftovers from when this project used a PIO-USB host implementation; the
current "Native Host mode" (`src/usb_host_core.c`, using RHPORT0) doesn't need them. Passing them
on a completely fresh cmake configure collides with a macro MicroPython itself defines for the
`firmware` target, and `-Werror` then fails the build on files like `py/asmarm.c` with
`"CFG_TUSB_MCU" redefined [-Werror]` (easy to miss if you're reusing an existing build cache, since
it only surfaces on a fresh configure). `src/usb_host/tusb_config.h` already provides its own
`#ifndef`-guarded fallback values for `usb_host_core.c`, so dropping these from CFLAGS doesn't
affect USB host functionality.

**Do not add `-DDEBUG_SKIP_CORE_INIT` to CFLAGS.** It bypasses the
`USB_HOST_SKIP_INIT` cmake option in `src/micropython.cmake` (default OFF,
i.e. real USB host init runs by default), forcing `usb_host.init()` to always
skip the actual `tuh_init()` call. The build and boot still succeed, so this
is easy to miss, but no USB keyboard will ever be recognized.

**CMake caches `CMAKE_C_FLAGS`.** Changing CFLAGS while reusing the same `build-<BOARD>/`
directory has no effect — the old cached value keeps being used. Whenever you change CFLAGS,
`rm -rf build-<BOARD>` first and rebuild.

Native Windows builds are not recommended given the CFLAGS above — use WSL2 with the commands shown.

The output firmware will be located at `build-RPI_PICO2_W/firmware.uf2` (or
`build-RPI_PICO2/firmware.uf2` for the plain Pico 2). When copying it out for flashing, rename it
to `firmware_pb1000_pico2w.uf2` (or `firmware_pb1000_pico2.uf2`) so it's not confused with builds
from other parallel projects.

> See `/home/flex/projects/micropython.pb1000/ports/rp2/bldfrm.sh` (this project's own dedicated
> MicroPython checkout) for this project's actual (environment-specific) build script.

## Flashing

1.  **Enter BOOTSEL mode**: Hold the BOOTSEL button on your Pico 2 (W) while connecting it to your PC via USB.
2.  **Mount**: The Pico 2 will appear as a USB mass storage device named `RPI-RP2`.
3.  **Copy**: Drag and drop `firmware_pb1000_pico2w.uf2` (Pico 2 W) or `firmware_pb1000_pico2.uf2`
    (plain Pico 2) onto the `RPI-RP2` drive, matching your hardware. The Pico 2 will reboot automatically.

## Post-Build Setup

Once the firmware is flashed, you need to upload the Python logic and ROM files.

> [!IMPORTANT]
> This firmware builds the Pico's USB port as **USB host only** (`CFG_TUD_ENABLED=0`), so
> `mpremote` cannot connect over the Pico's own USB port. Wire a USB-to-serial adapter to
> **GP0/GP1 (UART0 REPL)** and connect to that serial port explicitly (e.g. `/dev/ttyUSB0`,
> `COMx` on Windows) — see "UART and Serial" in the [Hardware Guide](hardware_guide_en.md) for
> wiring. Read every `mpremote` command below as `mpremote connect <port> ...`, or set
> `MPREMOTE_TTY=/dev/ttyUSB0` before running them.

1.  **Install mpremote**:
    ```bash
    pip install mpremote
    ```
2.  **Upload Python files**:

    ```bash
    cd PB-1000_emu_AG2/mp
    mpremote connect /dev/ttyUSB0 fs cp * :
    ```

3.  **Upload ROMs**:
    ```bash
    # Create roms directory on Pico
    mpremote connect /dev/ttyUSB0 fs mkdir :roms
    # Upload ROM files (rom0.bin, rom1.bin)
    cd ../roms
    mpremote connect /dev/ttyUSB0 fs cp *.bin :roms/
    ```

## Troubleshooting

- **"micropython.cmake not found"**: Double-check the absolute path in `USER_C_MODULES`.
- **"arm-none-eabi-gcc not found"**: Ensure the toolchain is in your `PATH`.
- **Build hangs (WSL2)**: Ensure you are building on the Linux file system (`~/...`), not on a mounted Windows drive (`/mnt/c/...`), as the latter is much slower and can cause issues with git submodules.
