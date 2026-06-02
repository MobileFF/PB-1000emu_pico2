#!/usr/bin/env python3
"""
md100.py - Casio MD-100 Floppy Disk Image Utility

Python port of the md100 C utility by Marcus von Cube.
Handles floppy disk images for Casio PB-1000 / PB-2000C MD-100 drives.

Usage:
  md100.py [-cSIZE] [-n] <image> <cmd> [options] [parameters]

Commands:
  dir   [options] ["<pattern>"]
  type  [options] <file>
  get   [options] <md100-file> [<pc-file>]
  mget  [options] ["<pattern>"]
  put   [options] <pc-file> [<md100-file>]
  mput  [options] <pc-files...>
  del   [options] "<pattern>"
  ren   [options] <source> <newname>
  set   [options] "<pattern>" -t<type> [-p<protect>]

Options:
  -i       case-insensitive matching
  -l       lowercase filenames on PC
  -u       uppercase filenames on PC
  -tX      type filter/set (B, C, M, R, S or hex)
  -pN      protection filter/set (0 or 1)
  -b       force binary transfer
  -a       force ASCII transfer
  -eX      escape syntax: N(one), H(ex), S(ymbols)
  -n       no updates to image
  -cSIZE   create new image (default 320 blocks, max 512)
  -d DIR   destination directory for mget
"""

import sys, os, struct, re, argparse
from typing import Optional, List, Tuple

# ─── Constants ────────────────────────────────────────────────────────────────

SECTOR_SIZE  = 256
BLOCK_SIZE   = SECTOR_SIZE * 4      # 1024 bytes per block (4 sectors)
DEFAULT_SIZE = 320                   # 80 tracks * 4 blocks/track
MAX_SIZE     = 512                   # 128 tracks * 4 blocks/track
MIN_SIZE     = 5

FAT_BLK  = 0;  FAT_CNT  = 1   # block 0: FAT
DIR_BLK  = 1;  DIR_CNT  = 3   # blocks 1-3: directory
DATA_BLK = 4                   # blocks 4+: data

MAX_DIR_ENTRIES = DIR_CNT * BLOCK_SIZE // 16   # 192
DIR_ENTRY_SIZE  = 16
EOF_CHAR        = 0x1A
DEF_UNUSED      = 0x1F

# FAT entry bit masks
FB_IN_USE = 0x8000   # entry is in use
FB_LAST   = 0x4000   # end of chain
FB_SECT   = 0x3000   # (sectors-1) in last block, bits 13-12
FB_BLOCK  = 0x01FF   # block number, bits 8-0

# File type bytes
TYPE_M = 0x0D   # machine code
TYPE_B = 0x10   # tokenized BASIC
TYPE_S = 0x24   # sequential ASCII
TYPE_R = 0xA4   # relative / random access
TYPE_C = 0xD4   # C source / BAT

TYPE_BY_LETTER = {'B': TYPE_B, 'C': TYPE_C, 'M': TYPE_M, 'R': TYPE_R, 'S': TYPE_S}
LETTER_BY_TYPE = {v: k for k, v in TYPE_BY_LETTER.items()}

# Extension → default type for put command
EXT_TYPE = {
    'c': TYPE_C, 'h': TYPE_C, 'bat': TYPE_C,
    'exe': TYPE_M, 'bas': TYPE_B, 'rel': TYPE_R,
}

# Escape sequences for BASIC text output
ESCAPES: List[Tuple[str, int]] = [
    ("YN", 0x5C), ("_1", 0x80), ("_2", 0x81), ("_3", 0x82), ("_4", 0x83),
    ("_5", 0x84), ("_6", 0x85), ("_7", 0x86), ("_8", 0x87), ("#",  0x87),
    ("|1", 0x88), ("|2", 0x89), ("|3", 0x8A), ("|4", 0x8B), ("|5", 0x8C),
    ("|6", 0x8D), ("|7", 0x8E), ("^",  0x90), ("V",  0x91), ("<-", 0x92),
    ("->", 0x93), (".",  0xA5), ("DG", 0xDF), ("TR", 0xE4), ("SP", 0xE8),
    ("HT", 0xE9), ("DI", 0xEA), ("CL", 0xEB), ("LD", 0xEC), ("@",  0xED),
    ("/",  0x83), ("\\", 0xEF), ("*",  0xF0), ("]",  0xFE),
]
ESC_BY_TEXT: dict = {t: v for t, v in ESCAPES}
ESC_BY_TOKEN: dict = {}
for _t, _v in ESCAPES:
    if _v not in ESC_BY_TOKEN:
        ESC_BY_TOKEN[_v] = _t

# PB-1000 BASIC token table  prefix 4..7, byte range 0x40..0xCF
# Each sub-list has 0x90 = 144 entries; index = token_byte - 0x40
_T4 = [
    None,None,None,None,None,None,None,None,None,"GOTO ","GOSUB ","RETURN",    # 40
    "RESUME ","RESTORE ","WRITE#",None,None,None,"SYSTEM","PASS ",             # 4C
    None,"DELETE ","BSAVE ","LIST ","LLIST ","LOAD ","MERGE ",None,None,        # 54
    "TRON",None,"TROFF","VERIFY ","MON","CALL ","POKE ",None,None,None,None,   # 5E
    None,"CHAIN ","CLEAR ","NEW ","SAVE ","RUN ","ANGLE ","EDIT ",             # 68
    "BEEP ","CLS","CLOSE ",None,None,None,"DEF ","DEFM ","DEFSEG ",            # 70
    None,"VAC",None,"DIM ","DRAW ",None,None,"DATA ","FOR ","NEXT ",           # 78
    None,None,"ERASE ","ERROR ","END",None,None,"FIELD ","FORMAT",             # 82
    "GET ","IF ",None,"LET ","LINE ","LOCATE ",None,"LSET ",None,None,None,    # 88
    "OPEN ",None,"OUT ","ON ",None,None,None,None,"CALCJMP ","BLOAD ",None,    # 94
    "DRAWC ","PRINT ","LPRINT ","PUT ",None,None,"READ ","REM ","RSET ",None,  # A0
    "SET ","STAT","STOP",None,"MODE ",None,"VAR ","PBLOAD ","PBGET ",          # A8
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,# B1
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # C0
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # CF (pad)
]
_T5 = [
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 40
    "ERL","ERR","CNT","SUMX","SUMY","SUMX2","SUMY2","SUMXY",                    # 4F
    "MEANX","MEANY","SDX","SDY","SDXN","SDYN","LRA","LRB","COR","PI",          # 57
    None,None,"CUR ",None,None,None,"FACT ",None,"EOX ","EOY ",                 # 60
    "SIN ","COS ","TAN ","ASN ","ACS ","ATN ",                                  # 6A
    "HYPSIN ","HYPCOS ","HYPTAN ","HYPASN ","HYPACS ","HYPATN ",                # 70
    "LOG ","LGT ","EXP ","SQR ","ABS ","SGN ","INT ","FIX ","FRAC ","RND ",    # 76
    None,None,None,None,None,"PEEK ",None,None,"LOF ","EOF ",None,None,"FRE ", # 80
    None,"POINT ","ROUND","RND","VALF","RAN#","ASC","LEN","VAL",                # 8D
    None,None,None,None,None,"DEG",None,None,None,                              # 96
    None,None,None,None,None,None,None,None,"REC","POL",None,"NPR","NCR","HYP",# 9F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # AD
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # BC
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # CB (pad)
]
_T6 = [
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 40
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 4F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 5E
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 6D
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 7C
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # 8B
    None,None,None,None,None,None,None,"DMS$",None,None,"MID","INPUT ",        # 94
    "MID$","RIGHT$","LEFT$",None,"CHR$","STR$",None,"HEX$",None,None,None,None, # A0
    "INKEY$","KEY",None,"DATE$","TIME$","CALC$",None,None,None,None,None,None,  # A8
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # B4
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None, # C3 (pad)
]
_T7 = [
    None,None,None,None,None,None,None," THEN ","ELSE ",None,None,None,None,None,None, # 40
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 4F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 5F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 6F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 7F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 8F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # 9F
    None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,    # AF
    None,None,"TAB ",None,"CSR ","REV ","NORM ","ALL ",                                  # B2
    " AS ","APPEND ",None,"OFF"," STEP "," TO ","USING ","NOT ",                        # BA
    " AND "," OR "," XOR "," MOD ",None,None,None,None,None,None,None,None,None,None,   # C2 (pad)
]
# Pad each table to exactly 144 entries
for _tab in (_T4, _T5, _T6, _T7):
    while len(_tab) < 0x90:
        _tab.append(None)
TOKENS = [_T4, _T5, _T6, _T7]


# ─── Directory Entry ──────────────────────────────────────────────────────────

class DirEntry:
    __slots__ = ('type_byte', 'name', 'ext', 'unused', 'start_block', 'protect')

    def __init__(self, type_byte: int, name: bytes, ext: bytes,
                 unused: int, start_block: int, protect: int):
        self.type_byte   = type_byte
        self.name        = bytes(name[:8]).ljust(8, b' ')[:8]
        self.ext         = bytes(ext[:3]).ljust(3, b' ')[:3]
        self.unused      = unused
        self.start_block = start_block
        self.protect     = protect

    @classmethod
    def empty(cls) -> 'DirEntry':
        return cls(0, b'        ', b'   ', 0, 0, 0)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'DirEntry':
        assert len(data) >= DIR_ENTRY_SIZE
        t    = data[0]
        name = data[1:9]
        ext  = data[9:12]
        unu  = data[12]
        blk  = (data[13] << 8) | data[14]
        prot = data[15]
        return cls(t, name, ext, unu, blk, prot)

    def to_bytes(self) -> bytes:
        out = bytearray(DIR_ENTRY_SIZE)
        out[0]     = self.type_byte
        out[1:9]   = self.name
        out[9:12]  = self.ext
        out[12]    = self.unused
        out[13]    = (self.start_block >> 8) & 0xFF
        out[14]    = self.start_block & 0xFF
        out[15]    = self.protect & 0xFF
        return bytes(out)

    @property
    def is_free(self) -> bool:
        return self.type_byte == 0

    @property
    def name_ext(self) -> bytes:
        """11 bytes: name[8] + ext[3], used for pattern matching."""
        return self.name + self.ext

    def display_name(self, case: str = 'AS_IS') -> str:
        n = self.name.rstrip(b' ').decode('latin-1')
        e = self.ext.rstrip(b' ').decode('latin-1')
        result = f"{n}.{e}" if e else n
        if case == 'LOWER': return result.lower()
        if case == 'UPPER': return result.upper()
        return result

    def type_str(self) -> str:
        return LETTER_BY_TYPE.get(self.type_byte, f"{self.type_byte:02X}")

    def __repr__(self):
        return (f"DirEntry({self.display_name()!r}, type={self.type_str()!r}, "
                f"block={self.start_block}, protect={self.protect})")


# ─── Disk Image ───────────────────────────────────────────────────────────────

class MD100Disk:
    """Represents an MD-100 floppy disk image file."""

    def __init__(self, path: str, create: bool = False,
                 size: int = DEFAULT_SIZE, no_update: bool = False):
        self.path      = path
        self.no_update = no_update
        self.dirty     = False

        if create and not os.path.exists(path):
            if not (MIN_SIZE <= size <= MAX_SIZE):
                raise ValueError(f"Disk size {size} out of range [{MIN_SIZE},{MAX_SIZE}]")
            self.num_blocks = size
            # FAT block: reserve blocks 0-3 (0xFFFF each)
            self.fat = bytearray(BLOCK_SIZE * FAT_CNT)
            for i in range(4):
                self.fat[2*i]   = 0xFF
                self.fat[2*i+1] = 0xFF
            self.dir = bytearray(BLOCK_SIZE * DIR_CNT)
            self._write_fat_dir()
            # Pad image to full size with a dummy last block
            with open(path, 'r+b') as f:
                f.seek(BLOCK_SIZE * (size - 1))
                f.write(b'\x00' * BLOCK_SIZE)
        else:
            with open(path, 'rb') as f:
                data = f.read()
            file_blocks = len(data) // BLOCK_SIZE
            if not (MIN_SIZE - 1 <= file_blocks <= MAX_SIZE):
                raise ValueError(f"Image size {len(data)} bytes is out of range")
            self.num_blocks = max(size, file_blocks)
            fat_end = BLOCK_SIZE * FAT_CNT
            dir_end = BLOCK_SIZE * (DIR_BLK + DIR_CNT)
            self.fat = bytearray(data[:fat_end])
            self.dir = bytearray(data[BLOCK_SIZE * DIR_BLK : dir_end])
            if len(self.fat) < BLOCK_SIZE * FAT_CNT:
                self.fat += b'\x00' * (BLOCK_SIZE * FAT_CNT - len(self.fat))
            if len(self.dir) < BLOCK_SIZE * DIR_CNT:
                self.dir += b'\x00' * (BLOCK_SIZE * DIR_CNT - len(self.dir))

    # ── FAT ──────────────────────────────────────────────────────────────────

    def fat_get(self, block: int) -> int:
        j = 2 * (block & FB_BLOCK)
        return (self.fat[j] << 8) | self.fat[j+1]

    def fat_set(self, block: int, value: int) -> None:
        j = 2 * (block & FB_BLOCK)
        self.fat[j]   = (value >> 8) & 0xFF
        self.fat[j+1] = value & 0xFF
        self.dirty = True

    def find_free_block(self, start: int = 0) -> int:
        """Return index of first free block >= start, or 0 if disk is full."""
        for i in range(start, self.num_blocks):
            if not (self.fat_get(i) & FB_IN_USE):
                return i
        return 0

    def disk_free(self) -> int:
        free = self.num_blocks - DATA_BLK
        for i in range(DATA_BLK, self.num_blocks):
            if self.fat[2*i] != 0:
                free -= 1
        return free

    # ── Directory ─────────────────────────────────────────────────────────────

    def _dir_offset(self, idx: int) -> int:
        return idx * DIR_ENTRY_SIZE

    def get_entry(self, idx: int) -> DirEntry:
        off = self._dir_offset(idx)
        return DirEntry.from_bytes(bytes(self.dir[off:off+DIR_ENTRY_SIZE]))

    def set_entry(self, idx: int, entry: DirEntry) -> None:
        off = self._dir_offset(idx)
        self.dir[off:off+DIR_ENTRY_SIZE] = entry.to_bytes()
        self.dirty = True

    def clear_entry(self, idx: int) -> None:
        off = self._dir_offset(idx)
        self.dir[off:off+DIR_ENTRY_SIZE] = b'\x00' * DIR_ENTRY_SIZE
        self.dirty = True

    def dir_free(self) -> int:
        return sum(1 for i in range(MAX_DIR_ENTRIES) if self.get_entry(i).is_free)

    def all_entries(self) -> List[Tuple[int, DirEntry]]:
        return [(i, self.get_entry(i)) for i in range(MAX_DIR_ENTRIES)]

    # ── Block I/O ─────────────────────────────────────────────────────────────

    def read_block(self, block_num: int) -> bytes:
        block_num &= FB_BLOCK
        with open(self.path, 'rb') as f:
            f.seek(BLOCK_SIZE * block_num)
            data = f.read(BLOCK_SIZE)
        return data.ljust(BLOCK_SIZE, b'\x00')

    def write_block(self, block_num: int, data: bytes) -> None:
        if self.no_update:
            return
        block_num &= FB_BLOCK
        padded = (data + b'\x00' * BLOCK_SIZE)[:BLOCK_SIZE]
        with open(self.path, 'r+b') as f:
            f.seek(BLOCK_SIZE * block_num)
            f.write(padded)

    # ── File chain ────────────────────────────────────────────────────────────

    def file_blocks_and_size(self, first_block: int) -> Tuple[int, int]:
        """Return (size_bytes, block_count) for file starting at first_block."""
        if first_block == 0:
            return 0, 0
        block_count   = 1
        sector_count  = 0
        last_fat      = 0
        last_blk_num  = first_block
        i = first_block
        while True:
            fat = self.fat_get(i)
            if fat & FB_LAST:
                last_fat     = fat
                last_blk_num = i & FB_BLOCK
                sectors_last = ((fat & FB_SECT) >> 12) + 1
                sector_count += sectors_last
                break
            block_count  += 1
            sector_count += BLOCK_SIZE // SECTOR_SIZE   # 4
            i = fat & FB_BLOCK
        size = sector_count * SECTOR_SIZE
        # Trim at EOF_CHAR in the last sector of the last block
        block_data   = self.read_block(last_blk_num)
        scan_end     = sector_count % (BLOCK_SIZE // SECTOR_SIZE)
        # scan_end in sectors within last block = sectors_last
        sectors_last = ((last_fat & FB_SECT) >> 12) + 1
        j            = sectors_last * SECTOR_SIZE
        for idx in range(j - 1, j - SECTOR_SIZE - 1, -1):
            if 0 <= idx < len(block_data) and block_data[idx] == EOF_CHAR:
                size -= j - idx
                break
        return size, block_count

    def iter_file_data(self, first_block: int, size: int):
        """Yield raw bytes chunks for the file (total exactly `size` bytes)."""
        remaining = size
        i = first_block
        while remaining > 0 and i != 0:
            fat  = self.fat_get(i)
            blk  = i & FB_BLOCK
            data = self.read_block(blk)
            chunk = min(remaining, BLOCK_SIZE)
            yield data[:chunk]
            remaining -= chunk
            if (fat & FB_LAST) or remaining == 0:
                break
            i = fat & FB_BLOCK

    # ── Persistence ───────────────────────────────────────────────────────────

    def _write_fat_dir(self) -> None:
        with open(self.path, 'ab') as f:
            pass  # ensure file exists
        with open(self.path, 'r+b') as f:
            f.seek(BLOCK_SIZE * FAT_BLK)
            f.write(bytes(self.fat))
            f.seek(BLOCK_SIZE * DIR_BLK)
            f.write(bytes(self.dir))
        self.dirty = False

    def flush(self) -> None:
        if self.dirty and not self.no_update:
            self._write_fat_dir()

    def __enter__(self): return self
    def __exit__(self, *_): self.flush()


# ─── Pattern matching ─────────────────────────────────────────────────────────

def expand_pattern(pattern: Optional[str], case: str = 'AS_IS') -> bytes:
    """
    Convert a DOS-style pattern string to an 11-byte match template.
    Direct port of the C expandPattern() function.
    '?' matches any character; ' ' = end-of-field (no character).
    Returns bytes of exactly 11.
    """
    if pattern is None or pattern == '*':
        return b'???????????' # 11 wildcards

    result = bytearray(b'           ')  # 11 spaces (8 name + 3 ext)
    i = 0
    p = 0
    while p < len(pattern) and i < 11:
        c = pattern[p]
        if c == '*':
            if i < 8:
                for k in range(i, 8): result[k] = ord('?')
                i = 8
            else:
                for k in range(i, 11): result[k] = ord('?')
                i = 11
        elif c == '.':
            i = 8 if i < 8 else 11
        else:
            ch = c.upper() if case == 'UPPER' else c.lower() if case == 'LOWER' else c
            result[i] = ord(ch)
            i += 1

        # After filling the name part (i just reached 8), skip forward in the
        # pattern to the next '.', mirroring the C inner-skip loop:
        #   while (pattern[1] != '\0' && *pattern != '.') ++pattern;
        if i == 8:
            while p + 1 < len(pattern) and pattern[p] != '.':
                p += 1

        p += 1

    return bytes(result)


def match_entry(entry: DirEntry, pattern: bytes, ignore_case: bool = False,
                type_filter: int = 0, protect_filter: int = -1) -> bool:
    """Check if a directory entry matches the given pattern and filters."""
    if entry.is_free:
        return False
    if type_filter != 0 and entry.type_byte != type_filter:
        return False
    if protect_filter >= 0 and entry.protect != protect_filter:
        return False
    ne = entry.name_ext
    for i in range(11):
        if pattern[i] == ord('?'):
            continue
        c = ne[i]
        pc = pattern[i]
        if ignore_case:
            if chr(c).upper() != chr(pc).upper():
                return False
        else:
            if c != pc:
                return False
    return True


def find_files(disk: MD100Disk, pattern: Optional[str],
               ignore_case: bool = False, type_filter: int = 0,
               protect_filter: int = -1, case: str = 'AS_IS'
               ) -> List[Tuple[int, DirEntry]]:
    """Return list of (index, entry) matching the pattern."""
    pat = expand_pattern(pattern, 'UPPER' if ignore_case else case)
    return [
        (i, e) for i, e in disk.all_entries()
        if match_entry(e, pat, ignore_case, type_filter, protect_filter)
    ]


def name_to_11bytes(name: str, case: str = 'AS_IS',
                    src_entry: Optional[DirEntry] = None) -> Tuple[bytes, bytes]:
    """
    Convert a filename string (up to 8.3) to (name_8, ext_3) bytes.
    If the pattern contains '?' wildcards, fill them from src_entry.
    """
    # Expand pattern first
    pat = expand_pattern(name, case)  # 11 bytes
    if src_entry is not None:
        # Replace wildcards with chars from source entry
        ne = src_entry.name_ext
        pat_list = bytearray(pat)
        for i in range(11):
            if pat_list[i] == ord('?'):
                ch = ne[i]
                pat_list[i] = ord(chr(ch).upper()) if case == 'UPPER' else \
                               ord(chr(ch).lower()) if case == 'LOWER' else ch
        pat = bytes(pat_list)
    # Replace remaining wildcards with spaces
    pat_list = bytearray(pat)
    for i in range(11):
        if pat_list[i] == ord('?'):
            pat_list[i] = ord(' ')
    return bytes(pat_list[:8]), bytes(pat_list[8:11])


def pc_name_to_md100(src_path: str, dest_pattern: Optional[str],
                     case: str = 'AS_IS') -> Tuple[bytes, bytes]:
    """
    Derive the MD-100 filename (name_8, ext_3) from a PC path and optional
    destination pattern.  dest_pattern may contain '?' wildcards.
    """
    basename = os.path.basename(src_path)
    # Strip leading stdin prefix used for piping
    if basename.lower().startswith('stdin'):
        basename = basename[5:].lstrip('.')

    # Parse basename into name+ext
    if '.' in basename:
        n, e = basename.rsplit('.', 1)
    else:
        n, e = basename, ''

    # Expand the destination pattern (or '*.*' if none)
    pat = expand_pattern(dest_pattern or '*.*', case)  # 11 bytes
    pat_list = bytearray(pat)

    # Fill wildcards from the source filename
    def apply_case(ch: str) -> int:
        if case == 'UPPER': return ord(ch.upper())
        if case == 'LOWER': return ord(ch.lower())
        return ord(ch)

    # Name part (indices 0-7)
    src_name = n[:8].ljust(8)
    for i in range(8):
        if pat_list[i] == ord('?') and i < len(src_name):
            pat_list[i] = apply_case(src_name[i])

    # Extension (indices 8-10)
    src_ext = e[:3].ljust(3)
    for i in range(3):
        if pat_list[8+i] == ord('?') and i < len(src_ext):
            pat_list[8+i] = apply_case(src_ext[i])

    # Replace leftover wildcards with spaces
    for i in range(11):
        if pat_list[i] == ord('?'):
            pat_list[i] = ord(' ')

    return bytes(pat_list[:8]), bytes(pat_list[8:11])


# ─── File data I/O ────────────────────────────────────────────────────────────

def read_file_data(disk: MD100Disk, entry: DirEntry) -> bytes:
    """Read the complete raw data of a file."""
    size, _ = disk.file_blocks_and_size(entry.start_block)
    chunks   = list(disk.iter_file_data(entry.start_block, size))
    return b''.join(chunks)


def _write_data_blocks(disk: MD100Disk, data: bytes) -> int:
    """
    Write data bytes to the disk, chaining FAT entries.
    Returns the first block number.
    Mirrors the C writeFile() loop: always writes at least one block (for EOF).
    """
    first_blk = 0
    prev_blk  = 0
    offset    = 0
    total     = len(data)

    while offset <= total:
        chunk_end = min(offset + BLOCK_SIZE, total)
        chunk     = data[offset:chunk_end]
        # A chunk is the last block iff it is smaller than a full block.
        # Full blocks (1024 B) always need an additional EOF-only block after them.
        is_last = len(chunk) < BLOCK_SIZE

        blk = disk.find_free_block(DATA_BLK)
        if blk == 0:
            raise RuntimeError("Disk full")

        buf = bytearray(BLOCK_SIZE)
        buf[:len(chunk)] = chunk

        if is_last:
            s = len(chunk) // SECTOR_SIZE   # sectors used (0-3); C uses count//256
            fat_val = FB_IN_USE | FB_LAST | (s << 12) | blk
            buf[len(chunk)] = EOF_CHAR
        else:
            # Mark as in-use with a temporary self-reference; patched when the
            # next block is allocated.
            fat_val = FB_IN_USE | blk

        disk.fat_set(blk, fat_val)
        disk.write_block(blk, bytes(buf))

        if prev_blk != 0:
            # Chain previous block → current block
            disk.fat_set(prev_blk, FB_IN_USE | blk)

        if first_blk == 0:
            first_blk = blk
        prev_blk = blk
        offset   = chunk_end
        if is_last:
            break

    return first_blk


def delete_file_chain(disk: MD100Disk, entry_idx: int, entry: DirEntry) -> None:
    """Free all FAT entries for a file and zero the directory entry."""
    i = entry.start_block
    while i != 0:
        j   = i & FB_BLOCK
        fat = disk.fat_get(j)
        if not (fat & FB_IN_USE):
            break   # broken chain
        disk.fat_set(j, 0)
        if fat & FB_LAST:
            break
        i = fat & FB_BLOCK
    disk.clear_entry(entry_idx)


# ─── Text / BASIC output ──────────────────────────────────────────────────────

_ESC_NONE    = 'NONE'
_ESC_HEX     = 'HEX'
_ESC_SYMBOLS = 'SYMBOLS'


def _token_to_text(prefix: int, byte: int, escape: str) -> str:
    """Translate a double-byte BASIC token to text."""
    if 4 <= prefix <= 7 and 0x40 <= byte <= 0xCF:
        tok = TOKENS[prefix - 4][byte - 0x40]
        if tok:
            return tok
    return f"\\{prefix:02X}\\{byte:02X}"


def print_basic(data: bytes, escape: str, out) -> None:
    """Decode and print a tokenized BASIC program (PB-1000 format)."""
    # First 256 bytes = header (password etc.)
    if len(data) > 17 and data[17] != 0xFF:
        pwd = []
        for i in range(17, min(17+8, len(data))):
            if data[i] == 0xFF: break
            c = data[i] ^ 0xFF
            pwd.append(f"\\{c:02X}" if (c < 0x20 or c >= 0x80) else chr(c))
        out.write("Password: " + ''.join(pwd) + '\n')

    # Token stream starts at byte 256
    i       = 256
    length  = 0
    pos     = 0
    line_nr = 0
    prefix  = 0
    lsb     = -1
    insert_space   = False
    pending_colon  = False
    quoted         = False
    quoted_eol     = False
    last_ch        = ''

    def emit(text: str):
        nonlocal insert_space, last_ch
        for ch in text:
            if ch == '"':
                quoted_state = True
            if insert_space and ch not in (' ', '\n') and (ch.isalnum() or ch >= 'A'):
                out.write(' ')
            out.write(ch)
            insert_space = False
            last_ch = ch

    while i < len(data):
        c = data[i]; i += 1

        if prefix == 3:   # line number reference
            if lsb == -1:
                lsb = c; continue
            else:
                emit(str(lsb + c * 256))
                lsb = -1; prefix = 0; continue

        if prefix != 0:
            text = _token_to_text(prefix, c, escape)
            prefix = 0
            if pending_colon:
                out.write(':'); pending_colon = False
            if text.endswith(' '):
                out.write(text[:-1])
                insert_space = True
            else:
                out.write(text)
            continue

        if length == -1:
            pos = 0; length = 0

        if pos == 0:
            length = c - 1; pos += 1; continue
        if pos == 1:
            line_nr = c; pos += 1; continue
        if pos == 2:
            line_nr += c * 256; out.write(f"{line_nr} "); pos += 1; continue

        pos += 1

        if c == 0x00:
            out.write('\n'); length = -1
        elif c == 0x01:
            pending_colon = True
        elif c == 0x02:
            if pending_colon:
                out.write(':'); pending_colon = False
            out.write("'"); quoted_eol = True
        elif 0x03 <= c <= 0x07:
            prefix = c; lsb = -1
        else:
            # Printable char or special
            if escape != _ESC_NONE:
                if escape == _ESC_SYMBOLS and c in ESC_BY_TOKEN:
                    text = f"\\{ESC_BY_TOKEN[c]}"
                elif c >= 0x80 or c == ord('\\'):
                    text = f"\\{c:02X}"
                else:
                    text = chr(c)
            else:
                text = chr(c) if 0x20 <= c < 0x80 else f"\\{c:02X}"
            if pending_colon and not text.startswith('ELSE'):
                out.write(':'); pending_colon = False
            if text == ' ':
                out.write(' '); insert_space = False
            else:
                if insert_space and (text[0].isalnum() or ord(text[0]) >= ord('A')):
                    out.write(' ')
                out.write(text)
                insert_space = text.endswith(' ')


def print_text(data: bytes, escape: str, out) -> None:
    """Print a sequential ASCII file, converting CR+LF to LF."""
    last_c = -1
    for byte in data:
        if byte == 0x0D:
            last_c = byte; continue  # skip CR
        out.write(chr(byte))
        last_c = byte
    if last_c not in (-1, 0x0A):
        out.write('\n')


def print_random(data: bytes, out) -> None:
    """Print a random-access file (256-byte records, trailing spaces stripped)."""
    for rec in range(0, len(data), SECTOR_SIZE):
        chunk = data[rec:rec+SECTOR_SIZE]
        line  = chunk.rstrip(b' ').decode('latin-1', errors='replace')
        out.write(f"{rec//SECTOR_SIZE + 1:4d}: {line}\n")


def print_machine(data: bytes, out) -> None:
    """Hex-dump a machine code file, showing header info first."""
    if len(data) >= 31:
        lo1 = data[25]; hi1 = data[26]
        lo2 = data[27]; hi2 = data[28]
        loe = data[29]; hie = data[30]
        out.write(f"Addresses: {hi1:02X}{lo1:02X}-{hi2:02X}{lo2:02X},"
                  f" Entry: {hie:02X}{loe:02X}\n\n")
        body  = data[SECTOR_SIZE:]
        start = hi1 * 256 + lo1
    else:
        body  = data
        start = 0
    _hex_dump(body, start, out)


def _hex_dump(data: bytes, addr: int, out) -> None:
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = ' '.join(f"{b:02X}" for b in chunk)
        asc_part = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
        out.write(f"{addr+i:05X}: {hex_part:<48}  {asc_part}\n")


def print_file(disk: MD100Disk, entry: DirEntry, mode: str, escape: str, out) -> None:
    """Print a file's content to `out`."""
    raw = read_file_data(disk, entry)
    if mode == 'BINARY' or entry.type_byte == TYPE_M:
        _hex_dump(raw, 0, out)
    elif entry.type_byte == TYPE_B and mode != 'ASCII':
        print_basic(raw, escape, out)
    elif entry.type_byte == TYPE_R and mode != 'ASCII':
        print_random(raw, out)
    else:
        print_text(raw, escape, out)


# ─── ASCII transfer helpers ───────────────────────────────────────────────────

def _is_ascii_basic_text(data: bytes) -> bool:
    """Return True if data is pure-ASCII text whose first non-empty line starts with a digit."""
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError:
        return False  # Non-ASCII bytes → binary tokenized BASIC
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[0].isdigit()
    return True  # empty or whitespace-only: treat as ASCII


def pc_to_disk_bytes(src_bytes: bytes, escape: str) -> bytes:
    """Convert PC text bytes to MD-100 format (LF→CR+LF, handle escapes)."""
    out  = bytearray()
    data = src_bytes
    i    = 0
    while i < len(data):
        c = data[i]; i += 1
        if c == 0x0D:
            continue  # discard stray CR
        if c == 0x0A:
            out.append(0x0D)
            out.append(0x0A)
            continue
        if escape != _ESC_NONE and c == ord('\\') and i < len(data):
            # Try 1- and 2-char escape sequences
            found = False
            for length in (2, 1):
                if i + length - 1 <= len(data):
                    seq = data[i:i+length].upper().decode('latin-1', errors='ignore')
                    if seq in ESC_BY_TEXT:
                        out.append(ESC_BY_TEXT[seq])
                        i += length
                        found = True
                        break
            if not found:
                # Try hex escape
                if i + 1 < len(data):
                    try:
                        val = int(data[i:i+2], 16)
                        out.append(val); i += 2; continue
                    except ValueError:
                        pass
                out.append(c)
        else:
            out.append(c)
    return bytes(out)


def disk_to_pc_bytes(raw: bytes) -> bytes:
    """Convert MD-100 text bytes to PC format (strip CR)."""
    return bytes(b for b in raw if b != 0x0D)


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_dir(disk: MD100Disk, args) -> int:
    pattern = args.pattern[0] if args.pattern else '*.*'
    case    = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    tfilter = args.type or 0
    pfilter = args.protect

    print(f"\n Directory of {disk.path}\n")
    print("   Name         Type   Size  Blk  Prot")

    total_size   = 0
    total_blocks = 0
    total_files  = 0

    for pat in (args.pattern or ['*.*']):
        matches = find_files(disk, pat, args.i, tfilter, pfilter, case)
        if not matches:
            print(f"{pat:<12}  no files")
            continue
        for idx, entry in matches:
            sz, blks = disk.file_blocks_and_size(entry.start_block)
            name     = entry.display_name(case)
            print(f"  {name:<12} {entry.type_str():<4} {sz:6d} {blks:4d}    {entry.protect:02X}")
            total_size   += sz
            total_blocks += blks
            total_files  += 1

    print(f"\n  Total: {total_files:3d} files  {total_size:8d} bytes  {total_blocks:4d} blocks")
    print(f"  Free:  {disk.dir_free():3d} slots  {disk.disk_free()*BLOCK_SIZE:8d} bytes  {disk.disk_free():4d} blocks")
    return 0


def cmd_type(disk: MD100Disk, args) -> int:
    if not args.files:
        print("type: missing filename", file=sys.stderr); return 1
    mode   = 'ASCII' if args.a else 'BINARY' if args.b else 'AUTO'
    escape = 'HEX' if args.eH else 'SYMBOLS' if args.eS else 'NONE'
    case   = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'

    for pat in args.files:
        matches = find_files(disk, pat, args.i, args.type or 0, args.protect, case)
        if not matches:
            print(f"{pat}: file not found", file=sys.stderr); continue
        multi = len(matches) > 1 or ('*' in pat or '?' in pat)
        for idx, entry in matches:
            if multi:
                print(f"\nFile {entry.display_name(case)}, Type {entry.type_str()}:\n")
            print_file(disk, entry, mode, escape, sys.stdout)
    return 0


def _dest_path(dest: Optional[str], name: str) -> str:
    """Resolve destination file path."""
    if dest is None:
        return name
    dest = dest.rstrip('*')
    if dest.endswith(('/','\\')) or os.path.isdir(dest):
        return os.path.join(dest, name)
    return dest


def _get_one(disk: MD100Disk, idx: int, entry: DirEntry,
             dest: Optional[str], mode: str, escape: str, case: str) -> str:
    """Extract one file to PC. Returns destination path."""
    dname = entry.display_name(case)
    # Sanitise filename for the PC filesystem
    safe  = re.sub(r'[\\/:*?"<>|]', '_', dname)
    out_path = _dest_path(dest, safe)

    raw    = read_file_data(disk, entry)
    binary = True
    if mode == 'ASCII':
        binary = False
    elif mode == 'AUTO':
        binary = entry.type_byte not in (TYPE_S, TYPE_C)

    if binary:
        with open(out_path, 'wb') as f:
            f.write(raw)
    else:
        text = disk_to_pc_bytes(raw)
        with open(out_path, 'wb') as f:
            f.write(text)
    return out_path


def cmd_get(disk: MD100Disk, args) -> int:
    if not args.source:
        print("get: missing source", file=sys.stderr); return 1
    mode   = 'ASCII' if args.a else 'BINARY' if args.b else 'AUTO'
    escape = 'HEX' if args.eH else 'SYMBOLS' if args.eS else 'NONE'
    case   = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'

    matches = find_files(disk, args.source, args.i, args.type or 0, args.protect, case)
    if not matches:
        print(f"{args.source}: file not found", file=sys.stderr); return 1

    idx, entry = matches[0]
    dest = args.dest or args.d
    out_path = _get_one(disk, idx, entry, dest, mode, escape, case)
    print(f"{entry.display_name(case):<12} {entry.type_str():<2} copied to {out_path}", file=sys.stderr)
    return 0


def cmd_mget(disk: MD100Disk, args) -> int:
    if not args.pattern:
        print("mget: missing pattern", file=sys.stderr); return 1
    mode   = 'ASCII' if args.a else 'BINARY' if args.b else 'AUTO'
    escape = 'HEX' if args.eH else 'SYMBOLS' if args.eS else 'NONE'
    case   = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    dest   = args.d

    total = 0
    for pat in args.pattern:
        matches = find_files(disk, pat, args.i, args.type or 0, args.protect, case)
        if not matches:
            print(f"{pat}: no files", file=sys.stderr); continue
        for idx, entry in matches:
            out_path = _get_one(disk, idx, entry, dest, mode, escape, case)
            print(f"{entry.display_name(case):<12} {entry.type_str():<2} copied to {out_path}")
            total += 1
    print(f"{total} file(s) copied")
    return 0


def _put_one(disk: MD100Disk, src_path: str, dest_pattern: Optional[str],
             mode: str, escape: str, case: str,
             type_byte: int, protect: int, no_update: bool) -> Tuple[str, str, int]:
    """
    Copy one PC file into the disk image.
    Returns (md100_name, type_str, block_count).
    Raises RuntimeError on failure.
    """
    name_8, ext_3 = pc_name_to_md100(src_path, dest_pattern, case)

    # Auto-detect type from extension if not specified
    if type_byte == 0:
        ext = ext_3.rstrip(b' ').decode('latin-1').lower()
        type_byte = EXT_TYPE.get(ext, TYPE_S)

    # Read source
    if src_path.lower().startswith('stdin'):
        raw = sys.stdin.buffer.read()
    else:
        with open(src_path, 'rb') as f:
            raw = f.read()

    # TYPE_B on disk requires a 256-byte header with machine-specific RAM addresses;
    # ASCII text content causes OM Error on LOAD (rom1.src DF9D-DFBA misparses it).
    # Use TYPE_S so the ROM tokenises the text on the fly (rom1.src DFBC-DFEE).
    if type_byte == TYPE_B and mode == 'AUTO' and _is_ascii_basic_text(raw):
        type_byte = TYPE_S

    # Transfer mode
    if mode == 'AUTO':
        mode = 'BINARY' if type_byte in (TYPE_M, TYPE_R, TYPE_B) else 'ASCII'

    if mode == 'ASCII':
        disk_bytes = pc_to_disk_bytes(raw, escape)
    else:
        disk_bytes = raw

    # Check free space
    needed_blocks = (len(disk_bytes) // BLOCK_SIZE) + 1
    if needed_blocks > disk.disk_free():
        raise RuntimeError("No room on disk")

    # Find existing entry with same name, or free slot
    name_ext = name_8 + ext_3
    found_idx  = -1
    free_idx   = -1
    for i in range(MAX_DIR_ENTRIES):
        e = disk.get_entry(i)
        if not e.is_free and e.name_ext == name_ext:
            found_idx = i; break
        if e.is_free and free_idx == -1:
            free_idx = i

    if found_idx >= 0:
        # Delete existing file first
        delete_file_chain(disk, found_idx, disk.get_entry(found_idx))
        slot = found_idx
    else:
        if free_idx < 0:
            raise RuntimeError("Directory full")
        slot = free_idx

    # Build new directory entry
    new_entry = DirEntry(
        type_byte   = type_byte,
        name        = name_8,
        ext         = ext_3,
        unused      = DEF_UNUSED,
        start_block = 0,
        protect     = protect if protect >= 0 else 0,
    )

    # Write data blocks
    if not no_update:
        first_blk = _write_data_blocks(disk, disk_bytes)
        new_entry.start_block = first_blk

    disk.set_entry(slot, new_entry)

    display = new_entry.display_name(case)
    _, blks  = disk.file_blocks_and_size(new_entry.start_block)
    return display, new_entry.type_str(), blks


def cmd_put(disk: MD100Disk, args) -> int:
    if not args.source:
        print("put: missing source", file=sys.stderr); return 1
    mode   = 'ASCII' if args.a else 'BINARY' if args.b else 'AUTO'
    escape = 'HEX' if args.eH else 'SYMBOLS' if args.eS else 'NONE'
    case   = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    dest   = args.dest if hasattr(args, 'dest') and args.dest else None

    try:
        name, ts, blks = _put_one(disk, args.source, dest, mode, escape, case,
                                   args.type or 0, args.protect, disk.no_update)
        print(f"{name:<12} {ts:<2} {blks:3d} block(s) created from {args.source}",
              file=sys.stderr)
    except (OSError, RuntimeError) as e:
        print(f"{args.source}: {e}", file=sys.stderr); return 1
    return 0


def cmd_mput(disk: MD100Disk, args) -> int:
    if not args.files:
        print("mput: missing source files", file=sys.stderr); return 1
    mode   = 'ASCII' if args.a else 'BINARY' if args.b else 'AUTO'
    escape = 'HEX' if args.eH else 'SYMBOLS' if args.eS else 'NONE'
    case   = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    dest   = args.d

    for src in args.files:
        if os.path.abspath(src) == os.path.abspath(disk.path):
            continue
        try:
            name, ts, blks = _put_one(disk, src, dest, mode, escape, case,
                                       args.type or 0, args.protect, disk.no_update)
            print(f"{name:<12} {ts:<2} {blks:3d} block(s) created from {src}",
                  file=sys.stderr)
        except (OSError, RuntimeError) as e:
            print(f"{src}: {e}", file=sys.stderr); return 1
    return 0


def cmd_del(disk: MD100Disk, args) -> int:
    if not args.pattern:
        print("del: missing pattern", file=sys.stderr); return 1
    case  = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    total = 0
    for pat in args.pattern:
        matches = find_files(disk, pat, args.i, args.type or 0, args.protect, case)
        if not matches:
            print(f"{pat}: file not found", file=sys.stderr); continue
        for idx, entry in matches:
            name = entry.display_name(case)
            delete_file_chain(disk, idx, entry)
            print(f"{name:<12} {entry.type_str():<2} deleted")
            total += 1
    if total:
        print(f"{total} file(s) deleted")
    else:
        print("No files deleted")
    return 0


def cmd_ren(disk: MD100Disk, args) -> int:
    if not args.source or not args.newname:
        print("ren: need source and newname", file=sys.stderr); return 1
    case  = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    total = 0

    matches = find_files(disk, args.source, args.i, args.type or 0, args.protect, 'AS_IS')
    if not matches:
        print(f"{args.source}: file not found", file=sys.stderr); return 1

    for idx, entry in matches:
        # Build new name from pattern, filling wildcards from existing name
        new_8, new_3 = name_to_11bytes(args.newname, case, entry)
        new_ne = new_8 + new_3

        # Check for duplicate
        dup = next(
            ((j, e) for j, e in disk.all_entries()
             if not e.is_free and j != idx and e.name_ext == new_ne),
            None
        )
        if dup:
            print(f"{entry.display_name()}: duplicate: {dup[1].display_name()}", file=sys.stderr)
            return 1

        old_name = entry.display_name(case)
        entry.name = new_8
        entry.ext  = new_3
        disk.set_entry(idx, entry)
        new_name = entry.display_name(case)
        if old_name != new_name:
            print(f"{old_name:<12} renamed to {new_name}")
            total += 1
        else:
            print(f"{old_name:<12} not changed")

    if total:
        print(f"{total} file(s) renamed")
    return 0


def cmd_set(disk: MD100Disk, args) -> int:
    if not args.pattern:
        print("set: missing pattern", file=sys.stderr); return 1
    if not args.type and args.protect < 0:
        print("set: requires -t and/or -p", file=sys.stderr); return 1
    case  = 'LOWER' if args.l else 'UPPER' if args.u else 'AS_IS'
    total = 0

    for pat in args.pattern:
        matches = find_files(disk, pat, args.i, 0, -1, case)
        if not matches:
            print(f"{pat}: file not found", file=sys.stderr); continue
        for idx, entry in matches:
            changed = False
            old_t   = entry.type_str()
            old_p   = entry.protect
            if args.type and entry.type_byte != args.type:
                entry.type_byte = args.type; changed = True
            if args.protect >= 0 and entry.protect != args.protect:
                entry.protect = args.protect; changed = True
            if changed:
                disk.set_entry(idx, entry)
                print(f"{entry.display_name(case):<12} {old_t}→{entry.type_str()}"
                      f"  P={old_p:X}→{entry.protect:X}")
                total += 1
            else:
                print(f"{entry.display_name(case):<12} {entry.type_str()} P={entry.protect:X} not changed")
    if total:
        print(f"{total} file(s) changed")
    return 0


# ─── Argument parsing ─────────────────────────────────────────────────────────

def _parse_type(s: str) -> int:
    s = s.upper()
    if s in TYPE_BY_LETTER:
        return TYPE_BY_LETTER[s]
    try:
        return int(s, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Unknown type '{s}' (use B/C/M/R/S or hex)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='md100.py',
        description='Casio MD-100 floppy disk image utility',
        add_help=True,
    )
    p.add_argument('--_create', metavar='SIZE', type=int, default=None,
                   help=argparse.SUPPRESS)   # injected by main() after pre-parsing -c
    p.add_argument('-n', action='store_true',
                   help='no updates written to image')
    p.add_argument('image', help='disk image file')
    sub = p.add_subparsers(dest='cmd')

    def common(sp):
        sp.add_argument('-i', action='store_true', help='case-insensitive')
        sp.add_argument('-l', action='store_true', help='lowercase on PC')
        sp.add_argument('-u', action='store_true', help='uppercase on PC')
        sp.add_argument('-t', dest='type', metavar='TYPE', type=_parse_type, default=0,
                        help='type filter/set (B/C/M/R/S or hex)')
        sp.add_argument('-p', dest='protect', metavar='N', type=lambda s: int(s,0),
                        default=-1, help='protection 0/1')

    def transfer(sp):
        sp.add_argument('-a', action='store_true', help='ASCII transfer')
        sp.add_argument('-b', action='store_true', help='binary transfer')
        sp.add_argument('-eH', action='store_true', help='hex escape syntax')
        sp.add_argument('-eS', action='store_true', help='symbol escape syntax')
        sp.add_argument('-d', metavar='DIR', default=None,
                        help='destination directory')

    # dir
    s = sub.add_parser('dir', help='list directory')
    common(s); s.add_argument('pattern', nargs='*')

    # type / list
    for name in ('type', 'list'):
        s = sub.add_parser(name, help='display file contents')
        common(s); transfer(s)
        s.add_argument('files', nargs='+')

    # get
    s = sub.add_parser('get', help='copy file from image to PC')
    common(s); transfer(s)
    s.add_argument('source')
    s.add_argument('dest', nargs='?', default=None)

    # mget
    s = sub.add_parser('mget', help='copy files from image to PC')
    common(s); transfer(s)
    s.add_argument('pattern', nargs='+')

    # put
    s = sub.add_parser('put', help='copy file from PC to image')
    common(s); transfer(s)
    s.add_argument('source')
    s.add_argument('dest', nargs='?', default=None)

    # mput
    s = sub.add_parser('mput', help='copy files from PC to image')
    common(s); transfer(s)
    s.add_argument('files', nargs='+')

    # del / delete
    for name in ('del', 'delete'):
        s = sub.add_parser(name, help='delete files')
        common(s); s.add_argument('pattern', nargs='+')

    # ren / rename
    for name in ('ren', 'rename'):
        s = sub.add_parser(name, help='rename files')
        common(s)
        s.add_argument('source')
        s.add_argument('newname')

    # set
    s = sub.add_parser('set', help='change file type/protection')
    common(s); s.add_argument('pattern', nargs='+')

    return p


# ─── Main ─────────────────────────────────────────────────────────────────────

CMD_MAP = {
    'dir':    cmd_dir,
    'type':   cmd_type,
    'list':   cmd_type,
    'get':    cmd_get,
    'mget':   cmd_mget,
    'put':    cmd_put,
    'mput':   cmd_mput,
    'del':    cmd_del,
    'delete': cmd_del,
    'ren':    cmd_ren,
    'rename': cmd_ren,
    'set':    cmd_set,
}


def _preprocess_argv(argv: list) -> tuple:
    """
    Extract -c[SIZE] from argv before argparse sees it.
    Returns (create, size, cleaned_argv).
    Handles: -c, -c320, -c 320
    """
    create = False
    size   = DEFAULT_SIZE
    out    = []
    i      = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '-c':
            create = True
            if i + 1 < len(argv):
                try:
                    size = int(argv[i + 1])
                    i += 2; continue
                except ValueError:
                    pass
            i += 1; continue
        elif re.match(r'^-c\d+$', arg):
            create = True
            size   = int(arg[2:])
            i += 1; continue
        out.append(arg)
        i += 1
    return create, size, out


def main() -> int:
    create, size, argv = _preprocess_argv(sys.argv[1:])
    parser = build_parser()
    args   = parser.parse_args(argv)

    # If no subcommand was given, default to 'dir' with empty pattern
    if args.cmd is None:
        argv.append('dir')
        args = parser.parse_args(argv)

    try:
        with MD100Disk(args.image, create=create, size=size,
                       no_update=args.n) as disk:
            cmd = args.cmd
            fn  = CMD_MAP.get(cmd)
            if fn is None:
                print(f"Unknown command: {cmd}", file=sys.stderr)
                parser.print_help(sys.stderr)
                return 2
            return fn(disk, args)
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
