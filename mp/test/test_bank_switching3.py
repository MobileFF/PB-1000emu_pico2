
import hd61700
import machine
import os
import sys

def test_memory_instructions():
    print("\nTesting HD61700 Memory Banking Instructions ...")
    # ensure callbacks still used even if previous tests enabled C memory
    print("use c memory")
    
    print("has exp_ram")
    hd61700.set_has_exp_ram(True)
        
    # hd61700.set_mem_callbacks(tester.mem_read, tester.mem_write)
    hd61700.reset()
   
    test_code = bytearray([
        0x42, 0x1F, 0x00,       # LD $31,0
        0x55, 0x1F,             # PSR SX, &H1F
        0xD6, 0x00, 0x00, 0x61, # PRE IX, 0x6100
        0x42, 0x00, 0x55,       # LD  $0, 0x55
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x10,       # PST UA, &H10
        0xD6, 0x00, 0x00, 0x81, # PRE IX, 0x8100
        0x42, 0x00, 0xAA,       # LD  $0, 0xAA
        0x20, 0x00,             # ST  $0, (IX+$SX)
        0x56, 0x60, 0x10,       # PST UA, &H10
        0xD6, 0x00, 0x00, 0x82, # PRE IX, 0x8200
        0x42, 0x00, 0x34,       # LD  $0, 0x34
        0x42, 0x01, 0x12,       # LD  $1, 0x12
        0xA0, 0x00,             # STW $0, (IX+$SX)
        0xF7                    # RTN
    ])
          
    TEST_ADDR = 0x7000
    addr = 0
    for i in test_code:
        hd61700.write_mem(TEST_ADDR+addr,i)
        addr += 1
        
    hd61700.set_pc(TEST_ADDR)
    print(f"  Executing {len(test_code)} bytes of test code at {hex(TEST_ADDR)}...")
    hd61700.execute_steps(42)

    stb_6100 = hd61700.read_mem(0x6100)
    stb_8100 = hd61700.read_mem(0x8100,1)
    stw_8200 = [hd61700.read_mem(0x8200,1),hd61700.read_mem(0x8201,1)]

    print(f"    0x6100 = {stb_6100:02X}")
    print(f"    0x8100 = {stb_8100:02X}")
    print(f"    0x8200 = {stw_8200[0]:02X}")
    print(f"    0x8201 = {stw_8200[1]:02X}")

    print("  Checking recorded writes:")

    if stb_6100 == 0x55:
        print("  PASS: STB at 0x6100 (Bank 0) OK.")
    else:
        print(f"  FAIL: STB at 0x6100 failed. Got {stb_6100}")
        
    if stb_8100 == 0xAA:
        print("  PASS: STB at 0x8100 (Bank 1) OK.")
    else:
        print(f"  FAIL: STB at 0x8100 failed. Got {stb_8100}")
        
    # verify two byte-stores for STW
    if (len(stw_8200) == 2 and
        stw_8200[0] == 0x34 and
        stw_8200[1] == 0x12):
        print("  PASS: STW at 0x8200 (Bank 1) OK.")
    else:
        print(f"  FAIL: STW at 0x8200 failed. Got {stw_8200}")

if __name__ == "__main__":
    test_memory_instructions()

