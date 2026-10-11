# pio_usb provenance

Vendored 2026-10-04 from a sibling MicroPython/RP2350 project
(`msx_emu_pico2`'s Waveshare RP2350-PiZero port, its own
`src/usb_host/pio_usb/PROVENANCE.md`), which in turn vendored it from:

- **Upstream**: [sekigon-gonnoc/Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB),
  MIT License (see `LICENSE` in this directory).
- **Exact version vendored**: tag `0.7.2`, commit
  `3c1eec341a5232640e4c00628b889b641af34b28`.

Files copied verbatim (unmodified) from that tag's `src/`: `pio_usb.c/h`,
`pio_usb_host.c`, `pio_usb_ll.h`, `sdk_compat.h`, `usb_crc.c/h`,
`usb_definitions.h`, `usb_tx.pio(.h)`, `usb_rx.pio(.h)` — the `.pio.h`
files are upstream's own pre-generated (pioasm) output, already committed
in their repo; not regenerated here, since regenerating PIO headers at
MicroPython build time doesn't work with this project's QSTR pre-scan
step (the same class of problem as any PIO-using C module built via
`USER_C_MODULES` — see `src/micropython.cmake`'s own comments for other
examples of build-order constraints this project works around).

Not copied: `examples/`, the library's own `CMakeLists.txt`
(`src/micropython.cmake` integrates the sources directly).
`pio_usb_device.c` is **not** vendored here at all — this project only
ever uses pio_usb as a host (USB keyboard); nothing calls into the
library's device-mode implementation.

## Deliberate deviation from vendored-verbatim

Unlike the other files, `pio_usb_configuration.h` carries one hand-edited
constant, inherited from the sibling project's own real-hardware finding:

- `PIO_USB_EP_POOL_CNT` reduced from upstream's default `32` to `8` — see
  that file's own comment. This project only ever has a single HID
  keyboard on this port; the sibling project measured the full 32-slot
  pool (`endpoint_t pio_usb_ep_pool[32]`, ~6KB of `.bss`) pushing its GC
  heap below MicroPython's required minimum on real RP2350 hardware once
  combined with its own large static buffers. This project has its own
  sizeable static buffers (ROM/RAM images, bank buffers — see
  `src/modhd61700.c`), so the same risk applies; kept at `8` defensively
  without having independently re-measured it here.

## Why this exists

This project's native USB host path (`src/usb_host_core.c`,
`${PICO_SDK_PATH}/lib/tinyusb/.../hcd_rp2040.c`) uses the Pico 2 (W)'s
single native USB controller, which can only be host **or** device at a
time — so with the keyboard on it, native USB CDC (`mpremote` over the
USB-C port) can't also be available, and the UART0 REPL (GP0/GP1) has to
be used instead. Moving the keyboard to a PIO-bitbanged USB port on
GP4/GP5 (free since 2026-08-22, see `references/gpio_pin_map.md`) via
this library frees the native controller to run as a USB device (CDC
REPL) instead — see `src/usb_host/hcd_pio_usb_pb1000.c`'s own header
comment for the HCD glue that bridges TinyUSB to this library on rhport 1,
alongside the native controller kept on rhport 0.

This project had its own earlier, unfinished attempt at PIO-USB
(`src/usb_host/hcd_pio_usb_custom.c`, a copy of TinyUSB's own
`hw/bsp/rp2040/family.c`-adjacent example glue) that was never actually
vendored/built against a real `pio_usb` checkout and was abandoned in
favor of the native host controller — see that file's own header comment.
This vendoring + `hcd_pio_usb_pb1000.c` supersede it; `hcd_pio_usb_custom.c`
is kept only as historical background, not a source to copy from.

## Board-specific facts this relies on

- `pin_dp` = GP4 (D- is always D+ + 1, i.e. GP5) — see
  `src/usb_host_core.c`'s `usb_host_core_init_pio_usb()`.
- Uses PIO0 exclusively (PIO1 is partially claimed by this project's
  RS-232C soft-UART, `mp/pio_uart.py`'s state machines 6/7 — i.e. PIO
  block 1's SMs 2/3 in its local numbering; PIO0 has no other claimant
  in this project). This library needs a whole PIO block (3 of its 4
  state machines) to itself.
- `tx_ch` = DMA channel 15 (RP2350's highest, 0-15), picked defensively
  far from whatever channel number other DMA users in this project might
  claim first (none identified at the time of writing, but low channels
  are the conventional first choice for anything added later).
- Runs entirely on a single core alongside MicroPython (this project
  doesn't use the second core), serviced by calling `tuh_task()` from
  normal (non-ISR) code -- `usb_host_core_task()`, called from
  `mp/main.py`'s main loop and centrally from `modhd61700.c`'s
  `get_last_key()` (so every keyboard-polling loop in `mp/`, menus
  included, services it without having to call it individually).
  `usb_host_core_start_bg_timer()` is a no-op in this build:  the
  ISR/repeating-timer approach used by the native host controller isn't
  used here.
- `skip_alarm_pool = true` is **mandatory**, not optional hardening:
  `pio_usb_host_init()` defaults to an internal `alarm_pool_create(2, 1)`
  for its own 1ms SOF timer, which collides with MicroPython's rp2 port
  permanently claiming hardware alarm #2 for its own soft-timer/scheduler
  (`MICROPY_HW_SOFT_TIMER_ALARM_NUM`, `ports/rp2/mpconfigport.h`) — a
  `hw_claim_or_assert` panic at boot (bare "Hard assert", no further
  text). Confirmed on the sibling project's real hardware; applies to
  any MicroPython rp2 port build, not just that project's board. Driving
  `pio_usb_host_frame()` from a raw `hardware_alarm_*` one-shot alarm
  instead (as `usb_host_core_init_pio_usb()` does) avoids this entirely —
  also avoids a second, independently-confirmed hazard where *any*
  `alarm_pool_create*()` call (including a self-supplied pool, or
  `add_repeating_timer_us()`/`_ms()`) draws its lock from the same shared
  striped-spinlock pool used by unrelated subsystems firmware-wide.
- This project runs `clk_sys` at 252MHz (`usb_host_core.c`'s
  `set_sys_clock_khz(252000, true)`) for both the native-controller and
  PIO-USB paths' own USB timing requirements and CPU emulation speed --
  unlike the sibling project's pizero board, which couldn't change
  `clk_sys` at all (committed to an onboard DVI's required frequency).
  PIO0 vs PIO1 and clk_sys 240MHz vs 252MHz were both ruled out as causes
  during this project's own real-hardware bring-up of a separate
  enumeration failure (ultimately wiring-related, not clock- or
  PIO-block-related).
