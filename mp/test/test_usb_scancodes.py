import time
import usb_host

def main():
    print("========================================")
    print(" USB Keyboard Raw Scancode Viewer")
    print("========================================")
    print("This script simply prints the raw USB HID scancodes received")
    print("from the keyboard. Use this to determine the scancode of")
    print("unmapped or special keys for emulator integration.")
    print("========================================\n")
    
    try:
        usb_host.init()
        print("[System] usb_host initialized successfully.")
    except Exception as e:
        print(f"[Error] Failed to initialize usb_host: {e}")
        return

    print("\n[Status] Press any key. Press 'Ctrl+C' to stop.\n")
    
    try:
        while True:
            usb_host.task()
            events = usb_host.get_keyboard_events()
            
            for scancode, is_pressed in events:
                if is_pressed:
                    print(f"[-] Key PRESSED  -> Scancode: Hex=0x{scancode:02X} Dec={scancode}")
                else:
                    print(f"[ ] Key RELEASED -> Scancode: Hex=0x{scancode:02X} Dec={scancode}")
            
            time.sleep_ms(10)
            
    except KeyboardInterrupt:
        print("\n[System] Test stopped by user.")

if __name__ == "__main__":
    main()
