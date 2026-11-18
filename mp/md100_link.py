"""MD-100 virtual disk: byte-level link protocol layer.

Implements the PB-1000 <-> MD-100 command protocol (one byte in, one byte
out per exchange) on top of md100_volume.Md100Volume.

Runs unchanged on MicroPython and CPython.  exchange() is called once per
transferred byte, so it avoids per-call heap allocation.
"""

import md100_volume as _v

BUF_SIZE = 1024
_ZEROS = memoryview(bytearray(256))

# MD-100 status bytes
S_NOT_OPEN = 0x80
S_NO_ROOM = 0x40
S_BAD_CMD = 0x20
S_FILE = 0x10
S_RENAME = 0x08
S_NO_DATA = 0x04
S_PROTECT = 0x02
S_EOF = 0x01
S_OK = 0x00

# FS status -> MD-100 status byte (indexed by md100_volume ST_* value)
_CONV = bytearray(9)
_CONV[_v.ST_OK] = 0x00
_CONV[_v.ST_TARGET_EXISTS] = 0x18
_CONV[_v.ST_NOT_FOUND] = 0x20
_CONV[_v.ST_NOT_OPEN] = 0x80
_CONV[_v.ST_HANDLE_BUSY] = 0x20
_CONV[_v.ST_NO_ROOM] = 0x40
_CONV[_v.ST_BAD_HANDLE] = 0x20
_CONV[_v.ST_NO_DATA] = 0x04
_CONV[_v.ST_IO_ERROR] = 0x02

# command classes
_K_NONE = 0
_K_DIR = 1
_K_CLOSE = 2
_K_OPEN = 3
_K_READ = 4
_K_WRITE = 5
_K_DELETE = 6
_K_RENAME = 7
_K_SREAD = 8
_K_SWRITE = 9
_K_SIZE = 10
_K_FREE = 11

_KIND = bytearray(256)
for _c in (0x00, 0x01, 0x02):
    _KIND[_c] = _K_DIR
_KIND[0x40] = _K_CLOSE
for _c in range(0x30, 0x35):
    _KIND[_c] = _K_OPEN
_KIND[0x20] = _K_READ
_KIND[0x21] = _K_READ
_KIND[0x10] = _K_WRITE
_KIND[0x11] = _K_WRITE
_KIND[0x50] = _K_DELETE
_KIND[0x60] = _K_RENAME
_KIND[0x80] = _K_SREAD
_KIND[0x70] = _K_SWRITE
_KIND[0xC0] = _K_SIZE
_KIND[0xD0] = _K_FREE
# note: 0x00 maps to _K_DIR, so "no command" is tracked separately (_busy).

# phases
_P_IDLE = 0
_P_LEN0 = 1
_P_LEN1 = 2
_P_DATA = 3
_P_EXEC = 4      # dummy byte: run the command, answer N low
_P_NLO = 5       # dummy byte: answer N low (already executed)
_P_NHI = 6
_P_SEND = 7
_P_CLOSE_H = 8
_P_SR_TRACK = 9
_P_SR_SECTOR = 10
_P_SIZE_H = 11
_P_SIZE_EXEC = 12
_P_LO = 13       # answer self._val low byte
_P_HI = 14       # answer self._val high byte, then idle
_P_FREE_EXEC = 15


def _conv(st):
    if 0 <= st < len(_CONV):
        return _CONV[st]
    return S_PROTECT


class Md100Link:
    def __init__(self, volume=None):
        self._buf = bytearray(BUF_SIZE)
        self._mv = memoryview(self._buf)
        self._vol = None
        self._cmd = 0
        self._kind = _K_NONE
        self._phase = _P_IDLE
        self._status = 0
        self._last_dir = 0
        self._len = 0
        self._rx = 0         # bytes of payload received
        self._wp = 0         # store position in buffer
        self._n = 0          # result length
        self._tx = 0         # result bytes sent
        self._val = 0        # 16-bit value for size / free
        self._track = 0
        self._wstage = False
        self._wh = 0
        self.attach(volume)

    def attach(self, volume):
        self._vol = volume
        self.reset()

    @property
    def volume(self):
        """The attached Md100Volume, or None."""
        return self._vol

    def reset(self):
        self._phase = _P_IDLE
        self._kind = _K_NONE
        self._status = 0
        self._last_dir = 0

    def shutdown(self):
        if self._vol is not None:
            self._vol.close(0x80)

    # ------------------------------------------------------- exchange
    def exchange(self, byte):
        byte &= 0xFF
        p = self._phase
        if p == _P_IDLE:
            return self._accept(byte)
        if p == _P_DATA:
            return self._data(byte)
        if p == _P_SEND:
            i = self._tx
            self._tx = i + 1
            if self._tx >= self._n:
                self._phase = _P_IDLE
            return self._buf[i if i < BUF_SIZE else BUF_SIZE - 1]
        if p == _P_LEN0:
            self._len = byte
            self._phase = _P_LEN1
            return self._status
        if p == _P_LEN1:
            self._len |= byte << 8
            if self._len == 0:
                self._status = S_BAD_CMD
                self._phase = _P_IDLE
                return S_BAD_CMD
            self._rx = 0
            self._wp = 0
            self._wstage = False
            self._phase = _P_DATA
            return self._status
        if p == _P_EXEC:
            self._execute()
            self._phase = _P_NHI
            return self._n & 0xFF
        if p == _P_NLO:
            self._phase = _P_NHI
            return self._n & 0xFF
        if p == _P_NHI:
            self._tx = 0
            self._phase = _P_SEND if self._n > 0 else _P_IDLE
            return (self._n >> 8) & 0xFF
        if p == _P_LO:
            self._phase = _P_HI
            return self._val & 0xFF
        if p == _P_HI:
            self._phase = _P_IDLE
            return (self._val >> 8) & 0xFF
        if p == _P_CLOSE_H:
            vol = self._vol
            vol.close(byte)
            st = vol.status
            self._status = 0x00 if st == _v.ST_NOT_OPEN else _conv(st)
            self._phase = _P_IDLE
            return self._status
        if p == _P_SR_TRACK:
            self._track = byte
            self._phase = _P_SR_SECTOR
            return self._status
        if p == _P_SR_SECTOR:
            sec = self._track * 16 + byte - 1
            if not self._vol.read_sector(sec, self._buf):
                self._status = S_BAD_CMD
                self._phase = _P_IDLE
                return S_BAD_CMD
            self._n = 256
            self._tx = 0
            self._phase = _P_SEND
            return self._status
        if p == _P_SIZE_H:
            self._val = byte
            self._phase = _P_SIZE_EXEC
            return self._status
        if p == _P_SIZE_EXEC:
            h = self._val & 0x0F
            size = self._vol.record_count(h)
            if size == 0:
                self._status = S_NOT_OPEN
            elif self._vol.get_mode(h) & 1:
                size -= 1
            self._val = size
            self._phase = _P_LO
            return self._status
        if p == _P_FREE_EXEC:
            self._val = self._vol.free_blocks()
            self._phase = _P_LO
            return self._status
        self._phase = _P_IDLE
        return S_BAD_CMD

    # --------------------------------------------------- command start
    def _accept(self, cmd):
        vol = self._vol
        if vol is None or not vol.is_ready():
            return S_NO_DATA
        if cmd == 0x90:
            return S_OK if vol.format() else S_NO_DATA
        k = _KIND[cmd]
        if k == _K_NONE:
            return S_BAD_CMD
        self._cmd = cmd
        self._kind = k
        self._status = S_OK
        if k == _K_DIR:
            self._phase = _P_EXEC
        elif k == _K_CLOSE:
            self._phase = _P_CLOSE_H
        elif k == _K_SREAD:
            self._phase = _P_SR_TRACK
        elif k == _K_SIZE:
            self._phase = _P_SIZE_H
        elif k == _K_FREE:
            self._phase = _P_FREE_EXEC
        else:
            self._phase = _P_LEN0
        return S_OK

    # ------------------------------------------------- data reception
    def _data(self, byte):
        wp = self._wp
        if wp < BUF_SIZE:
            self._buf[wp] = byte
        self._wp = wp + 1
        self._rx += 1
        last = self._rx >= self._len
        k = self._kind
        if k == _K_WRITE:
            if last:
                self._write_finish()
                self._n = 1
                self._phase = _P_NLO
            else:
                self._write_progress()
            return 0x00
        if not last:
            return self._status
        if k == _K_SWRITE:
            b = self._buf
            sec = b[1] * 16 + b[2] - 1
            if not self._vol.write_sector(sec, self._mv[3:259]):
                self._status = S_PROTECT
            self._phase = _P_IDLE
            return self._status
        self._phase = _P_EXEC
        return self._status

    def _write_progress(self):
        b = self._buf
        vol = self._vol
        if not self._wstage:
            if self._cmd == 0x10:
                if self._wp == 2:
                    self._wh = b[1] & 0x0F
                    self._wstage = True
            elif self._wp == 4:
                self._wh = b[1] & 0x0F
                rec = b[2] | (b[3] << 8)
                if rec == 0:
                    rec = 1
                vol.seek(self._wh, rec - 1)
                self._wstage = True
                self._wp = 2
            return
        if self._wp == 258:
            h = self._wh
            if vol.write_record(h, self._mv[2:258]) < 256 and self._status == 0:
                self._status = _conv(vol.status)
            vol.skip(h, 1)
            self._wp = 2

    def _write_finish(self):
        b = self._buf
        if self._wstage:
            vol = self._vol
            h = self._wh
            end = self._wp
            if vol.at_last_record(h) and end < BUF_SIZE:
                b[end] = 0x1A
                end += 1
            count = end - 2
            if count > 0:
                recs = (count + 255) // 256
                stop = 2 + recs * 256
                self._mv[end:stop] = _ZEROS[0:stop - end]
                pos = 2
                for r in range(recs):
                    if vol.write_record(h, self._mv[pos:pos + 256]) < 256:
                        break
                    pos += 256
                    if pos <= end:     # record held exactly 256 data bytes
                        vol.skip(h, 1)
            if self._status == 0:
                self._status = _conv(vol.status)
        b[0] = self._status

    # ----------------------------------------------------- execution
    def _execute(self):
        k = self._kind
        if k == _K_DIR:
            self._exec_dir()
        elif k == _K_OPEN:
            self._exec_open()
        elif k == _K_READ:
            self._exec_read()
        elif k == _K_DELETE or k == _K_RENAME:
            vol = self._vol
            mv = self._mv
            if k == _K_DELETE:
                vol.delete(mv[2:13])
            else:
                vol.rename(mv[2:13], mv[19:30])
            st = vol.status
            if st == _v.ST_OK:
                r = S_FILE
            elif st == _v.ST_NOT_FOUND:
                r = S_OK
            else:
                r = _conv(st)
            self._status = r
            self._buf[0] = r
            self._n = 1

    def _exec_dir(self):
        vol = self._vol
        cmd = self._cmd
        if cmd == 0x00:
            i, step = 0, 1
        elif cmd == 0x01:
            i, step = self._last_dir + 1, 1
        else:
            i, step = self._last_dir - 1, -1
        while True:
            st, data = vol.dir_entry(i)
            if st == _v.DIR_FREE:
                i += step
                continue
            break
        b = self._buf
        if st == _v.DIR_USED:
            b[0] = S_FILE
            self._mv[1:17] = data
            self._n = 17
            self._last_dir = i
        else:
            b[0] = S_EOF
            self._n = 1

    def _exec_open(self):
        vol = self._vol
        b = self._buf
        mv = self._mv
        cmd = self._cmd
        h = b[1] & 0x0F
        kind = b[2]
        name = mv[3:14]
        vol.set_mode(h, cmd)
        if cmd == 0x30:
            d = vol.create(h, name, kind)
        else:
            d = vol.open(h, name)
            if d < 0 and cmd == 0x31:
                d = vol.create(h, name, kind)
        n = 1
        if d >= 0 and cmd != 0x30:
            st, data = vol.dir_entry(d)
            if st == _v.DIR_USED:
                mv[1:17] = data
                n = 17
        if cmd == 0x34 and n == 17:
            r = vol.record_count(h)
            if r >= 1:
                vol.seek(h, r - 1)
                got = vol.read_record(h, mv[17:273])
                if got > 0:
                    n += got
                    while n > 17 and b[n - 1] == 0:
                        n -= 1
                    if n > 17 and b[n - 1] == 0x1A:
                        n -= 1
        st = _conv(vol.status)
        if n > 1:
            st |= S_FILE
        self._status = st
        b[0] = st
        self._n = n

    def _exec_read(self):
        vol = self._vol
        b = self._buf
        h = b[1] & 0x0F
        if self._cmd == 0x21:
            rec = b[2] | (b[3] << 8)
            if rec == 0:
                rec = 1
            vol.seek(h, rec - 1)
        n = vol.read_record(h, self._mv[1:257])
        vol.skip(h, 1)
        if n > 0:
            if vol.at_last_record(h):
                while n > 0 and b[n] == 0:
                    n -= 1
                if n > 0 and b[n] == 0x1A:
                    n -= 1
                self._status = S_EOF
        else:
            self._status = _conv(vol.status)
        self._n = n + 1
        b[0] = self._status
