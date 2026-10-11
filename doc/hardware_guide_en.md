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
- **Known limitation**: some USB keyboards are not recognized over this path (a compatibility
  issue; root cause not identified). Try a different keyboard if yours isn't recognized.

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
