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

try:
    from lcd_controller_c import LCDControllerC as LCDController
    _LCD_BACKEND = "C"
except ImportError:
    from lcd_controller import LCDController
    _LCD_BACKEND = "Python"

from keyboard import KeyboardMatrix

# PIO UART for RS-232C passthrough (optional)
try:
    from pio_uart import PioUart
    _HAS_PIO_UART = True
except ImportError:
    _HAS_PIO_UART = False

SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22
T_CS_PIN = 16
T_IRQ_PIN = 17

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
    touch = None
    try:
        from xpt2046 import XPT2046
        touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN)
    except Exception as e:
        print("Touch panel init failed:", e)

    return display, touch

def draw_bezel(display, scale=1.0, x=16, y=40):
    """Draws the PB-1000 LCD bezel scaled to fit the display."""
    # Inner LCD area size
    lw = int(192 * scale)
    lh = int(32 * scale)
    
    # Margin and boarders (scaled or fixed?)
    # Original: (12, 36, 296, 72, 0x4228) -> inner (16,40, 288,64)
    # 288 width was exactly 192 * 1.5.
    # Let's make it more general.
    
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

class PB1000System:
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

    def __init__(self, display=None, debug=False, restore_registers=True):
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

        if hasattr(cpu_core, "get_ram_view"):
            raw_view = cpu_core.get_ram_view()
            self.ram = RAMView(cpu_core, memoryview(raw_view), self.RAM_SIZE, self.RAM_START)
            self._ram_is_c_managed = True
            
            if self.has_exp and hasattr(cpu_core, "get_exp_ram_view"):
                exp_raw_view = cpu_core.get_exp_ram_view()
                self.exp_ram = RAMView(cpu_core, memoryview(exp_raw_view), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=1)
            else:
                self.exp_ram = bytearray(self.EXP_RAM_SIZE)
        else:
            self.ram = bytearray(self.RAM_SIZE)
            self.exp_ram = bytearray(self.EXP_RAM_SIZE)
            
        self.rom0 = bytearray(0)
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
        self.key_interrupt_via_scan = True
        self.key_reassert_enabled = False
        self._chata_zero_since_ms = None
        self._chata_stuck_logged = False
        self._key_noresp_since_ms = None
        self._key_noresp_logged = False
        self._key_noresp_threshold_ms = 1200
        self._display_probe_active = False
        self._display_probe_label = ""
        self._display_probe_hit = False

        self.lcd = LCDController(display, debug=self.debug_cfg["lcd"])
        self._save_requested = False
        self.lcd.on_scale_change = self._on_lcd_scale_change
        self.keyboard = KeyboardMatrix(debug=self.debug_cfg["kb"])
        self._disp_x = 16
        self._disp_y = 40
        self.port_data = 0
        self.console_uart = None  # Set externally from main.py
        self.status_msg = ""
        self.status_expiry_ms = 0
        self._status_rendered_msg = None
        self.pio_uart = None      # Set externally from main.py

        # Raw UART Pins for bit-level passthrough
        self._tx_pin = machine.Pin(6, machine.Pin.OUT, value=1)
        self._rx_pin = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_UP)

        # PIO UART for RS-232C (Reserved for high-level Python access)
        # We now use this for MMIO 0x0C00-0x0C03 as well.
        try:
            # Note: main.py will replace this with a real PioUart instance if needed
            print("PIO UART support enabled in system.")
        except Exception as e:
            print(f"PIO UART setup warning: {e}")

        # LCD write state tracking for console mirror
        self._lcd_cmd_state = 0     # 0=idle, 3=DRAW_CHAR pending
        self._lcd_char_bytes = []   # Accumulate bytes for DRAW_CHAR

        # Initialize CPU
        cpu_core.reset(self.debug_cfg["sys"])
        if hasattr(cpu_core, "set_key_debug"):
            cpu_core.set_key_debug(self.debug_cfg["sys"] and self.debug_cfg["kb"])
        if hasattr(cpu_core, "set_lcd_debug"):
            cpu_core.set_lcd_debug(self.debug_cfg["sys"] and self.debug_cfg["lcd"])
              
        self._cb_mem_read = self._mem_read
        self._cb_mem_write = self._mem_write
        self._cb_lcd_read = self.lcd.lcd_read
        self._cb_lcd_write = self._intercept_lcd_write
        self._cb_lcd_ctrl = self._intercept_lcd_ctrl
        self._cb_kb_read = self.keyboard.kb_read
        self._cb_kb_write = self.keyboard.kb_write
        self._cb_port_read = self._port_read
        self._cb_port_write = self._port_write

        cpu_core.set_mem_callbacks(self._cb_mem_read, self._cb_mem_write)
        cpu_core.set_lcd_callbacks(self._cb_lcd_read, self._cb_lcd_write, self._cb_lcd_ctrl)
        cpu_core.set_kb_callbacks(self._cb_kb_read, self._cb_kb_write)
        cpu_core.set_port_callbacks(self._cb_port_read, self._cb_port_write)
        
        if hasattr(cpu_core, "use_c_memory"):
            direct_mem = (not self.debug_cfg["sys"]) if direct_mem_override is None else bool(direct_mem_override)
            cpu_core.use_c_memory(direct_mem)
        if hasattr(cpu_core, "use_c_lcd"):
            direct_lcd = (not self.debug_cfg["sys"]) if direct_lcd_override is None else bool(direct_lcd_override)
            cpu_core.use_c_lcd(direct_lcd)
            
        if restore_registers:
            self.load_state()
            
    def load_rom(self, path, slot=0, keep_copy=False):
        try:
            with open(path, 'rb') as f:
                data = f.read()
                if slot == 0:
                    self.rom0 = data if keep_copy else None
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(0, data)
                else:
                    self.rom1 = data if keep_copy else None
                    if hasattr(cpu_core, "load_rom"):
                        cpu_core.load_rom(1, data)
        except OSError as e:
            print(f"ROM load error ({path}): {e}")

    def _mem_read(self, segment, offset):
        return self._mem_read_impl(segment, offset)

    def _mem_read_impl(self, segment, offset):
        if offset < 0x8000:
            if offset < 0x2000:
                if self.rom0 is not None:
                    if offset == 0x0C00:
                        # Status register: Bit 0=RX Ready, Bit 1=TX Ready
                        status = 0x02 # TX Ready
                        if self.pio_uart and self.pio_uart.any():
                            status |= 0x01 # RX Ready
                        return status
                    if offset == 0x0C01:
                        # RX Data Register
                        if self.pio_uart:
                            data = self.pio_uart.read(1)
                            return data[0] if data else 0
                        return 0
                    if offset < len(self.rom0):
                        return self.rom0[offset]
                elif hasattr(cpu_core, "read_mem"):
                    return cpu_core.read_mem(offset, segment)
            elif offset >= self.RAM_START:
                return self.ram[(offset - self.RAM_START) % len(self.ram)]
            else:
                return 0xFF

        bank = segment & 0x03
        if offset >= 0x8000:
            if bank == 0:
                rom_off = offset - 0x8000
                if self.rom1 is not None:
                    if rom_off < len(self.rom1):
                        return self.rom1[rom_off]
                elif hasattr(cpu_core, "read_mem"):
                    return cpu_core.read_mem(offset, segment)
            elif bank == 1 and self.has_exp:
                exp_off = offset - 0x8000
                if exp_off < len(self.exp_ram):
                    return self.exp_ram[exp_off]
        return 0xFF

    def _mem_write(self, segment, offset, data):
        if self.RAM_START <= offset < self.SYS_ROM_START:
            ram_index = (offset - self.RAM_START) % len(self.ram)
            if self._ram_is_c_managed and hasattr(cpu_core, "write_mem"):
                cpu_core.write_mem(offset & 0xFFFF, data & 0xFF, segment)
            else:
                self.ram[ram_index] = data
        elif offset == 0x0C03:
            # TX Data Register: Output character to pio_uart and console
            if self.pio_uart:
                self.pio_uart.write(data)
            
            char = chr(data & 0x7F)
            if self.console_uart:
                self.console_uart.write(char)
            else:
                print(char, end='')
        elif offset >= 0x8000 and (segment & 0x03) == 1 and self.has_exp:
            exp_off = offset - 0x8000
            if exp_off < len(self.exp_ram):
                if self._ram_is_c_managed and hasattr(cpu_core, "write_mem"):
                    cpu_core.write_mem(offset & 0xFFFF, data & 0xFF, segment)
                else:
                    self.exp_ram[exp_off] = data

    def _is_lcd_vram_addr(self, offset):
        return (0x6100 <= offset <= 0x61FF) or (0x6201 <= offset <= 0x6850)

    def arm_display_write_probe(self, label="EVENT"):
        self._display_probe_active = True
        self._display_probe_hit = False
        self._display_probe_label = label

    def _intercept_lcd_ctrl(self, data):
        self.lcd.lcd_ctrl(data)
        # Tracking for console mirror
        self._lcd_op_command = (data & 0x01) != 0
        self._lcd_cmd_buf = []

    def _intercept_lcd_write(self, data):
        self.lcd.lcd_write(data)
        
        if self.console_uart is None:
            return

        if self._lcd_op_command:
            # Command mode (OP=1)
            self._lcd_cmd_buf.append(data)
            cmd_id = self._lcd_cmd_buf[0] & 0x0F
            
            # Command lengths: 0x03 (DRAW_CHAR) is 3 bytes
            expected = 3 if cmd_id == 0x03 else 1
            if len(self._lcd_cmd_buf) >= expected:
                self._lcd_current_mode = cmd_id
                self._lcd_cmd_buf = []
        else:
            # Data mode (OP=0)
            if getattr(self, "_lcd_current_mode", 0) == 0x03:
                # DRAW_CHAR mode - data is character code
                decoded = self._decode_pb1000_char(data)
                if decoded:
                    self.console_uart.write(decoded)

    def _decode_pb1000_char(self, data):
        """Decode PB-1000 character code to ASCII for console mirror."""
        # Nibble swap as used in HD61700 / LCD protocol
        code = ((data & 0x0F) << 4) | (data >> 4)
        
        # Standard ASCII range
        if 0x20 <= code <= 0x7E:
            return chr(code)
        if code == 0x0D or code == 0x0A:
            return "\r\n"
        if code == 0xFF: # PB-1000 often uses 0xFF as spacer or blank?
            return None
        return None

    def display_write_probe_hit(self):
        return self._display_probe_hit

    def _port_read(self):
        # Bit 3 is RXD for RS-232C bit-banging
        rx_bit = self._rx_pin.value()
        # Mirror RX bit into port_data bit 3
        self.port_data = (self.port_data & ~0x08) | (rx_bit << 3)
        return self.port_data

    def _port_write(self, data):
        self.port_data = data
        # Bit 2 is TXD for RS-232C bit-banging
        # Output the bit directly to the physical pin
        self._tx_pin.value((data >> 2) & 1)

    def _register_dump_path(self):
        return "/roms/register.bin"

    def _restore_registers_from_dump(self):
        path = self._register_dump_path()
        try:
            with open(path, 'rb') as f:
                data = f.read(36)
            if len(data) >= 36:
                cpu_core.set_registers(data[:36])
                print(f"registers restored from {path}")
        except OSError:
            pass

    def _file_exists(self, path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def _ram_path(self, slot=0):
        return f"/roms/ram{slot}.bin"

    def load_ram(self):
        path0 = self._ram_path(0)
        try:
            with open(path0, 'rb') as f:
                val = f.read(self.RAM_SIZE)
            if val:
                for i in range(len(val)):
                    self.ram[i] = val[i]
                print(f"Standard RAM restored from {path0}")
        except OSError:
            pass

        if self.has_exp:
            path1 = self._ram_path(1)
            try:
                with open(path1, 'rb') as f:
                    val = f.read(self.EXP_RAM_SIZE)
                if val:
                    for i in range(len(val)):
                        self.exp_ram[i] = val[i]
                    print(f"Expanded RAM restored from {path1}")
            except OSError:
                pass

    def save_state(self, path=None):
        import json
        import gc
        gc.collect()
        if path is None:
            path0 = "/roms/ram0.bin"
            path1 = "/roms/ram1.bin"
            reg_path = "/roms/regs.json"
        else:
            path0 = f"{path}/ram0.bin"
            path1 = f"{path}/ram1.bin"
            reg_path = f"{path}/regs.json"

        try:
            with open(path0, "wb") as f:
                buf = self.ram._view if isinstance(self.ram, RAMView) else self.ram
                f.write(buf)
            if self.has_exp:
                with open(path1, "wb") as f:
                    buf = self.exp_ram._view if isinstance(self.exp_ram, RAMView) else self.exp_ram
                    f.write(buf)
        except Exception as e:
            print(f"Error saving RAM: {e}")

        try:
            regs = {
                "pc": int(cpu_core.get_pc()),
                "flags": int(cpu_core.get_flags()),
                "ia": int(cpu_core.get_reg8(4)),
                "ib": int(cpu_core.get_reg8(2)),
                "ie": int(cpu_core.get_reg8(5)),
                "ua": int(cpu_core.get_reg8(3)),
                "regmain": [int(cpu_core.get_reg(i)) for i in range(32)],
                "regsir": [int(cpu_core.get_sreg(i)) for i in range(3)],
                "reg16": [int(cpu_core.get_reg16(i)) for i in range(6)],
            }
            with open(reg_path, "w") as f:
                json.dump(regs, f)
            print(f"State saved to {reg_path}")
        except Exception as e:
            print(f"Error saving registers: {e}")

    def save_ram(self):
        """Compatibility alias for save_state."""
        self.save_state()

    def load_state(self, path=None):
        import json
        if path is None:
            path0 = "/roms/ram0.bin"
            path1 = "/roms/ram1.bin"
            reg_path = "/roms/regs.json"
            if not self._file_exists(path0):
                path0 = "/ram0.bin"
                path1 = "/ram1.bin"
                reg_path = "/regs.json"
                if not self._file_exists(reg_path):
                    reg_path = "/roms/register.bin"
        else:
            path0 = f"{path}/ram0.bin"
            path1 = f"{path}/ram1.bin"
            reg_path = f"{path}/regs.json"

        print(f"Loading state: RAM={path0}, EXP={path1}, REGS={reg_path}")
        import gc
        gc.collect()

        def _load_to_ram(file_path, ram_target, slot):
            if not self._file_exists(file_path):
                 print(f"RAM file not found: {file_path}")
                 return False
            try:
                with open(file_path, "rb") as f:
                    if hasattr(cpu_core, "load_ram"):
                        data = f.read()
                        cpu_core.load_ram(slot, data)
                        print(f"RAM slot {slot} restored via C-API from {file_path} ({len(data)} bytes)")
                    else:
                        # Fallback for old/generic core
                        offset = 0
                        total = 0
                        while True:
                            chunk = f.read(1024)
                            if not chunk: break
                            ram_target[offset:offset+len(chunk)] = chunk
                            offset += len(chunk)
                            total += len(chunk)
                        print(f"RAM slot {slot} restored via Python loop from {file_path} ({total} bytes)")
                return True
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
                return False

        _load_to_ram(path0, self.ram, 0)
        if self.has_exp:
            _load_to_ram(path1, self.exp_ram, 1)

        try:
            if self._file_exists(reg_path):
                if reg_path.endswith(".json"):
                    with open(reg_path, "r") as f:
                        regs = json.load(f)
                    cpu_core.set_pc(0x0000) # User requested: resetting PC to 0x0000 after RAM load
                    if hasattr(cpu_core, "set_flags"):
                        cpu_core.set_flags(int(regs["flags"]))
                    cpu_core.set_reg8(4, int(regs["ia"]))
                    cpu_core.set_reg8(2, int(regs["ib"]))
                    cpu_core.set_reg8(5, int(regs["IE"])) if "IE" in regs else cpu_core.set_reg8(5, int(regs.get("ie", 0)))
                    cpu_core.set_reg8(3, int(regs["ua"]))
                    for i, v in enumerate(regs["regmain"]):
                        cpu_core.set_reg(i, int(v))
                    for i, v in enumerate(regs["regsir"]):
                        cpu_core.set_sreg(i, int(v))
                    for i, v in enumerate(regs["reg16"]):
                        cpu_core.set_reg16(i, int(v))
                    print(f"Registers restored (RAM/Regs loaded, PC set to 0x0000 per request)")
                else:
                    self._restore_registers_from_dump()
            else:
                print(f"Register file not found: {reg_path}")
        except Exception as e:
            print(f"Error loading registers: {e}")

    def step(self, cycles=100, stop_pc=-1):
        return cpu_core.execute(cycles, stop_pc)

    def reset_emulator(self):
        """Perform a hardware-like reset (PC=0x0000)."""
        print("Emulator Reset triggered (PC=0x0000)")
        cpu_core.reset(self.debug_cfg["sys"])
        self.set_status("SYSTEM RESET", 1500)
        # Re-initialize basic state if needed but usually reset() is enough
        # We might want to keep RAM as is (like a warm reset) or clear it?
        # The user said "force PC to 0x0000", which is what reset() does.

    def tick_timer(self):
        cpu_core.timer_tick()

    def _key_interrupt_requested_by_scan(self):
        if not hasattr(cpu_core, "get_reg8"):
            return False
        ia = cpu_core.get_reg8(4) & 0xFF
        if (ia & 0x80) == 0:
            return False
        mask = self._KEY_PULSE_MASK_TABLE[(ia >> 4) & 0x03]
        ky = self.keyboard.kb_read() & 0xF0FF
        if mask != 0 and (ky & mask) != 0:
            return True
        return ky != 0

    def _key_scan_pending(self):
        base = 0x68D2 - self.RAM_START
        chata = self.ram[base + 1]
        keycm = self.ram[base + 2]
        return (chata != 0x20) or (keycm != 0x00)

    def service_input_lines(self):
        if self.key_interrupt_via_scan:
            if self._key_pulse_release_pending and self._key_line_state:
                cpu_core.set_input(cpu_core.KEY_INT, 0)
                self._key_line_state = 0
                self._key_pulse_release_pending = False

            pulse = 0
            key_pressed = self.keyboard.has_key_pressed()

            if self.is_key_input_enabled():
                ia = cpu_core.get_reg8(4) if hasattr(cpu_core, "get_reg8") else 0
                now = time.ticks_ms()
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
            return

    def service_pio_uart(self):
        """Service PIO UART TX/RX buffers.
        Only needed if pio_uart is active.
        """
        if self.pio_uart is not None and hasattr(self.pio_uart, "_sm_tx"):
             # main.py already handles polling for MMIO, but we keep this for consistency
             self.pio_uart.service_tx()
             self.pio_uart.service_rx()

    def update_display(self, x_offset=None, y_offset=None):
        if x_offset is not None: self._disp_x = x_offset
        if y_offset is not None: self._disp_y = y_offset
        self.lcd.render_to_display(self._disp_x, self._disp_y)
        self._render_status_bar()

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
        
        y_pos = self._disp_y + int(32 * self.lcd.scale) + 12
        display = self.lcd.display
        
        # Clear/Draw backdrop
        display.fill_rect(self._disp_x, y_pos - 2, 200, 12, 0x0000)
        
        if active_msg:
            self._draw_text(display, self._disp_x, y_pos, active_msg, 0x07FF) # Cyan text
            
        self._status_rendered_msg = active_msg

        # Draw status text below the bezel
        # LCD height is 32 * scale. Bezel margin is ~4.
        y_pos = self._disp_y + int(32 * self.lcd.scale) + 12
        display = self.lcd.display
        
        # Simple backdrop for text
        display.fill_rect(self._disp_x, y_pos - 2, 200, 12, 0x0000)
        self._draw_text(display, self._disp_x, y_pos, self.status_msg, 0x07FF) # Cyan text

    def _draw_text(self, display, x, y, text, color):
        # Extremely minimal 5x7 font (subset for common labels)
        font = {
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
        curr_x = x
        for char in text.upper():
            bits = font.get(char, 0x7F7F7F7F7F) # Block for unknown
            # Hex bytes are ordered MSB...LSB, so i=0 (left) should be MSB
            for i in range(5):
                col_bits = (bits >> ((4 - i) * 8)) & 0xFF
                for j in range(8):
                    if col_bits & (1 << j):
                        display.fill_rect(curr_x + i, y + j, 1, 1, color)
            curr_x += 6

    def _on_lcd_scale_change(self, scale):
        """Callback from LCDController when scale is changed."""
        if hasattr(self.lcd, 'display') and self.lcd.display:
            # Re-draw the bezel with new scale
            draw_bezel(self.lcd.display, scale, self._disp_x, self._disp_y)
            # Ensure the LCD content itself is marked dirty to fill the new bezel
            if hasattr(self.lcd, 'dirty'):
                self.lcd.dirty = True

    def press_key(self, key):
        self.keyboard.key_press(key)
        # Automatically show label on status bar
        if hasattr(self, 'set_status'):
            label = key
            if isinstance(key, str):
                if key.startswith("TK"):
                    label = f"TOUCH {key[2:]}"
                else:
                    label = key.upper()
            self.set_status(label)

        if not self.key_interrupt_via_scan:
            cpu_core.set_input(cpu_core.KEY_INT, 1)
            self._key_line_state = 1
        else:
            self._key_next_pulse_ms = time.ticks_ms()

    def release_key(self, key):
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

    def power_on(self, force=False):
        cpu_core.set_input(cpu_core.SW, 1)
        if hasattr(cpu_core, "set_pc"):
            current_pc = cpu_core.get_pc()
            # If the user has explicitly loaded a state (which sets PC=0 in our new logic),
            # or if it's a completely fresh start where we want the default vector 0x0001.
            # But here the user specifically wants 0x0000 to be preserved.
            
            if force:
                cpu_core.set_pc(0x0001)
                print("System power on forced to reset vector (PC=0x0001)")
            elif current_pc == 0x0001:
                 # Already at reset vector, no change needed or just confirmation
                 print("System power on at reset vector (PC=0x0001)")
            elif current_pc == 0x0000:
                 # Stick with 0x0000 if that's where we are (e.g. after load_state)
                 print("System power on at PC=0x0000 (Preserved)")
            else:
                print(f"System resumed at PC={current_pc:#06x}")

    def set_on_int(self, state):
        cpu_core.set_input(cpu_core.ON_INT, 1 if state else 0)

    @property
    def pc(self):
        return cpu_core.get_pc()

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
