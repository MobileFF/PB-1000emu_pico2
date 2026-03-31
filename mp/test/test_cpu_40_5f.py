"""HD61700 CPU Test: opcodes 0x40-0x5F
8-bit arith/logic/BCD with imm, LD/LDC imm, ST imm, PST imm, block xfer/search
"""
from test_cpu_common import *
import hd61700

def tests_40_43(t, check):
    """0x40-0x43: ADC/SBC/LD/LDC imm"""
    # --- 0x40 ADC $,IM8 (check only) ---
    def s(): hd61700.set_reg(0,0x80)
    check("40 ADC imm chk C", [0x40,0x00,0x80], s, [cf(c=True), cr(0,0x80)])
    def s(): hd61700.set_reg(0,5)
    check("40 ADC imm chk 5+3", [0x40,0x00,3], s, [cf(z=False,c=False), cr(0,5)])
    # --- 0x41 SBC $,IM8 (check only) ---
    def s(): hd61700.set_reg(0,5)
    check("41 SBC imm chk 5-5 Z", [0x41,0x00,5], s, [cf(z=True,c=False), cr(0,5)])
    def s(): hd61700.set_reg(0,3)
    check("41 SBC imm chk 3-5 C", [0x41,0x00,5], s, [cf(c=True), cr(0,3)])
    # --- 0x42 LD $,IM8 ---
    check("42 LD R0,55h", [0x42,0x00,0x55], None, [cr(0,0x55)])
    check("42 LD R3,CCh", [0x42,0x03,0xCC], None, [cr(3,0xCC)])
    # --- 0x43 LDC $,IM8 (no-op) ---
    def s(): hd61700.set_reg(0,0x99)
    check("43 LDC noop", [0x43,0x00,0x11], s, [cr(0,0x99)])


def tests_44_4f(t, check):
    """0x44-0x4F: Logic/BCD imm check & write-back"""
    # --- 0x44 ANC (AND check imm) ---
    def s(): hd61700.set_reg(0,0xFF)
    check("44 ANC FF&0F chk", [0x44,0x00,0x0F], s, [cf(z=False), cr(0,0xFF)])
    # --- 0x45 NAC (NAND check imm) ---
    def s(): hd61700.set_reg(0,0xFF)
    check("45 NAC ~(FF&FF)=0 C", [0x45,0x00,0xFF], s, [cf(z=True,c=True)])
    # --- 0x46 ORC (OR check imm) ---
    def s(): hd61700.set_reg(0,0)
    check("46 ORC 0|0=0 Z,C", [0x46,0x00,0x00], s, [cf(z=True,c=True)])
    # --- 0x47 XRC (XOR check imm) ---
    def s(): hd61700.set_reg(0,0xAA)
    check("47 XRC AA^AA=0 Z", [0x47,0x00,0xAA], s, [cf(z=True)])
    # --- 0x48 AD $,IM8 (write-back) ---
    def s(): hd61700.set_reg(0,10)
    check("48 AD imm 10+20=30", [0x48,0x00,20], s, [cr(0,30)])
    # --- 0x49 SB $,IM8 (write-back) ---
    def s(): hd61700.set_reg(0,1)
    check("49 SB imm 1-2=FF C", [0x49,0x00,2], s, [cr(0,0xFF), cf(c=True)])
    # --- 0x4A ADB $,IM8 (BCD add) ---
    def s(): hd61700.set_reg(0,0x99)
    check("4A ADB 99+01 carry", [0x4A,0x00,0x01], s, [cr(0,0x00), cf(c=True)])
    def s(): hd61700.set_reg(0,0x15)
    check("4A ADB 15+27=42", [0x4A,0x00,0x27], s, [cr(0,0x42)])
    # --- 0x4B SBB $,IM8 (BCD sub) ---
    def s(): hd61700.set_reg(0,0x42)
    check("4B SBB 42-15=27", [0x4B,0x00,0x15], s, [cr(0,0x27)])
    def s(): hd61700.set_reg(0,0x00)
    check("4B SBB 00-01 borrow", [0x4B,0x00,0x01], s, [cf(c=True)])
    # --- 0x4C AN $,IM8 ---
    def s(): hd61700.set_reg(0,0xFF)
    check("4C AN FF&0F=0F", [0x4C,0x00,0x0F], s, [cr(0,0x0F)])
    # --- 0x4D NA $,IM8 ---
    def s(): hd61700.set_reg(0,0xF0)
    check("4D NA ~(F0&0F)=FF C", [0x4D,0x00,0x0F], s, [cr(0,0xFF), cf(c=True)])
    # --- 0x4E OR $,IM8 ---
    def s(): hd61700.set_reg(0,0xA0)
    check("4E OR A0|05=A5 C", [0x4E,0x00,0x05], s, [cr(0,0xA5), cf(c=True)])
    # --- 0x4F XR $,IM8 ---
    def s(): hd61700.set_reg(0,0xFF)
    check("4F XR FF^FF=00 Z", [0x4F,0x00,0xFF], s, [cr(0,0x00), cf(z=True)])


def tests_50_57(t, check):
    """0x50-0x57: ST imm/SIR, PSR imm, PST imm, PPO imm, STL imm"""
    # --- 0x50 ST IM8,($SIR) ---
    def s():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("50 ST imm,(SX) 7F@6500", [0x50,0x00,0x7F], s, [cm(0x6500,0x7F)])
    # --- 0x51 ST IM8,$SIR (write SIR or reg) ---
    def s(): pass
    check("51 ST imm,SX", [0x51,0x00,0x0A], s, [csr(0,0x0A)])
    # --- 0x52 STL IM8 (LCD write imm) ---
    check("52 STL imm", [0x52,0xAA], None,
          [lambda: (0xAA in t.lcd_buf, f"lcd={t.lcd_buf}")])
    # --- 0x54 PPO IM8 / PFL IM8 ---
    check("54 PPO imm AB", [0x54,0x00,0xAB], None,
          [lambda: (t.port_out==0xAB, f"port={t.port_out:02X}")])
    # PFL imm (arg bit6=1)
    check("54 PFL imm F0", [0x54,0x40,0xF0], None,
          [lambda: ((F()&0xF0)==0xF0, f"F={F():02X}")])
    # --- 0x55 PSR imm ---
    check("55 PSR SX=5", [0x55,0x05], None, [csr(0,5)])
    # --- 0x56 PST PE,IM8 ---
    check("56 PST PE,FF", [0x56,0x00,0xFF], None, [cr8(0,0xFF)])
    check("56 PST UA,10", [0x56,0x60,0x10], None, [cr8(3,0x10)])
    check("56 PST IB,A0", [0x56,0x40,0xA0], None,
          [lambda: ((R8(2)&0xE0)==0xA0, f"IB={R8(2):02X}")])
    # --- 0x57 PST IA,IM8 ---
    check("57 PST IA,0D", [0x57,0x00,0x0D], None, [cr8(4,0x0D)])
    check("57 PST IE,40", [0x57,0x20,0x40], None, [cr8(5,0x40)])


def tests_58_5f(t, check):
    """0x58-0x5F: Block xfer/search"""
    # --- 0x58 BUPS (block up search+copy) ---
    def s():
        hd61700.set_reg16(0,0x6500)  # IX
        hd61700.set_reg16(1,0x6504)  # IY (end)
        hd61700.set_reg16(2,0x6600)  # IZ (dest)
        for i in range(5):
            hd61700.write_mem(0x6500+i, 0x10+i)
    # search for 0x12 (at IX+2)
    check("58 BUPS find 12h", [0x58,0x12], s,
          [cf(z=True), cm(0x6600,0x10), cm(0x6601,0x11), cm(0x6602,0x12)])
    # --- 0x59 BDNS (block down search+copy) ---
    def s():
        hd61700.set_reg16(0,0x6504)  # IX (start high)
        hd61700.set_reg16(1,0x6500)  # IY (end low)
        hd61700.set_reg16(2,0x6604)  # IZ (dest high)
        for i in range(5):
            hd61700.write_mem(0x6500+i, 0x10+i)
    check("59 BDNS find 12h", [0x59,0x12], s,
          [cf(z=True)])
    # --- 0x5C SUP (search up imm) ---
    def s():
        hd61700.set_reg16(0,0x6500)  # IX
        hd61700.set_reg16(1,0x6504)  # IY
        for i in range(5):
            hd61700.write_mem(0x6500+i, 0x10+i)
    check("5C SUP find 13h", [0x5C,0x13], s, [cf(z=True)])
    # --- 0x5D SDN (search down imm) ---
    def s():
        hd61700.set_reg16(0,0x6504)
        hd61700.set_reg16(1,0x6500)
        for i in range(5):
            hd61700.write_mem(0x6500+i, 0x10+i)
    check("5D SDN find 11h", [0x5D,0x11], s, [cf(z=True)])

    # --- 0x5A/0x5B/0x5E/0x5F no-jump-extension compatibility ---
    def s(): hd61700.set_reg(1,0x02)
    check("5A DIU no-jump", [0x5A,0xA1], s, [cr(1,0x20), cp(0x7002)])
    def s(): hd61700.set_reg(0,0x01)
    check("5B CMP no-jump", [0x5B,0x80], s, [cr(0,0xFF), cf(c=True), cp(0x7002)])
    def s(): hd61700.set_reg8(1,0xBB)
    check("5E GST PD no-jump", [0x5E,0xA0], s, [cr(0,0xBB), cp(0x7002)])
    def s(): hd61700.set_reg8(4,0x0D)
    check("5F GST IA no-jump", [0x5F,0x80], s, [cr(0,0x0D), cp(0x7002)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0x40-0x5F")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0x40-0x43", tests_40_43), ("0x44-0x4F", tests_44_4f),
                     ("0x50-0x57", tests_50_57), ("0x58-0x5F", tests_58_5f)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
