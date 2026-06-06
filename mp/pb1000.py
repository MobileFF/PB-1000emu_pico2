# PB1000 System
#
#
import hd61700 as cpu_core
import gc
import os
import sys
import time
import machine
from ili9341 import ILI9341
from fdd_protocol import FDDProtocol
from fdd_storage import ImageStorageBackend
from md100_dos import MD100Dos

try:
    from lcd_controller_c import LCDControllerC as LCDController
    _LCD_BACKEND = "C"
except ImportError:
    pass

try:
    import lcd_c
except ImportError:
    lcd_c = None

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
PD_RES = 0x08
PD_PWR = 0x10
PD_STR = 0x04
PD_ACK = 0x10  # Port B bit 4
PD_BEEP_MASK = 0xC0  # bit6 と bit7: BEEP 制御ビット
VFDD_IO_READ_ADDR = 0x0C03
VFDD_IO_WRITE_ADDR = 0x0C04
ENABLE_VIRTUAL_FDD = True

def init_sdcard(spi):
    try:
        from sdcard import SDCard
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        # Use verified 400kHz for stable initialization, restore to 40MHz for LCD
        sd = SDCard(spi, sd_cs, baudrate=400000, restore_baudrate=40_000_000)
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
        touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN, x_min=325, x_max=3850)
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

def load_virtual_fdd_config(path):
    try:
        os.stat(path)
    except OSError:
        return None
    section = ""
    values = {}
    with open(path, "r") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line[0] in ("#", ";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if section != "disk" or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lower()
            v = v.split(";", 1)[0].split("#", 1)[0].strip()
            values[k] = v
    if not values:
        return None
    def _bool(s):
        return s.lower() in ("1", "true", "yes", "on")
    raw_path = values.get("path", "").strip()
    if raw_path and not raw_path.startswith("/"):
        parts = path.rsplit("/", 1)
        base = parts[0] if len(parts) > 1 else ""
        raw_path = base + "/" + raw_path if base else raw_path
    return {
        "config_path": path,
        "enabled": _bool(values.get("enabled", "false")),
        "backend": values.get("backend", "image").strip().lower() or "image",
        "path": raw_path,
        "readonly": _bool(values.get("readonly", "false")),
    }


class PB1000System:
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

        # Bank presence detection: has_bank[0]=ROM1 (always), [1..3]=RAM banks
        self.has_bank = [True, False, False, False]
        for slot in range(1, 4):
            path = self._get_storage_path(f"ram{slot}.bin")
            self.has_bank[slot] = self._file_exists(path)
        print(f"Bank detection: RAM1={'Y' if self.has_bank[1] else 'N'} "
              f"RAM2={'Y' if self.has_bank[2] else 'N'} "
              f"RAM3={'Y' if self.has_bank[3] else 'N'}")
        if hasattr(cpu_core, "set_has_exp_ram"):
            cpu_core.set_has_exp_ram(self.has_bank[1])

        if hasattr(cpu_core, "get_ram_view"):
            raw_view = cpu_core.get_ram_view()
            self.ram = RAMView(cpu_core, memoryview(raw_view), self.RAM_SIZE, self.RAM_START)
            # Bank 1 (exp_ram): backward-compat view
            if self.has_bank[1] and hasattr(cpu_core, "get_exp_ram_view"):
                exp_raw_view = cpu_core.get_exp_ram_view()
                _b1 = RAMView(cpu_core, memoryview(exp_raw_view), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=0x10)
            elif self.has_bank[1]:
                _b1 = bytearray(self.EXP_RAM_SIZE)
            else:
                _b1 = bytearray(0)
            # Banks 2 and 3
            _bank_views = []
            for slot in range(2, 4):
                if self.has_bank[slot] and hasattr(cpu_core, "get_bank_view"):
                    rv = cpu_core.get_bank_view(slot)
                    _bank_views.append(RAMView(cpu_core, memoryview(rv), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=slot << 4))
                elif self.has_bank[slot]:
                    _bank_views.append(bytearray(self.EXP_RAM_SIZE))
                else:
                    _bank_views.append(bytearray(0))
        else:
            self.ram = bytearray(self.RAM_SIZE)
            _b1 = bytearray(self.EXP_RAM_SIZE)
            _bank_views = [bytearray(self.EXP_RAM_SIZE), bytearray(self.EXP_RAM_SIZE)]

        # _bank_ram[0]=unused, [1]=RAM1, [2]=RAM2, [3]=RAM3
        self.exp_ram = _b1
        self._bank_ram = [None, _b1, _bank_views[0], _bank_views[1]]
            
        self.rom0 = bytearray(0)
        self.rom1 = bytearray(0)
        self.rom_bank = 0
        self._key_trace_last = {}

        self.lcd = LCDController(display, debug=self.debug_cfg["lcd"])
        self._save_requested = False
        self.lcd.on_scale_change = self._on_lcd_scale_change
        if hasattr(self.lcd, 'set_char_output_callback'):
            self.lcd.set_char_output_callback(self._on_lcd_char_output)
        
        self._disp_x = 16
        self._disp_y = 40
        self.touch_x_offset = 0
        self.touch_y_offset = -104  # adjust if touch mapping seems biased downward
        self.port_data = 0
        self._console_uart = None      # active uart (None = serial console OFF)
        self._console_uart_hw = None   # uart hardware ref; set by main_boot, used by menu
        self._lcd_had_output = False
        self.status_msg = ""
        self.status_expiry_ms = 0
        self._status_rendered_msg = None
        self.pio_uart = None      # Set externally from main.py
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
            print("PIO UART setup warning")

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
        
        # lcd_char callback is registered on demand via the console_uart property setter

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
                self._c_port_active = False

        if restore_registers:
            self.load_state()
            
    def load_rom(self, path, slot=0, keep_copy=False):
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
        except OSError as e:
            print(f"ROM load error ({path}): {e}")

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

    def _is_lcd_vram_addr(self, offset):
        return (0x6100 <= offset <= 0x61FF) or (0x6201 <= offset <= 0x6850)

    def arm_display_write_probe(self, label="EVENT"):
        self._display_probe_active = True
        self._display_probe_hit = False
        self._display_probe_label = label

    def display_write_probe_hit(self):
        return self._display_probe_hit

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
            # Status Register: Return ready flags.
            # FDD mode requires BOTH _virtual_fdd_interface_powered AND _virtual_fdd_ack.
            # _virtual_fdd_ack is only True after a genuine FDD STR strobe fires.
            # RS-232C RECEIVE writes port D (setting _virtual_fdd_interface_powered) but
            # never issues a STR strobe, so _virtual_fdd_ack stays False and UART RX
            # remains visible throughout the entire RS-232C transfer.
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
                    # PB-1000 MD-100 Status:
                    # Bit 0: Low Battery (LB) flag (Active High).
                    # Bit 1: FDD Present / battery OK path for MD-100 polling.
                    # Bit 6: Interface Ready (Active High/1).
                    # Bit 7: Power Ready (Active High/1).
                    # Keep bit 0 clear to avoid ROM D8D0 -> LB Error.
                    status |= 0x02
                finally:
                    # Note: We keep it active if many polls are expected, but here we
                    # toggle it per-poll to be safe.
                    self._fdd_active = False
                if self.pio_uart and self.pio_uart.any() and not getattr(self, '_uart_vfdd_warn', False):
                    self._uart_vfdd_warn = True
                    print(f"[UART_WARN] FDD ack+power active while UART RX pending ({self.pio_uart.any()}B)")
            else:
                # Bytes stay in the Python PIO buffer (_rx_buffer); the MMIO
                # callback for 0x0C02 (IO read path) serves them directly.
                # service_pio_uart_bridge() only signals INT1 via uart_signal_rx()
                # without moving bytes to the C UART FIFO.
                if self.pio_uart and self.pio_uart.any():
                    status |= 0x01 # RX Ready (RS-232C mode)
                else:
                    status |= 0x02 # TX Ready
            return status
            
        elif index == 2:
            if self.pio_uart:
                data = self.pio_uart.read(1)
                self._io_rd_regs[2] = data[0] if data else 0
                if data and not getattr(self, '_uart_rx_logged', False):
                    self._uart_rx_logged = True
                    print(f"[UART_RX] ROM read first byte: {data[0]:#04x}")
                # Deassert INT1 when Python buffer is now empty so the CPU
                # does not re-enter the ISR before the next byte arrives.
                if not self.pio_uart.any() and hasattr(cpu_core, 'uart_clear_rx_signal'):
                    cpu_core.uart_clear_rx_signal()
                # ROM consumed the EOF byte — request auto-BREAK via main loop.
                # _pio_uart_eof_pending is checked each main loop tick; the
                # KeyboardInputManager queues BRK and waits for is_key_input_enabled
                # before pressing, so it fires only after the ROM finishes processing.
                if data and data[0] == 0x1A and not getattr(self, '_pio_uart_eof_pending', False):
                    self._pio_uart_eof_pending = True
                    print("[UART_EOF] EOF byte read by ROM; auto-BREAK scheduled")
            else:
                self._io_rd_regs[2] = 0
            return self._io_rd_regs[2]
            
        elif index == 3:
            if not self._virtual_fdd_interface_powered:
                # Always return 0x55 (MD-100 identifier) when the VFDD is
                # configured but powered off. This prevents the ROM from
                # storing 0xFF in OPTCD (which would switch it to the non-
                # MD-100 code path that checks every dir-entry byte for errors).
                return 0x55

            # Return the FDD data register value without any error-bit masking.
            # LB/FM error prevention is handled upstream by the D8D0 PC-based
            # approach (returning 0x55 for non-D924 reads), so masking here
            # is no longer needed and only corrupts legitimate data bytes
            # (e.g., status=0x10 would become 0x00 with the old 0xEE mask).
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
            # 0x0C03 = TX Data Register (UART) OR FDD read-data port.
            # Suppress PIO UART TX only during active FDD transfer (powered AND ack).
            # RS-232C operations set _virtual_fdd_interface_powered without _virtual_fdd_ack,
            # so TX is allowed through for RS-232C even when the interface power bit is low.
            _in_fdd_mode = (self.has_virtual_fdd()
                            and self._virtual_fdd_interface_powered
                            and self._virtual_fdd_ack)
            if not _in_fdd_mode:
                if self.pio_uart:
                    self.pio_uart.write(data)
            # console_uart (UART1, GP4/GP5) is on independent pins and must
            # always receive BASIC PRINT output regardless of FDD power state.
            char = chr(data & 0x7F)
            if self.console_uart:
                self.console_uart.write(char)
            else:
                print(char, end="")

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
        検索順: /sd/ext/ → /ext/  (先に見つかった方を優先)
        各モジュールは register(system) 関数を持つこと。
        """
        import os, sys
        for ext_dir in ("/sd/ext", "/ext"):
            try:
                files = os.listdir(ext_dir)
            except OSError:
                continue
            if ext_dir not in sys.path:
                sys.path.insert(0, ext_dir)
            for fname in sorted(files):
                if not fname.endswith(".py") or fname.startswith("_"):
                    continue
                mod_name = fname[:-3]
                try:
                    mod = __import__(mod_name)
                    if hasattr(mod, "register"):
                        mod.register(self)
                        print(f"EXT: loaded {mod_name} from {ext_dir}")
                    else:
                        print(f"EXT: {mod_name} has no register(), skipped")
                except Exception as e:
                    print(f"EXT: {mod_name} load error: {e}")
            break  # 最初に見つかったディレクトリのみ使用

    def _log_vfdd(self, msg):
        pass

    def _handle_virtual_fdd_port_write(self, data):
        if not self.has_virtual_fdd():
            self._port_last_write = data & 0xFF
            return

        current = data & 0xFF
        previous = self._port_last_write
        was_powered = (previous & PD_PWR) == 0
        powered_now = (current & PD_PWR) == 0
        self._virtual_fdd_interface_powered = powered_now
        # True only when RES is released in THIS same CTRL write (not a persistent flag).
        # Used to detect the boot pulse where STR falls simultaneously with RES release.
        res_released_now = (current & PD_RES) == 0 and (previous & PD_RES) != 0

        if (current & PD_PWR) != (previous & PD_PWR):
            print(f"[VFDD] Power: {'ON' if powered_now else 'OFF'}")
            if powered_now:
                # Power just turned ON: pre-load 0x55 so boot detection works
                # even before any RES/STR pulse occurs
                self._io_rd_regs[4] = 0x55  # MD-100 identifier
                self._gpo_parity = 0         # Reset parity for clean D92E P1/P0 pairs
            else:
                self._virtual_fdd_ack = False
                self.virtual_fdd_controller.close()

        if powered_now:
            # Power is ON (Active Low)
            if (current & PD_RES) != 0 and (previous & PD_RES) == 0:
                # Rising edge of RES: Device Enters Reset (Active HIGH)
                self._log_vfdd(f"Reset Detected (Active HIGH)")
                self._virtual_fdd_ack = False
                self.virtual_fdd_controller.open()
            elif (current & PD_RES) == 0 and (previous & PD_RES) != 0:
                # Falling edge of RES: Reset Released (Run Mode)
                self._log_vfdd("Reset released")
                # Initialize data register to MD-100 identifier (0x55).
                # The boot ROM reads 0x0C03 to detect the FDD: it stores this
                # value into OPTCD and later checks OPTCD == 0x55 to confirm
                # the MD-100 interface is present.
                self._io_rd_regs[4] = 0x55  # MD-100 identifier for boot detection
                if hasattr(cpu_core, "set_vfdd_data"):
                    cpu_core.set_vfdd_data(0x55)
                self._inject_optcd_signature("reset-release")

            # Allow transfer whenever power is on, ensuring P3 is always updated by STR
            # Handle STR (Strobe) toggling
            if (current & PD_STR) == 0 and (previous & PD_STR) != 0:
                # Falling edge of STR: Start of transfer cycle.
                # Data was already pre-fetched at the end of the previous cycle.
                self._virtual_fdd_ack = True

                # Retrieve the latest data written by the CPU to 0x0C04.
                # NOTE: cpu_core.get_vfdd_write_data() is broken (always returns 0x00).
                # Use _io_wr_regs[4] which is correctly updated by the _write_io_register callback.
                val_in = self._io_wr_regs[4]

                if res_released_now:
                    # Boot pulse: RES released in this same CTRL write as STR fell
                    # (e.g. CTRL 1C->00). Keep _io_rd_regs[4]=0x55 so the ROM's
                    # boot OPTCD detection read at 0x0C03 sees the MD-100 identifier.
                    # Do NOT call transfer() here, as it would advance the state
                    # before the real command is issued.
                    pass
                else:
                    val_out_next = self.virtual_fdd_controller.transfer(val_in)
                    self._io_rd_regs[4] = val_out_next
                    if hasattr(cpu_core, "set_vfdd_data"):
                        cpu_core.set_vfdd_data(val_out_next)
                
            elif (current & PD_STR) != 0 and (previous & PD_STR) == 0:
                # Rising edge of STR: End of transfer cycle.
                self._virtual_fdd_ack = False
        else:
            self._virtual_fdd_ack = False
            if was_powered and not powered_now:
                self._log_vfdd("Interface entered power-off state")
        if (current & PD_RES) != 0 and (previous & PD_RES) == 0:
            # FDD Reset rising edge outside powered block: reset state machine
            self.virtual_fdd_controller.open()

        self._port_last_write = current

    def _inject_optcd_signature(self, reason="runtime"):
        try:
            ram_idx = 0x6BFA - 0x6000
            if 0 <= ram_idx < len(self.ram):
                self.ram[ram_idx] = 0x55
                print(f"[VFDD] Injected OPTCD=0x55 at 0x6BFA ({reason})")
        except Exception as e:
            print(f"[VFDD] Failed to inject OPTCD ({reason}): {e}")

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
        """Return the best path for a file. Profile dir takes highest priority."""
        if self.profile_dir:
            return self.profile_dir + "/" + filename

        sd_path = "/sd/" + filename
        roms_path = "/roms/" + filename
        root_path = "/" + filename

        if self.sd_mounted:
            if filename in ("ram0.bin", "ram1.bin", "ram2.bin", "ram3.bin", "regs.json"):
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

    def _virtual_fdd_config_candidates(self):
        return (
            "/sd/profile.ini",
            "/sd/virtual_fdd.ini",
            "/roms/profile.ini",
        )

    def discover_virtual_fdd_config(self):
        if not ENABLE_VIRTUAL_FDD:
            self.virtual_fdd_config = {"enabled": False, "reason": "disabled by flag"}
            self._pending_virtual_fdd_config = None
            return None

        # Use [disk] section from merged pb1000.ini config if available
        if self._config is not None and "disk" in self._config:
            disk = self._config["disk"]
            enabled = disk.get("enabled", "false").lower() in ("1", "true", "yes", "on")
            if not enabled:
                self.virtual_fdd_config = {"enabled": False, "reason": "disabled in pb1000.ini"}
                self._pending_virtual_fdd_config = None
                return None
            raw_path = disk.get("path", "").strip()
            if raw_path and not raw_path.startswith("/"):
                raw_path = "/sd/" + raw_path
            cfg = {
                "enabled": True,
                "backend": disk.get("backend", "raw").strip(),
                "path": raw_path,
                "readonly": disk.get("readonly", "false").lower() in ("1", "true", "yes", "on"),
            }
            print(f"[VFDD] Config from pb1000.ini: {raw_path}")
            self.virtual_fdd_config = cfg
            self._pending_virtual_fdd_config = cfg
            return cfg

        for config_path in self._virtual_fdd_config_candidates():
            cfg = load_virtual_fdd_config(config_path)
            if cfg:
                print(f"[VFDD] Found config: {config_path}")
                self.virtual_fdd_config = cfg
                self._pending_virtual_fdd_config = cfg
                return cfg
        
        if ENABLE_VIRTUAL_FDD:
            # Fallback to default
            default_path = "/sd/disks/disk1.img"
            print(f"[VFDD] No config file found. Using default: {default_path}")
            cfg = {
                "enabled": True,
                "backend": "image",
                "path": default_path,
                "readonly": False
            }
            self.virtual_fdd_config = cfg
            self._pending_virtual_fdd_config = cfg
            return cfg
            
        return None

    def configure_virtual_fdd(self, path=None, readonly=False, enabled=True):
        if not enabled:
            self.disable_virtual_fdd()
            return False

        if not path:
            raise ValueError("virtual FDD path is required")

        # Create image file if it does not exist yet
        new_disk = False
        try:
            os.stat(path)
        except OSError:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent:
                try:
                    os.stat(parent)
                except OSError:
                    print(f"[VFDD] Directory not found: {parent}. Disabling virtual FDD.")
                    self.disable_virtual_fdd()
                    return False
            ImageStorageBackend.create(path, 256)
            new_disk = True
            print(f"[VFDD] Created new disk image: {path}")

        # Inject MD-100 identifier into OPTCD (&H6BFA) in main RAM.
        # This may be overwritten later by ROM error paths, so we also refresh it
        # on reset-release.
        self._inject_optcd_signature("configure")

        backend = ImageStorageBackend(path, readonly=readonly)
        dos = MD100Dos()
        dos.dos_init(backend)
        if new_disk:
            dos.format_disk()
        self.virtual_fdd = backend
        self.virtual_fdd_controller.attach_dos(dos)
        self.virtual_fdd_controller.fdd_open()

        # New C-side Selective Hooking:
        # We stay in C-managed memory mode (Full Speed) and only hook 0x0C00 range.
        if hasattr(cpu_core, "set_io_callbacks"):
            # I/O callbacks are already registered during __init__.
            # Re-registering here is unnecessary and can destabilize boot on-device.
            print("[VFDD] C-side selective MMIO hooking already active")

        self.virtual_fdd_config = {
            "enabled": True,
            "backend": "image",
            "path": path,
            "readonly": bool(readonly),
        }
        # Ensure Python-side ROM copies exist for the callback path (Bug #1)
        self._ensure_rom_copies()

        print(
            "Virtual FDD enabled: "
            f"path={path} readonly={1 if readonly else 0}"
        )
        return True

    def _ensure_rom_copies(self):
        """Reload ROM data into Python copies when C direct memory is disabled."""
        if self.rom0 is None or len(self.rom0) == 0:
            self.load_rom('/roms/rom0.bin', slot=0, keep_copy=True)
        if self.rom1 is None or len(self.rom1) == 0:
            self.load_rom('/roms/rom1.bin', slot=1, keep_copy=True)

    def disable_virtual_fdd(self):
        if self.virtual_fdd is not None:
            try:
                self.virtual_fdd.close()
            except Exception:
                pass
        self.virtual_fdd_controller.attach_dos(None)
        self.virtual_fdd = None
        self.virtual_fdd_config = {"enabled": False}

    def swap_disk(self, new_path):
        """実行中にディスクイメージを差し替える。new_path=None でイジェクト。"""
        if self.has_virtual_fdd():
            self.disable_virtual_fdd()
        if new_path is None:
            print("[VFDD] Disk ejected.")
            return True
        try:
            return self.configure_virtual_fdd(new_path)
        except Exception as e:
            print(f"[VFDD] swap_disk failed: {e}")
            return False

    def activate_pending_virtual_fdd(self):
        cfg = self._pending_virtual_fdd_config
        if not cfg or not cfg.get("enabled", False):
            self._pending_virtual_fdd_config = None
            return False
        try:
            result = self.configure_virtual_fdd(
                path=cfg.get("path"),
                readonly=cfg.get("readonly", False),
                enabled=True,
            )
            if result:
                self._pending_virtual_fdd_config = None
            return result
        except Exception as exc:
            print(f"[VFDD] Auto-config failed: {exc}")
            import sys
            sys.print_exception(exc)
            return False

    def try_auto_configure_virtual_fdd(self):
        self.discover_virtual_fdd_config()
        return self.activate_pending_virtual_fdd()

    def boot_virtual_fdd(self):
        """Robust initialization: Discovery + Activation in one call."""
        cfg = self.discover_virtual_fdd_config()
        if cfg:
            return self.activate_pending_virtual_fdd()
        print("[VFDD] No config found")
        return False

    def has_virtual_fdd(self):
        return self.virtual_fdd is not None

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
            reg_path = self._get_storage_path("regs.json")
        else:
            path0 = f"{path}/ram0.bin"
            reg_path = f"{path}/regs.json"

        # Ensure the target directory exists (handles profile subdirs like /sd/rams/work/)
        dir_path = path0.rsplit("/", 1)[0]
        if dir_path.startswith("/sd"):
            self._ensure_dir(dir_path)

        try:
            with open(path0, "wb") as f:
                buf = self.ram._view if isinstance(self.ram, RAMView) else self.ram
                f.write(buf)
            print(f"RAM0 saved: {path0} ({len(buf)} bytes)")
        except Exception as e:
            print(f"Error saving RAM0: {e}")

        for slot in range(1, 4):
            if not self.has_bank[slot]:
                continue
            rp = (self._get_storage_path(f"ram{slot}.bin") if path is None else f"{path}/ram{slot}.bin")
            try:
                sbuf = self._bank_ram[slot]
                data = sbuf._view if isinstance(sbuf, RAMView) else sbuf
                if len(data) == 0:
                    print(f"RAM{slot} skipped: buffer empty")
                    continue
                with open(rp, "wb") as f:
                    f.write(data)
                print(f"RAM{slot} saved: {rp} ({len(data)} bytes)")
            except Exception as e:
                print(f"Error saving RAM{slot}: {e}")

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

    def register_call_hook(self, address, fn):
        """Register a callable for the given CAL destination address.
        fn may be a Python function or a native C MicroPython function.
        Only CAL instructions targeting this address will invoke fn;
        JP/JR to the same address will not fire.
        """
        if not hasattr(self, "_call_hook_refs"):
            self._call_hook_refs = {}
        self._call_hook_refs[address] = fn  # Python-side GC anchor
        if hasattr(cpu_core, "set_call_hook"):
            cpu_core.set_call_hook(address, fn)

    def unregister_call_hook(self, address):
        """Unregister the hook for the given CAL destination address."""
        if hasattr(self, "_call_hook_refs"):
            self._call_hook_refs.pop(address, None)
        if hasattr(cpu_core, "clear_call_hook"):
            cpu_core.clear_call_hook(address)

    def enable_call_hook(self, address):
        """Enable a previously registered hook. No-op if not registered."""
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, True)

    def disable_call_hook(self, address):
        """Disable a registered hook without unregistering it."""
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, False)

    def load_state(self, path=None):
        import json
        if path is None:
            path0 = self._get_storage_path("ram0.bin")
            reg_path = self._get_storage_path("regs.json")
        else:
            path0 = f"{path}/ram0.bin"
            reg_path = f"{path}/regs.json"

        print(f"Loading state: RAM={path0}, REGS={reg_path}")
        import gc
        gc.collect()

        def _load_to_ram(file_path, ram_target, slot):
            if not self._file_exists(file_path):
                 print(f"RAM file not found: {file_path}")
                 return False
            try:
                gc.collect()
                with open(file_path, "rb") as f:
                    if hasattr(cpu_core, "load_ram"):
                        gc.collect()
                        try:
                            data = f.read()
                            cpu_core.load_ram(slot, data)
                            print(f"RAM slot {slot} restored via C-API from {file_path} ({len(data)} bytes)")
                            del data
                            gc.collect()
                        except MemoryError:
                            f.seek(0)
                            offset = 0
                            while True:
                                chunk = f.read(4096)
                                if not chunk:
                                    break
                                ram_target[offset:offset + len(chunk)] = chunk
                                offset += len(chunk)
                            print(f"RAM slot {slot} restored (chunked) from {file_path} ({offset} bytes)")
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
        for slot in range(1, 4):
            if self.has_bank[slot]:
                rp = (self._get_storage_path(f"ram{slot}.bin") if path is None else f"{path}/ram{slot}.bin")
                _load_to_ram(rp, self._bank_ram[slot], slot)

        try:
            if self._file_exists(reg_path):
                if reg_path.endswith(".json"):
                    with open(reg_path, "r") as f:
                        regs = json.load(f)
                    # Saved execution registers do not mix safely with a forced PC=0x0000.
                    # Start from a clean CPU state and keep the restored RAM only.
                    cpu_core.reset(self.debug_cfg["sys"])
                    cpu_core.set_pc(0x0000)
                    cpu_core.set_reg8(2, 0)  # Clear IB
                    cpu_core.set_reg8(5, 0)  # Clear IE
                    cpu_core.set_reg8(4, 0)  # Clear IA
                    cpu_core.set_reg8(3, 0)  # Clear UA
                    print("CPU reset after RAM load (saved registers ignored, PC=0x0000)")
                else:
                    self._restore_registers_from_dump()
            else:
                print(f"Register file not found: {reg_path}")
        except Exception as e:
            print(f"Error loading registers: {e}")

    def step(self, cycles=100, stop_pc=-1):
        return cpu_core.execute(int(cycles), int(stop_pc))

    def reset_emulator(self):
        """Perform a hardware-like reset (PC=0x0000)."""
        print("Emulator Reset triggered (PC=0x0000)")
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

    # ------------------------------------------------------------------ #
    #  VRAM text extraction (serial console output)                        #
    # ------------------------------------------------------------------ #

    def _build_char_lookup(self):
        return {}

    def _scan_vram_for_text(self):
        pass

    @property
    def console_uart(self):
        return self._console_uart

    @console_uart.setter
    def console_uart(self, uart):
        self._console_uart = uart
        if not hasattr(self, "_cb_refs"):
            return
        if not hasattr(cpu_core, "set_lcd_char_callback"):
            return
        if uart is not None:
            if "lcd_char" not in self._cb_refs:
                self._cb_refs["lcd_char"] = self._on_lcd_char_output
            cpu_core.set_lcd_char_callback(self._cb_refs["lcd_char"])
        else:
            cpu_core.set_lcd_char_callback(None)

    def update_display(self, x_offset=None, y_offset=None):
        if x_offset is not None: self._disp_x = x_offset
        if y_offset is not None: self._disp_y = y_offset
        self.lcd.render_to_display(self._disp_x, self._disp_y)
        self._render_status_bar()
        self._scan_vram_for_text()

    def force_full_redraw(self):
        """Redraw bezel + LCD after overlaying the screen (e.g. after menu closes)."""
        if hasattr(self.lcd, 'display') and self.lcd.display is not None:
            draw_bezel(self.lcd.display, self.lcd.scale, self._disp_x, self._disp_y)
        self.lcd.mark_dirty()
        self._status_rendered_msg = None  # force status bar refresh
        self.update_display()

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
        for char in str(text).upper():
            bits = font.get(char, 0x7F7F7F7F7F) # Block for unknown
            # Hex bytes are ordered MSB...LSB, so i=0 (left) should be MSB
            for i in range(5):
                col_bits = (bits >> ((4 - i) * 8)) & 0xFF
                for j in range(8):
                    if col_bits & (1 << j):
                        display.fill_rect(curr_x + i, y + j, 1, 1, color)
            curr_x += 6

    def _on_lcd_char_output(self, code):
        uart = getattr(self, 'console_uart', None)
        if not uart:
            return
        if code is None:
            if self._lcd_had_output:
                uart.write(b'\r\n')
                self._lcd_had_output = False
        elif 0x20 <= code <= 0x7E:
            uart.write(bytes([code]))
            self._lcd_had_output = True

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
            if force_reset and force_power_on:
                raise ValueError("force_reset and force_power_on cannot both be true")

            if force_reset:
                cpu_core.set_pc(0x0000)
                print("System forced to reset entry (PC=0x0000)")
            elif force_power_on:
                cpu_core.set_pc(0x0001)
                print("System forced to power-on entry (PC=0x0001)")
            elif current_pc == 0x0001:
                 print("System power on at power-on entry (PC=0x0001)")
            elif current_pc == 0x0000:
                 print("System power on at reset entry (PC=0x0000)")
            else:
                print(f"System resumed at PC={current_pc:#06x}")

        self.lcd.lcd_ctrl(0xDF) # OP=1, CE=3 (Both chips)
        self.lcd.lcd_write(0x14)
        self.lcd.lcd_ctrl(0xDE) # OP=0


    def set_on_int(self, state):
        cpu_core.set_input(cpu_core.ON_INT, 1 if state else 0)

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

    def dump_edtop_vram(self, bytes_per_line=16, printer=print):
        """Dump EDTOP VRAM (0x6100-0x61FF)."""
        self.dump_mem_range(0x6100, 0x61FF, bytes_per_line=bytes_per_line, printer=printer)

    def dump_ledtp_vram(self, bytes_per_line=16, printer=print):
        """Dump LEDTP VRAM (0x6201-0x6850)."""
        #self.dump_mem_range(0x6201, 0x6850, bytes_per_line=bytes_per_line, printer=printer)
        self.lcd.dump_vram()





