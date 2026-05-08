"""HD61700 CPU Test: opcodes 0x00-0x1F
8-bit arith/logic/BCD, LD/LDC, ST/LD via SIR, STL/LDL, PPO/PFL,
PSR/PST/GSR/GST, shift/rotate, CMP/INV, GPO/GFL
"""
from test_cpu_common import *
import hd61700

def tests_00_0b(t, check):
    """0x00-0x0B: 8-bit arith + BCD (check & write-back)"""
    # --- 0x00 ADC (check only, no write-back) ---
    def s(): hd61700.set_reg(0,5); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("00 ADC chk 5+3 noC", [0x00,0x20], s, [cf(z=False,c=False), cr(0,5)])
    def s(): hd61700.set_reg(0,0x80); hd61700.set_reg(1,0x80); hd61700.set_sreg(1,1)
    check("00 ADC chk overflow C", [0x00,0x20], s, [cf(z=True,c=True), cr(0,0x80)])
    def s(): hd61700.set_reg(0,0); hd61700.set_reg(1,0); hd61700.set_sreg(1,1)
    check("00 ADC chk 0+0 Z", [0x00,0x20], s, [cf(z=True,c=False,lz=True,uz=True)])
    def s(): hd61700.set_reg(0,0xF0); hd61700.set_reg(1,0x01); hd61700.set_sreg(1,1)
    check("00 ADC chk LZ/UZ", [0x00,0x20], s, [cf(lz=False,uz=False)])

    # --- 0x01 SBC (check only) ---
    def s(): hd61700.set_reg(0,5); hd61700.set_reg(1,4); hd61700.set_sreg(1,1)
    check("01 SBC chk 5-4 noC", [0x01,0x20], s, [cf(z=False,c=False), cr(0,5)])
    def s(): hd61700.set_reg(0,3); hd61700.set_reg(1,5); hd61700.set_sreg(1,1)
    check("01 SBC chk borrow", [0x01,0x20], s, [cf(c=True), cr(0,3)])
    def s(): hd61700.set_reg(0,5); hd61700.set_reg(1,5); hd61700.set_sreg(1,1)
    check("01 SBC chk zero", [0x01,0x20], s, [cf(z=True,c=False), cr(0,5)])

    # --- 0x02 LD $,$ ---
    def s(): hd61700.set_reg(0,0); hd61700.set_reg(1,0xAB); hd61700.set_sreg(1,1)
    check("02 LD R0,R1", [0x02,0x20], s, [cr(0,0xAB)])
    # sec=3 explicit: LD $5,$10
    def s(): hd61700.set_reg(5,0); hd61700.set_reg(10,0xCD)
    check("02 LD R5,R10 explicit", [0x02,0x65,0x0A], s, [cr(5,0xCD)])
    # JR explicit check:
    def s(): hd61700.set_reg(5,0); hd61700.set_reg(31,0xCD); hd61700.set_sreg(0, 0x1F)
    check("02 LD R5,(SX) with JR +15", [0x02,0x85,0x0F], s, [cr(5,0xCD), cp(0x7011)])


    # --- 0x03 LDC (no-op) ---
    def s(): hd61700.set_reg(0,0x99); hd61700.set_reg(1,0x11); hd61700.set_sreg(1,1)
    check("03 LDC noop", [0x03,0x20], s, [cr(0,0x99)])

    # --- 0x04 ANC (AND check, no write-back) ---
    def s(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0x55); hd61700.set_sreg(1,1)
    check("04 ANC AA&55=0 Z", [0x04,0x20], s, [cf(z=True), cr(0,0xAA)])
    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("04 ANC FF&0F", [0x04,0x20], s, [cf(z=False), cr(0,0xFF)])

    # --- 0x05 NAC (NAND check) ---
    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF); hd61700.set_sreg(1,1)
    check("05 NAC ~(FF&FF)=00 Z,C", [0x05,0x20], s, [cf(z=True,c=True), cr(0,0xFF)])
    def s(): hd61700.set_reg(0,0xF0); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("05 NAC ~(F0&0F)=FF C", [0x05,0x20], s, [cf(z=False,c=True), cr(0,0xF0)])

    # --- 0x06 ORC (OR check) ---
    def s(): hd61700.set_reg(0,0); hd61700.set_reg(1,0); hd61700.set_sreg(1,1)
    check("06 ORC 0|0=0 Z,C", [0x06,0x20], s, [cf(z=True,c=True), cr(0,0)])
    def s(): hd61700.set_reg(0,0xA0); hd61700.set_reg(1,0x0B); hd61700.set_sreg(1,1)
    check("06 ORC A0|0B=AB C", [0x06,0x20], s, [cf(z=False,c=True), cr(0,0xA0)])

    # --- 0x07 XRC (XOR check) ---
    def s(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0xAA); hd61700.set_sreg(1,1)
    check("07 XRC AA^AA=00 Z", [0x07,0x20], s, [cf(z=True,c=False), cr(0,0xAA)])
    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("07 XRC FF^0F=F0", [0x07,0x20], s, [cf(z=False,c=False), cr(0,0xFF)])

    # --- 0x08 AD (add with write-back) ---
    def s(): hd61700.set_reg(0,5); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("08 AD 5+3=8", [0x08,0x20], s, [cr(0,8), cf(z=False,c=False)])
    def s(): hd61700.set_reg(0,0x80); hd61700.set_reg(1,0x80); hd61700.set_sreg(1,1)
    check("08 AD overflow", [0x08,0x20], s, [cr(0,0), cf(z=True,c=True)])
    def s(): hd61700.set_reg(0,0x0F); hd61700.set_reg(1,0x01); hd61700.set_sreg(1,1)
    check("08 AD LZ=1,UZ=0", [0x08,0x20], s, [cr(0,0x10), cf(lz=True,uz=False)])

    # --- 0x09 SB (sub with write-back) ---
    def s(): hd61700.set_reg(0,1); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("09 SB 1-3=FE C", [0x09,0x20], s, [cr(0,0xFE), cf(c=True)])
    def s(): hd61700.set_reg(0,5); hd61700.set_reg(1,5); hd61700.set_sreg(1,1)
    check("09 SB 5-5=0 Z", [0x09,0x20], s, [cr(0,0), cf(z=True,c=False)])

    # --- 0x0A ADB (BCD add with write-back) ---
    def s(): hd61700.set_reg(0,0x15); hd61700.set_reg(1,0x27); hd61700.set_sreg(1,1)
    check("0A ADB 15+27=42", [0x0A,0x20], s, [cr(0,0x42)])
    def s(): hd61700.set_reg(0,0x99); hd61700.set_reg(1,0x01); hd61700.set_sreg(1,1)
    check("0A ADB 99+01 carry", [0x0A,0x20], s, [cr(0,0x00), cf(c=True)])

    # --- 0x0B SBB (BCD sub with write-back) ---
    def s(): hd61700.set_reg(0,0x42); hd61700.set_reg(1,0x15); hd61700.set_sreg(1,1)
    check("0B SBB 42-15=27", [0x0B,0x20], s, [cr(0,0x27)])
    def s(): hd61700.set_reg(0,0x00); hd61700.set_reg(1,0x01); hd61700.set_sreg(1,1)
    check("0B SBB 00-01 borrow", [0x0B,0x20], s, [cf(c=True)])


def tests_0c_0f(t, check):
    """0x0C-0x0F: 8-bit logic with write-back"""
    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("0C AN FF&0F=0F", [0x0C,0x20], s, [cr(0,0x0F), cf(z=False)])
    def s(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0x55); hd61700.set_sreg(1,1)
    check("0C AN AA&55=0 Z", [0x0C,0x20], s, [cr(0,0x00), cf(z=True)])

    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF); hd61700.set_sreg(1,1)
    check("0D NA ~(FF&FF)=00 C", [0x0D,0x20], s, [cr(0,0x00), cf(z=True,c=True)])
    def s(): hd61700.set_reg(0,0xF0); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("0D NA ~(F0&0F)=FF C", [0x0D,0x20], s, [cr(0,0xFF), cf(z=False,c=True)])

    def s(): hd61700.set_reg(0,0xA0); hd61700.set_reg(1,0x05); hd61700.set_sreg(1,1)
    check("0E OR A0|05=A5 C", [0x0E,0x20], s, [cr(0,0xA5), cf(z=False,c=True)])
    def s(): hd61700.set_reg(0,0); hd61700.set_reg(1,0); hd61700.set_sreg(1,1)
    check("0E OR 0|0=0 Z,C", [0x0E,0x20], s, [cr(0,0), cf(z=True,c=True)])

    def s(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0xFF); hd61700.set_sreg(1,1)
    check("0F XR AA^FF=55", [0x0F,0x20], s, [cr(0,0x55), cf(z=False,c=False)])
    def s(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0xFF); hd61700.set_sreg(1,1)
    check("0F XR FF^FF=00 Z", [0x0F,0x20], s, [cr(0,0x00), cf(z=True)])


def tests_10_13(t, check):
    """0x10-0x13: ST/LD via SIR, STL/LDL"""
    # --- 0x10 ST $,($) ---
    def s():
        hd61700.set_reg(0,0x42)
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("10 ST R0,(R4:5)", [0x10,0x00], s, [cm(0x6500,0x42)])
    def s():
        hd61700.set_reg(0,0x77)
        hd61700.set_reg(6,0x00); hd61700.set_reg(7,0x66)
        hd61700.set_sreg(1,6)
    check("10 ST R0,(R6:7) SY", [0x10,0x20], s, [cm(0x6600,0x77)])

    # --- 0x11 LD $,($) ---
    def s():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0xBE)
    check("11 LD R0,(R4:5)", [0x11,0x00], s, [cr(0,0xBE)])

    # --- 0x12 STL (LCD write) ---
    def s(): hd61700.set_reg(0,0xAA)
    check("12 STL R0", [0x12,0x00], s,
          [lambda: (t.lcd_buf==[0xAA],f"lcd={t.lcd_buf}")])

    # 0x12 STL with JR extension (bit7=1)
    # JR offset byte is at 0x7002, offset=0x00 -> target = 0x7002
    def s(): hd61700.set_reg(0,0xAB)
    check("12 STL R0,JR", [0x12,0x80,0x00], s,
          [lambda: (t.lcd_buf==[0xAB],f"lcd={t.lcd_buf}"), cp(0x7002)],
          stop=0x7003)

    # --- 0x13 LDL (LCD read) ---
    def s(): t.lcd_buf = [0x55]
    check("13 LDL R0", [0x13,0x00], s, [cr(0,0x55)])
    # LDL with empty buffer -> default 0xEE
    check("13 LDL R0 empty", [0x13,0x00], None, [cr(0,0xEE)])

    # 0x13 LDL with JR extension (bit7=1)
    # JR offset byte is at 0x7002, offset=0x00 -> target = 0x7002
    def s(): t.lcd_buf = [0x66]
    check("13 LDL R0,JR", [0x13,0x80,0x00], s,
          [cr(0,0x66), cp(0x7002)],
          stop=0x7003)


def tests_14_17(t, check):
    """0x14-0x17: PPO/PFL, PSR, PST"""
    # --- 0x14 PPO (port out) / PFL (put flag) ---
    def s(): hd61700.set_reg(0,0xAB)
    check("14 PPO R0", [0x14,0x00], s,
          [lambda: (t.port_out==0xAB,f"port={t.port_out:02X}")])
    # PFL: arg bit6=1 -> put flags
    def s(): hd61700.set_reg(0,0xF0)
    check("14 PFL R0", [0x14,0x40], s,
          [lambda: ((F()&0xF0)==0xF0, "F=%02X" % F())])

    # --- 0x15 PSR (put SIR) ---
    def s(): hd61700.set_reg(0,5)
    check("15 PSR SX<-R0=5", [0x15,0x00], s, [csr(0,5)])
    def s(): hd61700.set_reg(0,10)
    check("15 PSR SY<-R0=10", [0x15,0x20], s, [csr(1,10)])
    def s(): hd61700.set_reg(0,31)
    check("15 PSR SZ<-R0=31", [0x15,0x40], s, [csr(2,31)])

    # --- 0x16 PST (PE/PD/IB/UA from reg) ---
    def s(): hd61700.set_reg(0,0xFF)
    check("16 PST PE<-R0", [0x16,0x00], s, [cr8(0,0xFF)])
    def s(): hd61700.set_reg(0,0xAA)
    check("16 PST PD<-R0", [0x16,0x20], s, [cr8(1,0xAA)])
    def s(): hd61700.set_reg(0,0xE0)
    check("16 PST IB<-R0", [0x16,0x40], s,
          [lambda: ((R8(2)&0xE0)==0xE0, "IB=%02X" % R8(2))])
    def s(): hd61700.set_reg(0,0x10)
    check("16 PST UA<-R0", [0x16,0x60], s, [cr8(3,0x10)])

    # --- 0x17 PST (IA/IE from reg) ---
    def s(): hd61700.set_reg(0,0x0D)
    check("17 PST IA<-R0", [0x17,0x00], s, [cr8(4,0x0D)])
    def s(): hd61700.set_reg(0,0x40)
    check("17 PST IE<-R0", [0x17,0x20], s, [cr8(5,0x40)])


def tests_18_1b(t, check):
    """0x18-0x1B: Shift/Rotate, CMP/INV"""
    # --- 0x18 BID (shift right, no rotate-through) ---
    def s(): hd61700.set_reg(0,0x55)
    check("18 BID 55>>1=2A C", [0x18,0x40], s, [cr(0,0x2A), cf(c=True)])
    # BIU (shift left)
    def s(): hd61700.set_reg(0,0x80)
    check("18 BIU 80<<1=00 C", [0x18,0x60], s, [cr(0,0x00), cf(c=True)])
    # ROD (rotate right through carry) - carry=0
    def s(): hd61700.set_reg(0,0x01); hd61700.set_flags(0)
    check("18 ROD 01>>1=00 C(carry=0)", [0x18,0x00], s, [cr(0,0x00), cf(c=True,z=True)])
    # ROU (rotate left through carry) - carry=1
    def s(): hd61700.set_reg(0,0x01); hd61700.set_flags(0x40)
    check("18 ROU 01<<1+C=03", [0x18,0x20], s, [cr(0,0x03), cf(c=False)])

    # --- 0x19 same operations on different register ---
    def s(): hd61700.set_reg(3,0xAA)
    check("19 BID R3 AA>>1=55", [0x18,0x43], s, [cr(3,0x55), cf(c=False)])
    def s(): hd61700.set_reg(3,0x01)
    check("19 BIU R3 01<<1=02", [0x18,0x63], s, [cr(3,0x02), cf(c=False)])

    # --- 0x1A DID/DIU/BYD/BYU ---
    def s(): hd61700.set_reg(0,0xAB)
    check("1A DID AB>>4=0A", [0x1A,0x00], s, [cr(0,0x0A)])
    def s(): hd61700.set_reg(0,0xAB)
    check("1A DIU AB<<4=B0", [0x1A,0x20], s, [cr(0,0xB0)])
    # BYD (op1=2): clear reg, move value to reg-1
    def s(): hd61700.set_reg(1,0x55)
    check("1A BYD R1->R0,R1=0", [0x1A,0x41], s, [cr(0,0x55), cr(1,0x00)])
    # BYU (op1=3): clear reg, move value to reg+1
    def s(): hd61700.set_reg(0,0x77)
    check("1A BYU R0->R1,R0=0", [0x1A,0x60], s, [cr(1,0x77), cr(0,0x00)])

    # --- 0x1B CMP/INV ---
    def s(): hd61700.set_reg(0,1)
    check("1B CMP ~1+1=FF C", [0x1B,0x00], s, [cr(0,0xFF), cf(c=True)])
    def s(): hd61700.set_reg(0,0)
    check("1B CMP ~0+1=0 noC,Z", [0x1B,0x00], s, [cr(0,0x00), cf(c=False,z=True)])
    def s(): hd61700.set_reg(0,0x55)
    check("1B INV ~55=AA C", [0x1B,0x40], s, [cr(0,0xAA), cf(c=True)])
    def s(): hd61700.set_reg(0,0xFF)
    check("1B INV ~FF=00 C", [0x1B,0x40], s, [cr(0,0x00), cf(c=True,z=True)])

    # --- 0x5A/0x5B no-jump-extension compatibility tests ---
    def s(): hd61700.set_reg(1,0x02)
    check("5A DIU R1 no-jump", [0x5A,0xA1], s, [cr(1,0x20), cp(0x7002)])

    def s(): hd61700.set_reg(0,0x01)
    check("5B CMP R0 no-jump", [0x5B,0x80], s, [cr(0,0xFF), cf(c=True), cp(0x7002)])


def tests_1c_1f(t, check):
    """0x1C-0x1F: GPO/GFL, GSR, GST"""
    # --- 0x1C GPO (port read) ---
    def s(): t.port_out = 0x55
    check("1C GPO R0<-port", [0x1C,0x00], s, [cr(0,0x55)])
    # GFL: arg bit6=1 -> get flags
    def s(): hd61700.set_flags(0xB0)
    check("1C GFL R0<-flags", [0x1C,0x40], s, [cr(0,0xB0)])

    # --- 0x1D GSR (get SIR) ---
    def s(): hd61700.set_sreg(0,7)
    check("1D GSR SX->R0=7", [0x1D,0x00], s, [cr(0,7)])
    def s(): hd61700.set_sreg(1,15)
    check("1D GSR SY->R0=15", [0x1D,0x20], s, [cr(0,15)])
    def s(): hd61700.set_sreg(2,31)
    check("1D GSR SZ->R0=31", [0x1D,0x40], s, [cr(0,31)])

    # --- 0x1E GST (PE/PD/IB/UA -> reg) ---
    def s(): hd61700.set_reg8(0,0xAA)
    check("1E GST PE->R0", [0x1E,0x00], s, [cr(0,0xAA)])
    def s(): hd61700.set_reg8(1,0xBB)
    check("1E GST PD->R0", [0x1E,0x20], s, [cr(0,0xBB)])
    def s(): hd61700.set_reg8(3,0x10)
    check("1E GST UA->R0", [0x1E,0x60], s, [cr(0,0x10)])

    # --- 0x1F GST (IA/IE -> reg) ---
    def s(): hd61700.set_reg8(4,0x0D)
    check("1F GST IA->R0", [0x1F,0x00], s, [cr(0,0x0D)])
    def s(): hd61700.set_reg8(5,0x40)
    check("1F GST IE->R0", [0x1F,0x20], s, [cr(0,0x40)])

    # --- 0x5E/0x5F no-jump-extension GST ---
    def s(): hd61700.set_reg8(1,0xBB)
    check("5E GST PD->R0 no-jump", [0x5E,0xA0], s, [cr(0,0xBB), cp(0x7002)])
    def s(): hd61700.set_reg8(4,0x0D)
    check("5F GST IA->R0 no-jump", [0x5F,0x80], s, [cr(0,0x0D), cp(0x7002)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0x00-0x1F")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0x00-0x0B", tests_00_0b), ("0x0C-0x0F", tests_0c_0f),
                     ("0x10-0x13", tests_10_13), ("0x14-0x17", tests_14_17),
                     ("0x18-0x1B", tests_18_1b), ("0x1C-0x1F", tests_1c_1f)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
