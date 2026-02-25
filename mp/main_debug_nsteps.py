"""
PB-1000 Emulator - Main Entry Point for Raspberry Pi Pico 2
"""
import machine
import time
from ili9341 import ILI9341
from pb1000 import PB1000System

# ---- Hardware Pin Configuration ----
SPI_ID   = 1
SCK_PIN  = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN   = 9
DC_PIN   = 8
RST_PIN  = 7
BL_PIN   = 22

def init_display():
    """Initialize ILI9341 TFT display."""
    spi = machine.SPI(SPI_ID, baudrate=40_000_000,
                      sck=machine.Pin(SCK_PIN),
                      mosi=machine.Pin(MOSI_PIN),
                      miso=machine.Pin(MISO_PIN))
    cs  = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc  = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    bl  = machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)  # Black background
    return display

def draw_bezel(display):
    """Draw PB-1000 style bezel around LCD area."""
    # LCD viewport: 192*2=384 -> scale to fit 320px, use x=16, y=40
    # Bezel border
    display.fill_rect(12, 36, 296, 72, 0x4228)  # Dark gray bezel
    display.fill_rect(14, 38, 292, 68, 0x8410)  # Medium gray inner
    display.fill_rect(16, 40, 288, 64, 0xB5E6)  # LCD background (olive-green)

system = None  # Global instance

def debug_print_vram():
    """Dump first few bytes of VRAM to check if anything is drawn."""
    if system:
        print("VRAM[0:16]:", [hex(b) for b in system.lcd.vram[:16]])
        print("Page 0 Col 0:", system.lcd.vram[0])

def debug_trace(count=20):
    """Trace CPU execution for a number of steps."""
    if not system:
        print("System not initialized.")
        return
    
    print(f"Tracing {count} steps...")
    for i in range(count):
        pc = system.pc
        system.step(1)
        print(f"PC: {hex(pc)}")

def list_files():
    """List files in root directory."""
    import os
    print("Files in /:")
    print(os.listdir('/'))
    if 'roms' in os.listdir('/'):
        print("Files in /roms:")
        print(os.listdir('/roms'))

def dump_mem(addr, length=16):
    """Dump memory (words) starting at byte addr."""
    if not system:
        print("System not initialized.")
        return
    
    print(f"Dump at {hex(addr)}:")
    for i in range(0, length * 2, 2):
        a = addr + i
        # Read low byte and high byte at the byte address
        low = system._mem_read(0, a)
        high = system._mem_read(0, a + 1)
        val = (high << 8) | low
        print(f"{hex(a)}: {hex(val)}")

import sys
import uselect

def poll_keyboard(system):
    """Poll stdin for characters and send to emulator."""
    spoll = uselect.poll()
    spoll.register(sys.stdin, uselect.POLLIN)
    
    if spoll.poll(0):
        char = sys.stdin.read(1)
        # Map some common keys to PB-1000 keys
        # For this test, we just send alpha/numeric
        if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char in " .+-*/=":
            print(f"Key Press: {char}")
            system.press_key(char)
            # Immediate release for simplicity in this test
            # In a real system we might hold it for a few frames
            system.step(1000)
            system.release_key(char)
        elif char == '\x1b': # ESC -> MENU/MODE
            print("Key Press: MODE")
            system.press_key((6, 2))
            system.step(1000)
            system.release_key((6, 2))

def main():
    global system
    print("PB-1000 Emulator Starting...")

    # Initialize display
    display = init_display()
    try:
        draw_bezel(display)
    except:
        pass

    # Initialize system
    system = PB1000System(display)

    # Load ROMs
    try:
        system.load_rom('/roms/rom0.bin', slot=0)
        system.load_rom('/roms/rom1.bin', slot=1)
    except Exception as e:
        print(f"ROM load warning: {e}")

    print(f"System initialized. PC={system.pc:#06x}")
    print("Interactive Mode: Type in REPL to send keys (ESC for MENU).")

    # Timer for 1-second ticks
    last_tick = time.ticks_ms()
    frame_time = time.ticks_ms()
    FRAME_INTERVAL = 100  # ~10fps is enough for this LCD

    # Main emulation loop
    try:
        steps = int(input("How many steps?>"))
        step_count = 1
        while True:
            # 1. Execute CPU
            if not system.is_sleeping:
                # Execute exactly 1 second's worth of cycles per real second?
                # For now, just run 20k cycles per loop
                print(f"{step_count} ",end="")
                if step_count < steps:
                    system.debug_step(False)
                else:
                    system.debug_step(True)
                step_count += 1
            # else:
            #     time.sleep_ms(10)

            # 2. Poll Input
            # poll_keyboard(system)

            # 3. Update display
            # now = time.ticks_ms()
            # if time.ticks_diff(now, frame_time) >= FRAME_INTERVAL:
            #     system.update_display(x_offset=16, y_offset=40)
            #     frame_time = now

            # # 4. 1-second timer tick
            # if time.ticks_diff(now, last_tick) >= 1000:
            #     system.tick_timer()
            #     last_tick = now

            # # Yield
            # time.sleep_ms(1)
        system.update_display(x_offset=16, y_offset=40)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")

if __name__ == '__main__':
    main()

