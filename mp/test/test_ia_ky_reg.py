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
import pb1000
from pb1000 import PB1000System
from main import poll_keyboard

# ---- Hardware Pin Configuration (same as main.py) ----
SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22

# ---- Auto key injection for IA/KY test ----
# Every AUTO_KEY_PERIOD_STEPS instructions, press one key from AUTO_KEYS.
AUTO_KEY_ENABLED = True
AUTO_KEYS = ["1", "2", "3", "+", "EXE"]
AUTO_KEY_PERIOD_STEPS = 300
AUTO_KEY_HOLD_STEPS = 40


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


def _resolve_auto_key(token):
    if token == "EXE":
        return (10, 4), "EXE"
    return token, str(token)


def run_outac_once(system):
    # Warm-up a little so BIOS work areas are initialized.
    print("Initialize")
    system.set_debug(False)
    system.step(40000)
    system.set_debug(True)

    print("Test Start")
    stub_addr = 0x7000
    # IA/KY register check
    stub = bytes([
            0x57,0x00,0xBD,      # PST IA,&HBD
            0x9F,0x60,           # GRE KY,$0
            0xD1,0x02,0xFF,0xF0, # AD  $16,1
            0x84,0x60,0x02,      # ANCW $0,$2
            0xB0,0x8D,           # JR  N,MAIN
            0xF7,                # RTN     
    ])
    base = stub_addr - system.RAM_START
    for i, b in enumerate(stub):
        system.ram[(base + i) % len(system.ram)] = b

    system.set_pc(stub_addr)
    i = 0
    auto_idx = 0
    auto_active_key = None
    auto_release_at = -1
    next_auto_at = AUTO_KEY_PERIOD_STEPS
    while True:
        #print(f"{i}:",end="")
        #system.debug_step(dbg)
        system.debug_step(pause=False,trace=False)
        if system.pc == stub_addr+0xE:
        #if system.pc == stub_addr:
            break
        #if system.pc == 0x294:
        #    dbg = True
#         if (i+1)%200==0:
#             print("update display")
#             system.update_display()
        poll_keyboard(system)

        if AUTO_KEY_ENABLED:
            if auto_active_key is not None and i >= auto_release_at:
                system.release_key(auto_active_key)
                print(f"[AUTO] release {AUTO_KEYS[auto_idx - 1 if auto_idx > 0 else len(AUTO_KEYS) - 1]}")
                auto_active_key = None

            if auto_active_key is None and i >= next_auto_at and AUTO_KEYS:
                token = AUTO_KEYS[auto_idx]
                key, label = _resolve_auto_key(token)
                system.press_key(key)
                auto_active_key = key
                auto_release_at = i + AUTO_KEY_HOLD_STEPS
                next_auto_at = i + AUTO_KEY_PERIOD_STEPS
                auto_idx = (auto_idx + 1) % len(AUTO_KEYS)
                print(f"[AUTO] press {label} (step={i})")
        i+=1
        if i%10000==0:
            print("end")
            break

    if AUTO_KEY_ENABLED and auto_active_key is not None:
        system.release_key(auto_active_key)
    print()


def main():
    print("IA/KY test start")
    display = init_display()
    draw_bezel(display)

    # debug=True to see CPU/LCD logs while testing
    system = PB1000System(display=display, debug=True)
    system.key_interrupt_via_scan = True
    print("KEY_INT mode: IA/KY scan-gated")
    #system.lcd.setup_display(spi_id=1, cs_pin=9, dc_pin=8, scale=1, x_offset=16, y_offset=40)
    system.load_rom("/roms/rom0.bin", slot=0)
    system.load_rom("/roms/rom1.bin", slot=1)

    run_outac_once(system)
    #system.update_display(x_offset=16, y_offset=40)
    print("update display")
    system.update_display()
    
    print("test finished")

    # Keep visible
    #while True:
    #    time.sleep_ms(200)

if __name__ == "__main__":
    main()


