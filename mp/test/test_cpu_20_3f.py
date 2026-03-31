"""HD61700 CPU Test: opcodes 0x20-0x3F
Indexed ST/LD IX/IZ (reg offset), PHS/PHU/PPS/PPU, JP cond, IX/IZ arith
"""
from test_cpu_common import *
import hd61700

def tests_20_27(t, check):
    """0x20-0x27: ST/STI/STD IX/IZ, PHS/PHU"""
    # --- 0x20 ST $,(IX+$) ---
    def s():
        hd61700.set_reg(0,0x55); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
    check("20 ST (IX+),R0 restore", [0x20,0x20], s,
          [cm(0x6500,0x55), cr16(0,0x6500)])
    # --- 0x21 ST $,(IZ+$) ---
    def s():
        hd61700.set_reg(0,0x66); hd61700.set_reg(1,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(1,1)
    check("21 ST (IZ+),R0 restore", [0x21,0x20], s,
          [cm(0x6600,0x66), cr16(2,0x6600)])
    # --- 0x22 STI $,(IX+$)+ (no restore) ---
    def s():
        hd61700.set_reg(0,0x77); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
    check("22 STI (IX+),R0 inc", [0x22,0x20], s,
          [cm(0x6500,0x77), cr16(0,0x6501)])
    # --- 0x23 STI $,(IZ+$)+ ---
    def s():
        hd61700.set_reg(0,0x88); hd61700.set_reg(1,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(1,1)
    check("23 STI (IZ+),R0 inc", [0x23,0x20], s,
          [cm(0x6600,0x88), cr16(2,0x6601)])
    # --- 0x24 STD $,(IX+$) (no restore, no inc) ---
    def s():
        hd61700.set_reg(0,0x99); hd61700.set_reg(1,2)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
    check("24 STD (IX+2)", [0x24,0x20], s,
          [cm(0x6502,0x99), cr16(0,0x6502)])
    # --- 0x25 STD $,(IZ+$) ---
    def s():
        hd61700.set_reg(0,0xAA); hd61700.set_reg(1,3)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(1,1)
    check("25 STD (IZ+3)", [0x25,0x20], s,
          [cm(0x6603,0xAA), cr16(2,0x6603)])
    # --- 0x26 PHS (push to SS) ---
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg16(4,0x7F00)
    check("26 PHS", [0x26,0x00], s,
          [cm(0x7EFF,0xAB)])
    # --- 0x27 PHU (push to US) ---
    def s():
        hd61700.set_reg(0,0xCD); hd61700.set_reg16(3,0x7E00)
    check("27 PHU", [0x27,0x00], s,
          [cm(0x7DFF,0xCD)])


def tests_28_2f(t, check):
    """0x28-0x2F: LD/LDI/LDD IX/IZ, PPS/PPU"""
    # --- 0x28 LD $,(IX+$) restore ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6500,0xAA)
    check("28 LD R0,(IX+) restore", [0x28,0x20], s, [cr(0,0xAA), cr16(0,0x6500)])
    # --- 0x29 LD $,(IZ+$) restore ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6600,0xBB)
    check("29 LD R0,(IZ+) restore", [0x29,0x20], s, [cr(0,0xBB), cr16(2,0x6600)])
    # --- 0x2A LDI $,(IX+$)+ ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6500,0xBB)
    check("2A LDI R0,(IX+) inc", [0x2A,0x20], s, [cr(0,0xBB), cr16(0,0x6501)])
    # --- 0x2B LDI $,(IZ+$)+ ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg16(2,0x6500); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6500,0xCC)
    check("2B LDI R0,(IZ+) inc", [0x2B,0x20], s, [cr(0,0xCC), cr16(2,0x6501)])
    # --- 0x2C LDD $,(IX+$) no inc ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,2)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6502,0xDD)
    check("2C LDD R0,(IX+2)", [0x2C,0x20], s, [cr(0,0xDD), cr16(0,0x6502)])
    # --- 0x2D LDD $,(IZ+$) ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,3)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6603,0xEE)
    check("2D LDD R0,(IZ+3)", [0x2D,0x20], s, [cr(0,0xEE), cr16(2,0x6603)])
    # --- 0x2E PPS (pop from SS) ---
    def s():
        hd61700.set_reg16(4,0x7EFF)
        hd61700.write_mem(0x7EFF,0xAB)
    check("2E PPS", [0x2E,0x00], s, [cr(0,0xAB)])
    # --- 0x2F PPU (pop from US) ---
    def s():
        hd61700.set_reg16(3,0x7DFF)
        hd61700.write_mem(0x7DFF,0xCD)
    check("2F PPU", [0x2F,0x00], s, [cr(0,0xCD)])
    # PHS + PPS round-trip
    def s():
        hd61700.set_reg(0,0xEF); hd61700.set_reg16(4,0x7F00)
    check("26+2E PHS/PPS round", [0x26,0x00,0x2E,0x00], s, [cr(0,0xEF)])


def tests_30_37(t, check):
    """0x30-0x37: JP conditional and unconditional"""
    tgt = 0x7080
    # --- 0x37 JP unconditional ---
    def s(): t.lc(tgt, [0xF8])
    check("37 JP uncond", [0x37,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x30 JP Z (skip if NZ) ---
    def s(): hd61700.set_reg(0,1); t.lc(tgt,[0xF8])
    check("30 JP Z skip(NZ)", [0x30,tgt&0xFF,tgt>>8], s, [cp(TB+3)])
    # --- 0x30 JP Z (taken if Z) ---
    def s(): hd61700.set_flags(0x80); t.lc(tgt,[0xF8])
    check("30 JP Z taken", [0x30,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x31 JP NC ---
    def s(): hd61700.set_flags(0); t.lc(tgt,[0xF8])
    check("31 JP NC taken(noC)", [0x31,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    def s(): hd61700.set_flags(0x40); t.lc(tgt,[0xF8])
    check("31 JP NC skip(C)", [0x31,tgt&0xFF,tgt>>8], s, [cp(TB+3)])
    # --- 0x32 JP LZ ---
    def s(): hd61700.set_flags(0x20); t.lc(tgt,[0xF8])
    check("32 JP LZ taken", [0x32,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x33 JP UZ ---
    def s(): hd61700.set_flags(0x10); t.lc(tgt,[0xF8])
    check("33 JP UZ taken", [0x33,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x34 JP NZ ---
    def s(): hd61700.set_reg(0,1); t.lc(tgt,[0xF8])
    check("34 JP NZ taken", [0x34,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x35 JP C ---
    def s(): hd61700.set_flags(0x40); t.lc(tgt,[0xF8])
    check("35 JP C taken", [0x35,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0x36 JP NLZ ---
    def s(): hd61700.set_flags(0); t.lc(tgt,[0xF8])
    check("36 JP NLZ taken(LZ=0)", [0x36,tgt&0xFF,tgt>>8], s, [cp(tgt+1)], stop=tgt+1)


def tests_38_3f(t, check):
    """0x38-0x3F: ADC/SBC/AD/SB (IX/IZ+reg)"""
    # --- 0x38 ADC (IX+$),$  (check only) ---
    def s():
        hd61700.set_reg(2,0x05)
        hd61700.set_reg(4,0x11)
        hd61700.set_reg16(0,0x6500)
        hd61700.set_sreg(0,2)  # SX = R2
        hd61700.write_mem(0x6505,0x22)
    check("38 ADC (IX+R2),R4 chk", [0x38,0x04], s,
          [cm(0x6505,0x22), cf(c=False)])
    # --- 0x39 ADC (IZ+$),$ ---
    def s():
        hd61700.set_reg(2,0x03)
        hd61700.set_reg(5,0x10)
        hd61700.set_reg16(2,0x6500)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6503,0x20)
    check("39 ADC (IZ+R2),R5", [0x39,0x05], s,
          [cm(0x6503,0x20), cf(c=False)])
    # --- 0x3A SBC (IX+$),$ ---
    def s():
        hd61700.set_reg(2,0x00)
        hd61700.set_reg(4,0x10)
        hd61700.set_reg16(0,0x6500)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6500,0x20)
    check("3A SBC (IX+R2),R4", [0x3A,0x04], s,
          [cf(c=False)])
    # --- 0x3B SBC (IZ-$SX),$ ---
    def s():
        hd61700.set_reg(31,6); hd61700.set_reg(5,0x20)
        hd61700.set_reg16(2,0x6600)
        hd61700.set_sreg(0,31)
        hd61700.write_mem(0x65FA,0x50)
    check("3B SBC (IZ-$SX),R5", [0x3B,0x85], s,
          [cm(0x65FA,0x50), cf(c=False)])
    # --- 0x3C AD (IX+$),$ (write-back) ---
    def s():
        hd61700.set_reg(2,0x00); hd61700.set_reg(4,0x01)
        hd61700.set_reg16(0,0x6500)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6500,0xFE)
    check("3C AD (IX+R2),R4 wb", [0x3C,0x04], s,
          [cm(0x6500,0xFF), cf(c=False)])
    # --- 0x3D AD (IZ+$),$ (write-back) ---
    def s():
        hd61700.set_reg(2,0x00); hd61700.set_reg(5,0x01)
        hd61700.set_reg16(2,0x6600)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6600,0xFF)
    check("3D AD (IZ+R2),R5 wb", [0x3D,0x05], s,
          [cm(0x6600,0x00), cf(c=True,z=True)])
    # --- 0x3E SB (IX+$),$ ---
    def s():
        hd61700.set_reg(2,0x00); hd61700.set_reg(4,0x01)
        hd61700.set_reg16(0,0x6500)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6500,0x01)
    check("3E SB (IX+R2),R4 wb", [0x3E,0x04], s,
          [cm(0x6500,0x00), cf(c=False,z=True)])
    # --- 0x3F SB (IZ+$),$ ---
    def s():
        hd61700.set_reg(2,0x00); hd61700.set_reg(5,0x01)
        hd61700.set_reg16(2,0x6600)
        hd61700.set_sreg(0,2)
        hd61700.write_mem(0x6600,0x00)
    check("3F SB (IZ+R2),R5 wb", [0x3F,0x05], s,
          [cm(0x6600,0xFF), cf(c=True)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0x20-0x3F")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0x20-0x27", tests_20_27), ("0x28-0x2F", tests_28_2f),
                     ("0x30-0x37", tests_30_37), ("0x38-0x3F", tests_38_3f)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
