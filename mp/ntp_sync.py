"""
NTP time synchronization for PB-1000 Emulator on Raspberry Pi Pico 2W.

Connects to WiFi, fetches UTC from an NTP server, applies timezone offset,
sets the Pico's RTC, and writes the time into PB-1000 system variable RAM
(DATE$ at &H6BAD, TIME$ at &H6BB0) in BCD format.
All status messages go to REPL (print); no LCD output.
"""

import time
import struct
import machine


# PB-1000 system variable addresses (from sysvars.txt / Technical Handbook)
_ADDR_DATE = 0x6BAD   # 3 bytes: YY(raw binary), MM(BCD), DD(BCD)
_ADDR_TIME = 0x6BB0   # 2 bytes: MM(BCD) at +0, HH(BCD) at +1  ← minute-first order

# NTP constants
_NTP_PORT = 123
_NTP_DELTA = 2208988800   # seconds between 1900-01-01 and 1970-01-01
_NTP_PACKET_SIZE = 48


def is_wifi_supported():
    """Check if the hardware/firmware supports WiFi."""
    try:
        import network
        network.WLAN(network.STA_IF)
        return True
    except Exception:
        return False


def _to_bcd(val):
    """Convert integer 0-99 to BCD byte."""
    return ((val // 10) << 4) | (val % 10)


def _ntp_request(ntp_server, timeout_s=5):
    """Send NTP request and return UTC epoch seconds, or None on failure."""
    import socket
    try:
        addr = socket.getaddrinfo(ntp_server, _NTP_PORT)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout_s)
        # NTP request packet: LI=0, VN=3, Mode=3 (client)
        pkt = bytearray(_NTP_PACKET_SIZE)
        pkt[0] = 0x1B
        s.sendto(pkt, addr)
        msg = s.recv(_NTP_PACKET_SIZE)
        s.close()
        # Transmit timestamp starts at byte 40 (4 bytes seconds)
        ntp_secs = struct.unpack("!I", msg[40:44])[0]
        return ntp_secs - _NTP_DELTA
    except Exception as e:
        print(f"[NTP] request failed: {e}")
        return None


def _wifi_connect(ssid, password, timeout_ms=15000):
    """Connect to WiFi. Returns True on success."""
    try:
        import network
    except ImportError:
        print("[NTP] 'network' module not available (not a Pico W?)")
        return False

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print(f"[NTP] WiFi already connected: {ip}")
        return True

    print(f"[NTP] Connecting to WiFi '{ssid}'...")
    wlan.connect(ssid, password)

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while not wlan.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            print("[NTP] WiFi connection timed out.")
            wlan.active(False)
            return False
        time.sleep_ms(200)

    ip = wlan.ifconfig()[0]
    print(f"[NTP] WiFi connected: {ip}")
    return True


def _wifi_disconnect():
    """Disconnect WiFi and power down the radio to save power."""
    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        wlan.disconnect()
        wlan.active(False)
        print("[NTP] WiFi disconnected.")
    except Exception:
        pass


def sync_ntp(*, ssid, password, ntp_server="pool.ntp.org",
             tz_offset_h=9, timeout_ms=15000, disconnect_after=True):
    """
    Full NTP sync flow: connect WiFi → fetch NTP → set Pico RTC → disconnect.
    All status output goes to REPL (print). Returns (year,month,day,hour,min,sec)
    in local time, or None on failure.
    """
    if not ssid:
        print("[NTP] No SSID configured; skipping NTP sync.")
        return None

    if not _wifi_connect(ssid, password, timeout_ms):
        return None

    print(f"[NTP] Requesting {ntp_server}...")
    utc_epoch = _ntp_request(ntp_server)
    if utc_epoch is None:
        if disconnect_after:
            _wifi_disconnect()
        return None

    # Apply timezone offset
    local_epoch = utc_epoch + tz_offset_h * 3600
    tm = time.gmtime(local_epoch)
    year, month, day, hour, minute, second = tm[0], tm[1], tm[2], tm[3], tm[4], tm[5]

    # Set Pico's RTC
    try:
        rtc = machine.RTC()
        weekday = tm[6]  # 0=Monday in MicroPython
        rtc.datetime((year, month, day, weekday, hour, minute, second, 0))
        print(f"[NTP] Pico RTC set: {year:04d}-{month:02d}-{day:02d} "
              f"{hour:02d}:{minute:02d}:{second:02d} (UTC{tz_offset_h:+d})")
    except Exception as e:
        print(f"[NTP] RTC set failed: {e}")

    if disconnect_after:
        _wifi_disconnect()

    return (year, month, day, hour, minute, second)


def set_pb1000_time(year, month, day, hour, minute, second):
    """
    Write date/time into PB-1000 system variable RAM in BCD format.

    DATE$ at &H6BAD: 3 bytes — YY(raw binary), MM(BCD), DD(BCD)
    TIME$ at &H6BB0: 2 bytes — MM(BCD) at +0, HH(BCD) at +1 (minute-first)

    The seconds are managed by the HD61700 timer interrupt handler in ROM;
    the REG_TM (timer counter register) lower 6 bits represent seconds (0-59).
    We set REG_TM's lower 6 bits to the current second value.
    """
    import hd61700

    yy = year % 100  # 2-digit year

    # Write DATE$ (3 bytes at 0x6BAD): YY(raw binary), MM(BCD), DD(BCD)
    # NOTE: YY is stored as raw binary (NOT BCD) by the PB-1000 ROM.
    #       MM and DD use BCD as usual.
    hd61700.write_mem(_ADDR_DATE,     yy)              # raw binary, e.g. 26 = 0x1A
    hd61700.write_mem(_ADDR_DATE + 1, _to_bcd(month))
    hd61700.write_mem(_ADDR_DATE + 2, _to_bcd(day))

    # Write TIME$ (2 bytes at 0x6BB0): MM(BCD) at +0, HH(BCD) at +1
    # NOTE: PB-1000 stores minute at the lower address (0x6BB0) and
    #       hour at the higher address (0x6BB1) — opposite of what the
    #       variable name order might suggest.
    hd61700.write_mem(_ADDR_TIME,     _to_bcd(minute))  # 0x6BB0 = MM
    hd61700.write_mem(_ADDR_TIME + 1, _to_bcd(hour))    # 0x6BB1 = HH

    # Set seconds into REG_TM (special register index 6)
    # REG_TM: bits 0-5 = seconds counter (0-59), bits 6-7 = minute sub-counter
    current_tm = hd61700.get_reg8(6)
    new_tm = (current_tm & 0xC0) | (second & 0x3F)
    hd61700.set_reg8(6, new_tm)

    print(f"[NTP] PB-1000 clock set: {yy:02d}/{month:02d}/{day:02d} "
          f"{hour:02d}:{minute:02d}:{second:02d}")


def ntp_sync_and_set(*, ssid, password, ntp_server="pool.ntp.org",
                     tz_offset_h=9, timeout_ms=15000, disconnect_after=True):
    """
    Convenience function: perform NTP sync and set PB-1000 time in one call.
    Returns True if time was set successfully, False otherwise.
    """
    result = sync_ntp(
        ssid=ssid,
        password=password,
        ntp_server=ntp_server,
        tz_offset_h=tz_offset_h,
        timeout_ms=timeout_ms,
        disconnect_after=disconnect_after,
    )
    if result is None:
        return False

    year, month, day, hour, minute, second = result
    set_pb1000_time(year, month, day, hour, minute, second)
    return True
