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
    system.step(40000)
    #system.set_debug(True)

    print("Test Start")

    outdv_addr = 0x690C
    if system.RAM_START <= outdv_addr < system.SYS_ROM_START:
        system.ram[outdv_addr - system.RAM_START] = 0x00

    stub_addr = 0x7000
# # print mark and Alpabet(upper)
#     stub = bytes([
#             0x42,0x10,0x20, # LD      $16,&H1F
#             0x77,0x9E,0xFF, # CAL     OUTAC
#             0x48,0x10,0x01, # AD      $16,1
#             0x41,0x10,0x5B, # SBC     $16,&H5A
#             0xB4,0x8A,      # JR      NZ,LOOP
#             0xF7,           # RTN     
#     ])
# print all char
    stub = bytes([
            0x42,0x10,0x20, # LD      $16,&H20
            0x77,0x9E,0xFF, # CAL     OUTAC
            0x48,0x10,0x01, # AD      $16,1
            0xB1,0x87,      # JR      NC,LOOP
            0xF7,           # RTN
    ])
# print 1 char
#     stub = bytes([
#         0x42, 0x10, 0x47,        # LD $16,#&H24
#         0x77, 0x9E, 0xFF,        # CAL &HFF9E
#         0xF8, 0xF8,              # NOP, NOP
#         0xF8, 0xF8,              # NOP, NOP
#         0xF8, 0xF8,              # NOP, NOP
#         0xF8, 0xF8,              # NOP, NOP
#         0x37, stub_addr & 0xFF, (stub_addr >> 8) & 0xFF,  # JP stub_addr
#     ])
# print 
#     stub = bytes([
#         0x54, 0x00, 0xC3,        # PPO     &HDF    ;Select command register
#         0x42, 0x00, 0x83,        # LD      $0,&H83 ;Set 'DRAW CHARACTER','OVERWRITE', LCD1 select
#         0x42, 0x01, 0x10,        # LD      $1,48   ;Set LCD address(columns=(48/16)=3)
#         0x42, 0x02, 0x00,        # LD      $2,0    ;Set LCD address(line=0)
#         0xD2, 0x00, 0x40,        # STLM    $0,3    ;Set LCD command register & address(3byte)
#         0x42, 0x03, 0x04,        # LD      $3,&H14 ;Set data "A" ("A"=&H41 --> &H14) ("2"=&H32 --> &H23)
#         0x54, 0x00, 0xC2,        # PPO     &HDE    ;Select data ram
#         0x12, 0x03,              # STL     $3      ;Display LCD( character data )
#         0xF8, 0xF8,              # NOP, NOP
#         0x37, stub_addr & 0xFF, (stub_addr >> 8) & 0xFF,  # JP stub_addr
#     ])
#     stub = bytes([
#         0x54, 0x00, 0xFF,        # PPO     &HDF    ;Select command register
#         0x42, 0x00, 0x82,        # LD      $0,&H82 ;Set 'DRAW BITIMAGE','OVERWRITE', LCD1 select
#         0x42, 0x01, 0x00,        # LD      $1,0    ;Set LCD address(columns=(48/16)=3)
#         0x42, 0x02, 0x00,        # LD      $2,0    ;Set LCD address(line=0)
#         0xD2, 0x00, 0x40,        # STLM    $0,3    ;Set LCD command register & address(3byte)
#         0x42, 0x03, 0x01,        # LD      $3,&H01               ;Set data '&HXX'
#         0x42, 0x04, 0x03,        # LD      $4,&H03               ;Set data '&HXX'
#         0x42, 0x05, 0x07,        # LD      $5,&H07               ;Set data '&HXX'
#         0x42, 0x06, 0x0F,        # LD      $6,&H0F               ;Set data '&HXX'
#         0x42, 0x07, 0x1F,        # LD      $7,&H1F               ;Set data '&HXX'
#         0x42, 0x08, 0x3F,        # LD      $8,&H3F               ;Set data '&HXX'
#         0x54, 0x00, 0xDE,        # PPO     &HDE                  ;Select data ram(same as 'PPO &HC6')
#         0xD2, 0x03, 0xA0,        # STLM    $3,6                  ;Display LCD( 8bit data *6 )
#         0x37, stub_addr & 0xFF, (stub_addr >> 8) & 0xFF,  # JP stub_addr
#     ])
    base = stub_addr - system.RAM_START
    for i, b in enumerate(stub):
        system.ram[(base + i) % len(system.ram)] = b

    system.pc = stub_addr
    dbg = False
    # Run the stub
    # Using larger chunks and stop_pc for much better performance
    stop_pc = stub_addr + 0x0B
    max_total_steps = 1000000
    total_steps = 0
    
    print(f"Running stub at 0x{stub_addr:04X} until 0x{stop_pc:04X}...")
    while total_steps < max_total_steps:
        # Run up to 2000 cycles or until stop_pc
        system.step(4000, stop_pc=stop_pc)
        #total_steps +=1
        #system.step(1,stop_pc=stop_pc)
        # Periodic display update
        system.update_display()
        #if total_steps%update_step==0:
        #    system.update_display()
        
        if system.pc == stop_pc:
            print(f"Reached stop_pc: 0x{system.pc:04X}")
            break
            
        total_steps += 2000
#         if total_steps % 10000 == 0:
#             print(".", end="")
    print()
    elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ms)
    print(f"cpu execute about {total_steps} cycles.")
    print(f"display update every {update_step} steps.")
    print(f"run_outac_once elapsed: {elapsed_ms} ms ({elapsed_ms / 1000:.3f} s)")


def main():
    print("OUTAC real LCD test start")
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
    
    print("dump vrams")
    system.dump_edtop_vram()
    system.dump_ledtp_vram()
    system.lcd.dump_vram()
    print("save lcd.vram to pbm")
    system.lcd.save_pbm("lcd_dump.pbm")
    nonzero = sum(1 for b in system.lcd.vram if b)
    print("VRAM non-zero bytes:", nonzero)
    print("OUTAC test finished")

    # Keep visible
    #while True:
    #    time.sleep_ms(200)


if __name__ == "__main__":
    main()
