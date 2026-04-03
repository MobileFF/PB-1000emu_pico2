"""
PB-1000 Keyboard Matrix Emulation
Maps USB keyboard input to PB-1000's keyboard matrix.
The PB-1000 has a matrix keyboard scanned via IA (row select)
and read via KY (column data).
"""
import time
try:
    import keymap
except ImportError:
    # Fallback/Mock for testing if keymap.py is missing
    class keymap:
        KEY_MAP = {}
        ADV_MAP = {}
        USB_TO_CHAR = {}
        SPECIAL_MAP = {}
        KEY_SHIFT = (11, 2)

class KeyboardMatrix:
    """
    PB-1000 keyboard matrix emulation.
    """
    TRACE_MIN_PRINT_MS = 120
    TRACE_SCAN = True

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
        self.usb_shift_physical = False
        self.usb_alt_physical = False
        self._active_usb_scancodes = {}
        
        # Synchronize to C if possible
        self.sync_to_c()

    def sync_to_c(self):
        """Synchronize Python keyboard mapping to the C core."""
        try:
            import hd61700
            # Convert ADV_MAP: [(scancode, mod, [(row, ki), ...]), ...]
            adv_list = []
            for (scancode, mod), coords in keymap.ADV_MAP.items():
                adv_list.append((scancode, mod, coords))
            hd61700.keyboard_config_adv(adv_list)

            # Convert Base/Special maps
            base_list = []
            # 1. SPECIAL_MAP
            for scancode, coord in keymap.SPECIAL_MAP.items():
                base_list.append((scancode, coord[0], coord[1]))
            # 2. USB_TO_CHAR -> KEY_MAP
            for scancode, char in keymap.USB_TO_CHAR.items():
                if char in keymap.KEY_MAP:
                    coord = keymap.KEY_MAP[char]
                    base_list.append((scancode, coord[0], coord[1]))
            hd61700.keyboard_config_base(base_list)
            
            if self.debug:
                print("KB: Synchronized mapping to C core.")
        except (ImportError, AttributeError):
            pass

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
            if key_lower in keymap.KEY_MAP:
                row, ki = keymap.KEY_MAP[key_lower]
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
            if key_lower in keymap.KEY_MAP:
                row, ki = keymap.KEY_MAP[key_lower]
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
        Process a USB HID keyboard scancode with flexible mapping.
        """
        # 1. Update physical modifier state
        if scancode in (0xE1, 0xE5):  # Left Shift, Right Shift
            self.usb_shift_physical = pressed
        if scancode in (0xE2, 0xE6):  # Left Alt, Right Alt
            self.usb_alt_physical = pressed

        if not pressed:
            # Release exactly the keys that were pressed for this scancode
            if scancode in self._active_usb_scancodes:
                keys_to_release = self._active_usb_scancodes.pop(scancode)
                for key_coord in keys_to_release:
                    self.key_release(key_coord)
            return

        # 2. Check if already pressed (avoid duplicates)
        if scancode in self._active_usb_scancodes:
            return

        # 3. Handle modifiers -> Logical combinations
        current_mod = 0
        if self.usb_shift_physical: current_mod |= 1
        if self.usb_alt_physical:   current_mod |= 2

        coords_to_press = []

        # A. Advanced Map (Scancode, Mod) -> PB-1000 Coordinates
        if (scancode, current_mod) in keymap.ADV_MAP:
            coords_to_press.extend(keymap.ADV_MAP[(scancode, current_mod)])
        else:
            # B. Base USB HID scancode mapping
            if scancode in keymap.SPECIAL_MAP:
                coords_to_press.append(keymap.SPECIAL_MAP[scancode])
            elif scancode in keymap.USB_TO_CHAR:
                char = keymap.USB_TO_CHAR[scancode]
                if char in keymap.KEY_MAP:
                    coords_to_press.append(keymap.KEY_MAP[char])
                    # No automatic Shift for alphabets in base map

        if coords_to_press:
            self._active_usb_scancodes[scancode] = coords_to_press
            for coord in coords_to_press:
                self.key_press(coord)

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
