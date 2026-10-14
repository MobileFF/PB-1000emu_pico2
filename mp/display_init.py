"""
Early display/SD-card/touch bring-up -- split out of pb1000.py so that
init_display_only() (main_boot.py), needed before the profile picker even
appears, doesn't have to drag in all of pb1000.py (PB1000System and
everything it depends on, ~2000 lines) just for this one function. That
class isn't needed until create_system() at Step 7, well after the picker
and its F1 setup menu have already run -- see main_boot.py's own note
about main_input/main_runtime/etc. for the same reasoning applied there.

This module intentionally has no dependency on pb1000.py (or vice versa
beyond a thin re-export -- see pb1000.py's own `from display_init import
init_display` line, kept only so existing `from pb1000 import init_display`
call sites, mostly test scripts, don't need to change).
"""
import os
import sys
import machine

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

# SD_INIT_BAUD: the SD spec's mandatory low-speed handshake rate --
# sdcard.py's init_card() already hardcodes this itself for that handshake
# regardless of what's passed to SDCard(); this constant only documents
# that rate here, it isn't actually wired to anything below.
SD_INIT_BAUD = 400_000
#
# SD_DATA_BAUD: SDCard()'s own baudrate= parameter, used for every
# readblocks()/writeblocks() DATA transfer -- NOT just the handshake (see
# sdcard.py). This used to be given SD_INIT_BAUD (400000) directly below,
# meaning every SD block read/write ran the data phase at 400kHz too, not
# just the handshake -- found 2026-10-14 while porting a matching fix from
# the sibling MSX-emulator project (same RP2350 target, byte-identical SD
# wiring: SPI1, SCK=10/MOSI=11/MISO=12/CS=15), which had independently
# found and fixed the exact same bug in its own copy of sdcard.py.
#
# 12MHz is that project's own real-hardware result, not a fresh guess: a
# step sweep (4/6/8/10/12/16MHz, mp/test/sweep_sd_baud.py there) found
# byte-exact reads at every step with throughput plateauing right at
# 12MHz (16MHz gave no further real gain -- per-block/command overhead,
# not SPI clock, is the limiting factor above this point), confirmed
# stable across multiple real-hardware cold power-cycle boots. Carried
# over here on the strength of the identical SD wiring, but NOT yet
# independently verified on THIS project's own hardware -- please re-test
# (including a full cold boot, not just repeated reads after the card is
# already initialized) before this is treated as verified for PB-1000.
SD_DATA_BAUD = 12_000_000


def init_sdcard(spi, lcd_baudrate=40_000_000):
    try:
        from sdcard import SDCard
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        # Low-speed handshake only for init_card() (see SD_INIT_BAUD above);
        # SD_DATA_BAUD for every actual block read/write; restore_baudrate
        # (the LCD's own SPI baudrate) only for idle periods between SD
        # operations, since this bus is shared with the LCD.
        sd = SDCard(spi, sd_cs, baudrate=SD_DATA_BAUD, restore_baudrate=lcd_baudrate)
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
    # Passed through to the driver so it can re-deassert these before every
    # transaction -- see ILI9341/ST7796's own docstring note on this.
    sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
    t_cs = machine.Pin(T_CS_PIN, machine.Pin.OUT, value=1)

    if driver == "ST7796":
        from st7796 import ST7796
        display = ST7796(spi, cs, dc, rst, width=480, height=320, rotation=rotation, sd_cs=sd_cs, t_cs=t_cs)
        display.fill_rect(0, 0, 480, 320, 0x0000)
        print(f"Display: ST7796 480x320 (rotation={rotation})")
    else:
        from ili9341 import ILI9341
        display = ILI9341(spi, cs, dc, rst, width=320, height=240, rotation=rotation, sd_cs=sd_cs, t_cs=t_cs)
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
