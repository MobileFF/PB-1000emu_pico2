import time
import usb_host

# TinyUSB HID Keycode Map (Basic subset for testing)
HID_KEYCODES = {
    0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F', 0x0A: 'G', 0x0B: 'H',
    0x0C: 'I', 0x0D: 'J', 0x0E: 'K', 0x0F: 'L', 0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P',
    0x14: 'Q', 0x15: 'R', 0x16: 'S', 0x17: 'T', 0x18: 'U', 0x19: 'V', 0x1A: 'W', 0x1B: 'X',
    0x1C: 'Y', 0x1D: 'Z',
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5', 0x23: '6', 0x24: '7', 0x25: '8',
    0x26: '9', 0x27: '0',
    0x28: 'Enter', 0x29: 'Escape', 0x2A: 'Backspace', 0x2B: 'Tab', 0x2C: 'Space', 
    0x4F: 'Right', 0x50: 'Left', 0x51: 'Down', 0x52: 'Up',
    
    # Modifiers
    0xE0: 'Left Ctrl', 0xE1: 'Left Shift', 0xE2: 'Left Alt', 0xE3: 'Left GUI',
    0xE4: 'Right Ctrl', 0xE5: 'Right Shift', 0xE6: 'Right Alt', 0xE7: 'Right GUI'
}

def main():
    print("Initialize USB Keyboard Host (PIO-USB)")
    print("Make sure D+ is connected to GP2, and D- is connected to GP3.")
    
    try:
        usb_host.init()
        print("usb_host initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize usb_host: {e}")
        return

    print("\nWaiting for USB keyboard events. Press 'Ctrl+C' to stop.")
    
    try:
        while True:
            # Requires constant polling
            usb_host.task()
            
            # Fetch batched keyboard events from the C module
            events = usb_host.get_keyboard_events()
            
            for scancode, is_pressed in events:
                key_name = HID_KEYCODES.get(scancode, f"Unknown (0x{scancode:02X})")
                action = "PRESSED" if is_pressed else "RELEASED"
                
                print(f"Key Event: {action:8} | Scancode: 0x{scancode:02X} | Key: {key_name}")
            
            # small delay to avoid 100% CPU lock while testing
            time.sleep_ms(10)
            
    except KeyboardInterrupt:
        print("\nTest stopped by user.")

if __name__ == "__main__":
    main()
