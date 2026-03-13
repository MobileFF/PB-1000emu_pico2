"""
PB-1000 Keyboard Matrix Emulation
Maps USB keyboard input to PB-1000's keyboard matrix.
The PB-1000 has a matrix keyboard scanned via IA (row select)
and read via KY (column data).
"""
import time

class KeyboardMatrix:
    """
    PB-1000 keyboard matrix emulation.

    The HD61700 CPU scans the keyboard by:
    1. Writing a row select value to IA register (via PST IA instruction)
    2. Reading the KY register (via GRE KY instruction)

    The PB-1000 keyboard matrix layout:
    - 13 rows (selected by IA write: 0..12)
    - 12 KI lines (KI01..KI12)
    """

    # Key mapping: host key -> (KO row, KI line number)
    # Calibrated against pb1000es KeyTab (KO0..12).
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
        
        # Touch panel keys (T1-T16) mapping to KO7-KO10 and KI9-KI12
        'tk1': (7, 12), 'tk2': (8, 12), 'tk3': (9, 12), 'tk4': (10, 12),
        'tk5': (7, 11), 'tk6': (8, 11), 'tk7': (9, 11), 'tk8': (10, 11),
        'tk9': (7, 10), 'tk10': (8, 10), 'tk11': (9, 10), 'tk12': (10, 10),
        'tk13': (7, 9), 'tk14': (8, 9), 'tk15': (9, 9), 'tk16': (10, 9),
    }

    # Special key codes
    KEY_EXE    = (10, 4)  # Enter/Execute
    KEY_MODE   = (5, 11)  # Mode (MENU)
    KEY_SHIFT  = (11, 2)  # Shift
    KEY_BREAK  = (1, 1)   # Break (Ctrl+C / ESC)
    KEY_ANS    = (10, 5)  # ANS
    KEY_BS     = (6, 7)   # Backspace
    KEY_INS    = (6, 5)   # Insert
    KEY_DEL    = KEY_BS   # PB-1000 has no DEL key (Delete -> Backspace)
    KEY_LEFT   = (5, 10)  # Cursor Left
    KEY_RIGHT  = (3, 9)   # Cursor Right
    KEY_UP     = (5, 9)   # Cursor Up
    KEY_DOWN   = (4, 9)   # Cursor Down
    KEY_NEWALL = (6, 6)   # NEWALL
    KEY_MENU   = (5,11)   # MENU
    KEY_LCKEY  = (6,11)   # LCKEY
    KEY_CAL    = (4,11)   # CAL
    TRACE_MIN_PRINT_MS = 120

    @staticmethod
    def _ki_to_col(ki):
        """Convert KI line number to keyboard pin-input bit index.

        Pin bit mapping:
          KI01..KI08 -> bit7..bit0
          KI09..KI12 -> bit11..bit8
        """
        if 1 <= ki <= 8:
            return 8 - ki
        if 9 <= ki <= 12:
            return 20 - ki
        raise ValueError(f"Invalid KI number: {ki}")

    @staticmethod
    def _ky_to_active_kis(ky):
        """Decode active KI line numbers from a 16-bit KY register value."""
        active = []
        for ki in range(1, 13):
            pin_bit = KeyboardMatrix._ki_to_col(ki)
            if pin_bit < 8:
                ky_bit = pin_bit
            else:
                ky_bit = pin_bit + 4
            if ky & (1 << ky_bit):
                active.append(ki)
        return active

    def __init__(self, debug=False):
        # 13 rows x 12 keyboard input bits (bit0..bit11)
        self.matrix = [[False] * 12 for _ in range(13)]
        self.ia_select = 0    # Current row select value
        self.ky_data = 0      # Last KY read result
        self._pressed_keys = set()  # Currently pressed keys
        self.debug = bool(debug)
        self._trace_last_ia = None
        self._trace_last_ky = None
        self._trace_last_ms = 0

    def _selected_rows(self):
        """Decode IA low nibble as PB-1000 key-output selector."""
        sel = self.ia_select & 0x0F
        # 13: ALL KEY output
        if sel == 0x0D:
            return range(13)
        # 0..12 selects one key-output row.
        if 0 <= sel <= 12:
            return (sel,)
        return ()

    def kb_write(self, data):
        """CPU writes to IA register (selects keyboard row)."""
        self.ia_select = data

    def kb_read(self):
        """
        CPU reads KY register.
        Returns 16-bit KY value.
        """
        # Keep only bits used by MAME merge mask (low 8 + high nibble).
        # Read only currently selected KO row(s) based on IA.
        result = 0
        selected_rows = tuple(self._selected_rows())
        for row in selected_rows:
            for col in range(12):
                if self.matrix[row][col]:
                    if col < 8:
                        result |= (1 << col)
                    else:
                        result |= (1 << (col + 4))
        ky = result & 0xFFFF
        if self.debug and self.TRACE_SCAN and ky != 0:
            now = time.ticks_ms()
            changed = (self.ia_select != self._trace_last_ia) or (ky != self._trace_last_ky)
            elapsed = time.ticks_diff(now, self._trace_last_ms)
            should_print = changed or (elapsed >= self.TRACE_MIN_PRINT_MS)
            if should_print:
                kis = self._ky_to_active_kis(ky)
                ki_text = ",".join(f"KI{ki:02d}" for ki in kis) if kis else "NONE"
                if len(selected_rows) == 1:
                    ko_text = f"KO={selected_rows[0]}"
                elif len(selected_rows) == 13:
                    ko_text = "KO=ALL"
                else:
                    ko_text = "KO=NONE"
                print(
                    f"KB READ: {ko_text} IA=0x{self.ia_select:02X} "
                    f"-> KY=0x{ky:04X} ({ky:016b}) "
                    f"(active KI: {ki_text})"
                )
                self._trace_last_ia = self.ia_select
                self._trace_last_ky = ky
                self._trace_last_ms = now
        return ky

    def key_press(self, key):
        """
        Press a key.
        Args:
            key: str - the key character or tuple (row, KI)
        """
        if isinstance(key, tuple):
            row, ki = key
            col = self._ki_to_col(ki)
        elif isinstance(key, str):
            key_lower = key.lower()
            if key_lower in self.KEY_MAP:
                row, ki = self.KEY_MAP[key_lower]
                col = self._ki_to_col(ki)
            else:
                return
        else:
            return

        if 0 <= row < 13 and 0 <= col < 12:
            self.matrix[row][col] = True
            self._pressed_keys.add((row, col))
            if self.debug:
                print(f"KB PRESS: KO={row} KI={ki}")

    def key_release(self, key):
        """
        Release a key.
        Args:
            key: str - the key character or tuple (row, KI)
        """
        if isinstance(key, tuple):
            row, ki = key
            col = self._ki_to_col(ki)
        elif isinstance(key, str):
            key_lower = key.lower()
            if key_lower in self.KEY_MAP:
                row, ki = self.KEY_MAP[key_lower]
                col = self._ki_to_col(ki)
            else:
                return
        else:
            return

        if 0 <= row < 13 and 0 <= col < 12:
            self.matrix[row][col] = False
            self._pressed_keys.discard((row, col))
            if self.debug:
                print(f"KB RELEASE: KO={row} KI={ki}")

    def release_all(self):
        """Release all currently pressed keys."""
        for row in range(13):
            for col in range(12):
                self.matrix[row][col] = False
        self._pressed_keys.clear()

    def has_key_pressed(self):
        """Check if any key is currently pressed."""
        return len(self._pressed_keys) > 0

    def set_debug(self, enabled):
        """Enable/disable keyboard debug output."""
        self.debug = bool(enabled)

    def process_usb_key(self, scancode, pressed=True):
        """
        Process a USB HID keyboard scancode.
        Maps common USB scancodes to PB-1000 keys.

        Args:
            scancode: int - USB HID scancode
            pressed: bool - True=press, False=release
        """
        # USB HID scancode mapping
        usb_map = {
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
            0x2C: ' ',  # Space
            0x37: '.', 0x57: '+', 0x56: '-',
            0x55: '*', 0x54: '/',
            0x2E: '=',
        }
        # Special keys
        special_usb_map = {
            0x28: self.KEY_EXE,    # Enter
            0x29: self.KEY_BREAK,  # Escape
            0x2A: self.KEY_BS,     # Backspace
            0x4F: self.KEY_RIGHT,  # Right Arrow
            0x50: self.KEY_LEFT,   # Left Arrow
            0x51: self.KEY_DOWN,   # Down Arrow
            0x52: self.KEY_UP,     # Up Arrow
            0x49: self.KEY_INS,    # Insert
            0x4C: self.KEY_DEL,    # Delete
            0x45: self.KEY_NEWALL, # NEWALL(=F12)
            0x3A: self.KEY_MAP['tk13'], # TK13(=F1)
            0x3B: self.KEY_MAP['tk14'], # TK14(=F2)
            0x3C: self.KEY_MAP['tk15'], # TK15(=F3)
            0x3D: self.KEY_MAP['tk16'], # TK16(=F4)
            0x3E: self.KEY_MENU,   # MENU(=F5)
            0x3F: self.KEY_LCKEY,  # LCKEY(=F6)
            0x40: self.KEY_CAL,    # CAL(=F7)
            0xE1: self.KEY_SHIFT,  # Left Shift
            0xE5: self.KEY_SHIFT,  # Right Shift
        }

        if scancode in usb_map:
            key = usb_map[scancode]
            if pressed:
                self.key_press(key)
            else:
                self.key_release(key)
        elif scancode in special_usb_map:
            pos = special_usb_map[scancode]
            if pressed:
                self.key_press(pos)
            else:
                self.key_release(pos)

    def poll_usb_host(self):
        """
        Poll events from the native usb_host C module (if available)
        and process them. This should be called in the main loop.
        """
        try:
            import usb_host
            # Ensure TinyUSB background task is run
            usb_host.task()
            
            # Fetch pending keyboard events
            events = usb_host.get_keyboard_events()
            for scancode, pressed in events:
                self.process_usb_key(scancode, pressed)
            return events
        except ImportError:
            # usb_host module not built into this firmware
            return []

    TRACE_SCAN = True
