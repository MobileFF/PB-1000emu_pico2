# Hardware Guide

This guide describes the hardware components and wiring required to build the PB-1000 emulator.

## Bill of Materials (BOM)

- **Microcontroller**: Raspberry Pi Pico 2 (RP2350) or Pico (RP2040). Pico 2 is recommended for better performance.
- **Display**: ILI9341 320x240 TFT LCD (SPI interface). Modules with integrated touch (XPT2046) are supported.
- **Storage**: Micro SD card module (SPI interface).
- **USB Host**: USB OTG (On-The-Go) adapter (Micro-USB to USB-A Female) for connecting a keyboard.
- **Power**: USB power supply (5V via Micro-USB port).
- **Other**: Breadboard, jumper wires, and (optional) 100-ohm resistor for backlight PWM.

## Pin Assignments

All components share the same SPI bus (SPI1) except for the Console UART and USB.

### 1. ILI9341 Display (SPI1)

| ILI9341 Pin | Pico Pin | Function | Note |
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
| Console (UART1) | GP4 (TX), GP5 (RX) | Debug / REPL | Optional. Changeable via `uart_tx_pin` / `uart_rx_pin` in `pb1000.ini` |
| PIO UART | GP6 (TX), GP13 (RX) | Virtual RS-232C | Optional. Default 9600 bps (changeable via `[pio_uart] baudrate` in `pb1000.ini`) |

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
The pin assignments themselves are defined in `JoystickInputManager.DEFAULT_PIN_MAP` in `main_input.py` and can be changed by editing the code.
For details of a 3-bit connection circuit using a 74HC148 priority encoder, see `references/memo/joystick_3bit_encoding_circuit.md`.

**GPIO availability with the joystick enabled**

With the joystick using all of GP18–21 and GP26–27, **GP28** is the only GPIO left free for external devices (usable as ADC2). Other pins can be freed by disabling the corresponding feature below.

| GPIO | Used For | Freed When |
| :--- | :--- | :--- |
| GP2, GP3 | I2C1 (reserved, PR6 not yet implemented) | Currently unused — no I2C-based module ships in `mp/ext/`. Reserved for adding an I2C extension (e.g. `sample/mp/ext/dht20.py`) to `mp/ext/` in the future |
| GP4, GP5 | UART1 (console keyboard) | `pb1000.ini`: `enable_uart_kbd=false` |
| GP6, GP13 | PIO UART (RS-232C) | RS-232C not used |
| GP14 | BEEP PWM | `pb1000.ini`: `[beep] enable=false` |
| **GP28** | **Free (ADC2)** | **Always available** |

### 7. External SPI Device (using GP28 as CS)

The SPI1 bus is already shared by the LCD, SD card, and touch panel, each with its own CS pin; additional devices can be added the same way. In the standard configuration including the joystick, **GP28 is the only free GPIO**, so it is recommended as the CS pin for an additional device.

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

- Connect the USB keyboard to the Pico's Micro-USB port via a **USB OTG adapter**.

## Wiring Considerations

- **SPI Sharing**: The CS (Chip Select) pins must be independent for the LCD, SD, and Touch. Ensure all CS pins are pulled HIGH initially in code to avoid bus contention.
- **Power Consumption**: The ILI9341 backlight can draw significant current. If the Pico reboots or the screen flickers, use an external 3.3V regulator or power the backlight from the VBUS (5V) pin (if the module supports it).
- **Logic Level**: All pins are 3.3V logic. Do not connect 5V signals directly to the Pico pins.

## Schematic

A KiCad schematic file is available at:
- `hardware/pb1000_emulator.kicad_sch`

> [!TIP]
> You can open this file with KiCad 7.0 or later to view the full electrical connections.
