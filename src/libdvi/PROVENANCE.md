# libdvi provenance

Vendored 2026-10-21, phase 1 of the RP2350-PiZero (Waveshare, DVI-only
HDMI output) port, from the sibling **MSX_emu_pico2** project's own
identical copy (`MSX_emu_pico2/src/msx/libdvi/`), which vendored it as
follows (preserved from that project's own PROVENANCE.md):

- **Upstream**: [Wren6991/PicoDVI](https://github.com/Wren6991/PicoDVI)
  (Luke Wren, BSD-3-Clause) — the canonical PIO-bitbanged DVI library for
  RP2040/RP2350.
- **Actual source copied**: [SvenMb/PicoDVI_rp2350](https://github.com/SvenMb/PicoDVI_rp2350),
  a fork of the above "adapted for the Waveshare RP2350 PiZero" — carries
  the same BSD-3-Clause license (see `LICENSE` in this directory) plus the
  RP2350-specific GPIO>31 support (`PICO_PIO_USE_GPIO_BASE`,
  `pio_sm_set_pins_with_mask64()`/`pio_sm_set_pindirs_with_mask64()` in
  `dvi_serialiser.c`) that this board's DVI pins (GPIO32-39) need and that
  upstream had not yet merged as of this snapshot.

Files copied verbatim (unmodified from MSX_emu_pico2's own copy, which was
itself verbatim from upstream except `dvi.c` -- see below): `dvi.h`,
`dvi_config_defs.h`, `dvi_serialiser.c/h/.pio`, `dvi_timing.c/h`,
`tmds_encode.c/h/.S`, `tmds_table.h`, `tmds_table_fullres.h`,
`util_queue_u32_inline.h`.

`dvi.c` carries MSX_emu_pico2's own 2026-10-05 local patch (not upstream):
`dvi_dma_irq_handler()`'s `late_scanline_ctr` backlog counter could never
recover once it went positive (that project's real-hardware symptom:
permanent "No Signal" from something as small as a single `gc.collect()`
call), because their single-core-producer design (one scanline_callback
supplying exactly one fresh TMDS buffer per IRQ) could never out-produce
the drain loop's own "stale buffer" removal. Patched there with a
`was_backlogged` guard (only count a miss as a NEW backlog event when the
counter was 0 entering that IRQ) so the counter can actually reach 0
again. This project's own `dvi_display.c` (not yet written as of this
vendoring pass -- see doc/hardware_guide.md's RP2350-PiZero section) is
expected to use the same single-core-producer design, so this patch is
kept rather than reverted; re-evaluate if PB-1000's own producer design
ends up meaningfully different.

Further local patches (this project only), both so that core1's DVI code
path never executes from flash -- `src/dvi_display.c` keeps DVI running
on core1 while core0 writes flash, instead of pausing core1 as a
multicore_lockout victim:

- `util_queue_u32_inline.h`: every function is `__force_inline` instead
  of `static inline`, and the queue level is computed locally instead of
  via `queue_get_level_unsafe()`, so no out-of-line copy can land in
  flash.
- `dvi.c`: the control channels' `dma_channel_config` is computed once in
  `dvi_init()` (static `ctrl_chan_cfg[]`, single DVI instance) instead of
  calling `dma_channel_get_default_config()` from the IRQ handler on
  every scanline.

**GPL-3.0 compatibility note** (PB-1000_emu_AG2 is GPL-3.0, unlike
MSX_emu_pico2 which is MIT): BSD-3-Clause is a permissive license and may
be freely incorporated into a GPL-3.0 project (the resulting combined work
is distributed under GPL-3.0; the original BSD-3-Clause notice is kept
here and in `LICENSE`, and credited in `THIRD-PARTY-NOTICES.md` as the
project's own `src/hd61700.c` port from MAME already does for a different
BSD-3-Clause upstream).

Not copied: the fork's own `common_dvi_pin_configs.h` (this project, like
MSX_emu_pico2, defines its own pin config directly in `src/dvi_display.c`
instead of depending on upstream's macro-selection mechanism), example
apps, and the fork's own CMakeLists.txt (`src/micropython.cmake` integrates
the sources directly).

Confirmed working reference (GPL-3.0, NOT used as a code source):
[ugufru/xroar-waveshare-rp2350-pizero](https://github.com/ugufru/xroar-waveshare-rp2350-pizero)
independently validates this exact library + pin config on this exact
board at a locked 640x480p60. Its GPL-3.0 license is actually compatible
with this project's own GPL-3.0 license, but no code from it was copied
either way (hardware facts such as GPIO pin numbers are not themselves
copyrightable expression) -- the real source for this project's
`dvi_display.c` is libdvi's own public `dvi.h` API plus MSX_emu_pico2's
already-proven `disp_dvi.c` driver design (core1 launch, scanline_callback-
as-producer).

**Real-hardware status**: confirmed on this project's own firmware,
including live emulator content and the UI layer -- see
doc/hardware_guide.md §10 and the CHANGELOG for current status.
