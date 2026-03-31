"""HD61700 CPU Test: opcodes 0x60-0x7F
Indexed ST/LD IX/IZ (imm offset), 3-byte PHS/PHU/PPS/PPU,
CAL conditional, IX/IZ arith (imm offset)
"""
from test_cpu_common import *
import hd61700

def tests_60_65(t, check):
    """0x60-0x65: ST/STI/STD IX/IZ (imm offset)"""
    # --- 0x60 ST $,(IX+IM8) restore ---
    def s():
        hd61700.set_reg(0,0x55)
        hd61700.set_reg16(0,0x6500)
    check("60 ST (IX+5) restore", [0x60,0x00,5], s,
          [cm(0x6505,0x55), cr16(0,0x6500)])
    # --- 0x61 ST $,(IZ+IM8) restore ---
    def s():
        hd61700.set_reg(0,0x66)
        hd61700.set_reg16(2,0x6600)
    check("61 ST (IZ+3) restore", [0x61,0x00,3], s,
          [cm(0x6603,0x66), cr16(2,0x6600)])
    # --- 0x62 STI $,(IX+IM8)+ no restore ---
    def s():
        hd61700.set_reg(0,0x77)
        hd61700.set_reg16(0,0x6500)
    check("62 STI (IX+2) inc", [0x62,0x00,2], s,
          [cm(0x6502,0x77), cr16(0,0x6503)])
    # --- 0x63 STI $,(IZ+IM8)+ ---
    def s():
        hd61700.set_reg(0,0xCC)
        hd61700.set_reg16(2,0x7000)
    check("63 STI IZ-20", [0x63,0x80,0x14], s,
          [cr16(2,0x6FED), cm(0x6FEC,0xCC)])
    # --- 0x64 STD $,(IX+IM8) ---
    def s():
        hd61700.set_reg(0,0x88)
        hd61700.set_reg16(0,0x6500)
    check("64 STD (IX+10)", [0x64,0x00,10], s,
          [cm(0x650A,0x88), cr16(0,0x650A)])
    # --- 0x65 STD $,(IZ+IM8) ---
    def s():
        hd61700.set_reg(0,0xAA)
        hd61700.set_reg16(2,0x7000)
    check("65 STD IZ-20", [0x65,0x80,0x14], s,
          [cr16(2,0x6FEC), cm(0x6FEC,0xAA)])


def tests_66_6f(t, check):
    """0x66-0x6F: 3-byte PHS/PHU, LD/LDI/LDD IX/IZ (imm), PPS/PPU 3-byte"""
    # --- 0x66 PHS 3-byte ---
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg16(4,0x7F00)
    check("66 PHS 3byte", [0x66,0x00,0x00], s, [cm(0x7EFF,0xAB)])
    # --- 0x66 PHS$ no jump extension with arg bit7 ---
    def s():
        hd61700.set_reg(1,0xAC); hd61700.set_reg16(4,0x7F00)
    check("66 PHS$ no jump", [0x66,0x81,0x00], s, [cm(0x7EFF,0xAC), cp(0x7003)])
    # --- 0x67 PHU 3-byte ---
    def s():
        hd61700.set_reg(0,0xCD); hd61700.set_reg16(3,0x7E00)
    check("67 PHU 3byte", [0x67,0x00,0x00], s, [cm(0x7DFF,0xCD)])
    # --- 0x67 PHU$ no jump extension with arg bit7 ---
    def s():
        hd61700.set_reg(1,0xCE); hd61700.set_reg16(3,0x7E00)
    check("67 PHU$ no jump", [0x67,0x81,0x00], s, [cm(0x7DFF,0xCE), cp(0x7003)])
    # --- 0x68 LD $,(IX+IM8) restore ---
    def s():
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x6505,0xAA)
    check("68 LD (IX+5) restore", [0x68,0x00,5], s,
          [cr(0,0xAA), cr16(0,0x6500)])
    # --- 0x69 LD $,(IZ+IM8) restore ---
    def s():
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6603,0xBB)
    check("69 LD (IZ+3) restore", [0x69,0x00,3], s,
          [cr(0,0xBB), cr16(2,0x6600)])
    # --- 0x6A LDI $,(IX+IM8)+ ---
    def s():
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x6502,0xCC)
    check("6A LDI (IX+2) inc", [0x6A,0x00,2], s,
          [cr(0,0xCC), cr16(0,0x6503)])
    # --- 0x6B LDI $,(IZ+IM8)+ ---
    def s():
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6604,0xDD)
    check("6B LDI (IZ+4) inc", [0x6B,0x00,4], s,
          [cr(0,0xDD), cr16(2,0x6605)])
    # --- 0x6C LDD $,(IX+IM8) no inc ---
    def s():
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x6508,0xEE)
    check("6C LDD (IX+8)", [0x6C,0x00,8], s,
          [cr(0,0xEE), cr16(0,0x6508)])
    # --- 0x6D LDD $,(IZ+IM8) ---
    def s():
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6606,0xFF)
    check("6D LDD (IZ+6)", [0x6D,0x00,6], s,
          [cr(0,0xFF), cr16(2,0x6606)])
    # --- 0x6E PPS 3-byte ---
    def s():
        hd61700.set_reg16(4,0x7EFF)
        hd61700.write_mem(0x7EFF,0x12)
    check("6E PPS 3byte", [0x6E,0x00,0x00], s, [cr(0,0x12)])
    # --- 0x6F PPU 3-byte ---
    def s():
        hd61700.set_reg16(3,0x7DFF)
        hd61700.write_mem(0x7DFF,0x34)
    check("6F PPU 3byte", [0x6F,0x00,0x00], s, [cr(0,0x34)])


def tests_70_77(t, check):
    """0x70-0x77: CAL conditional"""
    tgt = 0x7080
    ret_addr = TB + 3  # CAL is always 3 bytes
    # --- 0x77 CAL uncond ---
    def s():
        hd61700.set_reg16(4,0x7F00)
        t.lc(tgt,[0xF8])
    check("77 CAL uncond", [0x77,tgt&0xFF,tgt>>8], s,
          [cp(tgt+1)], stop=tgt+1)
    # --- 0x70 CAL Z (skip) ---
    def s():
        hd61700.set_reg16(4,0x7F00); t.lc(tgt,[0xF8])
    check("70 CAL Z skip", [0x70,tgt&0xFF,tgt>>8], s, [cp(TB+3)])
    # --- 0x70 CAL Z (taken) ---
    def s():
        hd61700.set_reg16(4,0x7F00); hd61700.set_flags(0x80)
        t.lc(tgt,[0xF8])
    check("70 CAL Z taken", [0x70,tgt&0xFF,tgt>>8], s,
          [cp(tgt+1)], stop=tgt+1)
    # --- 0x74 CAL NZ ---
    def s():
        hd61700.set_reg16(4,0x7F00); t.lc(tgt,[0xF8])
    check("74 CAL NZ taken", [0x74,tgt&0xFF,tgt>>8], s,
          [cp(tgt+1)], stop=tgt+1)
    # --- 0x71 CAL NC ---
    def s():
        hd61700.set_reg16(4,0x7F00); t.lc(tgt,[0xF8])
    check("71 CAL NC taken", [0x71,tgt&0xFF,tgt>>8], s,
          [cp(tgt+1)], stop=tgt+1)
    # --- 0x75 CAL C (skip, no carry) ---
    def s():
        hd61700.set_reg16(4,0x7F00); t.lc(tgt,[0xF8])
    check("75 CAL C skip", [0x75,tgt&0xFF,tgt>>8], s, [cp(TB+3)])
    # CAL+RTN combined
    def s2():
        hd61700.set_reg16(4,0x7F00)
        t.lc(TB, [0x77,tgt&0xFF,tgt>>8])
        t.lc(tgt, [0xF7])        # RTN uncond
        t.lc(TB+3, [0xF8])       # NOP after return
    t.ex([0xF8], s2, stop=TB+4)
    ok = PC()==TB+4
    print(f"  {'77+F7 CAL+RTN':40} {'OK' if ok else 'NG'}")


def tests_78_7f(t, check):
    """0x78-0x7F: ADC/SBC/AD/SB (IX/IZ+IM8)"""
    # --- 0x78 ADC (IX+IM8),$ ---
    def s():
        hd61700.set_reg(2,0x11)
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x650A,0x22)
    # ADC is check-only, memory remains unchanged
    check("78 ADC (IX+10),R2", [0x78,0x02,10], s,
          [cm(0x650A,0x22), cf(c=False)])
    # --- 0x79 ADC (IZ+IM8),$ ---
    def s():
        hd61700.set_reg(2,0x10)
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6605,0x20)
    # ADC is check-only
    check("79 ADC (IZ+5),R2", [0x79,0x02,5], s,
          [cm(0x6605,0x20), cf(c=False)])
    # --- 0x7A SBC (IX+IM8),$ ---
    def s():
        hd61700.set_reg(2,0x10)
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x6505,0x30)
    check("7A SBC (IX+5),R2", [0x7A,0x02,5], s,
          [cf(c=False)])
    # --- 0x7B SBC (IZ+IM8),$ ---
    def s():
        hd61700.set_reg(2,0x50)
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6603,0x30)
    check("7B SBC (IZ+3),R2 borrow", [0x7B,0x02,3], s,
          [cf(c=True)])
    # --- 0x7C AD (IX+IM8),$ write-back ---
    def s():
        hd61700.set_reg(2,0x01)
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x650A,0xFE)
    check("7C AD (IX+10),R2 wb", [0x7C,0x02,10], s,
          [cm(0x650A,0xFF), cf(c=False)])
    # --- 0x7D AD (IZ+IM8),$ write-back ---
    def s():
        hd61700.set_reg(2,0x01)
        hd61700.set_reg16(2,0x6600)
        hd61700.write_mem(0x6605,0xFF)
    check("7D AD (IZ+5),R2 wb C", [0x7D,0x02,5], s,
          [cm(0x6605,0x00), cf(c=True,z=True)])
    # --- 0x7E SB (IX+IM8),$ ---
    def s():
        hd61700.set_reg(2,0x01)
        hd61700.set_reg16(0,0x6500)
        hd61700.write_mem(0x6505,0x01)
    check("7E SB (IX+5),R2 wb Z", [0x7E,0x02,5], s,
          [cm(0x6505,0x00), cf(c=False,z=True)])
    # --- 0x7F SB (IZ-5),$ ---
    def s():
        hd61700.set_reg(3,0x01)
        hd61700.set_reg16(2,0x6500)
        hd61700.write_mem(0x64FB,0x01)
    check("7F SB (IZ-5),R3 wb Z", [0x7F,0x83,5], s,
          [cm(0x64FB,0x00), cf(c=False,z=True)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0x60-0x7F")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0x60-0x65", tests_60_65), ("0x66-0x6F", tests_66_6f),
                     ("0x70-0x77", tests_70_77), ("0x78-0x7F", tests_78_7f)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
