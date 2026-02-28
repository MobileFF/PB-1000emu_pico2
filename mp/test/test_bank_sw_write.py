"""
OUTAC one-character display smoke test on real LCD hardware.

This script:
1. Initializes the real ILI9341 display
2. Boots PB-1000 emulator with ROMs
3. Forces NOWFC(0x690E)=0 (display device)
4. Executes a tiny RAM stub that loads $16 and calls OUTAC(0xFF9E)
5. Renders emulator VRAM to TFT
"""

import machine
import time
import hd61700 as cpu_core
from ili9341 import ILI9341
from pb1000 import PB1000System


# ---- Hardware Pin Configuration (same as main.py) ----
SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22


def init_display():
    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)
    return display


def draw_bezel(display):
    display.fill_rect(12, 36, 296, 72, 0x4228)
    display.fill_rect(14, 38, 292, 68, 0x8410)
    display.fill_rect(16, 40, 288, 64, 0xB5E6)


def run_outac_once(system,update_step=100):
    start_ms = time.ticks_ms()
    # Warm-up a little so BIOS work areas are initialized.
    print("Initialize")
    system.set_debug(False)
    #system.step(40000)
    #system.set_debug(True)

    print("Test Start")

    stub_addr = 0x7000
    # DRAW BITIMAGE test
    stub = bytes([
        0x42, 0x1F, 0x00,       # LD $31,0
        0x55, 0x1F,             # PSR SX, &H1F
        0xD6, 0x00, 0x00, 0x61, # PRE IX, 0x6100
        0x42, 0x00, 0x55,       # LD  $0, 0x55
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x01,       # PST UA, 1
        0xD6, 0x00, 0x00, 0x81, # PRE IX, 0x8100
        0x42, 0x00, 0xAA,       # LD  $0, 0xAA
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x00,       # PST UA, 0
        0xD6, 0x00, 0x10, 0x61, # PRE IX, 0x6110
        0x42, 0x00, 0x34,       # LD  $0, 0x34
        0x42, 0x01, 0x12,       # LD  $1, 0x12
        0xA0, 0x00,             # STW $0, (IZ)
        0xF8                    # NOP
    ])
    base = stub_addr - system.RAM_START
    for i, b in enumerate(stub):
        system.ram[(base + i) % len(system.ram)] = b

    system.set_pc(stub_addr)
    dbg = False
    # Run the stub
    # Using larger chunks and stop_pc for much better performance
    stop_pc = stub_addr + 0x29
    max_total_steps = 100
    total_steps = 0
    
    print(f"Running stub at 0x{stub_addr:04X} until 0x{stop_pc:04X}")
    while total_steps < max_total_steps:
        system.step(1,stop_pc=stop_pc)
        total_steps +=1

        if system.pc == stop_pc:
            print(f"Reached stop_pc: 0x{system.pc:04X}")
            break

        print(".", end="")
        
    print()
    
    print(f"0x6100: 0x{system.ram[0x6100-system.RAM_START]:02X}")
    print(f"0x8100: 0x{system.exp_ram[0x100]:02X}")
    print(f"0x6110: 0x{system.ram[0x6110-system.RAM_START]:02X}{system.ram[0x6111-system.RAM_START]:02X}")
    

def main():
    print("BANK SW WRITE test start")
    display = init_display()
    draw_bezel(display)

    # debug=True to see CPU/LCD logs while testing
    system = PB1000System(display=display)
    #system.lcd.setup_display(spi_id=1, cs_pin=9, dc_pin=8, scale=1, x_offset=16, y_offset=40)
    system.load_rom("/roms/rom0.bin", slot=0)
    system.load_rom("/roms/rom1.bin", slot=1)

    #update_step = int(input("update display step?>"))

    #run_outac_once(system,update_step)
    run_outac_once(system)
    #system.update_display(x_offset=16, y_offset=40)
    print("update display")
    system.update_display()
    
    print("test finished")

if __name__ == "__main__":
    main()



