"""
Standalone test script for mp/keyboard.py.

Run:
    python mp/test_keyboard_matrix.py
"""

from keyboard import KeyboardMatrix


def _assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: actual=0x{actual:04X}, expected=0x{expected:04X}")


def _expected_ky_mask_from_ki(ki):
    if 1 <= ki <= 8:
        return 1 << (8 - ki)
    if 9 <= ki <= 12:
        return 1 << (24 - ki)
    raise ValueError(f"invalid KI number: {ki}")


def test_single_key_low_bit():
    kb = KeyboardMatrix(debug=False)
    kb.key_press((0, 6))  # row0, KI6 -> KY bit2
    kb.kb_write(0x00)     # IA=0 -> row0
    _assert_eq(kb.kb_read(), 0x0004, "row0 KI6 should set KY bit2")


def test_single_key_high_bit():
    kb = KeyboardMatrix(debug=False)
    kb.key_press((0, 9))  # row0, KI9 -> KY bit15
    kb.kb_write(0x00)
    _assert_eq(kb.kb_read(), 0x8000, "row0 KI9 should set KY bit15")


def test_row_select_filters():
    kb = KeyboardMatrix(debug=False)
    kb.key_press((0, 6))  # bit2
    kb.key_press((1, 7))  # bit1

    kb.kb_write(0x00)  # row0
    _assert_eq(kb.kb_read(), 0x0004, "IA=0 should read only row0")

    kb.kb_write(0x01)  # row1
    _assert_eq(kb.kb_read(), 0x0002, "IA=1 should read only row1")


def test_all_rows_select():
    kb = KeyboardMatrix(debug=False)
    kb.key_press((0, 6))  # bit1
    kb.key_press((1, 7))  # bit2
    kb.kb_write(0x0D)     # all rows
    _assert_eq(kb.kb_read(), 0x0006, "IA=0x0D should OR all rows")


def test_keymap_six_and_plus():
    kb = KeyboardMatrix(debug=False)

    row, ki = KeyboardMatrix.KEY_MAP["6"]
    kb.key_press("6")
    kb.kb_write(row)
    _assert_eq(kb.kb_read(), _expected_ky_mask_from_ki(ki), "'6' should match KEY_MAP position")
    kb.key_release("6")

    row, ki = KeyboardMatrix.KEY_MAP["+"]
    kb.key_press("+")
    kb.kb_write(row)
    _assert_eq(kb.kb_read(), _expected_ky_mask_from_ki(ki), "'+' should match KEY_MAP position")


def test_release_all():
    kb = KeyboardMatrix(debug=False)
    kb.key_press((0, 6))
    kb.key_press((1, 7))
    kb.release_all()
    kb.kb_write(0x0D)
    _assert_eq(kb.kb_read(), 0x0000, "release_all should clear matrix")


def main():
    tests = [
        test_single_key_low_bit,
        test_single_key_high_bit,
        test_row_select_filters,
        test_all_rows_select,
        test_keymap_six_and_plus,
        test_release_all,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("All keyboard matrix tests passed.")


if __name__ == "__main__":
    main()
