SIZE_SECTOR = 256


class StorageBackend:
    @property
    def sector_count(self):
        raise NotImplementedError

    def read_raw(self, sector):
        raise NotImplementedError

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

    def write_raw(self, sector, data):
        if self._readonly:
            return False
        try:
            self._f.seek(sector * SIZE_SECTOR)
            self._f.write(bytes(data[:SIZE_SECTOR]))
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

    def write_raw(self, sector, data):
        base = sector * SIZE_SECTOR
        self._data[base:base + SIZE_SECTOR] = bytes(data[:SIZE_SECTOR])
        return True
