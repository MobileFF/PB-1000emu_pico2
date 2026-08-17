import hd61700 as cpu_core
import gc
import os
import sys
import time
import machine
# ILI9341 and ST7796 are imported lazily inside init_display() when selected
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

def init_sdcard(spi, lcd_baudrate=40_000_000):
    try:
        from sdcard import SDCard
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        # Use 400kHz for stable SD init, restore to the actual LCD SPI baudrate
        sd = SDCard(spi, sd_cs, baudrate=400000, restore_baudrate=lcd_baudrate)
        vfs = os.VfsFat(sd)
        os.mount(vfs, "/sd")
        print("SD Card mounted at /sd")
        return True
    except Exception as e:
        print(f"SD Card mount optional: {e}")
        sys.print_exception(e)
        return False

def _read_early_ini_sections(section_names):
    """Read the given sections from /pb1000.ini and /roms/pb1000.ini.
    SD card is not yet mounted at this point, so only internal flash is checked.
    Returns {section_name: {key: value}}."""
    wanted = {s.lower() for s in section_names}
    result = {s: {} for s in wanted}
    for path in ("/pb1000.ini", "/roms/pb1000.ini"):
        try:
            with open(path, "r") as f:
                section = None
                for raw in f:
                    line = raw.strip()
                    if not line or line[0] in (";", "#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        name = line[1:-1].strip().lower()
                        section = name if name in wanted else None
                        continue
                    if section and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip().lower()
                        v = v.split(";", 1)[0].split("#", 1)[0].strip()
                        result[section][k] = v
        except OSError:
            pass
    return result


def _early_bool(s, default):
    if s is None:
        return default
    return s.strip().lower() in ("1", "true", "yes", "on")

def init_display():
    _early_cfg = _read_early_ini_sections(("display", "touch"))
    disp_cfg = _early_cfg["display"]
    touch_early_cfg = _early_cfg["touch"]
    # Accept both "driver=ST7796" and "display=ST7796" as equivalent keys.
    driver = disp_cfg.get("driver", disp_cfg.get("display", "ILI9341")).upper()

    # ILI9341: 26 MHz (safe for all modules). ST7796: 40 MHz.
    # Override with spi_baudrate in [display] section of pb1000.ini if needed.
    default_baud = 40_000_000 if driver == "ST7796" else 26_000_000
    spi_baud = int(disp_cfg.get("spi_baudrate", str(default_baud)))

    # 0 = normal, 180 = physically flipped (board mounted upside down).
    try:
        rotation = int(disp_cfg.get("rotation", "0"))
    except (ValueError, TypeError):
        rotation = 0
    if rotation not in (0, 180):
        rotation = 0

    spi = machine.SPI(
        SPI_ID,
        baudrate=spi_baud,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    print(f"SPI baudrate: {spi_baud}")
    # Ensure all CS pins are high before starting
    machine.Pin(CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(T_CS_PIN, machine.Pin.OUT, value=1)
    machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)

    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    if driver == "ST7796":
        from st7796 import ST7796
        display = ST7796(spi, cs, dc, rst, width=480, height=320, rotation=rotation)
        display.fill_rect(0, 0, 480, 320, 0x0000)
        print(f"Display: ST7796 480x320 (rotation={rotation})")
    else:
        from ili9341 import ILI9341
        display = ILI9341(spi, cs, dc, rst, width=320, height=240, rotation=rotation)
        display.fill_rect(0, 0, 320, 240, 0x0000)
        print(f"Display: ILI9341 320x240 (rotation={rotation})")
    display.spi_baudrate = spi_baud

    # Try mounting SD card; pass actual SPI baudrate so it's restored correctly
    sd_mounted = init_sdcard(spi, spi_baud)

    touch = None
    try:
        from xpt2046 import XPT2046
        # XPT2046 orientation depends on how the touch overlay film is
        # mounted on each physical panel, which differs between the
        # ILI9341 and ST7796 modules — the two need different swap/invert
        # settings by default. ILI9341 needs axes swapped only (no invert);
        # ST7796 (MSP4021 etc.) needs axes swapped and both inverted, plus
        # its own calibration range. Can be overridden per-panel via
        # swap_xy/x_inv/y_inv in [touch] section of /pb1000.ini — use a
        # "ili9341."/"st7796." prefixed key (e.g. ili9341.y_inv) to scope the
        # override to one driver, since both drivers' settings can coexist in
        # the same ini and only one is active at a time via [display] driver.
        # An unprefixed key still applies to whichever driver is active.
        # (SD card is not yet mounted here, so only internal-flash
        # pb1000.ini is honored for these early keys.)
        _driver_prefix = driver.lower()
        def _touch_early(key):
            dkey = _driver_prefix + "." + key
            if dkey in touch_early_cfg:
                return touch_early_cfg[dkey]
            return touch_early_cfg.get(key)
        swap_xy = _early_bool(_touch_early("swap_xy"), True)
        if driver == "ST7796":
            x_inv = _early_bool(_touch_early("x_inv"), True)
            y_inv = _early_bool(_touch_early("y_inv"), True)
            touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN,
                            width=display.width, height=display.height,
                            swap_xy=swap_xy, x_inv=x_inv, y_inv=y_inv,
                            y_min=325, y_max=3850,
                            lcd_baudrate=spi_baud,
                            rotate180=(rotation == 180))
        else:
            x_inv = _early_bool(_touch_early("x_inv"), False)
            y_inv = _early_bool(_touch_early("y_inv"), False)
            touch = XPT2046(spi, T_CS_PIN, T_IRQ_PIN,
                            width=display.width, height=display.height,
                            swap_xy=swap_xy, x_inv=x_inv, y_inv=y_inv,
                            lcd_baudrate=spi_baud,
                            rotate180=(rotation == 180))
    except Exception as e:
        print("Touch panel init failed:", e)
        sys.print_exception(e)

    return display, touch, sd_mounted, spi

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

        # Bank presence: [0]=ROM1 (always), [1..3]=RAM banks. Buffer *space*
        # for all three RAM banks is always reserved below, regardless of
        # which files exist in the boot-time profile — this is what lets a
        # later RAM Load switch to a profile with more banks actually load
        # them (previously the buffer itself didn't exist unless the boot
        # profile happened to have that bank).
        #
        # has_bank[] itself, however, tracks whether the *currently active*
        # profile has real data for that bank, and is mirrored to the CPU
        # core via set_bank_present()/set_has_exp_ram() so that programs
        # probing bank presence (write-then-readback; absent banks read back
        # 0xFF regardless of what was written — see c_mem_direct_read in
        # modhd61700.c) see a result consistent with what this profile
        # actually represents, not a permanently-present fake. load_state()
        # re-evaluates and re-syncs this on every profile switch.
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
            # Bank 1 (exp_ram): backward-compat view. Always built regardless
            # of has_bank[1] — the buffer must exist so a later profile
            # switch that does have ram1.bin can load into it.
            if hasattr(cpu_core, "get_exp_ram_view"):
                exp_raw_view = cpu_core.get_exp_ram_view()
                _b1 = RAMView(cpu_core, memoryview(exp_raw_view), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=0x10)
            else:
                _b1 = bytearray(self.EXP_RAM_SIZE)
            # Banks 2 and 3 — same unconditional allocation.
            _bank_views = []
            for slot in range(2, 4):
                if hasattr(cpu_core, "get_bank_view"):
                    rv = cpu_core.get_bank_view(slot)
                    _bank_views.append(RAMView(cpu_core, memoryview(rv), self.EXP_RAM_SIZE, self.SYS_ROM_START, segment=slot << 4))
                else:
                    _bank_views.append(bytearray(self.EXP_RAM_SIZE))
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
        self.lcd.on_scale_change = self._on_lcd_scale_change
        if hasattr(self.lcd, 'set_char_output_callback'):
            self.lcd.set_char_output_callback(self._on_lcd_char_output)
        
        self._disp_x = 16
        self._disp_y = 40
        self._lcd_height = 32  # updated by create_system from ini
        self.touch_x_offset = 0
        self.touch_y_offset = -104
        self.funckey_touch_x_offset = 0
        self.funckey_touch_y_offset = 24
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
                data = self.pio_uart.read(1)
                self._io_rd_regs[2] = data[0] if data else 0
                if data and not getattr(self, '_uart_rx_logged', False):
                    self._uart_rx_logged = True
                    print(f"[UART_RX] ROM read first byte: {data[0]:#04x}")
                # Deassert INT1 when Python buffer is now empty so the CPU
                # does not re-enter the ISR before the next byte arrives.
                if not self.pio_uart.any() and hasattr(cpu_core, 'uart_clear_rx_signal'):
                    cpu_core.uart_clear_rx_signal()
                # ROM consumed EOF — flag auto-BREAK; main loop's KeyboardInputManager
                # queues BRK and waits for is_key_input_enabled so it only fires
                # after the ROM finishes processing.
                if data and data[0] == 0x1A and not getattr(self, '_pio_uart_eof_pending', False):
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
                # console_uart (UART1, GP4/GP5) is on independent pins and must
                # always receive BASIC PRINT output regardless of FDD power state.
                # Skipped during an active FDD transfer: those bytes are FDD
                # protocol data, not console text, and must not leak to the
                # debug REPL (was flooding the log with raw retry bytes).
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
        /sd/ext/ と /ext/ の両方を対象にマージしてロードする。
        同名モジュールが両方にある場合は /sd/ext/ 側を優先し、
        /ext/ 側は無視する(sys.path も /sd/ext を先に登録するため、
        import 解決自体が自然に SD 優先になる)。
        .py と .mpy の両方を候補として認識する(.mpy のみを認識しない
        既存実装は 2026-08-13 の不具合報告で判明)。同一ディレクトリに
        両方存在する場合は __import__() 自身の解決規則がそのまま働き、
        MicroPython は常に .py を .mpy より優先するため、ここで
        拡張子ごとの優先順位を別途実装する必要はない。
        各モジュールは register(system) 関数を持つこと。
        """
        import os, sys, gc
        mod_sources = {}  # mod_name -> ext_dir (最初に見つかった = 優先されるディレクトリ)
        for ext_dir in ("/sd/ext", "/ext"):
            try:
                files = os.listdir(ext_dir)
            except OSError:
                continue
            if ext_dir not in sys.path:
                sys.path.insert(0, ext_dir)
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
            try:
                _pc_dbg = f" PC={cpu_core.get_pc():#06x}"
            except Exception:
                _pc_dbg = ""
            print(f"[VFDD] Power: {'ON' if powered_now else 'OFF'}{_pc_dbg}")
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
                # Boot ROM reads 0x0C03, stores it in OPTCD, then checks
                # OPTCD==0x55 to confirm the MD-100 interface is present.
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

                # cpu_core.get_vfdd_write_data() is broken (always 0x00); use
                # _io_wr_regs[4], kept correct by the _write_io_register callback.
                val_in = self._io_wr_regs[4]

                if res_released_now:
                    # Boot pulse: RES released in this same CTRL write as STR
                    # fell (e.g. CTRL 1C->00). Don't call transfer() here — it
                    # would advance state before the real command is issued;
                    # just leave _io_rd_regs[4]=0x55 for the ROM's OPTCD read.
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
            sys.print_exception(e)

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
        except Exception as _e:
            sys.print_exception(_e)

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
            sys.print_exception(e)
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
            sys.print_exception(e)

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
                sys.print_exception(e)

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
                # irq_status/state: which interrupt handler (if any) is active,
                # and CPU_SLP/CPU_FAST — see get_irq_status()/get_state() in
                # modhd61700.c. A save taken mid-interrupt-handler (interrupts
                # fire on their own schedule regardless of what the loaded
                # program does, e.g. the periodic keyboard-scan interrupt) has
                # PC/UA pointing into ROM handler code that only makes sense
                # with these restored too, or resume can crash via a stale/
                # inconsistent fetch-bank state. Guarded with hasattr() so old
                # firmware without these C exports still round-trips the rest.
                "irq_status": int(cpu_core.get_irq_status()) if hasattr(cpu_core, "get_irq_status") else 0,
                "cpu_flow_state": int(cpu_core.get_state()) if hasattr(cpu_core, "get_state") else 0,
            }
            with open(reg_path, "w") as f:
                json.dump(regs, f)
            print(f"State saved to {reg_path}")
        except Exception as e:
            print(f"Error saving registers: {e}")
            sys.print_exception(e)

    def register_call_hook(self, address, fn, owner=None):
        """Register a callable for the given destination address.
        fn may be a Python function or a native C MicroPython function.
        Fires when CAL, JP, or JR targets this exact address — some ROM
        routines reach a given entry point via a plain JP/JR (a tail-call
        style jump) rather than CAL, so all three must be caught for the
        hook to reliably intercept every path in. CAL pushes a return
        address before jumping (interception pops it and returns as if
        RTN had executed); JP/JR push nothing, so interception simply
        skips the jump and continues at the next instruction instead.

        owner: optional human-readable label (e.g. "dotds_64dot") shown by
        the emulator menu's Hook Status screen. Extension modules should
        pass their own module name; if omitted, fn.__name__ is used as a
        best-effort fallback (may be unavailable on some MicroPython builds).
        """
        if not hasattr(self, "_call_hook_refs"):
            self._call_hook_refs = {}
            self._call_hook_owner = {}
            self._call_hook_enabled = {}
        self._call_hook_refs[address] = fn  # Python-side GC anchor
        self._call_hook_owner[address] = owner or getattr(fn, "__name__", "?")
        self._call_hook_enabled[address] = True  # new entries are enabled by default
        if hasattr(cpu_core, "set_call_hook"):
            cpu_core.set_call_hook(address, fn)

    def enable_call_hook(self, address):
        """Enable a previously registered hook. No-op if not registered."""
        if hasattr(self, "_call_hook_enabled") and address in self._call_hook_enabled:
            self._call_hook_enabled[address] = True
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, True)

    def disable_call_hook(self, address):
        """Disable a registered hook without unregistering it."""
        if hasattr(self, "_call_hook_enabled") and address in self._call_hook_enabled:
            self._call_hook_enabled[address] = False
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, False)

    def list_call_hooks(self):
        """Return [(address, owner, enabled), ...] sorted by address, for
        diagnostic display (e.g. the emulator menu's Hook Status screen)."""
        refs = getattr(self, "_call_hook_refs", {})
        owners = getattr(self, "_call_hook_owner", {})
        enabled = getattr(self, "_call_hook_enabled", {})
        return sorted(
            (addr, owners.get(addr, "?"), enabled.get(addr, True))
            for addr in refs
        )

    def register_mem_write_hook(self, addr_start, fn, addr_end=None, owner=None):
        """Call fn(addr, data, bank) before a byte is written to memory.
        Omit addr_end to watch a single address; pass addr_end to watch a
        range (addr_start..addr_end inclusive). fn returning True cancels
        the write. Registering again with the same addr_start overwrites
        the previous entry (range and callable included).

        owner: optional human-readable label shown by the emulator menu's
        Hook Status screen; see register_call_hook() for details."""
        if addr_end is None:
            addr_end = addr_start
        if not hasattr(self, "_mem_write_hook_refs"):
            self._mem_write_hook_refs = {}
            self._mem_write_hook_range = {}
            self._mem_write_hook_owner = {}
            self._mem_write_hook_enabled = {}
        self._mem_write_hook_refs[addr_start] = fn  # Python-side GC anchor
        self._mem_write_hook_range[addr_start] = addr_end
        self._mem_write_hook_owner[addr_start] = owner or getattr(fn, "__name__", "?")
        self._mem_write_hook_enabled[addr_start] = True  # new entries are enabled by default
        if hasattr(cpu_core, "set_mem_write_hook"):
            cpu_core.set_mem_write_hook(addr_start, addr_end, fn)

    def list_mem_write_hooks(self):
        """Return [(addr_start, addr_end, owner, enabled), ...] sorted by
        addr_start, for diagnostic display (e.g. the emulator menu's Hook
        Status screen)."""
        refs = getattr(self, "_mem_write_hook_refs", {})
        ranges = getattr(self, "_mem_write_hook_range", {})
        owners = getattr(self, "_mem_write_hook_owner", {})
        enabled = getattr(self, "_mem_write_hook_enabled", {})
        return sorted(
            (addr, ranges.get(addr, addr), owners.get(addr, "?"), enabled.get(addr, True))
            for addr in refs
        )

    def load_state(self, path=None, restore_cpu_state=True):
        """restore_cpu_state=True (default): full resume, including PC/UA/
        all registers, as captured by save_state() — used by the emulator
        menu's user-initiated "RAM Load".
        restore_cpu_state=False: RAM contents only, CPU forced to a clean
        reset (PC=0x0000, IB/IE/IA/UA cleared) — the old, conservative
        behavior. Used for the automatic boot-time restore (main.py), since
        an unattended boot must never be able to get stuck resuming a bad/
        inconsistent save with no way to reach the menu to recover (see
        2026-08-11 FOREX_PB boot-hang report: a save taken mid-VFDD-access
        resumed into a TRP whose 0x6FFA jump table pointed into the unmapped
        dead zone, hanging every subsequent boot until this split existed)."""
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

        def _load_direct(f, view):
            """readinto でCバッファに直接書き込む。Pythonヒープ割り当てゼロ。"""
            return f.readinto(view)

        def _load_chunked(f, ram_target, chunk_size=256):
            """チャンク単位で書き込む。chunk_size を小さくしてヒープ断片化に対応。"""
            offset = 0
            buf = bytearray(chunk_size)
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                ram_target[offset:offset + n] = buf[:n]
                offset += n
                gc.collect()
            return offset

        def _fill_bank(ram_target, value, chunk_size=1024):
            """バンクRAMをvalueで埋める(チャンク単位、Pythonヒープ確保は
            pattern一つ分のみ)。ファイルが無いプロファイルへ切り替えた際、
            前のプロファイルの内容が残留しないようにするために使う。"""
            view = ram_target._view if hasattr(ram_target, '_view') else ram_target
            size = len(ram_target)
            pattern = bytes([value & 0xFF]) * chunk_size
            offset = 0
            while offset < size:
                n = min(chunk_size, size - offset)
                view[offset:offset + n] = pattern if n == chunk_size else pattern[:n]
                offset += n

        def _load_to_ram(file_path, ram_target, slot):
            if not self._file_exists(file_path):
                print(f"RAM file not found: {file_path}")
                return False
            try:
                gc.collect()
                with open(file_path, "rb") as f:
                    if slot == 0 and hasattr(cpu_core, "load_ram"):
                        # メインRAM(8KB): C-API 経由で一括ロード
                        gc.collect()
                        try:
                            data = f.read()
                            cpu_core.load_ram(slot, data)
                            print(f"RAM slot {slot} loaded via C-API ({len(data)}B)")
                            del data
                            gc.collect()
                        except MemoryError:
                            f.seek(0)
                            n = _load_chunked(f, ram_target)
                            print(f"RAM slot {slot} loaded chunked ({n}B)")
                    elif hasattr(ram_target, '_view'):
                        # バンクRAM(32KB): readinto でCバッファに直接書き込み
                        # f.readinto(memoryview) はPythonヒープを一切消費しない
                        gc.collect()
                        try:
                            n = _load_direct(f, ram_target._view)
                            print(f"RAM slot {slot} loaded direct ({n}B) from {file_path}")
                        except Exception as _e:
                            print(f"RAM direct load failed ({_e}), retrying chunked")
                            f.seek(0)
                            n = _load_chunked(f, ram_target)
                            print(f"RAM slot {slot} loaded chunked ({n}B) from {file_path}")
                    else:
                        # fallback: bytearray バッファへのチャンク書き込み
                        n = _load_chunked(f, ram_target)
                        print(f"RAM slot {slot} loaded chunked ({n}B) from {file_path}")
                return True
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
                sys.print_exception(e)
                return False

        _load_to_ram(path0, self.ram, 0)
        gc.collect()
        for slot in range(1, 4):
            # Bank buffers are always allocated (see __init__), so switching
            # profiles can always load whichever banks the newly-selected
            # profile provides. has_bank[slot] (the CPU-visible presence
            # flag, mirrored into the C core) is re-evaluated per profile:
            # a profile without ramN.bin means "no card in this slot" for
            # this session, matching what a bank-presence probe would see
            # on real hardware — so the bank is filled with 0xFF (the same
            # value c_mem_direct_read returns for an absent bank) rather
            # than left with the previous profile's stale contents.
            rp = (self._get_storage_path(f"ram{slot}.bin") if path is None else f"{path}/ram{slot}.bin")
            present = self._file_exists(rp)
            self.has_bank[slot] = present
            if hasattr(cpu_core, "set_bank_present"):
                cpu_core.set_bank_present(slot, present)
            if present:
                _load_to_ram(rp, self._bank_ram[slot], slot)
            else:
                _fill_bank(self._bank_ram[slot], 0xFF)
                print(f"RAM slot {slot}: no save for this profile, filled 0xFF")
            gc.collect()

        try:
            if self._file_exists(reg_path):
                if reg_path.endswith(".json"):
                    with open(reg_path, "r") as f:
                        regs = json.load(f)
                    # cpu_core.reset() first regardless of restore_cpu_state:
                    # hd61700_init() zeroes the whole C struct including callback
                    # pointers/bank buffers that must be re-wired either way.
                    cpu_core.reset(self.debug_cfg["sys"])
                    if not restore_cpu_state:
                        cpu_core.set_pc(0x0000)
                        cpu_core.set_reg8(2, 0)  # Clear IB
                        cpu_core.set_reg8(5, 0)  # Clear IE
                        cpu_core.set_reg8(4, 0)  # Clear IA
                        cpu_core.set_reg8(3, 0)  # Clear UA
                        print("CPU reset after RAM load (saved registers ignored, PC=0x0000)")
                    else:
                        # Full resume: restore the exact CPU state save_state()
                        # captured (pc/flags/ia/ib/ie/ua/regmain/regsir/reg16), not
                        # just the RAM contents.
                        #
                        # set_reg8(3, ua) (UA) already re-syncs the internal fetch_ua/
                        # prev_ua fetch-bank pipeline as a side effect (see the comment
                        # on mod_set_reg8 in modhd61700.c, written specifically for this
                        # save-state-restore case) — without that, the first instruction
                        # executed after resume would fetch from bank 0 regardless of
                        # the restored UA (reset() leaves fetch_ua/prev_ua at 0), which
                        # is exactly the class of UA-bank corruption bug this project
                        # spent a long investigation on for FOREX_PB (see
                        # 調査用/FOREX_PB/investigation_notes.md) — so UA must be
                        # restored via set_reg8, not written directly into a save
                        # format that bypasses it.
                        #
                        # 2026-08-11 FOREX_PB boot-hang report: a menu-triggered RAM
                        # Load resumed at PC=0x9318/UA=0x50 — ROM handler code, not
                        # FOREX_PB's own (FOREX_PB does no disk access, ruling out
                        # the game itself having caused the VFDD activity seen right
                        # after resume) — straight into a TRP whose 0x6FFA jump table
                        # resolved to 0x3720 (unmapped dead zone). The likely
                        # explanation: the save was taken while a periodic interrupt
                        # handler (e.g. keyboard-scan, which fires on its own schedule
                        # regardless of the loaded program) was mid-flight, temporarily
                        # in ROM/UA=0x50 territory — but irq_status wasn't part of the
                        # saved format, so resume left it at 0 (reset() default) even
                        # though PC was sitting inside what was an active handler,
                        # leaving fetch_bank_ua()'s "force bank 0 while a handler is
                        # active" protection incorrectly disengaged for any interrupt
                        # nesting/RTNI bookkeeping that follows. get_irq_status()/
                        # get_state() (added same day) close this gap; hasattr() guards
                        # keep old saves (and old firmware without these C exports)
                        # loading fine, just without this restored.
                        cpu_core.set_reg8(2, int(regs.get("ib", 0)))   # IB
                        cpu_core.set_reg8(5, int(regs.get("ie", 0)))   # IE
                        cpu_core.set_reg8(4, int(regs.get("ia", 0)))   # IA
                        cpu_core.set_reg8(3, int(regs.get("ua", 0)))   # UA (syncs fetch_ua/prev_ua)
                        cpu_core.set_flags(int(regs.get("flags", 0)))
                        for i, v in enumerate(regs.get("regmain", [])):
                            cpu_core.set_reg(i, int(v))
                        for i, v in enumerate(regs.get("regsir", [])):
                            cpu_core.set_sreg(i, int(v))
                        for i, v in enumerate(regs.get("reg16", [])):
                            cpu_core.set_reg16(i, int(v))
                        if hasattr(cpu_core, "set_irq_status"):
                            cpu_core.set_irq_status(int(regs.get("irq_status", 0)))
                        if hasattr(cpu_core, "set_state"):
                            cpu_core.set_state(int(regs.get("cpu_flow_state", 0)))
                        cpu_core.set_pc(int(regs.get("pc", 0)))
                        print("CPU state restored from RAM load (PC=0x%04X UA=0x%02X irq_status=0x%02X)" %
                              (int(regs.get("pc", 0)), int(regs.get("ua", 0)),
                               int(regs.get("irq_status", 0))))
                        # Known limitation: peripheral-side state driven by the
                        # emulated program (e.g. an in-progress virtual-FDD transfer)
                        # is still not captured — a save taken mid-transfer can resume
                        # into a CPU state that no longer matches what the peripheral
                        # emulation expects. Unlike irq_status this isn't CPU state,
                        # so it isn't something get_irq_status()/get_state() can help
                        # with; a full fix would need VFDD's own controller state
                        # snapshotted too.
                else:
                    self._restore_registers_from_dump()
            else:
                print(f"Register file not found: {reg_path}")
        except Exception as e:
            print(f"Error loading registers: {e}")
            sys.print_exception(e)

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

        # Draw status text below the bezel
        # LCD height is lcd_height * scale. Bezel margin is ~4.
        y_pos = self._disp_y + int(self._lcd_height * self.lcd.scale) + 12
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





