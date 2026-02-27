
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

def test_pb1000_bank_callback_fix():
    print("\nTesting PB1000 Callback Segment Pass-through...")
    # Initialize system (dummy display)
    class DummyDisplay:
        def fill_rect(self, *args): pass
        def pixel(self, *args): pass
    
    # Create a dummy ram1.bin to trigger expansion
    try:
        with open("roms/ram1.bin", "wb") as f:
            f.write(bytearray(32768))
    except Exception as e:
        import sys
        sys.print_exception(e)
        #pass
        
        
    print("PB1000System()")
    sys_emu = PB1000System(DummyDisplay(), debug=False)
    sys_emu.has_exp = True # Force enable
    
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
    # We can't easily check C side state without a custom C module or debug.
    # But we can verify the API call signature in our mind - we already added segment.
    print("  (Note: Python-to-C segment passing depends on updated C module which we just modified)")

if __name__ == "__main__":
    test_stack_segment_fix()
    test_pb1000_bank_callback_fix()
