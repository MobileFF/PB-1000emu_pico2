"""
PIO UART - Software UART via RP2350's PIO for RS-232C passthrough
Provides TX and RX at configurable baud rates (default 9600bps).

Pins: configurable via tx_pin/rx_pin (default GP6 TX / GP13 RX); see
`[rs232c] tx_pin`/`rx_pin` in pb1000.ini (main_boot.py's
initialize_usb_host_and_pio() reads these and passes them through here).
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


class _RingBuffer:
    """Fixed-size circular byte buffer backed by one pre-allocated bytearray.

    Replaces the previous plain-`list` TX/RX buffers (`append()` +
    `pop(0)`), which reallocated/shifted their whole contents on every
    push and pop -- identified as a steady source of per-character GC
    churn while typing or transferring data over the PIO UART (see the
    project's chronic-heap-pressure survey memory, 2026-08). `push()`/
    `pop()` here do zero heap allocation; the buffer itself is allocated
    once, at construction.
    """
    def __init__(self, size):
        self._buf = bytearray(size)
        self._size = size
        self._head = 0   # next write index
        self._tail = 0   # next read index
        self._count = 0

    def __len__(self):
        return self._count

    def push(self, byte):
        """Push one byte. Returns False (byte dropped) if full -- matches
        real UART FIFO overflow behavior (drop incoming, keep what's
        already buffered) rather than silently evicting older data."""
        if self._count == self._size:
            return False
        self._buf[self._head] = byte & 0xFF
        self._head = (self._head + 1) % self._size
        self._count += 1
        return True

    def pop(self):
        """Pop and return one byte (int), or None if empty. No allocation."""
        if self._count == 0:
            return None
        b = self._buf[self._tail]
        self._tail = (self._tail + 1) % self._size
        self._count -= 1
        return b

    def pop_bytes(self, n):
        """Pop up to n bytes as a new `bytes` object. Only path that still
        allocates -- used by the general read(nbytes) API, not the
        single-byte hot path (see PioUart.read_byte())."""
        n = min(n, self._count)
        if n == 0:
            return b""
        out = bytearray(n)
        for i in range(n):
            out[i] = self._buf[self._tail]
            self._tail = (self._tail + 1) % self._size
        self._count -= n
        return bytes(out)

    def peek_recent(self, n):
        """Return up to the n most recently pushed bytes as a list, without
        consuming them. Only used by the rare debug prints below."""
        n = min(n, self._count)
        idx = (self._head - n) % self._size
        return [self._buf[(idx + i) % self._size] for i in range(n)]

    def clear(self):
        self._head = 0
        self._tail = 0
        self._count = 0


class PioUart:
    """Software UART using RP2350 PIO state machines."""

    def __init__(self, tx_pin=6, rx_pin=13, baudrate=9600, sm_tx=4, sm_rx=5, buf_size=2048):
        """
        Args:
            tx_pin: GPIO number for TX output
            rx_pin: GPIO number for RX input
            baudrate: Baud rate (default 9600). The real PB-1000's RS-232C interface only
                supports 300-9600bps (see setup_menu.py's schema, which enforces this range
                for the [rs232c] baudrate ini key); values outside that range don't
                correspond to any real PB-1000 mode.
            sm_tx: PIO state machine index for TX (0-7)
            sm_rx: PIO state machine index for RX (0-7)
            buf_size: software TX/RX ring buffer size in bytes. 2048 comfortably covers
                several seconds of buffering even at the PB-1000's max 9600bps, while still
                being a single small, one-time allocation rather than per-byte churn.
        """
        self._baudrate = baudrate
        self._tx_pin = tx_pin
        self._rx_pin = rx_pin
        self._sm_tx_id = sm_tx
        self._sm_rx_id = sm_rx
        self._tx_buffer = _RingBuffer(buf_size)
        self._rx_buffer = _RingBuffer(buf_size)
        self._rx_total = 0
        self._rx_overflow_logged = False
        self._tx_overflow_logged = False

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
            if not self._tx_buffer.push(data & 0xFF) and not self._tx_overflow_logged:
                self._tx_overflow_logged = True
                print("[PIO_TX] buffer full, dropping data")
        else:
            for b in data:
                if not self._tx_buffer.push(b) and not self._tx_overflow_logged:
                    self._tx_overflow_logged = True
                    print("[PIO_TX] buffer full, dropping data")
                    break

    def service_tx(self):
        """Push buffered TX data to PIO FIFO if space is available."""
        while len(self._tx_buffer) > 0 and self._sm_tx.tx_fifo() < 4:
            self._sm_tx.put(self._tx_buffer.pop())

    def service_rx(self):
        """Pull data from PIO RX FIFO to software buffer."""
        count = 0
        while self._sm_rx.rx_fifo() > 0:
            val = (self._sm_rx.get() >> 24) & 0xFF
            if not self._rx_buffer.push(val):
                if not self._rx_overflow_logged:
                    self._rx_overflow_logged = True
                    print("[PIO_RX] buffer full, dropping data")
                break
            count += 1
        if count > 0:
            prev_total = self._rx_total
            self._rx_total += count
            # Log first byte received (confirms GP13 is getting signal at correct baud rate)
            if prev_total == 0:
                print(f"[PIO_RX] First data @ {self._baudrate}bps: {count}B buf={self._rx_buffer.peek_recent(4)}")
            elif (self._rx_total >> 8) != (prev_total >> 8):
                print(f"[PIO_RX] {self._rx_total}B total")
            if 26 in self._rx_buffer.peek_recent(count):
                print("[EOF Received]")
                return "[EOF Received]"

    def read_byte(self):
        """Pop and return a single received byte (int), or None if none
        available. Zero heap allocation -- prefer this over read(1) on any
        per-step/per-poll hot path. pb1000.py's _read_io_register() (index
        2, the UART data register) calls this once per ROM MMIO read of
        that register, which can happen hundreds of times per second
        during an active RS-232C transfer; read(1) would have allocated a
        fresh bytearray + bytes object on every single one of those calls."""
        self.service_rx()
        return self._rx_buffer.pop()

    def read(self, nbytes=1):
        """Read up to nbytes from the software RX buffer as a bytes object,
        or None if nothing is available. Prefer read_byte() for the common
        nbytes=1 case -- it returns a plain int with no allocation, whereas
        this always allocates (even for a single byte), since bytes objects
        are immutable.

        Returns:
            bytes object, or None if no data available
        """
        self.service_rx()
        if len(self._rx_buffer) == 0:
            return None
        return self._rx_buffer.pop_bytes(nbytes)

    def any(self):
        """Return number of bytes available in software buffer and FIFO."""
        return len(self._rx_buffer) + self._sm_rx.rx_fifo()

    def clear_buffers(self):
        """Clear software TX and RX buffers."""
        self._tx_buffer.clear()
        self._rx_buffer.clear()
        self._rx_total = 0
        self._rx_overflow_logged = False
        self._tx_overflow_logged = False
        # Note: Clearing hardware FIFOs is tricky but possible by deactivating/activating
        self._sm_tx.active(0)
        self._sm_rx.active(0)
        # Drain hardware FIFOs if they were stuck (though active(0) usually helps)
        while self._sm_rx.rx_fifo() > 0:
            self._sm_rx.get()
        self._sm_tx.active(1)
        self._sm_rx.active(1)

    def flush_rx(self):
        """Discard all pending RX data without stopping state machines.

        Called on BREAK to prevent stale RS-232C bytes from keeping the ROM
        in its serial-drain loop after a file-load operation ends.
        """
        self._rx_buffer.clear()
        self._rx_total = 0
        self._rx_overflow_logged = False
        while self._sm_rx.rx_fifo() > 0:
            self._sm_rx.get()
