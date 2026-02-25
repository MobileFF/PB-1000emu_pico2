"""
PB-1000 System Emulation
Integrates HD61700 CPU, memory, LCD controller, and keyboard.
"""
import hd61700 as cpu_core
import os
import sys
import time
# print(f"DEBUG IMPORTS: cpu_core={cpu_core}, type={type(cpu_core)}")
# if 'execute' in dir(cpu_core):
#     print(f"DEBUG IMPORTS: cpu_core.execute={cpu_core.execute}")
# else:
#     print(f"DEBUG IMPORTS: cpu_core has no execute attribute!")
#     print(f"DEBUG IMPORTS: dir(cpu_core)={dir(cpu_core)}")
try:
    from lcd_controller_c import LCDControllerC as LCDController
    _LCD_BACKEND = "C"
except ImportError as e:
    from lcd_controller import LCDController
    _LCD_BACKEND = "Python"
#     import sys
#     sys.print_exception(e)
#from lcd_controller import LCDController
print(f"LCD Controller is {_LCD_BACKEND}")
from keyboard import KeyboardMatrix

class RAMView:
    """A writable wrapper for the C-side RAM buffer."""
    def __init__(self, core, read_view, size, start_addr):
        self._core = core
        self._view = read_view
        self._size = size
        self._start = start_addr
    def __getitem__(self, i):
        return self._view[i]
    def __len__(self):
        return self._size
    def __setitem__(self, i, v):
        if isinstance(i, slice):
            r = range(*i.indices(self._size))
            for idx, val in zip(r, v):
                self._core.write_mem(self._start + idx, val)
        else:
            self._core.write_mem(self._start + i, v)
    def __repr__(self):
        return f"<RAMView {self._size} bytes at 0x{self._start:04X}>"

class PB1000System:
    # ... (constants unchanged) ...
    # Memory map (Linear Byte Addresses)
    # Internal ROM: 0x0000 - 0x1FFF (rom0.bin)
    # RAM:          0x6000 - 0x7FFF (8KB, PB-1000 standard)
    # System ROM:   0x8000 - 0xFFFF (rom1.bin, 32KB)
    
    INT_ROM_LIMIT    = 0x2000   
    RAM_START        = 0x6000
    RAM_SIZE         = 0x2000   # 8KB
    SYS_ROM_START    = 0x8000   
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

    def __init__(self, display=None,debug=False, restore_registers=True):
        self.debug_cfg = self._normalize_debug_config(debug)
        self.debug = self.debug_cfg["sys"]
        self._ram_is_c_managed = False
        # Memory initialization
        if hasattr(cpu_core, "get_ram_view"):
            # Use C-side RAM buffer via a writable proxy
            raw_view = cpu_core.get_ram_view()
            self.ram = RAMView(cpu_core, memoryview(raw_view), self.RAM_SIZE, self.RAM_START)
            self._ram_is_c_managed = True
            print("Using C-side RAM proxy (writable)")
        else:
            self.ram = bytearray(self.RAM_SIZE)
            
        self.rom0 = bytearray(0)  # Mirror for Python-side access if needed
        self.rom1 = bytearray(0)
        self.rom_bank = 0
        self._key_trace_last = {}
        self._key_line_state = 0
        self._key_commit_pulse_sent = False
        self._key_commit_pulse_count = 0
        self._key_commit_pulse_max = 64
        self._key_pulse_interval_ms = 25
        self._key_next_pulse_ms = 0
        # True: assert KEY_INT only when IA/KY scan condition is met.
        # False: legacy behavior, assert KEY_INT immediately on key press.
        self.key_interrupt_via_scan = True
        # Validation mode: keep KEY input edge-driven (one pulse on press).
        self.key_reassert_enabled = False
        # One-shot dump when CHATA remains 0x00 while a key is held.
        self._chata_zero_since_ms = None
        self._chata_stuck_logged = False
        # One-shot probe: first LCD VRAM write after arming (e.g. EXE press).
        self._display_probe_active = False
        self._display_probe_label = ""
        self._display_probe_hit = False

        # LCD controller
        self.lcd = LCDController(display, debug=self.debug_cfg["lcd"])

        # Keyboard
        self.keyboard = KeyboardMatrix(debug=self.debug_cfg["kb"])

        # Port state
        self.port_data = 0

        # Initialize CPU
        cpu_core.reset(self.debug_cfg["sys"])
        if hasattr(cpu_core, "set_key_debug"):
            cpu_core.set_key_debug(self.debug_cfg["sys"] and self.debug_cfg["kb"])
        if hasattr(cpu_core, "set_lcd_debug"):
            cpu_core.set_lcd_debug(self.debug_cfg["sys"] and self.debug_cfg["lcd"])
        if restore_registers:
            self._restore_registers_from_dump()
        print("register loaded")
        # Keep references to callbacks to prevent GC!
        # The C module stores these in non-root pointers, so Python must keep them alive.
        self._cb_mem_read = self._mem_read
        self._cb_mem_write = self._mem_write
        self._cb_lcd_read = self.lcd.lcd_read
        self._cb_lcd_write = self.lcd.lcd_write
        self._cb_lcd_ctrl = self.lcd.lcd_ctrl
        self._cb_kb_read = self.keyboard.kb_read
        self._cb_kb_write = self.keyboard.kb_write
        self._cb_port_read = self._port_read
        self._cb_port_write = self._port_write

        # Register all callbacks
        cpu_core.set_mem_callbacks(self._cb_mem_read, self._cb_mem_write)
        cpu_core.set_lcd_callbacks(
            self._cb_lcd_read,
            self._cb_lcd_write,
            self._cb_lcd_ctrl
        )
        cpu_core.set_kb_callbacks(
            self._cb_kb_read,
            self._cb_kb_write
        )
        cpu_core.set_port_callbacks(self._cb_port_read, self._cb_port_write)

        # Enable high-performance C-to-C direct paths if supported
        if hasattr(cpu_core, "use_c_memory"):
            direct_mem = not self.debug_cfg["sys"]
            cpu_core.use_c_memory(direct_mem)
            if direct_mem:
                print("C-side Memory Direct Access: ENABLED")
            else:
                print("C-side Memory Direct Access: DISABLED (debug.sys=true)")
        if hasattr(cpu_core, "use_c_lcd"):
            direct_lcd = not self.debug_cfg["sys"]
            cpu_core.use_c_lcd(direct_lcd)
            if direct_lcd:
                print("C-side LCD Direct Access: ENABLED")
            else:
                print("C-side LCD Direct Access: DISABLED (debug.sys=true)")

    def load_rom(self, path, slot=0):
        """Load ROM image into slot 0 (Internal) or 1 (System)."""
        try:
            with open(path, 'rb') as f:
                data = f.read()
                if slot == 0:
                    self.rom0 = data
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(0, data)
                    print(f"Internal ROM loaded: {len(data)} bytes")
                else:
                    self.rom1 = data
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(1, data)
                    print(f"System ROM loaded: {len(data)} bytes")
        except OSError as e:
            print(f"ROM load error ({path}): {e}")

    def _mem_read(self, segment, offset):
        val = self._mem_read_impl(segment, offset)
        if self.debug and offset >= 0x1FFD0:
            print(f"READ {hex(offset)} -> {hex(val)}")
        return val

    def _mem_read_impl(self, segment, offset):
        """Memory read callback for CPU (Bank-based Mapping)."""
        # Internal memory area (0x0000-0x7FFF) - Fixed to Internal ROM/RAM
        if offset < 0x8000:
            # 0x0000-0x1FFF: Internal ROM (rom0.bin, 8KB)
            if offset < 0x2000:
                if offset < len(self.rom0):
                    return self.rom0[offset]
            # 0x6000-0x7FFF: RAM (8KB, PB-1000 standard)
            elif offset >= self.RAM_START:
                return self.ram[(offset - self.RAM_START) % len(self.ram)]
            else:
                return 0xFF

        # Banked memory area (0x8000-0xFFFF) - Segmented by UA
        # We use UA bits 0-1 for banking here for System ROM
        bank = segment & 0x03
        if offset >= 0x8000:
            # Mirror rom1 across banks for initial boot stability
            rom_off = offset - 0x8000
            if rom_off < len(self.rom1):
                return self.rom1[rom_off]
        
        return 0xFF

    def _mem_write(self, segment, offset, data):
        """Memory write callback for CPU."""
        # Only allow writes to PB-1000 standard RAM area (0x6000-0x7FFF)
        if self.RAM_START <= offset < self.SYS_ROM_START:
            ram_index = (offset - self.RAM_START) % len(self.ram)
            if self._ram_is_c_managed and hasattr(cpu_core, "write_mem"):
                cpu_core.write_mem(offset & 0xFFFF, data & 0xFF)
            else:
                self.ram[ram_index] = data
            if self._display_probe_active and (not self._display_probe_hit):
                if self._is_lcd_vram_addr(offset):
                    pc = cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0
                    print(
                        f"[PROBE] {self._display_probe_label}: first LCD VRAM write "
                        f"PC={pc:04X} ADDR={offset:04X} DATA={data:02X}"
                    )
                    self._display_probe_hit = True
                    self._display_probe_active = False
            if self.debug:
                # Trace key-state bytes on every write (including same-value writes).
                if offset in self._KEY_TRACE_ADDRS:
                    self._key_trace_last[offset] = data
                    name = self._KEY_TRACE_ADDRS.get(offset, "KEY")
                    print(f"KEY RAM WRITE: {name}[{offset:04X}] <= {data:02X}")
                elif self._KEY_BUF_TRACE_START <= offset <= self._KEY_BUF_TRACE_END:
                    print(f"KEY BUF WRITE: [{offset:04X}] <= {data:02X}")
        # Port-mapped RAM or expanded RAM might exist in other segments
        # but for initial boot we stick to the primary 0x2000-0x7FFF range.

    def _is_lcd_vram_addr(self, offset):
        return (0x6100 <= offset <= 0x61FF) or (0x6201 <= offset <= 0x6850)

    def arm_display_write_probe(self, label="EVENT"):
        self._display_probe_active = True
        self._display_probe_hit = False
        self._display_probe_label = label

    def display_write_probe_hit(self):
        return self._display_probe_hit

    def _port_read(self):
        """Port read callback."""
        return self.port_data

    def _port_write(self, data):
        """Port write callback."""
        # print(f"PORT Write: Data={hex(data)}")
        self.port_data = data

    def _register_dump_path(self):
        if '__file__' in globals():
            src = __file__
        elif hasattr(os, "getcwd"):
            src = os.getcwd()
        else:
            src = "/"

        src = src.replace("\\", "/")
        if "/" in src:
            base = src.rsplit("/", 1)[0] or "/"
        else:
            base = "."

        if base == "/":
            return "/roms/register.bin"
        if "/" in base:
            parent = base.rsplit("/", 1)[0] or "/"
        else:
            parent = "."
        return f"{parent}/roms/register.bin"

    def _restore_registers_from_dump(self):
        print("_restore_registers_from_dump")
        path = self._register_dump_path()
        try:
            with open(path, 'rb') as f:
                data = f.read(36)
        except OSError as exc:
            print(f"register.bin not loaded: {exc}")
            return
        if len(data) < 36:
            print(f"register.bin too short ({len(data)} bytes): {path}")
            return
        cpu_core.set_registers(data[:36])
        print(f"registers restored from {path}")

    def step(self, cycles=100, stop_pc=-1):
        """Execute CPU for given number of cycles, or until stop_pc is reached."""
        # Return consumed cycles
        return cpu_core.execute(cycles, stop_pc)

    def tick_timer(self):
        """Call once per second for RTC timer."""
        cpu_core.timer_tick()

    def _key_interrupt_requested_by_scan(self):
        """Replicate PB-1000 KeyInterrupt gating with IA/KY conditions."""
        if not hasattr(cpu_core, "get_reg8"):
            return False
        ia = cpu_core.get_reg8(4) & 0xFF
        if (ia & 0x80) == 0:
            return False
        mask = self._KEY_PULSE_MASK_TABLE[(ia >> 4) & 0x03]
        ky = self.keyboard.kb_read() & 0xF0FF
        if mask != 0 and (ky & mask) != 0:
            return True
        # Fallback: some ROM phases/keyboard lines may not match the current
        # mask table, but a non-zero KY still means a key is physically active.
        # Allow KEY interrupt so scan can advance and KEYCM/KEYIN can latch.
        return ky != 0

    def service_input_lines(self):
        """Refresh level-sensitive input lines before CPU execution."""
        if self.key_interrupt_via_scan:
            desired = 0
            if self.is_key_input_enabled():
                ia = cpu_core.get_reg8(4) if hasattr(cpu_core, "get_reg8") else 0
                # PB-1000 behavior: when IA bit7=0, KEY/Pulse interrupt is generated periodically.
                # This periodic pulse drives keyboard scan service even before a key-specific gate is active.
                if (ia & 0x80) == 0:
                    now = time.ticks_ms()
                    if time.ticks_diff(now, self._key_next_pulse_ms) >= 0:
                        desired = 1
                        self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
                elif self.keyboard.has_key_pressed() and self._key_interrupt_requested_by_scan():
                    desired = 1
            if desired != self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, desired)
                self._key_line_state = desired
            if not self.keyboard.has_key_pressed():
                self._chata_zero_since_ms = None
                self._chata_stuck_logged = False
            return

        if not self.keyboard.has_key_pressed():
            self._chata_zero_since_ms = None
            self._chata_stuck_logged = False
            return
        now = time.ticks_ms()
        chata = self.ram[0x68D3 - self.RAM_START]
        # One-shot diagnostic dump when CHATA appears stuck at 0x00.
        if chata == 0x00:
            if self._chata_zero_since_ms is None:
                self._chata_zero_since_ms = now
            elif (not self._chata_stuck_logged and
                  time.ticks_diff(now, self._chata_zero_since_ms) >= 500):
                ia = cpu_core.get_reg8(4) if hasattr(cpu_core, "get_reg8") else 0
                ib = cpu_core.get_reg8(2) if hasattr(cpu_core, "get_reg8") else 0
                ie = cpu_core.get_reg8(5) if hasattr(cpu_core, "get_reg8") else 0
                keyin = self.ram[0x68D5 - self.RAM_START]
                kysta = self.ram[0x68D2 - self.RAM_START]
                print(
                    "KEY STUCK CHATA=00: "
                    f"IA={ia:02X} IB={ib:02X} IE={ie:02X} "
                    f"KYSTA={kysta:02X} KEYIN={keyin:02X}"
                )
                self._chata_stuck_logged = True
        else:
            self._chata_zero_since_ms = None
            self._chata_stuck_logged = False

        if not self.key_reassert_enabled:
            return
        # Respect current IE mask state.
        if hasattr(self, "is_key_input_enabled") and not self.is_key_input_enabled():
            return
        # Wait until prior KEY request has been consumed by OS/CPU.
        if hasattr(cpu_core, "get_reg8") and (cpu_core.get_reg8(2) & 0x08):
            return
        if time.ticks_diff(now, self._key_next_pulse_ms) < 0:
            return

        # While CHATA is in key-debounce countdown (1..7), feed next KEY pulse.
        if 1 <= chata <= 7:
            cpu_core.set_input(cpu_core.KEY_INT, 1)
            self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
            return
        # CHATA==00 needs one more pulse to commit KEYIN in current ROM flow.
        if chata == 0x00 and self._key_commit_pulse_count < self._key_commit_pulse_max:
            cpu_core.set_input(cpu_core.KEY_INT, 1)
            self._key_commit_pulse_sent = True
            self._key_commit_pulse_count += 1
            self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
            return
        if chata == 0x20:
            self._key_commit_pulse_sent = False
            self._key_commit_pulse_count = 0

#     def dump_mem(addr, length=16):
#         """Dump memory (words) starting at byte addr."""
#         print(f"Dump at {hex(addr)}:")
#         for i in range(0, length * 2, 2):
#             a = addr + i
#             # Read low byte and high byte at the byte address
#             low = cpu_core._mem_read(0, a)
#             high = cpu_core._mem_read(0, a + 1)
#             val = (high << 8) | low
#             print(f"{hex(a)}: {hex(val)}")

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
        self.dump_mem_range(0x6201, 0x6850, bytes_per_line=bytes_per_line, printer=printer)

    def dump_vram_regions(self, bytes_per_line=16, printer=print):
        """Dump both EDTOP and LEDTP VRAM regions."""
        printer("EDTOP VRAM (0x6100-0x61FF)")
        self.dump_edtop_vram(bytes_per_line=bytes_per_line, printer=printer)
        printer("-" * 48)
        printer("LEDTP VRAM (0x6201-0x6850)")
        self.dump_ledtp_vram(bytes_per_line=bytes_per_line, printer=printer)

    def debug_step(self,pause=True,trace=True,trace_index=None,out=None):
        """Execute one instruction and print disassembly."""
        if trace==False:
            return cpu_core.step()

        if out is None:
            out = print
        
        pc = cpu_core.get_pc()
        flags = cpu_core.get_flags()
        
        # Decode flags: Z(80), C(40), LZ(20), UZ(10), SW(08), APO(04)
        f_str = ""
        f_str += "Z" if flags & 0x80 else "-"
        f_str += "C" if flags & 0x40 else "-"
        f_str += "L" if flags & 0x20 else "-"
        f_str += "U" if flags & 0x10 else "-"
        f_str += "S" if flags & 0x08 else "-"
        f_str += "A" if flags & 0x04 else "-"

        op_bytes = cpu_core.step()
        if not op_bytes:
            return None

        hex_str = "".join(f"{x:02X}" for x in op_bytes)
        try:
            from debug import decode_basic
            mnemonic = decode_basic(op_bytes, pc)
        except Exception as e:
            mnemonic = f"Parse Error: {e}"

        prefix = f"{trace_index:05d} : " if trace_index is not None else ""

        if pause:
            print(f"{prefix}[{pc:04X}] {hex_str:<10} | F:{f_str} | {mnemonic} | ",end="")
            while True:
                cmd = input(">")
                if cmd and cmd.strip().upper().startswith("R"):
                    self.print_registers()
                elif cmd and cmd.strip().upper().startswith("D"):
                    addr = int(input("address:"),16)
                    from main import dump_mem
                    dump_mem(addr,1,self)
                else:
                    break
        else:
            out(f"{prefix}[{pc:04X}] {hex_str:<10} | F:{f_str} | {mnemonic} ")
        return mnemonic
            
    def update_display(self, x_offset=24, y_offset=40):
        """Render LCD to physical display."""
        self.lcd.render_to_display(x_offset, y_offset)

    def press_key(self, key):
        """Press a key on the virtual keyboard."""
        self.keyboard.key_press(key)
        if not self.key_interrupt_via_scan:
            cpu_core.set_input(cpu_core.KEY_INT, 1)
            self._key_line_state = 1
        self._key_commit_pulse_sent = False
        self._key_commit_pulse_count = 0
        self._key_next_pulse_ms = time.ticks_add(time.ticks_ms(), self._key_pulse_interval_ms)

    def release_key(self, key):
        """Release a key on the virtual keyboard."""
        self.keyboard.key_release(key)
        if not self.keyboard.has_key_pressed():
            if self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, 0)
            self._key_line_state = 0
            self._key_commit_pulse_sent = False
            self._key_commit_pulse_count = 0
            self._key_next_pulse_ms = 0

    def power_on(self):
        """Simulate power-on / SW press."""
        cpu_core.set_input(cpu_core.SW, 1)
        if hasattr(cpu_core, "set_pc"):
            cpu_core.set_pc(0x0001)

    @property
    def pc(self):
        return cpu_core.get_pc()

    def set_debug(self, enabled):
        """Enable/disable debug output.

        Args:
            enabled: bool or {"sys": bool, "lcd": bool, "kb": bool}
        """
        self.debug_cfg = self._normalize_debug_config(enabled)
        self.debug = self.debug_cfg["sys"]
        if hasattr(cpu_core, "set_debug"):
            cpu_core.set_debug(self.debug_cfg["sys"])
        if hasattr(cpu_core, "set_key_debug"):
            cpu_core.set_key_debug(self.debug_cfg["sys"] and self.debug_cfg["kb"])
        if hasattr(cpu_core, "set_lcd_debug"):
            cpu_core.set_lcd_debug(self.debug_cfg["sys"] and self.debug_cfg["lcd"])
        if hasattr(self.lcd, "debug"):
            self.lcd.debug = self.debug_cfg["lcd"]
        if hasattr(self.keyboard, "set_debug"):
            self.keyboard.set_debug(self.debug_cfg["kb"])

    def set_sys_debug_output_enabled(self, enabled):
        """Enable/disable CPU core system debug output only."""
        val = bool(enabled)
        if hasattr(cpu_core, "set_debug"):
            cpu_core.set_debug(val)
        self.debug_cfg["sys"] = val
        self.debug = val

    def set_pc(self, addr):
        """Set CPU PC (debug helper)."""
        cpu_core.set_pc(addr & 0xFFFF)

    @property
    def is_sleeping(self):
        return cpu_core.is_sleeping()

    def print_registers(self, printer=print):
        regs = [cpu_core.get_reg(i) for i in range(32)]
        printer("Registers:")
        for idx in range(0, 32, 4):
            chunk = " ".join(f"${idx + j:02X}={regs[idx + j]:02X}" for j in range(4))
            printer(f"  {chunk}")
        if hasattr(cpu_core, "get_reg8"):
            ia = cpu_core.get_reg8(4)
            printer(f"IA: IA={ia:02X}")
        if hasattr(cpu_core, "get_sreg"):
            sx = cpu_core.get_sreg(0)
            sy = cpu_core.get_sreg(1)
            sz = cpu_core.get_sreg(2)
            printer(f"SIR: SX={sx:02X} SY={sy:02X} SZ={sz:02X}")
        pair_names = ["IX", "IY", "IZ", "US", "SS", "KY"]
        pair_values = [cpu_core.get_reg16(i) for i in range(6)]
        pairs = " ".join(f"{pair_names[i]}={pair_values[i]:04X}" for i in range(len(pair_names)))
        printer(f"16-bit: {pairs}")

    def get_register_snapshot(self):
        """Return a compact CPU register snapshot for trace diffing."""
        snap = {
            "pc": cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0,
            "flags": cpu_core.get_flags() if hasattr(cpu_core, "get_flags") else 0,
            "r": [cpu_core.get_reg(i) for i in range(32)] if hasattr(cpu_core, "get_reg") else [0] * 32,
            "ia": cpu_core.get_reg8(4) if hasattr(cpu_core, "get_reg8") else 0,
            "ib": cpu_core.get_reg8(2) if hasattr(cpu_core, "get_reg8") else 0,
            "ie": cpu_core.get_reg8(5) if hasattr(cpu_core, "get_reg8") else 0,
            "sx": cpu_core.get_sreg(0) if hasattr(cpu_core, "get_sreg") else 0,
            "sy": cpu_core.get_sreg(1) if hasattr(cpu_core, "get_sreg") else 0,
            "sz": cpu_core.get_sreg(2) if hasattr(cpu_core, "get_sreg") else 0,
            "ix": cpu_core.get_reg16(0) if hasattr(cpu_core, "get_reg16") else 0,
            "iy": cpu_core.get_reg16(1) if hasattr(cpu_core, "get_reg16") else 0,
            "iz": cpu_core.get_reg16(2) if hasattr(cpu_core, "get_reg16") else 0,
            "us": cpu_core.get_reg16(3) if hasattr(cpu_core, "get_reg16") else 0,
            "ss": cpu_core.get_reg16(4) if hasattr(cpu_core, "get_reg16") else 0,
            "ky": cpu_core.get_reg16(5) if hasattr(cpu_core, "get_reg16") else 0,
        }
        return snap

    def is_key_input_enabled(self):
        """Return True when KEY interrupt is enabled (IE bit6)."""
        if hasattr(cpu_core, "get_reg8"):
            return bool(cpu_core.get_reg8(5) & 0x40)  # IE register
        return True

    def get_key_scan_state(self):
        """Return key-scan RAM bytes for input dispatch control."""
        base = 0x68D2 - self.RAM_START
        return {
            "kysta": self.ram[base + 0],  # 0x68D2
            "chata": self.ram[base + 1],  # 0x68D3
            "keycm": self.ram[base + 2],  # 0x68D4
            "keyin": self.ram[base + 3],  # 0x68D5
            "keyinh": self.ram[base + 4], # 0x68D6
            "keyin16": self.ram[base + 3] | (self.ram[base + 4] << 8),
            "keymd": self.ram[base + 5],  # 0x68D7
            "kyrep": self.ram[base + 6],  # 0x68D8
        }

    def can_release_active_key(self):
        """Return True when current key press can be safely released."""
        st = self.get_key_scan_state()
        chata = st["chata"]
        return chata == 0x20

    def get_irq_scan_state(self):
        """Return key/interrupt related register snapshot for tracing."""
        if not hasattr(cpu_core, "get_reg8"):
            return {"ia": 0, "ib": 0, "ie": 0}
        return {
            "ia": cpu_core.get_reg8(4),
            "ib": cpu_core.get_reg8(2),
            "ie": cpu_core.get_reg8(5),
        }
