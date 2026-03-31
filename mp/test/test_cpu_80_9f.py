"""HD61700 CPU Test: opcodes 0x80-0x9F
16-bit arith/logic/BCD, LDW/LDCW, STLW/LDLW, PRE/GRE,
16-bit shift/rotate/CMP/INV
"""
from test_cpu_common import *
import hd61700

def tests_80_83(t, check):
    """0x80-0x83: ADCW/SBCW/LDW/LDCW"""
    # --- 0x80 ADCW (check only) ---
    def s():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x02); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("80 ADCW chk 1+2=3", [0x80,0x00], s,
          [cf(z=False,c=False), cr(0,0x01)])  # no write-back
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(2,0x01); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("80 ADCW overflow C", [0x80,0x00], s, [cf(c=True)])
    # --- 0x81 SBCW (check only) ---
    def s():
        hd61700.set_reg(0,0x03); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x03); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("81 SBCW chk 3-3=0 Z", [0x81,0x00], s,
          [cf(z=True,c=False)])
    # --- 0x82 LDW (16-bit copy) ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg(2,0x34); hd61700.set_reg(3,0x12)
        hd61700.set_sreg(0,2)
    check("82 LDW R0:1<-R2:3", [0x82,0x00], s,
          [cr(0,0x34), cr(1,0x12)])
    # --- 0x83 LDCW (no-op) ---
    def s():
        hd61700.set_reg(0,0x99); hd61700.set_reg(1,0x88)
        hd61700.set_reg(2,0x11); hd61700.set_reg(3,0x22)
        hd61700.set_sreg(0,2)
    check("83 LDCW noop", [0x83,0x00], s, [cr(0,0x99), cr(1,0x88)])


def tests_84_8f(t, check):
    """0x84-0x8F: 16-bit logic/arith/BCD"""
    # --- 0x84 ANCW (AND check 16-bit) ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x0F); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("84 ANCW chk", [0x84,0x00], s, [cf(z=False)])
    # --- 0x85 NACW (NAND check 16-bit) ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(2,0xFF); hd61700.set_reg(3,0xFF)
        hd61700.set_sreg(0,2)
    check("85 NACW ~(FFFF&FFFF)=0 Z,C", [0x85,0x00], s,
          [cf(z=True,c=True)])
    # --- 0x86 ORCW (OR check 16-bit) ---
    def s():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg(2,0); hd61700.set_reg(3,0)
        hd61700.set_sreg(0,2)
    check("86 ORCW 0|0=0 Z,C", [0x86,0x00], s, [cf(z=True,c=True)])
    # --- 0x87 XRCW (XOR check 16-bit) ---
    def s():
        hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0x55)
        hd61700.set_reg(2,0xAA); hd61700.set_reg(3,0x55)
        hd61700.set_sreg(0,2)
    check("87 XRCW same=0 Z", [0x87,0x00], s, [cf(z=True)])
    # --- 0x88 ADW (add 16-bit with write-back) ---
    def s():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x02); hd61700.set_reg(3,0x00)
    check("88 ADW 1+2=3", [0x88,0x60,0x02], s, [cr(0,0x03), cr(1,0x00)])
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(2,0x01); hd61700.set_reg(3,0x00)
    check("88 ADW FFFF+1 overflow", [0x88,0x60,0x02], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True,c=True)])
    # --- 0x89 SBW (sub 16-bit with write-back) ---
    def s():
        hd61700.set_reg(0,0x05); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x03); hd61700.set_reg(3,0x00)
    check("89 SBW 5-3=2", [0x89,0x60,0x02], s, [cr(0,0x02), cr(1,0x00)])
    # --- 0x8A ADBW (BCD add 16-bit) ---
    def s():
        hd61700.set_reg(0,0x99); hd61700.set_reg(1,0x99)
        hd61700.set_reg(2,0x01); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("8A ADBW 9999+0001", [0x8A,0x00], s, [cr(0,0x00), cr(1,0x00)])
    # --- 0x8B SBBW (BCD sub 16-bit) ---
    def s():
        hd61700.set_reg(0,0x00); hd61700.set_reg(1,0x01)
        hd61700.set_reg(2,0x01); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("8B SBBW 0100-0001=0099", [0x8B,0x00], s, [cr(0,0x99), cr(1,0x00)])
    # --- 0x8C ANW (AND with write-back) ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F)
        hd61700.set_reg(2,0x0F); hd61700.set_reg(3,0xFF)
        hd61700.set_sreg(0,2)
    check("8C ANW", [0x8C,0x00], s, [cr(0,0x0F), cr(1,0x0F)])
    # --- 0x8D NAW (NAND with write-back) ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(2,0xFF); hd61700.set_reg(3,0xFF)
        hd61700.set_sreg(0,2)
    check("8D NAW ~(FFFF)=0000 C", [0x8D,0x00], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True,c=True)])
    # --- 0x8E ORW ---
    def s():
        hd61700.set_reg(0,0xA0); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x0B); hd61700.set_reg(3,0x00)
        hd61700.set_sreg(0,2)
    check("8E ORW A0|0B=AB C", [0x8E,0x00], s,
          [cr(0,0xAB), cr(1,0x00), cf(c=True)])
    # --- 0x8F XRW ---
    def s():
        hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF)
        hd61700.set_reg(2,0xFF); hd61700.set_reg(3,0xFF)
        hd61700.set_sreg(0,2)
    check("8F XRW FFFF^FFFF=0 Z", [0x8F,0x00], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True)])


def tests_90_97(t, check):
    """0x90-0x97: STW/LDW via SIR, STLW/LDLW, PRE"""
    # --- 0x90 STW $:$+1,($SIR) ---
    def s():
        hd61700.set_reg(0,0x34); hd61700.set_reg(1,0x12)
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("90 STW R0:1,(SX)", [0x90,0x00], s,
          [cm(0x6500,0x34), cm(0x6501,0x12)])
    # --- 0x91 LDW ---
    def s():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0xAB); hd61700.write_mem(0x6501,0xCD)
    check("91 LDW R0:1,(SX)", [0x91,0x00], s,
          [cr(0,0xAB), cr(1,0xCD)])
    # --- 0x92 STLW (LCD write word) ---
    def s(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0xBB)
    check("92 STLW", [0x92,0x00], s,
          [lambda: (t.lcd_buf==[0xAA,0xBB], f"lcd={t.lcd_buf}")])
    # --- 0x93 LDLW (LCD read word) ---
    def s(): t.lcd_buf = [0x11, 0x22]
    check("93 LDLW", [0x93,0x00], s, [cr(0,0x11), cr(1,0x22)])
    # --- 0x96 PRE IX ---
    def s(): hd61700.set_reg(0,0x34); hd61700.set_reg(1,0x12)
    check("96 PRE IX", [0x96,0x00], s,
          [lambda: (hd61700.get_reg16(0)==0x1234, f"IX={hd61700.get_reg16(0):04X}")])
    # --- 0x96 PRE IY (idx=1: (0x96&1)<<2 | (0x20>>5)&3 = 0|1 = 1) ---
    def s(): hd61700.set_reg(0,0x78); hd61700.set_reg(1,0x56)
    check("96 PRE IY", [0x96,0x20], s,
          [lambda: (hd61700.get_reg16(1)==0x5678, "IY=%04X" % hd61700.get_reg16(1))])


def tests_98_9f(t, check):
    """0x98-0x9F: 16-bit shift/rotate, CMP/INV, GRE"""
    # --- 0x98 BIDW (16-bit shift right) ---
    def s():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
    check("98 BIDW 0001>>1=0000 C", [0x98,0x41], s,
          [cr(0,0x00), cr(1,0x00), cf(c=True,z=True)])
    # BIUW (16-bit shift left)
    def s():
        hd61700.set_reg(0,0x00); hd61700.set_reg(1,0x80)
    check("98 BIUW 8000<<1=0000 C", [0x98,0x60], s,
          [cr(0,0x00), cr(1,0x00), cf(c=True,z=True)])
    # --- 0x9A DIDW (16-bit digit shift right) ---
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg(1,0xCD)
    check("9A DIDW CDAB>>4=0CDA", [0x9A,0x01], s,
          [cr(0,0xDA), cr(1,0x0C)])
    # DIUW (16-bit digit shift left)
    def s():
        hd61700.set_reg(0,0xAB); hd61700.set_reg(1,0xCD)
    check("9A DIUW CDAB<<4=DAB0", [0x9A,0x20], s,
          [cr(0,0xB0), cr(1,0xDA)])
    # --- 0x9B CMPW (16-bit 2's complement) ---
    def s(): hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
    check("9B CMPW ~0001+1=FFFF C", [0x9B,0x00], s,
          [cr(0,0xFF), cr(1,0xFF), cf(c=True)])
    def s(): hd61700.set_reg(0,0x00); hd61700.set_reg(1,0x00)
    check("9B CMPW ~0+1=0 noC Z", [0x9B,0x00], s,
          [cr(0,0x00), cr(1,0x00), cf(z=True,c=False)])
    # INVW (16-bit bit inversion)
    def s(): hd61700.set_reg(0,0x55); hd61700.set_reg(1,0xAA)
    check("9B INVW ~AA55=55AA C", [0x9B,0x40], s,
          [cr(0,0xAA), cr(1,0x55), cf(c=True)])
    # --- 0x9E GRE IX->R0:R1 ---
    def s(): hd61700.set_reg16(0,0x5678)
    check("9E GRE IX", [0x9E,0x00], s, [cr(0,0x78), cr(1,0x56)])
    # GRE IX->R15:R16
    def s(): hd61700.set_reg16(0,0x1234)
    check("9E GRE IX->R15:16", [0x9E,0x0F], s, [cr(15,0x34), cr(16,0x12)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0x80-0x9F")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0x80-0x83", tests_80_83), ("0x84-0x8F", tests_84_8f),
                     ("0x90-0x97", tests_90_97), ("0x98-0x9F", tests_98_9f)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
