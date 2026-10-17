"""
PB-1000 Keyboard Mapping Definitions

Search order for the override file:
  /roms/keymap.map  >  /keymap.map  >  /sd/roms/keymap.map  >  /sd/keymap.map
(internal flash checked before the SD card root -- 2026-10-17, reversed
from the original SD-first order at the user's request, same direction as
rom0.bin/rom1.bin's fallback -- see [[project_rom_loading_sd_limitation]])
Falls back to built-in defaults when no override file is found.

2026-10-17: this used to be a keymap.json override, loaded with the `json`
module. Dropped in favor of this line-based format (see _load_text_keymap()
below) after a real-hardware finding (調査用/heap_budget_bank3.md): loading
a full custom keymap.json cost ~17KB of GC heap on a tight boot. Most of
that was never the actual data (a couple hundred ints/strings) but the
*generic JSON tree* json.load() has to build to hold it along the way --
one Python dict per entry, plus repeated per-entry key strings ("row"/"ki"/
"label" etc.), for every single one of this keyboard's ~105 mapped keys.
Parsing this format line-by-line builds USB_MAP/ADV_MAP directly, with no
intermediate tree at all. tools/keymap_editor.py still edits/saves the
human-friendly JSON form on a PC (no heap constraint there) and exports
this compact form for the device -- see that tool's "Export for device"
command.
"""

# ── Compact text-format loader ───────────────────────────────────────────────
#
# Line format (see tools/keymap_editor.py's export for the generator):
#   U|scancode_hex|row|ki|label            -- USB_MAP entry
#   A|scancode_hex|mod|row,ki[;row,ki]|label  -- ADV_MAP entry (1 or 2
#     simultaneous key coords, e.g. Shift+1 = "!")
# `label` is always the LAST field and runs to the end of the line verbatim
# -- parsing limits the split count (maxsplit=4) instead of assuming the
# label is free of `|`/`,`/`;`, since this keyboard has keys for all three
# and their labels are literally that one character.
# Lines starting with # (and blank lines) are ignored.

def _load_text_keymap():
    for path in ('/roms/keymap.map', '/keymap.map', '/sd/roms/keymap.map', '/sd/keymap.map'):
        try:
            usb_map = {}
            adv_map = {}
            with open(path) as f:
                for raw in f:
                    line = raw.rstrip('\r\n')
                    if not line or line[0] == '#':
                        continue
                    tag = line[0]
                    if tag == 'U':
                        parts = line.split('|', 4)
                        sc = int(parts[1], 16)
                        usb_map[sc] = ((int(parts[2]), int(parts[3])), parts[4])
                    elif tag == 'A':
                        parts = line.split('|', 4)
                        sc = int(parts[1], 16)
                        mod = int(parts[2])
                        coords = [tuple(int(x) for x in cp.split(','))
                                  for cp in parts[3].split(';')]
                        adv_map[(sc, mod)] = (coords, parts[4])
            if usb_map or adv_map:
                print(f"Keymap: loaded {path}")
                return usb_map, adv_map
        except OSError:
            pass
        except Exception as e:
            print(f"Keymap file error ({path}): {e}")
    return None, None


# ── Built-in defaults ─────────────────────────────────────────────────────────

def _build_defaults():
    KEY_SHIFT   = (11,  2)
    KEY_FUNC    = (12,  2)
    KEY_DOLLAR  = ( 2,  4)
    KEY_EQUAL   = ( 2,  6)
    KEY_L_BRKT  = ( 7,  4)
    KEY_R_BRKT  = ( 6,  3)
    KEY_PLUS    = ( 9,  3)
    KEY_ASTER   = ( 8,  3)
    KEY_CONTR   = ( 6,  1)
    KEY_D_QUOTE = ( 2,  3)
    KEY_AMP     = ( 2,  5)

    usb_map = {
        # --- Alphabetic (0x04-0x1D) ---
        0x04: (( 4,  4), 'A'),  0x05: (( 5,  7), 'B'),  0x06: (( 5,  5), 'C'),  0x07: (( 4,  6), 'D'),
        0x08: (( 3,  5), 'E'),  0x09: (( 4,  7), 'F'),  0x0A: (( 4,  8), 'G'),  0x0B: (( 4,  1), 'H'),
        0x0C: (( 8,  1), 'I'),  0x0D: (( 9,  1), 'J'),  0x0E: (( 9,  8), 'K'),  0x0F: (( 9,  7), 'L'),
        0x10: (( 5,  1), 'M'),  0x11: (( 5,  8), 'N'),  0x12: (( 8,  8), 'O'),  0x13: (( 8,  7), 'P'),
        0x14: (( 3,  3), 'Q'),  0x15: (( 3,  6), 'R'),  0x16: (( 4,  5), 'S'),  0x17: (( 3,  7), 'T'),
        0x18: (( 3,  1), 'U'),  0x19: (( 5,  6), 'V'),  0x1A: (( 3,  4), 'W'),  0x1B: (( 5,  4), 'X'),
        0x1C: (( 3,  8), 'Y'),  0x1D: (( 5,  3), 'Z'),
        # --- Numeric (0x1E-0x27) ---
        0x1E: (( 9,  6), '1'),  0x1F: (( 9,  5), '2'),  0x20: (( 9,  4), '3'),  0x21: (( 8,  6), '4'),
        0x22: (( 8,  5), '5'),  0x23: (( 8,  4), '6'),  0x24: (( 7,  7), '7'),  0x25: (( 7,  6), '8'),
        0x26: (( 7,  5), '9'),  0x27: ((10,  7), '0'),
        # --- Other base keys ---
        0x2C: ((10,  1), 'SPACE'),
        0x2D: ((10,  3), '-'),
        0x2E: (( 7,  1), '^'),
        0x33: (( 2,  7), ';'),
        0x34: (( 2,  8), ':'),
        0x36: (( 2,  1), ','),
        0x37: ((10,  6), '.'),
        0x38: (( 7,  3), '/'),
        # --- Navigation & editing ---
        0x28: ((10,  4), 'EXE'),
        0x29: (( 1,  1), 'BREAK'),
        0x2A: (( 6,  7), 'BS'),
        0x49: (( 6,  5), 'INS'),
        0x4F: (( 3,  9), 'RIGHT'),
        0x50: (( 5, 10), 'LEFT'),
        0x51: (( 4,  9), 'DOWN'),
        0x52: (( 5,  9), 'UP'),
        0x48: (( 6,  8), 'CLS'),
        # --- Function keys (PB-1000 mapped) ---
        0x3A: (( 7,  9), 'F1'),
        0x3B: (( 8,  9), 'F2'),
        0x3C: (( 9,  9), 'F3'),
        0x3D: ((10,  9), 'F4'),
        0x3E: (( 6, 11), 'LCKEY'),
        0x3F: (( 5, 11), 'MENU'),
        0x40: (( 4, 11), 'CAL'),
        0x41: (( 2, 11), 'MEMO IN'),
        0x42: (( 3, 11), 'MEMO'),
        0x43: (( 2, 10), 'IN'),
        0x44: (( 3, 10), 'OUT'),
        0x45: (( 4, 10), 'CALC'),
        # --- Other ---
        0x35: ((10,  8), 'KANA'),
        0x39: (( 4,  3), 'CAPS'),
        0x4D: (( 6,  4), 'STOP'),
        0x65: ((10,  5), 'ANS'),
        0x8A: (( 7,  8), 'ENG'),
    }

    def _u(label):
        for coord, lbl in usb_map.values():
            if lbl == label:
                return coord
        raise KeyError(label)

    adv_map = {
        (0xE2, 2): ([KEY_SHIFT],             'SHIFT'),
        (0xE6, 2): ([KEY_SHIFT],             'SHIFT'),
        (0xE0, 4): ([KEY_FUNC],              'Func'),
        (0xE4, 4): ([KEY_FUNC],              'Func'),
        (0x1E, 1): ([KEY_SHIFT, KEY_D_QUOTE],'!'),
        (0x1F, 1): ([KEY_D_QUOTE],           '"'),
        (0x21, 1): ([KEY_DOLLAR],            '$'),
        (0x22, 1): ([KEY_SHIFT, KEY_AMP],    '%'),
        (0x23, 1): ([KEY_AMP],               '&'),
        (0x25, 1): ([KEY_L_BRKT],            '('),
        (0x26, 1): ([KEY_R_BRKT],            ')'),
        (0x2D, 1): ([KEY_EQUAL],             '='),
        (0x33, 1): ([KEY_PLUS],              '+'),
        (0x34, 1): ([KEY_ASTER],             '*'),
        (0x20, 1): ([KEY_SHIFT, KEY_DOLLAR], '#'),
        (0x24, 1): ([KEY_SHIFT, KEY_EQUAL],  "'"),
        (0x38, 1): ([KEY_SHIFT, _u(',')],    '?'),
        (0x36, 1): ([KEY_SHIFT, _u(';')],    '<'),
        (0x37, 1): ([KEY_SHIFT, _u(':')],    '>'),
        (0x29, 1): ([_u('CLS')],             'CLS'),
        (0x30, 1): ([KEY_SHIFT, KEY_ASTER],  '{'),
        (0x32, 1): ([KEY_SHIFT, _u('/')],    '}'),
        (0x87, 1): ([KEY_SHIFT, _u('8')],    '_'),
        (0x2F, 1): ([KEY_SHIFT, _u('9')],    '`'),
        (0x89, 1): ([KEY_SHIFT, _u('4')],    '|'),
        (0x2E, 1): ([KEY_SHIFT, _u('5')],    '~'),
        (0x4C, 0): ([KEY_SHIFT, _u('INS')],  'DEL'),
        (0x89, 0): ([KEY_SHIFT, _u('^')],    '\\(Yen)'),
        (0x4A, 0): ([KEY_SHIFT, _u('CLS')],  'HOME'),
        (0x2F, 0): ([KEY_SHIFT, _u('7')],    '@'),
        (0x30, 0): ([KEY_SHIFT, KEY_L_BRKT], '['),
        (0x32, 0): ([KEY_SHIFT, KEY_R_BRKT], ']'),
        (0x87, 0): ([KEY_SHIFT, _u('6')],    '\\(b.s.)'),
        (0x47, 0): ([KEY_CONTR],             'CONTR.'),
        (0x45, 8): ([(6, 6)],                'NEWALL'),
    }

    return usb_map, adv_map


# ── Load (override file first, then defaults) ────────────────────────────────

_ovr_usb, _ovr_adv = _load_text_keymap()
if _ovr_usb is not None:
    USB_MAP = _ovr_usb
    ADV_MAP = _ovr_adv
else:
    USB_MAP, ADV_MAP = _build_defaults()


# ── Derived KEY_MAP (always rebuilt from active USB_MAP / ADV_MAP) ────────────

KEY_MAP = {v[1].lower(): v[0] for v in USB_MAP.values() if len(v[1]) == 1}
KEY_MAP.update({label: coords[0]
                for (sc, mod), (coords, label) in ADV_MAP.items()
                if len(label) == 1 and len(coords) == 1})
KEY_MAP.update({
    'tk1':  ( 7, 12), 'tk2':  ( 8, 12), 'tk3':  ( 9, 12), 'tk4':  (10, 12),
    'tk5':  ( 7, 11), 'tk6':  ( 8, 11), 'tk7':  ( 9, 11), 'tk8':  (10, 11),
    'tk9':  ( 7, 10), 'tk10': ( 8, 10), 'tk11': ( 9, 10), 'tk12': (10, 10),
    'tk13': ( 7,  9), 'tk14': ( 8,  9), 'tk15': ( 9,  9), 'tk16': (10,  9),
})

# Multi-character named keys (e.g. "bs", "exe", "menu") -- what pb1000.ini's
# [joystick] key_* options document as valid names. KEY_MAP above only ever
# covers single-character labels (a-z, 0-9, punctuation), so a name like
# "bs" was previously unresolvable by _parse_joystick_key() in
# main_input_joystick.py and silently fell back to the default key.
NAMED_KEYS = {v[1].lower(): v[0] for v in USB_MAP.values() if len(v[1]) > 1}
NAMED_KEYS.update({label.lower(): coords[0]
                    for (sc, mod), (coords, label) in ADV_MAP.items()
                    if len(label) > 1 and len(coords) == 1})
# pb1000.ini documents "brk", but the actual USB_MAP label is "BREAK".
NAMED_KEYS.setdefault('brk', NAMED_KEYS.get('break'))


# ── Public helpers ────────────────────────────────────────────────────────────

# Human-readable overrides for internal labels used in the status bar
_DISPLAY_LABEL_MAP = {
    'NEWALL': 'NEW ALL',
    'LCKEY':  'LC KEY',
}

def get_label(scancode, mod=0):
    """Resolve a USB scancode (and optional modifier bitmask) to a display label.

    mod bitmask: 1=Shift 2=Alt 4=Ctrl 8=Win/GUI

    2026-10-17: looks up hd61700.get_key_label() (src/modhd61700.c) instead
    of the old USB_MAP/ADV_MAP dicts -- those stayed resident on the GC
    heap all session just for this, duplicating data the C side already
    keeps for the real key-scan path. See release_maps()."""
    try:
        import hd61700 as _hd
        label = _hd.get_key_label(scancode, mod)
    except Exception:
        label = None
    if label is None:
        label = "SC:{:02X}".format(scancode)
    return _DISPLAY_LABEL_MAP.get(label, label)

def get_adv_map_list():
    """Returns ADV_MAP as [(scancode, mod, [(row, ki), ...], label), ...]
    for the C module (hd61700.keyboard_config_adv() -- the label is a
    2026-10-17 addition, see that function's comment)."""
    return [(sc, mod, v[0], v[1]) for (sc, mod), v in ADV_MAP.items()]

def get_base_map_list():
    """Returns [(scancode, row, ki, label), ...] for the C module
    (hd61700.keyboard_config_base() -- label added 2026-10-17)."""
    return [(sc, v[0][0], v[0][1], v[1]) for sc, v in USB_MAP.items()]

def release_maps():
    """Frees USB_MAP/ADV_MAP -- call once, right after
    get_adv_map_list()/get_base_map_list() have both been consumed by
    hd61700.keyboard_config_adv()/_base() (main_boot.py's
    configure_c_keyboard()). Safe to drop from here on: get_label() reads
    the C-side copy instead, and KEY_MAP/NAMED_KEYS (joystick key-name
    parsing) were already derived from these at module-load time and
    don't need them again. 2026-10-17: see this module's docstring --
    these two dicts (coords+label for ~105 entries) were the single
    largest piece of this module's GC-heap footprint, almost entirely
    redundant with the C-side dynamic_adv_map/dynamic_base_map once this
    point is reached.

    2026-10-17 real-hardware-pending fix: clearing USB_MAP/ADV_MAP alone
    did NOT actually free the dicts when an override file (keymap.map) was
    loaded -- _ovr_usb/_ovr_adv (module globals set once at module-load
    time, right above `if _ovr_usb is not None:`) still held their own
    reference to the exact same dict objects, so this function was only
    clearing two of the (at least) four names pointing at them. Must clear
    those too, or the override case (the common one -- a default-only
    install has _ovr_usb/_ovr_adv == None already) never actually released
    anything."""
    global USB_MAP, ADV_MAP, _ovr_usb, _ovr_adv
    USB_MAP = None
    ADV_MAP = None
    _ovr_usb = None
    _ovr_adv = None
