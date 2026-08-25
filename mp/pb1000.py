import hd61700 as cpu_core
import gc
import os
import sys
import time
import machine
# init_display()/init_sdcard() and the display/touch pin constants used to
# live here, but moved to display_init.py so code that only needs display
# bring-up (main_boot.py's init_display_only(), called before the profile
# picker) doesn't have to import all of PB1000System just to reach them --
# see display_init.py's module docstring. Re-exported here only so existing
# `from pb1000 import init_display` call sites (mostly test scripts) still
# work without changes; PB1000System itself never calls this re-export.
from display_init import init_display
from fdd_protocol import FDDProtocol
from fdd_storage import ImageStorageBackend
from md100_dos import MD100Dos
# PB1000System's own class body used to hold every method directly and grew
# to ~1943 lines, which on 2026-08-22 failed to compile on real hardware
# with MemoryError despite ~193KB free (heap fragmentation, not capacity --
# see pb1000_fdd.py's module docstring for the full explanation). Two of
# its largest self-contained chunks -- virtual FDD/storage-path helpers and
# save_state()/load_state()/the hook registry -- were split into their own
# files as mixins so each compiles as a separate, smaller unit.
from pb1000_fdd import PB1000FddMixin
from pb1000_state_io import PB1000StateIOMixin

try:
    from lcd_controller_c import LCDControllerC as LCDController
    _LCD_BACKEND = "C"
except ImportError:
    pass

try:
    import lcd_c
except ImportError:
    lcd_c = None

# PIO UART for RS-232C passthrough (optional)
try:
    from pio_uart import PioUart
    _HAS_PIO_UART = True
except ImportError:
    _HAS_PIO_UART = False

PD_RES = 0x08
PD_PWR = 0x10
PD_STR = 0x04
PD_ACK = 0x10  # Port B bit 4
PD_BEEP_MASK = 0xC0  # bit6 と bit7: BEEP 制御ビット
VFDD_IO_READ_ADDR = 0x0C03
VFDD_IO_WRITE_ADDR = 0x0C04

# Minimal 5x7 font for PB1000System._draw_text() (the small on-screen status
# toast, e.g. "Key Press: X" -- distinct from draw_text.py's shared helper
# used everywhere else). Module-level so it's built once at import time, not
# on every _draw_text() call -- that call runs once per status-bar redraw,
# which real-hardware [MEM_OVERLAY] logs showed firing far more often than
# expected (see project memory), making this 45-entry dict literal a much
# larger per-call cost than the similar mp/fdd_protocol.py:_switch_cmd()
# dispatch-dict issue found and fixed earlier the same session.
_STATUS_FONT = {
    'A':0x7E0909097E, 'B':0x7F49494936, 'C':0x3E41414122, 'D':0x7F4141413E,
    'E':0x7F49494941, 'F':0x7F09090901, 'G':0x3E4149493A, 'H':0x7F0808087F,
    'I':0x00417F4100, 'J':0x2041413F01, 'K':0x7F08142241, 'L':0x7F40404040,
    'M':0x7F020C027F, 'N':0x7F0408107F, 'O':0x3E4141413E, 'P':0x7F09090906,
    'Q':0x3E4151215E, 'R':0x7F09192946, 'S':0x4649494931, 'T':0x01017F0101,
    'U':0x3F4040403F, 'V':0x1F2040201F, 'W':0x7F4038407F, 'X':0x6314081463,
    'Y':0x0708700807, 'Z':0x6151494543, ' ':0x0000000000, '0':0x3E5149453E,
    '1':0x00427F4000, '2':0x4261514946, '3':0x2141454B31, '4':0x1814127F10,
    '5':0x2745454539, '6':0x3C4A494930, '7':0x0171090503, '8':0x3649494936,
    '9':0x064949291E, '.':0x0060600000, '+':0x08083E0808, '-':0x0808080808,
    '*':0x14083E0814, '/':0x2010080402, '=':0x2424242424, '<':0x0814224100,
    '>':0x0041221408, '!':0x00005F0000, '^':0x0402010204, '&':0x3649552250,
}

def draw_bezel(display, scale=1.0, x=16, y=40, lcd_height=32):
    """Draws the PB-1000 LCD bezel scaled to fit the display."""
    lw = int(192 * scale)
    lh = int(lcd_height * scale)
    padding = 4

    # Outer bezel (dark grey)
    display.fill_rect(x - padding, y - padding, lw + padding*2, lh + padding*2, 0x4228)
    # Middle bezel (bezel edge)
    display.fill_rect(x - padding//2, y - padding//2, lw + padding, lh + padding, 0x8410)
    # Inner background (olive-green)
    display.fill_rect(x, y, lw, lh, 0xB5E6)

class RAMView:
    """A writable wrapper for the C-side RAM buffer."""
    def __init__(self, core, read_view, size, start_addr, segment=0):
        self._core = core
        self._view = read_view
        self._size = size
        self._start = start_addr
        self._segment = segment
    def __getitem__(self, i):
        return self._view[i]
    def __len__(self):
        return self._size
    def __setitem__(self, i, v):
        if isinstance(i, slice):
            # Try fast slice assignment first
            try:
                self._view[i] = v
                return
            except Exception:
                 pass
            
            # Slow fallback: element-wise with write_mem
            r = range(*i.indices(self._size))
            for idx, val in zip(r, v):
                self._core.write_mem(self._start + idx, val & 0xFF, self._segment)
        else:
            b = v & 0xFF
            try:
                self._view[i] = b
                return
            except Exception:
                pass
            self._core.write_mem(self._start + i, b, self._segment)
            
    def __repr__(self):
        return f"<RAMView {self._size} bytes at 0x{self._start:04X}>"

class PB1000System(PB1000FddMixin, PB1000StateIOMixin):
    INT_ROM_LIMIT    = 0x2000
    RAM_START        = 0x6000
    RAM_SIZE         = 0x2000   # 8KB
    SYS_ROM_START    = 0x8000
    EXP_RAM_SIZE     = 0x8000   # 32KB Expanded RAM
    EXT_WORK_BASE    = 0x5F00   # Extension API work area (256 B)
    EXT_WORK_SIZE    = 0x100
    _KEY_TRACE_ADDRS = {
        0x68D2: "KYSTA",
        0x68D3: "CHATA",
        0x68D4: "KEYCM",
        0x68D5: "KEYINL",
        0x68D6: "KEYINH",
        0x68D7: "KEYMD",
        0x68D8: "KYREP",
        0x68D9: "KYCND",
    }
    _KEY_BUF_TRACE_START = 0x68D9
    _KEY_BUF_TRACE_END = 0x68EC
    _KEY_PULSE_MASK_TABLE = (0x0000, 0x0080, 0x00C0, 0xF0FF)

    def _normalize_debug_config(self, debug):
        if isinstance(debug, dict):
            return {
                "sys": bool(debug.get("sys", False)),
                "lcd": bool(debug.get("lcd", False)),
                "kb": bool(debug.get("kb", False)),
            }
        flag = bool(debug)
        return {"sys": flag, "lcd": flag, "kb": flag}

    def __init__(self, display=None, debug=False, restore_registers=True,
                 profile_dir=None, config=None):
        self.debug_cfg = self._normalize_debug_config(debug)
        self.debug = self.debug_cfg["sys"]
        # Detect C-port availability early so _beep_init() can skip machine.PWM
        self._c_port_active = hasattr(cpu_core, 'set_port_direct')
        self.sd_mounted = False
        self.spi = None
        if isinstance(display, tuple) and len(display) >= 4:
            self.sd_mounted = display[2]
            self.spi = display[3]
            # display[0] is the display object itself
            display = display[0]

        # Profile dir and merged config (must be set before _get_storage_path is called)
        self.profile_dir = profile_dir
        self._config = config

        # Bank presence: [0]=ROM1 (always), [1..3]=RAM banks. has_bank[]
        # tracks whether the *currently active* profile has real data for
        # each bank, and is mirrored to the CPU core via
        # set_bank_present()/set_has_exp_ram() so that programs probing
        # bank presence (write-then-readback; absent banks read back 0xFF
        # regardless of what was written — see c_mem_direct_read in
        # modhd61700.c) see a result consistent with what this profile
        # actually represents.
        #
        # Buffer *space* for each bank is allocated on demand, in C, only
        # for banks this profile actually has (set_bank_present()/
        # set_has_exp_ram() call ensure_bank_buf() -- see modhd61700.c --
        # which m_malloc()s from the same GC heap gc.mem_free() reports, so
        # a profile using fewer than 3 banks genuinely frees that much heap
        # rather than reserving it unconditionally). RAM Load no longer
        # exists (reboot + re-pick at the profile picker instead, see
        # [[project_ram_load_profile_switch_ext]]), so there is no
        # mid-session bank count change to support, and
        # get_bank_view()/get_exp_ram_view() are only ever called here for
        # banks this same __init__ just enabled.
        #
        # (2026-08-23: a first attempt at this caused a real-hardware boot
        # hang -- see [[project_bank_dynamic_alloc]] for the postmortem.
        # Root cause: modhd61700.c's c_mem_direct_read()/c_mem_direct_write()
        # gated bank access on has_bank[] alone without checking the buffer
        # pointer itself, so has_bank[]=true with an unallocated buffer (e.g.
        # via detect_all_banks()'s file probe, or a failed allocation) was an
        # unchecked NULL dereference. Re-implemented with an explicit
        # pointer check at that hot path, and with allocation strictly
        # ordered before has_bank[]=true in set_bank_present()/
        # set_has_exp_ram() so a failed m_malloc() can never leave the two
        # inconsistent.)
        self.has_bank = [True, False, False, False]
        for slot in range(1, 4):
            path = self._get_storage_path(f"ram{slot}.bin")
            self.has_bank[slot] = self._file_exists(path)
            print(f"Bank detection: RAM{slot}={'Y' if self.has_bank[slot] else 'N'}")
        if hasattr(cpu_core, "set_has_exp_ram"):
            cpu_core.set_has_exp_ram(self.has_bank[1])
        if hasattr(cpu_core, "set_bank_present"):
            cpu_core.set_bank_present(2, self.has_bank[2])
            cpu_core.set_bank_present(3, self.has_bank[3])

        if hasattr(cpu_core, "get_ram_view"):
            raw_view = cpu_core.get_ram_view()
            self.ram = RAMView(cpu_core, memoryview(raw_view), self.RAM_SIZE, self.RAM_START)
            # Bank 1 (exp_ram): backward-compat view, only built when this
            # profile actually has ram1.bin (has_bank[1]) -- the C buffer
            # only exists in that case now (see comment above).
            _b1 = None
            if self.has_bank[1] and hasattr(cpu_core, "get_exp_ram_view"):
                exp_raw_view = cpu_core.get_exp_ram_view()
                if exp_raw_view is not None:
                    _b1 = RAMView(cpu_core, memoryview(exp_raw_view), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=0x10)
            if _b1 is None and not hasattr(cpu_core, "get_exp_ram_view"):
                # Old firmware without this C export at all: fall back to a
                # plain Python buffer regardless of has_bank[1], matching
                # the pre-dynamic-allocation behavior for that case.
                _b1 = bytearray(self.EXP_RAM_SIZE)
            # Banks 2 and 3 — same, only built when present.
            _bank_views = []
            for slot in range(2, 4):
                view = None
                if self.has_bank[slot] and hasattr(cpu_core, "get_bank_view"):
                    rv = cpu_core.get_bank_view(slot)
                    if rv is not None:
                        view = RAMView(cpu_core, memoryview(rv), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=slot << 4)
                if view is None and not hasattr(cpu_core, "get_bank_view"):
                    view = bytearray(self.EXP_RAM_SIZE)
                _bank_views.append(view)
        else:
            self.ram = bytearray(self.RAM_SIZE)
            _b1 = bytearray(self.EXP_RAM_SIZE)
            _bank_views = [bytearray(self.EXP_RAM_SIZE), bytearray(self.EXP_RAM_SIZE)]

        # _bank_ram[0]=unused, [1]=RAM1, [2]=RAM2, [3]=RAM3. A slot is None
        # when that bank isn't present for this profile -- every consumer
        # already checks has_bank[slot] before touching _bank_ram[slot]
        # (save_state()/load_state()/bank_loader.py/vram_loader.py all do).
        self.exp_ram = _b1
        self._bank_ram = [None, _b1, _bank_views[0], _bank_views[1]]
            
        self.rom0 = bytearray(0)
        self.rom1 = bytearray(0)
        self.rom_bank = 0
        self._key_trace_last = {}

        self.lcd = LCDController(display, debug=self.debug_cfg["lcd"])
        self.lcd.on_scale_change = self._on_lcd_scale_change

        self._disp_x = 16
        self._disp_y = 40
        self._lcd_height = 32  # updated by create_system from ini
        self.touch_x_offset = 0
        self.touch_y_offset = -104
        self.funckey_touch_x_offset = 0
        self.funckey_touch_y_offset = 24
        self.port_data = 0
        self.status_msg = ""
        self.status_expiry_ms = 0
        self._status_rendered_msg = None
        self.pio_uart = None      # Set externally from main.py
        # Initialized here (not just in reset_emulator(), which already resets
        # them) so main.py's every-loop-iteration getattr(self, name, False)
        # checks (main.py's EOF-pending check; this file's UART RX/VFDD warn
        # checks) hit a real attribute via normal lookup instead of falling
        # through to __getattr__(), which raises AttributeError (caught by
        # getattr()'s default, but the exception + its f-string message still
        # get allocated on the GC heap every single call until first written).
        # 2026-08-23 mem_overlay log bisection: this was ~1.3-1.4KB/s of
        # input_alloc, isolated by the fact that keyboard/cursor/touch/joy
        # sub-brackets all read 0 while the outer bracket (which also spans
        # this getattr check) stayed nonzero -- see [[project_mem_churn_investigation]].
        self._pio_uart_eof_pending = False
        self._uart_rx_logged = False
        self._uart_vfdd_warn = False
        self.virtual_fdd = None
        self._virtual_fdd_ack = False
        self._last_vfdd_transfer_time = 0
        self.virtual_fdd_controller = FDDProtocol()
        self.virtual_fdd_config = None
        self._pending_virtual_fdd_config = None
        self._io_wr_regs = bytearray((0, 0, 0, 0, 0, 0xFF, 0x03, 0))
        # index 0 (0x0C00): receive/data register; must have bit0=0 (LB Error flag)
        # and bit4=0 (FM Error flag) to avoid spurious FDD errors.
        self._io_rd_regs = bytearray((0x00, 0x00, 0x00, 0x00, 0x55, 0x00, 0x00, 0x00))
        self._port_last_write = 0x1C
        self._virtual_fdd_ack = False
        self._virtual_fdd_interface_powered = False
        self._fdd_active = False # SPI Bus Lock flag
        self._gpo_parity = 0   # Tracks gpo call parity for D92E P1/P0 protocol
        self._beep_init()
        self._ext_init()

        # PIO UART for RS-232C (Reserved for high-level Python access)
        # We now use this for MMIO 0x0C00-0x0C03 as well.
        try:
            # Note: main.py will replace this with a real PioUart instance if needed
            print("PIO UART support enabled in system.")
        except Exception as e:
            print(f"PIO UART setup warning: {e}")
            sys.print_exception(e)

        # Initialize CPU
        cpu_core.reset(self.debug_cfg["sys"])
        if hasattr(cpu_core, "set_key_debug"):
            cpu_core.set_key_debug(self.debug_cfg["sys"] and self.debug_cfg["kb"])
        if hasattr(cpu_core, "set_lcd_debug"):
            cpu_core.set_lcd_debug(self.debug_cfg["sys"] and self.debug_cfg["lcd"])
              
        # GC Protection: Hold explicit references to all bound methods passed to C
        self._cb_refs = {
            "mem_read": self._mem_read,
            "mem_write": self._mem_write,
            "port_read": self._port_read,
            "port_write": self._port_write,
            "io_read": self._fdd_read_bridge_fn,
            "io_write": self._fdd_write_bridge_fn,
        }
        
        cpu_core.set_mem_callbacks(self._cb_refs["mem_read"], self._cb_refs["mem_write"])
        cpu_core.set_port_callbacks(self._cb_refs["port_read"], self._cb_refs["port_write"])
        if hasattr(cpu_core, "set_io_callbacks"):
            cpu_core.set_io_callbacks(self._cb_refs["io_read"], self._cb_refs["io_write"])

        # Use C-side port_read/port_write (RP2350 GPIO/PWM direct)
        if self._c_port_active:
            _beep_cfg = (self._config or {}).get("beep", {})
            _beep_enabled = _beep_cfg.get("enable", "true").lower() in ("1", "true", "yes", "on")
            _beep_pin = int(_beep_cfg.get("gpio_pin", "14")) if _beep_enabled else -1
            _freq_hz  = int(_beep_cfg.get("freq_hz",  "1000"))
            _duty_pct = int(_beep_cfg.get("duty",     "50"))
            try:
                cpu_core.set_port_direct(6, 13, _beep_pin, _freq_hz, _duty_pct)
            except Exception as _e:
                print(f"PORT: C-direct init failed: {_e}")
                sys.print_exception(_e)
                self._c_port_active = False

        if restore_registers:
            self.load_state()
            
    def load_rom(self, path, slot=0, keep_copy=False):
        """Load a ROM image into `slot` (0 or 1). Returns True on success,
        False if the file couldn't be read (missing/corrupt SD card etc)."""
        try:
            gc.collect()
            with open(path, 'rb') as f:
                data = f.read()
                must_keep_copy = bool(keep_copy or self.has_virtual_fdd())
                if slot == 0:
                    self.rom0 = data if must_keep_copy else None
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(0, data)
                else:
                    self.rom1 = data if must_keep_copy else None
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(1, data)
            return True
        except OSError as e:
            print(f"ROM load error ({path}): {e}")
            return False

    @property
    def has_exp(self):
        return self.has_bank[1]

    def _mem_read(self, segment, offset):
        return self._mem_read_impl(segment, offset)

    def _mem_read_impl(self, segment, offset):
        if hasattr(cpu_core, "read_mem"):
            return cpu_core.read_mem(offset, segment)
        return 0xFF

    def _mem_write(self, segment, offset, data):
        pass

    def _port_read(self):
        # Called by C only when FDD interface is powered (PD_PWR bit=0).
        # Return STR-based ACK for the MD-100 transfer protocol.
        return 0x01 if (self.port_data & PD_STR) == 0 else 0x00

    def _port_write(self, data):
        # Called by C only when FDD interface was/is powered.
        # TX GPIO and BEEP are handled by the C layer.
        self.port_data = data
        self._handle_virtual_fdd_port_write(data)

    def _read_io_register(self, offset):
        index = offset & 0x07
        
        if index == 0:
            # Mask LB (bit0) and FM (bit4) from the receive register to prevent
            # spurious hardware error triggers in the ROM's FDD check routine.
            val0 = self._io_rd_regs[0] & 0xEE
            return val0
            
        elif index == 1:
            # Status Register. FDD mode needs BOTH _virtual_fdd_interface_powered
            # AND _virtual_fdd_ack (only set by a genuine STR strobe); RS-232C sets
            # the former without the latter, so UART RX stays visible throughout.
            status = 0xC0
            _in_fdd_mode = (self.has_virtual_fdd()
                            and self._virtual_fdd_interface_powered
                            and self._virtual_fdd_ack)
            if _in_fdd_mode:
                # SPI Bus Arbitration: Ensure LCD DMA is finished
                if hasattr(self.lcd, "wait_for_idle"):
                    self.lcd.wait_for_idle()
                    time.sleep_us(50) # Allow physical signals to settle

                # FDD Mode: Signal "Data Ready" (Bit 0) AND "Interface Ready" (Bit 1)
                # Ensure rendering is inhibited during polling
                self._fdd_active = True
                try:
                    # MD-100 status bits: 0=LowBattery 1=FDD-present/ready
                    # 6=IF-ready 7=Power-ready. Keep bit0 clear (ROM D8D0
                    # treats a set bit0 as an LB Error).
                    status |= 0x02
                finally:
                    # Note: We keep it active if many polls are expected, but here we
                    # toggle it per-poll to be safe.
                    self._fdd_active = False
                if self.pio_uart and self.pio_uart.any() and not getattr(self, '_uart_vfdd_warn', False):
                    self._uart_vfdd_warn = True
                    print(f"[UART_WARN] FDD ack+power active while UART RX pending ({self.pio_uart.any()}B)")
            else:
                # Bytes stay in the Python PIO buffer; 0x0C02's MMIO callback
                # serves them directly — service_pio_uart_bridge() only signals
                # INT1, it never copies bytes into the C UART FIFO.
                if self.pio_uart and self.pio_uart.any():
                    status |= 0x01 # RX Ready (RS-232C mode)
                else:
                    status |= 0x02 # TX Ready
            return status
            
        elif index == 2:
            if self.pio_uart:
                # read_byte() (int or None, zero allocation) instead of
                # read(1) (would allocate a fresh bytearray+bytes every call)
                # -- this branch runs once per ROM MMIO read of the UART data
                # register, i.e. potentially hundreds of times per second
                # during an active RS-232C transfer. See pio_uart.py's
                # PioUart.read_byte() docstring.
                data = self.pio_uart.read_byte()
                self._io_rd_regs[2] = data if data is not None else 0
                if data is not None and not getattr(self, '_uart_rx_logged', False):
                    self._uart_rx_logged = True
                    print(f"[UART_RX] ROM read first byte: {data:#04x}")
                # Deassert INT1 when Python buffer is now empty so the CPU
                # does not re-enter the ISR before the next byte arrives.
                if not self.pio_uart.any() and hasattr(cpu_core, 'uart_clear_rx_signal'):
                    cpu_core.uart_clear_rx_signal()
                # ROM consumed EOF — flag auto-BREAK; main loop's KeyboardInputManager
                # queues BRK and waits for is_key_input_enabled so it only fires
                # after the ROM finishes processing.
                if data == 0x1A and not getattr(self, '_pio_uart_eof_pending', False):
                    self._pio_uart_eof_pending = True
                    print("[UART_EOF] EOF byte read by ROM; auto-BREAK scheduled")
            else:
                self._io_rd_regs[2] = 0
            return self._io_rd_regs[2]
            
        elif index == 3:
            if not self._virtual_fdd_interface_powered:
                # Return 0x55 (MD-100 ID) while VFDD is configured-but-off, so
                # the ROM doesn't store 0xFF in OPTCD and switch to the slower
                # non-MD-100 code path that checks every dir-entry byte.
                return 0x55

            # No error-bit masking here — LB/FM prevention is handled upstream
            # via the D8D0 PC-based 0x55 return; masking here would corrupt
            # real data bytes (e.g. status=0x10 -> 0x00 with the old mask).
            return self._io_rd_regs[4]
        elif index == 4:
            # Data register: Return last received data byte
            return self._io_rd_regs[4]
            
        return self._io_rd_regs[index]

    def _write_io_register(self, offset, data):
        index = offset & 0x07
        self._io_wr_regs[index] = data
        
        if index == 0:
            pass # Port 0 is not used as a data port in standard MD-100 logic
                
        elif index == 5:
            # Port D Control (Signals to external interface)
            self._handle_virtual_fdd_port_write(data)
            
        elif index == 3:
            # 0x0C03 = TX Data Register (UART) OR FDD read-data port. Suppress
            # PIO UART TX only during an active FDD transfer (powered AND ack) —
            # RS-232C sets powered without ack, so its TX always goes through.
            _in_fdd_mode = (self.has_virtual_fdd()
                            and self._virtual_fdd_interface_powered
                            and self._virtual_fdd_ack)
            if not _in_fdd_mode:
                if self.pio_uart:
                    self.pio_uart.write(data)
                # Echo to the REPL for debugging. Skipped during an active FDD
                # transfer: those bytes are FDD protocol data, not console
                # text, and must not leak to the debug REPL (was flooding the
                # log with raw retry bytes).
                print(chr(data & 0x7F), end="")

    def _fdd_read_bridge_fn(self, segment, offset):
        return self._read_io_register(offset)

    def _fdd_write_bridge_fn(self, segment, offset, data):
        self._write_io_register(offset, data)

    def _beep_init(self):
        self._beep_on  = False
        self._beep_pwm = None
        if getattr(self, '_c_port_active', False):
            print("BEEP: handled by C port layer")
            return
        cfg = (self._config or {}).get("beep", {})
        enabled = cfg.get("enable", "true").lower() in ("1", "true", "yes", "on")
        if not enabled:
            print("BEEP: disabled by config")
            return
        try:
            gpio_pin = int(cfg.get("gpio_pin", "2"))
            freq_hz  = int(cfg.get("freq_hz",  "1000"))
            duty_pct = int(cfg.get("duty",     "50"))
        except (ValueError, TypeError):
            gpio_pin, freq_hz, duty_pct = 2, 1000, 50
        self._beep_duty = max(0, min(65535, duty_pct * 65535 // 100))
        try:
            self._beep_pwm = machine.PWM(machine.Pin(gpio_pin))
            self._beep_pwm.freq(freq_hz)
            self._beep_pwm.duty_u16(0)
            print(f"BEEP: PWM on GP{gpio_pin} @ {freq_hz}Hz duty={duty_pct}%")
        except Exception as e:
            print(f"BEEP: init failed: {e}")
            sys.print_exception(e)
            self._beep_pwm = None

    def _beep_set(self, on):
        if self._beep_on == on:
            return
        self._beep_on = on
        if self._beep_pwm is None:
            return
        self._beep_pwm.duty_u16(self._beep_duty if on else 0)

    # ------------------------------------------------------------------
    # Extension API (EXT)
    # ------------------------------------------------------------------

    # Result codes written to _ext_work[0] by extension function handlers
    EXT_OK          = 0x00
    EXT_ERR_GENERAL = 0xFF

    def _ext_init(self):
        if hasattr(cpu_core, "get_ext_work_view"):
            self._ext_work = cpu_core.get_ext_work_view()
        else:
            self._ext_work = bytearray(self.EXT_WORK_SIZE)
        print(f"EXT: work area {self.EXT_WORK_BASE:#06x}-"
              f"{self.EXT_WORK_BASE + self.EXT_WORK_SIZE - 1:#06x}")
        self._ext_load_modules()

    def _ext_load_modules(self):
        """ext/ ディレクトリの拡張モジュールを自動ロードする。
        <プロファイル>/ext/ (選択中のRAMプロファイル配下、存在する場合のみ)・
        /sd/ext/ ・/ext/ の3箇所を対象にマージしてロードする。
        同名モジュールが複数箇所にある場合はこの優先順位（プロファイル別 >
        /sd/ext/ > /ext/）に従い、他は無視する(sys.path もこの順で登録するため、
        import 解決自体が自然にこの優先順位になる)。
        プロファイル別 ext/ は、特定のRAMプロファイルでだけ有効にしたい
        パッチ的な拡張（例: forex_pb_inkey_patch.py を FOREX_PB プロファイル
        選択時のみロードする等）のために用意している。他のプロファイルを
        選んだ場合や、プロファイル未選択（profile_dir なし）の場合は
        単純にスキャン対象から外れるため、ロードされない。
        .py と .mpy の両方を候補として認識する(.mpy のみを認識しない
        既存実装は 2026-08-13 の不具合報告で判明)。同一ディレクトリに
        両方存在する場合は __import__() 自身の解決規則がそのまま働き、
        MicroPython は常に .py を .mpy より優先するため、ここで
        拡張子ごとの優先順位を別途実装する必要はない。
        各モジュールは register(system) 関数を持つこと。

        _ext_init() から起動時に1回だけ呼ばれる。
        """
        import os, sys, gc
        mod_sources = {}  # mod_name -> ext_dir (最初に見つかった = 優先されるディレクトリ)
        ext_dirs = []
        profile_ext_dir = (self.profile_dir + "/ext") if self.profile_dir else None
        if self.profile_dir:
            ext_dirs.append(profile_ext_dir)
        ext_dirs += ["/sd/ext", "/ext"]

        # sys.path へは優先順位と逆順で insert(0, ...) する -- insert(0,...) は
        # 呼ぶたびに以前の内容を後ろへ押し出すため、最後に insert したものが
        # 結果的に sys.path の先頭に来る。ext_dirs をそのままの順で insert すると
        # 優先順位が逆転してしまう(最下位の /ext が sys.path[0] になる)ため、
        # reversed() で処理して mod_sources 側の優先順位と一致させる。
        for ext_dir in reversed(ext_dirs):
            if ext_dir not in sys.path:
                sys.path.insert(0, ext_dir)

        for ext_dir in ext_dirs:
            try:
                files = os.listdir(ext_dir)
            except OSError:
                continue
            for fname in sorted(files):
                if fname.startswith("_"):
                    continue
                if fname.endswith(".py"):
                    mod_name = fname[:-3]
                elif fname.endswith(".mpy"):
                    mod_name = fname[:-4]
                else:
                    continue
                if mod_name not in mod_sources:
                    mod_sources[mod_name] = ext_dir

        for mod_name in sorted(mod_sources):
            ext_dir = mod_sources[mod_name]
            try:
                # Compiling each ext module's source needs a transient
                # contiguous allocation; loading several in a row without
                # collecting in between lets heap fragmentation from
                # earlier imports cause a later, smaller import to fail
                # with "memory allocation failed" even when total free
                # memory looks sufficient.
                gc.collect()
                mod = __import__(mod_name)
                if hasattr(mod, "register"):
                    mod.register(self)
                    print(f"EXT: loaded {mod_name} from {ext_dir}")
                else:
                    print(f"EXT: {mod_name} has no register(), skipped")
            except Exception as e:
                print(f"EXT: {mod_name} load error: {e}")
                sys.print_exception(e)

    _LEDTP_ADDR = 0x6201     # references/rom0.src: LCD dot-matrix buffer (LEDTP)
    _LEDTP_RAM_OFF = _LEDTP_ADDR - 0x6000
    _SCTOP_RAM_OFF = 0x68C9 - 0x6000   # references/sysvars.txt: SCTOP, actual screen top

    def refresh_lcd_from_ledtp(self):
        """Reconstruct the LCD hardware's own pixel VRAM (lcd_state.vram in
        src/lcd_controller.c) from LEDTP -- the ROM's software text-screen
        buffer, which (unlike vram) *is* part of ram0.bin and so *is*
        correctly restored by load_state().

        Why this is needed: the PB-1000 only ever writes to its LCD through
        dedicated I/O port instructions (STL/PPO/STLM/etc. in hd61700.c),
        never through plain memory stores -- so vram lives in a separate C
        struct (lcd_state_t) that load_state() never touches. Normally the
        ROM's own boot code (from PC=0x0000) rebuilds vram as a side effect
        of drawing the boot screen; a full CPU-state resume starts mid-
        program instead, and unless the resumed code happens to redraw
        something on its own, vram is left however lcd_init() zeroed it,
        so nothing appears on screen despite RAM/registers being correct.

        This replicates the ROM's own DOTDS routine (&H022C, see
        references/rom0.src around 022E and mp/ext/dotds_64dot.py's
        _dotds_override(), which does the same transfer for a different
        reason -- its 64-dot-mode override -- and was the reference for
        this implementation): copy the currently-visible window of LEDTP
        (LEDTP + 6*SCTOP, get_num_pages()*192 bytes) into vram via
        blit_reversed() (matching the bit order lcd_write() itself applies),
        mark it dirty, and send the same LCD-ON sequence power_on() sends
        (DOTDS always ends with one, and vram starting blank means
        display_on may not be set yet either).

        Pure data movement -- reads RAM directly (no hd61700.read_mem(),
        which has UART-visible side effects) and never touches CPU
        registers/PC, so it's safe to call right after a full-state
        load_state() without disturbing the resumed program's state."""
        if lcd_c is None or not hasattr(cpu_core, "get_ram_view"):
            return
        try:
            ram_mv = memoryview(cpu_core.get_ram_view())
            sctop = ram_mv[self._SCTOP_RAM_OFF]
            length = lcd_c.get_num_pages() * 192
            src_off = self._LEDTP_RAM_OFF + 6 * sctop
            if src_off < 0 or src_off + length > len(ram_mv):
                return  # out-of-range SCTOP (e.g. a corrupt save) -- leave vram as-is
            lcd_c.blit_reversed(ram_mv[src_off:src_off + length], 0)
            lcd_c.mark_dirty()
            self.lcd.lcd_ctrl(0xDF)   # OP=1 (command mode), CE=3 (both chips)
            self.lcd.lcd_write(0x14)  # LCD ON
            self.lcd.lcd_ctrl(0xDE)   # OP=0 (back to data mode)
        except Exception as e:
            print(f"refresh_lcd_from_ledtp failed: {e}")

    def step(self, cycles=100, stop_pc=-1):
        return cpu_core.execute(int(cycles), int(stop_pc))

    def reset_emulator(self):
        """Perform a hardware-like reset (PC=0x0000)."""
        print("Emulator Reset triggered (PC=0x0000)")
        import time
        cpu_core.reset(self.debug_cfg["sys"])
        # cpu_core.reset() already silences the PWM and sets the post-reset beep
        # guard.  Also force Python-side beep off in case the Python path is in use.
        self._beep_set(False)

        # Clear PIO UART buffers upon reset
        if self.pio_uart and hasattr(self.pio_uart, "clear_buffers"):
            self.pio_uart.clear_buffers()
            print("PIO UART buffers cleared.")
        self._uart_rx_logged = False
        self._uart_vfdd_warn = False
        self._pio_uart_eof_pending = False

        # Reset page count to default (4=32-dot, 8=64-dot) so the renderer
        # doesn't expose pages 4-7 if a previous program used 64-dot mode.
        # (Vacated pages always render as background regardless of VDP state
        # since _pixel_color()'s active_pages clamp applies to both render
        # paths, so this no longer needs to also force VDP off to be safe.)
        self.lcd.set_num_pages(self._lcd_height // 8)
        self.set_status("SYSTEM RESET", 1500)

    def tick_timer(self):
        cpu_core.timer_tick()

    def service_pio_uart(self):
        """Service PIO UART TX/RX buffers.
        Only needed if pio_uart is active.
        """
        if self.pio_uart is not None and hasattr(self.pio_uart, "_sm_tx"):
             # main.py already handles polling for MMIO, but we keep this for consistency
             self.pio_uart.service_tx()
             result = self.pio_uart.service_rx()
             if result:
                 self.set_status(result,10000)

    def update_display(self, x_offset=None, y_offset=None):
        if x_offset is not None: self._disp_x = x_offset
        if y_offset is not None: self._disp_y = y_offset
        # LCD and HDMI are exclusive outputs (never both active at once) —
        # see mp/main.py's [hdmi] setup and mp/hdmi_menu_mirror.py (which
        # applies the same policy to the EMULATOR MENU). While HDMI is
        # enabled, the physical LCD (and its status-bar overlay) is left
        # untouched entirely instead of being mirrored alongside it.
        if getattr(self, "_hdmi_enabled", False):
            n = getattr(self, "_hdmi_frame_count", 0) + 1
            self._hdmi_frame_count = n
            if n % getattr(self, "_hdmi_frame_skip", 1) == 0:
                self.lcd.render_to_hdmi()
        else:
            self.lcd.render_to_display(self._disp_x, self._disp_y)
            self._render_status_bar()

    # lcd_render_to_hdmi() (src/lcd_controller.c) always sends the game
    # screen as a 192 x active_pages*8 canvas at this fixed scale
    # (HDMI_RECEIVER_SCALE there) — kept in sync manually since one side is
    # C and the other Python. _draw_bezel_hdmi() below sends the bezel in
    # that exact same logical coordinate space/scale (not the real LCD
    # panel's own _disp_x/_disp_y/scale) specifically so the two align on
    # the HDMI screen: the receiver centers each independently (see its
    # KIND_* window-centering comment), but since both use width=192,
    # height=lcd_height as their core size and the same scale, their
    # windows end up concentric — the bezel's fixed logical padding around
    # that core becomes a symmetric border around the game screen.
    _HDMI_SCALE = 3

    def _draw_bezel_hdmi(self, scale):
        """Mirror draw_bezel() to the HDMI bridge, aligned with the game
        screen's own window (see _HDMI_SCALE comment above) rather than
        reusing draw_bezel()'s real-LCD-panel coordinates/scale (which
        have no correspondence to HDMI's own scaled/centered window).
        `scale` (the real LCD panel's scale) is intentionally unused here.
        One-shot: builds a throwaway mirror and flushes it immediately."""
        if not getattr(self, "_hdmi_enabled", False):
            return
        if not hasattr(self.lcd, 'display') or self.lcd.display is None:
            return
        try:
            from hdmi_menu_mirror import create as _create_hdmi_mirror
            mirror = _create_hdmi_mirror(self.lcd.display, self.lcd, bezel=True)
            if mirror is None:
                return
            padding = 4
            lw, lh = 192, self._lcd_height
            mirror.width = lw + padding * 2
            mirror.height = lh + padding * 2
            mirror.scale = self._HDMI_SCALE
            mirror.fill_rect(0, 0, mirror.width, mirror.height, 0x4228)  # outer
            mid_off = padding - padding // 2
            mirror.fill_rect(mid_off, mid_off, lw + padding, lh + padding, 0x8410)  # mid
            mirror.fill_rect(padding, padding, lw, lh, 0xB5E6)  # inner
            mirror.flush_if_dirty()
        except Exception:
            pass

    def force_full_redraw(self):
        """Redraw bezel + LCD after overlaying the screen (e.g. after menu closes).
        While HDMI is the active output (see update_display()), the physical
        LCD is left untouched (exclusive-display policy) but the bezel is
        still mirrored to HDMI."""
        if getattr(self, "_hdmi_enabled", False):
            self._draw_bezel_hdmi(self.lcd.scale)
        elif hasattr(self.lcd, 'display') and self.lcd.display is not None:
            draw_bezel(self.lcd.display, self.lcd.scale, self._disp_x, self._disp_y, lcd_height=self._lcd_height)
        self.lcd.mark_dirty()
        self._status_rendered_msg = None  # force status bar refresh
        self.update_display()
        # Gap filler: draw_bezel()'s outer rect ends at game-screen-bottom+4
        # (padding) and _render_status_bar()'s backdrop starts at
        # game-screen-bottom+12-2=+10 -- a ~6px-tall band between them that
        # neither function ever paints, left showing whatever was drawn
        # there earlier. Repaint the whole band black so nothing is left
        # uncovered.
        if hasattr(self.lcd, 'display') and self.lcd.display is not None:
            _d = self.lcd.display
            _gsb = self._disp_y + int(self._lcd_height * self.lcd.scale)  # game screen bottom
            _d.fill_rect(self._disp_x, _gsb + 4, 200, 6, 0x0000)

    def set_status(self, msg, duration_ms=2000):
        self.status_msg = msg
        self.status_expiry_ms = time.ticks_add(time.ticks_ms(), duration_ms)

    def _render_status_bar(self):
        if not hasattr(self.lcd, 'display') or self.lcd.display is None:
            return

        now = time.ticks_ms()
        # Handle expiry
        active_msg = self.status_msg
        if active_msg and time.ticks_diff(self.status_expiry_ms, now) < 0:
            active_msg = ""
            self.status_msg = ""

        # Only redraw if the message has changed
        if active_msg == self._status_rendered_msg:
            return

        y_pos = self._disp_y + int(self._lcd_height * self.lcd.scale) + 12
        display = self.lcd.display
        
        # Clear/Draw backdrop
        display.fill_rect(self._disp_x, y_pos - 2, 200, 12, 0x0000)

        if active_msg:
            self._draw_text(display, self._disp_x, y_pos, active_msg, 0x07FF) # Cyan text

        self._status_rendered_msg = active_msg

    def _draw_text(self, display, x, y, text, color):
        curr_x = x
        for char in str(text).upper():
            bits = _STATUS_FONT.get(char, 0x7F7F7F7F7F) # Block for unknown
            # Hex bytes are ordered MSB...LSB, so i=0 (left) should be MSB
            for i in range(5):
                col_bits = (bits >> ((4 - i) * 8)) & 0xFF
                for j in range(8):
                    if col_bits & (1 << j):
                        display.fill_rect(curr_x + i, y + j, 1, 1, color)
            curr_x += 6

    def _on_lcd_scale_change(self, scale):
        """Callback from LCDController when scale is changed."""
        if getattr(self, "_hdmi_enabled", False):
            self._draw_bezel_hdmi(scale)
        elif hasattr(self.lcd, 'display') and self.lcd.display:
            # Re-draw the bezel with new scale
            draw_bezel(self.lcd.display, scale, self._disp_x, self._disp_y, lcd_height=self._lcd_height)
        # Ensure the LCD content itself is marked dirty to fill the new bezel
        if hasattr(self.lcd, 'dirty'):
            self.lcd.dirty = True

    def press_key(self, key):
        if hasattr(cpu_core, 'press_row_ki'):
            coord = None
            if isinstance(key, tuple) and len(key) >= 2:
                coord = key
            elif isinstance(key, str):
                import keymap
                k = key.lower()
                if k in keymap.KEY_MAP:
                    coord = keymap.KEY_MAP[k]
            
            if coord:
                cpu_core.press_row_ki(coord[0], coord[1])

        # Automatically show label on status bar
        if hasattr(self, 'set_status'):
            if isinstance(key, str):
                if key.startswith("TK"):
                    label = f"TOUCH {key[2:]}"
                else:
                    label = key.upper()
            elif isinstance(key, tuple):
                label = f"KEY {key[0]},{key[1]}"
            else:
                label = str(key)
            self.set_status(label)

    def release_key(self, key):
        if hasattr(cpu_core, 'release_row_ki'):
            coord = None
            if isinstance(key, tuple) and len(key) >= 2:
                coord = key
            elif isinstance(key, str):
                import keymap
                k = key.lower()
                if k in keymap.KEY_MAP:
                    coord = keymap.KEY_MAP[k]
            
            if coord:
                cpu_core.release_row_ki(coord[0], coord[1])

    def power_on(self, *, force_reset=False, force_power_on=False):
        cpu_core.set_input(cpu_core.SW, 1)
        if hasattr(cpu_core, "set_pc"):
            current_pc = cpu_core.get_pc()
            current_ua = cpu_core.get_reg8(3)
            if force_reset and force_power_on:
                raise ValueError("force_reset and force_power_on cannot both be true")

            if force_reset:
                cpu_core.set_pc(0x0000)
                print(f"System forced to reset entry (PC=0x0000 UA={current_ua:#04x})")
            elif force_power_on:
                cpu_core.set_pc(0x0001)
                print(f"System forced to power-on entry (PC=0x0001 UA={current_ua:#04x})")
            elif current_pc == 0x0001:
                 print(f"System power on at power-on entry (PC=0x0001 UA={current_ua:#04x})")
            elif current_pc == 0x0000:
                 print(f"System power on at reset entry (PC=0x0000 UA={current_ua:#04x})")
            else:
                print(f"System resumed at PC={current_pc:#06x} UA={current_ua:#04x}")

        self.lcd.lcd_ctrl(0xDF) # OP=1, CE=3 (Both chips)
        self.lcd.lcd_write(0x14)
        self.lcd.lcd_ctrl(0xDE) # OP=0


    @property
    def pc(self):
        return cpu_core.get_pc()

    @pc.setter
    def pc(self, value):
        cpu_core.set_pc(value)

    @property
    def flags(self):
        return cpu_core.get_flags()

    @flags.setter
    def flags(self, value):
        cpu_core.set_flags(value)

    @property
    def ia(self):
        return cpu_core.get_reg8(4)

    @ia.setter
    def ia(self, value):
        cpu_core.set_reg8(4, value)

    @property
    def ib(self):
        return cpu_core.get_reg8(2)

    @ib.setter
    def ib(self, value):
        cpu_core.set_reg8(2, value)

    @property
    def ie(self):
        return cpu_core.get_reg8(5)

    @ie.setter
    def ie(self, value):
        cpu_core.set_reg8(5, value)

    @property
    def ua(self):
        return cpu_core.get_reg8(3)

    @ua.setter
    def ua(self, value):
        cpu_core.set_reg8(3, value)

    # Main Registers r0-r31
    def __getattr__(self, name):
        if name.startswith('r') and name[1:].isdigit():
            idx = int(name[1:])
            if 0 <= idx <= 31:
                return cpu_core.get_reg(idx)
        raise AttributeError(f"'PB1000System' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name.startswith('r') and name[1:].isdigit():
            idx = int(name[1:])
            if 0 <= idx <= 31:
                cpu_core.set_reg(idx, value)
                return
        super().__setattr__(name, value)

    @property
    def ix(self): return cpu_core.get_reg16(0)
    @ix.setter
    def ix(self, v): cpu_core.set_reg16(0, v)
    @property
    def iy(self): return cpu_core.get_reg16(1)
    @iy.setter
    def iy(self, v): cpu_core.set_reg16(1, v)
    @property
    def iz(self): return cpu_core.get_reg16(2)
    @iz.setter
    def iz(self, v): cpu_core.set_reg16(2, v)

    @property
    def sx(self): return cpu_core.get_sreg(0)
    @sx.setter
    def sx(self, v): cpu_core.set_sreg(0, v)
    @property
    def sy(self): return cpu_core.get_sreg(1)
    @sy.setter
    def sy(self, v): cpu_core.set_sreg(1, v)
    @property
    def sz(self): return cpu_core.get_sreg(2)
    @sz.setter
    def sz(self, v): cpu_core.set_sreg(2, v)

    @property
    def cpu(self):
        return cpu_core
    
    def set_debug(self, enabled):
        self.debug_cfg = self._normalize_debug_config(enabled)
        self.debug = self.debug_cfg["sys"]
        if hasattr(cpu_core, "set_debug"):
            cpu_core.set_debug(self.debug_cfg["sys"])

    @property
    def is_sleeping(self):
        return cpu_core.is_sleeping()

    def is_key_input_enabled(self):
        if hasattr(cpu_core, "get_reg8"):
            return bool(cpu_core.get_reg8(5) & 0x40)
        return True

    def print_registers(self, printer=print):
        regs = [cpu_core.get_reg(i) for i in range(32)]
        printer("Registers:")
        for idx in range(0, 32, 4):
            chunk = " ".join(f"${idx + j:02d}={regs[idx + j]:02X}" for j in range(4))
            printer(f"  {chunk}")
        if hasattr(cpu_core, "get_reg8"):
            ia = cpu_core.get_reg8(4)
            ib = cpu_core.get_reg8(2)
            ua = cpu_core.get_reg8(3)
            ie = cpu_core.get_reg8(5)
            printer(f"IA: IA={ia:02X} IB={ib:02X} UA={ua:02X} IE={ie:02X}")
        if hasattr(cpu_core, "get_sreg"):
            sx = cpu_core.get_sreg(0)
            sy = cpu_core.get_sreg(1)
            sz = cpu_core.get_sreg(2)
            printer(f"SIR: SX={sx:02X} SY={sy:02X} SZ={sz:02X}")
        pair_names = ["IX", "IY", "IZ", "US", "SS", "KY"]
        pair_values = [cpu_core.get_reg16(i) for i in range(6)]
        pairs = " ".join(f"{pair_names[i]}={pair_values[i]:04X}" for i in range(len(pair_names)))
        printer(f"16-bit: {pairs}")

    def dump_mem_range(self, start, end, bytes_per_line=16, printer=print):
        """Dump linear memory bytes [start..end] in hex."""
        start &= 0xFFFF
        end &= 0xFFFF
        if end < start:
            printer(f"Invalid range: {start:04X}-{end:04X}")
            return

        printer(f"MEM DUMP {start:04X}-{end:04X} ({end - start + 1} bytes)")
        addr = start
        while addr <= end:
            line_end = addr + bytes_per_line - 1
            if line_end > end:
                line_end = end
            vals = []
            a = addr
            while a <= line_end:
                vals.append(f"{self._mem_read_impl(0, a):02X}")
                a += 1
            printer(f"{addr:04X}: {' '.join(vals)}")
            addr += bytes_per_line

    def dump_edtop_vram(self, bytes_per_line=16, printer=print):
        """Dump EDTOP VRAM (0x6100-0x61FF)."""
        self.dump_mem_range(0x6100, 0x61FF, bytes_per_line=bytes_per_line, printer=printer)

    def dump_ledtp_vram(self, bytes_per_line=16, printer=print):
        """Dump LEDTP VRAM (0x6201-0x6850)."""
        #self.dump_mem_range(0x6201, 0x6850, bytes_per_line=bytes_per_line, printer=printer)
        self.lcd.dump_vram()





