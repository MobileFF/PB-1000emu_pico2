"""MD-100 virtual disk: file-system layer.

Implements the MD-100 disk image file system (allocation table, directory,
record-oriented files).

Runs unchanged on MicroPython and CPython.  Sits on top of a
fdd_storage.StorageBackend (sector_count / read_into / write_raw).

Performance notes: the allocation table (sectors 0-3) is held in RAM
separately from the one-sector data cache, each open handle remembers its
start block and the chain position it last touched, so sequential access
costs about one storage read per record.
"""

SECTOR = 256
SECTORS_PER_BLOCK = 4
MAX_SECTORS = 2048          # 512 allocation entries * 4 sectors
FAT_SECTORS = 4
FIRST_DATA_BLOCK = 4
DIR_FIRST_SECTOR = 4
DIR_ENTRIES = 192
DIR_ENTRY_SIZE = 16
HANDLES = 16

# allocation entry bits
AE_USED = 0x8000
AE_LAST = 0x4000
AE_POS_MASK = 0x3000
AE_POS_SHIFT = 12
AE_BLOCK_MASK = 0x01FF

# status values
ST_OK = 0
ST_TARGET_EXISTS = 1
ST_NOT_FOUND = 2
ST_NOT_OPEN = 3
ST_HANDLE_BUSY = 4
ST_NO_ROOM = 5
ST_BAD_HANDLE = 6
ST_NO_DATA = 7
ST_IO_ERROR = 8

# directory entry states (dir_entry)
DIR_FREE = 0
DIR_USED = 1
DIR_INVALID = 2

_NO_SECTOR = -1
_FAR = 0x7FFFFFFF           # "walk to the end of the chain"
_ZERO_SECTOR = bytes(SECTOR)


class Md100Volume:
    def __init__(self, storage=None):
        self._storage = None
        self._cache = bytearray(SECTOR)     # one-sector data/dir cache
        self._cache_mv = memoryview(self._cache)
        self._cache_sec = _NO_SECTOR
        self._fat = bytearray(FAT_SECTORS * SECTOR)
        self._fat_mv = memoryview(self._fat)
        self._fat_len = 0                   # valid bytes in _fat (0 = not loaded)
        self._open = bytearray(HANDLES)
        self._dirno = [0] * HANDLES
        self._start = [0] * HANDLES         # start block per handle
        self._next = [0] * HANDLES
        self._lastrec = [0] * HANDLES
        self._mode = [0] * HANDLES
        self._pblk = [0] * HANDLES          # cached chain position: block ...
        self._pbase = [-1] * HANDLES        # ... and its first record (-1 = none)
        self.status = ST_OK
        if storage is not None:
            self.attach(storage)

    # ------------------------------------------------------------ setup
    def attach(self, storage):
        self._storage = storage
        self._cache_sec = _NO_SECTOR
        self._fat_len = 0
        self._close_all()
        return storage is not None

    def is_ready(self):
        return self._storage is not None

    def _close_all(self):
        for h in range(HANDLES):
            self._open[h] = 0

    def _drop_positions(self):
        for h in range(HANDLES):
            self._pbase[h] = -1

    def _total_sectors(self):
        st = self._storage
        if st is None:
            return 0
        try:
            return st.sector_count
        except Exception:
            return 0

    def _limit(self):
        n = self._total_sectors()
        return n if n < MAX_SECTORS else MAX_SECTORS

    def _block_limit(self):
        return self._limit() // SECTORS_PER_BLOCK

    # ---------------------------------------------------- sector access
    def _load(self, sec):
        """Bring absolute sector sec into the data cache. Returns bool."""
        if sec == self._cache_sec:
            return True
        if sec < 0 or sec >= self._total_sectors():
            return False
        self._cache_sec = _NO_SECTOR
        try:
            ok = self._storage.read_into(sec, self._cache)
        except Exception:
            ok = False
        if not ok:
            return False
        self._cache_sec = sec
        return True

    def _put(self, sec, data):
        """Plain storage write with range check."""
        if sec < 0 or sec >= self._total_sectors():
            return False
        try:
            return bool(self._storage.write_raw(sec, data))
        except Exception:
            return False

    def _store(self, sec, data):
        """Write data[:256] to sector sec, keeping both caches coherent."""
        ok = self._put(sec, data)
        if sec == self._cache_sec:
            if ok and data is not self._cache:
                self._cache_mv[0:SECTOR] = data[0:SECTOR]
            elif not ok:
                self._cache_sec = _NO_SECTOR
        if 0 <= sec < FAT_SECTORS:
            self._drop_positions()
            if self._fat_len > sec * SECTOR:
                if ok:
                    base = sec * SECTOR
                    self._fat_mv[base:base + SECTOR] = data[0:SECTOR]
                else:
                    self._fat_len = 0
        return ok

    def _flush_cache(self):
        return self._store(self._cache_sec, self._cache)

    # ---------------------------------------------- allocation table
    def _fat_ready(self):
        if self._fat_len:
            return True
        n = self._total_sectors()
        if n > FAT_SECTORS:
            n = FAT_SECTORS
        tmp = self._cache
        self._cache_sec = _NO_SECTOR
        for s in range(n):
            try:
                ok = self._storage.read_into(s, tmp)
            except Exception:
                ok = False
            if not ok:
                return False
            self._fat_mv[s * SECTOR:(s + 1) * SECTOR] = tmp
        self._fat_len = n * SECTOR
        return n > 0

    def _ae_get(self, blk):
        """Allocation entry of block blk, or -1 on error."""
        if blk < 0 or not self._fat_ready():
            return -1
        pos = blk * 2
        if pos + 1 >= self._fat_len:
            return -1
        f = self._fat
        return (f[pos] << 8) | f[pos + 1]

    def _ae_set(self, blk, value):
        if blk < 0 or not self._fat_ready():
            return False
        pos = blk * 2
        if pos + 1 >= self._fat_len:
            return False
        f = self._fat
        f[pos] = (value >> 8) & 0xFF
        f[pos + 1] = value & 0xFF
        sec = pos // SECTOR
        base = sec * SECTOR
        ok = self._put(sec, self._fat_mv[base:base + SECTOR])
        if sec == self._cache_sec:
            self._cache_sec = _NO_SECTOR
        if not ok:
            self._fat_len = 0       # reload the on-disk truth next time
        return ok

    def _alloc_block(self):
        """First free block from block 4 upward, or -1."""
        for b in range(FIRST_DATA_BLOCK, self._block_limit()):
            e = self._ae_get(b)
            if e >= 0 and not (e & AE_USED):
                return b
        return -1

    def free_blocks(self):
        n = 0
        for b in range(FIRST_DATA_BLOCK, self._block_limit()):
            e = self._ae_get(b)
            if e >= 0 and not (e & AE_USED):
                n += 1
        return n

    # --------------------------------------------------- chain walking
    def _walk(self, h, rec, remember=True):
        """Walk handle h's chain to the block holding record rec, or to the
        last block if rec lies beyond it.  Resumes from the handle's cached
        position when possible.  Returns (block, first_record, entry) or
        None on a broken chain / read error."""
        base = self._pbase[h]
        if base >= 0 and rec >= base:
            b = self._pblk[h]
        else:
            b = self._start[h]
            base = 0
        for _ in range(512):
            e = self._ae_get(b)
            if e < 0 or not (e & AE_USED):
                return None
            if (e & AE_LAST) or rec < base + SECTORS_PER_BLOCK:
                if remember:
                    self._pblk[h] = b
                    self._pbase[h] = base
                return (b, base, e)
            b = e & AE_BLOCK_MASK
            base += SECTORS_PER_BLOCK
        return None

    def _record_sector(self, h, rec):
        """Sector of record rec of handle h: -1 missing, -2 error."""
        if rec < 0:
            return -1
        w = self._walk(h, rec)
        if w is None:
            return -2
        b, base, e = w
        off = rec - base
        if off >= SECTORS_PER_BLOCK:
            return -1
        if (e & AE_LAST) and off > (e & AE_POS_MASK) >> AE_POS_SHIFT:
            return -1
        return b * SECTORS_PER_BLOCK + off

    def _extend(self, h):
        """Add one record to the end of handle h's file. Returns a status."""
        w = self._walk(h, _FAR)
        if w is None:
            return ST_IO_ERROR
        last, _, e = w
        pos = (e & AE_POS_MASK) >> AE_POS_SHIFT
        if pos < SECTORS_PER_BLOCK - 1:
            v = AE_USED | AE_LAST | ((pos + 1) << AE_POS_SHIFT) | last
            return ST_OK if self._ae_set(last, v) else ST_IO_ERROR
        nb = self._alloc_block()
        if nb < 0:
            return ST_NO_DATA
        if not self._ae_set(nb, AE_USED | AE_LAST | nb):
            return ST_IO_ERROR
        if not self._ae_set(last, AE_USED | nb):
            return ST_IO_ERROR
        return ST_OK

    # ------------------------------------------------------- directory
    def _dir_loc(self, i):
        return (DIR_FIRST_SECTOR + i // 16, (i % 16) * DIR_ENTRY_SIZE)

    def _dir_state(self, i):
        """DIR_FREE/USED/INVALID; on success the entry is in the cache at
        offset (i % 16) * 16."""
        if i < 0 or i >= DIR_ENTRIES:
            return DIR_INVALID
        sec, off = self._dir_loc(i)
        if not self._load(sec):
            return DIR_INVALID
        c = self._cache
        if c[off + 1] == 0 and c[off + 13] == 0 and c[off + 14] == 0:
            return DIR_FREE
        return DIR_USED

    def dir_entry(self, i):
        s = self._dir_state(i)
        if s == DIR_INVALID:
            return (DIR_INVALID, None)
        off = (i % 16) * DIR_ENTRY_SIZE
        return (s, bytes(self._cache[off:off + DIR_ENTRY_SIZE]))

    def _dir_start(self, i):
        """Start block of directory entry i, or -1."""
        if self._dir_state(i) == DIR_INVALID:
            return -1
        off = (i % 16) * DIR_ENTRY_SIZE
        return (self._cache[off + 13] << 8) | self._cache[off + 14]

    def _find(self, name):
        for i in range(DIR_ENTRIES):
            s = self._dir_state(i)
            if s == DIR_INVALID:
                return -1
            if s == DIR_FREE:
                continue
            c = self._cache
            base = (i % 16) * DIR_ENTRY_SIZE + 1
            match = True
            for k in range(11):
                if c[base + k] != name[k]:
                    match = False
                    break
            if match:
                return i
        return -1

    # -------------------------------------------------------- handles
    def _check(self, handle):
        if handle < 0 or handle >= HANDLES:
            self.status = ST_BAD_HANDLE
            return False
        if not self._open[handle]:
            self.status = ST_NOT_OPEN
            return False
        return True

    def _valid_open(self, handle):
        return 0 <= handle < HANDLES and self._open[handle]

    def _set_open(self, handle, d, start):
        self._open[handle] = 1
        self._dirno[handle] = d
        self._start[handle] = start
        self._next[handle] = 0
        self._lastrec[handle] = 0
        self._pbase[handle] = -1

    def find_free_handle(self):
        for h in range(HANDLES - 1, -1, -1):
            if not self._open[h]:
                return h
        return -1

    def get_mode(self, handle):
        if 0 <= handle < HANDLES:
            return self._mode[handle]
        return 0

    def set_mode(self, handle, value):
        if 0 <= handle < HANDLES:
            self._mode[handle] = value

    # ----------------------------------------------------- operations
    def format(self):
        self._close_all()
        self._drop_positions()
        self._cache_sec = _NO_SECTOR
        self._fat_len = 0
        ok = True
        for sec in range(1, self._limit()):
            if not self._put(sec, _ZERO_SECTOR):
                ok = False
                break
        if ok:
            w = bytearray(SECTOR)
            w[0:8] = b"\xff\xff\xff\xff\xff\xff\xff\xff"
            ok = self._put(0, w)
        self._cache_sec = _NO_SECTOR
        self._fat_len = 0
        return ok

    def open(self, handle, name11):
        if handle < 0 or handle >= HANDLES:
            self.status = ST_BAD_HANDLE
            return -1
        if self._open[handle]:
            self.status = ST_HANDLE_BUSY
            return -1
        d = self._find(name11)
        if d < 0:
            self.status = ST_NOT_FOUND
            return -1
        off = (d % 16) * DIR_ENTRY_SIZE     # entry is still in the cache
        start = (self._cache[off + 13] << 8) | self._cache[off + 14]
        self._set_open(handle, d, start)
        self.status = ST_OK
        return d

    def create(self, handle, name11, kind):
        if handle < 0 or handle >= HANDLES:
            self.status = ST_BAD_HANDLE
            return -1
        if self._find(name11) >= 0:
            self.delete(name11)
            if self.status != ST_OK:
                return -1
        d = -1
        for i in range(DIR_ENTRIES):
            s = self._dir_state(i)
            if s == DIR_USED:
                continue
            if s == DIR_FREE:
                d = i
            break
        if d < 0:
            self.status = ST_NO_ROOM
            return -1
        blk = self._alloc_block()
        if blk < 0:
            self.status = ST_NO_ROOM
            return -1
        if not self._ae_set(blk, AE_USED | AE_LAST | blk):
            self.status = ST_IO_ERROR
            return -1
        sec, off = self._dir_loc(d)
        if not self._load(sec):
            self.status = ST_IO_ERROR
            return -1
        c = self._cache
        c[off] = kind & 0xFF
        for k in range(11):
            c[off + 1 + k] = name11[k]
        c[off + 13] = (blk >> 8) & 0xFF
        c[off + 14] = blk & 0xFF
        if not self._flush_cache():
            self.status = ST_IO_ERROR
            return -1
        self._set_open(handle, d, blk)
        self.status = ST_OK
        return d

    def close(self, handle):
        if handle >= 0x80:
            self._close_all()
            self.status = ST_OK
            return
        if handle < 0 or handle >= HANDLES:
            self.status = ST_BAD_HANDLE
            return
        self.status = ST_OK if self._open[handle] else ST_NOT_OPEN
        self._open[handle] = 0

    def read_record(self, handle, buf):
        if not self._check(handle):
            return 0
        rec = self._next[handle]
        sec = self._record_sector(handle, rec)
        if sec == -1:
            self.status = ST_NO_DATA
            return 0
        if sec < 0 or not self._load(sec):
            self.status = ST_IO_ERROR
            return 0
        buf[0:SECTOR] = self._cache
        self._lastrec[handle] = rec
        self.status = ST_OK
        return SECTOR

    def write_record(self, handle, data):
        if not self._check(handle):
            return 0
        rec = self._next[handle]
        sec = self._record_sector(handle, rec)
        while sec == -1:
            st = self._extend(handle)
            if st != ST_OK:
                self.status = st
                return 0
            sec = self._record_sector(handle, rec)
        if sec < 0 or not self._store(sec, data):
            self.status = ST_IO_ERROR
            return 0
        self._lastrec[handle] = rec
        self.status = ST_OK
        return SECTOR

    def delete(self, name11):
        d = self._find(name11)
        if d < 0:
            self.status = ST_NOT_FOUND
            return
        for h in range(HANDLES):
            if self._open[h] and self._dirno[h] == d:
                self._open[h] = 0
        b = self._dir_start(d)
        lim = self._block_limit()
        for _ in range(512):
            if b < 0 or b >= lim:
                break
            e = self._ae_get(b)
            if e < 0:
                self.status = ST_IO_ERROR
                return
            if not self._ae_set(b, e & 0x00FF):
                self.status = ST_IO_ERROR
                return
            if (e & AE_LAST) or not (e & AE_USED):
                break
            b = e & AE_BLOCK_MASK
        sec, off = self._dir_loc(d)
        if not self._load(sec):
            self.status = ST_IO_ERROR
            return
        self._cache_mv[off:off + DIR_ENTRY_SIZE] = _ZERO_SECTOR[0:DIR_ENTRY_SIZE]
        self.status = ST_OK if self._flush_cache() else ST_IO_ERROR

    def rename(self, old11, new11):
        d = self._find(old11)
        if d < 0:
            self.status = ST_NOT_FOUND
            return
        if self._find(new11) >= 0:
            self.status = ST_TARGET_EXISTS
            return
        sec, off = self._dir_loc(d)
        if not self._load(sec):
            self.status = ST_IO_ERROR
            return
        c = self._cache
        for k in range(11):
            c[off + 1 + k] = new11[k]
        self.status = ST_OK if self._flush_cache() else ST_IO_ERROR

    # ------------------------------------------------- raw sectors
    def read_sector(self, n, buf):
        if not self._load(n):
            return False
        buf[0:SECTOR] = self._cache
        return True

    def write_sector(self, n, data):
        return self._store(n, data)

    # ------------------------------------------------- seek / size
    def seek(self, handle, rec):
        if self._valid_open(handle):
            self._next[handle] = rec

    def skip(self, handle, delta):
        if self._valid_open(handle):
            v = self._next[handle] + delta
            if v >= 0:
                self._next[handle] = v

    def record_count(self, handle):
        if not self._valid_open(handle):
            return 0
        # do not move the cached position: it serves sequential access
        w = self._walk(handle, _FAR, False)
        if w is None:
            return 0
        return w[1] + ((w[2] & AE_POS_MASK) >> AE_POS_SHIFT) + 1

    def at_last_record(self, handle):
        if not self._valid_open(handle):
            return False
        rec = self._lastrec[handle]
        w = self._walk(handle, rec)
        if w is None:
            return False
        b, base, e = w
        if not (e & AE_LAST):
            return False
        return rec - base == (e & AE_POS_MASK) >> AE_POS_SHIFT
