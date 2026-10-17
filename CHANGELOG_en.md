# Changelog

This file tracks the main user- and developer-facing changes, separately from the `git` commit
log. Dates are approximate (the day the change was made). Since this repo is pushed to GitHub
directly with every change, there's no separate "release" gate — entries are grouped under a
date heading instead of a version number or "Unreleased" marker.

---

## 2026-10-17

### Added

- **Froze most of `mp/*.py` into the firmware** (new `src/manifest_freeze.py`,
  build procedure updated): ~40 files, excluding `boot.py`/`main.py`/`ext/`, are now
  byte-compiled and baked into flash at build time, skipping the `import`-time
  compile cost at boot — this substantially cuts down on GC-heap-fragmentation-
  driven `MemoryError`s during the boot sequence. `main.py` stays out of the freeze
  list because `pyexec_file_if_exists()` checks frozen modules *before* the
  filesystem for that one entry point (the opposite priority from a normal
  `import`); its body moved to `main_entry.py` (an ordinary frozen module), leaving
  `main.py` itself a 3-line launcher.
- **ROM files (`rom0.bin`/`rom1.bin`) and `keymap.map` now fall back to the SD
  card (`/sd/roms/`) when not found on flash** (`mp/main_boot.py`, `mp/keymap.py`):
  flash is checked first, SD second.
- **Restored RAM Autosave's per-flush log line** (`[emulator] ram_autosave_log`,
  default `false`): previously dropped for heap-reduction reasons, now back as an
  opt-in setting.
- **RAM Autosave's boot-time cache restore now also commits to the profile's own
  save files** (`mp/ram_autosave.py` `commit_to_profile()`): a restore used to only
  overlay the in-memory RAM, leaving the profile's own `ram0.bin`/`ram1.bin`
  untouched — so "restored, but not yet re-saved" state would still show up on the
  next restore, and would be lost entirely if the cache were ever lost. A successful
  restore now commits straight to the profile's own files and discards the cache.
- **Added an "Export for Device" command to the keymap editor**
  (`tools/keymap_editor.py`): writes the device-ready `keymap.map` from the editable
  JSON (Ctrl+E).

### Fixed

- **Fixed the function-key bar (the LCKEY..CALC touch bar) displaying corrupted
  when its image is read from the SD card** (`mp/funckey_bar.py`): the likely cause
  was electrical coupling on the shared SPI bus from alternating SD-card reads and
  LCD writes (see the (non-committed) investigation notes for the full story).
  Replaced the image-file blit entirely with code-drawn rendering
  (`fill_rect()`/`draw_text()`), removing the whole code path this corruption could
  occur on. Colors are taken from measurements of a reference photo of the real
  device. `mp/emulator_menu_capture.py`'s full-screen capture was updated to match
  (computed colors, no file read).

### Changed

- **Heap reduction: dropped the `keymap.json` format in favor of a compact
  `keymap.map` format** (`mp/keymap.py`, `tools/keymap_editor.py`): loading a custom
  keymap via `json.load()` was found, on real hardware, to cost about 17 KB of GC
  heap relative to its actual data (a few hundred ints/strings) — almost all of it
  the generic JSON tree `json.load()` has to build along the way (one dict per
  entry, repeated key strings). `tools/keymap_editor.py` still edits/saves the
  human-friendly JSON form and exports this compact form via "Export for Device".
  `get_label()` now reads `hd61700.get_key_label()` (C side) instead of the
  Python-side USB_MAP/ADV_MAP dicts, and a new `release_maps()` frees those dicts
  once the C side has been configured from them.
- **Heap reduction: removed the Python-heap copy of ROM code kept for VFDD
  (virtual FDD)** (`mp/pb1000.py`, `mp/pb1000_fdd.py`, `mp/main_boot.py`): the CPU
  core (C side) already keeps its own independent copy in a fixed buffer, and the
  Python-side copy (`keep_copy`) had no live consumer — removing it frees about
  38 KB on VFDD-enabled profiles.

---

## 2026-10-16

### Fixed

- **Fixed a `MemoryError` at boot on 3-bank profiles (e.g. `bank23`) despite
  plenty of free heap being reported** (`src/modhd61700.c`): dynamic allocation
  (GC-heap `m_malloc()`) of the bank 1-3 buffers could fail to find a contiguous
  32 KB block even with well over 32 KB free in total, due to external
  fragmentation from live objects accumulated earlier in boot. Switched to a
  fixed-size static array (96 KB, outside the GC heap), same as `rom0_buf`/
  `rom1_buf`/`ram_buf`, so bank allocation can no longer fail by construction. The
  trade-off: every profile now permanently loses 96 KB of GC heap, whether it uses
  any banks or not (requires a firmware rebuild).
- **Fixed RAM Autosave's native dirty-range watch (watch 2, tracking the file
  area's content) unconditionally discarding its own just-accumulated dirty range
  on every boundary-pointer write into expanded RAM (bank1)** (`mp/ram_autosave.py`
  `_resync_ranges()`): the root cause of a report of a SAVE's content coming back
  empty after a power cut right after saving. Re-arming now only happens when the
  watched range actually changed, and the flush debounce window was also shortened
  from 800ms back down to 300ms.
- **Changed `default_profile` to persist automatically instead of staying fixed
  across reboots** (`mp/boot_session.py`, new `save_default_profile()`): the
  profile actually picked is now saved back to `pb1000.ini` every time a selection
  is confirmed. Writes go through a temp file + `os.rename()` so an exception
  mid-write can never leave `pb1000.ini` itself truncated/corrupted.

### Changed

- **Heap reduction: removed the `hasattr(cpu_core/lcd_c/usb_host/hd61700, ...)`
  feature-detection guards project-wide** (`mp/pb1000.py`, `mp/pb1000_state_io.py`,
  `mp/pb1000_fdd.py`, `mp/lcd_controller_c.py`, `mp/main_boot.py`,
  `mp/emulator_menu.py`, `mp/emulator_menu_capture.py`, `mp/hdmi_menu_mirror.py`):
  measurements showed the fallback branch's bytecode stayed compiled into the
  `.mpy` either way, so these guards were making files *larger*, not smaller. A
  `mp/*.py`/firmware version mismatch now fails loudly with `AttributeError`
  instead of silently degrading to an older code path (requires a firmware
  rebuild).
- **Consolidated the RGB565⇔RGB332 conversion and USB-scancode→display-label
  lookup, previously duplicated across several files, into the C extension module**
  (`src/modlcd_controller.c`, `src/modhd61700.c`, `mp/emulator_menu.py`,
  `mp/hdmi_menu_mirror.py`).

---

## 2026-10-14

### Added

- **Added a Win+PageUp display of RAM Autosave's cumulative flush stats** (`mp/ram_autosave.py`
  `stats_summary_line()`, `mp/main.py`): shows the flush count, bytes written, and average time
  per flush since boot, in the same status bar (below the game screen) used by Win+F8's ON/OFF
  message, for about 4 seconds.

### Fixed

- **Fixed RAM Autosave's cache restore clobbering live RAM with stale bytes from outside the
  tracked range** (`mp/ram_autosave.py` `restore_from_cache()`): differential flushing only keeps
  the HIMEM..DIREN content range current, so everything else in the cache file can be as old as
  the cache's last full rewrite. The previous implementation overlaid the *entire* buffer with one
  `readinto()`, which could clobber correct live RAM (OS work variables, etc.) with those stale
  bytes -- the actual cause of key input working right after a restore while the emulated OS then
  behaved erratically, with no later reset fixing it. Fixed by overlaying only the 12-byte
  boundary-pointer block plus the HIMEM..DIREN range, using the HIMEM/DIREN values as recorded in
  the cache itself. Also fixed a `MemoryError: ... allocating 32769 bytes` on restoring expansion
  RAM (RAM1) via `f.read(32768)` -- on real hardware, `f.read(n)` apparently needs n+1 bytes of
  internal allocation at this size; switching to `f.readinto()`, which writes directly into the
  destination buffer, avoids this entirely.
- **Fixed RAM Autosave flushing continuously during an all-BASIC program with no file access at
  all** (`mp/ram_autosave.py` `_resync_ranges()`): when the file area extends into expansion RAM
  (bank1), the watch range's low bound was unconditionally `0x8000`. For a profile where `HIMEM`
  itself sits inside bank1, this misclassified BASIC's own numeric/string variable tables (below
  `HIMEM`) as file content, triggering an unnecessary flush on every variable update. Fixed the
  low bound to `max(HIMEM, 0x8000)`; the boot-time restore path excludes that same region
  accordingly.
- **Fixed Win+F8 (autosave ON/OFF) and Win+PageUp (stats) messages never appearing on screen**
  (`mp/main.py`): both used `system.set_status()`, but the per-keypress key-label display
  (`handle_key_status_and_capture()`), which uses the same mechanism, ran later in the same
  main-loop iteration and immediately overwrote the message. Fixed by reordering so the
  Win+F7/F8/PageUp branches' messages are set last.
- **Fixed Win+F7 (emulator menu) and Win+F8 (autosave toggle) also pressing their mapped physical
  key (CAL / MEMO IN) at the same time** (`src/modhd61700.c` `c_kb_process_usb_key()`): Win+F11
  (reset) already suppressed the ordinary key press while Win is held, but F7/F8 had no such
  guard, so the Win combo and the ordinary key press both fired. Fixed using the same approach as
  F11 (firmware rebuild required).
- **Fixed the profile-name label disappearing after dismissing RAM Autosave's restore prompt or
  closing the emulator menu** (`mp/boot_status.py` new `redraw_header()`, `mp/main.py`,
  `mp/emulator_menu.py`): the top-left profile-name label is painted once at boot and never
  redrawn afterward; a later full-screen clear (dismissing the restore prompt, restoring the
  screen after the menu closes) erased it with nothing repainting it. Added `redraw_header()`
  (honors `pb1000.ini`'s settings) and call it right after each such clear.
- **Improved SD card transfer speed** (`mp/display_init.py`, `mp/sdcard.py`): fixed data transfers
  wrongly running at the 400kHz init-handshake baud rate, and introduced a dedicated
  `SD_DATA_BAUD` (12MHz, reusing the sibling MSX_emu_pico2 project's real-hardware sweep result).
  Also sped up `readinto()`/`write()` by busy-polling the first 100 data-token wait iterations and
  merging the checksum read/write from two 1-byte transfers into one 2-byte transfer.

---

## 2026-10-11

### Added

- **Added a PIO-USB host mode (GP4/GP5) and switched the default to it**: the USB keyboard host
  moved to PIO-USB (GP4/GP5), freeing the native USB-C port to run as a CDC device (direct
  `mpremote` access). Controlled by the `USE_PIO_USB` build option (default `ON`); pass `OFF` to
  build the previous native-USB-host-only configuration instead. See `doc/hardware_guide_en.md`
  and `doc/build_guide.md` for wiring and build instructions. Known limitation: some USB
  keyboards aren't recognized over PIO-USB.

### Fixed

- **Fixed a GC-heap pointer for the bank RAM buffers not being registered as a GC root**
  (`src/modhd61700.c`): `bank1_buf` etc. are now registered via `MP_REGISTER_ROOT_POINTER()`.
  Without that registration, `gc.collect()` could mistake the buffer for unreachable and hand its
  memory to an unrelated Python object.

---

## 2026-10-04

### Fixed

- **Fixed `[joystick] key_fire1`/`key_fire2` default coordinates** (`mp/main_input_joystick.py`'s
  `JoystickInputManager.DEFAULT_KEY_MAP`): comments and `pb1000.ini` both documented FIRE1=EXE and
  FIRE2=SHIFT as the defaults, but the actual coordinates were `fire1: (10,1)` (really SPACE) and
  `fire2: (10,4)` (really EXE) -- so with the default config, pressing FIRE1 typed SPACE and FIRE2
  typed EXE. Easy to miss since explicitly setting `key_fire1`/`key_fire2` worked fine. Fixed to
  `fire1: (10,4)` / `fire2: (11,2)` so they resolve to EXE/SHIFT as intended.
- **Fixed named `[joystick]` key mappings (`bs`, `exe`, `menu`, etc.) not working at all**
  (`mp/keymap.py`, `mp/main_input_joystick.py`): `pb1000.ini` documented multi-character name
  options (`bs`, `exe`, `ans`, `shift`, `up`, `down`, `left`, `right`, `ins`, `brk`, `newall`,
  `menu`, `cal`, `cls`, `kana`, etc.), but the resolver, `_parse_joystick_key()`, only checked
  `keymap.KEY_MAP` (single-character labels only) and a nonexistent `keymap.KEY_<name>` module
  constant -- so every one of these names silently failed to resolve and fell back to the default
  key, with no error or warning. Added a `NAMED_KEYS` dict to `keymap.py` for multi-character
  labels (auto-collected from `USB_MAP`/`ADV_MAP`, with an explicit alias for `brk` -> `BREAK`
  where the documented short name doesn't match the raw label), and wired `_parse_joystick_key()`
  to check it. This also newly covers names that weren't even documented before (`space`, `caps`,
  `eng`, `stop`, `func`, `contr.`, `in`, `out`, `calc`, `memo`, `memo in`, `lckey`, `f1`-`f4`,
  etc.), so every special key in `references/keyboard_layout.md` is now reachable by name.

### Added

- **Added joystick 2-button-combo key assignment (`[joystick_combo]`)**
  (`mp/main_input_joystick.py`, `mp/pb1000.ini`): lets you define a key that only fires when two
  or more buttons are held together, e.g. `fire1+fire2 = exe` or `up+fire1 = brk`. A combo
  covering more buttons takes priority over a smaller one, and while a combo is active its
  member buttons' individual key mappings don't fire on their own. Implemented by switching
  polling from "press/release each button immediately on its own" to "recompute the full desired
  key set from physical button state plus the combo definitions on every poll tick, then
  press/release only the delta" (`_apply_state()`); behaviour is unchanged when no combos are
  configured.

---

## 2026-09-16

### Fixed

- **`tools/md100_gui.py` / `tools/md100.py`: incorrect decoding of BASIC programs stored on
  disk images**: viewing or extracting a BASIC program (Type B) from an MD-100 disk image
  went through `md100.py`'s `print_basic()` detokenizer, which had several bugs.
  - Missing spacing where two keyword tokens are adjacent (e.g. `GOTO` right after `THEN`)
    produced run-together output like `THENGOTO`. The same missing-space issue showed up
    before string literals, right after `REM`, and before `&H` hex literals, e.g.
    `PRINT"HELLO"`, `REM====`, `CALL&H1000`.
  - **Root cause (the main one)**: cross-checking against a disassembly of the real PB-1000's
    ROM (`references/rom1.src`, keyword table at `&H88ED`-`&H8BC9`) revealed that the tail
    ends of the string-function table `_T6` and operator table `_T7` had their token codes
    shifted well away from the real hardware's assignments. `AND`/`OR`/`XOR`/`MOD`/`NOT`/`TO`/
    `STEP`/`USING`/`AS`/`APPEND`/`OFF`/`TAB`/`REV`/`NORM` and `CHR$`/`STR$`/`MID$`/`LEFT$`/
    `RIGHT$`/`INKEY$`/`HEX$`/`DATE$`/`TIME$`/`INPUT`/`DMS$` all sat at the wrong code
    positions, so decoding a genuine PB-1000-tokenized program would **show the wrong
    keyword, or garbled output**, for any of these — all very common in real programs.
    Rebuilt both tables from the verified ROM codes, and removed two fabricated keywords
    (`CSR`/`ALL`) that had no basis in the ROM table and were actively squatting on the real
    `NOT`/`XOR` codes.
  - Verified with a tokenize/decode round-trip against real sample programs
    (`sample/basic/CLVRAM1.BAS`/`CLVRAM2.BAS`/`BNK23TST.BAS`): several lines mismatched
    before the fix, all match exactly after.

---

## 2026-08-31

### Added

- **`[keyboard] poll_interval_ms` setting**: the USB HID keyboard polling interval (how often
  `usb_host_core.c`'s background timer calls `tuh_task()`) was previously hardcoded to 8ms; now
  configurable via the ini file and the boot-time setup menu (F1), default `8` (same as before).
  Lower values make key input more responsive, at the cost of more frequent USB host servicing.
  Documented alongside the existing `[emulator] active_step_count`/`loop_idle_ms` (adjustable
  live from EMULATOR MENU -> System) as the settings to try when key input feels sluggish.

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
