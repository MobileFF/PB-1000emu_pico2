# Changelog

This file tracks the main user- and developer-facing changes, separately from the `git` commit
log. Dates are approximate (the day the change was made). Since this repo is pushed to GitHub
directly with every change, there's no separate "release" gate — entries are grouped under a
date heading instead of a version number or "Unreleased" marker.

---

## 2026-08-13

### Added

- **Full execution-state resume for RAM Load** (headline feature): previously, the EMULATOR
  MENU's **RAM Load** only restored RAM contents — the CPU was always force-reset (PC=0x0000),
  so a program could never resume mid-execution. **RAM Save now also captures PC, flags, every
  register, the UA bank, and IRQ state; RAM Load fully restores all of it, resuming execution
  exactly where RAM Save was taken.** No reset happens.
  - The state auto-loaded from the profile directory at boot (for unattended-boot safety) still
    restores RAM only and resets to PC=0x0000, as before. The full-state resume only happens
    when RAM Load is explicitly chosen from the EMULATOR MENU.
  - **Known limitation**: peripheral-side state driven by the running program (e.g. an
    in-progress virtual-FDD transfer) is not captured, so a save taken mid-transfer can resume
    into a mismatched state.
- Added a **CPU Status** screen to the EMULATOR MENU (register dump: PC, flags, UA, IA/IB/IE,
  IX-KY, $0-$31; a raw byte dump around PC; and a disassembly view — read-only).
- `_ext_load_modules()` now also recognizes `.mpy` files (precompiled via `mpy-cross`), not just
  `.py`, for auto-loading. When both exist for the same module name, `.py` still wins, as before.

### Documentation

- `doc/emulator_menu_guide.md` / `_en.md`: corrected the description of RAM Load (it no longer
  resets — execution resumes from the saved point); documented the newly-added CPU Status item.
  Also documented **LCD Height** (32-dot/64-dot toggle, Display sub-menu) — this feature
  already existed from an earlier session but was missing from the docs; this is a
  documentation fix, not a feature added in this session.
- `doc/usage_guide.md` / `_en.md`: updated the RAM Load / auto-load descriptions to match the
  current implementation.
- `doc/extension_api.md` / `_en.md`: documented `.mpy` support in the ext loader.
- `doc/dev_guide.md` / `_en.md`: added a note on the subroutine hook (call_hook) mechanism —
  its stack side effects differ depending on how a hooked address is reached (direct CAL / direct
  JP or JR / any other generic path), and it always intercepts regardless of the Python
  function's return value.
- `doc/build_guide.md` / `_en.md`: documented `.mpy` handling for initial setup and firmware
  updates, which was previously missing. Key points:
  1. If a `.py` and `.mpy` with the same name both exist on the device, `.py` always wins —
     so uploading all of `mp/` as-is silently leaves the `.mpy` versions
     (`debug.py`/`keymap.py`/`pb1000.py`/`main_runtime.py`/`emulator_menu.py`/
     `emulator_menu_ext.py`) unused. Delete the on-device `.py` to actually use the `.mpy`
     — **the order must be "copy all `.py` → explicitly `rm` just those files →
     copy `.mpy`"**; copying two separate folders (e.g. `mp/` then an `mpy/` overlay) does
     NOT substitute for deleting the `.py` and does not work (corrects an earlier flawed
     proposal).
  2. A `.mpy` only works on firmware built from the exact same MicroPython version as the
     `mpy-cross` that compiled it. A version mismatch fails only that one module's `import`
     at runtime (easy to miss, since build and flash both succeed).
  3. Rebuilding the firmware against a different MicroPython version requires recompiling and
     re-uploading the `.mpy` files with a matching `mpy-cross`.
  4. `main.py`/`boot.py` cannot be shipped as `.mpy` — the RP2 port's boot sequence checks for
     them by literal filename, not module-name import resolution, so `main.mpy` alone would
     never run at boot.
  Also added the corresponding `ImportError` symptom to Troubleshooting.

---

## Format Notes

Add a new date heading at the top of this file each time there's a meaningful batch of changes.
Record only what a user can actually perceive as a difference from the previous release
(the version actually distributed/announced) — not a commit-by-commit diff.

- **Do record**: functional, behavioral, or documentation changes visible from the previous
  release (new features, bugs that were actually fixed, corrected documentation errors, etc.).
- **Don't record**: a bug introduced and fixed within the same development session / same
  unreleased period, or internal-only implementation churn that was never part of a release
  (e.g. investigation-only debug logging that was added and later removed). No user ever
  experienced these, so they carry no meaning as a "diff from the previous release." If you
  want a record of that work for its own sake, put it in commit messages instead.
