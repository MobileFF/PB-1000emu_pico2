"""
PIO UART - Software UART via RP2350's PIO for RS-232C passthrough
Provides TX and RX at configurable baud rates (default 9600bps).

Pins: GP6 (TX), GP13 (RX)
"""
import rp2
from machine import Pin


# PIO UART TX program
# Sends 8N1: start bit, 8 data bits (LSB first), stop bit
@rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH,
             out_shiftdir=rp2.PIO.SHIFT_RIGHT, autopull=False)
def uart_tx_prog():
    pull()               .side(1)       # Wait for data in TX FIFO
    set(x, 7)            .side(0) [7]   # Start bit (low), set bit counter
    label("tx_bitloop")
    out(pins, 1)                  [6]   # Shift out 1 data bit
    jmp(x_dec, "tx_bitloop")             # Loop 8 times
    nop()                .side(1) [6]   # Stop bit (high)


# PIO UART RX program
# Receives 8N1: detects start bit, samples 8 data bits (LSB first)
@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT, autopush=False)
def uart_rx_prog():
    wait(0, pin, 0)                     # Wait for start bit (low)
    set(x, 7)                    [11]   # Delay to middle of first data bit (1 + 11 = 12 cycles = 1.5 bits)
    label("rx_bitloop")
    in_(pins, 1)                        # Sample 1 data bit
    jmp(x_dec, "rx_bitloop")     [6]    # Loop 8 times (1 + 1 + 6 = 8 cycles = 1 bit)
    push()                              # Push 8-bit result to RX FIFO
    # Wait for stop bit to finish (high)
    wait(1, pin, 0)


class PioUart:
    """Software UART using RP2350 PIO state machines."""

    def __init__(self, tx_pin=6, rx_pin=13, baudrate=9600, sm_tx=4, sm_rx=5):
        """
        Args:
            tx_pin: GPIO number for TX output
            rx_pin: GPIO number for RX input
            baudrate: Baud rate (default 9600)
            sm_tx: PIO state machine index for TX (0-7)
            sm_rx: PIO state machine index for RX (0-7)
        """
        self._baudrate = baudrate
        self._tx_pin = tx_pin
        self._rx_pin = rx_pin
        self._sm_tx_id = sm_tx
        self._sm_rx_id = sm_rx
        self._tx_buffer = []
        self._rx_buffer = []

        # TX state machine
        self._sm_tx = rp2.StateMachine(
            sm_tx, uart_tx_prog,
            freq=8 * baudrate,
            sideset_base=Pin(tx_pin),
            out_base=Pin(tx_pin),
        )
        self._sm_tx.active(1)

        # RX state machine
        self._sm_rx = rp2.StateMachine(
            sm_rx, uart_rx_prog,
            freq=8 * baudrate,
            in_base=Pin(rx_pin, Pin.IN, Pin.PULL_UP),
            jmp_pin=Pin(rx_pin, Pin.IN, Pin.PULL_UP),
        )
        self._sm_rx.active(1)

    def write(self, data):
        """Write bytes to PIO UART TX.
        
        Args:
            data: bytes, bytearray, or int (single byte)
        """
        if isinstance(data, int):
            self._tx_buffer.append(data & 0xFF)
        else:
            self._tx_buffer.extend(data)

    def service_tx(self):
        """Push buffered TX data to PIO FIFO if space is available."""
        while len(self._tx_buffer) > 0 and self._sm_tx.tx_fifo() < 4:
            self._sm_tx.put(self._tx_buffer.pop(0))

    def service_rx(self):
        """Pull data from PIO RX FIFO to software buffer."""
        count = 0
        while self._sm_rx.rx_fifo() > 0:
            val = self._sm_rx.get() >> 24
            self._rx_buffer.append(val & 0xFF)
            count += 1
        if count > 0:
            # Optional: Debug log to console (can be noisy, so keep it short)
            print(f"RX: {self._rx_buffer[-count:]}")
            pass

    def read(self, nbytes=1):
        """Read up to nbytes from software buffer.
        
        Returns:
            bytes object, or None if no data available
        """
        # First, try to pull from FIFO to catch latest data
        self.service_rx()

        if not self._rx_buffer:
            return None
        
        chunk_size = min(nbytes, len(self._rx_buffer))
        result = bytearray()
        for _ in range(chunk_size):
            result.append(self._rx_buffer.pop(0))
        return bytes(result)

    def any(self):
        """Return number of bytes available in software buffer and FIFO."""
        return len(self._rx_buffer) + self._sm_rx.rx_fifo()

    def set_baudrate(self, baudrate):
        """Change baud rate dynamically."""
        if baudrate == self._baudrate:
            return
        self._baudrate = baudrate
        # Restart state machines with new frequency
        self._sm_tx.active(0)
        self._sm_rx.active(0)

        self._sm_tx = rp2.StateMachine(
            self._sm_tx_id, uart_tx_prog,
            freq=8 * baudrate,
            sideset_base=Pin(self._tx_pin),
            out_base=Pin(self._tx_pin),
        )
        self._sm_rx = rp2.StateMachine(
            self._sm_rx_id, uart_rx_prog,
            freq=8 * baudrate,
            in_base=Pin(self._rx_pin, Pin.IN, Pin.PULL_UP),
            jmp_pin=Pin(self._rx_pin, Pin.IN, Pin.PULL_UP),
        )
        self._sm_tx.active(1)
        self._sm_rx.active(1)

    def clear_buffers(self):
        """Clear software TX and RX buffers."""
        self._tx_buffer = []
        self._rx_buffer = []
        # Note: Clearing hardware FIFOs is tricky but possible by deactivating/activating
        self._sm_tx.active(0)
        self._sm_rx.active(0)
        # Drain hardware FIFOs if they were stuck (though active(0) usually helps)
        while self._sm_rx.rx_fifo() > 0:
            self._sm_rx.get()
        self._sm_tx.active(1)
        self._sm_rx.active(1)

    def deinit(self):
        """Stop PIO state machines."""
        self._sm_tx.active(0)
        self._sm_rx.active(0)
