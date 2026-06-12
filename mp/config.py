"""
PB-1000 Emulator configuration loader.
Loads and merges pb1000.ini files in priority order:
  built-in defaults < /pb1000.ini < /sd/pb1000.ini < <profile>/pb1000.ini
"""
import os

_DEFAULTS = {
    "keyboard": {
        "enable_usb_kbd": "true",
        "enable_uart_kbd": "false",
        "uart_baudrate": "115200",
        "uart_tx_pin": "4",
        "uart_rx_pin": "5",
        "key_hold_ms": "120",
        "key_release_hard_timeout_ms": "1200",
        "inter_key_gap_ms": "80",
        "uart_enter_always_exe": "true",
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
    "pio_uart": {
        "baudrate": "9600",
    },
    "display": {
        "fg_color": "0",
        "bg_color": "180",
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


def _merge(base, override):
    for section, kv in override.items():
        base.setdefault(section, {}).update(kv)


def load_config(profile_dir=None):
    """Load and merge all config files in priority order."""
    cfg = {s: dict(kv) for s, kv in _DEFAULTS.items()}
    _merge(cfg, load_ini("/pb1000.ini"))
    _merge(cfg, load_ini("/sd/pb1000.ini"))
    if profile_dir:
        _merge(cfg, load_ini(profile_dir + "/pb1000.ini"))
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
