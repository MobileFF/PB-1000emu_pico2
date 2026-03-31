"""HD61700 CPU Test: opcodes 0xE0-0xFF
Multi-byte indexed ST/LD (STM/STIM/STDM/LDM/LDIM/LDDM),
Multi-byte stack (PHSM/PHUM/PPSM/PPUM),
RTN conditional, NOP, CLT, FST, SLW, CANI, RTNI, OFF, TRP
"""
from test_cpu_common import *
import hd61700

def tests_e0_e5(t, check):
    """0xE0-0xE5: STM/STIM/STDM IX/IZ multi-byte indexed"""
    # STM encoding: [op, arg, ext]
    # arg: bits4:0=start_reg, bit7=sign, bits6:5=sec
    # ext: bits7:5=((cnt-1)), bits4:0=offset_reg (via get_sir_im8_arg1)
    # For sec=3 (explicit offset reg): offset comes from ext&0x1f

    # --- 0xE0 STM $,(IX) restore, cnt=2 ---
    def s():
        hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0xBB)
        hd61700.set_reg(4,0)  # offset reg = 0
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(0,4)
    # arg=0x00 (reg0, sec=0→SX), ext=0x20 (cnt=2, offset via SX=R4=0)
    check("E0 STM IX cnt=2 restore", [0xE0,0x00,0x20], s,
          [cm(0x6500,0xAA), cm(0x6501,0xBB), cr16(0,0x6500)])
    # --- 0xE2 STIM $,(IX)+ cnt=2 no restore ---
    def s():
        hd61700.set_reg(0,0x11); hd61700.set_reg(1,0x22)
        hd61700.set_reg(4,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(0,4)
    check("E2 STIM IX cnt=2", [0xE2,0x00,0x20], s,
          [cm(0x6500,0x11), cm(0x6501,0x22), cr16(0,0x6502)])
    # --- 0xE1 STM $,(IZ) restore ---
    def s():
        hd61700.set_reg(0,0x33); hd61700.set_reg(1,0x44)
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(0,4)
    check("E1 STM IZ cnt=2 restore", [0xE1,0x00,0x20], s,
          [cm(0x6600,0x33), cm(0x6601,0x44), cr16(2,0x6600)])
    # --- 0xE3 STIM $,(IZ)+ ---
    def s():
        hd61700.set_reg(0,0x55); hd61700.set_reg(1,0x66)
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(0,4)
    check("E3 STIM IZ cnt=2", [0xE3,0x00,0x20], s,
          [cm(0x6600,0x55), cm(0x6601,0x66), cr16(2,0x6602)])
    # --- 0xE4 STDM $,(IX)- ---
    # STDM stores in decrementing order: REG_IX--, using READ_REG(arg--)
    def s():
        hd61700.set_reg(1,0x77); hd61700.set_reg(0,0x88)
        hd61700.set_reg(4,0)
        hd61700.set_reg16(0,0x6502); hd61700.set_sreg(0,4)
    # arg=0x01 (start at R1), ext=0x20 (cnt=2)
    check("E4 STDM IX cnt=2", [0xE4,0x01,0x20], s,
          [cm(0x6502,0x77), cm(0x6501,0x88)])
    # --- 0xE5 STDM $,(IZ)- ---
    def s():
        hd61700.set_reg(1,0x99); hd61700.set_reg(0,0xAA)
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6602); hd61700.set_sreg(0,4)
    check("E5 STDM IZ cnt=2", [0xE5,0x01,0x20], s,
          [cm(0x6602,0x99), cm(0x6601,0xAA)])


def tests_e6_ef(t, check):
    """0xE6-0xEF: Multi-byte stack, LDM/LDIM/LDDM IX/IZ"""
    # --- 0xE6 PHSM (push multi to SS) ---
    def s():
        hd61700.set_reg(0,0x11); hd61700.set_reg(1,0x22)
        hd61700.set_reg16(4,0x7F00)
    # PHSM pushes arg-n for n=0..cnt-1
    # arg=0x01 (start R1), ext=0x20 (cnt=2) → push R1, R0
    check("E6 PHSM cnt=2", [0xE6,0x01,0x20], s,
          [cm(0x7EFF,0x22), cm(0x7EFE,0x11)])
    # --- 0xE7 PHUM (push multi to US) ---
    def s():
        hd61700.set_reg(0,0x33); hd61700.set_reg(1,0x44)
        hd61700.set_reg16(3,0x7E00)
    check("E7 PHUM cnt=2", [0xE7,0x01,0x20], s,
          [cm(0x7DFF,0x44), cm(0x7DFE,0x33)])
    # --- 0xEE PPSM (pop multi from SS) ---
    def s():
        hd61700.set_reg16(4,0x7EFE)
        hd61700.write_mem(0x7EFE,0xAA); hd61700.write_mem(0x7EFF,0xBB)
    check("EE PPSM cnt=2", [0xEE,0x00,0x20], s,
          [cr(0,0xAA), cr(1,0xBB)])
    # --- 0xEF PPUM (pop multi from US) ---
    def s():
        hd61700.set_reg16(3,0x7DFE)
        hd61700.write_mem(0x7DFE,0xCC); hd61700.write_mem(0x7DFF,0xDD)
    check("EF PPUM cnt=2", [0xEF,0x00,0x20], s,
          [cr(0,0xCC), cr(1,0xDD)])
    # --- 0xE8 LDM (IX) restore cnt=2 ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0x11); hd61700.write_mem(0x6501,0x22)
    check("E8 LDM IX cnt=2 restore", [0xE8,0x00,0x20], s,
          [cr(0,0x11), cr(1,0x22), cr16(0,0x6500)])
    # --- 0xEA LDIM (IX)+ cnt=2 ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0x33); hd61700.write_mem(0x6501,0x44)
    check("EA LDIM IX cnt=2", [0xEA,0x00,0x20], s,
          [cr(0,0x33), cr(1,0x44), cr16(0,0x6502)])
    # --- 0xE9 LDM (IZ) restore cnt=2 ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6600,0x55); hd61700.write_mem(0x6601,0x66)
    check("E9 LDM IZ cnt=2 restore", [0xE9,0x00,0x20], s,
          [cr(0,0x55), cr(1,0x66), cr16(2,0x6600)])
    # --- 0xEB LDIM (IZ)+ cnt=2 ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6600); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6600,0x77); hd61700.write_mem(0x6601,0x88)
    check("EB LDIM IZ cnt=2", [0xEB,0x00,0x20], s,
          [cr(0,0x77), cr(1,0x88), cr16(2,0x6602)])
    # --- 0xEC LDDM (IX) cnt=2 (decrement) ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(0,0x6502); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6502,0xAA); hd61700.write_mem(0x6501,0xBB)
    # arg=0x01 (start R1), read R1<-[IX], IX--; R0<-[IX]
    check("EC LDDM IX cnt=2", [0xEC,0x01,0x20], s,
          [cr(1,0xAA), cr(0,0xBB)])
    # --- 0xED LDDM (IZ) cnt=2 ---
    def s():
        hd61700.set_reg(4,0)
        hd61700.set_reg16(2,0x6602); hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6602,0xCC); hd61700.write_mem(0x6601,0xDD)
    check("ED LDDM IZ cnt=2", [0xED,0x01,0x20], s,
          [cr(1,0xCC), cr(0,0xDD)])


def tests_f0_f7(t, check):
    """0xF0-0xF7: RTN conditional"""
    ret_addr = TB + 10  # address to return to
    # Store return address on stack and then test RTN
    # Stack stores (hi, lo) with push(SS, hi) then push(SS, lo)
    # RTN: lo=pop, hi=pop, pc = (hi<<8|lo)+1

    # --- 0xF7 RTN uncond ---
    def s():
        hd61700.set_reg16(4,0x7F00)
        # Push return address: ret_addr-1 because RTN does +1
        ra = ret_addr - 1
        ra = ret_addr - 1
        hd61700.write_mem(0x7EFE, ra & 0xFF)   # lo
        hd61700.write_mem(0x7EFF, ra >> 8)      # hi
        hd61700.set_reg16(4,0x7EFE)
        t.lc(ret_addr, [0xF8])
    check("F7 RTN uncond", [0xF7], s, [cp(ret_addr+1)], stop=ret_addr+1)
    # --- 0xF0 RTN Z (skip when Z=0) ---
    check("F0 RTN Z skip", [0xF0], None, [cp(TB+1)])
    # --- 0xF0 RTN Z (taken when Z=1) ---
    def s():
        hd61700.set_flags(0x80)
        hd61700.set_reg16(4,0x7F00)
        ra = ret_addr - 1
        hd61700.write_mem(0x7EFE, ra & 0xFF)
        hd61700.write_mem(0x7EFF, ra >> 8)
        hd61700.set_reg16(4,0x7EFE)
        t.lc(ret_addr, [0xF8])
    check("F0 RTN Z taken", [0xF0], s, [cp(ret_addr+1)], stop=ret_addr+1)
    # --- 0xF4 RTN NZ (taken when Z=0) ---
    def s():
        hd61700.set_reg16(4,0x7F00)
        ra = ret_addr - 1
        hd61700.write_mem(0x7EFE, ra & 0xFF)
        hd61700.write_mem(0x7EFF, ra >> 8)
        hd61700.set_reg16(4,0x7EFE)
        t.lc(ret_addr, [0xF8])
    check("F4 RTN NZ taken", [0xF4], s, [cp(ret_addr+1)], stop=ret_addr+1)
    # --- 0xF1 RTN NC (taken when C=0) ---
    def s():
        hd61700.set_reg16(4,0x7F00)
        ra = ret_addr - 1
        hd61700.write_mem(0x7EFE, ra & 0xFF)
        hd61700.write_mem(0x7EFF, ra >> 8)
        hd61700.set_reg16(4,0x7EFE)
        t.lc(ret_addr, [0xF8])
    check("F1 RTN NC taken", [0xF1], s, [cp(ret_addr+1)], stop=ret_addr+1)
    # --- 0xF5 RTN C (skip when C=0) ---
    check("F5 RTN C skip", [0xF5], None, [cp(TB+1)])


def tests_f8_ff(t, check):
    """0xF8-0xFF: NOP, CLT, FST, SLW, CANI, RTNI, OFF, TRP"""
    # --- 0xF8 NOP ---
    check("F8 NOP", [0xF8], None, [cp(TB+1)])
    # --- 0xF9 CLT ---
    def s(): hd61700.set_reg8(6,0xFF)  # TM = 0xFF
    check("F9 CLT TM&=C0", [0xF9], s,
          [lambda: (R8(6)==0xC0, f"TM={R8(6):02X}")])
    # --- 0xFA FST (set fast mode) ---
    check("FA FST", [0xFA], None, [cp(TB+1)])
    # --- 0xFB SLW (clear fast mode) ---
    check("FB SLW", [0xFB], None, [cp(TB+1)])
    # --- 0xFE OFF ---
    def s():
        hd61700.set_reg16(0,0x1234)  # IX
        hd61700.set_reg8(3,0x10)     # UA
    check("FE OFF sleep", [0xFE], s,
          [cslp(True), cr16(0,0), cr16(1,0), cr16(2,0)])
    # --- 0xFF TRP (trap to 0x6ffa) ---
    def s():
        hd61700.set_reg16(4,0x7F00)
    check("FF TRP -> 6FFA", [0xFF], s,
          [cp(0x6FFA)], stop=0x6FFA)
    # --- 0xFC CANI (clear and interrupt acknowledge) ---
    def s():
        hd61700.set_reg8(2, 0x1F)  # IB = all IRQ bits set
    check("FC CANI clears top IB", [0xFC], s,
          [lambda: ((R8(2)&0x10)==0, "IB=%03X" % R8(2))])
    # --- 0xFD RTNI (return from interrupt) ---
    def s():
        hd61700.set_reg16(4,0x7F00)
        # Push return address: 0x1234
        ra = 0x1234
        hd61700.write_mem(0x7EFE, ra & 0xFF)   # lo
        hd61700.write_mem(0x7EFF, ra >> 8)      # hi
        hd61700.set_reg16(4,0x7EFE)
        hd61700.set_reg8(2, 0x1F)  # IB = all IRQ bits set
    check("FD RTNI restores PC and clears top IB", [0xFD], s,
          [cp(0x1234), lambda: ((R8(2)&0x10)==0, "IB=%02X" % R8(2)), cslp(False)])


def run_tests():
    t = get_t()
    print("HD61700 CPU Test: 0xE0-0xFF")
    print("=" * 50)
    tp = 0; tf = 0
    for lbl, fn in [("0xE0-0xE5", tests_e0_e5), ("0xE6-0xEF", tests_e6_ef),
                     ("0xF0-0xF7", tests_f0_f7), ("0xF8-0xFF", tests_f8_ff)]:
        p, f = run_group(t, lbl, fn)
        tp += p; tf += f
    print("\n" + "=" * 50)
    print(f"Result: {tp} OK, {tf} NG")
    print("=" * 50)

run_tests()
