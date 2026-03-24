"""
PB-1000 Keyboard Mapping Definitions
This file contains the matrix coordinates and USB-to-Guest translation tables.
"""

# Special key coordinates (KO row, KI line)
KEY_EXE    = (10,  4)  # Enter/Execute
KEY_SHIFT  = (11,  2)  # Shift (Guest SFT)
KEY_BREAK  = ( 1,  1)  # Break (Ctrl+C / ESC)
KEY_ANS    = (10,  5)  # ANS
KEY_BS     = ( 6,  7)  # Backspace
KEY_INS    = ( 6,  5)  # Insert
KEY_LEFT   = ( 5, 10)  # Cursor Left
KEY_RIGHT  = ( 3,  9)  # Cursor Right
KEY_UP     = ( 5,  9)  # Cursor Up
KEY_DOWN   = ( 4,  9)  # Cursor Down
KEY_NEWALL = ( 6,  6)  # NEWALL
KEY_MENU   = ( 5, 11)  # MENU
KEY_LCKEY  = ( 6, 11)  # LCKEY
KEY_CAL    = ( 4, 11)  # CAL
KEY_CLS    = ( 6,  8)  # CLS
KEY_KANA   = (10,  8)  # KANA

# Basic Key mapping: label -> (KO row, KI line)
KEY_MAP = {
    'a': (4, 4), 'b': (5, 7), 'c': (5, 5), 'd': (4, 6),
    'e': (3, 5), 'f': (4, 7), 'g': (4, 8), 'h': (4, 1),
    'i': (8, 1), 'j': (9, 1), 'k': (9, 8), 'l': (9, 7),
    'm': (5, 1), 'n': (5, 8), 'o': (8, 8), 'p': (8, 7),
    'q': (3, 3), 'r': (3, 6), 's': (4, 5), 't': (3, 7),
    'u': (3, 1), 'v': (5, 6), 'w': (3, 4), 'x': (5, 4),
    'y': (3, 8), 'z': (5, 3),
    '0': (10, 7), '1': (9, 6), '2': (9, 5), '3': (9, 4),
    '4': (8, 6), '5': (8, 5), '6': (8, 4), '7': (7, 7),
    '8': (7, 6), '9': (7, 5),
    ' ': (10, 1), '.': (10, 6), '=': (2, 6),
    '+': (9, 3), '-': (10, 3), '*': (8, 3), '/': (7, 3),
    
    # Touch panel keys (T1-T16)
    'tk1': (7, 12), 'tk2': (8, 12), 'tk3': (9, 12), 'tk4': (10, 12),
    'tk5': (7, 11), 'tk6': (8, 11), 'tk7': (9, 11), 'tk8': (10, 11),
    'tk9': (7, 10), 'tk10': (8, 10), 'tk11': (9, 10), 'tk12': (10, 10),
    'tk13': (7, 9), 'tk14': (8, 9), 'tk15': (9, 9), 'tk16': (10, 9),
}

# Advanced Map (USB Scancode, Host Mod) -> List of (row, ki)
# mod: bit0=Shift, bit1=Alt
ADV_MAP = {
    (0xE2, 2): [KEY_SHIFT], # L_ALT -> SFT
    (0xE6, 2): [KEY_SHIFT], # R_ALT -> SFT
    (0x1F, 1): [(2, 3)],    # Shift + 2 -> "
    (0x21, 1): [(2, 4)],            # Shift + 4 -> $
    (0x23, 1): [(2, 5)],            # Shift + 6 -> &
    (0x25, 1): [(7, 4)],            # Shift + 8 -> (
    (0x26, 1): [(6, 3)],            # Shift + 9 -> )
    (0x2D, 1): [(2, 6)],            # Shift + - -> =
    (0x33, 1): [(9, 3)],            # Shift + ; -> +
    (0x34, 1): [(8, 3)],            # Shift + : -> *
    (0x20, 1): [KEY_SHIFT,( 2, 4)], # Shift + 3 -> #(SFT+$)
    (0x24, 1): [KEY_SHIFT,( 2, 6)], # Shift + 7 -> SFT + = (')
    (0x32, 1): [KEY_SHIFT,( 7, 3)], # Shift + ] -> SFT + /
    (0x38, 1): [KEY_SHIFT,( 2, 1)], # Shift + / -> SFT + , (?)
    
    # Unshifted symbols
    (0x37, 0): [(10, 6)],   # . (KO10, KI6)
    (0x36, 0): [(2, 1)],    # , (KO2, KI1)
    (0x2E, 0): [(7, 1)],    # ^ (KO7, KI1)
    (0x2D, 0): [(10, 3)],   # - (KO10, KI3)
    (0x33, 0): [(2, 7)],    # ; (KO2, KI7)
    (0x34, 0): [(2, 8)],    # : (KO2, KI8)
}

# USB Scancode -> Label for Base Mapping
USB_TO_CHAR = {
    0x04: 'a', 0x05: 'b', 0x06: 'c', 0x07: 'd',
    0x08: 'e', 0x09: 'f', 0x0A: 'g', 0x0B: 'h',
    0x0C: 'i', 0x0D: 'j', 0x0E: 'k', 0x0F: 'l',
    0x10: 'm', 0x11: 'n', 0x12: 'o', 0x13: 'p',
    0x14: 'q', 0x15: 'r', 0x16: 's', 0x17: 't',
    0x18: 'u', 0x19: 'v', 0x1A: 'w', 0x1B: 'x',
    0x1C: 'y', 0x1D: 'z',
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4',
    0x22: '5', 0x23: '6', 0x24: '7', 0x25: '8',
    0x26: '9', 0x27: '0',
    0x2C: ' ',
}

# Special scancode mapping
SPECIAL_MAP = {
    0x28: KEY_EXE,
    0x29: KEY_BREAK,
    0x2A: KEY_BS,
    0x4F: KEY_RIGHT,
    0x50: KEY_LEFT,
    0x51: KEY_DOWN,
    0x52: KEY_UP,
    0x49: KEY_INS,
    0x4C: KEY_BS,
    0x45: KEY_NEWALL,
    0x3A: (7, 9),
    0x3B: (8, 9),
    0x3C: (9, 9),
    0x3D: (10, 9),
    0x3E: KEY_MENU,
    0x3F: KEY_LCKEY,
    0x40: KEY_CAL,
    0x43: KEY_CLS,
    0x35: KEY_KANA,
}
# Status Label Mapping (Scancode -> Display Name)
STATUS_LABELS = {
    0x45: "NEW ALL",
    0x28: "EXE",
    0x29: "BREAK",
    0x2A: "BS",
    0x4C: "BS",
    0x4F: "RIGHT",
    0x50: "LEFT",
    0x51: "DOWN",
    0x52: "UP",
    0x49: "INS",
    0x3E: "MENU",
    0x3F: "LCKEY",
    0x40: "CAL",
    0x2C: "SPACE",
    0x3A: "F1",
    0x3B: "F2",
    0x3C: "F3",
    0x3D: "F4",
    0x42: "RESET",
    0x43: "CLS",
    0x35: "KANA",
}

def get_label(scancode):
    """Resolve a scancode to a display label."""
    if scancode in STATUS_LABELS:
        return STATUS_LABELS[scancode]
    if scancode in USB_TO_CHAR:
        return USB_TO_CHAR[scancode].upper()
    return f"SC:{scancode:02X}"

def get_adv_map_list():
    """Returns ADV_MAP formatted as a list of tuples for the C module:
    [(scancode, mod, [(row, ki), ...]), ...]
    """
    res = []
    for (sc, mod), coords in ADV_MAP.items():
        res.append((sc, mod, coords))
    return res

def get_base_map_list():
    """Returns a flattened BASE map for the C module:
    [(scancode, row, ki), ...]
    """
    res = []
    # 1. From USB_TO_CHAR + KEY_MAP
    for sc, char in USB_TO_CHAR.items():
        if char in KEY_MAP:
            coord = KEY_MAP[char]
            res.append((sc, coord[0], coord[1]))
    # 2. From SPECIAL_MAP
    for sc, coord in SPECIAL_MAP.items():
        res.append((sc, coord[0], coord[1]))
    return res
