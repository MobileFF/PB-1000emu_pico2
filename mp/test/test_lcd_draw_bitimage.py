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
from pb1000 import PB1000System,init_display,draw_bezel

# ---- Hardware Pin Configuration (same as main.py) ----
SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22

def run_draw_bitimage_once(system,update_step=100):
    #input("press enter to go")
    start_ms = time.ticks_ms()
    # Warm-up a little so BIOS work areas are initialized.
    print("Initialize")
    system.set_debug(False)
    system.step(40000)
    #system.set_debug(True)

    print("Test Start")

    stub_addr = 0x7000
    # DRAW BITIMAGE test
    stub = bytes([
        0x54, 0x00, 0xDF,        # 7000:PPO     &HDF    ;Select command register
        0x42, 0x00, 0x14,        # 7003:LD      $0,&H14 ;Set 'not OLD and NEW write','DISPLAY ON/OFF', LCD1 select
        0x12, 0x00,              # 7006:STL     $0      ;Store LCD command register & address(3byte)
        0x54, 0x00, 0xDF,        # 7008:PPO     &HDF    ;Select command register
        0x42, 0x00, 0x82,        # 700B:LD      $0,&H82 ;Set 'DRAW BITIMAGE','OVERWRITE', LCD1 select
        0x42, 0x01, 0x00,        # 700E:LD      $1,0    ;Set LCD address(columns=(48/16)=3)
        0x42, 0x02, 0x00,        # 7011:LD      $2,0    ;Set LCD address(line=0)
        0xD2, 0x00, 0x40,        # 7014:STLM    $0,3    ;Store LCD command register & address(3byte)
        0x42, 0x03, 0x01,        # 7017:LD      $3,&H01 ;Set data '&HXX'
        0x42, 0x04, 0x03,        # 701A:LD      $4,&H03 ;Set data '&HXX'
        0x42, 0x05, 0x07,        # 701D:LD      $5,&H07 ;Set data '&HXX'
        0x42, 0x06, 0x0F,        # 7020:LD      $6,&H0F ;Set data '&HXX'
        0x42, 0x07, 0x1F,        # 7023:LD      $7,&H1F ;Set data '&HXX'
        0x42, 0x08, 0x3F,        # 7026:LD      $8,&H3F ;Set data '&HXX'
        0x54, 0x00, 0xDE,        # 7029:PPO     &HDE    ;Select data ram(same as 'PPO &HC6')
        0xD2, 0x03, 0xA0,        # 702C:STLM    $3,6    ;Display LCD( 8bit data *6 )
        0xF7, 0xF8, 0xF8,        # 702F:RTN
    ])
    base = stub_addr - system.RAM_START
    for i, b in enumerate(stub):
        system.ram[(base + i) % len(system.ram)] = b

    system.dump_mem_range(0x7000,0x702F)

    system.pc = stub_addr
    # dbg = False
    # Run the stub
    # Using larger chunks and stop_pc for much better performance
    stop_pc = stub_addr + 0x2F
    max_total_steps = 1000000
    total_steps = 0
    
    print(f"Running stub at 0x{stub_addr:04X} until 0x{stop_pc:04X}...")
    while total_steps < max_total_steps:
        # Run up to 2000 cycles or until stop_pc
        steps = system.step(100, stop_pc=stop_pc)
        # steps=1
        # system.debug_step()
        total_steps += steps
        if total_steps % 10000 == 0:
            print(".", end="")
        #total_steps +=1
        #system.step(1,stop_pc=stop_pc)
        # Periodic display update
        system.update_display()
        #if total_steps%update_step==0:
        #    system.update_display()
        
        if system.pc == stop_pc:
            print(f"Reached stop_pc: 0x{system.pc:04X}")
            break
            
    print("-----")
    elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ms)
    print(f"cpu execute about {total_steps} cycles.")
    print(f"display update every {update_step} steps.")
    print(f"run_draw_bitimage_once elapsed: {elapsed_ms} ms ({elapsed_ms / 1000:.3f} s)")


def main():
    print("DRAW BITIMAGE LCD test start")
    ret = init_display()
    if isinstance(ret, tuple) and len(ret) >= 2:
        display = ret[0]
        touch = ret[1]
    else:
        display = ret
        touch = None
    #display.fill_rect(0, 0, 320, 240, 0xC618)
    draw_bezel(display)
    
    if hasattr(display, 'lcd_sync'):
        display.lcd_sync()
#     display = init_display()
#     draw_bezel(display)

    # debug=True to see CPU/LCD logs while testing
    system = PB1000System(display=display)
    system.lcd.set_display_scale(1.5)
    #system.lcd.setup_display(spi_id=1, cs_pin=9, dc_pin=8, scale=1, x_offset=16, y_offset=40)
    system.load_rom("/roms/rom0.bin", slot=0)
    system.load_rom("/roms/rom1.bin", slot=1)
    system.lcd.lcd_ctrl(0xDF) # OP=1, CE=3 (Both chips)
    system.lcd.lcd_write(0x14)
    system.lcd.lcd_ctrl(0xDE) # OP=0
    #system.lcd.lcd_ctrl(system.lcd.CMD_DISPLAY_ON)
    
    #update_step = int(input("update display step?>"))

    #run_outac_once(system,update_step)
    run_draw_bitimage_once(system)
    #system.update_display(x_offset=16, y_offset=40)
    print("update display")
    system.update_display()
    
    print("dump vrams")
    system.dump_edtop_vram()
    system.dump_ledtp_vram()
    #system.lcd.dump_vram()
    print("save lcd.vram to pbm")
    system.lcd.save_pbm("lcd_dump.pbm")
    nonzero = sum(1 for b in system.lcd.vram if b)
    print("VRAM non-zero bytes:", nonzero)
    print("DRAW BITIMAGE test finished")

    # Keep visible
    #while True:
    #    time.sleep_ms(200)


if __name__ == "__main__":
    main()


