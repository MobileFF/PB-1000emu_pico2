# Hardware Guide

This guide describes the hardware components and wiring required to build the PB-1000 emulator.

## Bill of Materials (BOM)

- **Microcontroller**: Raspberry Pi Pico 2 (RP2350) or Pico (RP2040). Pico 2 is recommended for better performance.
- **Display**: ILI9341 320x240 or ST7796 480x320 TFT LCD (SPI interface), selectable via `[display] driver` in `pb1000.ini`. Modules with integrated touch (XPT2046) are supported.
- **Storage**: Micro SD card module (SPI interface).
- **USB keyboard connection (pick one)**:
  - **PIO-USB (default)**: wire the USB keyboard directly to GP4/GP5 (see "8. USB Keyboard"
    below). This frees the Pico's own USB-C port to run as a CDC device, so `mpremote` can
    connect directly over it.
  - **Native USB host (`USE_PIO_USB=OFF` build)**: USB OTG (On-The-Go) adapter (Micro-USB to
    USB-A Female) for connecting a keyboard to the Pico's own USB port. In this configuration
    the Pico's USB port is keyboard-only, so a separate USB-to-serial adapter is needed for the
    REPL.
- **USB-to-serial adapter (required only for the native USB host option)**: for reaching the
  MicroPython REPL (wired to GP0/GP1). See "UART and Serial" below. Not needed with the default
  PIO-USB build, which exposes the REPL as a CDC device on the USB-C port instead.
- **Power**: USB power supply (5V via Micro-USB port).
- **Other**: Breadboard, jumper wires, and (optional) 100-ohm resistor for backlight PWM.
  The PIO-USB option additionally needs a USB connector and two 22-ohm resistors (see "8. USB
  Keyboard" below).

> [!IMPORTANT]
> In the default build (`USE_PIO_USB=ON`), the USB keyboard host moves to PIO-USB on GP4/GP5,
> and the Pico's own USB-C port runs as a CDC device. Connect with a single USB-C cable using
> `mpremote connect /dev/ttyACM0` (`COMx` on Windows) for the REPL and file transfer. If built
> with `USE_PIO_USB=OFF`, the Pico's USB port becomes **host-only** and you **cannot** reach the
> MicroPython REPL over it; the REPL is then reachable **only through the UART wired to GP0/GP1**.
> See the "REPL (UART0)" entry under "UART and Serial" below for wiring details.

## Pin Assignments

All components share the same SPI bus (SPI1) except for the Console UART and USB.

### 1. LCD Display (ILI9341 / ST7796, shared pinout, SPI1)

Both ILI9341 and ST7796 modules use the same pin roles. Which driver is used is
selected via `[display] driver` (`ILI9341` or `ST7796`) in `pb1000.ini`.

| LCD Pin | Pico Pin | Function | Note |
| :--- | :--- | :--- | :--- |
| VCC | 3.3V / 5V | Power | Check module requirements |
| GND | GND | Ground | |
| CS | GP9 | Chip Select | |
| DC / RS | GP8 | Data/Command | |
| RST | GP7 | Reset | |
| SDI (MOSI) | GP11 | SPI1 TX | |
| SCK | GP10 | SPI1 SCK | |
| SDO (MISO) | GP12 | SPI1 RX | |
| LED (BL) | GP22 | Backlight | Connected to 3.3V via resistor optionally |

> **Driver differences**: ST7796 (480x320) has a higher resolution than ILI9341
> (320x240), so the recommended `[display] scale` value in `pb1000.ini` differs between
> them (documented in comments in the file itself). The default SPI baud rate also
> switches automatically per driver: 26MHz for ILI9341, 40MHz for ST7796.

### 2. SD Card Module (SPI1)

| SD Pin | Pico Pin | Function | Note |
| :--- | :--- | :--- | :--- |
| VCC | 3.3V / 5V | Power | |
| GND | GND | Ground | |
| CS | GP15 | Chip Select | |
| MOSI | GP11 | SPI1 TX | Shared |
| SCK | GP10 | SPI1 SCK | Shared |
| MISO | GP12 | SPI1 RX | Shared |

### 3. Touch Panel (XPT2046) (SPI1)

| Touch Pin | Pico Pin | Function | Note |
| :--- | :--- | :--- | :--- |
| T_CS | GP16 | Chip Select | |
| T_CLK | GP10 | SPI1 SCK | Shared |
| T_DIN | GP11 | SPI1 TX | Shared |
| T_DO | GP12 | SPI1 RX | Shared |
| T_IRQ | GP17 | Interrupt | |

### 4. UART and Serial

| Device | Pico Pin | Function | Note |
| :--- | :--- | :--- | :--- |
| **REPL (UART0)** | **GP0 (TX), GP1 (RX)** | **MicroPython REPL / file transfer (`mpremote`)** | Not needed in the default build (`USE_PIO_USB=ON`) — use the USB-C CDC port instead. **Required** with `USE_PIO_USB=OFF` (the Pico's USB port is then keyboard-only). If wired, use a USB-to-serial (3.3V TTL) adapter and connect from the PC with e.g. `mpremote connect /dev/ttyUSB0` (`COMx` on Windows) |
| **USB keyboard host (PIO-USB)** | **GP4 (D+), GP5 (D-)** | **USB keyboard connection** | Used in the default build (`USE_PIO_USB=ON`). See "8. USB Keyboard" below for wiring |
| PIO UART | GP6 (TX), GP13 (RX) (default) | Virtual RS-232C | Optional. Both pins and baud rate (default 9600 bps) are changeable via `[rs232c] tx_pin`/`rx_pin`/`baudrate` in `pb1000.ini` -- any free pins not used by another feature work |

> **Wiring (native USB host option, UART-based REPL)**: connect the USB-to-serial adapter's TXD to the Pico's **GP1 (RX)**, its RXD to **GP0 (TX)**, and tie the grounds together (note the TX↔RX crossover).

### 5. Beep (PWM)

| Function | Pico Pin | Note |
| :--- | :--- | :--- |
| Beep output | **GP14** | PWM output. Changeable via `[beep] gpio_pin` in `pb1000.ini` |

Connect the pin to a piezo buzzer or small speaker, through a resistor of roughly 100 Ω.

### 6. Joystick (Optional)

Connected directly (PULL_UP inputs, active LOW).

| Button | Default Pico Pin | `pb1000.ini` Key | Default PB-1000 Key |
| :--- | :--- | :--- | :--- |
| UP | GP18 | `key_up` | Cursor Up |
| DOWN | GP19 | `key_down` | Cursor Down |
| LEFT | GP20 | `key_left` | Cursor Left |
| RIGHT | GP21 | `key_right` | Cursor Right |
| FIRE1 | GP26 | `key_fire1` | EXE |
| FIRE2 | GP27 | `key_fire2` | SHIFT |

The PB-1000 key assigned to each button can be changed in the `[joystick]` section of `pb1000.ini`; an empty value falls back to the default map.
The pin assignments themselves are defined in `JoystickInputManager.DEFAULT_PIN_MAP` in `main_input_joystick.py` and can be changed by editing the code.
For details of a 3-bit connection circuit using a 74HC148 priority encoder, see `references/memo/joystick_3bit_encoding_circuit.md`.

**GPIO availability with the joystick enabled**

With the joystick using all of GP18–21 and GP26–27, only **GP28** is free for external devices in
the default build (`USE_PIO_USB=ON`); with `USE_PIO_USB=OFF`, **GP4, GP5, and GP28** are free
(GP28 also usable as ADC2). Other pins can be freed by disabling the corresponding feature below.

| GPIO | Used For | Freed When |
| :--- | :--- | :--- |
| GP0, GP1 | **REPL (UART0)** | Freeable in the default build (CDC over USB-C substitutes for it). **Never freeable with `USE_PIO_USB=OFF`** |
| GP2, GP3 | I2C1 (reserved, PR6 not yet implemented) | Currently unused — no I2C-based module ships in `mp/ext/`. Reserved for adding an I2C extension (e.g. `sample/mp/ext/dht20.py`) to `mp/ext/` in the future |
| GP4, GP5 | **USB keyboard host (PIO-USB)**. Used in the default build (`USE_PIO_USB=ON`) | Always available only with `USE_PIO_USB=OFF` (native USB host option) |
| GP6, GP13 | PIO UART (RS-232C, default) | RS-232C not used, or reassigned to other pins via `[rs232c] tx_pin`/`rx_pin` in `pb1000.ini` |
| GP14 | BEEP PWM | `pb1000.ini`: `[beep] enable=false` |
| **GP28** | **Free (ADC2)** | **Always available** |

### 7. External SPI Device (using GP28 as CS)

The SPI1 bus is already shared by the LCD, SD card, and touch panel, each with its own CS pin; additional devices can be added the same way. In the standard configuration including the joystick, **GP28** is recommended as the CS pin for an additional device (with `USE_PIO_USB=OFF`, GP4/GP5 are also free, but GP28's ADC2 capability makes it the more flexible default).

| Signal | Pico Pin | Note |
| :--- | :--- | :--- |
| SCK | GP10 | Shared SPI1 |
| MOSI | GP11 | Shared SPI1 |
| MISO | GP12 | Shared SPI1 |
| CS (extra device) | **GP28** | Dedicated CS |

**Usage**: place an extension module under `ext/` and reference `system.spi` inside `register(system)`. See `sample/mp/ext/spi_sample.py` for a template (copy it into `mp/ext/` before using).

```python
# Example: ext/my_device.py
import machine

MY_CS_PIN = 28
LCD_BAUD  = 40_000_000

def register(system):
    cs = machine.Pin(MY_CS_PIN, machine.Pin.OUT, value=1)
    system.register_call_hook(0x5E30, lambda: _callback(system, cs))

def _callback(system, cs):
    spi = system.spi
    spi.init(baudrate=1_000_000)   # switch to the device's baud rate
    cs.value(0)
    # ... transfer data ...
    cs.value(1)
    spi.init(baudrate=LCD_BAUD)    # restore the LCD's baud rate
```

> **Note**: Always restore the LCD baud rate with `spi.init(baudrate=40_000_000)` before the callback returns. Forgetting to do so will corrupt LCD rendering.

### 8. USB Keyboard

Which wiring applies depends on the `USE_PIO_USB` build option (see [build_guide.md](build_guide.md)).

**PIO-USB (default, `USE_PIO_USB=ON`)**

Wire the USB keyboard directly to GP4/GP5. No USB OTG adapter is needed, but you do need a
USB-A connector (or a cut USB cable) and two resistors.

| Signal | Pico Pin | Note |
| :--- | :--- | :--- |
| D+ | GP4 | Through a 22-ohm series resistor |
| D- | GP5 | Through a 22-ohm series resistor |
| VBUS | 5V (VBUS) | Powers the keyboard |
| GND | GND | |

- **No external pull-up or pull-down resistor is needed** — RP2350's internal pull-down alone is
  sufficient. Adding an external pull-down (e.g. 15kΩ) has been observed to cause unstable
  enumeration failures where SETUP (control transfers) succeed but longer data transfers fail
  CRC.
- ESD protection diodes (bidirectional, D+/D- to GND, e.g. ESD5302F) are optional but recommended
  for a real build.
- **Wiring quality matters**: a breadboard + jumper-wire prototype has been observed to pass
  SETUP but fail CRC on subsequent data transfers, breaking enumeration. Use a soldered
  perfboard or similar solid construction.
- **Known limitation**: some USB keyboards (including wireless keyboard receivers) are not
  recognized when plugged straight into GP4/GP5. Putting a **USB hub in between** can make them
  work (up to 4 devices besides the hub itself, including any device built into the hub). If a
  keyboard still isn't recognized through a hub, try a different one. `mp/test/usb_kbd_diag.py`
  helps find out where it fails.

**Native USB host (`USE_PIO_USB=OFF` build)**

- Connect the USB keyboard to the Pico's own USB port via a **USB OTG adapter**. In this
  configuration the Pico's USB port is keyboard-only, so the REPL requires the GP0/GP1 UART
  wiring (see "4. UART and Serial" above).


### 9. HDMI Mirror Output (Optional, Second Pico 2)

The main unit's LCD contents can be mirrored to an HDMI monitor using a second Raspberry Pi
Pico 2 (RP2350) plus an HDMI output addon (e.g. PICO-HDMI-PLUS). It reuses the §7 "External SPI
Device" mechanism as-is — just add one new CS pin (GP28) onto the SPI1 bus. If you don't have
the addon, leaving `[hdmi] enable=false` (the default) in `pb1000.ini` has zero effect.

> [!NOTE]
> The receiver (the second Pico 2) is an independent project, not included in this repo. See
> [`hdmi_bridge_receiver`](https://github.com/MobileFF/hdmi_bridge_receiver) for how to get/build
> the firmware and for the receiver's own wiring (HSTX output → HDMI addon).
> **The receiver requires a Pico 2 (RP2350)** — the HSTX peripheral is RP2350-specific, so a
> plain Pico/Pico W will not work.

**Wiring (main unit Pico 2 W → receiver Pico 2)**

Shares the same SPI1 bus as the §7 table; only CS is new (GP28).

| Signal | Main Unit Pico 2 W | Receiver Pico 2 | Note |
| :--- | :--- | :--- | :--- |
| SCK | GP10 | GP2 | Shared SPI1 |
| MOSI | GP11 | GP0 | Shared SPI1 (seen as SPI0 RX on the receiver) |
| CS | **GP28** (new) | GP1 | Receiver's dedicated CS — a new pin that doesn't collide with LCD/SD/touch |
| GND | GND | GND | Common ground |

For the receiver's own HSTX output (GP12-19) → HDMI addon wiring, see the receiver project's
documentation.

**Configuration**: the `[hdmi]` section of `pb1000.ini` (see [config_guide_en.md](config_guide_en.md)
§14), or toggle ON/OFF from the boot-time F1 setup menu (takes effect after saving and an MCU
reboot). **LCD and HDMI are exclusive outputs** — while HDMI is enabled, the physical LCD stops
updating and everything is shown on HDMI instead (see [usage_guide_en.md](usage_guide_en.md)
§11).

### 10. Waveshare RP2350-PiZero (onboard DVI output, experimental)

**This is a separate, dedicated board from the Pico 2 / Pico 2 W** (RP2350B, 48 GPIO, no SPI
LCD, onboard DVI + onboard microSD), targeting **HDMI/DVI output only** — no physical LCD or
touch panel at all. This port reuses the board definition and DVI output library
(`src/libdvi/`, BSD-3-Clause, see [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md)) from the
sibling MSX_emu_pico2 project's own already real-hardware-confirmed port to this exact board.

**Status**: with `[display] driver=DVI` set in `pb1000.ini` (a template for this board is
`mp/pb1000_pizero.ini`) and a normal boot (`main.py`), the following has been confirmed on real
hardware:

- The emulator screen (PB-1000's LCD), bezel, FunKeyBar, status line, and the always-on
  clock/memory readouts (`[overlay]`) over DVI
- The boot-time profile picker and the Win+F7 emulator menu over DVI
- A USB keyboard (on the board's USB-PIO connector, via PIO-USB)
- Reading and writing the onboard microSD (`/sd`)
- DVI output keeps running while flash is being written (e.g. saving a RAM profile)

RS-232C (PIO UART) has been confirmed to initialize; actual sending/receiving hasn't been
confirmed yet.

**Display details**:

- Output is fixed at 640×480 / 60Hz. Internally, a 320×240 screen is scaled up 2× in both
  directions (one dot becomes 2×2 monitor pixels).
- The LCD area is drawn by reading the emulator's LCD VRAM directly, scanline by scanline. Menus,
  the profile picker, FunKeyBar, etc. are drawn into a separate 320×240 layer on top of it (up to
  16 colors at a time).
- Choose a `[display] scale` that fits within 320 dots of width. At `scale = 1.0`, one LCD dot
  is exactly 2×2 monitor pixels. If the value doesn't fit (e.g. `2.0` = 384 dots wide), it is
  reduced automatically at boot to the largest scale that fits (320/192 ≈ 1.67). Since that is not
  an integer scale, dot widths become slightly uneven.

**PIO assignment**: pio0 = DVI output, pio1 = PIO-USB (USB keyboard), pio2 = RS-232C (PIO UART,
state machines 8/9, TX=GP6, RX=GP13). This differs from the Pico 2 / Pico 2 W.

**microSD**: the onboard slot is wired to SPI1 (SCK=GP30, MOSI=GP31, MISO=GP40, CS=GP43), on
different pins from the Pico 2 / Pico 2 W's SD card module. It is used automatically when the
DVI driver is selected.

**Build**: `bash bldfrm.sh pizero` (from `/home/flex/projects/micropython.pb1000/ports/rp2/`;
note the explicit argument — plain `bash bldfrm.sh` still builds Pico 2 / Pico 2 W as before).
Output: `firmware/firmware_pb1000_pizero.uf2`. The build procedure includes board-specific
patches:

- `memmap_mp_rp2350.ld`: moves core0's C stack (16KB) into regular SRAM, leaving SCRATCH_X/Y to
  core1 (DVI)
- `CMakeLists.txt`: stack size adjustments
- An explicit `MICROPY_C_HEAP_SIZE` (MicroPython's rp2 port defaults the C heap to 0 bytes, which
  makes a `calloc()` call inside this board's DVI init return a garbage pointer)

These are applied and reverted automatically depending on which target you build.

**Connecting**: after flashing, `mpremote connect <port>` works — either the native USB-C
port's CDC REPL, or the UART0 (GP0/GP1) REPL. This board's USB-C port cannot act as a native USB
host for the keyboard, so plug the keyboard into the USB-PIO connector (PIO-USB, GP28/29; see
`src/usb_host_core.c`'s `PB1000_PIO_USB_DP_PIN` comment for details).

**Wiring a USB-A connector from the 40-pin header (GP22 build)**: GP28/29 are not on the 40-pin
header, so for an external USB-A connector on the header use
`firmware/firmware_pb1000_pizero_gp22.uf2` (`bash bldfrm.sh pizero gp22`), which puts PIO-USB on
GP22/GP23. With this firmware the board's USB-PIO connector is not used; everything else is the
same as the regular build.

| USB-A | GPIO | Header pin |
|---|---|---|
| D+ | GP22 (22Ω in series) | 15 |
| D- | GP23 (22Ω in series) | 16 |
| VBUS | 5V | 2 or 4 |
| GND | GND | 14, etc. |

To check the wiring, run `mp/test/pio_usb_gp22_test.py` (`mpremote run mp/test/pio_usb_gp22_test.py`).
It checks the keyboard connection from the D+/D- levels, checks from the GPIO function
assignments whether the flashed firmware is the GP22 build, and prints the name of each key
pressed.

**Extension modules**: as on the other boards, `mp/ext/` is not frozen into the firmware, so copy
it to the internal flash's `/ext/` or to `/sd/ext/` if you use it. This includes
`dotds_64dot.py`, which shows all 8 lines in 64-dot mode.

**Confirming the DVI signal itself (for troubleshooting)**: run `mp/test/test_dvi_display.py`
directly instead of the normal boot sequence (`main.py`):

```
mpremote connect <port> run mp/test/test_dvi_display.py
```

If the DVI signal path (clocks, PIO, DMA, core1 launch) is working, your HDMI/DVI monitor should
show 8 vertical color bars (white/yellow/cyan/green/magenta/red/blue/black) plus a thin white
line across the vertical middle.

**Known limitations**:

- There is no WiFi, so no NTP time sync. PB-1000's own clock runs on from the time saved in the
  RAM profile.
- The overlay layer (menus etc.) shows up to 16 colors at a time; further colors are shown as the
  nearest of those.

## Wiring Considerations

- **SPI Sharing**: The CS (Chip Select) pins must be independent for the LCD, SD, and Touch. Ensure all CS pins are pulled HIGH initially in code to avoid bus contention.
- **Power Consumption**: The LCD backlight (ILI9341 or ST7796) can draw significant current. If the Pico reboots or the screen flickers, use an external 3.3V regulator or power the backlight from the VBUS (5V) pin (if the module supports it).
- **Logic Level**: All pins are 3.3V logic. Do not connect 5V signals directly to the Pico pins.
- **Board Mounting Orientation**: If the board (including the LCD/touch panel) has to be mounted upside down for enclosure reasons, set `[display] rotation = 180` in `pb1000.ini` instead of rewiring anything. Both the on-screen display and touch panel coordinates are flipped automatically (see the [Usage Guide](usage_guide_en.md)).

## Schematic

A KiCad schematic file is available at:
- `hardware/pb1000_emulator.kicad_sch`

> [!TIP]
> You can open this file with KiCad 7.0 or later to view the full electrical connections.
