# Configuration File Guide (pb1000.ini)

This guide is a reference for every section and key in `pb1000.ini`. For setup
instructions and feature-by-feature explanations, see `usage_guide_en.md`.

---

## 1. Load Order and Merge Rules

Files are loaded in the following order (low → high priority) and merged
**per key, not per section**. Only the keys you write are overridden; any key
you omit falls back to the next lower layer, ultimately the built-in default.

```
built-in defaults (mp/config.py _DEFAULTS)
  < /pb1000.ini             (root of Pico flash)
  < /sd/pb1000.ini           (SD card)
  < <profile>/pb1000.ini     (/sd/rams/<name>/pb1000.ini)
```

Implementation: `load_config()` in `mp/config.py`. Comments start with `;` or `#`.

### Exception: keys read once at boot, from internal flash only

A few keys are needed to initialize the display/touch hardware, so they are
read **before the SD card is mounted**. They therefore do not follow the
priority chain above — only `/pb1000.ini` and `/roms/pb1000.ini` (internal
flash) are honored. Writing them to `/sd/pb1000.ini` or a per-profile ini has
no effect.

- `[display]`: `driver` / `spi_baudrate` / `rotation`
- `[touch]`: `swap_xy` / `x_inv` / `y_inv` (including their driver-prefixed forms)

Every other `[display]` key (`scale` / `lcd_height` / `x_offset` / `y_offset` /
`fg_color` / `bg_color`) and the `[touch]` offset keys (`x_offset` / `y_offset`
/ `funckey_x_offset` / `funckey_y_offset`) follow the normal priority chain and
can be overridden from the SD card or a per-profile ini.

Implementation: `_read_early_ini_sections()` / `init_display()` in `mp/display_init.py`.

### Exception 2: an entire section is flash-only (`[hdmi]`)

The **entire** `[hdmi]` section is honored only from the internal flash's
`/pb1000.ini`. Writing an `[hdmi]` section to `/sd/pb1000.ini` or a
per-profile ini has no effect — `load_config()` skips it at merge time (it
does **not** follow the priority chain at the top of §1). This is a
hardware-wiring setting, not something that should vary by SD card or
profile.

This uses a different mechanism from "Exception 1" above (`[display]`/
`[touch]`'s early keys). That one naturally avoids SD/profile-ini influence
by reading before the SD card is mounted (`_read_early_ini_sections()`), but
`[hdmi]`'s values aren't needed until after the SD card is mounted (around
profile selection), by which point SD is already readable. So instead,
`load_config()` in `mp/config.py` explicitly skips the `[hdmi]` section
(`_FLASH_ONLY_SECTIONS`).

Implementation: `load_config()` / `_FLASH_ONLY_SECTIONS` in `mp/config.py`.

---

## 2. `[display]`

| Key | Default | Description |
| --- | --- | --- |
| `driver` | `ILI9341` | `ILI9341` (320×240) or `ST7796` (480×320, e.g. MSP4021). `display =` is accepted as an alias. *Internal-flash-only key.* |
| `spi_baudrate` | ILI9341: `26000000` / ST7796: `40000000` | LCD SPI baud rate (Hz). *Internal-flash-only key.* |
| `scale` | ILI9341: `1.5` / ST7796: `2.0` | On-screen magnification. The default follows ST7796 sizing whenever the actual display width is 480px or more. |
| `lcd_height` | `32` | `32` = original PB-1000, `64` = extended mode (enables row-address bit\<2\>, allowing writes to pages 4-7 / rows 32-63). Falls back to `32` for any value other than 32/64. |
| `x_offset` | auto (horizontal center) | X position of the LCD's left edge, in pixels. Auto-computed as `(display width - 192*scale) / 2` when omitted. |
| `y_offset` | auto (vertical center) | Y position of the top of the LCD+FuncKeyBar group, in pixels. Auto-centers the whole group when omitted; smaller values move it up. |
| `rotation` | `0` | `0` = normal, `180` = upside down (to match how the board is physically mounted). Touch coordinates are flipped automatically to match. Falls back to `0` for any value other than 0/180. *Internal-flash-only key.* |
| `fg_color` | `0` | Foreground (lit-pixel) color, RGB332 format, 0–255. Changing it via **Foreground Color** in the emulator menu writes it back automatically to `/sd/pb1000.ini` (or `/pb1000.ini` if no SD card). |
| `bg_color` | `180` | Background (unlit-pixel) color, RGB332 format, 0–255. Write-back behaves the same as `fg_color`. |
| `vdp_enable` | `true` | Enable color VRAM (VDP, per-pixel color rendering). `false` forces plain two-color monochrome rendering regardless of what `load_state()` tried to restore. Before 2026-08-22 this was a runtime-only toggle (EMULATOR MENU's "Color VRAM (VDP)"); it's now unified into this key. |

RGB332 (8-bit) layout: bits 7-5 = R (3 bit), bits 4-2 = G (3 bit), bits 1-0 = B (2 bit).
Representative values: `0` = black, `255` = white, `180` (0xB4) = slightly bluish gray, `7` = blue.

Implementation: `init_display()` in `mp/display_init.py` (driver/spi_baudrate/rotation),
`create_system()` in `mp/main_boot.py` (scale/lcd_height/x_offset/y_offset/fg_color/bg_color),
`mp/main.py` (applies `vdp_enable` right after `load_state()`).

---

## 3. `[keyboard]`

| Key | Default | Description |
| --- | --- | --- |
| `enable_usb_kbd` | `true` | Enable the USB keyboard. |
| `key_pulse_interval_ms` | `25` | KEY_INT pulse interval (ms). The real hardware's Key/Pulse ISR runs every 3.9ms (256Hz). Lower values shorten how long the ROM's keyboard debounce takes to register a key. Other time-based tuning (cursor-key repeat, `dev_guide_en.md` §9) assumes this interval too, so re-check normal typing and cursor repeat behavior after changing it. Also settable live via REPL: `hd61700.set_key_pulse_interval_ms(ms)`. |
| `key_hold_ms` | `120` | Key-press hold duration (ms). |
| `key_release_hard_timeout_ms` | `1200` | Hard timeout for forcing a key release (ms). |
| `inter_key_gap_ms` | `80` | Gap between successive key presses (ms). |

Implementation: boot sequence in `mp/main.py`; defaults in `mp/config.py` `_DEFAULTS["keyboard"]`.

> [!NOTE]
> `enable_uart_kbd` / `uart_baudrate` / `uart_tx_pin` / `uart_rx_pin` / `uart_enter_always_exe`
> (UART keyboard input over GP4/GP5, UART1 -- and the Serial Console feature that shared the
> same UART object) were removed on 2026-08-22. RS-232C (`[pio_uart]`, GP6/GP13) is unaffected.

---

## 4. `[emulator]`

| Key | Default | Description |
| --- | --- | --- |
| `enable_repl_uart` | `true` | Set to `false` to have `boot.py` stop the UART0 REPL wired to GP0/GP1 (does not affect the USB CDC REPL). |
| `frame_interval_ms` | `33` | Display refresh interval (ms); 33ms ≈ 30fps. |
| `active_step_count` | `12000` | CPU steps executed per outer-loop slice. |
| `sleep_poll_ms` | `10` | Polling interval while the CPU is asleep (ms). |
| `step_timer_tick_steps` | `40000` | CPU steps per timer-tick unit. |
| `timer_tick_ms` | `1000` | Real-time timer tick interval (ms). Set to `0` or below to disable this tick processing. |
| `loop_idle_ms` | `0` | Main-loop idle wait (ms). |
| `step_chunk` | `2048` | Inner chunk size (in steps) used within a CPU execution slice; also paces how often the PIO UART bridge is serviced. |

Implementation: main-loop constant loading/usage in `mp/main.py`; `run_cpu_slice()` in `mp/main_runtime.py`.

---

## 5. `[disk]` (Virtual FDD)

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `false` | Enable the virtual FDD. |
| `backend` | `raw` | Storage backend. |
| `path` | (empty) | Disk image file path (e.g. `/sd/disks/disk1.img`). |
| `readonly` | `false` | Mount read-only. |

Implementation: virtual-FDD init in `PB1000System`, `mp/pb1000.py` (reads `self._config["disk"]` directly).

---

## 6. `[profile]`

| Key | Default | Description |
| --- | --- | --- |
| `default_profile` | `default` | Default profile name at boot (`/sd/rams/<name>/`). |
| `ui_timeout_ms` | `30000` | Timeout for the profile-selection UI (ms). The UI is skipped entirely when only one profile exists. |

Implementation: `mp/main.py`; `select_profile_ui()` in `mp/boot_session.py`.

---

## 7. `[joystick]`

| Key | Default | Description |
| --- | --- | --- |
| `enable` | `false` | Enable an Atari-compatible 9-pin joystick (PULL_UP inputs). |
| `enable_fire2` | `true` | Enable the FIRE2 button. |
| `debounce_ms` | `20` | Debounce time (ms). |
| `poll_interval_ms` | `10` | Poll interval (ms). |
| `key_up` / `key_down` / `key_left` / `key_right` / `key_fire1` / `key_fire2` | (empty = built-in default) | PB-1000 key sent by each button. Accepts a named constant (`exe`, `ans`, `shift`, `up`, `down`, `left`, `right`, `bs`, `ins`, `brk`, `newall`, `menu`, `cal`, `cls`, `kana`, `a`-`z`, `0`-`9`) or a raw `row,col` coordinate (e.g. `10,4`). Built-in defaults: UP=cursor up, DOWN=cursor down, LEFT=cursor left, RIGHT=cursor right, FIRE1=EXE, FIRE2=SHIFT. |

Pin assignments (GP18/19/20/21/26/27) cannot be changed via ini. Edit
`JoystickInputManager.DEFAULT_PIN_MAP` in `mp/main_input_joystick.py` instead.

Implementation: `_parse_joystick_key()` call site in `mp/main.py`; `_parse_joystick_key()` and
`JoystickInputManager` in `mp/main_input_joystick.py`.

---

## 8. `[beep]`

| Key | Default | Description |
| --- | --- | --- |
| `enable` | `true` | Enable the beeper. |
| `gpio_pin` | `14` | Beeper output pin (GPIO number). |
| `freq_hz` | `1000` | Beep frequency (Hz). |
| `duty` | `50` | PWM duty cycle (%). |

Implementation: `_beep_set()` etc. in `mp/pb1000.py`; freq/duty changes from the menu in `mp/emulator_menu.py`.

---

## 9. `[touch]` (XPT2046 Touch Panel)

Because the touch film is mounted differently on each LCD module, axis
swap/invert and pixel-offset tuning are sometimes needed. Settings for
ILI9341 and ST7796 can **coexist in the same `[touch]` section**: prefix a key
with `ili9341.` or `st7796.` to scope it to whichever driver is active via
`[display] driver`. An unprefixed key applies to either driver unless
overridden by a driver-scoped one.

| Key (supports `ili9341.` / `st7796.` prefix) | ILI9341 default | ST7796 default | Description | Overridable from SD/profile ini |
| --- | --- | --- | --- | --- |
| `swap_xy` | `true` | `true` | Swap the X/Y touch axes. | No (internal-flash only) |
| `x_inv` | `false` | `true` | Invert the X axis. | No (internal-flash only) |
| `y_inv` | `false` | `true` | Invert the Y axis. | No (internal-flash only) |
| `x_offset` | `0` | `8` | X-axis pixel correction for the LCD touch area (TK1..16). | Yes |
| `y_offset` | `-10` | `0` | Y-axis pixel correction for the LCD touch area. | Yes |
| `funckey_x_offset` | `0` | `2` | X-axis pixel correction for the FuncKeyBar. | Yes |
| `funckey_y_offset` | `24` | `-8` | Y-axis pixel correction for the FuncKeyBar. | Yes |

Example (override only ST7796's Y offset):

```ini
[touch]
st7796.y_offset = -4
```

**Notes:**

- `swap_xy`/`x_inv`/`y_inv` are read only from the internal-flash `/pb1000.ini`
  (and `/roms/pb1000.ini`) at boot, before the SD card is mounted — see §1.
- Flipping `y_inv`/`x_inv` changes what the raw coordinate means, so the
  `x_offset`/`y_offset` correction values may need re-tuning too (a good
  starting point is to flip their sign, then fine-tune on real hardware).
- The LCD touch area (TK1..16) hit-test region is **always fixed at
  32 dots × `scale`**, regardless of `[display] lcd_height`. The real
  hardware's physical touch pad only ever covers 32 dots, so the hit-test
  area does not grow in 64-dot extended mode.

Implementation: `init_display()`, `_read_early_ini_sections()`, `_early_bool()` (all in
`mp/display_init.py`); `_setup_touch_offsets()` in `mp/main_boot.py`;
`TouchInputManager.poll_coords()` in `mp/main_input_touch.py`.

---

## 10. `[rs232c]`

Implemented internally as a PIO-based software UART (`pio_uart.py`), but the setting name
matches the real PB-1000's feature name, "RS-232C".

| Key | Default | Description |
| --- | --- | --- |
| `enable` | `true` | Enable RS-232C. `false` means `system.pio_uart` is never constructed and stays `None` for the whole session (RS-232C disabled entirely). Before 2026-08-22 this was a runtime-only toggle (EMULATOR MENU's "RS-232C (PIO)"); it's now unified into this key. |
| `baudrate` | `9600` | Baud rate. The real PB-1000's RS-232C interface only supports 300-9600bps. |
| `tx_pin` | `6` | GPIO number used for TX (default GP6). |
| `rx_pin` | `13` | GPIO number used for RX (default GP13). Pick pins that don't conflict with other features (LCD/SD/touch/BEEP/HDMI/joystick, etc). |

Implementation: `mp/main.py`; `initialize_usb_host_and_pio()` in `mp/main_boot.py`; `mp/pio_uart.py`.

---

## 11. `[wifi]`

| Key | Default | Description |
| --- | --- | --- |
| `ssid` | (empty) | WiFi SSID. Leave empty to skip NTP sync entirely. |
| `password` | (empty) | WiFi password. |

Implementation: `mp/main.py` (only consulted when `[ntp] enable=true`); `mp/ntp_sync.py`.

---

## 12. `[ntp]`

| Key | Default | Description |
| --- | --- | --- |
| `enable` | `true` | Enable NTP time sync at boot (requires a WiFi connection). |
| `server` | `pool.ntp.org` | NTP server address. |
| `tz_offset_h` | `9` | Timezone offset in hours (JST is 9). |
| `timeout_ms` | `15000` | Connection timeout (ms). |

Implementation: `mp/main.py`; `mp/ntp_sync.py`.

---

## 13. `[debug]`

| Key | Default | Description |
| --- | --- | --- |
| `cpu_debug` | `false` | CPU instruction trace (only emitted at specific PC breakpoints). |
| `key_debug` | `false` | Key-input trace (KEYSCAN GRE, etc.). Very verbose — follows the ROM's key-scan loop continuously. |
| `lcd_debug` | `false` | LCD write trace. |

Setting any of these to `true` emits `[HD61700] ...`-prefixed trace lines on
the serial console. See `dev_guide_en.md` §12 "Debugging and Tracing" for details.

Implementation: `mp/main.py`.

---

## 14. `[hdmi]`

Settings for the optional HDMI mirror output feature, which uses a second Raspberry Pi Pico 2
plus an HDMI output addon. If you don't have the addon, leaving `enable = false` (the default)
has zero effect. See [hardware_guide_en.md](hardware_guide_en.md) §9 for wiring and receiver
firmware details.

> [!IMPORTANT]
> **This entire section is flash-only** (see §1 "Exception 2"). An `[hdmi]` section in
> `/sd/pb1000.ini` or a per-profile ini is ignored.

There is no runtime toggle -- edit this key (or use the boot-time F1 setup menu), save, and
reboot the MCU for it to take effect.

| Key | Default | Description |
| --- | --- | --- |
| `enable` | `false` | Enable HDMI mirror output. |
| `cs_pin` | `28` | The extra SPI1 CS pin (GPIO number) used to talk to the receiver. GP28 is recommended (the only free GPIO — see §7). |
| `baudrate` | `10000000` | SPI communication speed with the receiver (Hz). Can be raised if the wiring is solid. |
| `frame_skip` | `1` | How many frames pass between sends to the HDMI side. `1` = every frame. PB-1000's transfer volume is small enough that `1` should normally be fine. |

**LCD and HDMI are exclusive outputs** (never both active at once). When `enable=true`, nothing
is drawn to the physical LCD — the game screen, the EMULATOR MENU, and the boot-time profile
picker are all shown on HDMI instead. See [usage_guide_en.md](usage_guide_en.md) §11 for details.

Implementation: `mp/main.py` (boot-time init only -- no runtime toggle); `mp/pb1000.py`
(`update_display()` — LCD/HDMI exclusivity); `src/lcd_controller.c`
(`lcd_init_hdmi_output()`/`lcd_render_to_hdmi()`).

---

## 15. `[overlay]`

Groups all the on-screen info displays into one section (unified from the former
`[boot_status]`/`[clock_overlay]`/`[mem_overlay]` on 2026-08-24). Covers both the "boot" window
(right after profile selection until the emulator finishes starting up) and the "runtime" window
(after boot completes, during the main loop). If HDMI mirroring has taken over the screen
(`[hdmi] enable=true`), none of these overlays are shown at all, per the physical LCD/HDMI
exclusivity policy (see §14).

| Key | Default | Description |
| --- | --- | --- |
| `show_profile_name` | `true` | Boot only. Show the profile name in the top-left |
| `show_clock` | `false` | Show a clock throughout both boot and runtime. **What the clock actually reads differs by phase** (see below) |
| `show_mem_free` | `false` | Runtime only. Show the Pico 2's free heap (`gc.mem_free()`) at the top-center |
| `show_log` | `false` | Boot only. Mirror the most recent REPL log line at the bottom of the screen |

`show_clock` is a single key that toggles the clock for both phases, but the underlying
implementation differs: the CPU isn't stepping yet during boot, so there's no PB-1000 time
information to read yet. **During boot it reads the Pico's own built-in RTC** (if `[ntp]
enable=true`, the display switches to the correct time once NTP sync finishes partway through
this window); **during runtime it reads TIME$/DATE$ directly from PB-1000's own system variable
RAM** (`DATE$` at `0x6BAD`, `TIME$` at `0x6BB0`, seconds in the timer register's low 6 bits), so
it always matches what `PRINT TIME$` / `PRINT DATE$` would show from BASIC — including when NTP
is disabled or the user has POKEd those addresses directly.

Defaults to off because, unlike the one-time boot splash, `show_clock`/`show_mem_free` occupy
part of the screen for the entire runtime session, and depending on layout
(`scale`/`x_offset`/`y_offset`) may not fit cleanly in the margin above the bezel. Enabling both
together on narrower displays (e.g. 320px wide) can visually overlap the top-right clock text
with the top-center memory text — combining them is left to the user's judgment. Both
temporarily disappear while the EMULATOR MENU occupies the whole screen, and repaint themselves
automatically on the next once-per-second redraw after the menu closes.

The bottom boot-time log line (no config key — always on) mirrors REPL output line-by-line via
`os.dupterm()`, so anything printed during that window shows up there, not just `main.py`'s own
prints but also output from `main_boot.py`, `ntp_sync.py`, etc. No `gc.collect()` is called
before reading `gc.mem_free()` — doing that would defeat the point of that readout, which is to
show the fragmentation/pressure actually happening during normal operation, not an artificially
cleaned-up number.

Implementation: `mp/boot_status.py` (`BootStatusOverlay`, boot-time), `mp/clock_overlay.py`
(`ClockOverlay`, runtime), `mp/mem_overlay.py` (`MemOverlay`, runtime), `mp/main.py` (where
`boot_status.start()`/`stop()` are called in the boot sequence, and where
`clock_overlay.poll()`/`mem_overlay.poll()` are called from the main loop), `mp/ntp_sync.py`
(writes to the TIME$/DATE$ addresses). `BootStatusOverlay` and `ClockOverlay` only share the
`show_clock` config key -- they remain separate implementations, for the phase-dependent reason
above.

---

## See Also

- Feature-by-feature usage: `usage_guide_en.md`
- Touch panel / FuncKeyBar behavior: `usage_guide_en.md` §4
- Changing settings from the emulator menu: `emulator_menu_guide_en.md`
- Debugging and tracing: `dev_guide_en.md` §12
