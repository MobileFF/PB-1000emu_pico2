import hd61700

REG_NAMES = [f"${i}" for i in range(32)]
REG8_NAMES = ["PE", "PD", "IB", "UA", "IA", "IE", "TM", "TM2"]
COND_NAMES = ["Z", "NC", "LZ", "UZ", "NZ", "C", "NLZ", "AL"]
CALC_NAMES = [
    "ADC", "SBC", "LD", "LDC", "ANC", "NAC", "ORC", "XRC",
    "AD", "SB", "ADB", "SBB", "AN", "NA", "OR", "XR",
]
STR1_NAMES = ["PE", "PD", "IB", "UA"]
STR2_NAMES = ["IA", "IE", "??", "TM"]
SREG2_NAMES = ["SX", "SY", "SZ", "?"]
IOCMD_NAMES = ["PO", "PO?", "FL", "FL?"]
SPCMD_NAMES = ["NOP", "CLT", "FST", "SLW", "CANI", "RTNI", "OFF", "TRP"]
CALC_MEM_NAMES = ["ADC", "SBC", "AD", "SB"]
IR1_NAMES = ["IX", "IY", "IZ", "US"]
IR2_NAMES = ["SS", "KY", "KY", "KY"]
ROT_NAMES = ["ROD", "ROU", "BID", "BIU"]
ROT2_NAMES = ["DID", "DIU", "BYD", "BYU"]
CMPINV_NAMES = ["CMP", "CMP?", "INV", "INV?"]

INT_ROM_WORDS = 0x0C00

def _advance_fetch_addr(pc, byte_count):
    if pc is None:
        return None
    pc_cur = pc & 0xFFFF
    fetch_addr = (pc_cur << 1) if (pc_cur <= INT_ROM_WORDS) else pc_cur
    for _ in range(byte_count):
        fetch_addr += 1
        if pc_cur <= INT_ROM_WORDS:
            pc_cur = (fetch_addr >> 1) & 0xFFFF
        else:
            pc_cur = fetch_addr & 0xFFFF
    return pc_cur


def get_sir_name(val):
    names = {0: "SX", 1: "SY", 2: "SZ"}
    return names.get(val & 0x03, f"S{val}")


def _read_u8(b, idx):
    if idx >= len(b):
        return None
    return b[idx]


def _fmt_reg(idx):
    return REG_NAMES[idx & 0x1F]


def _fmt_regpair(idx):
    lo = idx & 0x1F
    hi = (lo + 1) & 0x1F
    return f"{_fmt_reg(lo)}:{_fmt_reg(hi)}"


def _fmt_signed7(v):
    return (0x80 - v) if (v & 0x80) else v


def _parse_sir_or_imm5(b, idx, arg):
    sel = (arg >> 5) & 0x03
    if sel == 0x03:
        imm = _read_u8(b, idx)
        if imm is None:
            return "??", idx
        return _fmt_reg(imm & 0x1F), idx + 1
    return f"${get_sir_name(sel)}", idx


def _parse_optional_jr(b, idx, arg, pc, inst_len):
    if not (arg & 0x80):
        return "", idx
    skip = 0
    # Internal ROM is word-aligned.
    # Match CPU check_optional_jr(): skip one padding byte when fetch address
    # is word-aligned before reading JR offset.
    if pc is not None and (pc < INT_ROM_WORDS):
        fetch_addr = (pc << 1) + idx
        if (fetch_addr & 1) == 0:
            skip = 1
    off = _read_u8(b, idx + skip)
    if off is None:
        return ", JR ?", idx
    signed = _fmt_signed7(off)
    if pc is None:
        return f", JR {signed:+d}", idx + skip + 1
    pc_after = _advance_fetch_addr(pc, idx + skip + 1)
    target = ((pc_after - 1) + signed) & 0xFFFF
    return f", JR {signed:+d} -> &H{target:04X}", idx + skip + 1


def _fmt_multi_src(arg, ext):
    sec = (arg >> 5) & 0x03
    if sec == 0x03:
        return _fmt_reg(ext & 0x1F)
    return f"${get_sir_name(sec)}"


def decode_basic(b, pc=None):
    op = b[0]
    i = 1

    if 0x00 <= op <= 0x0F:
        arg = _read_u8(b, i)
        if arg is None:
            return "CALC ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        mnem = CALC_NAMES[op & 0x0F]
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"{mnem}  {_fmt_reg(arg)} , {src}{jr}"

    if op in (0x10, 0x90):
        arg = _read_u8(b, i)
        if arg is None:
            return "ST ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "STW" if op == 0x90 else "ST"
        lhs = _fmt_regpair(arg) if op == 0x90 else _fmt_reg(arg)
        return f"{mnem:<4} {lhs} , ({src}){jr}"

    if op in (0x11, 0x91):
        arg = _read_u8(b, i)
        if arg is None:
            return "LD ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "LDW" if op == 0x91 else "LD"
        lhs = _fmt_regpair(arg) if op == 0x91 else _fmt_reg(arg)
        return f"{mnem:<4} {lhs} , ({src}){jr}"

    if op == 0x12:
        arg = _read_u8(b, i)
        if arg is None:
            return "STL ?"
        return f"STL  {_fmt_reg(arg)}"

    if op == 0x13:
        arg = _read_u8(b, i)
        if arg is None:
            return "LDL ?"
        return f"LDL  {_fmt_reg(arg)}"

    if op in (0x14, 0x1C):
        arg = _read_u8(b, i)
        if arg is None:
            return "P/G?? ?"
        sec = (arg >> 5) & 0x03
        cmd = IOCMD_NAMES[sec]
        mnem = f"G{cmd}" if op == 0x1C else f"P{cmd}"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {_fmt_reg(arg)}{jr}"

    if op in (0x15, 0x1D):
        arg = _read_u8(b, i)
        if arg is None:
            return "PSR/GSR ?"
        sir = SREG2_NAMES[(arg >> 5) & 0x03]
        mnem = "GSR" if op == 0x1D else "PSR"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {sir} , {_fmt_reg(arg)}{jr}"

    if op in (0x16, 0x17, 0x1E, 0x1F):
        arg = _read_u8(b, i)
        if arg is None:
            return "STS ?"
        sec = (arg >> 5) & 0x03
        name = STR2_NAMES[sec] if (op & 0x01) else STR1_NAMES[sec]
        mnem = "GST" if op in (0x1E, 0x1F) else "PST"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {name} , {_fmt_reg(arg)}{jr}"

    if op in (0x96, 0x97, 0x9E, 0x9F):
        arg = _read_u8(b, i)
        if arg is None:
            return "PRE/GRE ?"
        sec = (arg >> 5) & 0x03
        ir = IR2_NAMES[sec] if (op & 0x01) else IR1_NAMES[sec]
        mnem = "GRE" if (op & 0x08) else "PRE"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {ir} , {_fmt_regpair(arg)}{jr}"

    if op in (0xD6, 0xD7):
        arg = _read_u8(b, i)
        lo = _read_u8(b, i + 1)
        hi = _read_u8(b, i + 2)
        if arg is None or lo is None or hi is None:
            return "PRE ?"
        sec = (arg >> 5) & 0x03
        ir = IR2_NAMES[sec] if (op & 0x01) else IR1_NAMES[sec]
        addr = lo | (hi << 8)
        return f"PRE  {ir} , &H{addr:04X}"

    if op in (0x18, 0x19, 0x1A, 0x5A, 0x98, 0x99, 0x9A, 0xDA):
        arg = _read_u8(b, i)
        if arg is None:
            return "ROT ?"
        sec = (arg >> 5) & 0x03
        mnem = ROT2_NAMES[sec] if (op & 0x02) else ROT_NAMES[sec]
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {_fmt_reg(arg)}{jr}"

    if op in (0x1B, 0x5B, 0x9B, 0xDB):
        arg = _read_u8(b, i)
        if arg is None:
            return "CMP/INV ?"
        sec = (arg >> 5) & 0x03
        mnem = CMPINV_NAMES[sec]
        if op in (0xDB,):
            ext = _read_u8(b, i + 1)
            if ext is None:
                return f"{mnem} {_fmt_reg(arg)} , ?"
            return f"{mnem} {_fmt_reg(arg)} , {((ext >> 5) & 0x07) + 1}"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {_fmt_reg(arg)}{jr}"

    if op in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D):
        arg = _read_u8(b, i)
        if arg is None:
            return "XFER ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        sign = "-" if (arg & 0x80) else "+"
        base = "IX" if ((op & 0x01) == 0) else "IZ"
        post = "+" if (op & 0x02) else ""
        group = (op & 0x0E) >> 1
        if group == 0:
            mnem = "ST"
        elif group == 1:
            mnem = "STI"
        elif group == 2:
            mnem = "STD"
        elif group == 4:
            mnem = "LD"
        elif group == 5:
            mnem = "LDI"
        elif group == 6:
            mnem = "LDD"
        else:
            return f"DB   &H{op:02X}"
        if mnem.startswith("LD"):
            return f"{mnem:<4} {_fmt_reg(arg)} , ({base}{sign}{src}){post}"
        return f"{mnem:<4} {_fmt_reg(arg)} , ({base}{sign}{src}){post}"

    if op == 0x26:
        arg = _read_u8(b, i)
        if arg is None:
            return "PUSH ?"
        return f"PUSH {_fmt_reg(arg)}"

    if op == 0x27:
        arg = _read_u8(b, i)
        if arg is None:
            return "PHU ?"
        return f"PHU  {_fmt_reg(arg)}"

    if op == 0x66:
        arg = _read_u8(b, i)
        dmy = _read_u8(b, i + 1)
        if arg is None or dmy is None:
            return "PHS ?"
        return f"PHS  {_fmt_reg(arg)} , DB &H{dmy:02X}"

    if op == 0x67:
        arg = _read_u8(b, i)
        dmy = _read_u8(b, i + 1)
        if arg is None or dmy is None:
            return "PHU ?"
        return f"PHU  {_fmt_reg(arg)} , DB &H{dmy:02X}"

    if op == 0x2E:
        arg = _read_u8(b, i)
        if arg is None:
            return "POP ?"
        return f"POP  {_fmt_reg(arg)}"

    if op == 0x2F:
        arg = _read_u8(b, i)
        if arg is None:
            return "PPU ?"
        return f"PPU  {_fmt_reg(arg)}"

    if op == 0x6E:
        arg = _read_u8(b, i)
        dmy = _read_u8(b, i + 1)
        if arg is None or dmy is None:
            return "PPS ?"
        return f"PPS  {_fmt_reg(arg)} , DB &H{dmy:02X}"

    if op == 0x6F:
        arg = _read_u8(b, i)
        dmy = _read_u8(b, i + 1)
        if arg is None or dmy is None:
            return "PPU ?"
        return f"PPU  {_fmt_reg(arg)} , DB &H{dmy:02X}"

    if op in (0xA6, 0xA7, 0xAE, 0xAF):
        arg = _read_u8(b, i)
        if arg is None:
            return "P??W ?"
        mnem = "PHSW" if op == 0xA6 else "PHUW" if op == 0xA7 else "PPSW" if op == 0xAE else "PPUW"
        return f"{mnem} {_fmt_regpair(arg)}"

    if op in (0xE6, 0xE7, 0xEE, 0xEF):
        arg = _read_u8(b, i)
        ext = _read_u8(b, i + 1)
        if arg is None or ext is None:
            return "P??M ?"
        cnt = ((ext >> 5) & 0x07) + 1
        mnem = "PHSM" if op == 0xE6 else "PHUM" if op == 0xE7 else "PPSM" if op == 0xEE else "PPUM"
        return f"{mnem} {_fmt_reg(arg)} , {cnt}"

    if 0x30 <= op <= 0x37:
        lo = _read_u8(b, i)
        hi = _read_u8(b, i + 1)
        if lo is None or hi is None:
            return "JP ?"
        addr = lo | (hi << 8)
        cond = op & 0x07
        if cond == 7:
            return f"JP   &H{addr:04X}"
        return f"JP   {COND_NAMES[cond]} , &H{addr:04X}"

    if 0x38 <= op <= 0x3F:
        arg = _read_u8(b, i)
        if arg is None:
            return "ALU-MEM ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        base = "IZ" if (op & 0x01) else "IX"
        sign = "-" if (arg & 0x80) else "+"
        mnem = CALC_MEM_NAMES[(op & 0x06) >> 1]
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"{mnem}  {_fmt_reg(arg)} , ({base}{sign}{src}){jr}"

    if 0x70 <= op <= 0x77:
        lo = _read_u8(b, i)
        hi = _read_u8(b, i + 1)
        if lo is None or hi is None:
            return "CAL ?"
        addr = lo | (hi << 8)
        cond = op & 0x07
        if cond == 7:
            return f"CAL  &H{addr:04X}"
        return f"CAL  {COND_NAMES[cond]} , &H{addr:04X}"

    if 0x78 <= op <= 0x7F:
        arg = _read_u8(b, i)
        disp = _read_u8(b, i + 1)
        if arg is None or disp is None:
            return "ALU-MEMI ?"
        base = "IZ" if (op & 0x01) else "IX"
        sign = "-" if (arg & 0x80) else "+"
        mnem = CALC_MEM_NAMES[(op & 0x06) >> 1]
        return f"{mnem}  {_fmt_reg(arg)} , ({base}{sign}{disp})"

    if 0x40 <= op <= 0x4F:
        arg = _read_u8(b, i)
        imm = _read_u8(b, i + 1)
        if arg is None or imm is None:
            return "IMM ?"
        mnem = CALC_NAMES[op & 0x0F]
        jr, _ = _parse_optional_jr(b, i + 2, arg, pc, i + 3)
        return f"{mnem}  {_fmt_reg(arg)} , #&H{imm:02X}{jr}"

    if op in (0x50, 0x51, 0xD0, 0xD1):
        arg = _read_u8(b, i)
        lo = _read_u8(b, i + 1)
        if arg is None or lo is None:
            return "IMM-MEM ?"
        sec = (arg >> 5) & 0x03
        src = _fmt_reg(arg) if sec == 0x03 else f"${get_sir_name(sec)}"
        if op == 0x50:
            return f"ST   #&H{lo:02X} , ({src})"
        if op == 0x51:
            return f"ST   #&H{lo:02X} , {src}"
        hi = _read_u8(b, i + 2)
        if hi is None:
            return "IM16 ?"
        imm16 = lo | (hi << 8)
        if op == 0xD0:
            return f"STW  #&H{imm16:04X} , ({src})"
        return f"LDW  {_fmt_regpair(arg)} , #&H{imm16:04X}"

    if op in (0x56, 0x57):
        arg = _read_u8(b, i)
        imm = _read_u8(b, i + 1)
        if arg is None or imm is None:
            return "PST ?"
        sec = (arg >> 5) & 0x03
        name = STR2_NAMES[sec] if (op & 0x01) else STR1_NAMES[sec]
        return f"PST  {name} , #&H{imm:02X}"

    if op == 0x54:
        arg = _read_u8(b, i)
        imm = _read_u8(b, i + 1)
        if arg is None or imm is None:
            return "IMM-SR ?"
        sec = (arg >> 5) & 0x03
        return f"P{IOCMD_NAMES[sec]} #&H{imm:02X}"

    if op == 0x55:
        arg = _read_u8(b, i)
        if arg is None:
            return "IMM-SR ?"
        sec = (arg >> 5) & 0x03
        return f"PSR {SREG2_NAMES[sec]} , #&H{arg & 0x1F:02X}"

    if op == 0x52:
        imm = _read_u8(b, i)
        if imm is None:
            return "STL ?"
        return f"STL  #&H{imm:02X}"

    if op == 0x53:
        return "DB   &H53"

    if op in (0x92, 0x93):
        arg = _read_u8(b, i)
        if arg is None:
            return "STLW/LDLW ?"
        mnem = "LDLW" if (op & 0x01) else "STLW"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {_fmt_regpair(arg)}{jr}"

    if op in (0xD2, 0xD3):
        arg = _read_u8(b, i)
        ext = _read_u8(b, i + 1)
        if arg is None or ext is None:
            return "STLM/LDLM ?"
        mnem = "LDLM" if (op & 0x01) else "STLM"
        return f"{mnem} {_fmt_regpair(arg)} , {((ext >> 5) & 0x07) + 1}"

    if op in (0x94, 0x9C, 0xD4):
        arg = _read_u8(b, i)
        if arg is None:
            return "P/GFL ?"
        sec = (arg >> 5) & 0x03
        cmd = IOCMD_NAMES[sec]
        mnem = f"G{cmd}" if (op & 0x08) else f"P{cmd}"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        if op == 0xD4:
            ext = _read_u8(b, i + 1)
            if ext is not None:
                return f"{mnem} {_fmt_regpair(arg)} , {((ext >> 5) & 0x07) + 1}"
        return f"{mnem} {_fmt_regpair(arg)}{jr}"

    if op in (0x95, 0x9D, 0xD5):
        arg = _read_u8(b, i)
        if arg is None:
            return "P/GSR ?"
        sec = (arg >> 5) & 0x03
        sir = SREG2_NAMES[sec]
        mnem = "GSR" if (op & 0x08) else "PSR"
        if op == 0xD5:
            ext = _read_u8(b, i + 1)
            if ext is None:
                return f"{mnem} {sir} , {_fmt_regpair(arg)}"
            return f"{mnem} {sir} , {_fmt_regpair(arg)} , {((ext >> 5) & 0x07) + 1}"
        jr, _ = _parse_optional_jr(b, i + 1, arg, pc, i + 2)
        return f"{mnem} {sir} , {_fmt_regpair(arg)}{jr}"

    if op in (0x58, 0x59, 0x5C, 0x5D, 0xD8, 0xD9, 0xDC, 0xDD):
        if op in (0xD8, 0xD9):
            return "SUP" if op == 0xD8 else "SDN"
        arg = _read_u8(b, i)
        if arg is None:
            return "UP/DN ?"
        if op == 0x58:
            return f"BUPS {_fmt_reg(arg)}"
        if op == 0x59:
            return f"BDNS {_fmt_reg(arg)}"
        if op == 0x5C:
            return f"BUP  {_fmt_reg(arg)}"
        if op == 0x5D:
            return f"BDN  {_fmt_reg(arg)}"
        if op == 0xDC:
            return f"SUP  {_fmt_reg(arg)}"
        return f"SDN  {_fmt_reg(arg)}"

    if op in (0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D):
        arg = _read_u8(b, i)
        off = _read_u8(b, i + 1)
        if arg is None or off is None:
            return "XFERI ?"
        signed = _fmt_signed7(off) if (arg & 0x80) else off
        base = "IX" if ((op & 0x01) == 0) else "IZ"
        post = "+" if (op & 0x02) else ""
        group = (op & 0x0E) >> 1
        if group == 0:
            mnem = "ST"
        elif group == 1:
            mnem = "STI"
        elif group == 2:
            mnem = "STD"
        elif group == 4:
            mnem = "LD"
        elif group == 5:
            mnem = "LDI"
        elif group == 6:
            mnem = "LDD"
        else:
            return f"DB   &H{op:02X}"
        return f"{mnem:<4} {_fmt_reg(arg)} , ({base}{signed:+d}){post}"

    if op in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD):
        arg = _read_u8(b, i)
        if arg is None:
            return "XFERW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        base = "IX" if ((op & 0x01) == 0) else "IZ"
        sign = "-" if (arg & 0x80) else "+"
        post = "+" if (op & 0x02) else ""
        group = (op & 0x0E) >> 1
        if group == 0:
            mnem = "STW"
        elif group == 1:
            mnem = "STIW"
        elif group == 2:
            mnem = "STDW"
        elif group == 4:
            mnem = "LDW"
        elif group == 5:
            mnem = "LDIW"
        elif group == 6:
            mnem = "LDDW"
        else:
            return f"DB   &H{op:02X}"
        return f"{mnem:<4} {_fmt_regpair(arg)} , ({base}{sign}{src}){post}"

    if op in (0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED):
        arg = _read_u8(b, i)
        ext = _read_u8(b, i + 1)
        if arg is None or ext is None:
            return "XFERM ?"
        sec = (arg >> 5) & 0x03
        if sec == 0x03:
            src = _fmt_reg(ext)
        else:
            src = f"${get_sir_name(sec)}"
        count = ((ext >> 5) & 0x07) + 1
        base = "IX" if ((op & 0x01) == 0) else "IZ"
        sign = "-" if (arg & 0x80) else "+"
        post = "+" if (op & 0x02) else ""
        group = (op & 0x0E) >> 1
        if group == 0:
            mnem = "STM"
        elif group == 1:
            mnem = "STIM"
        elif group == 2:
            mnem = "STDM"
        elif group == 4:
            mnem = "LDM"
        elif group == 5:
            mnem = "LDIM"
        elif group == 6:
            mnem = "LDDM"
        else:
            return f"DB   &H{op:02X}"
        return f"{mnem:<4} {_fmt_reg(arg)} , ({base}{sign}{src}){post} , {count}"

    if op in (0x80, 0x81):
        arg = _read_u8(b, i)
        if arg is None:
            return "ADCW/SBCW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "SBCW" if (op & 0x01) else "ADCW"
        return f"{mnem} {_fmt_regpair(arg)} , {src}{jr}"

    if op in (0x88, 0x89):
        arg = _read_u8(b, i)
        if arg is None:
            return "ADW/SBW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "SBW" if (op & 0x01) else "ADW"
        return f"{mnem} {_fmt_regpair(arg)} , {src}{jr}"

    if op == 0x82:
        arg = _read_u8(b, i)
        if arg is None:
            return "LDW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"LDW  {_fmt_regpair(arg)} , {src}{jr}"

    if op == 0x83:
        arg = _read_u8(b, i)
        if arg is None:
            return "LDCW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"LDCW {_fmt_regpair(arg)} , {src}{jr}"

    if op in (0x84, 0x85, 0x86, 0x87):
        arg = _read_u8(b, i)
        if arg is None:
            return "LOGCW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = ["ANCW", "NACW", "ORCW", "XRCW"][op & 0x03]
        return f"{mnem} {_fmt_regpair(arg)} , {src}{jr}"

    if op in (0x8A, 0x8B):
        arg = _read_u8(b, i)
        if arg is None:
            return "ADBW/SBBW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "SBBW" if (op & 0x01) else "ADBW"
        return f"{mnem} {_fmt_regpair(arg)} , {src}{jr}"

    if op in (0x8C, 0x8D, 0x8E, 0x8F):
        arg = _read_u8(b, i)
        if arg is None:
            return "LOGW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = ["ANW", "NAW", "ORW", "XRW"][op & 0x03]
        return f"{mnem} {_fmt_regpair(arg)} , {src}{jr}"

    if 0xB0 <= op <= 0xB7:
        off = _read_u8(b, i)
        if off is None:
            return "JR ?"
        signed = _fmt_signed7(off)
        cond = op & 0x07
        target = None
        if pc is not None:
            pc_after = _advance_fetch_addr(pc, 2)
            target = ((pc_after - 1) + signed) & 0xFFFF
        if cond == 7:
            if target is None:
                return f"JR   {signed:+d}"
            return f"JR   {signed:+d} -> &H{target:04X}"
        if target is None:
            return f"JR   {COND_NAMES[cond]} , {signed:+d}"
        return f"JR   {COND_NAMES[cond]} , {signed:+d} -> &H{target:04X}"

    if 0xB8 <= op <= 0xBF:
        arg = _read_u8(b, i)
        if arg is None:
            return "ALU-MEMW ?"
        i += 1
        src, i = _parse_sir_or_imm5(b, i, arg)
        base = "IZ" if (op & 0x01) else "IX"
        sign = "-" if (arg & 0x80) else "+"
        mnem = ["ADCW", "SBCW", "ADW", "SBW"][(op & 0x06) >> 1]
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"{mnem} ({base}{sign}{src}) , {_fmt_regpair(arg)}{jr}"

    if op in (0xC0, 0xC1, 0xC8, 0xC9):
        arg = _read_u8(b, i)
        if arg is None:
            return "ADBCM ?"
        i += 1
        ext = _read_u8(b, i)
        if ext is None:
            return "ADBCM ?"
        i += 1
        cnt = ((ext >> 5) & 0x07) + 1
        src = _fmt_multi_src(arg, ext)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        if op < 0xC8:
            mnem = "SBBCM" if (op & 0x01) else "ADBCM"
        else:
            mnem = "SBBM" if (op & 0x01) else "ADBM"
        return f"{mnem} {_fmt_reg(arg)} , {src} , {cnt}{jr}"

    if op == 0xC2:
        arg = _read_u8(b, i)
        if arg is None:
            return "LDM ?"
        i += 1
        ext = _read_u8(b, i)
        if ext is None:
            return "LDM ?"
        i += 1
        cnt = ((ext >> 5) & 0x07) + 1
        src = _fmt_multi_src(arg, ext)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"LDM  {_fmt_reg(arg)} , {src} , {cnt}{jr}"

    if op == 0xC3:
        arg = _read_u8(b, i)
        if arg is None:
            return "LDCM ?"
        i += 1
        ext = _read_u8(b, i)
        if ext is None:
            return "LDCM ?"
        i += 1
        cnt = ((ext >> 5) & 0x07) + 1
        src = _fmt_multi_src(arg, ext)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        return f"LDCM {_fmt_reg(arg)} , {src} , {cnt}{jr}"

    if op in (0xC4, 0xC5, 0xC6, 0xC7, 0xCC, 0xCD, 0xCE, 0xCF):
        arg = _read_u8(b, i)
        if arg is None:
            return "MULTI ?"
        i += 1
        ext = _read_u8(b, i)
        if ext is None:
            return "MULTI ?"
        i += 1
        cnt = ((ext >> 5) & 0x07) + 1
        src = _fmt_multi_src(arg, ext)
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        if op < 0xCC:
            mnem = ["ANCM", "NACM", "ORCM", "XRCM"][op & 0x03]
        else:
            mnem = ["ANM", "NAM", "ORM", "XRM"][op & 0x03]
        return f"{mnem} {_fmt_reg(arg)} , {src} , {cnt}{jr}"

    if op in (0xCA, 0xCB):
        arg = _read_u8(b, i)
        if arg is None:
            return "ADBM ?"
        i += 1
        ext = _read_u8(b, i)
        if ext is None:
            return "ADBM ?"
        i += 1
        cnt = ((ext >> 5) & 0x07) + 1
        imm = ext & 0x1F
        jr, _ = _parse_optional_jr(b, i, arg, pc, i + 1)
        mnem = "SBBM" if (op & 0x01) else "ADBM"
        return f"{mnem} {_fmt_reg(arg)} , #&H{imm:02X} , {cnt}{jr}"

    if op == 0xDE:
        arg = _read_u8(b, i)
        if arg is None:
            return "JPW ?"
        return f"JPW  {_fmt_regpair(arg)}"

    if op == 0xDF:
        arg = _read_u8(b, i)
        if arg is None:
            return "JPW (?)"
        return f"JPW  ({_fmt_regpair(arg)})"

    if 0xF0 <= op <= 0xF7:
        cond = op & 0x07
        if cond == 7:
            return "RTN"
        return f"RTN  {COND_NAMES[cond]}"

    if 0xF8 <= op <= 0xFF:
        if op == 0xFF:
            return "TRP &H22"
        return SPCMD_NAMES[op & 0x07]

    return f"DB   &H{op:02X}"


def step_debug():
    pc = hd61700.get_pc()
    op_bytes = hd61700.step()
    if not op_bytes:
        return

    hex_str = "".join(f"{x:02X}" for x in op_bytes)
    try:
        mnemonic = decode_basic(op_bytes, pc)
    except Exception as e:
        mnemonic = f"Parse Error: {e}"
        import sys
        sys.print_exception(e)

    print(f"[{pc:04X}] {hex_str:<10} | {mnemonic}")
