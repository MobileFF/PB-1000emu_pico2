import hd61700
import machine
import binascii

# Move test code to RAM area (Standard PB-1000 RAM: 0x6000-0x7FFF)
TEST_BASE_ADDR = 0x7000

class CPUTester:
    def __init__(self):
        # Initialize anchor list for GC protection
        try:
            hd61700._init_anchor()
        except AttributeError:
            pass

        # Anchor callbacks to prevent GC
        self._mem_read_cb = self._mem_read
        self._mem_write_cb = self._mem_write
        self._cbs = (self._mem_read_cb, self._mem_write_cb)
        
        # New C API to anchor objects
        try:
            hd61700._anchor_callbacks(self._cbs)
        except AttributeError:
             pass 
            
        hd61700.set_mem_callbacks(self._mem_read_cb, self._mem_write_cb)
        
        # By default, use direct C memory
        self.use_c_mem = True
        hd61700.use_c_memory(True)
        self.reset_context()

    def reset_context(self):
        hd61700.set_debug(False)
        hd61700.set_key_debug(False)
        hd61700.set_lcd_debug(False)
        
        # RAM area (0x6000-0x7FFF)
        self.ram = bytearray(0x2000)
        self.rom0 = bytearray(0x2000)
        self.rom1 = bytearray(0x8000)
        
        hd61700.reset(False)
        hd61700.use_c_memory(self.use_c_mem) # Ensure C-direct mode persists after reset
        
        # Initialize SIR registers to expected defaults for tests
        # SX=0, SY=1, SZ=2
        hd61700.set_sreg(0, 0) # SX
        hd61700.set_sreg(1, 1) # SY
        hd61700.set_sreg(2, 2) # SZ
        
        hd61700.load_rom(0, self.rom0)
        hd61700.load_rom(1, self.rom1)
        # Clear all RAM
        for i in range(0x2000):
            hd61700.write_mem(0x6000 + i, 0)

    def _mem_read(self, segment, offset):
        if offset < 0x2000:
            return self.rom0[offset]
        elif 0x6000 <= offset < 0x8000:
            return self.ram[offset - 0x6000]
        elif offset >= 0x8000:
            return self.rom1[offset - 0x8000]
        return 0xFF

    def _mem_write(self, segment, offset, data):
        if 0x6000 <= offset < 0x8000:
            self.ram[offset - 0x6000] = data

    def load_code(self, addr, code):
        for i, b in enumerate(code):
            hd61700.write_mem(addr + i, b)
            if 0x6000 <= addr + i < 0x8000:
                self.ram[addr + i - 0x6000] = b

    def run_test(self, name, code, setup_fn=None, verify_fn=None, start_pc=TEST_BASE_ADDR):
        print(f"Testing {name:35}... ", end="")
        self.reset_context()
        
        # Append a landing loop (JR -2 = 17 FE) to stabilize PC
        full_code = list(code)
        # But wait, 0xf8 (NOP) is 1 byte, 0x17 0xfe is 2 bytes.
        # It's safer to just provide code that ends in a known state.
        
        self.load_code(start_pc, full_code)
        hd61700.set_pc(start_pc)
        
        if setup_fn:
            setup_fn()
            
        try:
            # Determine stop PC if code ends in 0xf8 (NOP)
            stop_pc = -1
            if full_code[-1] == 0xf8:
                # Execution should stop at the last NOP or slightly after
                # Actually mod_execute stops WHEN it fetches an instruction at stop_pc
                # So if we want to include the NOP, stop_pc = last_pc + 1
                stop_pc = start_pc + len(full_code) - 1
            
            # Execute
            hd61700.execute(500, stop_pc) 
            
            if verify_fn:
                ok, msg = verify_fn()
                if ok:
                    print("PASS")
                    return True
                else:
                    print(f"FAIL ({msg})")
                    return False
            else:
                print("OK")
                return True
        except Exception as e:
            import sys
            import io
            f = io.StringIO()
            sys.print_exception(e, f)
            print(f"FAIL (Exception: {f.getvalue().splitlines()[-1]})")
            return False

# --- Enhanced helper to check flags ---
def check_flags(expected_z=None, expected_c=None, expected_lz=None, expected_uz=None):
    f = hd61700.get_flags()
    errs = []
    # FLAG_Z 0x80, FLAG_C 0x40, FLAG_LZ 0x20, FLAG_UZ 0x10
    if expected_z is not None:
        if bool(f & 0x80) != expected_z: errs.append(f"Z:{bool(f & 0x80)}!=exp:{expected_z}")
    if expected_c is not None:
        if bool(f & 0x40) != expected_c: errs.append(f"C:{bool(f & 0x40)}!=exp:{expected_c}")
    if expected_lz is not None:
        if bool(f & 0x20) != expected_lz: errs.append(f"LZ:{bool(f & 0x20)}!=exp:{expected_lz}")
    if expected_uz is not None:
        if bool(f & 0x10) != expected_uz: errs.append(f"UZ:{bool(f & 0x10)}!=exp:{expected_uz}")
    
    if errs:
        return False, f"Flags mismatch: {', '.join(errs)} (Flags: {f:02X})"
    return True, ""

# --- Rigorous Test Group: Check-Only Arithmetic & Register Preservation ---

def test_rigor_check_only(tester):
    results = []
    
    # CASE 1: SBC R0, R1 (Check Only) (0x01 0x20)
    # R0=5, R1=3. Expected: R0 remains 5, Flags update (5-3=2 -> Z=0, C=0, LZ=0, UZ=1)
    def verify_check_only():
        r0 = hd61700.get_reg(0)
        if r0 != 5: return False, f"R0 mod! got {r0:02X}"
        return check_flags(expected_z=False, expected_c=False, expected_lz=False, expected_uz=True)

    results.append(tester.run_test("SBC R0, R1 (Check Only)", 
        [0x42, 0x00, 0x05, 0x42, 0x01, 0x03, 0x01, 0x20, 0xf8], None, verify_check_only))

    # CASE 2: AN R0, #0x55 (Check Only) (0x44 0x00 0x55)
    # R0=0xAA. AA & 55 = 0. Expected: R0 remains AA, Z=1
    def verify_an_check():
        r0 = hd61700.get_reg(0)
        if r0 != 0xAA: return False, f"R0 mod! got {r0:02X}"
        return check_flags(expected_z=True)

    results.append(tester.run_test("AN R0, imm (Check Only)", 
        [0x42, 0x00, 0xAA, 0x44, 0x00, 0x55, 0xf8], None, verify_an_check))

    return all(results)

# --- Rigorous Test Group: Memory Interaction ---

def test_rigor_memory(tester):
    results = []
    
    def verify_mem_sync():
        val_c = hd61700.read_mem(0x6500)
        if val_c != 0x99: return False, f"C-direct mismatch: exp 99, got {val_c:02X}"
        return True, ""

    # ST R0, (offset) where offset = 0x6500
    # 0x56 0x20 (ST RA, (IX))? No, using 0x56 is for complex.
    # Simple ST R0, (addr16) is 0x11 addr_lo addr_hi
    # Actually [0x56, 0x20, 0x00, 0x65, 0x10, 0x00] ? Let's check.
    # Case 0x56: uint8_t arg = read_op(cpu); ... 
    # [0x42, 0x00, 0x99, 0x56, 0x20, 0x00, 0x65, 0x10, 0x00, 0xf8]
    # R0=99, IX=6500, ST R0, (IX)
    results.append(tester.run_test("Memory Write Sync (C-Direct)", 
        [0x42, 0x00, 0x99, 0x56, 0x10, 0x00, 0x65, 0x10, 0x00, 0xf8], None, verify_mem_sync))

    def setup_external_modify():
        hd61700.write_mem(0x6600, 0x77)

    def verify_external_read():
        r3 = hd61700.get_reg(3)
        if r3 != 0x77: return False, f"CPU read mismatch: exp 77, got {r3:02X}"
        return True, ""

    # LD R3, (addr16) is 0x11 (lo) (hi) (reg)
    # Wait, LD reg, (IX) is 0x10 (reg)
    results.append(tester.run_test("Memory Read Sync (External Mod)", 
        [0x56, 0x10, 0x00, 0x66, 0x10, 0x03, 0xf8], setup_external_modify, verify_external_read))

    return all(results)

# --- Functional Tests ---

def test_arithmetic(tester):
    results = []
    # R0=5, R1=3. AD R0, R1 -> R0=8. (Op 0x08, SY selected by Arg 0x20)
    results.append(tester.run_test("AD R0, R1 (5+3=8)", 
        [0x42, 0x00, 0x05, 0x42, 0x01, 0x03, 0x08, 0x20, 0xf8], 
        None, lambda: (hd61700.get_reg(0) == 8, f"got {hd61700.get_reg(0):02X}")))
    return all(results)

def test_logic(tester):
    results = []
    # AA & 55 = 0.
    results.append(tester.run_test("AN R0, R1 (AA&55=0)", 
        [0x42, 0x00, 0xAA, 0x42, 0x01, 0x55, 0x0C, 0x20, 0xf8], 
        None, lambda: (hd61700.get_reg(0) == 0, f"got {hd61700.get_reg(0):02X}")))
    return all(results)

def test_load_store(tester):
    results = []
    def verify_st():
        val = hd61700.read_mem(0x7FFF)
        return (val == 0x42, f"got {val:02X}")
    # IX=7FFF, ST R0, (IX)
    results.append(tester.run_test("ST R0, (IX)", 
        [0x42, 0x00, 0x42, 0x56, 0x10, 0xFF, 0x7F, 0x10, 0x00, 0xf8], None, verify_st))
    return all(results)

def test_bcd(tester):
    results = []
    # 19+7=26 BCD
    results.append(tester.run_test("ADB R0, R1 (19+7=26)", 
        [0x42, 0x00, 0x19, 0x42, 0x01, 0x07, 0x0A, 0x20, 0xf8], 
        None, lambda: (hd61700.get_reg(0) == 0x26, f"got {hd61700.get_reg(0):02X}")))
    return all(results)

def test_control_flow(tester):
    results = []
    # JP 0x7080. Place landing loop at 0x7080.
    target = 0x7080
    def setup_target():
        tester.load_code(target, [0x17, 0xFE]) # JR -2
    results.append(tester.run_test(f"JP 0x{target:04X}", 
        [0x37, target & 0xFF, (target >> 8) & 0xFF, 0xf8], setup_target, 
        lambda: (hd61700.get_pc() == target, f"got {hd61700.get_pc():04X}")))
    return all(results)

def test_shift_rotate(tester):
    results = []
    # 0x80 >> 1 = 0x40
    results.append(tester.run_test("BID R0 (Shift Right)", [0x42, 0x00, 0x80, 0x18, 0x40, 0xf8], None, 
        lambda: (hd61700.get_reg(0) == 0x40, f"got {hd61700.get_reg(0):02X}")))
    return all(results)

def test_block_ops(tester):
    results = []
    def setup_bup():
        hd61700.write_mem(0x6100, 0x11); hd61700.write_mem(0x6101, 0x22)
    def verify_bup():
        d0 = hd61700.read_mem(0x7100); d1 = hd61700.read_mem(0x7101)
        return (d0 == 0x11 and d1 == 0x22, f"got [{d0:02X},{d1:02X}]")
    # IX=6100, IY=6101, IZ=7100. BUP counter=2 -> [0xD8, 0x01]
    # Actually BUP uses the counter in KY? 
    # MAME: BUP uses arg to determine count if arg<32? No.
    # BUP 0xD8: count from KY?
    results.append(tester.run_test("BUP (Block Up)", 
        [0x56, 0x10, 0x00, 0x61, 0x56, 0x30, 0x01, 0x61, 0x56, 0x50, 0x00, 0x71, 0x42, 0x05, 0x02, 0xD8, 0xf8], 
        setup_bup, verify_bup))
    return all(results)

def test_stack_ops(tester):
    results = []
    # PHS R2; PPS R3. SS=0x7F00.
    results.append(tester.run_test("PHS/PPS R2->R3", 
        [0x42, 0x02, 0x42, 0x56, 0x80, 0x00, 0x7F, 0x26, 0x02, 0x2E, 0x03, 0xf8], 
        None, lambda: (hd61700.get_reg(3) == 0x42, f"got {hd61700.get_reg(3):02X}")))
    return all(results)

def test_complex_arithmetic(tester):
    results = []
    # ADC (Check Only) FF+1 -> Z=1, C=1, LZ=1, UZ=1. 
    # SY=1, R1=0x01.
    results.append(tester.run_test("ADC (Check Only) FF+1", 
        [0x42, 0x00, 0xFF, 0x42, 0x01, 0x01, 0x00, 0x20, 0xf8], 
        None, lambda: check_flags(expected_z=True, expected_c=True)))
    return all(results)

def main():
    tester = CPUTester()
    print("Starting REFINED HD61700 CPU Regression Suite\n")
    overall_pass = True
    print("[Rigor & Integration Tests]")
    overall_pass &= test_rigor_check_only(tester)
    overall_pass &= test_rigor_memory(tester)
    print("\n[Functional Tests]")
    overall_pass &= test_arithmetic(tester)
    overall_pass &= test_complex_arithmetic(tester)
    overall_pass &= test_logic(tester)
    overall_pass &= test_load_store(tester)
    overall_pass &= test_bcd(tester)
    overall_pass &= test_control_flow(tester)
    overall_pass &= test_shift_rotate(tester)
    overall_pass &= test_block_ops(tester)
    overall_pass &= test_stack_ops(tester)
    
    print("\n-------------------------------------------")
    if overall_pass: print("OVERALL RESULT: PASS")
    else: print("OVERALL RESULT: FAIL")
    print("-------------------------------------------")

if __name__ == "__main__":
    main()
