"""
PIO-USB keyboard test for the RP2350-PiZero header wiring (GP22=D+, GP23=D-).

Requires firmware/firmware_pb1000_pizero_gp22.uf2 (`bash bldfrm.sh pizero
gp22`). With the regular pizero firmware (GP28) the
wiring check below still works, but no keys will arrive; step 3 reports
which pin pair the running firmware actually drives.

Usage (do not use `mpremote exec` -- run the file):
    mpremote connect <port> run mp/test/pio_usb_gp22_test.py

Step 1 reads GP22/GP23 with pull-downs before the USB host starts. With a
keyboard plugged in, its own 1.5k pull-up makes one line read 1:
    D+ (GP22) = 1 -> full-speed device, D- (GP23) = 1 -> low-speed device.
Both 0 means no device / no VBUS / wiring problem; both 1 means a short.

Step 2 sets clk_sys to 252MHz (as dvi_display.init() does in main.py),
starts the USB host, calls usb_host.task() from this loop and prints
key names as they arrive, plus the usb_host.debug() counters whenever they
change (attach/detach, enumeration errors).
"""
import time

import machine
import hd61700
import usb_host

DP_PIN = 22
DM_PIN = 23

print("PIO-USB keyboard test: D+ = GP%d, D- = GP%d" % (DP_PIN, DM_PIN))
print()

# --- Step 1: line-level wiring check -------------------------------------
dp = machine.Pin(DP_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)
dm = machine.Pin(DM_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)
time.sleep_ms(20)
lv_dp, lv_dm = dp.value(), dm.value()
print("[1] line levels: D+(GP%d)=%d  D-(GP%d)=%d" % (DP_PIN, lv_dp, DM_PIN, lv_dm))
if lv_dp and not lv_dm:
    print("    -> full-speed device detected")
elif lv_dm and not lv_dp:
    print("    -> low-speed device detected (typical keyboard)")
elif lv_dp and lv_dm:
    print("    -> both high: check for a D+/D- short or wrong wiring")
else:
    print("    -> no device: check keyboard, 5V/GND and D+/D- wiring")
print()

# --- Step 2: USB host + key input ----------------------------------------
try:
    import keymap
    get_label = keymap.get_label
except (ImportError, AttributeError):
    get_label = lambda sc: "SC:0x%02X" % sc

# PIO-USB needs clk_sys to be a multiple of 12MHz. On pizero, main.py gets
# 252MHz from dvi_display.init() and usb_host.init() leaves the clock alone,
# so a standalone run would otherwise stay at the 150MHz power-on default.
CLK_MHZ = 252
machine.freq(CLK_MHZ * 1_000_000)
print("[2] clk_sys = %d MHz" % (machine.freq() // 1_000_000))
usb_host.init()
print("    usb_host.init() done")

# --- Firmware check: which pins did usb_host.init() hand to the PIO? ------
# IO_BANK0 GPIOn_CTRL FUNCSEL (bits 4:0) on RP2350: 6/7/8 = PIO0/1/2,
# 5 = SIO, 31 = NULL. PIO-USB sets FUNCSEL on its D+/D- pins, so this shows
# which pin pair the running firmware was built for.
_IO_BANK0 = 0x40028000
_FUNC = {5: "SIO", 6: "PIO0", 7: "PIO1", 8: "PIO2", 31: "NULL"}


def _funcsel(pin):
    return machine.mem32[_IO_BANK0 + 8 * pin + 4] & 0x1F


def _is_pio(pin):
    return _funcsel(pin) in (6, 7, 8)


print("[3] firmware check: board = %s" % __import__("sys").implementation._machine)
for p in (DP_PIN, DM_PIN, 28, 29):
    f = _funcsel(p)
    print("    GP%d FUNCSEL = %d (%s)" % (p, f, _FUNC.get(f, "?")))
if _is_pio(DP_PIN) and _is_pio(DM_PIN):
    print("    -> OK: PIO-USB is on GP%d/GP%d" % (DP_PIN, DM_PIN))
elif _is_pio(28) and _is_pio(29):
    print("    -> NG: this firmware uses GP28/GP29 (onboard USB-PIO connector)")
    print("       flash firmware/firmware_pb1000_pizero_gp22.uf2")
else:
    print("    -> NG: PIO-USB pins not found on GP%d/%d or GP28/29" % (DP_PIN, DM_PIN))
print()
print("    press keys (Ctrl-C to stop)")
print("    debug: (tick, connected, suspended, ints, attach, detach,")
print("            ep_complete, ep_stalled, ep_error)")

last_dbg = None
last_ms = time.ticks_ms()
keys = 0
try:
    while True:
        usb_host.task()
        sc = hd61700.get_last_key()
        if sc >= 0:
            keys += 1
            print("key: %-12s 0x%02X" % (get_label(sc), sc))
        now = time.ticks_ms()
        if time.ticks_diff(now, last_ms) >= 1000:
            last_ms = now
            dbg = usb_host.debug()
            # tick (index 0) and ep_complete (6) climb continuously; report
            # only when the connection/error state changes.
            state = dbg[1:6] + dbg[7:]
            if state != last_dbg:
                last_dbg = state
                print("debug:", dbg)
                if dbg[8]:
                    print("debug3:", usb_host.debug3(), "debug4:", usb_host.debug4())
except KeyboardInterrupt:
    print()
    print("stopped. keys received: %d, last debug: %s" % (keys, usb_host.debug()))
