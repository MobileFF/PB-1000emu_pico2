"""
PIO-USB host standalone bring-up test (GP4=D+, GP5=D-).

Verifies the PIO-USB keyboard host path in isolation, WITHOUT loading
main.py or any of the rest of the emulator (no ROM, no LCD, no boot
sequence) -- requires a firmware built with USE_PIO_USB=ON
(src/micropython.cmake; see src/usb_host/pio_usb/PROVENANCE.md and
src/usb_host_core.c's usb_host_core_init_pio_usb()).

Not named test_*.py on purpose: this is an interactive, infinite-loop
hardware bring-up script meant to be run directly via mpremote against
real hardware, not a pytest-collected assertion test (there is no
`machine` mock here, and no USB keyboard attached in CI).

Usage:
    mpremote connect <port> run mp/test/pio_usb_bringup.py

Wiring: USB-A connector D+ -> GP4, D- -> GP5, plus 5V/GND to the
connector (see references/gpio_pin_map.md). Reuses the same
hd61700.get_last_key() (read-and-clear) API the real keyboard pipeline
already uses, so a successful key print here means the PIO-USB HCD
layer (src/usb_host/hcd_pio_usb_pb1000.c) is delivering real HID
reports into the exact same path main.py's keyboard handling consumes
-- nothing new to re-verify once this works.

2026-10-05: calls usb_host.task() (-> tuh_task()) directly from this
loop instead of usb_host.start_bg_timer()'s hardware-alarm-ISR-driven
polling. usb_host_core.c's existing bg_timer runs tuh_task() from a
repeating_timer callback, which executes in hardware alarm ISR
context -- the same priority level as the 1ms SOF alarm that drives
pio_usb_host_frame(). A sibling PIO-USB bring-up (same library, ported
from msx_emu_pico2) hit an enumeration hang tracked to exactly this
(tuh_task() called from IRQ context) and fixed it by moving tuh_task()
to the normal main loop instead; this script does the same as the
first thing to try for this project's own "enumeration starts, 5 OK
transfers, then 4 XFER_RESULT_FAILED, then permanent silence" symptom.
"""
import time

import hd61700
import usb_host

print("PIO-USB host bring-up test")
print("  D+ = GP4, D- = GP5 (see references/gpio_pin_map.md)")
print("  Plug in a USB keyboard and press keys; scancodes print below.")
print("  Ctrl-C to stop.")
print()

usb_host.init()
print("usb_host.init() done -- calling usb_host.task() from this main")
print("loop (NOT start_bg_timer(), to keep tuh_task() out of ISR")
print("context) -- waiting for keys...")
print("debug(): (tick_count, connected, suspended, ints,")
print("         attach_count, detach_count, ep_complete_count,")
print("         ep_stalled_count, ep_error_count)")
print("debug2(): (last_complete_ep, last_error_ep) -- each is")
print("          (dev_addr<<8)|ep_num, 0xFFFFFFFF = none yet;")
print("          ep_num 0x00 = EP0/control (enumeration itself),")
print("          e.g. 0x81 = a typical keyboard interrupt-IN endpoint.")
print("debug3(): (setup_send_count, last_setup_word,")
print("           port_reset_count, port_reset_end_count, is_fullspeed) --")
print("          last_setup_word = (bmRequestType<<24)|(bRequest<<16)|wLength;")
print("          bRequest: 0=GET_STATUS 5=SET_ADDRESS 6=GET_DESCRIPTOR")
print("          9=SET_CONFIGURATION -- shows which control request is")
print("          associated with the EP0 failures above. is_fullspeed:")
print("          1=full-speed (12Mbit), 0=low-speed (1.5Mbit) detected.")
print("debug4(): (count, (event0, event1, ...)) -- chronological EP0")
print("          event history (last 8), each packed as")
print("          (result<<28)|(ep_num<<16)|(data_id<<8)|dev_addr.")
print("          result: 1=SUCCESS 2=STALLED 3=ERROR;")
print("          data_id: 0x2D=SETUP stage, 1=DATA1, 0=DATA0.")
print(" printed every ~1s -- tick_count should climb steadily even with")
print(" nothing plugged in; connected should flip to 1 and attach_count")
print(" should increment once a device is detected; ep_complete_count")
print(" increments once HID reports actually start arriving.")
print()

try:
    last_debug_ms = time.ticks_ms()
    while True:
        usb_host.task()
        scancode = hd61700.get_last_key()
        if scancode >= 0:
            print("scancode:", hex(scancode))
        now = time.ticks_ms()
        if time.ticks_diff(now, last_debug_ms) >= 1000:
            last_debug_ms = now
            print("debug:", usb_host.debug(), "debug2:", usb_host.debug2(),
                  "debug3:", usb_host.debug3())
            print("debug4:", usb_host.debug4())
except KeyboardInterrupt:
    print("stopped.")
