# Keymap Guide (USB Keyboard → PB-1000 Key Assignments)

This guide lists the emulator's default **USB keyboard → real PB-1000 key** assignments
(the default keymap). The implementation lives in `mp/keymap.py` (built-in fallback
defaults) and `mp/keymap.json` (the same content expressed as JSON so it can be
overridden from an SD card, etc.).

For emulator-wide operations (Save State, screenshots, the emulator menu, and other
system-function keys that have no equivalent on the real PB-1000), see `usage_guide_en.md`
§3. This guide covers **only assignments that correspond to a real PB-1000 key**.

---

## 1. How it works

1. The USB keyboard reports an HID scancode (e.g. `0x04`) and the current modifier state
   (Shift/Alt/Ctrl/Win).
2. If a modifier is held, the emulator first looks up the (scancode, mod) pair in `ADV_MAP`
   for an additional mapping.
3. If nothing is found there, it falls back to `USB_MAP`, keyed by scancode alone.
4. The result is one PB-1000 keyboard-matrix **(row, ki)** coordinate (13 rows × 12
   columns), or a list of coordinates (e.g. for a simulated Shift + another key), which
   feeds into the CPU-side key scan.

Meaning of the `mod` bits (from the `get_label()` docstring):

| Bit value | Modifier |
| :--- | :--- |
| 1 | Shift |
| 2 | Alt |
| 4 | Ctrl |
| 8 | Win (GUI) |

### Customizing the keymap

At boot, the emulator searches for a JSON file in the following order and uses it if
found (otherwise it falls back to the built-in defaults in `mp/keymap.py`):

```
/sd/roms/keymap.json > /sd/keymap.json > /roms/keymap.json > /keymap.json
```

---

## 2. Direct mapping (USB_MAP)

These are USB scancodes that map directly to a PB-1000 key with no modifier held.

### Letters and digits

USB `A`–`Z` and `0`–`9` map directly to the same PB-1000 character keys (scancodes
`0x04`–`0x1D` for `A`–`Z`, `0x1E`–`0x27` for `1`–`0`).

### Symbols and other base keys

| USB key (scancode) | PB-1000 key |
| :--- | :--- |
| Space (`0x2C`) | SPACE |
| `-` (`0x2D`) | `-` |
| `=` position / JIS `^` (`0x2E`) | `^` |
| `;` (`0x33`) | `;` |
| `'` position / JIS `:` (`0x34`) | `:` |
| `,` (`0x36`) | `,` |
| `.` (`0x37`) | `.` |
| `/` (`0x38`) | `/` |
| Grave position / JIS Kana toggle (`0x35`) | KANA |
| Caps Lock (`0x39`) | CAPS |
| International4 / JIS Henkan key (`0x8A`) | ENG |

> Some assignments assume the physical key positions of a JIS keyboard (e.g. `0x2E` →
> `^`, `0x34` → `:`), so the legend printed on a US-layout keyboard may not match.

### Editing and cursor keys

| USB key | PB-1000 key |
| :--- | :--- |
| Enter (`0x28`) | EXE |
| Esc (`0x29`) | BREAK |
| Backspace (`0x2A`) | BS |
| Insert (`0x49`) | INS |
| ↑ / ↓ / ← / → (`0x52`/`0x51`/`0x50`/`0x4F`) | Cursor up/down/left/right (repeats on long press) |
| Pause (`0x48`) | CLS |
| End (`0x4D`) | STOP |

### Function keys (PB-1000-specific assignments)

| USB key | PB-1000 key |
| :--- | :--- |
| F1 (`0x3A`) | F1 (same as touch key T13) |
| F2 (`0x3B`) | F2 (same as T14) |
| F3 (`0x3C`) | F3 (same as T15) |
| F4 (`0x3D`) | F4 (same as T16) |
| F5 (`0x3E`) | LC KEY |
| F6 (`0x3F`) | MENU |
| F7 (`0x40`) | CAL |
| F8 (`0x41`) | MEMO IN |
| F9 (`0x42`) | MEMO |
| F10 (`0x43`) | IN |
| F11 (`0x44`) | OUT |
| F12 (`0x45`) | CALC (with Win held, see NEW ALL below instead) |

### Other

| USB key | PB-1000 key |
| :--- | :--- |
| Application / Menu key (`0x65`) | ANS |
| International3 / JIS ¥ key (`0x89`) | ENG (unmodified; see the Shift table below for the Shift-held case) |

---

## 3. Modifier keys pressed alone

Pressing either the left or right Alt/Ctrl key by itself is treated as pressing the
corresponding PB-1000 key.

| USB key | PB-1000 key |
| :--- | :--- |
| Alt (left) / Alt (right) | SHIFT |
| Ctrl (left) / Ctrl (right) | Func |

---

## 4. Shift combinations (symbol entry, ADV_MAP)

Pressing `Shift+◯◯` on the USB keyboard is translated into a different PB-1000 key (or
the PB-1000 SHIFT key held together with another key), producing a character close to
what a PC user would expect.

| USB key (with Shift) | PB-1000-side input | Resulting character |
| :--- | :--- | :--- |
| `1` (`0x1E`) | SHIFT + `"` key | `!` |
| `2` (`0x1F`) | `"` key | `"` |
| `3` (`0x20`) | SHIFT + `$` key | `#` |
| `4` (`0x21`) | `$` key | `$` |
| `5` (`0x22`) | SHIFT + `&` key | `%` |
| `6` (`0x23`) | `&` key | `&` |
| `7` (`0x24`) | SHIFT + `=` key | `'` |
| `8` (`0x25`) | `[`-equivalent key | `(` |
| `9` (`0x26`) | `]`-equivalent key | `)` |
| `-` (`0x2D`) | `=` key | `=` |
| `;` (`0x33`) | `+` key | `+` |
| `'` (`0x34`) | `*` key | `*` |
| `/` (`0x38`) | SHIFT + `,` key | `?` |
| `,` (`0x36`) | SHIFT + `;` key | `<` |
| `.` (`0x37`) | SHIFT + `:` key | `>` |
| Esc (`0x29`) | CLS key | CLS |
| `]` (`0x30`) | SHIFT + `*` key | `{` |
| Non-US # (`0x32`) | SHIFT + `/` key | `}` |
| International1 (`0x87`) | SHIFT + `8` key | `_` |
| `[` (`0x2F`) | SHIFT + `9` key | `` ` `` |
| International3 / ¥ (`0x89`) | SHIFT + `4` key | `\|` |
| `=` / JIS `^` (`0x2E`) | SHIFT + `5` key | `~` |

---

## 5. Additional standalone key assignments (ADV_MAP, no-modifier case)

The following are scancodes not present in `USB_MAP`, defined individually in `ADV_MAP`
instead (applied as that key's own dedicated conversion even without Shift held).

| USB key | PB-1000-side input | Resulting character / function |
| :--- | :--- | :--- |
| Delete (`0x4C`) | SHIFT + INS | DEL |
| Home (`0x4A`) | SHIFT + CLS | HOME |
| `[` (`0x2F`) | SHIFT + `7` key | `@` |
| `]` (`0x30`) | SHIFT + `[`-equivalent key | `[` |
| Non-US # (`0x32`) | SHIFT + `]`-equivalent key | `]` |
| International1 (`0x87`) | SHIFT + `6` key | `\` (backslash equivalent) |
| International3 / ¥ (`0x89`) | SHIFT + `^` key | `¥` (Yen sign) |
| Scroll Lock (`0x47`) | CONTR. key | CONTR. |

---

## 6. NEW ALL key

The PB-1000's **NEW ALL** key (clear-all) is not assigned to any single USB key on its
own; instead it is assigned to **Win (GUI) + F12**.

| USB key | PB-1000 matrix coordinate | PB-1000 key |
| :--- | :--- | :--- |
| Win (GUI) + F12 (`0x45`, mod=8) | (row 6, ki 6) | NEW ALL |

Pressing F12 (`0x45`) alone, without Win held, is treated as the normal **CALC** key (see
§2).

---

## 7. Correspondence with touch keys (T1–T16)

Independently of any USB key, `KEY_MAP` in `mp/keymap.py` also defines the mapping
between the PB-1000's 16-key touch panel (T1–T16) and matrix coordinates.

| Touch key | Coordinate (row, ki) | Touch key | Coordinate (row, ki) |
| :--- | :--- | :--- | :--- |
| T1 | (7, 12) | T9  | (7, 10) |
| T2 | (8, 12) | T10 | (8, 10) |
| T3 | (9, 12) | T11 | (9, 10) |
| T4 | (10, 12) | T12 | (10, 10) |
| T5 | (7, 11) | T13 | (7, 9) |
| T6 | (8, 11) | T14 | (8, 9) |
| T7 | (9, 11) | T15 | (9, 9) |
| T8 | (10, 11) | T16 | (10, 9) |

T13–T16 share the same coordinates as F1–F4 in §2, so on the USB keyboard, F1–F4 and
touch keys T13–T16 effectively refer to the same PB-1000 key.

---

## References

- Implementation: `mp/keymap.py` (built-in defaults), `mp/keymap.json` (JSON representation of the same, overridable from SD card, etc.)
- System-function keys (Save State, screenshots, emulator menu, etc.): `usage_guide_en.md` §3
- Keymap verification script: `mp/test/verify_keymap_against_pb1000es.py` (cross-checks against the `KeyTab` of the original `pb1000es` emulator)
