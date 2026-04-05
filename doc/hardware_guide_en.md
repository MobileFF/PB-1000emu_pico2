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
| Console (UART1) | GP4 (TX), GP5 (RX) | Debug / REPL | Optional |
| PIO UART | GP6 (TX), GP13 (RX) | Virtual RS-232C | Optional |

### 5. USB Keyboard

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
