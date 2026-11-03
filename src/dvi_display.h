/*
 * dvi_display.h — onboard DVI output driver for the Waveshare RP2350-PiZero.
 * Only compiled/linked when building for that board (see micropython.cmake's
 * PB1000_IS_PIZERO block). See dvi_display.c's header comment for the full
 * design writeup and real-hardware findings this is based on (ported from
 * the sibling MSX_emu_pico2 project's own already-working disp_dvi.c).
 */
#ifndef PB1000_DVI_DISPLAY_H
#define PB1000_DVI_DISPLAY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Bring up the DVI output hardware: clocks, PIO/DMA, core1 launch.
 * Phase 1 (2026-10-21): drives a static color-bar test pattern only, to
 * verify the DVI signal path itself on real hardware before wiring up
 * actual PB-1000 screen content (Phase 2 -- see doc/hardware_guide.md).
 * Returns false if set_sys_clock_khz() fails (should not happen on a
 * genuine RP2350B at this voltage/clock combination, but checked anyway
 * since this call cannot be retried safely once other clock-dependent
 * peripherals have also started).
 */
bool dvi_display_init(void);

/* Diagnostic: see this file's header comment on msx_dvi_debug_info() in
 * the sibling project for how to use these two counters to tell "core1
 * crashed" apart from "core1 alive but producing wrong content" without
 * a fresh reflash. */
void dvi_display_debug_info(uint32_t *heartbeat, uint32_t *late_ctr);

/* Frames realigned after a missed row deadline (cumulative), and the
 * longest single-row produce time in microseconds since the last call
 * (reset by this call). */
void dvi_display_timing_info(uint32_t *resyncs, uint32_t *pump_max_us);

/* Phase 2a (2026-10-23): switch the scanline producer between the Phase 1
 * static color-bar test pattern (enable=false, the default -- see
 * mp/test/test_dvi_display.py) and live PB-1000 VRAM content read via
 * lcd_c_get_state()/lcd_render_row_rgb565() (enable=true -- see
 * mp/display_init.py's DVI branch, which calls this right after
 * lcd_c.init() has run). Safe to call at any time (just flips a flag read
 * by the next scanline callback); calling with enable=true before
 * lcd_c.init() has run is harmless (lcd_c_get_state() always returns a
 * valid, if all-zero/not-yet-initialized, struct). */
void dvi_display_set_live_content(bool enable);

/* TEMPORARY DIAGNOSTIC (2026-10-25) -- see dvi_display.c's isr_hardfault
 * override comment. occurred=1 if core1 ever hit a hard fault since boot;
 * cfsr/bfar/hfsr are ARM Cortex-M33's SCB->CFSR/BFAR/HFSR captured at
 * fault time (bfar is only meaningful if cfsr's BFARVALID bit, 1<<15, is
 * set). Remove once isolated -- see 調査用/
 * RP2350-PiZero_実機認識しない問題調査.md. */
void dvi_display_fault_info(uint32_t *occurred, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr);

/* Permanent diagnostic (added 2026-10-28) -- see dvi_display.c's
 * dvi_fault_log_persist()/isr_hardfault_c() comment. Unlike fault_info()
 * above (RAM, lost on the power cycle needed to recover from a hang
 * where USB/REPL never comes back), this reads back a flash-persisted
 * copy of the same info plus the faulting PC/LR and which core faulted,
 * from the PREVIOUS boot's crash. magic_ok=0 means no valid log was
 * ever written (flash erased to all-1s). See 調査用/
 * RP2350-PiZero_実機認識しない問題調査.md for the investigation this was
 * built for and an example of resolving pc/lr with arm-none-eabi-addr2line
 * against build-WAVESHARE_RP2350_PIZERO/firmware.elf. */
void dvi_display_read_fault_log(uint32_t *magic_ok, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr, uint32_t *pc, uint32_t *lr, uint32_t *core_num);

/* Erases the persisted flash fault log (see dvi_display_read_fault_log()
 * above), so a stale log from a previous investigation isn't mistaken
 * for a fresh one. CAUTION: like any flash_range_erase()/_program()
 * call, this hangs immediately if core1 is concurrently executing from
 * flash (XIP) -- e.g. while DVI live content is running after
 * dvi_display_init()/dvi_display_set_live_content(true). Only call this
 * before DVI has been started, or after independently stopping core1. */
void dvi_display_clear_fault_log(void);

/* Diagnostic: heartbeat/late_ctr/fault_info are all pure software
 * bookkeeping -- they only observe whether dvi_scanline_pump() itself keeps
 * running and whether it ever took a CPU exception. None of them can see a
 * purely hardware-level PIO TX FIFO underrun (the TMDS serialiser's state
 * machine running dry because the DMA/software producer fell behind for
 * even a single scanline) -- which would desync the TMDS bit/word
 * alignment and show as a permanent "No Signal" on the monitor while
 * heartbeat keeps climbing normally afterward, since heartbeat++ happens
 * right after handing a buffer to the queue, not after confirming the
 * hardware actually transmitted it. The RP2350's PIO FDEBUG register's
 * TXSTALL bits (one per state machine) latch (write-1-to-clear) exactly
 * this event. *fdebug is the raw, unmodified register value (all four
 * nibbles: TXSTALL bits 27:24, TXOVER 19:16, RXUNDER 11:8, RXSTALL 3:0) for
 * the PIO instance driving the three TMDS state machines (sm_tmds 0,1,2 --
 * see pizero_dvi_cfg); never cleared by this call, so it reflects
 * everything latched since boot. Kept as a permanent diagnostic (used to
 * confirm the lcd_render_row_rgb565() fix -- see 調査用/
 * RP2350-PiZero_実機認識しない問題調査.md for the finding this uncovered). */
void dvi_display_pio_debug_info(uint32_t *fdebug);

/* UI plane (see dvi_display.c's "UI plane" comment): a 320x240 image
 * composited over the live PB-1000 screen, standing in for the SPI panel
 * that the bezel/FuncKeyBar/menus are drawn on on the other boards.
 * Coordinates are clipped to the plane. Colors are RGB565. */
void dvi_display_ui_fill_rect(int x, int y, int w, int h, uint16_t color);

/* Blits a window (x0..x1, y0..y1 inclusive) of big-endian RGB565 pixels,
 * row-major -- the same data an SPI panel's set_window()/write_data()
 * pair receives. */
void dvi_display_ui_blit(int x0, int y0, int x1, int y1, const uint8_t *data, size_t len);

/* Places the live PB-1000 screen at (x, y) with scale scale_num/scale_den
 * and makes the UI plane transparent over it. Called whenever the PB-1000
 * screen is redrawn (mp/lcd_controller_c.py's render_to_display()). */
void dvi_display_set_lcd_window(int x, int y, int scale_num, int scale_den);

#endif /* PB1000_DVI_DISPLAY_H */
