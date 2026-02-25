import hd61700
import machine
import time

class CPUTester:
    def __init__(self):
        self.mem = bytearray(65536)
        hd61700.set_mem_callbacks(self._mem_read, self._mem_write)
        self.reset()

    def reset(self):
        for i in range(len(self.mem)):
            self.mem[i] = 0
        hd61700.reset()

    def _mem_read(self, segment, offset):
        phys = ((segment & 0x03) << 16) | offset if offset >= 0x8000 else offset
        return self.mem[phys % len(self.mem)]

    def _mem_write(self, segment, offset, data):
        phys = ((segment & 0x03) << 16) | offset if offset >= 0x8000 else offset
        self.mem[phys % len(self.mem)] = data

    def load_code(self, addr, code):
        for i, b in enumerate(code):
            self.mem[addr + i] = b

    def run_test(self, name, code, setup_fn=None, verify_fn=None):
        print(f"Testing {name:20}... ", end="")
        self.reset()
        self.load_code(0x0000, code)
        
        if setup_fn:
            setup_fn()
            
        try:
            hd61700.execute(len(code) * 20)
            if verify_fn:
                if verify_fn():
                    print("PASS")
                else:
                    print("FAIL (Verification Error)")
            else:
                print("OK")
        except Exception as e:
            print(f"FAIL (Exception: {e})")

# --- Setup and Verify Functions ---

def verify_add():
    # R0 = 0x08, R1 = 0x03. R0 should be 0x08.
    return hd61700.get_reg(0) == 0x08 and (hd61700.get_flags() & 0x80) == 0

def verify_flags_z():
    # After SB R0, 5 where R0 was 5, Z should be 1.
    return (hd61700.get_flags() & 0x80) != 0

def verify_stack():
    # R3 should be 0x42
    return hd61700.get_reg(3) == 0x42

def verify_jp():
    # PC should be 0x0100
    return hd61700.get_pc() == 0x0100

def verify_banking():
    # Just checking if it runs without crashing for now
    return True 

# --- Test Runner ---

def main():
    tester = CPUTester()
    
    # 1. Basic Arithmetic: LD R0, 5; LD R1, 3; AD R0, R1 (0x08 0x20)
    tester.run_test("AD R0, R1", [0x42, 0x00, 0x05, 0x42, 0x01, 0x03, 0x08, 0x20, 0xf8], None, verify_add)

    # 2. CMP (Zero Flag): LD R0, 5; LD R1, 5; SB R0, R1 (0x09 0x20) -> Z=1
    tester.run_test("CMP (Zero Flag)", [0x42, 0x00, 0x05, 0x42, 0x01, 0x05, 0x09, 0x20, 0xf8], None, verify_flags_z)

    # 3. Stack: LD SS, 0x7000; LD R2, 0x42; PHS R2; PPS R3
    # PRE SS, 0x00, 0x70 (0xD6 0x80 0x00 0x70)
    tester.run_test("PHS/PPS R2->R3", [0x42, 0x02, 0x42, 0xD6, 0x80, 0x00, 0x70, 0x26, 0x02, 0x2E, 0x03, 0xf8], None, verify_stack)

    # 4. Branching: LD R0, 5; LD R1, 5; SB R0, R1 (Z=1); JP Z, 0x0100
    tester.run_test("JP Z (Branch)", [0x42, 0x00, 0x05, 0x42, 0x01, 0x05, 0x09, 0x20, 0x30, 0x00, 0x01, 0xf8], None, verify_jp)

    # 5. Banking check
    tester.run_test("Banking (UA Set)", [0x56, 0x60, 0x04, 0xf8], None, verify_banking)

    print("\nTest Suite Completed.")

if __name__ == "__main__":
    main()
