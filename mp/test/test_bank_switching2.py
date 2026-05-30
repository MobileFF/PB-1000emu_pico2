
import hd61700
import machine
import os
import sys

# Add parent dir to sys.path to import pb1000
sys.path.append('..')
from pb1000 import PB1000System

class BankTest:
    def __init__(self):
        self.recorded_accesses = []
        
    def mem_read(self, segment, offset):
        self.recorded_accesses.append(('R', segment, offset))
        return 0
        
    def mem_write(self, segment, offset, data):
        self.recorded_accesses.append(('W', segment, offset, data))
"""
def test_stack_segment_fix():
    print("Testing HD61700 Stack Segment Fix (executing at 0x7000)...")
    
    print("  Initializing BankTest and anchoring...")
    tester = BankTest()
    # Ensure GC anchor list is initialized in C side
    
    print("  hd61700._init_anchor()")
    if hasattr(hd61700, "_init_anchor"):
        hd61700._init_anchor()
        
    tester.read_fn = tester.mem_read
    tester.write_fn = tester.mem_write
    
    print("  Calling set_mem_callbacks...")
    hd61700.set_mem_callbacks(tester.read_fn, tester.write_fn)
    
    print("  Calling reset...")
    hd61700.reset()
    
    # Test Code (Linear Byte Addressing Area)
    # 1. PRE SS, 0x7F00 (D7 00 00 7F) - Set stack to RAM
    # 2. PST UA, #1 (56 60 01) - Set Segment 1
    # 3. PHS R0 (26 00) - Push (should use UA=1)
    # 4. PPS R1 (2E 01) - Pop
    # 5. NOP (F8)
    code = bytearray([
        0xD7, 0x00, 0x00, 0x7F, 
        0x56, 0x60, 0x01, 
        0x26, 0x00, 
        0x2E, 0x01, 
        0xF8
    ])
    
    TEST_ADDR = 0x7000
    
    def mem_read_with_code(seg, off):
        if TEST_ADDR <= off < TEST_ADDR + len(code):
            return code[off - TEST_ADDR]
        return 0
        
    tester.rom_read = mem_read_with_code
    
    print("  Setting mem_callbacks with ROM reader...")
    hd61700.set_mem_callbacks(tester.rom_read, tester.write_fn)
    
    print(f"  Setting PC to {hex(TEST_ADDR)}...")
    hd61700.set_pc(TEST_ADDR)
    
    # Run
    if hasattr(hd61700, "execute_steps"):
        print("  Calling execute_steps(20)...")
        hd61700.execute_steps(20)
    else:
        print("  Calling execute(100)...")
        hd61700.execute(100)
    
    print("  Execution finished. Checking results...")
    # Verification
    push_access = None
    for acc in tester.recorded_accesses:
        if acc[0] == 'W':
            push_access = acc
            break
            
    if push_access:
        seg = push_access[1]
        print(f"  Push segment recorded: {seg}")
        if seg == 1:
            print("  PASS: Push used UA=1 correctly.")
        else:
            print(f"  FAIL: Push used segment {seg} (UA >> 2 bug likely).")
    else:
        print("  FAIL: No push access recorded.")
"""
def test_pb1000_bank_callback_fix():
    print("\nTesting PB1000 Callback Segment Pass-through...")
    # Initialize system (dummy display)
    class DummyDisplay:
        def fill_rect(self, *args): pass
        def pixel(self, *args): pass
        def _auto_setup_hw(self): pass
    
    # Create a dummy ram1.bin to trigger expansion
    try:
        with open("roms/ram1.bin", "wb") as f:
            f.write(bytearray(32768))
    except Exception as e:
        import sys
        sys.print_exception(e)
        #pass
        
        
    print("PB1000System(debug=True)")
    sys_emu = PB1000System(DummyDisplay(), debug=False)
    if not sys_emu.has_exp:
        print("  SKIP: ram1.bin not found, expanded RAM is disabled by design.")
        return
    
    # Simulate a write from C to Bank 1, Offset 0x8000
    # In a real scenario, UA=1 and CPU does write_mem.
    # The callback from C to Python is _mem_write(segment, offset, data).
    # We want to check if Python's _mem_write correctly land it in exp_ram.
    print("_mem_write")
    sys_emu._mem_write(1, 0x8000, 0x55)
    if sys_emu.exp_ram[0] == 0x55:
        print("  PASS: Bank 1 write landed in exp_ram.")
    else:
        print(f"  FAIL: Bank 1 write failed (Data: {hex(sys_emu.exp_ram[0])})")

    # Simulate a write from Python (via RAMView) to C
    # We want to check if the segment is passed to C's write_mem.
    print("  Testing 16-bit Word access...")
    sys_emu.ram[0] = 0xAA
    sys_emu.ram[1] = 0x55
    if sys_emu.ram[0] == 0xAA and sys_emu.ram[1] == 0x55:
        print("  PASS: Standard RAM 0x8000 writable.")
    else:
        print(f"  FAIL: Standard RAM 0x8000 write failed ({hex(sys_emu.ram[0])}, {hex(sys_emu.ram[1])})")
"""
def test_memory_instructions():
    print("\nTesting HD61700 Memory Instructions (LDB, STB, LDW, STW)...")
    tester = BankTest()
    hd61700.set_mem_callbacks(tester.mem_read, tester.mem_write)
    hd61700.reset()
    
    # 1. STB A, (IX) where IX=0x6100, UA=0
    # 2. STB B, (IY) where IY=0x8100, UA=1
    # 3. STW R0, (IZ) where IZ=0x6200, UA=0
    # 4. LDW (IZ), R2
    
    # Code:
    # PRE IX, 0x6100  (D6 00 00 61)
    # LD R0, 0x55     (00 55)
    # STB R0, (IX)    (F0 00) -> [UA=0, ADDR=0x6100] <= 0x55
    # PST UA, #1      (56 60 01)
    # PRE IY, 0x8100  (D6 20 00 81)
    # LD R0, 0xAA     (00 AA)
    # STB R0, (IY)    (F0 10) -> [UA=1, ADDR=0x8100] <= 0xAA
    # PST UA, #0      (56 60 00)
    # PRE IZ, 0x6200  (D6 40 00 62)
    # LD R0, 0x34     (00 34)
    # LD R1, 0x12     (01 12)
    # STW R0, (IZ)    (F2 20) -> [UA=0, ADDR=0x6200] <= 0x34, 0x12
    # NOP             (F8)
    
    test_code = bytearray([
        0x42, 0x1F, 0x00,       # LD $31,0
        0x55, 0x1F,             # PSR SX, &H1F
        0xD6, 0x00, 0x00, 0x61, # PRE IX, 0x6100
        0x42, 0x00, 0x55,       # LD  $0, 0x55
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x01,       # PST UA, 1
        0xD6, 0x00, 0x00, 0x81, # PRE IX, 0x8100
        0x42, 0x00, 0xAA,       # LD  $0, 0xAA
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x00,       # PST UA, 0
        0xD6, 0x40, 0x00, 0x62, # PRE IX, 0x6200
        0x42, 0x00, 0x34,       # LD  $0, 0x34
        0x42, 0x01, 0x12,       # LD  $1, 0x12
        0xA0, 0x00,             # STW $0, (IZ)
        0xF8                    # NOP
    ])
    
    TEST_ADDR = 0x7000
    def memory_reader(seg, off):
        if TEST_ADDR <= off < TEST_ADDR + len(test_code):
            return test_code[off - TEST_ADDR]
        return 0
        
    hd61700.set_mem_callbacks(memory_reader, tester.mem_write)
    hd61700.set_pc(TEST_ADDR)
    print(f"  Executing {len(test_code)} bytes of test code at {hex(TEST_ADDR)}...")
    hd61700.execute_steps(41)
    
    print("  Checking recorded writes:")
    stb_6100 = None
    stb_8100 = None
    stw_6200 = []
    
    for acc in tester.recorded_accesses:
        if acc[0] == 'W':
            _, seg, off, data = acc
            if off == 0x6100:
                stb_6100 = (seg, data)
            if off == 0x8100:
                stb_8100 = (seg, data)
            if off in (0x6200, 0x6201):
                stw_6200.append((seg, off, data))
    
    if stb_6100 == (0, 0x55):
        print("  PASS: STB at 0x6100 (Bank 0) OK.")
    else:
        print(f"  FAIL: STB at 0x6100 failed. Got {stb_6100}")
        print(f"    recorded={tester.recorded_accesses}")
        
    if stb_8100 == (1, 0xAA):
        print("  PASS: STB at 0x8100 (Bank 1) OK.")
    else:
        print(f"  FAIL: STB at 0x8100 failed. Got {stb_8100}")
        print(f"    recorded={tester.recorded_accesses}")
        
    # verify two byte-stores for STW
    if (len(stw_6200) == 2 and
        stw_6200[0][1] == 0x6200 and stw_6200[0][2] == 0x34 and
        stw_6200[1][1] == 0x6201 and stw_6200[1][2] == 0x12):
        print("  PASS: STW at 0x6200 (Bank 0) OK.")
    else:
        print(f"  FAIL: STW at 0x6200 failed. Got {stw_6200}")
        print(f"    recorded={tester.recorded_accesses}")
"""

if __name__ == "__main__":
    test_pb1000_bank_callback_fix()

