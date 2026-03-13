import time
import usb_host

# TinyUSB HID Keycode Map
HID_KEYCODES = {
    0x04: 'a', 0x05: 'b', 0x06: 'c', 0x07: 'd', 0x08: 'e', 0x09: 'f', 0x0A: 'g', 0x0B: 'h',
    0x0C: 'i', 0x0D: 'j', 0x0E: 'k', 0x0F: 'l', 0x10: 'm', 0x11: 'n', 0x12: 'o', 0x13: 'p',
    0x14: 'q', 0x15: 'r', 0x16: 's', 0x17: 't', 0x18: 'u', 0x19: 'v', 0x1A: 'w', 0x1B: 'x',
    0x1C: 'y', 0x1D: 'z',
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5', 0x23: '6', 0x24: '7', 0x25: '8',
    0x26: '9', 0x27: '0',
    0x28: 'Enter', 0x29: 'Escape', 0x2A: 'Backspace', 0x2B: 'Tab', 0x2C: 'Space',
    0x2D: '-', 0x2E: '=', 0x2F: '[', 0x30: ']', 0x31: '\\', 0x32: '#', 0x33: ';', 0x34: "'",
    0x35: '`', 0x36: ',', 0x37: '.', 0x38: '/', 0x39: 'CapsLock',
    0x3A: 'F1', 0x3B: 'F2', 0x3C: 'F3', 0x3D: 'F4', 0x3E: 'F5', 0x3F: 'F6',
    0x40: 'F7', 0x41: 'F8', 0x42: 'F9', 0x43: 'F10', 0x44: 'F11', 0x45: 'F12',
    0x46: 'PrtSc', 0x47: 'ScrollLock', 0x48: 'Pause', 0x49: 'Insert', 0x4A: 'Home',
    0x4B: 'PgUp', 0x4C: 'Delete', 0x4D: 'End', 0x4E: 'PgDn',
    0x4F: 'Right', 0x50: 'Left', 0x51: 'Down', 0x52: 'Up',
    # Modifiers
    0xE0: 'L_Ctrl', 0xE1: 'L_Shift', 0xE2: 'L_Alt', 0xE3: 'L_GUI',
    0xE4: 'R_Ctrl', 0xE5: 'R_Shift', 0xE6: 'R_Alt', 0xE7: 'R_GUI'
}

def main():
    print("========================================")
    print(" USB Host Keyboard Test (Native OTG)")
    print("========================================")
    print("Hardware Setup:")
    print("1. Use a Raspberry Pi Pico / Pico 2.")
    print("2. Connect a USB keyboard to the microUSB/USB-C port.")
    print("3. MUST use an OTG adapter (VBUS must be supplied).")
    print("========================================\n")
    
    try:
        print("[System] Initializing USB Host...")
        usb_host.init()
        print("[System] USB Host initialized successfully.")
    except Exception as e:
        print(f"[Error] Failed to initialize usb_host: {e}")
        return

    print("\n[Status] Waiting for keyboard events. Press 'Ctrl+C' to stop.")
    
    # Set to track currently pressed keys
    pressed_keys = set()
    
    try:
        while True:
            # Poll TinyUSB host task
            usb_host.task()
            
            # Retrieve keyboard events (scancode, is_pressed)
            events = usb_host.get_keyboard_events()
            
            for scancode, is_pressed in events:
                key_name = HID_KEYCODES.get(scancode, f"Unknown(0x{scancode:02X})")
                
                if is_pressed:
                    pressed_keys.add(key_name)
                    print(f"[DN] {key_name:10} (0x{scancode:02X}) | Active: {list(pressed_keys)}")
                else:
                    pressed_keys.discard(key_name)
                    print(f"[UP] {key_name:10} (0x{scancode:02X}) | Active: {list(pressed_keys)}")
            
            # Efficient polling delay
            time.sleep_ms(1)
            
    except KeyboardInterrupt:
        print("\n[System] Test stopped by user.")
    except Exception as e:
        print(f"\n[Error] Runtime error: {e}")

if __name__ == "__main__":
    main()
