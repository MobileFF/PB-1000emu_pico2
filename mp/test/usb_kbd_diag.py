"""
USB keyboard diagnostic: shows why a keyboard (or a wireless keyboard's
receiver) does or does not produce keys.

Works on every board/build (Pico 2 / Pico 2 W / RP2350-PiZero, PIO-USB or
native USB host). Needs firmware with usb_host.dev_info()/hid_info()/
hid_reports()/setup_log() (2026-11-04 or later).

Usage (do not use `mpremote exec` -- run the file), one keyboard per run:
    mpremote connect <port> reset
    mpremote connect <port> run mp/test/usb_kbd_diag.py

The reset matters: the USB host survives the soft reset `mpremote run`
does, so a second run would show the previous device. Plug the keyboard
(receiver) in after the script starts, wait for the device summary, then
press some keys. Every 5 seconds a status line shows the report counts
(Ctrl-C ends mpremote before the script could print anything more).

What it prints:
  - when the device mounts: VID:PID and every HID interface it exposes
    (protocol keyboard/mouse/none, boot/report mode, report descriptor
    length and bytes, and whether it uses report IDs)
  - every raw HID report, marked "-> keys" if the firmware's keyboard
    handler accepts it (keyboard interface AND 8-byte report) or
    "-> ignored" otherwise
  - if the device attaches but never mounts: the last setup packets, to
    show how far enumeration got
Copy the whole output when reporting a keyboard that does not work.
Some receivers do not work plugged straight into the PIO-USB port but do
work through a USB hub (see doc/hardware_guide.md); try one before
reporting.
"""
import sys
import time

import machine
import usb_host

# clk_sys for the USB host, in kHz. 252000 is what the emulator uses.
USB_KHZ = 252000

# Print a status line this often, in seconds (0 = off): the HID reports so
# far and the failed transfers, which are not printed one by one.
STATUS_S = 5

# Seconds after attach before reporting a device that has not mounted
REPORT_AFTER_S = 5

_PROTO = {0: "none", 1: "keyboard", 2: "mouse"}
_MODE = {0: "boot", 1: "report"}
_DESC_TYPE = {1: "DEVICE", 2: "CONFIG", 3: "STRING", 6: "QUALIFIER",
              0x21: "HID", 0x22: "REPORT"}
_REQ = {0: "GET_STATUS", 5: "SET_ADDRESS", 6: "GET_DESCRIPTOR",
        9: "SET_CONFIGURATION", 0x0A: "SET_IDLE/GET_INTERFACE",
        0x0B: "SET_PROTOCOL/SET_INTERFACE"}


def _hex(b):
    return " ".join("%02X" % x for x in b)


def _report_ids(desc):
    """Report IDs declared in a HID report descriptor (item 0x85)."""
    ids = []
    i = 0
    while i < len(desc):
        prefix = desc[i]
        size = (0, 1, 2, 4)[prefix & 3]
        if prefix == 0x85 and i + 1 < len(desc):
            ids.append(desc[i + 1])
        i += 1 + size
    return ids


def _print_setup_log():
    log = usb_host.setup_log()
    if not log:
        print("  (setup log not available on this build)")
        return
    print("  last setup packets (oldest first):")
    for e in log:
        addr, rt, req = e[0], e[1], e[2]
        wval = e[3] | (e[4] << 8)
        widx = e[5] | (e[6] << 8)
        wlen = e[7] | (e[8] << 8)
        name = _REQ.get(req, "req%d" % req) if (rt & 0x60) == 0 else "class req%d" % req
        extra = ""
        if req == 6 and (rt & 0x60) == 0:
            extra = " %s" % _DESC_TYPE.get(wval >> 8, "type0x%02X" % (wval >> 8))
        print("    addr=%d %-24s%s wValue=0x%04X wIndex=%d wLength=%d"
              % (addr, name, extra, wval, widx, wlen))


def _print_ep0_history():
    """Speed and the result of each recent control-endpoint (EP0) stage
    (PIO-USB only; see usb_host.debug3()/debug4() in src/modusb_host.c)."""
    d3 = usb_host.debug3()
    print("  speed: %s, port resets: %d" % ("full (12M)" if d3[4] else "low (1.5M)", d3[2]))
    print("  debug: %s" % (usb_host.debug(),))
    n, events = usb_host.debug4()
    print("  EP0 results (oldest first, %d total):" % n)
    for ev in events:
        result = (ev >> 28) & 0xF
        data_id = (ev >> 8) & 0xFF
        stage = {0x2D: "SETUP", 1: "DATA1", 0: "DATA0"}.get(data_id, "pid0x%02X" % data_id)
        print("    addr=%d %-5s %s" % (ev & 0xFF, stage,
                                       {1: "OK", 2: "STALL", 3: "ERROR"}.get(result, str(result))))


def _print_device():
    mount, umount, vid, pid = usb_host.dev_info()
    print()
    print("=== device mounted: VID:PID = %04X:%04X (mount #%d)" % (vid, pid, mount))
    itfs = usb_host.hid_info()
    if not itfs:
        print("  no HID interface was opened")
    kbd = False
    for inst, addr, proto, mode, dlen, cnt, desc in itfs:
        ids = _report_ids(desc)
        print("  HID instance %d: protocol=%s mode=%s report_desc=%d bytes%s"
              % (inst, _PROTO.get(proto, str(proto)), _MODE.get(mode, str(mode)),
                 dlen, "" if not ids else " report_ids=%s" % ids))
        print("    desc: %s%s" % (_hex(desc), " ..." if dlen > len(desc) else ""))
        if proto == 1:
            kbd = True
    if kbd:
        print("  -> has a boot keyboard interface; press keys to see its reports")
    else:
        print("  -> NO boot keyboard interface: the firmware ignores this device's keys")
    print()
    return {i[0]: i[2] for i in itfs}


print("USB keyboard diagnostic (board: %s)" % sys.implementation._machine)
# The USB host keeps running across a soft reset (`mpremote run` does one),
# with the previous run's device, counters and report log still in place.
# Start each run from a hard reset so the results belong to this device.
if usb_host.dev_info()[0] or usb_host.debug()[4]:
    print("USB host already started since the last hard reset.")
    print("Run `mpremote reset` first, then plug in the keyboard after starting.")
    sys.exit()
if "PiZero" in sys.implementation._machine:
    # main.py gets 252MHz from dvi_display.init(); PIO-USB needs a 12MHz
    # multiple, and the power-on 150MHz is not one.
    machine.freq(USB_KHZ * 1000)
usb_host.init(USB_KHZ)
print("usb_host.init() done, clk_sys = %d MHz" % (machine.freq() // 1_000_000))
print("plug in the keyboard / receiver, then press keys (Ctrl-C to stop)")

protos = {}
seq = 0
last_mount = usb_host.dev_info()[0]
last_dbg = None
attach_ms = None
setup_shown = False
counts = {}
status_ms = time.ticks_ms()
try:
    while True:
        if STATUS_S and time.ticks_diff(time.ticks_ms(), status_ms) >= STATUS_S * 1000:
            status_ms = time.ticks_ms()
            errs = sum(n for (i, ln, ok), n in counts.items() if ln == 0)
            keys = sum(n for (i, ln, ok), n in counts.items() if ok)
            print("[status] reports=%d (keys %d) failed transfers=%d"
                  % (sum(counts.values()) - errs, keys, errs))
        usb_host.task()
        mount = usb_host.dev_info()[0]
        if mount != last_mount:
            last_mount = mount
            attach_ms = None
            protos = _print_device()

        seq, reps = usb_host.hid_reports(seq)
        for s, inst, ln, data in reps:
            if ln == 0:
                # TinyUSB reports a failed interrupt transfer as length 0
                counts[(inst, 0, False)] = counts.get((inst, 0, False), 0) + 1
                continue
            ok = protos.get(inst) == 1 and ln == 8
            counts[(inst, ln, ok)] = counts.get((inst, ln, ok), 0) + 1
            print("report #%d inst=%d len=%d: %s%s  -> %s"
                  % (s, inst, ln, _hex(data), " ..." if ln > len(data) else "",
                     "keys" if ok else "ignored"))

        # PIO-USB connection state (all zeros on the native-USB build)
        dbg = usb_host.debug()
        state = (dbg[1], dbg[4], dbg[5])  # connected, attach, detach
        if state != last_dbg:
            last_dbg = state
            print("[pio-usb] connected=%d attach=%d detach=%d ep_error=%d"
                  % (dbg[1], dbg[4], dbg[5], dbg[8]))
            if dbg[1]:
                attach_ms = time.ticks_ms()
                setup_shown = False
        if (attach_ms is not None and not setup_shown
                and time.ticks_diff(time.ticks_ms(), attach_ms) > REPORT_AFTER_S * 1000
                and usb_host.dev_info()[0] == last_mount):
            setup_shown = True
            print("attached %ds ago but not mounted" % REPORT_AFTER_S)
            _print_setup_log()
            _print_ep0_history()
except KeyboardInterrupt:
    print()
    print("=== summary")
    print("dev_info (mount, umount, vid, pid):", usb_host.dev_info())
    print("debug:", usb_host.debug())
    for (inst, ln, ok), n in sorted(counts.items()):
        kind = "transfer errors" if ln == 0 else ("keys" if ok else "ignored")
        print("  inst=%d len=%d %s: %d reports" % (inst, ln, kind, n))
    if not counts:
        print("  no HID reports received")
