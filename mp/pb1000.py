# PB1000 System
#
#
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
    pass

# original keyboard.py is kept but unused.

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
SD_CS_PIN = 15
T_CS_PIN = 16
T_IRQ_PIN = 17

def init_sdcard(spi):
    try:
        from sdcard import SDCard
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        # Use verified 400kHz for stable initialization, restore to 40MHz for LCD
        sd = SDCard(spi, sd_cs, baudrate=400000, restore_baudrate=40000000)
        vfs = os.VfsFat(sd)
        os.mount(vfs, "/sd")
        print("SD Card mounted at /sd")
        return True
    except Exception as e:
        print(f"SD Card mount optional: {e}")
        return False

def init_display():
    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    # Ensure all CS pins are high before starting
    machine.Pin(CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(T_CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)

    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)
    
    # Try mounting SD card
    sd_mounted = init_sdcard(spi)
    
    touch = None
    try:
        from xpt2046 import XPT2046
        touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN)
    except Exception as e:
        print("Touch panel init failed:", e)

    return display, touch, sd_mounted, spi

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
    PROG_TRACE_START = 0xB5D6
    PROG_TRACE_END = 0xB7E6
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
        self.sd_mounted = False
        if isinstance(display, tuple) and len(display) >= 4:
            self.sd_mounted = display[2]
            # display[0] is the display object itself
            display = display[0]

        self._exp_ram_path = self._get_storage_path("ram1.bin")
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

        self.lcd = LCDController(display, debug=self.debug_cfg["lcd"])
        self._save_requested = False
        self.lcd.on_scale_change = self._on_lcd_scale_change
        
        self._disp_x = 16
        self._disp_y = 40
        self.touch_x_offset = 0
        self.touch_y_offset = -104  # adjust if touch mapping seems biased downward
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
            print("PIO UART setup warning")

        # Initialize CPU
        cpu_core.reset(self.debug_cfg["sys"])
        if hasattr(cpu_core, "set_key_debug"):
            cpu_core.set_key_debug(self.debug_cfg["sys"] and self.debug_cfg["kb"])
        if hasattr(cpu_core, "set_lcd_debug"):
            cpu_core.set_lcd_debug(self.debug_cfg["sys"] and self.debug_cfg["lcd"])
              
        self._cb_mem_read = self._mem_read
        self._cb_mem_write = self._mem_write
        self._cb_port_read = self._port_read
        self._cb_port_write = self._port_write

        cpu_core.set_mem_callbacks(self._cb_mem_read, self._cb_mem_write)
        cpu_core.set_port_callbacks(self._cb_port_read, self._cb_port_write)
        
        if hasattr(cpu_core, "use_c_memory"):
            # Keep direct C memory enabled by default even in sys debug mode.
            # Python-managed memory is now opt-in via debug={"c_memory": False}.
            direct_mem = True if direct_mem_override is None else bool(direct_mem_override)
            cpu_core.use_c_memory(direct_mem)
            # RAMView may still exist in Python-callback mode, but delegating writes
            # back through cpu_core.write_mem() would recurse into _mem_write().
            self._ram_is_c_managed = bool(direct_mem)
        # Use C LCD logic
        if hasattr(cpu_core, "use_c_lcd"):
            cpu_core.use_c_lcd(True)

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
            pc = cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0
            ua = cpu_core.get_reg8(3) if hasattr(cpu_core, "get_reg8") else 0
            if self.PROG_TRACE_START <= offset <= self.PROG_TRACE_END:
                print(f"[HD61700] PROG-WR PC={pc:04X} UA={ua:02X} BANK={segment & 0x03} RAM[{offset & 0xFFFF:04X}] <= {data & 0xFF:02X}")
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
                pc = cpu_core.get_pc() if hasattr(cpu_core, "get_pc") else 0
                ua = cpu_core.get_reg8(3) if hasattr(cpu_core, "get_reg8") else 0
                if self.PROG_TRACE_START <= offset <= self.PROG_TRACE_END:
                    print(f"[HD61700] PROG-WR PC={pc:04X} UA={ua:02X} BANK={segment & 0x03} RAM[{offset & 0xFFFF:04X}] <= {data & 0xFF:02X}")
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

    def display_write_probe_hit(self):
        return self._display_probe_hit

    def _port_read(self):
        # Bit 3 is RXD for RS-232C bit-banging
        rx_bit = self._rx_pin.value()
        
        if not hasattr(self, '_port_read_count'):
            self._port_read_count = 0
        self._port_read_count += 1
        
        # PB-1000 ROM boot sequence waits for ON key (bit 0) PRESSED (0) then RELEASED (1).
        # We simulate the key being held down for the first 100 port reads.
        on_key_state = 0 if self._port_read_count < 100 else 1
        
        # Mirror RX bit into port_data bit 3 and inject ON key state into bit 0
        self.port_data = (self.port_data & ~0x09) | (rx_bit << 3) | on_key_state
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

    def _ensure_dir(self, path):
        """Create directory if it doesn't exist."""
        try:
            parts = path.strip("/").split("/")
            curr = ""
            for p in parts:
                curr += "/" + p
                try:
                    os.mkdir(curr)
                except OSError:
                    pass
        except Exception:
            pass

    def _get_storage_path(self, filename):
        """Return the best path for a file, prioritizing SD card."""
        sd_path = "/sd/" + filename
        roms_path = "/roms/" + filename
        root_path = "/" + filename
        
        if self.sd_mounted:
            if filename in ("ram0.bin", "ram1.bin", "regs.json"):
                if self._file_exists(sd_path):
                    return sd_path
                if self._file_exists(roms_path):
                    return roms_path
                return sd_path
            
            if self._file_exists(sd_path):
                return sd_path
        
        if self._file_exists(roms_path):
            return roms_path
        return root_path

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
            path0 = self._get_storage_path("ram0.bin")
            path1 = self._get_storage_path("ram1.bin")
            reg_path = self._get_storage_path("regs.json")
        else:
            path0 = f"{path}/ram0.bin"
            path1 = f"{path}/ram1.bin"
            reg_path = f"{path}/regs.json"

        # Ensure directory for saving exists if it's on SD
        if path0.startswith("/sd/"):
            self._ensure_dir("/sd")

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
            path0 = self._get_storage_path("ram0.bin")
            path1 = self._get_storage_path("ram1.bin")
            reg_path = self._get_storage_path("regs.json")
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
                    cpu_core.set_reg8(2, 0) # Clear IB (Interrupt Request) to prevent instant IRQ on PC=0000
                    cpu_core.set_reg8(5, 0) # Clear IE (Interrupt Enable)
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
        
        # Clear PIO UART buffers upon reset
        if self.pio_uart and hasattr(self.pio_uart, "clear_buffers"):
            self.pio_uart.clear_buffers()
            print("PIO UART buffers cleared.")
            
        self.set_status("SYSTEM RESET", 1500)
        # Re-initialize basic state if needed but usually reset() is enough
        # We might want to keep RAM as is (like a warm reset) or clear it?
        # The user said "force PC to 0x0000", which is what reset() does.

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
            label = key
            if isinstance(key, str):
                if key.startswith("TK"):
                    label = f"TOUCH {key[2:]}"
                else:
                    label = key.upper()
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

        def _read_bank0_u8(addr):
            if hasattr(cpu_core, "read_mem"):
                return cpu_core.read_mem(addr, 0) & 0xFF
            return 0

        def _read_bank0_u16(addr):
            lo = _read_bank0_u8(addr)
            hi = _read_bank0_u8(addr + 1)
            return lo | (hi << 8)

        def _read_bank0_hex(addr, count):
            return " ".join(
                f"{_read_bank0_u8((addr + i) & 0xFFFF):02X}" for i in range(count)
            )

        extra_lines = []
        if pc in (0x9A2F, 0x9A3C):
            sbot = _read_bank0_u16(0x6933)
            forsk = _read_bank0_u16(0x6935)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: SBOT={sbot:04X} FORSK={forsk:04X} FREE={(forsk - sbot) & 0xFFFF:04X}"
            )
        elif pc in (0xB2A3, 0xB2AB):
            memen = _read_bank0_u16(0x6945)
            datdi = _read_bank0_u16(0x6947)
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r45 = (cpu_core.get_reg(5) << 8) | cpu_core.get_reg(4)
            r67 = (cpu_core.get_reg(7) << 8) | cpu_core.get_reg(6)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: MEMEN={memen:04X} DATDI={datdi:04X} FREE={(datdi - memen) & 0xFFFF:04X} REQ={r01:04X} R45={r45:04X} R67={r67:04X}"
            )
        elif pc in (0xB34A, 0xB353):
            memen = _read_bank0_u16(0x6945)
            datdi = _read_bank0_u16(0x6947)
            basdi = _read_bank0_u16(0x6949)
            r23 = (cpu_core.get_reg(3) << 8) | cpu_core.get_reg(2)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: MEMEN={memen:04X} DATDI={datdi:04X} BASDI={basdi:04X} DIRFREE={(r23 - 0x0021) & 0xFFFF:04X} R23={r23:04X}"
            )
        elif pc in (0xB201, 0xB203, 0xB205, 0xB215):
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r23 = (cpu_core.get_reg(3) << 8) | cpu_core.get_reg(2)
            r3031 = (cpu_core.get_reg(31) << 8) | cpu_core.get_reg(30)
            sy = cpu_core.get_sreg(1) & 0x1F
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: SY={sy:02X} R01={r01:04X} R23={r23:04X} R30_31={r3031:04X} R30={cpu_core.get_reg(30):02X} R31={cpu_core.get_reg(31):02X}"
            )
        elif pc in (0xB720, 0xB724, 0xB726, 0xDCBC, 0xDCBF):
            nowfl = _read_bank0_u16(0x6F54)
            ix = cpu_core.get_reg16(0)
            if pc == 0xB720:
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: NOWFL={nowfl:04X} IX={ix:04X} RAM[6F54:6F5B]={_read_bank0_hex(0x6F54, 8)}"
                )
            elif pc == 0xB724:
                r12 = (cpu_core.get_reg(2) << 8) | cpu_core.get_reg(1)
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: NOWFL={nowfl:04X} IX={ix:04X} R1_2={r12:04X} RAM[6F54:6F5B]={_read_bank0_hex(0x6F54, 8)}"
                )
            elif pc == 0xB726:
                r12 = (cpu_core.get_reg(2) << 8) | cpu_core.get_reg(1)
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: NOWFL={nowfl:04X} IX={ix:04X} R1_2={r12:04X} RAM[6F54:6F5B]={_read_bank0_hex(0x6F54, 8)}"
                )
            else:
                sy = cpu_core.get_sreg(1) & 0x1F
                addr = (ix + cpu_core.get_reg(sy)) & 0xFFFF
                m0 = _read_bank0_u8(addr)
                m1 = _read_bank0_u8((addr + 1) & 0xFFFF)
                m2 = _read_bank0_u8((addr + 2) & 0xFFFF)
                m3 = _read_bank0_u8((addr + 3) & 0xFFFF)
                r2526 = (cpu_core.get_reg(26) << 8) | cpu_core.get_reg(25)
                r2728 = (cpu_core.get_reg(28) << 8) | cpu_core.get_reg(27)
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: NOWFL={nowfl:04X} IX={ix:04X} SY={sy:02X} SRC={addr:04X} MEM={m0:02X} {m1:02X} {m2:02X} {m3:02X} R25_26={r2526:04X} R27_28={r2728:04X} RAM[6F54:6F5B]={_read_bank0_hex(0x6F54, 8)}"
                )
        elif pc in (0xD23F, 0xD242):
            iz = cpu_core.get_reg16(2)
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: IZ={iz:04X} R01={r01:04X} RAM[6FCC:6FD3]={_read_bank0_hex(0x6FCC, 8)}"
            )
        elif pc in (0xD2BB, 0xD2C9, 0xD2CD, 0xD2D7, 0xD2E7):
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r23 = (cpu_core.get_reg(3) << 8) | cpu_core.get_reg(2)
            r2021 = (cpu_core.get_reg(21) << 8) | cpu_core.get_reg(20)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: R01={r01:04X} R23={r23:04X} R20_21={r2021:04X} RAM[6FAF:6FB4]={_read_bank0_hex(0x6FAF, 6)} RAM[6FCC:6FD3]={_read_bank0_hex(0x6FCC, 8)}"
            )
            if pc in (0xD2CD, 0xD2D7):
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: RAM[6E1D:6E24]={_read_bank0_hex(0x6E1D, 8)} RAM[6F74:6F7B]={_read_bank0_hex(0x6F74, 8)}"
                )
        elif pc in (0xB1C2, 0xB1E8, 0xB1EB, 0xB1F4, 0xB201, 0xB210):
            ix = cpu_core.get_reg16(0)
            r0102 = (cpu_core.get_reg(2) << 16) | (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r34 = (cpu_core.get_reg(4) << 8) | cpu_core.get_reg(3)
            r5 = cpu_core.get_reg(5)
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: IX={ix:04X} R0={cpu_core.get_reg(0):02X} R1={cpu_core.get_reg(1):02X} R2={cpu_core.get_reg(2):02X} R3={cpu_core.get_reg(3):02X} R4={cpu_core.get_reg(4):02X} R5={r5:02X} R34={r34:04X} RAM[6F74:6F7B]={_read_bank0_hex(0x6F74, 8)}"
            )
            if ix:
                extra_lines.append(
                    f"BSAVE-OM {pc:04X}: IXMEM[{ix:04X}]={_read_bank0_hex(ix, 8)}"
                )
        elif pc in (0xDCE4, 0xDCE7, 0xE00B, 0xE011, 0xE013, 0xE019):
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r2324 = (cpu_core.get_reg(24) << 8) | cpu_core.get_reg(23)
            r2526 = (cpu_core.get_reg(26) << 8) | cpu_core.get_reg(25)
            r2728 = (cpu_core.get_reg(28) << 8) | cpu_core.get_reg(27)
            r3031 = (cpu_core.get_reg(31) << 8) | cpu_core.get_reg(30)
            sy = cpu_core.get_sreg(1) & 0x1F
            extra_lines.append(
                f"BSAVE-OM {pc:04X}: SY={sy:02X} R01={r01:04X} R23_24={r2324:04X} R25_26={r2526:04X} R27_28={r2728:04X} R30_31={r3031:04X}"
            )
        elif pc == 0xABBD:
            r01 = (cpu_core.get_reg(1) << 8) | cpu_core.get_reg(0)
            r45 = (cpu_core.get_reg(5) << 8) | cpu_core.get_reg(4)
            r67 = (cpu_core.get_reg(7) << 8) | cpu_core.get_reg(6)
            r3031 = (cpu_core.get_reg(31) << 8) | cpu_core.get_reg(30)
            extra_lines.append(
                f"BSAVE-OM TRAP {pc:04X}: R01={r01:04X} R45={r45:04X} R67={r67:04X} R30_31={r3031:04X} IX={cpu_core.get_reg16(0):04X} IZ={cpu_core.get_reg16(2):04X}"
            )

        line = f"{prefix}[{pc:04X}] {hex_str:<10} | F:{f_str} | {mnemonic} "
        if pause:
            print(f"{line}| ",end="")
            for extra in extra_lines:
                print()
                print(extra, end="")
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
            if prt:
                out(line)
                for extra in extra_lines:
                    out(extra)
        return line
            
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

#     def set_pc(self, addr):
#             """Set CPU PC (debug helper)."""
#             cpu_core.set_pc(addr & 0xFFFF)

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





