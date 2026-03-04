"""
PB-1000 System Emulation
Integrates HD61700 CPU, memory, LCD controller, and keyboard.
"""
import hd61700 as cpu_core
import os
import sys
import time
import machine
from ili9341 import ILI9341

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

SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22

def init_display():
    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)
    return display


def draw_bezel(display):
    display.fill_rect(12, 36, 296, 72, 0x4228)
    display.fill_rect(14, 38, 292, 68, 0x8410)
    display.fill_rect(16, 40, 288, 64, 0xB5E6)

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
        def _write_one(idx, val):
            b = val & 0xFF
            # Prefer direct memoryview write for C-managed buffers.
            try:
                self._view[idx] = b
                return
            except Exception:
                pass
            # Fallback path (older ports / non-writable views).
            self._core.write_mem(self._start + idx, b, self._segment)

        if isinstance(i, slice):
            r = range(*i.indices(self._size))
            for idx, val in zip(r, v):
                _write_one(idx, val)
        else:
            _write_one(i, v)
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
    EXP_RAM_SIZE     = 0x8000   # 32KB Expanded RAM
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
        direct_mem_override = None
        direct_lcd_override = None
        if isinstance(debug, dict):
            if "c_memory" in debug:
                direct_mem_override = bool(debug.get("c_memory"))
            if "c_lcd" in debug:
                direct_lcd_override = bool(debug.get("c_lcd"))
        self.debug_cfg = self._normalize_debug_config(debug)
        self.debug = self.debug_cfg["sys"]
        self._ram_is_c_managed = False

        self._exp_ram_path = self._ram_path(1)
        self.has_exp = self._file_exists(self._exp_ram_path)
        print(f"Expanded RAM detection: {'FOUND' if self.has_exp else 'NOT FOUND'} ({self._exp_ram_path})")
        if hasattr(cpu_core, "set_has_exp_ram"):
            cpu_core.set_has_exp_ram(self.has_exp)
            print(f"HD61700 bank1 RAM: {'ENABLED' if self.has_exp else 'DISABLED'}")

        # Memory initialization
        if hasattr(cpu_core, "get_ram_view"):
            # Use C-side RAM buffer via a writable proxy
            raw_view = cpu_core.get_ram_view()
            self.ram = RAMView(cpu_core, memoryview(raw_view), self.RAM_SIZE, self.RAM_START)
            self._ram_is_c_managed = True
            
            if self.has_exp and hasattr(cpu_core, "get_exp_ram_view"):
                exp_raw_view = cpu_core.get_exp_ram_view()
                # Wrap in RAMView with segment=1 so writes go to C bank 1
                self.exp_ram = RAMView(cpu_core, memoryview(exp_raw_view), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=1)
            else:
                self.exp_ram = bytearray(self.EXP_RAM_SIZE)
                
            print(f"Using C-side RAM proxy (writable). Expansion RAM present: {self.has_exp}")
        else:
            self.ram = bytearray(self.RAM_SIZE)
            self.exp_ram = bytearray(self.EXP_RAM_SIZE)
            print(f"Using Python-side RAM buffer. Expansion RAM present: {self.has_exp}")
            
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
        self._key_pulse_release_pending = False
        self._key_post_release_pulses_remaining = 0
        self._key_post_release_pulses_max = 16
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
        self.load_ram()
        print("register and ram loaded")
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
        print("callbacks set to instance fields")

        # Register all callbacks
        cpu_core.set_mem_callbacks(self._cb_mem_read, self._cb_mem_write)
        print("set_mem_callbacks")
        cpu_core.set_lcd_callbacks(
            self._cb_lcd_read,
            self._cb_lcd_write,
            self._cb_lcd_ctrl
        )
        print("set_lcd_callbacks")
        cpu_core.set_kb_callbacks(
            self._cb_kb_read,
            self._cb_kb_write
        )
        print("set_kb_callbacks")
        cpu_core.set_port_callbacks(self._cb_port_read, self._cb_port_write)
        print("set_port_callbacks")
        print("all callbacks set")
        
        # Enable high-performance C-to-C direct paths if supported
        if hasattr(cpu_core, "use_c_memory"):
            direct_mem = (not self.debug_cfg["sys"]) if direct_mem_override is None else bool(direct_mem_override)
            cpu_core.use_c_memory(direct_mem)
            if direct_mem:
                print("C-side Memory Direct Access: ENABLED")
            else:
                print("C-side Memory Direct Access: DISABLED")
        if hasattr(cpu_core, "use_c_lcd"):
            direct_lcd = (not self.debug_cfg["sys"]) if direct_lcd_override is None else bool(direct_lcd_override)
            cpu_core.use_c_lcd(direct_lcd)
            if direct_lcd:
                print("C-side LCD Direct Access: ENABLED")
            else:
                print("C-side LCD Direct Access: DISABLED")

    def load_rom(self, path, slot=0, keep_copy=False):
        """Load ROM image into slot 0 (Internal) or 1 (System).

        ``keep_copy`` defaults to ``False`` so that the Python object holding the
        bytes is discarded immediately.  This gives the *smallest* heap
        footprint on devices such as the Pico.  If your code needs to examine
        the ROM contents directly (e.g. accessing ``system.rom0`` or invoking
        ``_mem_read_impl``), pass ``keep_copy=True`` and a mirror will be
        retained.
        """
        try:
            with open(path, 'rb') as f:
                data = f.read()
                if slot == 0:
                    # optionally keep a Python copy for _mem_read_impl
                    self.rom0 = data if keep_copy else None
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(0, data)
                    print(f"Internal ROM loaded: {len(data)} bytes")
                else:
                    self.rom1 = data if keep_copy else None
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
                # internal ROM read; prefer Python copy if present, otherwise
                # fall back to the C core (read_mem is always defined when
                # the ROM has been loaded in C).
                if self.rom0 is not None:
                    if offset < len(self.rom0):
                        return self.rom0[offset]
                elif hasattr(cpu_core, "read_mem"):
                    return cpu_core.read_mem(offset, segment)
            # 0x6000-0x7FFF: RAM (8KB, PB-1000 standard)
            elif offset >= self.RAM_START:
                return self.ram[(offset - self.RAM_START) % len(self.ram)]
            else:
                return 0xFF

        # Banked memory area (0x8000-0xFFFF) - Segmented by UA
        bank = segment & 0x03
        if offset >= 0x8000:
            if bank == 0:
                # System ROM
                rom_off = offset - 0x8000
                if self.rom1 is not None:
                    if rom_off < len(self.rom1):
                        return self.rom1[rom_off]
                elif hasattr(cpu_core, "read_mem"):
                    return cpu_core.read_mem(offset, segment)
            elif bank == 1 and self.has_exp:
                # Expanded RAM
                exp_off = offset - 0x8000
                if exp_off < len(self.exp_ram):
                    return self.exp_ram[exp_off]

        return 0xFF

    def _mem_write(self, segment, offset, data):
        """Memory write callback for CPU.

        Watch for writes to the two system‑variable addresses used for
        the FOR stack.  When a write to SBOT (0x6933) or FORSK (0x6935)
        occurs we print the current PC so that the emulator log can be
        correlated with the ROM code that is manipulating the heap.
        """
        # SBOT/FORSK addresses (absolute RAM locations)
        if 0x692F <= offset <= 0x6941:
            pc = cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0
            print(f"[HEAPRANGE] write off={offset:04X} at PC={pc:04X} data={data:02X}")
#         if offset in (0x6933, 0x6935):
#             pc = cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0
#             name = "SBOT" if offset == 0x6933 else "FORSK"
#             print(f"[HEAP] write to {name} at PC={pc:04X} val={data:02X}")
        if self.debug: print(f"_mem_write: seg={segment} off={hex(offset)} data={hex(data)}")
        if self.RAM_START <= offset < self.SYS_ROM_START:
            # 8KB standard RAM
            ram_index = (offset - self.RAM_START) % len(self.ram)
            if self._ram_is_c_managed and hasattr(cpu_core, "write_mem"):
                cpu_core.write_mem(offset & 0xFFFF, data & 0xFF, segment)
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
                    
        elif offset >= 0x8000 and (segment & 0x03) == 1 and self.has_exp:
            # Bank 1 32KB Expanded RAM
            exp_off = offset - 0x8000
            if exp_off < len(self.exp_ram):
                if self.debug: print(f"  -> Writing to Expand RAM (C-managed: {self._ram_is_c_managed})")
                # Always sync C-side via API or proxy
                if self._ram_is_c_managed and hasattr(cpu_core, "write_mem"):
                    # c_mem_direct_write knows about expanded RAM if segment is 1
                    cpu_core.write_mem(offset & 0xFFFF, data & 0xFF, segment)
                else:
                    self.exp_ram[exp_off] = data

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

    def _file_exists(self, path):
        """Helper to check if a file exists (MicroPython compatible)."""
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def _ram_path(self, slot=0):
        # reuse logic from _register_dump_path for consistency
        path = self._register_dump_path()
        base = path.rsplit("/", 1)[0]
        return f"{base}/ram{slot}.bin"

    def load_ram(self):
        """Load standard and expanded RAM contents from file."""
        # Load standard RAM (8KB)
        path0 = self._ram_path(0)
        try:
            val = None
            with open(path0, 'rb') as f:
                val = f.read(self.RAM_SIZE)
            if val:
                # Use a loop to avoid memoryview slice assignment issues in some MP versions
                for i in range(len(val)):
                    self.ram[i] = val[i]
                print(f"Standard RAM restored from {path0}")
        except OSError:
            print(f"Standard RAM file {path0} not found, starting clean.")

        # Load expanded RAM (32KB)
        if self.has_exp:
            path1 = self._exp_ram_path
            try:
                val = None
                with open(path1, 'rb') as f:
                    val = f.read(self.EXP_RAM_SIZE)
                if val:
                    for i in range(len(val)):
                        self.exp_ram[i] = val[i]
                    print(f"Expanded RAM restored from {path1}")
            except OSError:
                print(f"Expanded RAM file {path1} not found.")

    def save_ram(self):
        """Save standard and expanded RAM contents to file."""
        # Save standard RAM
        path0 = self._ram_path(0)
        try:
            with open(path0, 'wb') as f:
                # RAMView doesn't support buffer protocol directly, use the internal view
                buf = self.ram._view if isinstance(self.ram, RAMView) else self.ram
                f.write(buf)
            print(f"Standard RAM saved to {path0}")
        except OSError as e:
            print(f"Failed to save standard RAM: {e}")

        # Save expanded RAM
        if self.has_exp:
            path1 = self._exp_ram_path
            try:
                with open(path1, 'wb') as f:
                    buf = self.exp_ram._view if isinstance(self.exp_ram, RAMView) else self.exp_ram
                    f.write(buf)
                print(f"Expanded RAM saved to {path1}")
            except OSError as e:
                print(f"Failed to save expanded RAM: {e}")

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

    def _key_scan_pending(self):
        """Return True while key-scan state has not fully returned to idle."""
        base = 0x68D2 - self.RAM_START
        chata = self.ram[base + 1]  # 68D3
        keycm = self.ram[base + 2]  # 68D4
        # Idle is CHATA=20 and KEYCM=00 in current ROM flow.
        return (chata != 0x20) or (keycm != 0x00)

    def service_input_lines(self):
        """Refresh level-sensitive input lines before CPU execution."""
        if self.key_interrupt_via_scan:
            # Emit KEY interrupt as a short pulse (1 call high, next call low)
            # instead of holding high. This matches ROM-side edge-like handling
            # and avoids getting stuck in debounce paths.
            if self._key_pulse_release_pending and self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, 0)
                self._key_line_state = 0
                self._key_pulse_release_pending = False

            pulse = 0
            key_pressed = self.keyboard.has_key_pressed()

            if self.is_key_input_enabled():
                ia = cpu_core.get_reg8(4) if hasattr(cpu_core, "get_reg8") else 0
                now = time.ticks_ms()
                # PB-1000 behavior: when IA bit7=0, KEY/Pulse interrupt is generated periodically.
                # This periodic pulse drives keyboard scan service even before a key-specific gate is active.
                if (ia & 0x80) == 0:
                    if time.ticks_diff(now, self._key_next_pulse_ms) >= 0:
                        pulse = 1
                        self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
                elif key_pressed and self._key_interrupt_requested_by_scan():
                    if time.ticks_diff(now, self._key_next_pulse_ms) >= 0:
                        pulse = 1
                        self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
                elif (not key_pressed) and (self._key_post_release_pulses_remaining > 0):
                    if time.ticks_diff(now, self._key_next_pulse_ms) >= 0:
                        pulse = 1
                        self._key_post_release_pulses_remaining -= 1
                        self._key_next_pulse_ms = time.ticks_add(now, self._key_pulse_interval_ms)
            if pulse and not self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, 1)
                self._key_line_state = 1
                self._key_pulse_release_pending = True
            if (not key_pressed) and (self._key_post_release_pulses_remaining == 0):
                if self._key_line_state:
                    cpu_core.set_input(cpu_core.KEY_INT, 0)
                self._key_line_state = 0
                self._key_pulse_release_pending = False
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

    def debug_step(self,pause=True,trace=True,prt=True,trace_index=None,out=None):
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
            #print("not op_bytes")
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
             if prt: out(f"{prefix}[{pc:04X}] {hex_str:<10} | F:{f_str} | {mnemonic} ")
        #return mnemonic
        return f"{prefix}[{pc:04X}] {hex_str:<10} | F:{f_str} | {mnemonic} "
            
    def update_display(self, x_offset=24, y_offset=40):
        """Render LCD to physical display."""
        self.lcd.render_to_display(x_offset, y_offset)

    def press_key(self, key):
        """Press a key on the virtual keyboard."""
        self.keyboard.key_press(key)
        if not self.key_interrupt_via_scan:
            cpu_core.set_input(cpu_core.KEY_INT, 1)
            self._key_line_state = 1
        else:
            # Allow immediate first pulse after key press.
            self._key_next_pulse_ms = time.ticks_ms()
        self._key_commit_pulse_sent = False
        self._key_commit_pulse_count = 0
        if not self.key_interrupt_via_scan:
            self._key_next_pulse_ms = time.ticks_add(time.ticks_ms(), self._key_pulse_interval_ms)

    def release_key(self, key):
        """Release a key on the virtual keyboard."""
        self.keyboard.key_release(key)
        if not self.keyboard.has_key_pressed():
            if self._key_scan_pending():
                self._key_post_release_pulses_remaining = self._key_post_release_pulses_max
            else:
                self._key_post_release_pulses_remaining = 0
            if self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, 0)
            self._key_line_state = 0
            self._key_pulse_release_pending = False
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
            chunk = " ".join(f"${idx + j:02d}={regs[idx + j]:02X}" for j in range(4))
            printer(f"  {chunk}")
        if hasattr(cpu_core, "get_reg8"):
            ia = cpu_core.get_reg8(4)
            ua = cpu_core.get_reg8(3)
            ie = cpu_core.get_reg8(5)
            printer(f"IA: IA={ia:02X} UA={ua:02X} IE={ie:02X}")
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
            "$": [cpu_core.get_reg(i) for i in range(32)] if hasattr(cpu_core, "get_reg") else [0] * 32,
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

    def get_key_buffer_state(self):
        """Return keyboard buffer state (KYCND/RD/WR/first 16-byte shadow)."""
        base = 0x68D9 - self.RAM_START
        return {
            "kycnt": self.ram[base + 0],  # 0x68D9 KYCND
            "rd": self.ram[base + 1],     # 0x68DA
            "wr": self.ram[base + 2],     # 0x68DB
            "buf": bytes(self.ram[base + 6: base + 6 + 16]),  # 0x68DF..0x68EE shadow
        }

    def can_release_active_key(self, keybuf_base=None):
        """Return True when current key press can be safely released.

        If keybuf_base is provided, use composite enqueue detection:
        KYCND or RD/WR change, or buffer shadow update.
        """
        st = self.get_key_scan_state()
        chata = st["chata"]
        if keybuf_base is None:
            return chata == 0x20

        now = self.get_key_buffer_state()
        buffered = (
            now["kycnt"] != keybuf_base.get("kycnt", now["kycnt"]) or
            now["rd"] != keybuf_base.get("rd", now["rd"]) or
            now["wr"] != keybuf_base.get("wr", now["wr"]) or
            now["buf"] != keybuf_base.get("buf", now["buf"])
        )
        return buffered

    def get_irq_scan_state(self):
        """Return key/interrupt related register snapshot for tracing."""
        if not hasattr(cpu_core, "get_reg8"):
            return {"ia": 0, "ib": 0, "ie": 0}
        return {
            "ia": cpu_core.get_reg8(4),
            "ib": cpu_core.get_reg8(2),
            "ie": cpu_core.get_reg8(5),
        }
