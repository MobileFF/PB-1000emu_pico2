#!/usr/bin/env python3
"""
pc_link.py — PC-side client for the emulator's PC Link mode (mp/pc_link.py).

On the device, press F2 at the boot-time profile picker to enter PC Link
mode, then connect to its REPL serial port (USB-C on the default build).
See mp/pc_link.py for the protocol (PBL1).

Requires pyserial (pip install pyserial).

Connecting over WiFi (Pico 2 W with [wifi] ssid set): give the device's
address shown on its PC Link screen instead of a serial port -- "pb1000.local",
an IP address, or "wifi:<host>" -- plus the PIN shown there (--pin, or asked
for). Everything else is the same.

Command line use:
    python pc_link.py ports
    python pc_link.py --port pb1000.local --pin 1234 ls /sd/rams
    python pc_link.py [--port PORT] ls /sd/rams
    python pc_link.py [--port PORT] get /sd/screenshots/full_x.png [local]
    python pc_link.py [--port PORT] put local_file /sd/disks/disk1.img
    python pc_link.py [--port PORT] rm /sd/screenshots/full_x.png
    python pc_link.py [--port PORT] bye
"""

import os
import socket
import sys
import time
import zlib

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # reported by connect(), so importing this module never fails
    serial = None

RP2_USB_VID = 0x2E8A  # Raspberry Pi (MicroPython's CDC port on RP2040/RP2350)
TCP_PORT = 8100       # mp/pc_link.py's PORT
DEFAULT_HOST = "pb1000.local"


class LinkError(Exception):
    pass


def find_ports():
    """[(device, description)] of serial ports, MicroPython/RP2 ones first."""
    if serial is None:
        return []
    ports = list(serial.tools.list_ports.comports())
    ports.sort(key=lambda p: (p.vid != RP2_USB_VID, p.device))
    return [(p.device, p.description or "") for p in ports]


def _port_users(port):
    """[(pid, command)] of other processes that have port open. Linux only
    (reads /proc; returns [] elsewhere, and only sees this user's own
    processes, which is where mpremote/a terminal program would be)."""
    if not os.path.isdir("/proc"):
        return []
    target = os.path.realpath(port)
    me = os.getpid()
    users = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        fd_dir = "/proc/%s/fd" % pid
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == target:
                    break
            except OSError:
                pass
        else:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            cmd = "?"
        users.append((int(pid), cmd))
    return users


def is_network_target(target):
    """True if target names the device over WiFi rather than a serial port:
    "wifi:<host>", a host name with a dot or an IP address (optionally
    ":port"). Serial ports are /dev/... or COMn."""
    t = target.strip()
    if t.lower().startswith("wifi:"):
        return True
    if t.startswith("/dev/") or t.upper().startswith("COM"):
        return False
    return "." in t or ":" in t


def _split_host(target):
    t = target.strip()
    if t.lower().startswith("wifi:"):
        t = t[5:].strip()
    host, sep, port = t.rpartition(":")
    if sep and port.isdigit():
        return host, int(port)
    return t, TCP_PORT


def check_host(target):
    """Resolve a network target's host name (see is_network_target()) and
    return its (host, port), or raise LinkError if the name can't be looked
    up -- typically pb1000.local where mDNS doesn't reach (e.g. a Linux VM
    or container behind NAT)."""
    host, port = _split_host(target)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise LinkError(_RESOLVE_HINT % host)
    return host, port


_RESOLVE_HINT = ("cannot resolve %s -- use the IP address shown on the device's "
                 "PC Link screen instead")


class _TcpStream:
    """The subset of pyserial's Serial that PCLink uses, over a TCP socket:
    readline()/read() return whatever arrived (possibly short) on timeout,
    like pyserial does."""

    def __init__(self, host, port, timeout):
        self._timeout = timeout
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except socket.gaierror:
            raise LinkError(_RESOLVE_HINT % host)
        except OSError as e:
            raise LinkError("cannot connect to %s:%d (%s) -- is the device in PC Link mode "
                            "(F2) and showing a WiFi address?" % (host, port, e))
        self._sock.settimeout(timeout)
        self._buf = b""

    def write(self, data):
        self._sock.sendall(data)

    def _fill(self):
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout:
            return False
        if not chunk:
            raise LinkError("connection closed by the device")
        self._buf += chunk
        return True

    def readline(self):
        while b"\n" not in self._buf:
            if not self._fill():
                out, self._buf = self._buf, b""
                return out
        i = self._buf.index(b"\n") + 1
        out, self._buf = self._buf[:i], self._buf[i:]
        return out

    def read(self, n):
        while len(self._buf) < n:
            if not self._fill():
                break
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def reset_input_buffer(self):
        self._buf = b""
        self._sock.setblocking(False)
        try:
            while self._sock.recv(65536):
                pass
        except OSError:  # BlockingIOError: nothing more buffered
            pass
        finally:
            self._sock.settimeout(self._timeout)

    def close(self):
        self._sock.close()


class Entry:
    def __init__(self, name, is_dir, size):
        self.name = name
        self.is_dir = is_dir
        self.size = size

    def __repr__(self):
        return "Entry(%r, %s, %d)" % (self.name, "dir" if self.is_dir else "file", self.size)


class PCLink:
    def __init__(self, port, baudrate=115200, timeout=10.0, pin=None):
        """port: a serial port, or the device's WiFi address (see
        is_network_target()), in which case pin is required."""
        self.chunk = 4096
        if is_network_target(port):
            if not pin:
                raise LinkError("a PIN is required for WiFi (shown on the device's PC Link screen)")
            self._ser = _TcpStream(*_split_host(port), timeout=timeout)
            try:
                self._send("AUTH", pin.strip())
                f = self._line()
                if f[0] != "@OK":
                    raise LinkError("wrong PIN" if f[0] == "@ERR" else
                                    "unexpected response %r" % "\t".join(f))
                self.hello()
            except Exception:
                self.close()
                raise
            return
        if serial is None:
            raise LinkError("pyserial is not installed (pip install pyserial)")
        # On Linux nothing stops a second program (typically a still-running
        # `mpremote ... run main.py`, which keeps reading the port to show the
        # device's output) from reading the same port at the same time; the
        # replies then get split between the two and arrive here incomplete.
        # Windows refuses to open a busy COM port, so this only matters here.
        users = _port_users(port)
        if users:
            raise LinkError("%s is in use by another program -- close it first:\n%s" % (
                port, "\n".join("  PID %d: %s" % u for u in users)))
        # baudrate only matters for a real UART (USE_PIO_USB=OFF builds);
        # the USB CDC port ignores it.
        self._ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        try:
            self.hello()
        except Exception:
            # Don't leave the port open behind a failed connect -- the GUI
            # would otherwise keep holding it until it is closed.
            self.close()
            raise

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── low level ────────────────────────────────────────────────────────────

    def _send(self, *fields):
        self._ser.write(("\t".join(str(f) for f in fields) + "\n").encode())

    def _line(self):
        """Next protocol line (starting with "@"), skipping anything else
        the device printed (boot log lines etc.)."""
        while True:
            raw = self._ser.readline()
            if not raw:
                raise LinkError("no response from device (is it in PC Link mode? F2 at the profile picker)")
            if not raw.endswith(b"\n"):
                raise LinkError("timed out in the middle of a response "
                                "(is another program, e.g. mpremote, also using this port?)")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("@"):
                return line.split("\t")

    def _expect(self, tag):
        f = self._line()
        if f[0] == "@ERR":
            raise LinkError(f[1] if len(f) > 1 else "device error")
        if f[0] != tag:
            raise LinkError("unexpected response %r" % "\t".join(f))
        return f

    def _read_exact(self, n):
        data = self._ser.read(n)
        if len(data) != n:
            raise LinkError("timed out receiving data")
        return data

    # ── commands ─────────────────────────────────────────────────────────────

    def hello(self):
        # Terminate whatever partial line the device may already be holding
        # (mpremote leaves "\r\x02" behind when it disconnects), so it can't
        # get glued to the front of HELLO. The device answers that leftover
        # with @ERR (older device code) or ignores it; either way, give it a
        # moment and discard it along with anything else already buffered
        # (boot log) before syncing.
        self._ser.write(b"\n")
        time.sleep(0.3)
        self._ser.reset_input_buffer()
        self._send("HELLO")
        f = self._expect("@OK")
        if len(f) < 3 or f[1] != "PBL1":
            raise LinkError("unsupported protocol %r" % "\t".join(f))
        self.chunk = int(f[2])

    def listdir(self, path):
        self._send("LS", path)
        entries = []
        while True:
            f = self._line()
            if f[0] == "@F":
                entries.append(Entry(f[3], f[1] == "d", int(f[2])))
            elif f[0] == "@OK":
                return entries
            elif f[0] == "@ERR":
                raise LinkError(f[1] if len(f) > 1 else "device error")
            else:
                raise LinkError("unexpected response %r" % "\t".join(f))

    def disk_free(self):
        """(total, free) bytes of the SD card."""
        self._send("DF")
        f = self._expect("@OK")
        return int(f[1]), int(f[2])

    def get(self, path, progress=None):
        """Return the file's contents. progress(done, total) is called per chunk."""
        self._send("GET", path)
        size = int(self._expect("@DATA")[1])
        parts = []
        done = 0
        while done < size:
            part = self._read_exact(min(self.chunk, size - done))
            parts.append(part)
            done += len(part)
            if progress:
                progress(done, size)
        data = b"".join(parts)
        crc = int(self._expect("@END")[1], 16)
        if zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise LinkError("CRC mismatch receiving %s" % path)
        return data

    def put(self, path, data, progress=None):
        """Write data to path. The device only replaces the existing file
        once everything has arrived intact."""
        self._send("PUT", path, len(data), "%08x" % (zlib.crc32(data) & 0xFFFFFFFF))
        self._expect("@GO")
        done = 0
        while done < len(data):
            n = min(self.chunk, len(data) - done)
            self._ser.write(data[done:done + n])
            self._expect("@ACK")
            done += n
            if progress:
                progress(done, len(data))
        self._expect("@OK")

    def remove(self, path):
        self._send("RM", path)
        self._expect("@OK")

    def rmdir(self, path):
        self._send("RMDIR", path)
        self._expect("@OK")

    def mkdir(self, path):
        self._send("MKDIR", path)
        self._expect("@OK")

    def bye(self):
        """Leave PC Link mode (the device returns to the profile picker)."""
        self._send("BYE")
        self._expect("@OK")

    # ── helpers built on the commands ─────────────────────────────────────────

    def exists(self, path):
        parent, name = path.rsplit("/", 1)
        try:
            return any(e.name == name for e in self.listdir(parent or "/"))
        except LinkError:
            return False

    def makedirs(self, path):
        """mkdir for every missing level of path (under /sd)."""
        parts = path.strip("/").split("/")
        cur = ""
        for p in parts:
            cur += "/" + p
            if cur == "/sd":
                continue
            if not self.exists(cur):
                self.mkdir(cur)

    def get_tree(self, remote_dir, local_dir, progress=None):
        """Download remote_dir recursively into local_dir."""
        os.makedirs(local_dir, exist_ok=True)
        for e in self.listdir(remote_dir):
            r = remote_dir + "/" + e.name
            l = os.path.join(local_dir, e.name)
            if e.is_dir:
                self.get_tree(r, l, progress)
            else:
                with open(l, "wb") as f:
                    f.write(self.get(r, progress))

    def put_tree(self, local_dir, remote_dir, progress=None):
        """Upload local_dir recursively to remote_dir (created if missing)."""
        self.makedirs(remote_dir)
        for name in sorted(os.listdir(local_dir)):
            l = os.path.join(local_dir, name)
            r = remote_dir + "/" + name
            if os.path.isdir(l):
                self.put_tree(l, r, progress)
            else:
                with open(l, "rb") as f:
                    self.put(r, f.read(), progress)

    def remove_tree(self, path):
        for e in self.listdir(path):
            p = path + "/" + e.name
            if e.is_dir:
                self.remove_tree(p)
            else:
                self.remove(p)
        self.rmdir(path)


# ── command line ──────────────────────────────────────────────────────────────

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="PB-1000 emulator PC Link client")
    ap.add_argument("--port", help="serial port, or the device's WiFi address "
                    "(e.g. pb1000.local; default: first MicroPython serial port found)")
    ap.add_argument("--pin", help="PIN shown on the device's PC Link screen (WiFi only)")
    ap.add_argument("--baud", type=int, default=115200, help="UART baudrate (USE_PIO_USB=OFF builds)")
    ap.add_argument("cmd", choices=["ports", "ls", "df", "get", "put", "rm", "rmdir", "mkdir", "bye"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)

    if a.cmd == "ports":
        for dev, desc in find_ports():
            print("%s\t%s" % (dev, desc))
        return 0

    port = a.port
    if port is None:
        ports = find_ports()
        if not ports:
            print("no serial port found", file=sys.stderr)
            return 1
        port = ports[0][0]

    pin = a.pin
    if is_network_target(port) and not pin:
        pin = input("PIN shown on the device: ")
    with PCLink(port, a.baud, pin=pin) as link:
        if a.cmd == "ls":
            for e in link.listdir(a.args[0] if a.args else "/sd"):
                print("%s %10d  %s" % ("d" if e.is_dir else "-", e.size, e.name))
        elif a.cmd == "df":
            total, free = link.disk_free()
            print("total %d  free %d" % (total, free))
        elif a.cmd == "get":
            t = time.time()
            data = link.get(a.args[0])
            local = a.args[1] if len(a.args) > 1 else a.args[0].rsplit("/", 1)[1]
            with open(local, "wb") as f:
                f.write(data)
            dt = max(time.time() - t, 1e-6)
            print("%s: %d bytes, %.1f KB/s" % (local, len(data), len(data) / 1024 / dt))
        elif a.cmd == "put":
            with open(a.args[0], "rb") as f:
                data = f.read()
            t = time.time()
            link.put(a.args[1], data)
            dt = max(time.time() - t, 1e-6)
            print("%s: %d bytes, %.1f KB/s" % (a.args[1], len(data), len(data) / 1024 / dt))
        elif a.cmd == "rm":
            link.remove(a.args[0])
        elif a.cmd == "rmdir":
            link.rmdir(a.args[0])
        elif a.cmd == "mkdir":
            link.mkdir(a.args[0])
        elif a.cmd == "bye":
            link.bye()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except LinkError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)
