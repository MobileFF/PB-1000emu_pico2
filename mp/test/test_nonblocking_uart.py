import sys
# Mocking machine and rp2 for testing environment if needed
class MockPin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    def __init__(self, *args, **kwargs): pass

class MockStateMachine:
    def __init__(self, *args, **kwargs):
        self._fifo = []
    def active(self, x): pass
    def put(self, x):
        self._fifo.append(x)
    def tx_fifo(self):
        return len(self._fifo)
    def rx_fifo(self):
        return 0

sys.modules['machine'] = type('machine', (), {'Pin': MockPin})
sys.modules['rp2'] = type('rp2', (), {'StateMachine': MockStateMachine, 'asm_pio': lambda **kwargs: lambda f: f, 'PIO': type('PIO', (), {'OUT_HIGH': 1, 'SHIFT_RIGHT': 1})})

from pio_uart import PioUart
import time

def test_nonblocking_write():
    print("Testing non-blocking write...")
    uart = PioUart()
    
    # Large data that would normally block if sm.put was called directly 
    # (MockStateMachine has infinite FIFO but we test the buffer logic)
    large_data = b"Hello world! This is a long string to test the non-blocking buffer."
    
    # This should return immediately
    start = time.time()
    uart.write(large_data)
    end = time.time()
    
    print(f"Write returned in {end - start:.6f} seconds")
    assert end - start < 0.1, "Write took too long (blocked?)"
    assert len(uart._tx_buffer) == len(large_data), "Data not in buffer"
    
    # Manually service and check
    print("Servicing TX...")
    
    # Simulate FIFO logic:
    # Total capacity = 4
    # Filled = variable
    uart._sm_tx = MockStateMachine() 
    uart._sm_tx._filled = 3
    def limited_tx_fifo():
        return uart._sm_tx._filled
    uart._sm_tx.tx_fifo = limited_tx_fifo
    
    # Override put to increment _filled
    def mock_put(x):
        uart._sm_tx._fifo.append(x)
        uart._sm_tx._filled += 1
    uart._sm_tx.put = mock_put
    
    uart.service_tx()
    # Initial: buffer=67, filled=3. 
    # service_tx loop:
    # 1. 3 < 4? Yes. put byte 1. filled=4.
    # 2. 4 < 4? No. Exit.
    # Result: buffer=66, filled=4.
    assert len(uart._tx_buffer) == len(large_data) - 1, f"Expected {len(large_data)-1}, got {len(uart._tx_buffer)}"
    assert len(uart._sm_tx._fifo) == 1, f"Expected 1 byte in sm_tx fifo, got {len(uart._sm_tx._fifo)}"
    print("Success: Non-blocking write and partial service verified.")

if __name__ == "__main__":
    try:
        test_nonblocking_write()
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
