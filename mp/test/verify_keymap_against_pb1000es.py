import re
import sys
from pathlib import Path


def parse_keytab_positions(keyboard_pas_text: str):
    block_pattern = re.compile(r"\( \{ KO[^\n]*\}[\s\S]*?\),", re.MULTILINE)
    value_pattern = re.compile(r"\$([0-9A-Fa-f]{4})")
    header_pattern = re.compile(r"\{ KO(?: code)?\s*(\d+)")

    blocks = block_pattern.findall(keyboard_pas_text)
    ko_keycode_values = {}

    for block in blocks:
        hm = header_pattern.search(block)
        if not hm:
            continue
        ko = int(hm.group(1))
        vals = [int(m.group(1), 16) for m in value_pattern.finditer(block)]
        ko_keycode_values[ko] = vals

    keycode_positions = {}
    for ko, vals in ko_keycode_values.items():
        if ko > 12:
            continue
        for keycode, value in enumerate(vals):
            if value == 0:
                continue
            positions = keycode_positions.setdefault(keycode, set())
            for bit in range(16):
                if value & (1 << bit):
                    if 0 <= bit <= 7:
                        col = bit
                    elif 12 <= bit <= 15:
                        col = 8 + (bit - 12)
                    else:
                        continue
                    positions.add((ko, col))

    return keycode_positions


def parse_char_to_keycode(main_pas_text: str):
    m_first = re.search(r"FIRST\s*=\s*(\d+)", main_pas_text)
    m_letters = re.search(r"Letters:\s*string\[COUNT\]\s*=\s*'([^']*)'", main_pas_text)
    if not (m_first and m_letters):
        raise RuntimeError("Could not parse FIRST / Letters from main.pas")

    first = int(m_first.group(1))
    letters = m_letters.group(1)

    char_to_keycode = {}
    for i, ch in enumerate(letters):
        if ch not in char_to_keycode:
            char_to_keycode[ch] = first + i
    return char_to_keycode


def compare(workspace_root: Path):
    mp_dir = workspace_root / "mp"
    pb_dir = workspace_root / "pb1000es"

    keyboard_pas = (pb_dir / "keyboard.pas").read_text(encoding="utf-8", errors="ignore")
    main_pas = (pb_dir / "main.pas").read_text(encoding="utf-8", errors="ignore")

    sys.path.insert(0, str(mp_dir))
    import keyboard as kb_mod  # noqa: E402

    keytab_positions = parse_keytab_positions(keyboard_pas)
    char_to_keycode = parse_char_to_keycode(main_pas)

    mismatches = []
    checked = 0

    for ch, actual_pos in sorted(kb_mod.KeyboardMatrix.KEY_MAP.items()):
        lookup = ch.upper() if "a" <= ch <= "z" else ch
        if lookup not in char_to_keycode:
            mismatches.append((f"KEY_MAP['{ch}']", actual_pos, "no keycode in main.pas"))
            continue

        keycode = char_to_keycode[lookup]
        expected = keytab_positions.get(keycode, set())
        checked += 1

        if not expected:
            mismatches.append((f"KEY_MAP['{ch}']", actual_pos, f"keycode={keycode} has no KO mapping"))
            continue

        if actual_pos not in expected:
            mismatches.append((f"KEY_MAP['{ch}']", actual_pos, f"expected one of {sorted(expected)} (keycode={keycode})"))

    special_keycodes = {
        "KEY_EXE": 69,
        "KEY_MODE": 70,
        "KEY_SHIFT": 43,
        "KEY_BREAK": 46,
        "KEY_ANS": 47,
        "KEY_BS": 48,
        "KEY_INS": 49,
        "KEY_DEL": 50,
        "KEY_LEFT": 78,
        "KEY_RIGHT": 79,
        "KEY_UP": 80,
        "KEY_DOWN": 81,
    }

    for name, keycode in special_keycodes.items():
        actual_pos = getattr(kb_mod.KeyboardMatrix, name)
        expected = keytab_positions.get(keycode, set())
        checked += 1

        if not expected:
            mismatches.append((name, actual_pos, f"keycode={keycode} has no KO mapping"))
            continue

        if actual_pos not in expected:
            mismatches.append((name, actual_pos, f"expected one of {sorted(expected)} (keycode={keycode})"))

    print(f"Checked entries: {checked}")
    if not mismatches:
        print("Result: OK (all compared mappings match pb1000es KeyTab)")
        return 0

    print(f"Result: NG ({len(mismatches)} mismatches)")
    for name, actual, detail in mismatches:
        print(f"- {name}: actual={actual} / {detail}")
    return 1


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(compare(root))
