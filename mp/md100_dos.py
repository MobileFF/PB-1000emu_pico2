"""MD-100 DOS layer: faithful Python port of dos.pas (Casio pb1000es)."""

from fdd_storage import SIZE_SECTOR

# dos.pas layout constants
SECTORS_FAT   = 4
SECTORS_DIR   = 12
START_FAT     = 0
START_DIR     = 4
START_DATA    = 16
MAX_FILES     = 16
SIZE_FILE_NAME = 11   # 8 + 3
SIZE_DIR_ENTRY = 16
SIZE_BLOCK    = 4     # sectors per block
SIZE_FAT_ENTRY = 2
MAX_DIR_ENTRY = SECTORS_DIR * SIZE_SECTOR // SIZE_DIR_ENTRY   # 192
MAX_FAT_ENTRY = SECTORS_FAT * SIZE_SECTOR // SIZE_FAT_ENTRY   # 512

# FAT entry bit masks
FB_IN_USE = 0x8000
FB_LAST   = 0x4000
FB_SECTORS = 0x3000
FB_BLOCK  = 0x01FF

# TStorageProperty equivalents
SP_ERROR    = 0
SP_FREE     = 1
SP_OCCUPIED = 2

# TDosStatusCode equivalents
DS_NO_ERROR        = 0
DS_RENAME_FAILED   = 1
DS_FILE_NOT_FOUND  = 2
DS_FILE_NOT_OPENED = 3
DS_HANDLE_IN_USE   = 4
DS_NO_ROOM         = 5
DS_HANDLE_INVALID  = 6
DS_NO_DATA         = 7
DS_IO_ERROR        = 8

# TFileInfo field indices
_FI_DIRINDEX = 0
_FI_NEXTREC  = 1
_FI_FIRSTSEC = 2
_FI_LASTREC  = 3
_FI_LASTSEC  = 4
_FI_TAG      = 5

# TDirEntry byte offsets
_DE_KIND    = 0
_DE_NAME    = 1    # 8 bytes
_DE_EXT     = 9    # 3 bytes
_DE_UNUSED  = 12
_DE_BLOCK0  = 13   # MSB of starting block number
_DE_BLOCK1  = 14   # LSB
_DE_ATTR    = 15


def _my_min(x, y):
    return x if x < y else y


class MD100Dos:
    """Faithful port of dos.pas. Owns secbuf/secnum (sector cache)."""

    def __init__(self):
        self._backend = None
        self._secbuf = bytearray(SIZE_SECTOR)
        self._secnum = -1
        self._fileinfo = [[-1, 0, 0, 0, 0, 0] for _ in range(MAX_FILES)]
        self._direntrybuf = bytearray(SIZE_DIR_ENTRY)
        self.dos_status = DS_NO_ERROR

    # ------------------------------------------------------------------
    # Public: init / close
    # ------------------------------------------------------------------

    def dos_init(self, backend):
        """DosInit: attach a backend and initialise state."""
        self._backend = backend
        self._secnum = -1
        self._close_disk_file(0xFF)   # close all
        return backend is not None

    def dos_close(self):
        """DosClose"""
        self._backend = None
        self._secnum = -1

    def is_ready(self):
        return self._backend is not None

    # ------------------------------------------------------------------
    # Internal: sector cache  (MySecRead / MySecWrite)
    # ------------------------------------------------------------------

    def _my_sec_read(self, x):
        """MySecRead: read sector x into secbuf (skip if already cached)."""
        if x == self._secnum:
            return True
        self._secnum = -1
        raw = self._backend.read_raw(x)
        if raw is None or len(raw) < SIZE_SECTOR:
            return False
        self._secbuf[:] = raw[:SIZE_SECTOR]
        self._secnum = x
        return True

    def _my_sec_write(self, x):
        """MySecWrite: write secbuf to sector x."""
        self._secnum = -1
        if not self._backend.write_raw(x, self._secbuf):
            return False
        self._secnum = x
        return True

    # ------------------------------------------------------------------
    # FormatDisk  (fdd.pas $90 command calls this)
    # ------------------------------------------------------------------

    def format_disk(self):
        """FormatDisk: erase all sectors, mark system blocks in FAT."""
        self._secnum = -1
        self._close_disk_file(0xFF)
        sectors = self._backend.sector_count
        maxsector = _my_min(sectors, SIZE_BLOCK * MAX_FAT_ENTRY)
        # Zero out sectors 1..maxsector-1
        for i in range(SIZE_SECTOR):
            self._secbuf[i] = 0
        for i in range(1, maxsector):
            if not self._backend.write_raw(i, self._secbuf):
                return False
        # Mark system area in FAT (blocks 0..START_DATA/SIZE_BLOCK-1)
        system_bytes = (START_DATA // SIZE_BLOCK) * SIZE_FAT_ENTRY   # = 8
        for i in range(system_bytes):
            self._secbuf[i] = 0xFF
        for i in range(system_bytes, SIZE_SECTOR):
            self._secbuf[i] = 0
        if not self._backend.write_raw(0, self._secbuf):
            return False
        self._secnum = 0
        return True

    # ------------------------------------------------------------------
    # Directory entry access
    # ------------------------------------------------------------------

    def read_dir_entry(self, i):
        """ReadDirEntry: returns (SP_*, bytearray copy) or (SP_ERROR, None)."""
        if i < 0 or i >= MAX_DIR_ENTRY:
            return SP_ERROR, None
        x = i * SIZE_DIR_ENTRY
        s = x // SIZE_SECTOR + START_DIR
        if not self._my_sec_read(s):
            return SP_ERROR, None
        offset = x % SIZE_SECTOR
        self._direntrybuf[:] = self._secbuf[offset:offset + SIZE_DIR_ENTRY]
        # Free check: name[0]==0 and block[0]==0 and block[1]==0
        if (self._direntrybuf[_DE_NAME] == 0 and
                self._direntrybuf[_DE_BLOCK0] == 0 and
                self._direntrybuf[_DE_BLOCK1] == 0):
            return SP_FREE, self._direntrybuf
        return SP_OCCUPIED, self._direntrybuf

    def write_dir_entry(self, data, i):
        """WriteDirEntry: write 16-byte data to dir entry i."""
        if i < 0 or i >= MAX_DIR_ENTRY:
            return False
        x = i * SIZE_DIR_ENTRY
        s = x // SIZE_SECTOR + START_DIR
        if not self._my_sec_read(s):
            return False
        offset = x % SIZE_SECTOR
        self._secbuf[offset:offset + SIZE_DIR_ENTRY] = data[:SIZE_DIR_ENTRY]
        return self._my_sec_write(s)

    def find_dir_entry(self, name11):
        """FindDirEntry: scan directory for filename (11 bytes). Returns index or -1."""
        i = 0
        while True:
            sp, entry = self.read_dir_entry(i)
            if sp == SP_ERROR:
                break
            if sp == SP_OCCUPIED and entry[_DE_NAME:_DE_NAME + SIZE_FILE_NAME] == bytes(name11[:SIZE_FILE_NAME]):
                return i
            i += 1
        return -1

    # ------------------------------------------------------------------
    # FAT access
    # ------------------------------------------------------------------

    def _read_fat_entry(self, x):
        """ReadFatEntry: read 2-byte big-endian FAT entry for block containing sector x."""
        x = (x // SIZE_BLOCK) * SIZE_FAT_ENTRY   # byte offset in FAT
        s = x // SIZE_SECTOR + START_FAT
        if s >= START_FAT + SECTORS_FAT:
            return 0xFFFFFFFF
        if not self._my_sec_read(s):
            return 0xFFFFFFFF
        o = x % SIZE_SECTOR
        return (self._secbuf[o] << 8) | self._secbuf[o + 1]

    def _write_fat_entry(self, x, y):
        """WriteFatEntry: write 2-byte big-endian FAT entry for block containing sector x."""
        x = (x // SIZE_BLOCK) * SIZE_FAT_ENTRY
        s = x // SIZE_SECTOR + START_FAT
        if s >= START_FAT + SECTORS_FAT:
            return False
        if not self._my_sec_read(s):
            return False
        o = x % SIZE_SECTOR
        self._secbuf[o]     = (y >> 8) & 0xFF
        self._secbuf[o + 1] = y & 0xFF
        return self._my_sec_write(s)

    def _find_free_block(self):
        """FindFreeBlock: find first free block. Returns first sector of block, or 0."""
        sectors = self._backend.sector_count
        maxsector = _my_min(sectors, SIZE_BLOCK * MAX_FAT_ENTRY)
        x = START_DATA
        while x < maxsector:
            if (self._read_fat_entry(x) & FB_IN_USE) == 0:
                return x
            x += SIZE_BLOCK
        return 0

    def _fat_next_sector(self, x, allocate):
        """FatNextSector: return next sector in chain, or 0 at EOF/error."""
        if x < START_DATA:
            # New file: allocate first block
            if not allocate:
                return 0
            y = self._find_free_block()
            if y == 0:
                return 0
            entry = FB_IN_USE | FB_LAST | (y // SIZE_BLOCK)
            if not self._write_fat_entry(y, entry):
                return 0
            return y

        # Existing file
        entry = self._read_fat_entry(x)
        if entry > 0xFFFF:
            return 0
        if (entry & FB_IN_USE) == 0:
            return 0

        if (entry & FB_LAST) != 0:
            # Last block in chain
            if (x & (SIZE_BLOCK - 1)) >= ((entry & FB_SECTORS) >> 12):
                # End of file
                if not allocate:
                    return 0
                if (x & (SIZE_BLOCK - 1)) < (SIZE_BLOCK - 1):
                    # Extend in same block
                    if not self._write_fat_entry(x + 1, entry + 0x1000):
                        return 0
                    return x + 1
                else:
                    # Need a new block
                    y = self._find_free_block()
                    if y == 0:
                        return 0
                    new_entry = FB_IN_USE | (y // SIZE_BLOCK)
                    if not self._write_fat_entry(x, new_entry):
                        return 0
                    new_entry2 = FB_IN_USE | FB_LAST | (y // SIZE_BLOCK)
                    if not self._write_fat_entry(y, new_entry2):
                        return 0
                    return y
            else:
                # Not yet at EOF within last block
                return x + 1
        else:
            # Not the last block
            if (x & (SIZE_BLOCK - 1)) < (SIZE_BLOCK - 1):
                return x + 1
            else:
                # Follow FAT chain to next block
                x = (entry & FB_BLOCK) * SIZE_BLOCK
                entry = self._read_fat_entry(x)
                if entry > 0xFFFF:
                    return 0
                if (entry & FB_IN_USE) != 0:
                    return x
                return 0

    def _fat_free_chain(self, x):
        """FatFreeChain: free all blocks in the chain starting at sector x."""
        while True:
            entry = self._read_fat_entry(x)
            if entry > 0xFFFF:
                return False
            # Clear in-use flag, keep low byte (block pointer direction cleared)
            if not self._write_fat_entry(x, entry & 0x00FF):
                return False
            if (entry & FB_IN_USE) == 0 or (entry & FB_LAST) != 0:
                break
            x = (entry & FB_BLOCK) * SIZE_BLOCK
        return True

    # ------------------------------------------------------------------
    # File handle validation
    # ------------------------------------------------------------------

    def _check_file_handle(self, handle):
        if handle >= MAX_FILES:
            return DS_HANDLE_INVALID
        if self._fileinfo[handle][_FI_DIRINDEX] < 0:
            return DS_FILE_NOT_OPENED
        return DS_NO_ERROR

    # ------------------------------------------------------------------
    # File open / create / close
    # ------------------------------------------------------------------

    def open_disk_file(self, handle, name11):
        """OpenDiskFile: open existing file. Returns dir index or -1."""
        self.dos_status = DS_HANDLE_INVALID
        if handle >= MAX_FILES:
            return -1
        fi = self._fileinfo[handle]
        self.dos_status = DS_HANDLE_IN_USE
        if fi[_FI_DIRINDEX] >= 0:
            return -1
        self.dos_status = DS_FILE_NOT_FOUND
        idx = self.find_dir_entry(name11)
        fi[_FI_DIRINDEX] = idx
        if idx < 0:
            return -1
        # _direntrybuf was populated by find_dir_entry's last read_dir_entry call
        block = (self._direntrybuf[_DE_BLOCK0] << 8) | self._direntrybuf[_DE_BLOCK1]
        firstsec = SIZE_BLOCK * block
        fi[_FI_NEXTREC]  = 0
        fi[_FI_FIRSTSEC] = firstsec
        fi[_FI_LASTREC]  = 0
        fi[_FI_LASTSEC]  = firstsec
        self.dos_status = DS_NO_ERROR
        return idx

    def create_disk_file(self, handle, name11, filekind):
        """CreateDiskFile: create new file (deletes existing same-name first). Returns dir index or -1."""
        self.dos_status = DS_HANDLE_INVALID
        if handle >= MAX_FILES:
            return -1
        # Delete existing file with same name
        self.delete_disk_file(name11)
        # Find free directory entry
        i = 0
        while True:
            sp, _ = self.read_dir_entry(i)
            if sp != SP_OCCUPIED:
                break
            i += 1
        self.dos_status = DS_NO_ROOM
        if sp != SP_FREE:
            return -1
        # Allocate first FAT block
        ensec = self._fat_next_sector(0, True)
        if ensec == 0:
            return -1
        self.dos_status = DS_IO_ERROR
        enblk = ensec // SIZE_BLOCK
        if not self._write_fat_entry(ensec, FB_IN_USE | FB_LAST | enblk):
            return -1
        # Build and write directory entry
        de = bytearray(SIZE_DIR_ENTRY)
        de[_DE_KIND] = filekind & 0xFF
        de[_DE_NAME:_DE_NAME + SIZE_FILE_NAME] = bytes(name11[:SIZE_FILE_NAME])
        de[_DE_BLOCK0] = (enblk >> 8) & 0xFF
        de[_DE_BLOCK1] = enblk & 0xFF
        if not self.write_dir_entry(de, i):
            return -1
        # Populate fileinfo
        fi = self._fileinfo[handle]
        fi[_FI_DIRINDEX] = i
        fi[_FI_NEXTREC]  = 0
        fi[_FI_FIRSTSEC] = ensec
        fi[_FI_LASTREC]  = 0
        fi[_FI_LASTSEC]  = ensec
        self.dos_status = DS_NO_ERROR
        return i

    def _close_disk_file(self, handle):
        """CloseDiskFile (internal, accepts 0xFF = close all)."""
        if handle >= 0x80:
            self.dos_status = DS_NO_ERROR
            for fi in self._fileinfo:
                fi[_FI_DIRINDEX] = -1
        else:
            self.dos_status = self._check_file_handle(handle)
            if handle < MAX_FILES:
                self._fileinfo[handle][_FI_DIRINDEX] = -1

    def close_disk_file(self, handle):
        """Public CloseDiskFile."""
        self._close_disk_file(handle)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    def _navigate_to_nextrec(self, handle, allocate):
        """Walk FAT chain to fileinfo[handle].nextrec. Returns sector or 0."""
        fi = self._fileinfo[handle]
        nextrec  = fi[_FI_NEXTREC]
        lastrec  = fi[_FI_LASTREC]
        lastsec  = fi[_FI_LASTSEC]
        firstsec = fi[_FI_FIRSTSEC]
        if nextrec >= lastrec:
            fromrec = lastrec
            fromsec = lastsec
        else:
            fromrec = 0
            fromsec = firstsec
        while fromrec < nextrec:
            fromsec = self._fat_next_sector(fromsec, allocate)
            fromrec += 1
            if fromsec == 0:
                self.dos_status = DS_NO_DATA
                return 0, 0, 0
        return fromsec, fromrec, 0

    def write_disk_file(self, handle, data):
        """WriteDiskFile: write SIZE_SECTOR bytes to record nextrec. Returns bytes written."""
        self.dos_status = self._check_file_handle(handle)
        if self.dos_status != DS_NO_ERROR:
            return 0
        fi = self._fileinfo[handle]
        nextrec = fi[_FI_NEXTREC]
        lastrec = fi[_FI_LASTREC]
        lastsec = fi[_FI_LASTSEC]
        firstsec = fi[_FI_FIRSTSEC]
        if nextrec >= lastrec:
            fromrec = lastrec
            fromsec = lastsec
        else:
            fromrec = 0
            fromsec = firstsec
        while fromrec < nextrec:
            fromsec = self._fat_next_sector(fromsec, True)
            fromrec += 1
            if fromsec == 0:
                self.dos_status = DS_NO_DATA
                return 0
        self._secbuf[:SIZE_SECTOR] = bytes(data[:SIZE_SECTOR])
        self.dos_status = DS_IO_ERROR
        if not self._my_sec_write(fromsec):
            return 0
        fi[_FI_LASTREC] = fromrec
        fi[_FI_LASTSEC] = fromsec
        self.dos_status = DS_NO_ERROR
        return SIZE_SECTOR

    def read_disk_file(self, handle, buf):
        """ReadDiskFile: read SIZE_SECTOR bytes from record nextrec into buf. Returns bytes read."""
        self.dos_status = self._check_file_handle(handle)
        if self.dos_status != DS_NO_ERROR:
            return 0
        fi = self._fileinfo[handle]
        nextrec = fi[_FI_NEXTREC]
        lastrec = fi[_FI_LASTREC]
        lastsec = fi[_FI_LASTSEC]
        firstsec = fi[_FI_FIRSTSEC]
        if nextrec >= lastrec:
            fromrec = lastrec
            fromsec = lastsec
        else:
            fromrec = 0
            fromsec = firstsec
        while fromrec < nextrec:
            fromsec = self._fat_next_sector(fromsec, False)
            fromrec += 1
            if fromsec == 0:
                self.dos_status = DS_NO_DATA
                return 0
        self.dos_status = DS_IO_ERROR
        if not self._my_sec_read(fromsec):
            return 0
        buf[:SIZE_SECTOR] = self._secbuf[:SIZE_SECTOR]
        fi[_FI_LASTREC] = fromrec
        fi[_FI_LASTSEC] = fromsec
        self.dos_status = DS_NO_ERROR
        return SIZE_SECTOR

    # ------------------------------------------------------------------
    # Seek / Size / EOF
    # ------------------------------------------------------------------

    def seek_abs_disk_file(self, handle, position):
        """SeekAbsDiskFile: set nextrec to absolute position."""
        if self._check_file_handle(handle) != DS_NO_ERROR:
            return
        self._fileinfo[handle][_FI_NEXTREC] = position

    def seek_rel_disk_file(self, handle, offset):
        """SeekRelDiskFile: advance nextrec by offset."""
        if self._check_file_handle(handle) != DS_NO_ERROR:
            return
        fi = self._fileinfo[handle]
        val = fi[_FI_NEXTREC] + offset
        if val >= 0:
            fi[_FI_NEXTREC] = val

    def size_of_disk_file(self, handle):
        """SizeOfDiskFile: count records by walking FAT chain. Returns 0 on error."""
        if self._check_file_handle(handle) != DS_NO_ERROR:
            return 0
        fi = self._fileinfo[handle]
        fromrec = fi[_FI_LASTREC]
        fromsec = fi[_FI_LASTSEC]
        while True:
            fromsec = self._fat_next_sector(fromsec, False)
            fromrec += 1
            if fromsec == 0:
                break
        return fromrec

    def is_end_of_disk_file(self, handle):
        """IsEndOfDiskFile: True if last accessed record is the file's last record."""
        if self._check_file_handle(handle) != DS_NO_ERROR:
            return False
        sector = self._fileinfo[handle][_FI_LASTSEC]
        entry = self._read_fat_entry(sector)
        if entry > 0xFFFF:
            return False
        if (entry & FB_LAST) == 0:
            return False
        return (sector & (SIZE_BLOCK - 1)) == ((entry & FB_SECTORS) >> 12)

    # ------------------------------------------------------------------
    # Delete / Rename
    # ------------------------------------------------------------------

    def delete_disk_file(self, name11):
        """DeleteDiskFile: find, close handles, free FAT chain, clear dir entry."""
        self.dos_status = DS_FILE_NOT_FOUND
        x = self.find_dir_entry(name11)
        if x < 0:
            return
        # Close any open handles for this file
        for fi in self._fileinfo:
            if fi[_FI_DIRINDEX] == x:
                fi[_FI_DIRINDEX] = -1
        sp, entry = self.read_dir_entry(x)
        if sp == SP_OCCUPIED:
            self.dos_status = DS_NO_ERROR
            block = (self._direntrybuf[_DE_BLOCK0] << 8) | self._direntrybuf[_DE_BLOCK1]
            if not self._fat_free_chain(block * SIZE_BLOCK):
                self.dos_status = DS_IO_ERROR
            blank = bytearray(SIZE_DIR_ENTRY)
            if not self.write_dir_entry(blank, x):
                self.dos_status = DS_IO_ERROR
        elif sp == SP_FREE:
            self.dos_status = DS_NO_ERROR
        else:
            self.dos_status = DS_IO_ERROR

    def rename_disk_file(self, old11, new11):
        """RenameDiskFile: rename file (allowed while open)."""
        y = self.find_dir_entry(new11)
        self.dos_status = DS_FILE_NOT_FOUND
        x = self.find_dir_entry(old11)
        if x < 0:
            return
        self.dos_status = DS_RENAME_FAILED
        if y >= 0:
            return
        self.dos_status = DS_NO_ERROR
        # _direntrybuf now holds old entry (from last find_dir_entry call)
        self._direntrybuf[_DE_NAME:_DE_NAME + SIZE_FILE_NAME] = bytes(new11[:SIZE_FILE_NAME])
        if not self.write_dir_entry(self._direntrybuf, x):
            self.dos_status = DS_IO_ERROR

    # ------------------------------------------------------------------
    # Raw sector access (for fdd.pas ReadSector / WriteSector commands)
    # ------------------------------------------------------------------

    def dos_sec_read(self, x):
        """DosSecRead: read absolute sector x into secbuf. Returns SIZE_SECTOR or 0."""
        if self._my_sec_read(x):
            return SIZE_SECTOR
        return 0

    def dos_sec_write(self, x, data):
        """DosSecWrite: write data to absolute sector x. Returns SIZE_SECTOR or 0."""
        self._secbuf[:SIZE_SECTOR] = bytes(data[:SIZE_SECTOR])
        if self._my_sec_write(x):
            return SIZE_SECTOR
        return 0

    # ------------------------------------------------------------------
    # File tag (used by fdd.pas to store cmdcode per handle)
    # ------------------------------------------------------------------

    def get_disk_file_tag(self, handle):
        """GetDiskFileTag"""
        if handle < MAX_FILES:
            return self._fileinfo[handle][_FI_TAG]
        return 0

    def put_disk_file_tag(self, handle, value):
        """PutDiskFileTag"""
        if handle < MAX_FILES:
            self._fileinfo[handle][_FI_TAG] = value

    # ------------------------------------------------------------------
    # Free space
    # ------------------------------------------------------------------

    def get_free_disk_space(self):
        """GetFreeDiskSpace: count free blocks."""
        sectors = self._backend.sector_count
        maxsector = _my_min(sectors, SIZE_BLOCK * MAX_FAT_ENTRY)
        x = START_DATA
        count = 0
        while x < maxsector:
            if (self._read_fat_entry(x) & FB_IN_USE) == 0:
                count += 1
            x += SIZE_BLOCK
        return count
