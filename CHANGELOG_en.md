# Changelog

This file tracks the main user- and developer-facing changes, separately from the `git` commit
log. Dates are approximate (the day the change was made). Since this repo is pushed to GitHub
directly with every change, there's no separate "release" gate — entries are grouped under a
date heading instead of a version number or "Unreleased" marker.

---

## 2026-08-25

### Fixed

- **SD card / touch controller SPI bus arbitration gap** (`mp/ili9341.py`/`mp/st7796.py`/
  `mp/display_init.py`): `src/lcd_controller.c`'s C-accelerated renderer already
  force-deasserted the SD card's (GP15) and touch controller's (GP16) CS pins before every
  transaction ("SPI Bus Arbitration"), but the Python-side driver's `write_cmd()`/
  `write_data()`/`fill_rect()` (used by the bezel, status bar, and other UI overlays) had no
  equivalent guard. Added a `_deselect_others()` helper, wired through `display_init.py` passing
  `sd_cs`/`t_cs` Pin objects into the driver constructors.
- **FuncKeyBar intermittently failing to redraw** (e.g. right after Reset) (`mp/funckey_bar.py`):
  `_blit_raw()` was interleaving file reads with LCD SPI writes while holding the LCD's CS low,
  a bus-arbitration risk when the image is loaded from the SD-card fallback path
  (`/sd/fkbar.raw`). Reading the whole ~27KB image into one buffer up front fixed that but
  introduced a new regression -- the single large contiguous allocation intermittently failed
  under heap fragmentation (e.g. right after Reset), silently skipping the redraw since the
  caller only catches `OSError`, not `MemoryError`. Final fix reuses a small 512-byte chunk
  buffer and toggles the LCD's CS off during each file read instead.

### Added

- **`[overlay] show_log` setting**: the boot-time REPL-log-mirror-to-LCD feature
  (`BootStatusOverlay`) had no on/off switch; added (default off).

---

## 2026-08-24

### Changed

- **Renamed `[pio_uart]` to `[rs232c]`** in `pb1000.ini`, to match the PB-1000 feature name
  rather than the internal implementation (the Python module/class stay named `pio_uart.py` /
  `PioUart`, since those describe the real technique). Also made `tx_pin`/`rx_pin` configurable
  via the ini, and corrected the setup menu's `baudrate` input range to the real PB-1000
  RS-232C's 300-9600bps.
- **Merged `[boot_status]`/`[clock_overlay]`/`[mem_overlay]` into `[overlay]`**: the three
  `show_profile_name`/`show_clock`/`show_mem_free` keys now live in one section
  (`BootStatusOverlay`/`ClockOverlay`/`MemOverlay` remain separate classes, since boot-time and
  runtime clocks read from different sources).
- **Removed the HDMI toggle from EMULATOR MENU**: HDMI can still be enabled/disabled via the
  boot-time setup menu (F1) or `pb1000.ini`'s `[hdmi] enable`, so the runtime menu no longer
  needs its own copy. Also reordered the System submenu so `Reboot Emulator (MCU)` is last.

### Removed

- **`new_all_debug` feature removed entirely** (the `[debug] newall_debug` setting and its
  C-side ROM/USB-scancode trace output) -- no longer needed given the current codebase.

---

## 2026-08-23

### Added

- **Dynamic memory allocation for BANK1/2/3** (`src/modhd61700.c`): the RAM bank 1-3 buffers are
  now allocated from the MicroPython GC heap on demand, only for the banks the boot-time
  profile's `ramN.bin` files actually cover — a profile using fewer banks genuinely frees more
  heap. The first attempt at this made real hardware fail to boot at all (blank LCD, no REPL);
  reverted to the static-array design to recover, then root-caused the crash to the CPU's
  per-byte memory-access path checking only the `has_bank[]` flag, not whether the buffer
  pointer itself was non-NULL. The fixed re-implementation (adds that pointer check, plus
  strictly ordering allocation before the presence flag is set) has been confirmed booting and
  running correctly across multiple real-hardware sessions.

### Fixed

- Root-caused and fixed a long list of contributors to the free-heap "sawtooth" seen in
  `[mem_overlay]` logs, via iterative real-hardware measurement: display-driver
  `set_window()`/`write_cmd()`/`fill_rect()` allocating a fresh buffer on every call, the status
  bar's per-call font-dictionary rebuild, several `PB1000System` attributes that weren't
  initialized in `__init__` (so `getattr(obj, name, default)` silently raised `AttributeError`
  every main-loop iteration), and bound methods being re-created every call in the PIO UART
  bridge and CPU execution loop. Combined, these accounted for tens of KB/sec of steady-state
  allocation; reduced to near zero.

---

## 2026-08-22

### Removed

- **Removed the UART1 serial-console keyboard input path entirely** (GP4/GP5,
  `[keyboard] enable_uart_kbd`). This is unrelated to the PIO UART-based virtual RS-232C
  (GP6/GP13), which is untouched.
- **Removed the EMULATOR MENU's RAM Load** (restoring a saved snapshot mid-session). The
  Toggles submenu was also trimmed down to Beep only (other toggles are now unified into the
  boot-time setup menu (F1) or `pb1000.ini` settings). To switch to a different profile or save,
  reboot the emulator (MCU) and pick again from the profile selection screen.

### Changed

- Split several large files by feature to fight compile-time heap fragmentation:
  - `pb1000.py` (1943 lines) → `pb1000_fdd.py` (virtual FDD) and `pb1000_state_io.py`
    (save_state/load_state and the hook registry), pulled in via multiple-inheritance mixins.
  - `emulator_menu_ext.py` (704 lines) → `emulator_menu_ram.py`, `emulator_menu_capture.py`,
    `emulator_menu_debug.py`.
  - Deferred several of `main.py`'s top-level imports so `main_input`, `main_runtime`, and
    `pb1000.py` itself aren't compiled during the profile picker or boot-time setup menu (F1).
- Rewrote `pio_uart.py`'s RX/TX buffers from list+`pop(0)` to a zero-allocation ring buffer.
- Consolidated 7 duplicate LCD text-drawing implementations into a single shared
  `mp/draw_text.py`.

---

## 2026-08-17

### Added

- **Extension (ext) module auto-loading now supports per-RAM-profile directories**
  (`_ext_load_modules()` in `mp/pb1000.py`). Previously only `/sd/ext/` and `/ext/` were
  scanned; added `<profile>/ext/` (e.g. `/sd/rams/FOREX_PB/ext/`), scanned at the *highest*
  priority. This lets a program-specific patch extension (e.g. enabling
  `forex_pb_inkey_patch.py` only when the `FOREX_PB` profile is loaded) ship without affecting
  any other profile at all. Priority when the same module name appears in more than one place:
  per-profile > `/sd/ext/` > `/ext/`.
  Also fixed how that priority gets reflected into `sys.path`: the code called
  `sys.path.insert(0, ...)` in priority order, but since each `insert(0, ...)` pushes everything
  already there further back, that actually reversed the intended priority — a latent bug only
  observable when the same module name exists in more than one location, caught while adding
  this third directory.
- Added **Reboot Emulator (MCU)** to the EMULATOR MENU's System sub-menu
  (`mp/emulator_menu.py`, with a confirmation prompt). The existing **Reset** item only resets
  the emulated PB-1000 side (equivalent to the real hardware's NumLock key) — the Pico itself
  (the MCU) never restarts. This new item is a distinctly different, more severe action: it
  calls `machine.reset()` to actually hardware-reboot the Pico. It exists as a way to recover
  when the emulator has frozen or hung badly enough that nothing else works. Since any progress
  not already written out via RAM Save is lost, it goes through a confirmation screen first
  (the same severity as NEW ALL).
- Added an **F12: Exit to REPL (don't boot)** shortcut to the boot-time profile picker
  (`mp/boot_session.py`, with a confirmation prompt). A freshly-flashed `main.py` with a bug
  that only surfaces later in boot or in the interactive loop would otherwise block REPL access
  entirely, since `main.py` auto-runs on every boot — leaving no way to recover short of a
  BOOTSEL + `flash_nuke.uf2` full wipe. (This risk is exactly why `main.py` had, until now,
  deliberately never been copied to flash — testing always went through `mpremote run` instead.)
  Pressing F12 on this screen — which runs before ROM/RAM loading or CPU startup, the safest
  point available — raises `SystemExit` to cleanly unwind out of `main()` back to the REPL. This
  makes it practical to actually deploy `main.py` to flash for testing.
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
- Made the `[hdmi]` section **flash-root `/pb1000.ini` only** (`load_config()` in
  `mp/config.py` now ignores an `[hdmi]` section in `/sd/pb1000.ini` or a per-profile ini
  entirely; the EMULATOR MENU's HDMI toggle now always saves to `/pb1000.ini`; the `[hdmi]`
  entries in `mp/setup_menu.py`'s F1 BIOS-style menu are now hidden while editing an SD/profile
  ini). During real-hardware testing, the EMULATOR MENU's HDMI toggle had saved
  `enable=true` to `/sd/pb1000.ini`; editing the flash-root `/pb1000.ini` to `enable=false`
  afterward had no effect, since `/sd/pb1000.ini` outranks it in the normal priority chain
  (`/pb1000.ini` < `/sd/pb1000.ini` < per-profile ini) — the SD-side value kept winning. Since
  this is a fixed hardware-wiring setting, not something that should vary by SD card or
  profile, it's now treated as flash-only, the same way certain `[display]`/`[touch]` keys
  already are.
- Fixed the boot-time **RAM Profile picker** failing to scroll when there are more profiles
  than fit on screen (`mp/boot_session.py`). `_draw_profile_ui()` always rendered starting from
  the first profile and simply stopped once it ran out of vertical space — there was no
  mechanism at all to keep the scroll position following the cursor (`sel`, moved via Up/Down)
  once it went off-screen. As a result, with more profiles than visible rows, pressing Up to
  wrap around to the last profile moved the selection past the drawn range entirely, so the
  highlight simply never appeared anywhere. Fixed by adopting the same "scroll offset follows
  the cursor" approach the EMULATOR MENU already uses (`_run_menu()` in `emulator_menu.py`).
- **Boot-time profile selection now does the same full resume as the EMULATOR MENU's RAM Load**
  (PC/registers restored, no reset) (`mp/main.py`). When full-state RAM Load resume was added on
  2026-08-13, boot's own auto-load was deliberately kept conservative by explicitly passing
  `system.load_state(restore_cpu_state=False)` — restoring RAM only and resetting to PC=0x0000 —
  out of concern that an unattended boot (the picker timing out with nobody at the keyboard)
  could resume a bad/inconsistent save with no way to reach the menu to recover. In practice,
  though, that safeguard applied uniformly to both the timeout case and an explicit Enter-key
  selection, so selecting a profile at the picker never actually resumed exactly where RAM Save
  left off, as intended. Changed to use `load_state()`'s own default (`restore_cpu_state=True`),
  so boot now matches the EMULATOR MENU's RAM Load behavior. A separate safety net for the
  unattended case still exists: within the first 1.5 seconds of the main loop, if the CPU comes
  up stuck sleeping with KEY_INT disabled, it force-resets (the "Startup sleep detected" guard).
  - This change surfaced a bug where the screen right after resume just stayed at whatever it
    showed before the load (a black screen), never updating. Root cause wasn't timing — it's that
    **the LCD hardware's own pixel buffer** (`lcd_state.vram` in `src/lcd_controller.c`) **was
    never part of RAM Save/Load's saved files at all.** The PB-1000 only ever accesses its LCD
    through the HD61700's dedicated I/O port instructions (STL/PPO/STLM/etc. — not plain memory
    stores to `ram0.bin`), so `vram` lives in a C struct completely separate from `ram0.bin`,
    which `load_state()` never touches. Normally the ROM's own boot code (running from PC=0x0000)
    rebuilds `vram` as a side effect; resuming mid-program instead means it stays exactly as
    `lcd_init()` zeroed it unless the resumed program happens to draw something on its own.
    Meanwhile `LEDTP` (`&H6201`-`&H6800`) — the ROM's own text-screen management buffer — lives in
    plain RAM (part of `ram0.bin`, correctly restored) and gets transferred into `vram` by the
    ROM's own DOTDS routine (`&H022C`, see `references/rom0.src`). Added
    `system.refresh_lcd_from_ledtp()` (`mp/pb1000.py`) replicating that same transfer — pure data
    movement, never touches CPU registers — called right after `power_on()` and before
    `force_full_redraw()`, both at boot (`main.py`) and in the EMULATOR MENU's RAM Load
    (`emulator_menu_ext.py`'s `_do_ram_load()`). The transfer logic was modeled on the existing
    64-dot-mode DOTDS replacement (`_dotds_override()` in `mp/ext/dotds_64dot.py`), differing from
    it by correctly accounting for SCTOP (scroll position) — the 64-dot version deliberately
    ignores SCTOP for its own purposes, so it couldn't be reused as-is. No C changes needed
    (reuses the existing `lcd_c.blit_reversed()`/`get_num_pages()`/`mark_dirty()`); Python-only.
  - The same kind of gap existed for **Color VRAM** (the VDP extension, `lcd_state.color_vram`),
    fixed alongside this. Unlike mono vram, color VRAM has no equivalent of "LEDTP" — plain RAM
    content it can be rebuilt from — since it's either stamped incrementally by
    `write_vram_pixel_byte()` as the program draws, or bulk-written directly via the bank-RAM→
    color-VRAM DMA registers or `vram_loader.py`'s SD/FDD loader. The only way to capture it is
    as its own file. `save_state()` now writes `color_vram.bin` (12,288 B) whenever VDP is
    actually active (`system.lcd.vdp_enabled`); `load_state()` reads it back and restores the
    VDP-enabled/VDP-initialized flags if present, and explicitly disables VDP if not (so
    switching profiles mid-session via RAM Load can't leave a previous profile's VDP state active
    over content that never used it). Since `lcd_c.get_color_vram()` already returns a direct
    reference to `lcd_state.color_vram`, no C changes were needed here either.

### Documentation

- `doc/usage_guide_en.md` / JA: added previously-undocumented coverage of the **F1 BIOS-style
  setup menu** (`mp/setup_menu.py` — picking a target file, editing keys, the reset-on-save
  behavior, Discard) and **F12: exit to REPL** (the development/recovery escape hatch) to §2
  "Boot Flow". Also noted that the profile picker now scrolls automatically. Updated the
  "Auto-Load" section to match boot now doing the same full resume as RAM Load (the old
  behavior is kept as a note for context). Added `color_vram.bin` (VDP only) to the saved-file
  list in §8 "State Management".
- `doc/hardware_guide_en.md` / JA: added a new section 9 covering HDMI mirror output wiring
  (main unit Pico 2 W ↔ receiver Pico 2, the new GP28 CS pin) and a link to the receiver
  project.
- `doc/config_guide_en.md` / JA: added a new section 14 documenting every `[hdmi]` key
  (`enable`/`cs_pin`/`baudrate`/`frame_skip`). Also added "Exception 2" to §1 noting that the
  entire `[hdmi]` section is flash-only (and how its mechanism differs from the `[display]`/
  `[touch]` early keys).
- `doc/usage_guide_en.md` / JA: added a new section 11 covering how to enable it and the
  LCD/HDMI exclusivity behavior (while HDMI is enabled, the game screen, bezel, RAM profile
  picker, and EMULATOR MENU are all shown exclusively on HDMI, never on the physical LCD).
- `doc/emulator_menu_guide_en.md` / JA: added an **HDMI** row to the Display sub-menu table.
  Also fixed the menu-layout diagram's stale Display description ("change on-screen colors" —
  it was never updated when LCD Height was added in an earlier session) to
  "colors, resolution, and HDMI output" (a pre-existing documentation gap, not something
  introduced by this session's feature work). Added a **Reboot Emulator (MCU)** row to the
  System sub-menu table too, with a warning clarifying how it differs from the existing
  **Reset** (which only affects the emulated PB-1000 side).
- `mp/pb1000.ini`: updated the `[hdmi]` section's comment to reference the new section 9 in
  `hardware_guide.md`, and added a note that the section is flash-only.
- `doc/extension_api.md` / `_en.md`: documented the `<profile>/ext/` directory and the
  three-way priority order (per-profile > `/sd/ext/` > `/ext/`).

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
