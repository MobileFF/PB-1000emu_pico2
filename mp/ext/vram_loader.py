"""
vram_loader.py — カラーVRAM イメージローダー 拡張モジュール

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CALL &H5E20  SDカード/フラッシュ → バンクRAM → カラーVRAM
 CALL &H5E21  仮想FDDイメージ     → バンクRAM → カラーVRAM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ロード後はバンクRAMにデータが残るため、DMA MMIO (0x0C30-0x0C37) で
ファイルI/Oなしの高速再転送が可能。

【共通パラメータ (CALL 前に POKE)】
  &H5F42  中継バンク番号 (1/2/3、デフォルト=2)
  &H5F43  転送先オフセット lo  (color_vram 内、デフォルト=0)
  &H5F44  転送先オフセット hi
  &H5F45  転送バイト数 lo      (0=ファイル全体)
  &H5F46  転送バイト数 hi

【CALL &H5E20 専用パラメータ】
  &H5F01  ファイル名バイト長 (1-64)
  &H5F02-&H5F41  ファイル名 ASCII
          絶対パス例: /sd/images/bg.bin
          ファイル名のみの場合は /sd/images/, /sd/screenshots/, /sd/, / の順に検索

【CALL &H5E21 専用パラメータ】
  &H5F01  ファイル名バイト長 (1-12)
  &H5F02-&H5F0D  ファイル名 ASCII (8.3形式: "NAME.EXT" or "NAME    EXT")
          FDD がマウントされていない場合はエラー (結果コード 5)

【出力結果 (CALL 後に PEEK)】
  &H5F00  0=OK / 1=ファイル未発見 / 2=読み取りエラー
          3=バンク未割当 / 4=範囲外 / 5=FDD未マウント
  &H5F47  実転送バイト数 lo
  &H5F48  実転送バイト数 hi

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 BASIC 使用例 (SDカード)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  10 S$="bg.bin"
  20 POKE &H5F01,LEN(S$)
  30 FOR I=1 TO LEN(S$):POKE &H5F01+I,ASC(MID$(S$,I,1)):NEXT I
  40 POKE &H5F42,2:POKE &H5F43,0:POKE &H5F44,0
  50 POKE &H5F45,0:POKE &H5F46,0
  60 CALL &H5E20
  70 IF PEEK(&H5F00)<>0 THEN PRINT "ERR:";PEEK(&H5F00):END
  80 PRINT PEEK(&H5F47)+PEEK(&H5F48)*256;"bytes"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 BASIC 使用例 (仮想FDD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  10 S$="BG.BIN"
  20 POKE &H5F01,LEN(S$)
  30 FOR I=1 TO LEN(S$):POKE &H5F01+I,ASC(MID$(S$,I,1)):NEXT I
  40 POKE &H5F42,2:POKE &H5F43,0:POKE &H5F44,0
  50 POKE &H5F45,0:POKE &H5F46,0
  60 CALL &H5E21
  70 IF PEEK(&H5F00)<>0 THEN PRINT "ERR:";PEEK(&H5F00):END
  80 PRINT PEEK(&H5F47)+PEEK(&H5F48)*256;"bytes"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DMA 再転送例 (ロード済みデータをVRAMに再反映)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  POKE &H0C30,2:POKE &H0C31,0:POKE &H0C32,0
  POKE &H0C33,0:POKE &H0C34,0
  POKE &H0C35,0:POKE &H0C36,&H30
  POKE &H0C37,0
  IF PEEK(&H0C37) AND 1 THEN PRINT "DMA ERR"
"""

try:
    import uos as _os
except ImportError:
    import os as _os

CALL_ADDR     = 0x5E20   # SD/フラッシュファイル
CALL_FDD_ADDR = 0x5E21   # 仮想FDDイメージ内ファイル

_COLOR_VRAM_SIZE = 192 * 64   # 12,288 bytes
_BANK_SIZE       = 0x8000     # 32 KB per bank
_FNAME_MAX_SD    = 64
_FNAME_MAX_FDD   = 12         # 8.3 形式: "NAME    EXT" or "NAME.EXT"

# ext_work オフセット (EXT_WORK_BASE 0x5F00 からの相対)
_W_FNAME_LEN = 0x01
_W_FNAME     = 0x02
_W_BANK      = 0x42
_W_DST_LO    = 0x43
_W_DST_HI    = 0x44
_W_LEN_LO    = 0x45
_W_LEN_HI    = 0x46
_W_XFER_LO   = 0x47
_W_XFER_HI   = 0x48

# 結果コード
_OK         = 0x00
_ERR_NOFILE = 0x01   # ファイル未発見
_ERR_READ   = 0x02   # 読み取りエラー
_ERR_NOBANK = 0x03   # バンク未割当
_ERR_RANGE  = 0x04   # 範囲外
_ERR_NOFDD  = 0x05   # FDD 未マウント

# SD ファイル名のみ指定時の検索ディレクトリ
_SEARCH_DIRS = ('/sd/images', '/sd/screenshots', '/sd', '')

# FDD 内部で使用するファイルハンドル番号
_FDD_HANDLE = 15


def register(system):
    system.register_call_hook(CALL_ADDR,     lambda: _load_vram_sd(system))
    system.register_call_hook(CALL_FDD_ADDR, lambda: _load_vram_fdd(system))
    print(f"vram_loader: CALL &H{CALL_ADDR:04X} (SD)  CALL &H{CALL_FDD_ADDR:04X} (FDD) ready")


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def _parse_common(w):
    """共通転送パラメータを ext_work から取得して返す。"""
    slot = w[_W_BANK] if w[_W_BANK] in (1, 2, 3) else 2
    dst  = w[_W_DST_LO] | (w[_W_DST_HI] << 8)
    rlen = w[_W_LEN_LO] | (w[_W_LEN_HI] << 8)
    return slot, dst, rlen


def _bank_write(buf, offset, data):
    """RAMView / bytearray への高速書き込み (_view 経由)。"""
    buf[offset:offset + len(data)] = data


def _bank_read(buf, offset, length):
    """RAMView / bytearray からの高速読み出し (memoryview を返す)。"""
    return buf[offset:offset + length]


def _write_result(w, result, xfer_len=0):
    w[0]         = result
    w[_W_XFER_LO] = xfer_len & 0xFF
    w[_W_XFER_HI] = (xfer_len >> 8) & 0xFF


def _finish_transfer(system, bank_buf, slot, dst, xfer_len):
    """バンクRAM → カラーVRAM 転送の共通後処理。
    転送後に VDP を有効化して dirty フラグを立て、次フレームで再描画させる。"""
    import lcd_c as _lc
    cvram = _lc.get_color_vram()
    cvram[dst:dst + xfer_len] = _bank_read(bank_buf, 0, xfer_len)
    _lc.set_vdp_enable(True)   # カラーVRAM レンダリングを有効化
    _lc.mark_dirty()           # 次フレームで全ページ再描画


# ---------------------------------------------------------------------------
# SD カード / フラッシュ版
# ---------------------------------------------------------------------------

def _resolve_path(fname):
    """絶対パスはそのまま。ファイル名のみは共通ディレクトリを順に検索。"""
    if fname.startswith('/'):
        return fname
    for d in _SEARCH_DIRS:
        p = (d + '/' + fname) if d else ('/' + fname)
        try:
            _os.stat(p)
            return p
        except OSError:
            pass
    return '/' + fname


def _load_vram_sd(system):
    """CALL &H5E20: SD/フラッシュファイル → バンクRAM → カラーVRAM。"""
    w = system._ext_work

    fname_len = w[_W_FNAME_LEN]
    if not (1 <= fname_len <= _FNAME_MAX_SD):
        _write_result(w, _ERR_RANGE)
        return
    fname = bytes(w[_W_FNAME:_W_FNAME + fname_len]).decode('ascii', 'ignore')
    path  = _resolve_path(fname)

    slot, dst, rlen = _parse_common(w)

    if not system.has_bank[slot]:
        _write_result(w, _ERR_NOBANK)
        print(f"vram_loader: bank{slot} not available")
        return
    if dst >= _COLOR_VRAM_SIZE:
        _write_result(w, _ERR_RANGE)
        return

    read_cap = min(rlen if rlen else _COLOR_VRAM_SIZE, _BANK_SIZE)
    try:
        with open(path, 'rb') as f:
            data = f.read(read_cap)
    except OSError:
        _write_result(w, _ERR_NOFILE)
        print(f"vram_loader: not found: {path}")
        return
    except Exception as e:
        _write_result(w, _ERR_READ)
        print(f"vram_loader: read error: {e}")
        return

    xfer_len = min(len(data), _COLOR_VRAM_SIZE - dst, _BANK_SIZE)
    if xfer_len == 0:
        _write_result(w, _ERR_RANGE)
        return

    bank_buf = system._bank_ram[slot]
    _bank_write(bank_buf, 0, data[:xfer_len])
    _finish_transfer(system, bank_buf, slot, dst, xfer_len)
    _write_result(w, _OK, xfer_len)
    print(f"vram_loader(SD): '{path}' -> BANK{slot}+cvram[{dst}:{dst+xfer_len}] ({xfer_len}B)")


# ---------------------------------------------------------------------------
# 仮想 FDD 版
# ---------------------------------------------------------------------------

def _to_name11(fname):
    """ファイル名を 11 バイト MD-100 DOS 形式に変換する。
    "NAME.EXT" → b"NAME    EXT"  (space-padded, uppercase)
    "NAME    EXT" (11 chars, no dot) → そのままバイト列に変換
    """
    fname = fname.upper().strip()
    if len(fname) == 11 and '.' not in fname:
        return bytearray(fname.encode('ascii', 'replace'))
    if '.' in fname:
        parts = fname.rsplit('.', 1)
        name = (parts[0][:8] + '        ')[:8]
        ext  = (parts[1][:3] + '   ')[:3]
    else:
        name = (fname[:8] + '        ')[:8]
        ext  = '   '
    return bytearray((name + ext).encode('ascii', 'replace'))


def _get_dos(system):
    """FDDProtocol から MD100Dos インスタンスを取得する。None なら未マウント。"""
    ctrl = getattr(system, 'virtual_fdd_controller', None)
    if ctrl is None:
        return None
    dos = getattr(ctrl, '_dos', None)
    if dos is None or not dos.is_ready():
        return None
    return dos


def _find_free_handle(dos):
    """未使用のファイルハンドルを返す。なければ -1。"""
    # _fileinfo[h][0] (_FI_DIRINDEX) が -1 なら未使用
    for h in range(len(dos._fileinfo) - 1, -1, -1):
        if dos._fileinfo[h][0] < 0:
            return h
    return -1


def _load_vram_fdd(system):
    """CALL &H5E21: 仮想FDDイメージ内ファイル → バンクRAM → カラーVRAM。"""
    from fdd_storage import SIZE_SECTOR

    w = system._ext_work

    fname_len = w[_W_FNAME_LEN]
    if not (1 <= fname_len <= _FNAME_MAX_FDD):
        _write_result(w, _ERR_RANGE)
        return
    fname = bytes(w[_W_FNAME:_W_FNAME + fname_len]).decode('ascii', 'ignore')
    name11 = _to_name11(fname)

    slot, dst, rlen = _parse_common(w)

    if not system.has_bank[slot]:
        _write_result(w, _ERR_NOBANK)
        print(f"vram_loader(FDD): bank{slot} not available")
        return
    if dst >= _COLOR_VRAM_SIZE:
        _write_result(w, _ERR_RANGE)
        return

    dos = _get_dos(system)
    if dos is None:
        _write_result(w, _ERR_NOFDD)
        print("vram_loader(FDD): FDD not mounted")
        return

    handle = _find_free_handle(dos)
    if handle < 0:
        _write_result(w, _ERR_READ)
        print("vram_loader(FDD): no free file handle")
        return

    # ── ファイルオープン ───────────────────────────────────────────────────
    from md100_dos import DS_NO_ERROR, DS_FILE_NOT_FOUND
    idx = dos.open_disk_file(handle, name11)
    if idx < 0:
        _write_result(w, _ERR_NOFILE)
        print(f"vram_loader(FDD): not found: {fname!r} (status={dos.dos_status})")
        return

    # ── セクタ単位で読み込みバンクRAMに積む ──────────────────────────────
    max_bytes = min(rlen if rlen else _COLOR_VRAM_SIZE, _BANK_SIZE, _COLOR_VRAM_SIZE - dst)
    bank_buf  = system._bank_ram[slot]
    sec_buf   = bytearray(SIZE_SECTOR)
    offset    = 0
    try:
        while offset < max_bytes:
            n = dos.read_disk_file(handle, sec_buf)
            if n == 0:
                break
            copy_n = min(n, max_bytes - offset)
            _bank_write(bank_buf, offset, sec_buf[:copy_n])
            offset += copy_n
            if dos.is_end_of_disk_file(handle):
                break
            dos.seek_rel_disk_file(handle, 1)
    except Exception as e:
        dos.close_disk_file(handle)
        _write_result(w, _ERR_READ)
        print(f"vram_loader(FDD): read error: {e}")
        return

    dos.close_disk_file(handle)

    xfer_len = offset
    if xfer_len == 0:
        _write_result(w, _ERR_NOFILE)
        return

    # ── バンクRAM → カラーVRAM 転送 ───────────────────────────────────────
    _finish_transfer(system, bank_buf, slot, dst, xfer_len)
    _write_result(w, _OK, xfer_len)
    print(f"vram_loader(FDD): '{fname}' -> BANK{slot}+cvram[{dst}:{dst+xfer_len}] ({xfer_len}B)")
