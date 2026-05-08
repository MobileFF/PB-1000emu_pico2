"""FDD Protocol layer: faithful Python port of fdd.pas (Casio pb1000es).

Implements the MD-100 command/data protocol as a flat cmdtab state machine.
One call to transfer() per STR pulse, matching Delphi FddTransfer() exactly.
"""

from md100_dos import (
    MD100Dos,
    SIZE_DIR_ENTRY, SIZE_FILE_NAME,
    SP_OCCUPIED,
    DS_NO_ERROR, DS_FILE_NOT_OPENED,
    _DE_NAME, _DE_BLOCK0, _DE_BLOCK1,
    SIZE_BLOCK, SIZE_SECTOR,
)

# fdd.pas constants
SEC_COUNT = 16
SEC_BASE  = 1
BUFSIZE   = 1024

# MD-100 status codes (fdd.pas)
MD_FILE_NOT_OPENED = 0x80
MD_NO_ROOM         = 0x40
MD_INVALID_COMMAND = 0x20
MD_FILE_FOUND      = 0x10
MD_RENAME_FAILED   = 0x08
MD_NO_DATA         = 0x04
MD_WRITE_PROTECTED = 0x02
MD_END_OF_FILE     = 0x01
MD_OK              = 0x00

# CnvStatus table: TDosStatusCode → MD-100 byte  (fdd.pas:159-174)
from md100_dos import (
    DS_RENAME_FAILED, DS_FILE_NOT_FOUND, DS_HANDLE_IN_USE,
    DS_NO_ROOM, DS_HANDLE_INVALID, DS_NO_DATA, DS_IO_ERROR,
)
_DSTATUS_TO_MD = {
    DS_NO_ERROR:        MD_OK,
    DS_RENAME_FAILED:   MD_RENAME_FAILED | MD_FILE_FOUND,
    DS_FILE_NOT_FOUND:  MD_INVALID_COMMAND,
    DS_FILE_NOT_OPENED: MD_FILE_NOT_OPENED,
    DS_HANDLE_IN_USE:   MD_INVALID_COMMAND,
    DS_NO_ROOM:         MD_NO_ROOM,
    DS_HANDLE_INVALID:  MD_INVALID_COMMAND,
    DS_NO_DATA:         MD_NO_DATA,
    DS_IO_ERROR:        MD_WRITE_PROTECTED,
}


def _cnv_status(dstatus):
    return _DSTATUS_TO_MD.get(dstatus, MD_INVALID_COMMAND)


class FDDProtocol:
    """Port of fdd.pas. Maintains identical state to the Delphi implementation."""

    def __init__(self, dos=None):
        self._dos = dos
        self._index   = 0
        self._cmdcode = 0
        self._opstatus = 0
        self._count   = 1
        self._bufindex = 0
        self._deindex  = 0
        self._buffer   = bytearray(BUFSIZE)
        # Build cmdtab as a list of bound methods (indices 0-58)
        self._cmdtab = [
            self._switch_cmd,        # 0
            self._exec_dir,          # 1
            self._return_count_hi,   # 2
            self._return_block,      # 3
            self._switch_cmd,        # 4
            self._exec_close_file,   # 5
            self._switch_cmd,        # 6
            self._accept_count_lo,   # 7
            self._accept_count_hi,   # 8
            self._accept_block,      # 9
            self._exec_open_file,    # 10
            self._return_count_hi,   # 11
            self._return_block,      # 12
            self._switch_cmd,        # 13
            self._accept_count_lo,   # 14
            self._accept_count_hi,   # 15
            self._accept_block,      # 16
            self._exec_read_file,    # 17
            self._return_count_hi,   # 18
            self._return_block,      # 19
            self._switch_cmd,        # 20
            self._accept_count_lo,   # 21  (track number for READ SECTOR)
            self._exec_read_sector,  # 22
            self._return_block,      # 23
            self._switch_cmd,        # 24
            self._accept_count_lo,   # 25
            self._accept_count_hi,   # 26
            self._accept_block,      # 27
            self._exec_kill_file,    # 28
            self._return_count_hi,   # 29
            self._return_block,      # 30
            self._switch_cmd,        # 31
            self._accept_count_lo,   # 32
            self._accept_count_hi,   # 33
            self._accept_block,      # 34
            self._exec_rename_file,  # 35
            self._return_count_hi,   # 36
            self._return_block,      # 37
            self._switch_cmd,        # 38
            self._accept_count_lo,   # 39
            self._accept_count_hi,   # 40
            self._exec_write_sector, # 41
            self._switch_cmd,        # 42
            self._accept_count_lo,   # 43
            self._accept_count_hi,   # 44
            self._exec_write_file,   # 45  ← called per-byte
            self._return_count_lo,   # 46
            self._return_count_hi,   # 47
            self._return_block,      # 48
            self._switch_cmd,        # 49
            self._accept_count_lo,   # 50  (file handle for GET SIZE)
            self._exec_get_size,     # 51
            self._return_count_lo,   # 52
            self._return_count_hi,   # 53
            self._switch_cmd,        # 54
            self._exec_get_free,     # 55
            self._return_count_lo,   # 56
            self._return_count_hi,   # 57
            self._switch_cmd,        # 58
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_dos(self, dos):
        self._dos = dos

    def fdd_open(self):
        """FddOpen: reset state machine."""
        self._index    = 0
        self._cmdcode  = 0
        self._opstatus = 0
        self._count    = 1
        self._bufindex = 0
        self._deindex  = 0

    # keep old names for pb1000.py compatibility
    def open(self):
        self.fdd_open()

    def close(self):
        if self._dos:
            self._dos.close_disk_file(0xFF)

    def transfer(self, data_in):
        """FddTransfer: dispatch one STR pulse. Returns response byte."""
        return self._cmdtab[self._index](data_in & 0xFF) & 0xFF

    # ------------------------------------------------------------------
    # SwitchCmd  (cmdtab[0] / [4] / [6] / ... )
    # ------------------------------------------------------------------

    def _switch_cmd(self, x):
        self._cmdcode  = x
        self._opstatus = MD_NO_DATA   # disk not inserted
        self._index    = 0
        if not self._dos or not self._dos.is_ready():
            return self._opstatus

        self._opstatus = MD_OK
        _dispatch = {
            0x00: 1,  0x01: 1,  0x02: 1,
            0x10: 43, 0x11: 43,
            0x20: 14, 0x21: 14,
            0x30: 7,  0x31: 7,  0x32: 7,  0x33: 7,  0x34: 7,
            0x40: 5,
            0x50: 25,
            0x60: 32,
            0x70: 39,
            0x80: 21,
            0xC0: 50,
            0xD0: 55,
        }
        idx = _dispatch.get(x)
        if idx is not None:
            self._index = idx
        elif x == 0x90:   # FORMAT DISK
            if not self._dos.format_disk():
                self._opstatus = MD_NO_DATA
        else:
            self._opstatus = MD_INVALID_COMMAND
        return self._opstatus

    # ------------------------------------------------------------------
    # AcceptCountLo / AcceptCountHi / AcceptBlock
    # ------------------------------------------------------------------

    def _accept_count_lo(self, x):
        self._index   += 1
        self._count    = x
        self._bufindex = 0
        return self._opstatus

    def _accept_count_hi(self, x):
        self._index += 1
        self._count |= (x << 8)
        if self._count == 0:
            self._opstatus = MD_INVALID_COMMAND
            self._index    = 0
        return self._opstatus

    def _accept_block(self, x):
        if self._bufindex < BUFSIZE:
            self._buffer[self._bufindex] = x
            self._bufindex += 1
        self._count -= 1
        if self._count <= 0:
            self._index += 1
        return self._opstatus

    # ------------------------------------------------------------------
    # ReturnCountLo / ReturnCountHi / ReturnBlock
    # ------------------------------------------------------------------

    def _return_count_lo(self, x):
        self._index   += 1
        self._bufindex = 0
        return self._count & 0xFF

    def _return_count_hi(self, x):
        self._index += 1
        return (self._count >> 8) & 0xFF

    def _return_block(self, x):
        result = self._buffer[self._bufindex] if self._bufindex < BUFSIZE else 0
        if self._bufindex < BUFSIZE - 1:
            self._bufindex += 1
        self._count -= 1
        if self._count <= 0:
            self._index += 1
        return result

    # ------------------------------------------------------------------
    # ExecDir  (fdd.pas:212-250)
    # ------------------------------------------------------------------

    def _exec_dir(self, x):
        self._index   += 1
        self._bufindex = 0
        cmd = self._cmdcode
        if cmd == 0x00:
            i    = -1
            step = 1
        elif cmd == 0x01:
            i    = self._deindex
            step = 1
        else:   # 0x02
            i    = self._deindex
            step = -1

        while True:
            i += step
            sp, entry = self._dos.read_dir_entry(i)
            if sp != SP_OCCUPIED and sp != 1:   # not SP_FREE == 1 means error
                break
            if sp == SP_OCCUPIED:
                break

        if sp == SP_OCCUPIED:
            self._deindex    = i
            self._buffer[0]  = MD_FILE_FOUND
            self._buffer[1:1 + SIZE_DIR_ENTRY] = entry[:SIZE_DIR_ENTRY]
            self._count      = SIZE_DIR_ENTRY + 1   # 17
        else:
            self._buffer[0] = MD_END_OF_FILE
            self._count     = 1
        return self._count & 0xFF   # Lo(count)

    # ------------------------------------------------------------------
    # ExecCloseFile  (fdd.pas:255-262)
    # ------------------------------------------------------------------

    def _exec_close_file(self, x):
        self._index += 1
        self._dos.close_disk_file(x & 0xFF)
        if self._dos.dos_status == DS_FILE_NOT_OPENED:
            self._opstatus = MD_OK
        else:
            self._opstatus = _cnv_status(self._dos.dos_status)
        return self._opstatus

    # ------------------------------------------------------------------
    # ExecOpenFile  (fdd.pas:265-309)
    # ------------------------------------------------------------------

    def _exec_open_file(self, x):
        self._index += 1
        file_handle = self._buffer[1] & 0x0F
        self._dos.put_disk_file_tag(file_handle, self._cmdcode)

        name11 = bytes(self._buffer[3:3 + SIZE_FILE_NAME])

        i = -1
        if self._cmdcode != 0x30:   # sequential output: skip open, go straight to create
            i = self._dos.open_disk_file(file_handle, name11)
        if i < 0 and self._cmdcode < 0x32:   # $30 and $31 may create
            i = self._dos.create_disk_file(file_handle, name11, self._buffer[2])

        self._count = 1
        if i >= 0:
            if self._cmdcode != 0x30:
                sp, entry = self._dos.read_dir_entry(i)
                if sp == SP_OCCUPIED:
                    self._count = SIZE_DIR_ENTRY + 1   # 17
                    self._buffer[1:1 + SIZE_DIR_ENTRY] = entry[:SIZE_DIR_ENTRY]
                    if self._cmdcode == 0x34:   # append: seek to last record, load it
                        position = self._dos.size_of_disk_file(file_handle)
                        if position > 0:
                            self._dos.seek_abs_disk_file(file_handle, position - 1)
                            n = self._dos.read_disk_file(file_handle, memoryview(self._buffer)[17:])
                            self._count += n
                            # trim trailing zeros and Ctrl-Z
                            while self._count > 17 and self._buffer[self._count] == 0:
                                self._count -= 1
                            if self._count > 17 and self._buffer[self._count] == 0x1A:
                                self._count -= 1

        self._opstatus = _cnv_status(self._dos.dos_status)
        if self._count > 1:
            self._opstatus |= MD_FILE_FOUND
        self._buffer[0] = self._opstatus
        self._bufindex  = 0
        return self._count & 0xFF

    # ------------------------------------------------------------------
    # ExecReadFile  (fdd.pas:312-342)
    # ------------------------------------------------------------------

    def _exec_read_file(self, x):
        self._index += 1
        file_handle = self._buffer[1] & 0x0F
        if self._cmdcode == 0x21:   # random read: seek first
            position = self._buffer[2] | (self._buffer[3] << 8)
            if position == 0:
                position = 1
            self._dos.seek_abs_disk_file(file_handle, position - 1)

        n = self._dos.read_disk_file(file_handle, memoryview(self._buffer)[1:])
        self._dos.seek_rel_disk_file(file_handle, 1)

        if self._dos.dos_status == DS_NO_ERROR:
            if self._dos.is_end_of_disk_file(file_handle):
                # Last record: strip trailing zeros and Ctrl-Z
                while n > 0 and self._buffer[n] == 0:
                    n -= 1
                if n > 0 and self._buffer[n] == 0x1A:
                    n -= 1
                self._opstatus = MD_END_OF_FILE
        else:
            self._opstatus = _cnv_status(self._dos.dos_status)

        self._count      = n + 1
        self._buffer[0]  = self._opstatus
        self._bufindex   = 0
        return self._count & 0xFF

    # ------------------------------------------------------------------
    # ExecWriteFile  (fdd.pas:345-411)  — called PER BYTE from cmdtab[45]
    # ------------------------------------------------------------------

    def _exec_write_file(self, x):
        if self._bufindex < BUFSIZE:
            self._buffer[self._bufindex] = x
            self._bufindex += 1
        self._count -= 1

        if self._count <= 0:
            # === Final byte: complete the write ===
            self._index += 1   # → _return_count_lo
            if self._cmdcode == 0:   # normalised sequential path
                file_handle = self._buffer[1] & 0x0F
                if self._dos.is_end_of_disk_file(file_handle) and self._bufindex < BUFSIZE - 1:
                    self._buffer[self._bufindex] = 0x1A   # append Ctrl-Z
                    self._bufindex += 1
                # Pad remainder of buffer with zeros
                for k in range(self._bufindex, BUFSIZE):
                    self._buffer[k] = 0
                # Write data from buffer[2] in SIZE_SECTOR chunks
                i = 2
                while i < self._bufindex:
                    if self._dos.write_disk_file(file_handle, memoryview(self._buffer)[i:i + SIZE_SECTOR]) != SIZE_SECTOR:
                        break
                    i += SIZE_SECTOR
                    if i <= self._bufindex:
                        self._dos.seek_rel_disk_file(file_handle, 1)
                if self._opstatus == MD_OK:
                    self._opstatus = _cnv_status(self._dos.dos_status)
            self._buffer[0] = self._opstatus
            self._count     = 1

        else:
            # === Intermediate byte: conditional mid-record flush ===
            if self._cmdcode == 0 and self._bufindex == SIZE_SECTOR + 2:
                # Sequential: 256 bytes accumulated → flush immediately
                self._bufindex  = 2
                file_handle = self._buffer[1] & 0x0F
                if self._dos.write_disk_file(file_handle, memoryview(self._buffer)[2:2 + SIZE_SECTOR]) != SIZE_SECTOR:
                    if self._opstatus == MD_OK:
                        self._opstatus = _cnv_status(self._dos.dos_status)
                self._dos.seek_rel_disk_file(file_handle, 1)

            elif self._cmdcode == 0x10 and self._bufindex == 2:
                # Sequential WRITE ($10): handle byte received → normalise to 0
                self._cmdcode = 0

            elif self._cmdcode == 0x11 and self._bufindex == 4:
                # Random WRITE ($11): position bytes received → seek and normalise
                self._cmdcode = 0
                self._bufindex = 2
                file_handle = self._buffer[1] & 0x0F
                position = self._buffer[2] | (self._buffer[3] << 8)
                if position == 0:
                    position = 1
                self._dos.seek_abs_disk_file(file_handle, position - 1)

        return MD_OK

    # ------------------------------------------------------------------
    # ExecKillFile  (fdd.pas:415-429)
    # ------------------------------------------------------------------

    def _exec_kill_file(self, x):
        self._index += 1
        name11 = bytes(self._buffer[2:2 + SIZE_FILE_NAME])
        self._dos.delete_disk_file(name11)
        if self._dos.dos_status == DS_NO_ERROR:
            self._opstatus = MD_FILE_FOUND
        elif self._dos.dos_status == DS_FILE_NOT_FOUND:   # missing file: silent OK
            self._opstatus = MD_OK
        else:
            self._opstatus = _cnv_status(self._dos.dos_status)
        self._count     = 1
        self._buffer[0] = self._opstatus
        self._bufindex  = 0
        return self._count & 0xFF

    # ------------------------------------------------------------------
    # ExecRenameFile  (fdd.pas:432-446)
    # ------------------------------------------------------------------

    def _exec_rename_file(self, x):
        self._index += 1
        old11 = bytes(self._buffer[2:2 + SIZE_FILE_NAME])
        new11 = bytes(self._buffer[19:19 + SIZE_FILE_NAME])
        self._dos.rename_disk_file(old11, new11)
        if self._dos.dos_status == DS_NO_ERROR:
            self._opstatus = MD_FILE_FOUND
        elif self._dos.dos_status == DS_FILE_NOT_FOUND:
            self._opstatus = MD_OK
        else:
            self._opstatus = _cnv_status(self._dos.dos_status)
        self._count     = 1
        self._buffer[0] = self._opstatus
        self._bufindex  = 0
        return self._count & 0xFF

    # ------------------------------------------------------------------
    # ExecReadSector  (fdd.pas:450-462)
    # ------------------------------------------------------------------

    def _exec_read_sector(self, x):
        """Called with sector number; count holds track number from AcceptCountLo."""
        self._index += 1
        abs_sector = self._count * SEC_COUNT + x - SEC_BASE
        n = self._dos.dos_sec_read(abs_sector)
        if n == 0:
            self._opstatus = MD_INVALID_COMMAND
            self._index    = 0
        else:
            # secbuf is in _dos._secbuf; copy to our buffer for ReturnBlock
            self._buffer[:SIZE_SECTOR] = self._dos._secbuf[:SIZE_SECTOR]
            self._count = n
        self._bufindex = 0
        return self._opstatus

    # ------------------------------------------------------------------
    # ExecWriteSector  (fdd.pas:465-483)  — called per byte
    # ------------------------------------------------------------------

    def _exec_write_sector(self, x):
        if self._bufindex < BUFSIZE:
            self._buffer[self._bufindex] = x
            self._bufindex += 1
        self._count -= 1
        if self._count <= 0:
            self._index += 1
            abs_sector = self._buffer[1] * SEC_COUNT + self._buffer[2] - SEC_BASE
            if self._dos.dos_sec_write(abs_sector, memoryview(self._buffer)[3:]) == 0:
                self._opstatus = MD_WRITE_PROTECTED
            self._bufindex = 0
            self._count    = 0
        return self._opstatus

    # ------------------------------------------------------------------
    # ExecGetSize  (fdd.pas:487-494)
    # ------------------------------------------------------------------

    def _exec_get_size(self, x):
        self._index += 1
        # count was set to file_handle by AcceptCountLo
        file_handle = self._count & 0x0F
        self._count = self._dos.size_of_disk_file(file_handle)
        if self._count == 0:
            self._opstatus = MD_FILE_NOT_OPENED
        elif (self._dos.get_disk_file_tag(file_handle) & 1) != 0:
            self._count -= 1
        return self._opstatus

    # ------------------------------------------------------------------
    # ExecGetFree  (fdd.pas:498-503)
    # ------------------------------------------------------------------

    def _exec_get_free(self, x):
        self._index += 1
        self._count = self._dos.get_free_disk_space()
        return self._opstatus

    # ------------------------------------------------------------------
    # Compatibility shim for pb1000.py status string
    # ------------------------------------------------------------------

    @property
    def status_str(self):
        if self._index < len(self._cmdtab):
            name = getattr(self._cmdtab[self._index], "__name__", "?")
        else:
            name = "?"
        return "cmd=%02x idx=%d hnd=%s cnt=%d" % (
            self._cmdcode, self._index, name, self._count)
