/*
 * dvi_display.c — onboard DVI output driver for the Waveshare RP2350-PiZero.
 *
 * 2026-10-21, Phase 1 of the RP2350-PiZero port (HDMI-only output, no
 * physical SPI LCD on this board -- see doc/hardware_guide.md). This
 * file, and every real-hardware finding baked into it, is ported
 * directly from the sibling MSX_emu_pico2 project's own already-working
 * src/msx/display/disp_dvi.c (same physical board, same libdvi -- see
 * src/libdvi/PROVENANCE.md). Only compiled when building for the
 * WAVESHARE_RP2350_PIZERO board (see micropython.cmake's PB1000_IS_PIZERO
 * block); a Pico 2 / Pico 2 W build never sees this file.
 *
 * PHASE 1 SCOPE: drives a static color-bar test pattern, not real PB-1000
 * screen content. The goal of this phase is to confirm the DVI signal
 * path itself (clocks, PIO/DMA, core1 launch, the real-hardware pitfalls
 * documented below) on THIS project's own build, before wiring up actual
 * VRAM content in Phase 2. The sibling project confirmed this exact same
 * checkpoint (color bars over DVI) on real hardware on 2026-09-25;
 * independently CONFIRMED WORKING for this project's own build on
 * 2026-10-24 (color bars + center line, via mp/test/test_dvi_display.py)
 * -- see the CHANGELOG and 調査用/RP2350-PiZero_実機認識しない問題調査.md
 * for how a completely unrelated bug (ports/rp2/CMakeLists.txt's
 * MICROPY_C_HEAP_SIZE defaulting to 0, corrupting dvi_init()'s internal
 * queue buffers via a NULL-unchecked calloc()) masked this file's own
 * logic as broken for several real-hardware debugging sessions first.
 *
 * ---------------------------------------------------------------------
 * Hardware facts this file depends on (confirmed against the vendor
 * schematic by an independent working reference port, see
 * src/libdvi/PROVENANCE.md, and by the sibling project's own real-
 * hardware color-bar test):
 *   - DVI/HDMI-connector TMDS pins: D2±=GPIO32/33, D1±=GPIO34/35,
 *     D0±=GPIO36/37, CLK±=GPIO38/39 -- all outside the RP2350's fixed
 *     HSTX pin range (GPIO12-19), so this MUST go through PIO, not HSTX.
 *   - RP2350B's PIO can only address a 32-pin *window* of its 48 GPIOs at
 *     a time (`pio_set_gpio_base()`); our TMDS pins (32-39) need the
 *     window based at 16 (covers 16-47). See hardware/pio.h's own
 *     PICO_PIO_USE_GPIO_BASE doc comment.
 *   - DMA_IRQ_1, not DMA_IRQ_0 -- something else in a MicroPython build
 *     already holds DMA_IRQ_0's exclusive handler slot (the sibling
 *     project hit a real-hardware "Hard assert" inside
 *     irq_set_exclusive_handler() with DMA_IRQ_0, never traced further;
 *     DMA_IRQ_1 sidesteps it -- carried over here preemptively).
 *   - vreg 1.20V / 252MHz bit clock: confirmed locking correctly on real
 *     silicon by the sibling project's own color-bar test.
 *
 * ---------------------------------------------------------------------
 * Core split: core1 owns DVI *entirely*, standalone -- core0 never
 * touches DVI again after launching core1, freeing it for HD61700/LCD
 * emulation + the MicroPython VM. Everything core1 executes after startup
 * is RAM-resident, and core1 is not a multicore_lockout victim, so core0's
 * flash writes (filesystem, ram_autosave.py) neither stall nor pause it --
 * see core1_dvi_main().
 *
 * Scanline production runs in core1's thread context, not in the DMA
 * IRQ: core1_dvi_main() calls dvi_scanline_pump() (render + TMDS-encode
 * one source scanline, a single stage) whenever a free TMDS buffer is
 * available, and libdvi's DMA IRQ handler is left with only its own
 * short DMA reprogramming work (plus dvi_frame_sync(), a once-per-frame
 * row alignment check -- see there). Encoding
 * one scanline takes longer than one 640x480p60 line period (~31.8us),
 * and the IRQ must reload the next line's DMA control blocks before the
 * current line's active period ends; if the pump ran inside the IRQ, it
 * would delay the next IRQ to within a few microseconds of that
 * deadline, and any extra stall (bus or XIP contention from core0, e.g.
 * USB keyboard traffic) would desynchronise the three TMDS lanes'
 * DMA chains permanently ("No Signal" until reset).
 *
 * Startup pre-fill is mandatory (see dvi_display_init()): without
 * filling every available TMDS buffer before the first DMA IRQ can
 * possibly fire, the sibling project found the screen stayed solid red
 * (libdvi's own "no valid scanline ready" fallback) permanently, because
 * a one-buffer-per-callback producer can never out-produce
 * dvi_dma_irq_handler()'s own "late scanline" backlog-drain step once it
 * has gone positive even once.
 *
 * tmds_encode_scanline_pump() must be __not_in_flash_func -- see the
 * sibling project's own real-hardware finding (reproduced in this file's
 * function comment below): MicroPython's own flash-resident bytecode
 * interpreter running concurrently on core0 contends for the same shared
 * XIP bus, corrupting ("snow") this function's own fetches if it isn't
 * RAM-resident.
 */

#include "dvi_display.h"

#ifdef PB1000_BOARD_PIZERO

#include <string.h>

#include "pico/multicore.h"
#include "pico/sync.h"
#include "pico/time.h"
#include "hardware/clocks.h"
#include "hardware/sync.h"
#include "hardware/vreg.h"
#include "hardware/pio.h"
#include "hardware/structs/scb.h"
#include "hardware/flash.h"
#include "hardware/regs/addressmap.h"
#include "pico/platform.h"

/* See this file's header comment -- must be defined before dvi.h pulls in
 * dvi_config_defs.h's #ifndef guard. Each buffer costs 3*640/2*4 = 3840
 * bytes. Rows covered by the LCD window cost more to produce than the
 * rest of the screen; the buffers are the slack that lets the producer
 * run behind through that band and catch up on the cheaper rows below it
 * and during vblank. 4 (~15KB) leaves room for the UI plane below. */
#define DVI_N_TMDS_BUFFERS 4

#include "dvi.h"
#include "dvi_serialiser.h"
#include "tmds_encode.h"

/* Phase 2a (2026-10-23): live PB-1000 VRAM content -- see
 * lcd_controller.h's lcd_render_row_rgb565() comment for the full
 * rationale (no persistent DVI framebuffer, direct live read, same
 * pattern as the sibling MSX_emu_pico2 project's own disp_dvi.c). This
 * project's own established lcd_c_get_state() cross-module accessor
 * pattern (see modhd61700.c/moddotds64.c for the other existing call
 * sites) -- declared extern locally here rather than centrally in
 * lcd_controller.h, matching that existing convention exactly. */
#include "lcd_controller.h"
extern lcd_state_t *lcd_c_get_state(void);

#define DVI_BORDER_COLOR 0x0000 /* plain black, wherever neither the PB-1000
                                  * screen nor the UI plane draws */

/* DVI pin config -- Waveshare RP2350-PiZero onboard HDMI connector. See
 * this file's header comment for the schematic-confirmed pin facts. */
static const struct dvi_serialiser_cfg pizero_dvi_cfg = {
    .pio              = pio0,
    .sm_tmds          = {0, 1, 2},
    .pins_tmds        = {36, 34, 32},  /* D0+, D1+, D2+ (the +/odd pin of
                                         * each differential pair; the -
                                         * pin is always +1, handled by
                                         * dvi_serialiser_init() itself) */
    .pins_clk         = 38,
    .invert_diffpairs = false,
};

/* Phase 1 source resolution: matches the ILI9341 panel's own 320x240 so
 * that, once Phase 2 wires up real content, every existing mp/ bezel/
 * menu layout constant that already assumes a 320x240 "display" keeps
 * working unchanged (see doc/hardware_guide.md). libdvi's
 * DVI_VERTICAL_REPEAT=2 timing plus its TMDS encoder's own horizontal 2x
 * pixel replication scale this out to a locked 640x480p60 signal with no
 * software upscaling needed -- confirmed on the sibling project's real
 * hardware via its own color-bar test at this exact resolution. */
#define DVI_FB_W 320
#define DVI_FB_H 240

static uint16_t dvi_row_buf[DVI_FB_W];
static struct dvi_inst dvi0;
static volatile uint32_t dvi_heartbeat = 0;

/* Next source row dvi_scanline_pump() produces. dvi_frame_sync() resets
 * it (and bumps dvi_frame_gen) when the queued rows don't line up with
 * the next frame. */
static volatile uint dvi_pump_y = 0;
static volatile uint32_t dvi_frame_gen = 0;
static volatile uint32_t dvi_resync_count = 0;
static volatile uint32_t dvi_pump_max_us = 0;

/* TEMPORARY DIAGNOSTIC (2026-10-25, RP2350-PiZero DVI "No Signal"
 * investigation) -- a real flash write (flash_range_erase()/_program(),
 * e.g. ram_autosave.py's cache writes, or any plain file write of real
 * size) was confirmed on real hardware to permanently freeze core1's
 * dvi_heartbeat the moment it runs, even in complete isolation. Per the
 * RP2350 bootrom docs, an XIP (flash-mapped) read by DMA, the debugger,
 * or the *other* core during an active erase/program returns a bus
 * fault -- exactly what would happen if core1 is reading from a
 * flash-resident address (const data not actually RAM-relocated like
 * dvi_timing_640x480p_60hz is, or similar) at that moment. isr_hardfault
 * is a weak pico-sdk default (crt0.S's decl_isr_bkpt) that just BKPTs
 * forever with no diagnostic output -- this overrides it to capture the
 * fault status/address registers for inspection from Python afterward
 * (dvi_display.fault_info()), without needing a debugger attached.
 * Remove once isolated -- see 調査用/RP2350-PiZero_実機認識しない問題調査.md. */
static volatile bool dvi_fault_occurred = false;
static volatile uint32_t dvi_fault_cfsr = 0;
static volatile uint32_t dvi_fault_bfar = 0;
static volatile uint32_t dvi_fault_hfsr = 0;

/* Permanent diagnostic (added 2026-10-28 while investigating a full
 * main.py boot hang with [display] driver=DVI -- see
 * 調査用/RP2350-PiZero_実機認識しない問題調査.md for that investigation,
 * kept afterward since the underlying problem -- a hard-to-recover,
 * physical-power-cycle-only hang -- can recur for unrelated reasons).
 * The dvi_fault_* globals above are RAM and don't survive the power
 * cycle that's the only way to recover from a hang like this (the fault
 * handler spins forever with faults effectively masked, so there's no
 * way to read them back over USB/REPL before power is removed). This
 * persists the same info, plus the hardware-stacked PC/LR (the actual
 * faulting instruction and its caller) and which core faulted, to a
 * fixed flash sector so it can be read back on the NEXT boot. Fixed
 * sector: the last 4KB of this board's 2MB firmware FLASH region,
 * immediately before FLASH_FS begins (mpconfigboard.cmake:
 * MICROPY_HW_FLASH_STORAGE_BYTES=14MB out of 16MB total, so FLASH itself
 * is the other 2MB) -- firmware here is currently ~600KB, nowhere near
 * this boundary. CAUTION: flash_range_erase()/_program() while the
 * OTHER core is concurrently executing from flash (XIP) is the exact
 * real-hardware hang this same file already warns about further down
 * (dvi_display_debug_info's history comment). dvi_fault_log_persist()
 * itself, called from isr_hardfault_c(), halts whichever core actually
 * faulted -- but if CORE1 faults while core0 is still running normally
 * (executing Python, which reads flash-resident frozen modules via
 * XIP), this same hang can still happen on core0's side. Confirmed safe
 * only for faults that happen before dvi_display_init() has launched
 * core1 (core0 is the only core running at all yet); a fault handler
 * wanting to be safe for a core1 fault with core0 active would need the
 * multicore lockout dance first. dvi_display_clear_fault_log() (called
 * from ordinary running code, not a fault handler) has the same
 * constraint -- see its own doc comment. */
#define DVI_FAULT_LOG_FLASH_OFFSET (2 * 1024 * 1024 - FLASH_SECTOR_SIZE)
#define DVI_FAULT_LOG_MAGIC 0x46415543u /* "FAUC" */

/* buf[7..38]: 32 words of raw stack dump starting AT msp (the hardware
 * exception frame itself: r0,r1,r2,r3,r12,lr,pc,xpsr, i.e. buf[7..14] ==
 * pc/lr already decoded above into buf[4]/buf[5] -- kept raw too for
 * cross-checking), continuing upward into whatever was on the stack
 * just before the fault. Since panic() (pico-sdk's pico_platform_panic)
 * is a plain, non-naked C function, ITS OWN saved return address (i.e.
 * panic()'s real caller, the thing actually worth knowing -- PC/LR
 * above only ever point inside panic() itself, at its own call to
 * _exit()/__breakpoint(), the same for every panic() call site in the
 * whole program) is one of these words; found by scanning for a word
 * that looks like a FLASH code address (0x10xxxxxx) other than the
 * known panic()-internal ones, then resolving it with
 * arm-none-eabi-addr2line against firmware.elf. */
static void __not_in_flash_func(dvi_fault_log_persist)(uint32_t cfsr, uint32_t bfar, uint32_t hfsr, uint32_t pc, uint32_t lr, uint32_t core_num, uint32_t msp) {
    uint32_t buf[FLASH_PAGE_SIZE / sizeof(uint32_t)];
    for (unsigned i = 0; i < sizeof(buf) / sizeof(buf[0]); i++) {
        buf[i] = 0xFFFFFFFFu;
    }
    buf[0] = DVI_FAULT_LOG_MAGIC;
    buf[1] = cfsr;
    buf[2] = bfar;
    buf[3] = hfsr;
    buf[4] = pc;
    buf[5] = lr;
    buf[6] = core_num;
    volatile uint32_t *stack = (volatile uint32_t *)msp;
    for (unsigned i = 0; i < 32; i++) {
        buf[7 + i] = stack[i];
    }

    flash_range_erase(DVI_FAULT_LOG_FLASH_OFFSET, FLASH_SECTOR_SIZE);
    flash_range_program(DVI_FAULT_LOG_FLASH_OFFSET, (const uint8_t *)buf, FLASH_PAGE_SIZE);
}

static void __attribute__((used)) __not_in_flash_func(isr_hardfault_c)(uint32_t msp) {
    /* msp is the value MSP had at the exact moment of exception entry
     * (passed in by the naked trampoline below, which reads it before
     * any C-compiler-generated prologue can touch the stack -- reading
     * it directly inside a normal (non-naked) C function is NOT safe:
     * the compiler's own prologue for THIS function already runs before
     * the first C statement here executes, which was silently
     * corrupting the captured PC/LR (observed as nonsense values 1/4)
     * before this fix). It still points at the hardware-stacked
     * exception frame (r0,r1,r2,r3,r12,lr,pc,xpsr), since this is the
     * first/only fault (not re-entrant) and thread-mode code here only
     * ever runs on MSP (no PSP switch in this build). */
    volatile uint32_t *frame = (volatile uint32_t *)msp;
    uint32_t stacked_lr = frame[5];
    uint32_t stacked_pc = frame[6];

    dvi_fault_cfsr = *(volatile uint32_t *)0xE000ED28u; /* SCB->CFSR */
    dvi_fault_bfar = *(volatile uint32_t *)0xE000ED38u; /* SCB->BFAR */
    dvi_fault_hfsr = *(volatile uint32_t *)0xE000ED2Cu; /* SCB->HFSR */
    dvi_fault_occurred = true;

    dvi_fault_log_persist(dvi_fault_cfsr, dvi_fault_bfar, dvi_fault_hfsr,
                           stacked_pc, stacked_lr, get_core_num(), msp);

    while (1) {
        __asm volatile ("nop");
    }
}

/* Naked trampoline: the actual vector-table entry. No C prologue runs
 * before "mrs r0, msp", so r0 (the first AAPCS argument) is guaranteed
 * to hold the true exception-entry MSP when isr_hardfault_c() starts --
 * see its own comment above for why a plain (non-naked) function here
 * was silently wrong. */
__attribute__((naked)) void __not_in_flash_func(isr_hardfault)(void) {
    __asm volatile (
        "mrs r0, msp\n"
        "b isr_hardfault_c\n"
    );
}

void dvi_display_fault_info(uint32_t *occurred, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr) {
    *occurred = dvi_fault_occurred ? 1 : 0;
    *cfsr = dvi_fault_cfsr;
    *bfar = dvi_fault_bfar;
    *hfsr = dvi_fault_hfsr;
}

/* Reads back the persisted flash fault log from a previous hard fault
 * (see dvi_fault_log_persist() above). *magic_ok is 1 iff a valid log
 * was found (flash erased to 0xFF reads back as all-1s, so a mismatch
 * reliably means "never written"). */
void dvi_display_read_fault_log(uint32_t *magic_ok, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr, uint32_t *pc, uint32_t *lr, uint32_t *core_num) {
    const uint32_t *buf = (const uint32_t *)(XIP_BASE + DVI_FAULT_LOG_FLASH_OFFSET);
    *magic_ok = (buf[0] == DVI_FAULT_LOG_MAGIC) ? 1 : 0;
    *cfsr = buf[1];
    *bfar = buf[2];
    *hfsr = buf[3];
    *pc = buf[4];
    *lr = buf[5];
    *core_num = buf[6];
}

/* Erases the persisted flash fault log, so a stale log from a previous
 * investigation isn't mistaken for a fresh one. */
void dvi_display_clear_fault_log(void) {
    flash_range_erase(DVI_FAULT_LOG_FLASH_OFFSET, FLASH_SECTOR_SIZE);
}

/* 2026-10-23 real-hardware finding: libdvi's dvi_init() normally mallocs
 * DVI_N_TMDS_BUFFERS TMDS buffers from the separate pico_malloc/newlib
 * heap (NOT MicroPython's own GC heap) -- confirmed too small/nonexistent
 * on this board's MicroPython-custom linker script to satisfy even one
 * such malloc(), causing a real-hardware panic("TMDS buffer allocation
 * failed") (see micropython.cmake's matching comment for the full
 * writeup). Fixed by building with DVI_N_TMDS_BUFFERS=0 for dvi.c's own
 * compile unit (see micropython.cmake) -- its malloc() loop then does
 * zero iterations -- and supplying our own statically-allocated (BSS)
 * pool here instead, pushed onto dvi0.q_tmds_free ourselves right after
 * dvi_init() returns (see dvi_display_init() below). Sized for the one
 * timing this project actually drives (640x480p60, non-monochrome, 3
 * lanes): 3 * 640 / DVI_SYMBOLS_PER_WORD(2) * sizeof(uint32_t) = 3840
 * bytes per buffer, matching dvi.c's own original malloc() size exactly.
 * DVI_N_TMDS_BUFFERS here is dvi_display.c's own macro (defined above);
 * it never reaches dvi.c's own malloc loop (separate translation unit,
 * built with DVI_N_TMDS_BUFFERS=0). */
#define DVI_STATIC_TMDS_BUF_WORDS (3 * 640 / DVI_SYMBOLS_PER_WORD)
/* 2026-10-25, tried and refuted (see modlcd_controller.c's matching comment
 * on lcd_state): this array was briefly 64KB-bank-aligned alongside
 * lcd_state, hypothesizing CPU/DMA SRAM-bank contention between the two as
 * the cause of "No Signal". "No Signal" still occurred after that change --
 * the real cause was unrelated (see 調査用/
 * RP2350-PiZero_実機認識しない問題調査.md). Reverted to plain static. */
static uint32_t dvi_static_tmds_bufs[DVI_N_TMDS_BUFFERS][DVI_STATIC_TMDS_BUF_WORDS];

/* false until dvi_display_set_live_content(true) is called (see
 * mp/display_init.py's DVI branch, which does so right after lcd_c.init()
 * has run) -- keeps the Phase 1 static color-bar pattern as the default,
 * so mp/test/test_dvi_display.py (which never touches lcd_c at all) is
 * completely unaffected by this Phase 2a addition. */
static volatile bool dvi_live_content = false;

/* -----------------------------------------------------------------------
 * UI plane (live-content mode only). The physical-LCD boards draw the
 * bezel, status bar, FuncKeyBar, profile picker, setup menu and EMULATOR
 * MENU straight onto a 320x240 SPI panel, with the PB-1000 screen itself
 * rendered into one rectangle of it. This plane reproduces that panel for
 * DVI: a 320x240, 4-bit-per-pixel image (high nibble = even x) that the
 * Python display object (mp/display_init.py's DVI class) draws into via
 * dvi_display_ui_fill_rect()/dvi_display_ui_blit(), composited on top of
 * the live PB-1000 screen every scanline.
 *
 * Index 0 is transparent: the live PB-1000 screen shows through inside
 * the LCD window (dvi_display_set_lcd_window()), black elsewhere.
 * Indices 1..15 map to RGB565 through dvi_ui_pal, assigned on first use
 * of each color (nearest existing entry once all 15 are taken). A fill
 * covering the whole plane starts a fresh palette, so every full-screen
 * UI (menus, profile picker) gets all 15 entries to itself.
 *
 * The LCD window works like the physical panel's LCD rectangle: whatever
 * the UI draws there covers the PB-1000 screen (e.g. a menu), until the
 * PB-1000 screen is redrawn -- dvi_display_set_lcd_window() is called
 * from the same place the physical panel's LCD refresh happens, and makes
 * the window transparent again if anything was drawn over it since.
 * ----------------------------------------------------------------------- */
/* core1 must not execute from flash (see core1_dvi_main()): keep GCC from
 * turning fill loops into calls to the flash-resident memset(). */
#define DVI_NO_LIBCALLS __attribute__((optimize("no-tree-loop-distribute-patterns")))

#define DVI_UI_STRIDE (DVI_FB_W / 2)
static uint8_t dvi_ui_plane[DVI_FB_H][DVI_UI_STRIDE];
static uint16_t dvi_ui_pal[16];
static uint8_t dvi_ui_pal_used = 1;  /* entry 0 = transparent, never assigned */

/* LCD window (top-left + scale), defaults matching pb1000.py's own
 * _disp_x/_disp_y and the default 1.5x scale until Python sets it. */
static volatile int16_t dvi_lcd_x = 16;
static volatile int16_t dvi_lcd_y = 40;
static volatile uint8_t dvi_lcd_num = 3;
static volatile uint8_t dvi_lcd_den = 2;
static bool dvi_lcd_window_clear = false;  /* UI plane transparent over the window */

/* Scratch row for a PB-1000 screen wider than the plane (up to 3x scale). */
#define DVI_LCD_ROW_MAX (LCD_WIDTH * 3)
static uint16_t dvi_lcd_row[DVI_LCD_ROW_MAX];

static void DVI_NO_LIBCALLS __not_in_flash_func(dvi_compose_row)(int y, uint16_t *row) {
    const lcd_state_t *lcd = lcd_c_get_state();
    int num = dvi_lcd_num, den = dvi_lcd_den;
    int lx = dvi_lcd_x, ly = dvi_lcd_y;
    int out_w = (LCD_WIDTH * num) / den;
    int out_h = ((int)lcd->active_pages * 8 * num) / den;

    for (int x = 0; x < DVI_FB_W; x++) row[x] = DVI_BORDER_COLOR;
    if (y >= ly && y < ly + out_h && lx >= 0 && lx < DVI_FB_W) {
        if (lx + out_w <= DVI_FB_W) {
            lcd_render_row_rgb565(lcd, y - ly, out_w, out_h, (uint8_t)num, (uint8_t)den,
                                  DVI_BORDER_COLOR, row + lx);
        } else if (out_w <= DVI_LCD_ROW_MAX) {
            /* Wider than the plane (e.g. scale 2.0 = 384px): clip at the
             * right edge, like an SPI panel would. */
            lcd_render_row_rgb565(lcd, y - ly, out_w, out_h, (uint8_t)num, (uint8_t)den,
                                  DVI_BORDER_COLOR, dvi_lcd_row);
            for (int x = lx; x < DVI_FB_W; x++) row[x] = dvi_lcd_row[x - lx];
        }
    }

    const uint8_t *src = dvi_ui_plane[y];
    for (int i = 0; i < DVI_UI_STRIDE; i++) {
        uint8_t b = src[i];
        if (b) {
            if (b >> 4) row[2 * i] = dvi_ui_pal[b >> 4];
            if (b & 0x0F) row[2 * i + 1] = dvi_ui_pal[b & 0x0F];
        }
    }
}

static uint8_t dvi_ui_color_index(uint16_t c) {
    for (uint8_t i = 1; i < dvi_ui_pal_used; i++) {
        if (dvi_ui_pal[i] == c) return i;
    }
    if (dvi_ui_pal_used < 16) {
        dvi_ui_pal[dvi_ui_pal_used] = c;
        return dvi_ui_pal_used++;
    }
    int r = c >> 11, g = (c >> 5) & 0x3F, b = c & 0x1F;
    uint8_t best = 1;
    int best_d = 0x7FFFFFFF;
    for (uint8_t i = 1; i < 16; i++) {
        uint16_t p = dvi_ui_pal[i];
        int dr = (p >> 11) - r, dg = (int)((p >> 5) & 0x3F) - g, db = (p & 0x1F) - b;
        int d = 4 * dr * dr + dg * dg + 4 * db * db;
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    return best;
}

/* Clips (x, y, w, h) to the plane; returns false if nothing is left. */
static bool dvi_ui_clip(int *x, int *y, int *w, int *h) {
    if (*x < 0) { *w += *x; *x = 0; }
    if (*y < 0) { *h += *y; *y = 0; }
    if (*x + *w > DVI_FB_W) *w = DVI_FB_W - *x;
    if (*y + *h > DVI_FB_H) *h = DVI_FB_H - *y;
    return *w > 0 && *h > 0;
}

static void dvi_ui_fill_index(int x, int y, int w, int h, uint8_t idx) {
    for (int yy = y; yy < y + h; yy++) {
        uint8_t *line = dvi_ui_plane[yy];
        int xx = x;
        if (xx & 1) {
            line[xx >> 1] = (line[xx >> 1] & 0xF0) | idx;
            xx++;
        }
        int end = x + w;
        if (end - xx >= 2) {
            memset(&line[xx >> 1], (idx << 4) | idx, (size_t)((end - xx) >> 1));
            xx += (end - xx) & ~1;
        }
        if (xx < end) {
            line[xx >> 1] = (line[xx >> 1] & 0x0F) | (uint8_t)(idx << 4);
        }
    }
}

static void dvi_ui_touched(int x, int y, int w, int h) {
    int out_w = (LCD_WIDTH * dvi_lcd_num) / dvi_lcd_den;
    int out_h = ((int)lcd_c_get_state()->active_pages * 8 * dvi_lcd_num) / dvi_lcd_den;
    if (x < dvi_lcd_x + out_w && x + w > dvi_lcd_x &&
        y < dvi_lcd_y + out_h && y + h > dvi_lcd_y) {
        dvi_lcd_window_clear = false;
    }
}

/* -----------------------------------------------------------------------
 * dvi_scanline_pump -- the entire DVI "producer + encoder" in one
 * function, called from core1_dvi_main()'s loop once per free TMDS buffer
 * (i.e. once per unique source scanline), and preempted by the DVI DMA
 * IRQ. Fills dvi_row_buf with either live PB-1000 LCD content or a static
 * color-bar test pattern (8 vertical bars, recomputed from `y` only), then
 * TMDS-encodes it. Kept allocation-free and RAM-resident.
 * ----------------------------------------------------------------------- */
static void DVI_NO_LIBCALLS __not_in_flash_func(dvi_scanline_pump)(void) {
    uint32_t gen = dvi_frame_gen;
    uint y = dvi_pump_y;
    uint32_t t0 = timer_hw->timerawl;
    uint32_t *tmdsbuf; /* declared early so `goto encode_and_push` below
                         * only ever jumps over a statement, never a
                         * declaration (not valid C). */

    if (dvi_live_content) {
        /* Per-pixel work in lcd_render_row_rgb565() must stay minimal --
         * see that function's own comment in lcd_controller.c. */
        dvi_compose_row((int)y, dvi_row_buf);
        goto encode_and_push;
    }

    /* 8 vertical color bars: white/yellow/cyan/green/magenta/red/blue/black,
     * classic SMPTE-ish ordering, same RGB565 values regardless of row --
     * a simple, unambiguous way to confirm the signal (not just that
     * *something* is showing, but that the right pixel ends up in the
     * right column, confirming horizontal TMDS encoding) on a real screen
     * before any PB-1000 content is involved. */
    /* 2026-10-25 real-hardware finding: a `static const` array declared
     * inside a __not_in_flash_func() function does NOT inherit that
     * function's RAM placement -- the compiler still puts the array's
     * DATA in flash's .rodata by default (confirmed via the built ELF's
     * symbol table: bar_colors ended up at 0x10085f7e, flash's XIP
     * address range, while dvi_scanline_pump() itself and
     * dvi_timing_640x480p_60hz correctly landed in RAM at 0x2000xxxx).
     * Reading this table from flash on every single scanline, from
     * inside this exact hot path, is precisely the "snow" hazard
     * this file's own header comment and the sibling MSX project's
     * near-identical dvi_scanline_pump() comment both warn about:
     * MicroPython's own flash-resident bytecode interpreter running on
     * core0 contends for the same shared XIP bus, corrupting this
     * table's fetches -- confirmed on real hardware (color-bar test
     * pattern displayed correctly at first, then degraded into "snow"
     * after ~8s; see 調査用/RP2350-PiZero_実機認識しない問題調査.md).
     * Fixed the same way dvi_timing.c's own const timing structs are --
     * __not_in_flash_func() applied directly to the variable name pulls
     * its data into RAM (.time_critical section) instead of .rodata. */
    static const uint16_t __not_in_flash_func(bar_colors)[8] = {
        0xFFFF, /* white */
        0xFFE0, /* yellow */
        0x07FF, /* cyan */
        0x07E0, /* green */
        0xF81F, /* magenta */
        0xF800, /* red */
        0x001F, /* blue */
        0x0000, /* black */
    };
    int bar_w = DVI_FB_W / 8;
    for (int x = 0; x < DVI_FB_W; x++) {
        int bar = x / bar_w;
        if (bar > 7) bar = 7;
        dvi_row_buf[x] = bar_colors[bar];
    }
    /* Thin horizontal line across the vertical middle, so a capture can
     * also confirm vertical positioning/scaling is centered correctly. */
    if (y == DVI_FB_H / 2) {
        for (int x = 0; x < DVI_FB_W; x++) dvi_row_buf[x] = 0xFFFF;
    }

encode_and_push:
    if (queue_try_remove_u32(&dvi0.q_tmds_free, &tmdsbuf)) {
        const uint32_t *scanbuf = (const uint32_t *)dvi_row_buf;
        uint pixwidth = dvi0.timing->h_active_pixels;              /* 640 */
        uint words_per_channel = pixwidth / DVI_SYMBOLS_PER_WORD;  /* 320 */
        tmds_encode_data_channel_16bpp(scanbuf, tmdsbuf + 0 * words_per_channel, pixwidth / 2, DVI_16BPP_BLUE_MSB,  DVI_16BPP_BLUE_LSB );
        tmds_encode_data_channel_16bpp(scanbuf, tmdsbuf + 1 * words_per_channel, pixwidth / 2, DVI_16BPP_GREEN_MSB, DVI_16BPP_GREEN_LSB);
        tmds_encode_data_channel_16bpp(scanbuf, tmdsbuf + 2 * words_per_channel, pixwidth / 2, DVI_16BPP_RED_MSB,   DVI_16BPP_RED_LSB  );
        /* Publish the row and advance y as one step with respect to
         * dvi_frame_sync() (DMA IRQ, same core). If it realigned the frame
         * while this row was being made, the row belongs to the old
         * alignment: hand the buffer back instead. */
        uint32_t irq = save_and_disable_interrupts();
        if (gen == dvi_frame_gen) {
            queue_try_add_u32(&dvi0.q_tmds_valid, &tmdsbuf);
            dvi_pump_y = (y + 1 == DVI_FB_H) ? 0 : (y + 1);
        } else {
            queue_try_add_u32(&dvi0.q_tmds_free, &tmdsbuf);
        }
        restore_interrupts(irq);
    }
    /* If no free TMDS buffer was available, the row is retried on the next
     * call (dvi_dma_irq_handler() shows its own solid-red "no valid
     * scanline" pattern meanwhile) -- should not normally happen once the
     * startup pre-fill (see dvi_display_init()) has run. */

    dvi_heartbeat++;
    uint32_t dt = timer_hw->timerawl - t0;
    if (dt > dvi_pump_max_us) dvi_pump_max_us = dt;
}

/* scanline_callback, called from libdvi's DMA IRQ on every second active
 * line. On the last active line, every row of the current frame has been
 * taken, so the queue must hold exactly rows 0..dvi_pump_y-1 of the next
 * frame. Otherwise (the producer missed a row's deadline, and libdvi's
 * late-scanline handling dropped or skipped rows), rows would be shown at
 * the wrong height from then on, and keep drifting with every further
 * miss -- the picture appears to scroll vertically. Discard the queued
 * rows and restart the producer at row 0, so a miss costs at most one
 * frame. */
static void __not_in_flash_func(dvi_frame_sync)(void) {
    if (dvi0.timing_state.v_ctr != dvi0.timing->v_active_lines - 1) return;
    dvi0.late_scanline_ctr = 0;
    if (_queue_get_level_u32(&dvi0.q_tmds_valid) == dvi_pump_y) return;
    uint32_t *buf;
    while (queue_try_remove_u32(&dvi0.q_tmds_valid, &buf))
        queue_try_add_u32(&dvi0.q_tmds_free, &buf);
    dvi_pump_y = 0;
    dvi_frame_gen++;
    dvi_resync_count++;
}

/* Set by core1 once dvi_start() has run -- see dvi_display_init(). */
static volatile bool dvi_core1_started = false;

static __force_inline bool dvi_tmds_free_empty(void) {
    return dvi0.q_tmds_free.wptr == dvi0.q_tmds_free.rptr;
}

static void __not_in_flash_func(core1_dvi_main)(void) {
    /* core1 is deliberately NOT a multicore_lockout victim: everything it
     * runs from here on (this loop, dvi_scanline_pump() and what it calls,
     * libdvi's DMA IRQ handler, tmds_encode) is RAM-resident, so core0 can
     * erase/program flash (filesystem writes) while DVI keeps running.
     * Pausing core1 for a flash write instead stops the DMA IRQ past
     * libdvi's per-line deadline, which desynchronises the TMDS lanes'
     * DMA chains permanently. Anything added to core1's path must stay
     * out of flash (check with arm-none-eabi-objdump: no branches or
     * literals into 0x10xxxxxx). */
    dvi_register_irqs_this_core(&dvi0, DMA_IRQ_1);
    dvi_start(&dvi0);
    dvi_core1_started = true;
    /* Scanline producer (see this file's header comment): refill each
     * TMDS buffer as soon as the DMA IRQ hands it back. queue_try_add_u32()
     * in the IRQ signals an event (SEV), which wakes __wfe(). */
    while (true) {
        while (dvi_tmds_free_empty())
            __wfe();
        dvi_scanline_pump();
    }
}

bool dvi_display_init(void) {
    /* Voltage/clock: confirmed locking correctly on the sibling project's
     * real hardware via its own color-bar bring-up test. */
    vreg_set_voltage(VREG_VOLTAGE_1_20);
    sleep_ms(10);
    if (!set_sys_clock_khz(dvi_timing_640x480p_60hz.bit_clk_khz, true)) {
        return false;
    }

    /* RP2350B: PIO0 must be told which 32-pin GPIO window to address
     * before dvi_serialiser_init() configures pins 32-39 (see header
     * comment / hardware/pio.h's PICO_PIO_USE_GPIO_BASE doc). */
    pio_set_gpio_base(pizero_dvi_cfg.pio, 16);

    /* spin_lock_claim_unused() (not next_striped_spin_lock_num()) --
     * the sibling project found a striped spinlock collision (shared
     * with MicroPython's own internal critical sections, e.g.
     * gc.collect()) visibly corrupted the picture ("snow") for its
     * duration on real hardware. Claiming from the dedicated-use range
     * instead guarantees nothing else in the firmware can also be handed
     * the same one. */
    dvi0.timing = &dvi_timing_640x480p_60hz;
    dvi0.ser_cfg = pizero_dvi_cfg;
    /* Rows are produced by core1_dvi_main(), not the IRQ; the callback
     * only keeps them aligned with the frame. */
    dvi0.scanline_callback = dvi_frame_sync;
    dvi_init(&dvi0, (uint)spin_lock_claim_unused(true), (uint)spin_lock_claim_unused(true));

    /* Supply our own statically-allocated TMDS buffers (see
     * dvi_static_tmds_bufs's comment above) -- dvi.c's own malloc() loop
     * inside dvi_init() just ran above with DVI_N_TMDS_BUFFERS=0 (set for
     * its compile unit only, via micropython.cmake), so q_tmds_free is
     * still empty at this point; this is the direct replacement for that
     * skipped loop, pushing the exact same way dvi.c's own version does
     * (queue_add_blocking_u32() of a local void* holding the buffer
     * address -- never blocks since q_tmds_free's queue depth, fixed at
     * 8 by dvi_init() above, is at least DVI_N_TMDS_BUFFERS here). */
    for (int i = 0; i < DVI_N_TMDS_BUFFERS; i++) {
        void *tmdsbuf = dvi_static_tmds_bufs[i];
        queue_add_blocking_u32(&dvi0.q_tmds_free, &tmdsbuf);
    }

    /* Mandatory startup pre-fill -- see this file's header comment. Must
     * happen before multicore_launch_core1()/dvi_start(), i.e. before the
     * first DMA IRQ can possibly fire. */
    for (int i = 0; i < DVI_N_TMDS_BUFFERS; i++) {
        dvi_scanline_pump();
    }

    multicore_launch_core1(core1_dvi_main);
    /* core1 runs its (flash-resident) startup calls above before reaching
     * RAM-only code; don't return -- and let core0 write flash -- until
     * it has. */
    while (!dvi_core1_started)
        tight_loop_contents();
    return true;
}

void dvi_display_debug_info(uint32_t *heartbeat, uint32_t *late_ctr) {
    *heartbeat = dvi_heartbeat;
    *late_ctr = dvi0.late_scanline_ctr;
}

void dvi_display_timing_info(uint32_t *resyncs, uint32_t *pump_max_us) {
    *resyncs = dvi_resync_count;
    *pump_max_us = dvi_pump_max_us;
    dvi_pump_max_us = 0;
}

void dvi_display_set_live_content(bool enable) {
    dvi_live_content = enable;
}

void dvi_display_pio_debug_info(uint32_t *fdebug) {
    *fdebug = pizero_dvi_cfg.pio->fdebug;
}

void dvi_display_ui_fill_rect(int x, int y, int w, int h, uint16_t color) {
    if (!dvi_ui_clip(&x, &y, &w, &h)) return;
    if (w == DVI_FB_W && h == DVI_FB_H) dvi_ui_pal_used = 1;
    dvi_ui_fill_index(x, y, w, h, dvi_ui_color_index(color));
    dvi_ui_touched(x, y, w, h);
}

void dvi_display_ui_blit(int x0, int y0, int x1, int y1, const uint8_t *data, size_t len) {
    int w = x1 - x0 + 1;
    int h = y1 - y0 + 1;
    if (w <= 0 || h <= 0) return;
    size_t n = len / 2;
    if (n > (size_t)w * (size_t)h) n = (size_t)w * (size_t)h;
    uint16_t last_c = 0;
    uint8_t last_idx = 0;
    bool have_last = false;
    for (size_t i = 0; i < n; i++) {
        int px = x0 + (int)(i % (size_t)w);
        int py = y0 + (int)(i / (size_t)w);
        if (px < 0 || px >= DVI_FB_W || py < 0 || py >= DVI_FB_H) continue;
        /* Same byte order as the SPI panels' write_data(): big-endian RGB565. */
        uint16_t c = (uint16_t)((data[2 * i] << 8) | data[2 * i + 1]);
        if (!have_last || c != last_c) {
            last_idx = dvi_ui_color_index(c);
            last_c = c;
            have_last = true;
        }
        uint8_t *p = &dvi_ui_plane[py][px >> 1];
        if (px & 1) *p = (*p & 0xF0) | last_idx;
        else        *p = (*p & 0x0F) | (uint8_t)(last_idx << 4);
    }
    dvi_ui_touched(x0, y0, w, h);
}

void dvi_display_set_lcd_window(int x, int y, int scale_num, int scale_den) {
    if (scale_num < 1 || scale_den < 1) return;
    bool moved = x != dvi_lcd_x || y != dvi_lcd_y ||
                 scale_num != dvi_lcd_num || scale_den != dvi_lcd_den;
    dvi_lcd_x = (int16_t)x;
    dvi_lcd_y = (int16_t)y;
    dvi_lcd_num = (uint8_t)scale_num;
    dvi_lcd_den = (uint8_t)scale_den;
    if (moved || !dvi_lcd_window_clear) {
        int wx = x, wy = y;
        int ww = (LCD_WIDTH * scale_num) / scale_den;
        int wh = ((int)lcd_c_get_state()->active_pages * 8 * scale_num) / scale_den;
        if (dvi_ui_clip(&wx, &wy, &ww, &wh)) dvi_ui_fill_index(wx, wy, ww, wh, 0);
        dvi_lcd_window_clear = true;
    }
}

#else /* !PB1000_BOARD_PIZERO */

bool dvi_display_init(void) { return false; }
void dvi_display_debug_info(uint32_t *heartbeat, uint32_t *late_ctr) {
    *heartbeat = 0;
    *late_ctr = 0;
}
void dvi_display_timing_info(uint32_t *resyncs, uint32_t *pump_max_us) {
    *resyncs = 0;
    *pump_max_us = 0;
}
void dvi_display_set_live_content(bool enable) { (void)enable; }
void dvi_display_fault_info(uint32_t *occurred, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr) {
    *occurred = 0;
    *cfsr = 0;
    *bfar = 0;
    *hfsr = 0;
}

void dvi_display_read_fault_log(uint32_t *magic_ok, uint32_t *cfsr, uint32_t *bfar, uint32_t *hfsr, uint32_t *pc, uint32_t *lr, uint32_t *core_num) {
    *magic_ok = 0;
    *cfsr = 0;
    *bfar = 0;
    *hfsr = 0;
    *pc = 0;
    *lr = 0;
    *core_num = 0;
}

void dvi_display_clear_fault_log(void) {
}

void dvi_display_pio_debug_info(uint32_t *fdebug) {
    *fdebug = 0;
}

void dvi_display_ui_fill_rect(int x, int y, int w, int h, uint16_t color) {
    (void)x; (void)y; (void)w; (void)h; (void)color;
}

void dvi_display_ui_blit(int x0, int y0, int x1, int y1, const uint8_t *data, size_t len) {
    (void)x0; (void)y0; (void)x1; (void)y1; (void)data; (void)len;
}

void dvi_display_set_lcd_window(int x, int y, int scale_num, int scale_den) {
    (void)x; (void)y; (void)scale_num; (void)scale_den;
}

#endif /* PB1000_BOARD_PIZERO */
