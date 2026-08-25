SIZE_SECTOR = 256


class StorageBackend:
    @property
    def sector_count(self):
        raise NotImplementedError

    def read_raw(self, sector):
        raise NotImplementedError

    def read_into(self, sector, buf):
        """Read SIZE_SECTOR bytes into buf (a pre-allocated bytearray of
        that length) in place. Default implementation falls back to
        read_raw() for backends that don't override this; ImageStorageBackend
        overrides it with a zero-Python-heap-allocation path via
        f.readinto(), since this is called on every sector-cache miss
        (md100_dos.py's _my_sec_read()) -- a real hot path for any file
        read/directory-scan/seek operation, unlike write_raw() which is only
        hit on writes. Returns True on success."""
        raw = self.read_raw(sector)
        if raw is None or len(raw) < SIZE_SECTOR:
            return False
        buf[:] = raw[:SIZE_SECTOR]
        return True

    def write_raw(self, sector, data):
        raise NotImplementedError


class ImageStorageBackend(StorageBackend):
    def __init__(self, path, readonly=False):
        self._path = path
        self._readonly = readonly
        self._f = open(path, "rb" if readonly else "r+b")
        self._f.seek(0, 2)
        self._size = self._f.tell()

    @property
    def sector_count(self):
        return self._size // SIZE_SECTOR

    def read_raw(self, sector):
        self._f.seek(sector * SIZE_SECTOR)
        return self._f.read(SIZE_SECTOR)

    def read_into(self, sector, buf):
        # readinto() fills buf directly from the file with zero Python-heap
        # allocation, unlike read_raw() (which must return a fresh bytes
        # object every call). md100_dos.py's _my_sec_read() -- the sector
        # cache miss path hit by essentially every disk read/dir-scan/seek
        # -- always passes its own persistent secbuf here, so there's no
        # need to allocate an intermediate object at all.
        self._f.seek(sector * SIZE_SECTOR)
        return self._f.readinto(buf) == SIZE_SECTOR

    def write_raw(self, sector, data):
        if self._readonly:
            return False
        try:
            self._f.seek(sector * SIZE_SECTOR)
            # data[:SIZE_SECTOR] already returns a new bytes/bytearray of the
            # right type and size (slicing does) -- wrapping it in bytes()
            # again was a second, unneeded copy on every sector write.
            self._f.write(data[:SIZE_SECTOR])
            self._f.flush()
            return True
        except OSError:
            return False

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass

    @staticmethod
    def create(path, sector_count):
        with open(path, "wb") as f:
            f.write(bytes(sector_count * SIZE_SECTOR))
        return ImageStorageBackend(path)


class MemoryStorageBackend(StorageBackend):
    def __init__(self, sector_count):
        self._data = bytearray(sector_count * SIZE_SECTOR)

    @property
    def sector_count(self):
        return len(self._data) // SIZE_SECTOR

    def read_raw(self, sector):
        base = sector * SIZE_SECTOR
        return bytes(self._data[base:base + SIZE_SECTOR])

    def read_into(self, sector, buf):
        # memoryview() over self._data is zero-copy; only the final
        # buf[:] = ... assignment actually moves bytes, straight from the
        # backing store into the caller's buffer with no intermediate
        # bytes/bytearray object (unlike read_raw() above).
        base = sector * SIZE_SECTOR
        buf[:SIZE_SECTOR] = memoryview(self._data)[base:base + SIZE_SECTOR]
        return True

    def write_raw(self, sector, data):
        base = sector * SIZE_SECTOR
        self._data[base:base + SIZE_SECTOR] = bytes(data[:SIZE_SECTOR])
        return True
