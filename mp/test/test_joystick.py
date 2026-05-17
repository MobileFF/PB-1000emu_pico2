"""
Joystick hardware test script
Run standalone on Pico (no emulator required): import test_joystick
"""
import time
from machine import Pin

# GPIO assignments (matches JoystickInputManager.DEFAULT_PIN_MAP)
_BUTTONS = [
    ("UP",    18),
    ("DOWN",  19),
    ("LEFT",  20),
    ("RIGHT", 21),
    ("FIRE1", 26),
    ("FIRE2", 27),
]

DEBOUNCE_MS   = 20
DISPLAY_MS    = 200   # status line refresh interval

def _init_pins():
    return {name: Pin(gpio, Pin.IN, Pin.PULL_UP) for name, gpio in _BUTTONS}

def _raw_state(pins):
    """Returns dict name->bool (True=pressed, active-low)."""
    return {name: (pin.value() == 0) for name, pin in pins.items()}

def _status_line(confirmed):
    """Single-line summary of confirmed button states."""
    parts = []
    for name, _ in _BUTTONS:
        if confirmed.get(name):
            parts.append(f"[{name}]")
        else:
            parts.append(f" {name} ")
    return "  ".join(parts)

def run():
    pins = _init_pins()
    #print(pins)

    print("=== Joystick Test  (Ctrl+C to quit) ===")
    print(f"Pins: " + "  ".join(f"{n}=GP{g}" for n, g in _BUTTONS))
    print("Active Low (GND = ON), PULL_UP\n")
    print("Buttons: " + "  ".join(n for n, _ in _BUTTONS))
    print("-" * 56)

    raw     = {name: 1 for name, _ in _BUTTONS}   # last raw reading (1=open)
    #print(raw)
    deadline = {name: 0 for name, _ in _BUTTONS}   # debounce deadline
    confirmed = {name: False for name, _ in _BUTTONS}  # debounced state

    next_display = time.ticks_ms()

    try:
        while True:
            now = time.ticks_ms()

            # --- debounce poll ---
            for name, pin in pins.items():
                v = pin.value()
                #print(f"[{name}]:{v}")
                if v != raw[name]:
                    raw[name] = v
                    deadline[name] = time.ticks_add(now, DEBOUNCE_MS)

                if time.ticks_diff(now, deadline[name]) < 0:
                    continue  # still in debounce window

                new_on = (raw[name] == 0)
                if new_on == confirmed[name]:
                    continue

                confirmed[name] = new_on
                action = "PRESS  " if new_on else "RELEASE"
                gp = dict(_BUTTONS)[name]
                print(f"  {action}  {name:<6}  GP{gp}  raw={raw[name]}")

            # --- periodic status line ---
            if time.ticks_diff(now, next_display) >= 0:
                next_display = time.ticks_add(now, DISPLAY_MS)
                line = _status_line(confirmed)
                # overwrite current line
                import sys
                sys.stdout.write(f"\r  {line}  ")

            time.sleep_ms(5)

    except KeyboardInterrupt:
        print("\n--- stopped ---")
        print("Final raw readings:")
        for name, pin in pins.items():
            gp = dict(_BUTTONS)[name]
            v = pin.value()
            print(f"  GP{gp:2d} {name:<6} = {v}  ({'PRESS' if v==0 else 'open '})")

run()
