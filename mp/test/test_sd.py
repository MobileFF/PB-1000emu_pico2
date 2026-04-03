import machine
import os
import time
from sdcard import SDCard

# SPI1 Pins (Same as pb1000.py)
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
SD_CS_PIN = 15  # New pin for SD CS

def test_sd():
    print("--- SD Card Unit Test Start ---")
    
    # 1. SPI/CS Initialization
    try:
        # Initial SPI speed for SD card should be low (e.g., 400kHz)
        spi = machine.SPI(1, baudrate=400000, sck=machine.Pin(SCK_PIN), mosi=machine.Pin(MOSI_PIN), miso=machine.Pin(MISO_PIN))
        
        # Ensure all CS pins on the module are HIGH (disabled) except SD
        lcd_cs = machine.Pin(9, machine.Pin.OUT, value=1)
        touch_cs = machine.Pin(16, machine.Pin.OUT, value=1)
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        
        print(f"SPI1 and CS pins initialized. (LCD_CS=9, T_CS=16, SD_CS={SD_CS_PIN})")
    except Exception as e:
        print(f"ERROR: SPI initialization failed: {e}")
        return

    # 2. SD Card Object
    try:
        sd = SDCard(spi, sd_cs, baudrate=400000)
        sectors = sd.sectors if hasattr(sd, "sectors") else sd.ioctl(4, 0)
        print(f"SD Card object created. Sectors: {sectors}")
    except Exception as e:
        print(f"ERROR: SD Card init failed: {e}")
        print("Check your wiring (CS, SCK, MOSI, MISO) and ensure the card is inserted.")
        return

    # 3. Low-level block read test (Directly call readblocks before mounting)
    try:
        print("Testing direct block read (Sector 0)...")
        buf = bytearray(512)
        sd.readblocks(0, buf)
        print("Direct block read SUCCESS!")
        print("First 16 bytes:", " ".join(f"{b:02X}" for b in buf[:16]))
    except Exception as e:
        print(f"ERROR: Direct block read failed: {e}")
        # Continue to mount attempt anyway for completeness
    
    # 4. Mount
    vfs_path = "/sd"
    try:
        vfs = os.VfsFat(sd)
        os.mount(vfs, vfs_path)
        print(f"SD Card mounted at {vfs_path}")
    except Exception as e:
        print(f"ERROR: Mounting failed: {e}")
        return

    # 5. Directory Creation (renumbered)
    test_dir = vfs_path + "/test_dir"
    try:
        print(f"Creating directory: {test_dir}")
        if "test_dir" not in os.listdir(vfs_path):
            os.mkdir(test_dir)
        print("Directory exists/created.")
    except Exception as e:
        print(f"ERROR: Directory creation failed: {e}")

    # 5. File Write/Read
    test_file = test_dir + "/hello.txt"
    test_content = "Hello PB-1000 Emulator SD Storage! " + str(time.ticks_ms())
    try:
        print(f"Writing to: {test_file}")
        with open(test_file, "w") as f:
            f.write(test_content)
        
        print(f"Reading back from: {test_file}")
        with open(test_file, "r") as f:
            read_content = f.read()
        
        print(f"Content: {read_content}")
        if read_content == test_content:
            print("SUCCESS: File R/W match!")
        else:
            print("FAILED: File R/W mismatch!")
    except Exception as e:
        print(f"ERROR: File R/W failed: {e}")

    # 6. List Files
    try:
        print(f"Files in {vfs_path}:")
        print(os.listdir(vfs_path))
    except Exception as e:
        print(f"ERROR: listdir failed: {e}")

    print("--- SD Card Unit Test End ---")
    print("If all above was SUCCESS, the hardware/driver is working correctly.")

if __name__ == "__main__":
    test_sd()
