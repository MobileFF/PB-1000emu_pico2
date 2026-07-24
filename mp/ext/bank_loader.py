"""
Bank RAM Loader Extension

CALL &H5E81  SD/フラッシュファイル   → バンクRAM (1/2/3) ロード
CALL &H5E91  仮想FDDイメージ内ファイル → バンクRAM (1/2/3) ロード

ext_work レイアウト・結果コード・BASIC使用例は
doc/extension_api.md「bank_loader.py」を参照。ここに全文を置くと
起動時ロードのたびにコンパイル時メモリを圧迫するため要点のみ。
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
        system.register_call_hook(SD_LOAD_ADDR,  lambda: _load_sd(system), owner="bank_loader")
        system.register_call_hook(FDD_LOAD_ADDR, lambda: _load_fdd(system), owner="bank_loader")
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
