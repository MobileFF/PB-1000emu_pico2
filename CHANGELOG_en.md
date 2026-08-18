# Changelog

This file tracks the main user- and developer-facing changes, separately from the `git` commit
log. Dates are approximate (the day the change was made). Since this repo is pushed to GitHub
directly with every change, there's no separate "release" gate — entries are grouped under a
date heading instead of a version number or "Unreleased" marker.

---

## 2026-08-17

### Added

- **HDMI mirror output** (headline feature): with a second Raspberry Pi Pico 2 plus an HDMI
  output addon (e.g. PICO-HDMI-PLUS), the main unit's LCD contents can now be mirrored to an
  HDMI monitor in real time. Opt-in by design — has zero effect on anyone without the addon
  (new `[hdmi]` section in `pb1000.ini`, default `enable=false`).
  - Wiring only adds one new CS pin (GP28 recommended — the only spare GPIO for external SPI
    devices) onto the existing SPI1 bus (already shared with LCD/SD, GP10=SCK/GP11=MOSI).
    `baudrate`/`frame_skip` are also tunable from `pb1000.ini`.
  - Can be toggled on/off (and saved) from the EMULATOR MENU (GUI+F7 > Display > HDMI).
  - Beyond the game screen (LCD VRAM / the Color VRAM extension), the boot-time RAM profile
    picker, the EMULATOR MENU, and the bezel (frame decoration) are each mirrored to HDMI as
    independent layers. Each layer manages its own window centering and its own "clear the
    previously drawn area" tracking entirely independently on the receiver side, so multiple
    layers with different canvas sizes can coexist without drifting out of alignment or
    accidentally erasing each other. Text/rectangle-heavy screens (menus, etc.) are sent as a
    compact stream of "fill rect" / "draw text" commands rather than raw pixels, keeping the
    sender's processing load and transfer volume down.
  - The receiver firmware (runs on the second Pico 2, generates the HDMI/DVI signal via HSTX)
    is generic enough to be shared with the sibling MSX_emu_pico2 project, so it's published as
    an independent project,
    [`hdmi_bridge_receiver`](https://github.com/MobileFF/hdmi_bridge_receiver) (MIT license).
    See that project's documentation (Japanese/English) for the protocol spec, wiring, and
    build instructions (also linked from this project's `hdmi_bridge/README.md`).

### Fixed

- `mp/sdcard.py` / `mp/xpt2046.py` (touch panel): fixed a bug where the
  `self.spi.init(baudrate=...)` call made right before SD card / touch panel access only
  reset the baud rate — the SPI communication mode (CPOL/CPHA) was left however it had last
  been set. Root cause: a documented-nowhere MicroPython behavior discovered in the sibling
  MSX_emu_pico2 project's `mp/sdcard.py` — `spi_set_format()` is only called when `polarity`/
  `phase` are passed explicitly (see `references/sdcard_spi_mode_bug.md` for the full writeup).
  With the new HDMI mirror output (mode 3) enabled, accessing the SD card or touch panel could
  hit read timeouts, corrupted data, or garbled touch coordinates due to the mode mismatch.
  Fixed by explicitly adding `polarity=0, phase=0` at every call site (6 in `sdcard.py`, 2 in
  `xpt2046.py`). Since the HDMI mirror output feature is itself new in this batch of work and
  has never shipped in a released build, this code path was never actually reachable in any
  previous release — so it's recorded here as part of the HDMI feature rather than as a
  standalone bug fix.

### Documentation

- `doc/hardware_guide_en.md` / JA: added a new section 9 covering HDMI mirror output wiring
  (main unit Pico 2 W ↔ receiver Pico 2, the new GP28 CS pin) and a link to the receiver
  project.
- `doc/config_guide_en.md` / JA: added a new section 14 documenting every `[hdmi]` key
  (`enable`/`cs_pin`/`baudrate`/`frame_skip`).
- `doc/usage_guide_en.md` / JA: added a new section 11 covering how to enable it and the
  LCD/HDMI exclusivity behavior (while HDMI is enabled, the game screen, bezel, RAM profile
  picker, and EMULATOR MENU are all shown exclusively on HDMI, never on the physical LCD).
- `doc/emulator_menu_guide_en.md` / JA: added an **HDMI** row to the Display sub-menu table.
  Also fixed the menu-layout diagram's stale Display description ("change on-screen colors" —
  it was never updated when LCD Height was added in an earlier session) to
  "colors, resolution, and HDMI output" (a pre-existing documentation gap, not something
  introduced by this session's feature work).
- `mp/pb1000.ini`: updated the `[hdmi]` section's comment to reference the new section 9 in
  `hardware_guide.md`.

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
