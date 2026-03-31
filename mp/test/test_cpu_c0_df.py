"""HD61700 CPU Test: opcodes 0xC0-0xDF
Multi-byte BCD arith (ADBM/SBBM), multi-byte logic (ANCM/NACM/ORCM/XRCM),
multi-byte copy (LDM), DIDM/DIUM/BYDM/BYUM, CMPM/INVM,
BUP/BDN, SUP/SDN reg, JPW, PRE/GRE imm, D0/D1 STW/LDW imm,
STLM/LDLM, PFLM/PSRM
"""
from test_cpu_common import *
import hd61700

def tests_c0_c3(t, check):
    """0xC0-0xC3: Multi-byte BCD (check), LDM, LDCM"""
    # _C0: ADBM check (no write-back, op < 0xC8)
    # enc: [op, arg, ext] where cnt=GET_IM3(ext)=((ext>>5)&7)+1, sec=(arg>>5)&3
    # sec=3 → src = ext&0x1f
    # For cnt=2: (ext>>5)&7 = 1 → ext = 0x20 | src_reg
    # R0=0x15, R1=0x27; src R4=0x01, R5=0x00; cnt=2
    def s():
        hd61700.set_reg(0,0x15); hd61700.set_reg(1,0x27)
        hd61700.set_reg(4,0x01); hd61700.set_reg(5,0x00)
    # arg=0x60 (reg=0, sec=3), ext=0x24 (cnt=((0x24>>5)&7)+1=2, src=4)
    check("C0 ADBM chk cnt=2", [0xC0,0x60,0x24], s,
          [cr(0,0x15), cr(1,0x27)])  # no write-back
    # _C1: SBBM check (no write-back)
    check("C1 SBBM chk cnt=2", [0xC1,0x60,0x24], s,
          [cr(0,0x15)])  # no write-back
    # _C2: LDM (multi-byte copy)
    def s():
        hd61700.set_reg(4,0xAA); hd61700.set_reg(5,0xBB); hd61700.set_reg(6,0xCC)
    # arg=0x60 (reg=0, sec=3), ext=0x44 (cnt=((0x44>>5)&7)+1=3, src=4)
    check("C2 LDM cnt=3", [0xC2,0x60,0x44], s,
          [cr(0,0xAA), cr(1,0xBB), cr(2,0xCC)])
    # _C3: LDCM (no-op)
    def s():
        hd61700.set_reg(0,0x99); hd61700.set_reg(1,0x88)
    check("C3 LDCM noop", [0xC3,0x60,0x24], s, [cr(0,0x99), cr(1,0x88)])


def tests_c4_cf(t, check):
    """0xC4-0xCF: Multi-byte logic (check & write-back), BCD imm"""
    # _C4: ANCM (AND check, no write-back)
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F)
        hd61700.set_reg(4,0x0F); hd61700.set_reg(5,0xFF)
    check("C4 ANCM chk cnt=2", [0xC4,0x60,0x24], s,
          [cr(0,0xFF), cr(1,0x0F)])  # no write-back
    # _CC: ANM (AND write-back)
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F)
        hd61700.set_reg(4,0x0F); hd61700.set_reg(5,0xFF)
    check("CC ANM cnt=2", [0xCC,0x60,0x24], s,
          [cr(0,0x0F), cr(1,0x0F)])
    # _C5: NACM (NAND check)
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(4,0xFF); hd61700.set_reg(5,0xFF)
    check("C5 NACM ~(FF&FF)=0 chk C", [0xC5,0x60,0x24], s,
          [cr(0,0xFF)])  # no write-back; flags: Z=True, C=True
    # _CD: NAM (NAND write-back)
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(4,0xFF); hd61700.set_reg(5,0xFF)
    check("CD NAM cnt=2", [0xCD,0x60,0x24], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True,c=True)])
    # _CE: ORM write-back
    def s():
        hd61700.set_reg(0,0xA0); hd61700.set_reg(1,0x00)
        hd61700.set_reg(4,0x0B); hd61700.set_reg(5,0x0C)
    check("CE ORM cnt=2 C", [0xCE,0x60,0x24], s,
          [cr(0,0xAB), cr(1,0x0C), cf(c=True)])
    # _CF: XRM write-back
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(4,0xFF); hd61700.set_reg(5,0xFF)
    check("CF XRM cnt=2", [0xCF,0x60,0x24], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True)])
    # _C8: ADBM (BCD add write-back)
    def s():
        hd61700.set_reg(0,0x15); hd61700.set_reg(1,0x27)
        hd61700.set_reg(4,0x01); hd61700.set_reg(5,0x00)
    check("C8 ADBM wb cnt=2", [0xC8,0x60,0x24], s,
          [cr(0,0x16), cr(1,0x27)])
    # _C9: SBBM (BCD sub write-back)
    def s():
        hd61700.set_reg(0,0x42); hd61700.set_reg(1,0x10)
        hd61700.set_reg(4,0x15); hd61700.set_reg(5,0x01)
    check("C9 SBBM wb cnt=2", [0xC9,0x60,0x24], s,
          [cr(0,0x27), cr(1,0x09)])
    # _CA: ADBM imm
    def s():
        hd61700.set_reg(0,0x15); hd61700.set_reg(1,0x27)
    # arg=0x00 (reg0), ext=0x21 (cnt=((0x21>>5)&7)+1=2, imm=1)
    check("CA ADBM imm+1 cnt=2", [0xCA,0x00,0x21], s,
          [cr(0,0x16), cr(1,0x27)])
    # _CB: SBBM imm
    def s():
        hd61700.set_reg(0,0x10); hd61700.set_reg(1,0x01)
    check("CB SBBM imm-1 cnt=2", [0xCB,0x00,0x21], s,
          [cr(0,0x09), cr(1,0x01)])


def tests_d0_d7(t, check):
    """0xD0-0xD7: STW/LDW imm, STLM/LDLM, PFLM, PSRM, PRE/GRE imm"""
    # --- 0xD0 STW IM16,(SIR) ---
    def s():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("D0 STW imm16 @SX", [0xD0,0x00,0x34,0x12], s,
          [cm(0x6500,0x34), cm(0x6501,0x12)])
    # --- 0xD1 LDW IM16 -> R0:R1 ---
    check("D1 LDW R0:1=1234h", [0xD1,0x00,0x34,0x12], None,
          [cr(0,0x34), cr(1,0x12)])
    # --- 0xD2 STLM (multi-byte LCD write) ---
    def s():
        hd61700.set_reg(0,0x11); hd61700.set_reg(1,0x22); hd61700.set_reg(2,0x33)
    # arg=0x00, ext=0x40 → cnt=((0x40>>5)&7)+1=3
    check("D2 STLM cnt=3", [0xD2,0x00,0x40], s,
          [lambda: (t.lcd_buf==[0x11,0x22,0x33], f"lcd={t.lcd_buf}")])
    # --- 0xD3 LDLM (multi-byte LCD read) ---
    def s(): t.lcd_buf = [0xAA, 0xBB]
    # arg=0x00, ext=0x20 → cnt=2
    check("D3 LDLM cnt=2", [0xD3,0x00,0x20], s,
          [cr(0,0xAA), cr(1,0xBB)])
    # --- 0xD4 PFLM (put flags multi) ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xBB)
    # idx=(arg>>5)&7=0→PE, cnt=2 → PE=R0, PD=R1
    check("D4 PFLM PE,PD cnt=2", [0xD4,0x00,0x20], s,
          [cr8(0,0xFF), cr8(1,0xBB)])
    # --- 0xD5 PSRM (put SIR multi) ---
    def s():
        hd61700.set_reg(0,5); hd61700.set_reg(1,10)
    # arg=0x00 (idx=(arg>>5)&3=0→SX), ext=0x20 → cnt=2
    check("D5 PSRM SX,SY cnt=2", [0xD5,0x00,0x20], s,
          [csr(0,5), csr(1,10)])
    # --- 0xD6 PRE IM16 -> IX ---
    check("D6 PRE IX imm", [0xD6,0x00,0x78,0x56], None,
          [cr16(0,0x5678)])
    # --- 0xD7 PRE IY imm ---
    def s(): hd61700.set_reg16(1,0)
    check("D7 PRE IY imm", [0xD6,0x20,0xAB,0xCD], s,
          [cr16(1,0xCDAB)])


def tests_d8_df(t, check):
    """0xD8-0xDF: BUP/BDN, DA multi-byte digit shift, DB CMP/INV multi,
       DC/DD SUP/SDN reg, DE/DF JPW"""
    # --- 0xD8 BUP (block up copy) ---
    def s():
        hd61700.set_reg16(0,0x6500)  # IX
        hd61700.set_reg16(1,0x6502)  # IY (end)
        hd61700.set_reg16(2,0x6600)  # IZ (dest)
        for i in range(3):
            hd61700.write_mem(0x6500+i, 0xA0+i)
    check("D8 BUP 3 bytes", [0xD8], s,
          [cm(0x6600,0xA0), cm(0x6601,0xA1), cm(0x6602,0xA2)])
    # --- 0xD9 BDN (block down copy) ---
    def s():
        hd61700.set_reg16(0,0x6502)  # IX (start high)
        hd61700.set_reg16(1,0x6500)  # IY (end low)
        hd61700.set_reg16(2,0x6602)  # IZ (dest high)
        for i in range(3):
            hd61700.write_mem(0x6500+i, 0xB0+i)
    check("D9 BDN 3 bytes", [0xD9], s,
          [cm(0x6600,0xB0), cm(0x6601,0xB1), cm(0x6602,0xB2)])
    # --- 0xDA DIDM (multi-byte digit shift right) ---
    # R0=0xAB, R1=0xCD, cnt=2
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg(1,0xCD)
    # arg=0x01 (reg=1, op1=0→DIDM), ext=0x20 (cnt=2)
    # Note: DIDM decrements arg, so start=R1, process R1,R0
    check("DA DIDM cnt=2", [0xDA,0x01,0x20], s,
          [cr(0,0xDA), cr(1,0x0C)])
    # --- 0xDA DIUM (multi-byte digit shift left) ---
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg(1,0xCD)
    # arg=0x20 (reg=0, op1=1→DIUM), ext=0x20 (cnt=2)
    check("DA DIUM cnt=2", [0xDA,0x20,0x20], s,
          [cr(0,0xB0), cr(1,0xDA)])
    # --- 0xDB CMPM (multi-byte 2's complement) ---
    def s():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
    # arg=0x00 (reg=0, CMP), ext=0x20 (cnt=2)
    check("DB CMPM cnt=2", [0xDB,0x00,0x20], s,
          [cr(0,0xFF), cr(1,0xFF), cf(c=True)])
    # --- 0xDB INVM (multi-byte inversion, arg bit6=1) ---
    def s():
        hd61700.set_reg(0,0x55); hd61700.set_reg(1,0xAA)
    check("DB INVM cnt=2", [0xDB,0x40,0x20], s,
          [cr(0,0xAA), cr(1,0x55), cf(c=True)])
    # --- 0xDC SUP reg (search up using reg value) ---
    def s():
        hd61700.set_reg(0,0x13)  # search value
        hd61700.set_reg16(0,0x6500); hd61700.set_reg16(1,0x6504)
        for i in range(5):
            hd61700.write_mem(0x6500+i, 0x10+i)
    check("DC SUP reg", [0xDC,0x00], s, [cf(z=True)])
    # --- 0xDE JPW (jump to reg pair address) ---
    tgt = 0x7080
    def s():
        hd61700.set_reg(0,tgt&0xFF); hd61700.set_reg(1,tgt>>8)
        t.lc(tgt,[0xF8])  # NOP at target
    check("DE JPW R0:R1", [0xDE,0x00], s, [cp(tgt+1)], stop=tgt+1)
    # --- 0xDF JPW indirect (jump via memory pointed by reg pair) ---
    def s():
        hd61700.set_reg(0,0x00); hd61700.set_reg(1,0x65)  # ptr=0x6500
        hd61700.write_mem(0x6500,tgt&0xFF)
        hd61700.write_mem(0x6501,tgt>>8)
        t.lc(tgt,[0xF8])
    check("DF JPW (R0:R1)", [0xDF,0x00], s, [cp(tgt+1)], stop=tgt+1)


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0xC0-0xDF")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0xC0-0xC3", tests_c0_c3), ("0xC4-0xCF", tests_c4_cf),
                     ("0xD0-0xD7", tests_d0_d7), ("0xD8-0xDF", tests_d8_df)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
