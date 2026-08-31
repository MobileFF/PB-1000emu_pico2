"""
PB-1000 Emulator configuration loader.
Loads and merges pb1000.ini files in priority order:
  built-in defaults < /pb1000.ini < /sd/pb1000.ini < <profile>/pb1000.ini
"""
import os

_DEFAULTS = {
    "keyboard": {
        "enable_usb_kbd": "true",
        "key_hold_ms": "120",
        "key_release_hard_timeout_ms": "1200",
        "inter_key_gap_ms": "80",
        "key_pulse_interval_ms": "25",
        "poll_interval_ms": "8",  # USB HID polling interval (usb_host_core.c's bg timer)
    },
    "emulator": {
        "frame_interval_ms": "33",
        "active_step_count": "12000",
        "sleep_poll_ms": "10",
        "step_timer_tick_steps": "40000",
        "timer_tick_ms": "1000",
        "loop_idle_ms": "0",
        "step_chunk": "2048",
        "enable_repl_uart": "true",
    },
    "disk": {
        "enabled": "false",
        "backend": "raw",
        "path": "",
        "readonly": "false",
    },
    "profile": {
        "default_profile": "default",
        "ui_timeout_ms": "30000",
    },
    "joystick": {
        "enable": "false",
        "enable_fire2": "true",
        "debounce_ms": "20",
        "poll_interval_ms": "10",
        "key_up": "",
        "key_down": "",
        "key_left": "",
        "key_right": "",
        "key_fire1": "",
        "key_fire2": "",
    },
    "beep": {
        "enable":   "true",
        "gpio_pin": "14",
        "freq_hz":  "1000",
        "duty":     "50",
    },
    "rs232c": {
        "enable": "true",
        "baudrate": "9600",
        "tx_pin": "6",
        "rx_pin": "13",
    },
    "display": {
        "fg_color": "0",
        "bg_color": "180",
        "driver":   "ILI9341",
        "scale":    "1.5",
        "rotation": "0",
        "vdp_enable": "true",
    },
    "wifi": {
        "ssid": "",
        "password": "",
    },
    "ntp": {
        "enable": "true",
        "server": "pool.ntp.org",
        "tz_offset_h": "9",
        "timeout_ms": "15000",
    },
    "debug": {
        "cpu_debug": "false",
        "key_debug": "false",
        "lcd_debug": "false",
    },
    "hdmi": {
        "enable":    "false",
        "cs_pin":    "28",
        "baudrate":  "10000000",
        "frame_skip": "1",
    },
    # Unifies what used to be [boot_status]/[clock_overlay]/[mem_overlay] --
    # three separately-toggleable on-screen info displays (BootStatusOverlay/
    # ClockOverlay/MemOverlay classes remain separate implementations, since
    # boot-time and runtime genuinely need different clock sources -- the CPU
    # isn't stepping yet during boot, so TIME$/DATE$ can't be read from PB-1000
    # RAM the way ClockOverlay does at runtime; BootStatusOverlay's clock reads
    # the Pico's own RTC instead. See boot_status.py/clock_overlay.py docstrings).
    "overlay": {
        "show_profile_name": "true",   # boot-time only
        "show_clock": "false",         # boot-time (Pico RTC) + runtime (PB-1000 TIME$/DATE$)
        "show_mem_free": "false",      # runtime only
        "show_log": "false",           # boot-time only -- mirrors the last REPL log line to the LCD
    },
}


def load_ini(path):
    """Parse an ini file. Returns {section: {key: value}} or {} on error."""
    try:
        os.stat(path)
    except OSError:
        return {}
    result = {}
    section = ""
    with open(path, "r") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line[0] in ("#", ";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if not section or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lower()
            v = v.split(";", 1)[0].split("#", 1)[0].strip()
            result.setdefault(section, {})[k] = v
    return result


# Sections that describe fixed hardware wiring rather than a per-SD-card or
# per-profile preference. Honored only from the flash-root /pb1000.ini (plus
# built-in defaults) -- an [hdmi] section in /sd/pb1000.ini or a per-profile
# pb1000.ini is ignored entirely, never merged in. (Real incident this
# guards against: an [hdmi] enable=true left over in /sd/pb1000.ini kept
# silently re-enabling HDMI even after the flash-root ini was changed to
# enable=false, since /sd/pb1000.ini normally outranks it in the priority
# chain above.)
_FLASH_ONLY_SECTIONS = {"hdmi"}


def _merge(base, override, skip_sections=()):
    for section, kv in override.items():
        if section in skip_sections:
            continue
        base.setdefault(section, {}).update(kv)


def load_config(profile_dir=None):
    """Load and merge all config files in priority order."""
    cfg = {s: dict(kv) for s, kv in _DEFAULTS.items()}
    _merge(cfg, load_ini("/pb1000.ini"))
    _merge(cfg, load_ini("/sd/pb1000.ini"), skip_sections=_FLASH_ONLY_SECTIONS)
    if profile_dir:
        _merge(cfg, load_ini(profile_dir + "/pb1000.ini"), skip_sections=_FLASH_ONLY_SECTIONS)
    return cfg


def get_bool(cfg, section, key):
    v = cfg.get(section, {}).get(key, "false").lower()
    return v in ("1", "true", "yes", "on")


def get_int(cfg, section, key):
    try:
        return int(cfg.get(section, {}).get(key, "0"))
    except (ValueError, TypeError):
        return 0


def get_str(cfg, section, key):
    return cfg.get(section, {}).get(key, "")
