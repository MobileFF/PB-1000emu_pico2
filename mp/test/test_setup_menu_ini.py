"""setup_menu.py ini read/write logic -- functional test.

Covers only the pure file I/O helper (`_apply_ini_changes`) and the
KEY_SCHEMA table sanity; the interactive UI (screens, input widgets) is not
exercised here since it needs real hd61700/display hardware. Runs on the
host with plain CPython (setup_menu.py has no top-level `machine` import --
see the F1 dispatch in boot_session.py for the one place that needs it).

Run:
    python3 test_setup_menu_ini.py
"""
import sys
import os
import tempfile
import shutil

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MP_DIR   = os.path.dirname(_THIS_DIR)
if _MP_DIR not in sys.path:
    sys.path.insert(0, _MP_DIR)

import setup_menu
from config import load_ini


class T:
    def eq(self, a, b, msg=""):
        assert a == b, "%r != %r %s" % (a, b, msg)

    def true(self, v, msg=""):
        assert v, "expected True: %s" % msg

    def in_(self, needle, haystack, msg=""):
        assert needle in haystack, "%r not in %r %s" % (needle, haystack, msg)

    def not_in(self, needle, haystack, msg=""):
        assert needle not in haystack, "%r unexpectedly in %r %s" % (needle, haystack, msg)


t = T()
_TMPDIR = None


def _setup():
    global _TMPDIR
    _TMPDIR = tempfile.mkdtemp(prefix="setup_menu_test_")


def _teardown():
    if _TMPDIR:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def test_set_new_key_in_existing_section():
    path = os.path.join(_TMPDIR, "a.ini")
    _write(path, "[display]\n; comment\ndriver = ILI9341\n\n[wifi]\nssid = old\n")
    setup_menu._apply_ini_changes(path, {"display": {"lcd_height": "64"}})
    cfg = load_ini(path)
    t.eq(cfg["display"]["driver"], "ILI9341")
    t.eq(cfg["display"]["lcd_height"], "64")
    t.eq(cfg["wifi"]["ssid"], "old")
    with open(path) as f:
        raw = f.read()
    t.in_("; comment", raw, "comment preserved")


def test_overwrite_existing_key_preserves_others():
    path = os.path.join(_TMPDIR, "b.ini")
    _write(path, "[beep]\nenable = true\nfreq_hz = 1000\nduty = 50\n")
    setup_menu._apply_ini_changes(path, {"beep": {"freq_hz": "4000"}})
    cfg = load_ini(path)
    t.eq(cfg["beep"]["freq_hz"], "4000")
    t.eq(cfg["beep"]["enable"], "true")
    t.eq(cfg["beep"]["duty"], "50")


def test_delete_key_removes_line():
    path = os.path.join(_TMPDIR, "c.ini")
    _write(path, "[disk]\nenabled = true\npath = /sd/disks/disk1.img\nreadonly = false\n")
    setup_menu._apply_ini_changes(path, {"disk": {"path": None}})
    cfg = load_ini(path)
    t.not_in("path", cfg.get("disk", {}))
    t.eq(cfg["disk"]["enabled"], "true")


def test_create_new_section_when_missing():
    path = os.path.join(_TMPDIR, "d.ini")
    _write(path, "[display]\ndriver = ST7796\n")
    setup_menu._apply_ini_changes(path, {"hdmi": {"enable": "true"}})
    cfg = load_ini(path)
    t.eq(cfg["hdmi"]["enable"], "true")
    t.eq(cfg["display"]["driver"], "ST7796")


def test_multi_section_change_in_one_call():
    # Mirrors real usage: one setup-menu session can edit keys across
    # several sections before "Save & Exit" applies them all at once.
    path = os.path.join(_TMPDIR, "e.ini")
    _write(path, "[beep]\nenable = true\n\n[wifi]\nssid = old\n")
    setup_menu._apply_ini_changes(path, {
        "beep": {"freq_hz": "4000"},
        "wifi": {"ssid": "new", "password": None},
        "hdmi": {"enable": "true"},
    })
    cfg = load_ini(path)
    t.eq(cfg["beep"]["enable"], "true")
    t.eq(cfg["beep"]["freq_hz"], "4000")
    t.eq(cfg["wifi"]["ssid"], "new")
    t.not_in("password", cfg.get("wifi", {}))
    t.eq(cfg["hdmi"]["enable"], "true")


def test_missing_file_creates_it():
    path = os.path.join(_TMPDIR, "does_not_exist.ini")
    setup_menu._apply_ini_changes(path, {"wifi": {"ssid": "myssid"}})
    cfg = load_ini(path)
    t.eq(cfg["wifi"]["ssid"], "myssid")


def test_schema_has_no_duplicate_keys():
    seen = set()
    for section, key, kind, extra, curated, flash_only in setup_menu.SCHEMA:
        k = (section, key)
        t.true(k not in seen, "duplicate schema entry %r" % (k,))
        seen.add(k)
        t.in_(kind, ("bool", "enum", "int", "str"))


def test_list_targets_flash_always_present():
    targets = setup_menu._list_targets(sd_mounted=False, profiles=[])
    kinds = [k for _l, _p, k in targets]
    t.eq(kinds, ["flash"])

    targets = setup_menu._list_targets(sd_mounted=True, profiles=["default", "game1"])
    kinds = [k for _l, _p, k in targets]
    t.eq(kinds, ["flash", "sd", "profile", "profile"])
    paths = [p for _l, p, _k in targets]
    t.in_("/sd/rams/default/pb1000.ini", paths)
    t.in_("/sd/rams/game1/pb1000.ini", paths)


# ------------------------------------------------------------------
_TESTS = [
    test_set_new_key_in_existing_section,
    test_overwrite_existing_key_preserves_others,
    test_delete_key_removes_line,
    test_create_new_section_when_missing,
    test_multi_section_change_in_one_call,
    test_missing_file_creates_it,
    test_schema_has_no_duplicate_keys,
    test_list_targets_flash_always_present,
]

if __name__ == "__main__":
    _setup()
    passed = failed = 0
    try:
        for fn in _TESTS:
            print("RUN %-45s" % fn.__name__, end=" ")
            try:
                fn()
                print("PASS")
                passed += 1
            except Exception as e:
                import traceback
                print("FAIL")
                traceback.print_exc()
                failed += 1
    finally:
        _teardown()
    print("-" * 55)
    print("Tests: %d  Passed: %d  Failed: %d" % (passed + failed, passed, failed))
    sys.exit(0 if failed == 0 else 1)
