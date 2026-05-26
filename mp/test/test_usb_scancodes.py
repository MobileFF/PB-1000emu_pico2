import time
import usb_host
import hd61700
import machine
import os

# UART0を起動し、REPLをUARTに複製する設定
uart = machine.UART(0, baudrate=115200, tx=machine.Pin(0), rx=machine.Pin(1), txbuf=4)
os.dupterm(uart)

def main():
    print("========================================")
    print(" USB Keyboard Raw Scancode Viewer")
    print("========================================")
    print("Press any key to see its raw USB HID scancode.")
    print("Ctrl+C to stop.")
    print("========================================\n")

    try:
        usb_host.init()
        usb_host.start_bg_timer(8)
        print("[System] usb_host initialized.")
    except Exception as e:
        print(f"[Error] Failed to initialize usb_host: {e}")
        return

    print("\n[Status] Waiting for key presses...\n")

    try:
        while True:
            sc = hd61700.get_last_key()
            if sc >= 0:
                print(f"Key PRESSED  -> Hex=0x{sc:02X}  Dec={sc}")
            time.sleep_ms(10)

    except KeyboardInterrupt:
        print("\n[System] Test stopped by user.")
    finally:
        try:
            usb_host.stop_bg_timer()
        except Exception:
            pass

if __name__ == "__main__":
    main()
