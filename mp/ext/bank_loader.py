"""
Bank RAM Loader Extension

Load binary data from SD card or virtual FDD disk image into bank RAM (banks 1/2/3).

--- Load from SD file ---
CALL &H5E81

Input (_ext_work before CALL):
  [0]      : bank number (1/2/3)
  [1][2]   : destination offset high/low byte (0x0000-0x7FFF, bank-relative)
  [3][4]   : file byte offset high/low byte (0x0000 = start of file)
  [5][6]   : max length high/low byte (0x0000 = load all that fits)
  [7..]    : file path, null-terminated ASCII (e.g. "/sd/game.bin")

Output (_ext_work after CALL):
  [0]      : 0x00=OK  0x01=bank not present  0x02=file error  0xFF=other error
  [1][2]   : bytes loaded high/low byte

--- Load from FDD disk image ---
CALL &H5E91

Input (_ext_work before CALL):
  [0]      : bank number (1/2/3)
  [1][2]   : destination offset high/low byte (0x0000-0x7FFF, bank-relative)
  [3]      : skip records (0 = from beginning of file)
  [4..14]  : filename 11 bytes (8 name + 3 ext, space-padded, e.g. b"PROGRAM BAS")

Output (_ext_work after CALL):
  [0]      : 0x00=OK  0x01=bank not present  0x02=file not found
             0x03=FDD not ready  0xFF=other error
  [1][2]   : bytes loaded high/low byte

BASIC example (load "/sd/game.bin" into bank 2 at offset 0x0000):
  POKE &H5F00, 2          ' bank 2
  POKE &H5F01, &H00       ' dest high
  POKE &H5F02, &H00       ' dest low
  POKE &H5F03, 0          ' file offset high
  POKE &H5F04, 0          ' file offset low
  POKE &H5F05, 0          ' max length high (0=all)
  POKE &H5F06, 0          ' max length low
  ' "/sd/game.bin" = 47,115,100,47,103,97,109,101,46,98,105,110,0
  POKE &H5F07, 47  : POKE &H5F08, 115 : POKE &H5F09, 100
  ...
  CALL &H5E81
  IF PEEK(&H5F00)<>0 THEN PRINT "ERR": END
  PRINT "LOADED:"; PEEK(&H5F01)*256+PEEK(&H5F02); "BYTES"
"""

from md100_dos import MD100Dos, DS_NO_ERROR

SD_LOAD_ADDR  = 0x5E81
FDD_LOAD_ADDR = 0x5E91

_ERR_OK        = 0x00
_ERR_NO_BANK   = 0x01
_ERR_FILE      = 0x02
_ERR_FDD_READY = 0x03
_ERR_GENERAL   = 0xFF

_CHUNK = 256


def register(system):
    try:
        system.register_call_hook(SD_LOAD_ADDR,  lambda: _load_sd(system))
        system.register_call_hook(FDD_LOAD_ADDR, lambda: _load_fdd(system))
        print(f"bank_loader: CALL &H{SD_LOAD_ADDR:04X}  -> load SD file to bank RAM")
        print(f"bank_loader: CALL &H{FDD_LOAD_ADDR:04X}  -> load FDD file to bank RAM")
    except Exception as e:
        print(f"bank_loader: init failed: {e}")


def _check_bank(system, bank):
    """Return (buf, buf_size) or None if bank is unavailable."""
    if bank < 1 or bank > 3 or not system.has_bank[bank]:
        return None
    buf = system._bank_ram[bank]
    if buf is None:
        return None
    return buf, len(buf)


def _load_sd(system):
    w = system._ext_work
    try:
        bank     = w[0]
        dest_off = (w[1] << 8) | w[2]
        file_off = (w[3] << 8) | w[4]
        max_len  = (w[5] << 8) | w[6]

        # Decode null-terminated filename starting at w[7]
        end = 7
        while end < len(w) and w[end] != 0:
            end += 1
        path = bytes(w[7:end]).decode("ascii", "replace")

        result = _check_bank(system, bank)
        if result is None:
            _set_result(w, _ERR_NO_BANK, 0)
            return
        buf, buf_size = result

        avail = buf_size - dest_off
        if avail <= 0:
            _set_result(w, _ERR_GENERAL, 0)
            return

        to_read = avail if max_len == 0 else min(max_len, avail)

        total = 0
        with open(path, "rb") as f:
            if file_off:
                f.seek(file_off)
            remaining = to_read
            while remaining > 0:
                data = f.read(min(_CHUNK, remaining))
                if not data:
                    break
                n = len(data)
                for i in range(n):
                    buf[dest_off + total + i] = data[i]
                total += n
                remaining -= n

        _set_result(w, _ERR_OK, total)

    except OSError as e:
        print(f"bank_loader SD: {e}")
        _set_result(w, _ERR_FILE, 0)
    except Exception as e:
        print(f"bank_loader SD: {e}")
        _set_result(w, _ERR_GENERAL, 0)


def _load_fdd(system):
    w = system._ext_work
    try:
        bank      = w[0]
        dest_off  = (w[1] << 8) | w[2]
        skip_recs = w[3]
        name11    = bytes(w[4:15])

        result = _check_bank(system, bank)
        if result is None:
            _set_result(w, _ERR_NO_BANK, 0)
            return
        buf, buf_size = result

        if system.virtual_fdd is None:
            _set_result(w, _ERR_FDD_READY, 0)
            return

        # Use a fresh DOS instance on the shared backend to avoid disturbing
        # the FDD protocol's own open handles and sector cache.
        dos = MD100Dos()
        dos.dos_init(system.virtual_fdd)

        handle = 0
        idx = dos.open_disk_file(handle, name11)
        if idx < 0:
            _set_result(w, _ERR_FILE, 0)
            return

        if skip_recs:
            dos.seek_abs_disk_file(handle, skip_recs)

        rec_buf = bytearray(256)
        total = 0
        while True:
            n = dos.read_disk_file(handle, rec_buf)
            if n == 0:
                break
            avail = buf_size - dest_off - total
            to_copy = min(n, avail)
            for i in range(to_copy):
                buf[dest_off + total + i] = rec_buf[i]
            total += to_copy
            if total >= buf_size - dest_off:
                break
            dos.seek_rel_disk_file(handle, 1)

        dos.close_disk_file(handle)

        _set_result(w, _ERR_OK, total)

    except Exception as e:
        print(f"bank_loader FDD: {e}")
        _set_result(w, _ERR_GENERAL, 0)


def _set_result(w, code, byte_count):
    w[0] = code
    w[1] = (byte_count >> 8) & 0xFF
    w[2] = byte_count & 0xFF
