"""
PC Link mode: file transfer between the SD card and a PC over the REPL's
serial port or WiFi, entered with F2 from the boot-time profile picker
(boot_session.py). The PC side is tools/pc_link.py (protocol client) and
tools/device_files_gui.py (the GUI, also reachable from tools/launcher.py).

Runs only at the profile picker, before any ROM/RAM is loaded or the CPU
starts, so no FD image is held open and no RAM profile is in use -- a file
can be replaced without the emulator's own view of it going stale. That is
also why this is not offered from the runtime EMULATOR MENU.

The serial port is whatever sys.stdin/sys.stdout are: the USB-C CDC port on
USE_PIO_USB=ON builds, UART0 on USE_PIO_USB=OFF builds. Ctrl-C handling is
disabled while the link runs (file data is raw binary, and 0x03 would
otherwise raise KeyboardInterrupt mid-transfer), and nothing here print()s
to stdout while the link is open, since the PC is reading that stream.

WiFi (Pico 2 W, when [wifi] ssid is set in pb1000.ini): the same protocol
over TCP port PORT, served at the same time as the serial port -- whichever
the PC uses works. The device announces itself as HOSTNAME.local (mDNS) and
shows its IP address and a 4-digit PIN, freshly random each time PC Link is
entered, on the screen; a TCP connection must send "AUTH <TAB> pin" (->
@OK) before anything else, and a wrong PIN closes it. One PC at a time.

Protocol (PBL1). Requests are one line, fields separated by TAB, ending in
"\\n". Every device line starts with "@" so the PC can skip any unrelated
output (boot log lines, a stray C-level message) while waiting for a reply:

  AUTH <TAB> pin              -> @OK  (TCP only; required first)
  HELLO                      -> @OK <TAB> PBL1 <TAB> <chunk size>
  LS <TAB> dir               -> @F <TAB> d|f <TAB> size <TAB> name  (per entry)
                                @OK <TAB> count
  DF                         -> @OK <TAB> total bytes <TAB> free bytes  (of /sd)
  GET <TAB> path             -> @DATA <TAB> size, <size raw bytes>,
                                @END <TAB> crc32 (8 hex digits)
  PUT <TAB> path <TAB> size <TAB> crc32
                             -> @GO, then for each chunk of up to <chunk
                                size> bytes the PC sends: @ACK; finally
                                @OK, or @ERR if the data didn't arrive intact
                                or couldn't be written (the original file is
                                then left untouched -- see _put())
  RM <TAB> path              -> @OK (file)
  RMDIR <TAB> path           -> @OK (empty directory)
  MKDIR <TAB> path           -> @OK
  BYE                        -> @OK, and PC Link mode ends

Any request can instead get "@ERR <TAB> message". Paths must be under /sd
and may not contain ".." components.
"""

import os
import sys
import time

CHUNK = 4096
_ROOT = "/sd"
PORT = 8100
HOSTNAME = "pb1000"
CONNECT_TIMEOUT_MS = 45000   # WiFi join + DHCP, see _Wifi._progress()


class _Disconnected(Exception):
    """The TCP peer went away (or stalled) mid-request. Deliberately not an
    OSError, so _Link.handle()'s per-request error reporting doesn't catch
    it -- the connection is dropped instead (serve())."""


def _check_path(path):
    if path != _ROOT and not path.startswith(_ROOT + "/"):
        raise ValueError("path outside /sd")
    if ".." in path.split("/"):
        raise ValueError("'..' not allowed")
    return path.rstrip("/") or _ROOT


def _is_dir(path):
    return (os.stat(path)[0] & 0x4000) != 0


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


class _Link:
    def __init__(self, status, inp, out, buf):
        self._in = inp
        self._out = out
        self._buf = buf
        self._status = status

    def _send(self, *fields):
        self._out.write(("\t".join(str(f) for f in fields) + "\n").encode())

    def _read_exact(self, mv):
        got = 0
        n = len(mv)
        while got < n:
            try:
                r = self._in.readinto(mv[got:])
            except OSError:  # TCP timeout/reset
                raise _Disconnected()
            if r == 0:       # TCP peer closed (the serial port never returns 0)
                raise _Disconnected()
            if r:
                got += r
        return n

    def handle(self, line):
        """Run one request. Returns False once the PC has said BYE."""
        parts = line.split("\t")
        cmd = parts[0]
        try:
            if cmd == "HELLO":
                self._send("@OK", "PBL1", CHUNK)
            elif cmd == "LS":
                self._ls(_check_path(parts[1]))
            elif cmd == "DF":
                st = os.statvfs(_ROOT)
                self._send("@OK", st[0] * st[2], st[0] * st[3])
            elif cmd == "GET":
                self._get(_check_path(parts[1]))
            elif cmd == "PUT":
                self._put(_check_path(parts[1]), int(parts[2]), int(parts[3], 16))
            elif cmd == "RM":
                path = _check_path(parts[1])
                self._status("Delete " + path)
                os.remove(path)
                self._send("@OK")
            elif cmd == "RMDIR":
                path = _check_path(parts[1])
                self._status("Delete " + path)
                os.rmdir(path)
                self._send("@OK")
            elif cmd == "MKDIR":
                path = _check_path(parts[1])
                os.mkdir(path)
                self._send("@OK")
            elif cmd == "BYE":
                self._send("@OK")
                return False
            else:
                self._send("@ERR", "unknown command")
        except (OSError, ValueError, IndexError) as e:
            self._send("@ERR", repr(e))
        return True

    def _ls(self, path):
        count = 0
        for ent in os.ilistdir(path):
            name, kind = ent[0], ent[1]
            size = ent[3] if len(ent) > 3 else 0
            self._send("@F", "d" if kind == 0x4000 else "f", size, name)
            count += 1
        self._send("@OK", count)

    def _get(self, path):
        import binascii
        size = os.stat(path)[6]
        self._status("Send " + path)
        crc = 0
        with open(path, "rb") as f:
            self._send("@DATA", size)
            mv = memoryview(self._buf)
            while True:
                n = f.readinto(self._buf)
                if not n:
                    break
                crc = binascii.crc32(mv[:n], crc)
                self._out.write(mv[:n])
        self._send("@END", "%08x" % crc)

    def _put(self, path, size, crc_expected):
        """Receive into <path>.tmp and only replace <path> once the whole
        file has arrived with a matching CRC -- a cancelled or corrupted
        upload must never leave a half-written RAM image or disk image
        behind (same reasoning as boot_session.save_default_profile()'s
        temp-file-and-rename). FAT's rename won't overwrite, so the old
        file is removed right before the rename."""
        import binascii
        if _exists(path) and _is_dir(path):
            raise ValueError("is a directory")
        tmp = path + ".tmp"
        self._status("Receive " + path)
        crc = 0
        err = None
        mv = memoryview(self._buf)
        try:
            f = open(tmp, "wb")
        except OSError as e:
            f = None
            err = e
        self._send("@GO")
        left = size
        try:
            while left > 0:
                n = min(CHUNK, left)
                self._read_exact(mv[:n])
                crc = binascii.crc32(mv[:n], crc)
                if f is not None:
                    try:
                        f.write(mv[:n])
                    except OSError as e:
                        # Keep reading so the PC's remaining chunks are consumed
                        # and the stream stays in sync; report at the end.
                        err = e
                        f.close()
                        f = None
                left -= n
                self._send("@ACK")
        except Exception:
            # Connection lost mid-upload: leave the original file alone.
            if f is not None:
                f.close()
            _remove_quiet(tmp)
            raise
        if f is not None:
            f.close()
        if err is None and crc != crc_expected:
            err = "CRC mismatch"
        if err is not None:
            _remove_quiet(tmp)
            self._send("@ERR", str(err))
            return
        _remove_quiet(path)
        os.rename(tmp, path)
        self._send("@OK")


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _clean(raw):
    """Request line bytes -> str, or "" to ignore it."""
    try:
        line = raw.decode()
    except UnicodeError:
        return ""
    # Drop control characters other than TAB: mpremote sends "\r\x02"
    # (leave raw REPL) when it disconnects, even after `run --no-follow`,
    # and that would otherwise end up glued to the front of the PC's first
    # request ("\x02HELLO").
    return "".join(c for c in line if c >= " " or c == "\t").strip()


class _Wifi:
    """TCP side of serve(): brings the WLAN up without blocking (tick() is
    polled from serve()'s loop, so the serial side is usable meanwhile),
    then listens on PORT and serves one PC at a time."""

    def __init__(self, ssid, password, show, status, buf):
        import network
        self._show = show        # show(line1, line2): the WiFi lines on screen
        self._status = status
        self._buf = buf
        self.pin = "%04d" % (int.from_bytes(os.urandom(2), "little") % 10000)
        try:
            network.hostname(HOSTNAME)   # must precede activation for mDNS
        except Exception:
            pass
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        self._ssid = ssid
        self._password = password
        self._t0 = time.ticks_ms()
        self._tries = 0
        if not self._wlan.isconnected():
            self._join()
        self._shown = None       # last progress line, to redraw only on change
        self.listener = None
        self.client = None
        self.link = None
        self.authed = False
        show("WiFi: connecting to " + ssid, "")

    def tick(self, poll):
        if self.listener is not None or self._wlan is None:
            return
        if self._wlan.isconnected():
            import socket
            import select
            ip = self._wlan.ifconfig()[0]
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", PORT))
            s.listen(1)
            self.listener = s
            poll.register(s, select.POLLIN)
            self._show("WiFi: %s:%d" % (ip, PORT),
                       "PIN: %s  (%s.local)" % (self.pin, HOSTNAME))
        else:
            self._progress()

    def _join(self):
        self._tries += 1
        self._t_try = time.ticks_ms()
        if self._tries > 1:
            self._wlan.disconnect()
        self._wlan.connect(self._ssid, self._password)

    def _progress(self):
        # The radio does not retry on its own: an attempt can end in "no AP
        # found" / "failed" even with the AP in range, or hang in "joining",
        # and DHCP can take 15 s or more right after power-on. So rejoin as
        # needed, show the step with a seconds counter, and give up only
        # after CONNECT_TIMEOUT_MS (or at once on a wrong password).
        import network
        st = self._wlan.status()
        now = time.ticks_ms()
        secs = time.ticks_diff(now, self._t0) // 1000
        if st == network.STAT_WRONG_PASSWORD:
            fail = "WiFi: wrong password ([wifi] password)"
        elif secs * 1000 >= CONNECT_TIMEOUT_MS:
            fail = "WiFi: could not connect (%s)" % (
                "no IP address from DHCP" if st == 2 else "no answer from " + self._ssid)
        else:
            fail = None
        if fail is not None:
            self._show(fail, "")
            self._wlan.active(False)
            self._wlan = None
            return
        tried = time.ticks_diff(now, self._t_try)
        if (st < 0 and tried >= 2000) or (st == 1 and tried >= 15000):
            self._join()
        text = ("WiFi: getting IP address... %ds" % secs if st == 2
                else "WiFi: connecting to %s... %ds" % (self._ssid, secs))
        if self._tries > 1:
            text += " (try %d)" % self._tries
        if text != self._shown:
            self._shown = text
            self._show(text, "")

    def accept(self, poll):
        import select
        cs, addr = self.listener.accept()
        cs.settimeout(10)
        self.client = cs
        self.authed = False
        self.link = _Link(self._status, cs, cs, self._buf)
        # One PC at a time: stop accepting until drop(), so a PC that
        # connects while the previous one is still closing waits in the
        # listen backlog instead of being turned away.
        poll.unregister(self.listener)
        poll.register(cs, select.POLLIN)
        self._status("PC connected over WiFi")

    def drop(self, poll):
        if self.client is None:
            return
        try:
            poll.unregister(self.client)
        except Exception:
            pass
        try:
            self.client.close()
        except OSError:
            pass
        self.client = None
        self.link = None
        if self.listener is not None:
            import select
            poll.register(self.listener, select.POLLIN)
        self._status("Ready")

    def serve_client(self, poll):
        """Handle one request line. Returns False once the PC has said BYE."""
        raw = self.client.readline()
        if not raw:              # peer closed
            self.drop(poll)
            return True
        line = _clean(raw)
        if not line:
            return True
        if not self.authed:
            parts = line.split("\t")
            if parts[0] == "AUTH" and len(parts) > 1 and parts[1] == self.pin:
                self.authed = True
                self.link._send("@OK")
            else:
                self.link._send("@ERR", "wrong PIN")
                time.sleep_ms(1000)  # slow down guessing
                self.drop(poll)
            return True
        if not self.link.handle(line):
            return False
        self._status("Ready")
        return True

    def close(self):
        for s in (self.client, self.listener):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self._wlan is not None:
            try:
                self._wlan.disconnect()
                self._wlan.active(False)
            except Exception:
                pass


def _wifi_settings():
    """(ssid, password, None) if this board has WiFi and pb1000.ini sets
    [wifi] ssid; otherwise (None, None, reason for the screen or None)."""
    try:
        import network
        network.WLAN
    except (ImportError, AttributeError):
        return None, None, None    # no WiFi on this board: say nothing
    from config import load_config, get_str
    cfg = load_config()
    ssid = get_str(cfg, "wifi", "ssid")
    if not ssid:
        return None, None, "WiFi: set [wifi] ssid to use"
    return ssid, get_str(cfg, "wifi", "password"), None


def serve(status, should_quit, idle=None, wifi_show=None):
    """Answer PC requests until the PC sends BYE or should_quit() is True.

    status(text) reports what is going on (shown on the LCD by run());
    should_quit() is polled between requests (ESC on the keyboard);
    idle(), if given, is called while waiting (HDMI mirror flush).
    wifi_show(line1, line2), if given, enables WiFi (see _wifi_settings())
    and shows its state."""
    import micropython
    import select

    buf = bytearray(CHUNK)   # one transfer buffer, shared (one request at a time)
    link = _Link(status, sys.stdin.buffer, sys.stdout.buffer, buf)
    pending = bytearray()    # serial request line being received
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)

    wifi = None
    if wifi_show is not None:
        ssid, password, reason = _wifi_settings()
        if ssid is not None:
            try:
                wifi = _Wifi(ssid, password, wifi_show, status, buf)
            except Exception as e:
                wifi_show("WiFi: unavailable (%s)" % e, "")
        elif reason:
            wifi_show(reason, "")

    micropython.kbd_intr(-1)
    try:
        while True:
            if should_quit():
                return
            if wifi is not None:
                wifi.tick(poll)
            events = poll.poll(50)
            if not events:
                if idle is not None:
                    idle()
                continue
            for obj, ev in events:
                if wifi is not None and obj is wifi.listener:
                    wifi.accept(poll)
                elif wifi is not None and wifi.client is not None and obj is wifi.client:
                    try:
                        if not wifi.serve_client(poll):
                            return
                    except (OSError, _Disconnected):
                        wifi.drop(poll)
                else:
                    # One byte per wakeup, never readline(): stray bytes
                    # with no newline after them (mpremote's "\r\x02" on
                    # disconnect) would block readline() -- and with it
                    # the WiFi side and ESC -- until a serial PC connects.
                    c = link._in.read(1)
                    if c != b"\n":
                        if len(pending) < 512:
                            pending.extend(c)
                        continue
                    line = _clean(pending)
                    pending[:] = b""
                    if not line:
                        continue
                    if not link.handle(line):
                        return
                    status("Ready")
    finally:
        micropython.kbd_intr(3)
        if wifi is not None:
            wifi.close()


# ── LCD screen ───────────────────────────────────────────────────────────────

_BG = 0x0000
_FG = 0xFFFF
_HDR = 0xFFE0
_FTR = 0x7BEF
_ACT = 0x07E0


def run(display):
    """Show the PC Link screen and serve requests until ESC or the PC is
    done. Returns normally either way; boot_session.py redraws the profile
    picker (re-scanning profiles, since the PC may have added or removed
    one) afterward."""
    import hd61700
    from draw_text import draw_text
    from hdmi_menu_mirror import hdmi_flush

    W, H = display.width, display.height
    display.fill_rect(0, 0, W, H, _BG)
    draw_text(display, 4, 4, "PC LINK", _HDR)
    draw_text(display, 4, 24, "Connect the PC to the REPL port", _FG)
    draw_text(display, 4, 36, "and open Device Files in", _FG)
    draw_text(display, 4, 48, "tools/launcher.py.", _FG)
    draw_text(display, 4, H - 12, "ESC: back to profile list", _FTR)
    wifi_y = 68
    status_y = 100

    def line(y, text, fg):
        display.fill_rect(0, y, W, 8, _BG)
        draw_text(display, 4, y, text, fg)

    def status(text):
        line(status_y, text, _ACT)
        hdmi_flush(display)

    def wifi_show(l1, l2):
        line(wifi_y, l1, _FG)
        line(wifi_y + 12, l2, _HDR)
        hdmi_flush(display)

    prev = [hd61700.get_last_key()]

    def should_quit():
        sc = hd61700.get_last_key()
        if sc != prev[0]:
            prev[0] = sc
            return sc == 0x29  # ESC
        return False

    status("Ready")
    serve(status, should_quit, lambda: hdmi_flush(display), wifi_show)
