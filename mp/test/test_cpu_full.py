"""HD61700 CPU Test Suite v6 - compact for MicroPython"""
import hd61700
import gc
import force_gc


TB = 0x7000
_A = []
USE_C_MEMORY=True

class T:
    def __init__(self):
        _A.append(hd61700._init_anchor())
        self._mr = self._mem_read
        self._mw = self._mem_write
        
        _A.extend([self._mr, self._mw])
        hd61700.set_mem_callbacks(self._mr, self._mw)
        # Disable C-side memory for this unit test file so Python callbacks
        # control all accesses deterministically. The C core supports bank-1
        # when enabled and has_exp_ram is true.
        hd61700.use_c_memory(USE_C_MEMORY)
        # standard 8KB RAM at 0x6000-0x7FFF
        self.ram = bytearray(0x2000)
        # provide simple 32KB expanded RAM area for bank1 tests
        self.exp_ram = bytearray(0x8000)
        _A.append(self.ram)
        _A.append(self.exp_ram)
        self.rst()

    def rst(self):
        hd61700.reset(False)
        hd61700.use_c_memory(USE_C_MEMORY)
        hd61700.set_debug(False)
        hd61700.set_key_debug(False)
        hd61700.set_lcd_debug(False)
        for i in range(32): hd61700.set_reg(i, 0)
        for i in range(3): hd61700.set_sreg(i, i)
        for i in range(8): hd61700.set_reg16(i, 0)

    def _mem_read(self, seg, off):
        # seg is bank index from UA >>4 (0-3); ignore for base RAM
        if 0x6000 <= off < 0x8000:
            return self.ram[off - 0x6000]
        # bank1 expanded region
        if off >= 0x8000 and (seg & 0x03) == 1:
            idx = off - 0x8000
            if idx < len(self.exp_ram):
                return self.exp_ram[idx]
        return 0

    def _mem_write(self, seg, off, d):
        if 0x6000 <= off < 0x8000:
            self.ram[off - 0x6000] = d & 0xFF
            return
        if off >= 0x8000 and (seg & 0x03) == 1:
            idx = off - 0x8000
            if idx < len(self.exp_ram):
                self.exp_ram[idx] = d & 0xFF
                return

    def lc(self, addr, code):
        for i, b in enumerate(code):
            a = addr + i
            hd61700.write_mem(a, b)
            if 0x6000 <= a < 0x8000: self.ram[a - 0x6000] = b

    def ex(self, code, setup=None, pc=TB, stop=None):
        self.rst()
        gc.collect()
        self.lc(pc, code)
        hd61700.set_pc(pc)
        if setup: setup()
        sp = stop if stop else pc + len(code)
        hd61700.execute(500, sp)

def R(n): return hd61700.get_reg(n)
def F(): return hd61700.get_flags()
def M(a): return hd61700.read_mem(a)
def PC(): return hd61700.get_pc()

def run_tests():
    t = T()
    print("HD61700 CPU Test v6")
    print("=" * 40)
    total_p = 0; total_f = 0

    def check(name, code, setup, checks, stop=None):
        nonlocal total_p, total_f
        try:
            t.ex(code, setup, stop=stop)
            ok = True; msg = ""
            for c in checks:
                r = c()
                if r is not None and not r[0]:
                    ok = False; msg = r[1]; break
            if ok:
                print(f"  {name:32} OK")
                total_p += 1
            else:
                print(f"  {name:32} NG ({msg})")
                total_f += 1
        except Exception as e:
            print(f"  {name:32} EX ({e})")
            total_f += 1

    def cr(r, e): return lambda: (R(r)==e, f"R{r}={R(r):02X}!={e:02X}")
    def cm(a, e): return lambda: (M(a)==e, f"[{a:04X}]={M(a):02X}!={e:02X}")
    def cf(z=None, c=None):
        def _ck():
            f = F(); e = []
            if z is not None and bool(f&0x80)!=z: e.append(f"Z={bool(f&0x80)}")
            if c is not None and bool(f&0x40)!=c: e.append(f"C={bool(f&0x40)}")
            return (len(e)==0, ",".join(e)) if e else (True,"")
        return _ck
    def cp(e): return lambda: (PC()==e, f"PC={PC():04X}!={e:04X}")

    # ── 8-bit Arithmetic ──
    print("\n[8-bit Arith]")
    def s1(): hd61700.set_reg(0,5); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("ADC chk(5+3)", [0x00,0x20], s1, [cf(z=False,c=False)])

    def s2(): hd61700.set_reg(0,5); hd61700.set_reg(1,4); hd61700.set_sreg(1,1)
    check("SBC chk(5-4)", [0x01,0x20], s2, [cf(z=False,c=False)])

    def s3(): hd61700.set_reg(0,3); hd61700.set_reg(1,5); hd61700.set_sreg(1,1)
    check("SBC borrow(3-5)", [0x01,0x20], s3, [cf(c=True)])

    def s4(): hd61700.set_reg(0,5); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("AD(5+3=8)", [0x08,0x20], s4, [cr(0,8), cf(z=False,c=False)])

    def s5(): hd61700.set_reg(0,1); hd61700.set_reg(1,3); hd61700.set_sreg(1,1)
    check("SB(1-3=FE,C=1)", [0x09,0x20], s5, [cr(0,0xFE), cf(c=True)])

    def s6(): hd61700.set_reg(0,0x80); hd61700.set_reg(1,0x80); hd61700.set_sreg(1,1)
    check("AD overflow", [0x08,0x20], s6, [cr(0,0), cf(z=True,c=True)])

    def s7(): hd61700.set_reg(0,10)
    # use 0x48 to perform write-back
    check("AD imm(10+20)", [0x48,0x00,20], s7, [cr(0,30)])

    def s8(): hd61700.set_reg(0,1)
    # use 0x49 to perform write-back
    check("SB imm(1-2)", [0x49,0x00,2], s8, [cr(0,0xFF), cf(c=True)])

    def s9(): hd61700.set_reg(0,0x80)
    check("ADC imm chk(C)", [0x48,0x00,0x80], s9, [cf(c=True)])

    def s10(): hd61700.set_reg(0,5)
    check("SBC imm chk(Z)", [0x49,0x00,5], s10, [cf(z=True,c=False)])

    def s11(): hd61700.set_reg(0,0x15); hd61700.set_reg(1,0x27); hd61700.set_sreg(1,1)
    check("ADB(15+27=42)", [0x0A,0x20], s11, [cr(0,0x42)])

    def s12(): hd61700.set_reg(0,0x42); hd61700.set_reg(1,0x15); hd61700.set_sreg(1,1)
    check("SBB(42-15=27)", [0x0B,0x20], s12, [cr(0,0x27)])

    def s13(): hd61700.set_reg(0,0x99)
    check("ADB imm(99+01)", [0x4A,0x00,0x01], s13, [cr(0,0x00), cf(c=True)])

    # ── 8-bit Logic ──
    print("\n[8-bit Logic]")
    def l1(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0x55); hd61700.set_sreg(1,1)
    check("AN chk(AA&55=0)", [0x04,0x20], l1, [cf(z=True)])

    def l2(): hd61700.set_reg(0,0xFF); hd61700.set_reg(1,0x0F); hd61700.set_sreg(1,1)
    check("AN(FF&0F=0F)", [0x0C,0x20], l2, [cr(0,0x0F)])

    def l3(): hd61700.set_reg(0,0xAA); hd61700.set_reg(1,0xFF); hd61700.set_sreg(1,1)
    check("XR(AA^FF=55)", [0x0F,0x20], l3, [cr(0,0x55)])

    def l4(): hd61700.set_reg(0,0xFF)
    check("AN imm(FF&0F)", [0x4C,0x00,0x0F], l4, [cr(0,0x0F)])

    def l5(): hd61700.set_reg(0,0xA0)
    check("OR imm(A0|05)", [0x4E,0x00,0x05], l5, [cr(0,0xA5)])

    def l6(): hd61700.set_reg(0,0xFF)
    check("XR imm(FF^FF)", [0x4F,0x00,0xFF], l6, [cr(0,0x00), cf(z=True)])

    # ── Register Move ──
    print("\n[Reg Move]")
    def m1(): hd61700.set_reg(0,0); hd61700.set_reg(1,0xAB); hd61700.set_sreg(1,1)
    check("LD R0,R1", [0x02,0x20], m1, [cr(0,0xAB)])
    check("LD imm(55)", [0x42,0x00,0x55], None, [cr(0,0x55)])
    check("LD R3,imm(CC)", [0x42,0x03,0xCC], None, [cr(3,0xCC)])

    # ── Shift/Rotate ──
    print("\n[Shift/Rot]")
    def sh1(): hd61700.set_reg(0,0x55)
    check("BID(55>>1=2A)", [0x18,0x40], sh1, [cr(0,0x2A), cf(c=True)])

    def sh2(): hd61700.set_reg(0,0x80)
    check("BIU(80<<1=00)", [0x18,0x60], sh2, [cr(0,0x00), cf(c=True)])

    def sh3(): hd61700.set_reg(0,0xAB)
    check("DID(AB>>4=0A)", [0x1A,0x00], sh3, [cr(0,0x0A)])

    def sh4(): hd61700.set_reg(0,0xAB)
    check("DIU(AB<<4=B0)", [0x1A,0x20], sh4, [cr(0,0xB0)])

    def sh5(): hd61700.set_reg(0,1)
    check("CMP(~1+1=FF)", [0x1B,0x00], sh5, [cr(0,0xFF), cf(c=True)])

    def sh6(): hd61700.set_reg(0,0x55)
    check("INV(~55=AA)", [0x1B,0x40], sh6, [cr(0,0xAA), cf(c=True)])

    # ── Memory Access via SIR ──
    print("\n[Mem SIR]")
    # SX=4 ↁER4. REG_GET16(4) = R4|(R5<<8) = address
    def ms1():
        hd61700.set_reg(0,0x42)
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("ST R0,(R4:5)", [0x10,0x00], ms1, [cm(0x6500,0x42)])

    def ms2():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0xBE)
    check("LD R0,(R4:5)", [0x11,0x00], ms2, [cr(0,0xBE)])

    def ms3():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("ST imm,(R4:5)", [0x50,0x00,0x7F], ms3, [cm(0x6500,0x7F)])

    def ms4():
        hd61700.set_reg(0,0x34); hd61700.set_reg(1,0x12)
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
    check("STW R0:1,(R4:5)", [0x90,0x00], ms4, [cm(0x6500,0x34), cm(0x6501,0x12)])

    def ms5():
        hd61700.set_reg(4,0x00); hd61700.set_reg(5,0x65)
        hd61700.set_sreg(0,4)
        hd61700.write_mem(0x6500,0xAB); hd61700.write_mem(0x6501,0xCD)
    check("LDW R0:1,(R4:5)", [0x91,0x00], ms5, [cr(0,0xAB), cr(1,0xCD)])

    # SY=6 ↁER6:R7
    def ms6():
        hd61700.set_reg(0,0x77)
        hd61700.set_reg(6,0x00); hd61700.set_reg(7,0x66)
        hd61700.set_sreg(1,6)
    check("ST R0,(R6:7)", [0x10,0x20], ms6, [cm(0x6600,0x77)])

    # D1 arg=0 ↁEREG_PUT16(0) ↁER0:R1
    check("LDW imm R0:R1", [0xD1,0x00,0x34,0x12], None, [cr(0,0x34), cr(1,0x12)])

    # ── Indexed Mem via IX/IZ ──
    print("\n[Idx Mem]")
    # 0x20: ST (IX+offset), arg=0x20 (sec=1→SY=1→R1 as offset)
    def ix1():
        hd61700.set_reg(0,0x55); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
    check("ST (IX+),R0", [0x20,0x20], ix1, [cm(0x6500,0x55)])

    def ix2():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
        hd61700.set_reg16(0,0x6500); hd61700.set_sreg(1,1)
        hd61700.write_mem(0x6500,0xAA)
    check("LD R0,(IX+)", [0x28,0x20], ix2, [cr(0,0xAA)])

    # ── Stack ──
    print("\n[Stack]")
    def sk1():
        hd61700.set_reg(0,0xAB); hd61700.set_reg16(4,0x7F00)
    check("PUSH/POP SS", [0x26,0x00,0x2E,0x00], sk1, [cr(0,0xAB)])

    def sk2():
        hd61700.set_reg(0,0xCD); hd61700.set_reg16(3,0x7E00)
    check("PUSH/POP US", [0x27,0x00,0x2F,0x00], sk2, [cr(0,0xCD)])

    # ── Jumps ──
    print("\n[Jumps]")
    tgt = 0x7080
    def jp1(): t.lc(tgt, [0xF8])
    check("JP uncond", [0x37,tgt&0xFF,tgt>>8], jp1, [cp(tgt+1)], stop=tgt+1)

    def jp2(): hd61700.set_reg(0,1); t.lc(tgt,[0xF8])
    check("JP Z skip", [0x30,0x80,0x70], jp2, [cp(TB+3)])

    def jp3(): hd61700.set_reg(0,1); t.lc(tgt,[0xF8])
    check("JP NZ taken", [0x34,0x80,0x70], jp3, [cp(tgt+1)], stop=tgt+1)

    def jp4(): hd61700.set_reg16(4,0x7F00); t.lc(tgt,[0xF8])
    check("CAL uncond", [0x77,0x80,0x70], jp4, [cp(tgt+1)], stop=tgt+1)

    # CAL+RTN combined
    def jp5():
        hd61700.set_reg16(4,0x7F00)
        t.lc(TB, [0x77,0x80,0x70])
        t.lc(tgt, [0xF7])
        t.lc(TB+3, [0xF8])
    t.ex([0xF8], jp5, stop=TB+4)  # dummy code, setup overrides
    ok = PC()==TB+4
    print(f"  {'CAL+RTN':32} {'OK' if ok else 'NG'}")
    if ok: total_p += 1
    else: total_f += 1

    # ── PRE/GRE ──
    print("\n[PRE/GRE]")
    def pr1(): hd61700.set_reg(0,0x34); hd61700.set_reg(1,0x12)
    check("PRE IX", [0x96,0x00], pr1,
        [lambda: (hd61700.get_reg16(0)==0x1234, f"IX={hd61700.get_reg16(0):04X}")])

    check("PRE IX imm", [0xD6,0x00,0xCD,0xAB], None,
        [lambda: (hd61700.get_reg16(0)==0xABCD, f"IX={hd61700.get_reg16(0):04X}")])

    # GET_REG_IDX(0xD6,0x20) = ((0&1)<<2)|((0x20>>5)&3) = 0|1 = 1 ↁEIY
    check("PRE IY imm", [0xD6,0x20,0x56,0x78], None,
        [lambda: (hd61700.get_reg16(1)==0x7856, f"IY={hd61700.get_reg16(1):04X}")])

    def gr1(): hd61700.set_reg16(0,0x5678)
    check("GRE IX→R0:R1", [0x9E,0x00], gr1, [cr(0,0x78), cr(1,0x56)])
    # edge cases: target register pair crosses 0x?F boundary
    def gr_edge1(): hd61700.set_reg16(0,0x1234)
    # arg=0x0F means destination R15:R16; high‑byte sits in register 0x10
    check("GRE IX→R15:R16", [0x9E,0x0F], gr_edge1,
        [cr(15,0x34), cr(16,0x12)])
    def gr_edge2(): hd61700.set_reg16(0,0xABCD)
    # another boundary: arg=0x1F ↁER31:R00 (wrap wrap)
    check("GRE IX→R31:R00", [0x9E,0x1F], gr_edge2,
        [cr(31,0xCD), cr(0,0xAB)])
    # ── 16-bit Arith ──
    print("\n[16-bit Arith]")
    # arg=0x60 (sec=3,reg=0), src=2 ↁER2:R3
    def w1():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x02); hd61700.set_reg(3,0x00)
    check("ADW(1+2=3)", [0x88,0x60,0x02], w1, [cr(0,0x03), cr(1,0x00)])

    def w2():
        hd61700.set_reg(0,0x05); hd61700.set_reg(1,0x00)
        hd61700.set_reg(2,0x03); hd61700.set_reg(3,0x00)
    check("SBW(5-3=2)", [0x89,0x60,0x02], w2, [cr(0,0x02), cr(1,0x00)])

    def w3():
        hd61700.set_reg(0,0x01); hd61700.set_reg(1,0x00)
    check("CMPW(~1+1=FFFF)", [0x9B,0x00], w3, [cr(0,0xFF), cr(1,0xFF)])

    # regression test: LDW instruction must consume optional JR offset
    def lj():
        hd61700.set_reg(0,0); hd61700.set_reg(1,0)
    # bytes: LDW r0:r1,r0 with JR+3, followed by two LD imm ops
    code = [0x82, 0x80, 0x00, 0x03, 0x42, 0x00, 0xAA, 0x42, 0x00, 0x55]
    check("LDW+JR consumes offset", code, lj, [cr(0,0x55)])

    # ensure debugger shows '?' when JR byte missing
    from debug import decode_basic
    def dt():
        pass
    # supply only opcode and arg; no third byte available
    small = [0x82, 0x80]
    result = decode_basic(small, TB)
    check("decode_missing_jr", [], dt, [lambda: ("JR ?" in result, f"got '{result}'")])

    # ── UA/bank switching regression ──
    print("\n[UA banking]")
    def bankp():
        pass
    # PST UA,#$10 ; PRE IX,#$FF70 ; LD R0,#$AA ; ST (IX+),R0 ; PRE IX,#$FF70 ; LD R0,(IX+)
    # PRE IX (0xD6) uses GET_REG_IDX(op,arg)=0 when arg bits 5-6 are 0b00.
    # Therefore arg=0x00 selects IX.
    bank_prog = [0x56,0x60,0x10,       # PST	UA,&H10
                 0xD6,0x00,0x70,0xFF,  # PRE	IX,&HFF70
                 0x42,0x00,0xAA,       # LD		$0,&HAA
                 0x20,0x00,            # ST		$0,(IX+$31)
                 0xD6,0x00,0x70,0xFF,  # PRE	IX,&HFF70
                 0x28,0x00             # LD		$0,(IX+$31)
                 ]
    check("UA bank1 write/read", bank_prog, bankp,
          [lambda: (hd61700.get_reg(0)==0xAA, f"$0={hd61700.get_reg(0):02X}")])

    # ── GST/PST ──
    print("\n[GST/PST]")
    def gp1(): hd61700.set_reg(0,5)
    check("PST SIR(R0→SX)", [0x15,0x00], gp1,
        [lambda: (hd61700.get_sreg(0)==5, f"SX={hd61700.get_sreg(0)}")])

    def gp2(): hd61700.set_sreg(0,7)
    check("GST SIR(SX→R0)", [0x1D,0x00], gp2, [cr(0,7)])

    # ── 0xD instructions ──
    print("\n[0xD instructions]")
    def d_setup():
        hd61700.set_reg(0, 0x11); hd61700.set_reg(1, 0x12); hd61700.set_reg(2, 0x13)
        hd61700.set_sreg(0, 0); hd61700.set_sreg(1, 0); hd61700.set_sreg(2, 0)
    
    # PFLM R0, 3 (idx=0) -> R0->PE, R1->PD, R2->IB
    # 0xD4 ARG(00) EXT(40 -> count 3)
    check("PFLM R0,3", [0xD4, 0x00, 0x40], d_setup,
        [lambda: (hd61700.get_reg8(0)==0x11, f"PE={hd61700.get_reg8(0):02X}"),
         lambda: (hd61700.get_reg8(1)==0x12, f"PD={hd61700.get_reg8(1):02X}")])

    # PSRM R0, 3 (idx=0) -> R0->SX, R1->SY, R2->SZ
    # 0xD5 ARG(00) EXT(40 -> count 3)
    check("PSRM R0,3", [0xD5, 0x00, 0x40], d_setup,
        [lambda: (hd61700.get_sreg(0)==0x11 & 0x1f, f"SX={hd61700.get_sreg(0):02X}"),
         lambda: (hd61700.get_sreg(1)==0x12 & 0x1f, f"SY={hd61700.get_sreg(1):02X}"),
         lambda: (hd61700.get_sreg(2)==0x13 & 0x1f, f"SZ={hd61700.get_sreg(2):02X}")])

    # LDW imm + JR (Positive)
    # bytes: [D1 80 34 12 02] [F8] [F8] [42 00 55]
    # TB..TB+4: Op, Arg, Lo, Hi, Offset(02)
    # pc at end of read_op(Offset) is TB+5.
    # new_pc = (TB+5) + 2 - 1 = TB+6.
    # lands on index 6 (second F8). Skips index 5 (first F8).
    # Then executes TB+7 (42 00 55) which loads R0.
    code_jr_pos = [0xD1, 0x80, 0x34, 0x12, 0x02, 0xF8, 0xF8, 0x42, 0x00, 0x55]
    check("LDW imm + JR positive", code_jr_pos, None, [cr(0, 0x55)])

    # LDW imm + JR (Negative)
    # Target: verify that it can jump back to a previously skipped section.
    # Index 0..2:   LD R0, 0 (42 00 00)
    # Index 3..5:   JP index 12 (37 0C 70) -> Skip index 6..11
    # Index 6..8:   LD R0, 1 (42 00 01)  <-- Target of Negative Jump
    # Index 9..11:  JP index 17 (37 11 70) -> Exit
    # Index 12..16: LDW R2:3, 1234 + JR(-10) (D1 82 34 12 8A)
    # Index 17:     NOP (F8)
    # Calculation: offset = -10 (0x80 - 0x8A). PC after 0x8A is TB+17.
    # new_pc = 17 + (-10) - 1 = index 6. Success!
    code_jr_neg = [0x42, 0x00, 0x00, 0x37, 0x0C, 0x70, 
                   0x42, 0x00, 0x01, 0x37, 0x11, 0x70, 
                   0xD1, 0x82, 0x34, 0x12, 0x8A, 0xF8]
    def setup_jr_neg(): hd61700.set_reg(0, 0xFF)
    check("LDW imm + JR negative", code_jr_neg, setup_jr_neg, [cr(0, 0x01), cp(TB+18)])

    check("CLT", [0xF9], None, [cp(TB+1)])

    print("\n" + "=" * 40)
    print(f"Result: {total_p} OK, {total_f} NG")
    print("=" * 40)

run_tests()
