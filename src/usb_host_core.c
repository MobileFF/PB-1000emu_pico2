#include "usb_host_core.h"
#include "py/mpprint.h"
#include "tusb.h"
#include "host/hcd.h"
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include <string.h>

#ifdef PB1000_USE_PIO_USB
#include "hardware/timer.h"
#include "hardware/pio.h"
#include "pio_usb.h"
#include "pio_usb_ll.h"
#endif

// Helpers for logging to MicroPython REPL
#define TRACE(str) mp_printf(&mp_plat_print, str "\n")
#define DEBUG_PRINTF(...) mp_printf(&mp_plat_print, __VA_ARGS__)

/* External: C keyboard event processing from modhd61700.c */
extern void c_kb_process_usb_key_extern(uint8_t scancode, bool pressed);

// Custom allocator for TinyUSB (isolates it from SDK heap limits)
#define USB_HOST_HEAP_SIZE (64 * 1024)
static uint8_t usb_host_heap[USB_HOST_HEAP_SIZE] __attribute__((aligned(8)));
static size_t usb_host_heap_pos = 0;

/* Background timer for tuh_task() */
static struct repeating_timer usb_bg_timer;
static bool usb_bg_timer_active = false;

static bool usb_bg_timer_callback(struct repeating_timer *t) {
  (void)t;
  tuh_task();
  return true; /* keep repeating */
}

// TinyUSB debug string helpers
char const *const tu_str_speed[] = {"Full", "Low", "High", "Unknown"};
char const *const tu_str_xfer_result[] = {"OK", "FAIL", "STALL", "ERROR"};
char const *const tu_str_std_request[] = {
    "GET_STATUS",        "CLEAR_FEATURE",  "Reserved",
    "SET_FEATURE",       "Reserved",       "SET_ADDRESS",
    "GET_DESCRIPTOR",    "SET_DESCRIPTOR", "GET_CONFIGURATION",
    "SET_CONFIGURATION", "GET_INTERFACE",  "SET_INTERFACE",
    "SYNCH_FRAME"};

static void *tu_malloc(size_t size) {
  size = (size + 7) & ~7;
  if (usb_host_heap_pos + size > USB_HOST_HEAP_SIZE) return NULL;
  void *p = &usb_host_heap[usb_host_heap_pos];
  usb_host_heap_pos += size;
  return p;
}

static void *tu_calloc(size_t nmemb, size_t size) {
  size_t total = nmemb * size;
  void *p = tu_malloc(total);
  if (p) memset(p, 0, total);
  return p;
}

static void *tu_realloc(void *ptr, size_t size) {
  if (!ptr) return tu_malloc(size);
  void *q = tu_malloc(size);
  if (q) memcpy(q, ptr, size);
  return q;
}

static void tu_free(void *ptr) { (void)ptr; }


// TinyUSB Event Hooks
// NOTE: These callbacks run from the bg_timer alarm ISR context.
//       Do NOT call mp_printf here — it is not ISR-safe on Pico 2W
//       and causes intermittent hangs due to stdout conflicts.
void tuh_event_hook_cb(uint8_t rhport, uint32_t eventid, bool in_isr) {
  (void)rhport; (void)eventid; (void)in_isr;
}

/* Diagnostics for usb_host.dev_info()/hid_info()/hid_reports(): what the
 * attached device presented and the raw HID reports it sends, so a
 * keyboard that mounts but produces no keys can be told apart from one
 * that never mounts. Only a keyboard interface (protocol 1) sending 8-byte
 * reports reaches c_kb_process_usb_key_extern() below. */
#define DIAG_DESC_MAX USB_HOST_DIAG_DESC_MAX
#define DIAG_REPORT_LOG 16
#define DIAG_REPORT_BYTES USB_HOST_DIAG_REPORT_BYTES

typedef struct {
  uint8_t used, dev_addr, itf_protocol, protocol_mode;
  uint16_t desc_len;
  uint32_t report_count;
  uint8_t desc[DIAG_DESC_MAX];
} diag_hid_itf_t;

typedef struct {
  uint8_t instance, len;
  uint8_t data[DIAG_REPORT_BYTES];
} diag_report_t;

static uint32_t diag_mount_count, diag_umount_count;
static uint16_t diag_vid, diag_pid;
static diag_hid_itf_t diag_hid[CFG_TUH_HID];
static diag_report_t diag_reports[DIAG_REPORT_LOG];
static uint32_t diag_report_seq;

void tuh_mount_cb(uint8_t dev_addr) {
  diag_mount_count++;
  tuh_vid_pid_get(dev_addr, &diag_vid, &diag_pid);
}

void tuh_umount_cb(uint8_t dev_addr) {
  (void)dev_addr;
  diag_umount_count++;
}

void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *desc_report, uint16_t desc_len) {
  if (instance < CFG_TUH_HID) {
    diag_hid_itf_t *d = &diag_hid[instance];
    d->used = 1;
    d->dev_addr = dev_addr;
    d->itf_protocol = tuh_hid_interface_protocol(dev_addr, instance);
    d->protocol_mode = tuh_hid_get_protocol(dev_addr, instance);
    d->desc_len = desc_len;
    d->report_count = 0;
    memcpy(d->desc, desc_report, desc_len < DIAG_DESC_MAX ? desc_len : DIAG_DESC_MAX);
  }
  tuh_hid_receive_report(dev_addr, instance);
}

void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
  (void)dev_addr;
  if (instance < CFG_TUH_HID) diag_hid[instance].used = 0;
}

static void diag_log_report(uint8_t instance, uint8_t const *report, uint16_t len) {
  if (instance < CFG_TUH_HID) diag_hid[instance].report_count++;
  diag_report_t *r = &diag_reports[diag_report_seq % DIAG_REPORT_LOG];
  r->instance = instance;
  r->len = len > 255 ? 255 : (uint8_t)len;
  memcpy(r->data, report, len < DIAG_REPORT_BYTES ? len : DIAG_REPORT_BYTES);
  diag_report_seq++;
}

void usb_host_core_diag_dev(uint32_t *mount, uint32_t *umount, uint16_t *vid, uint16_t *pid) {
  *mount = diag_mount_count;
  *umount = diag_umount_count;
  *vid = diag_vid;
  *pid = diag_pid;
}

/* Copies interface `idx` (0..CFG_TUH_HID-1); returns false if unused. */
bool usb_host_core_diag_hid(uint8_t idx, uint8_t *dev_addr, uint8_t *itf_protocol,
                            uint8_t *protocol_mode, uint16_t *desc_len,
                            uint32_t *report_count, uint8_t *desc, uint16_t *desc_copied) {
  if (idx >= CFG_TUH_HID || !diag_hid[idx].used) return false;
  diag_hid_itf_t *d = &diag_hid[idx];
  *dev_addr = d->dev_addr;
  *itf_protocol = d->itf_protocol;
  *protocol_mode = d->protocol_mode;
  *desc_len = d->desc_len;
  *report_count = d->report_count;
  *desc_copied = d->desc_len < DIAG_DESC_MAX ? d->desc_len : DIAG_DESC_MAX;
  memcpy(desc, d->desc, *desc_copied);
  return true;
}

/* Copies report `seq` (a running sequence number) if still in the log. */
bool usb_host_core_diag_report(uint32_t seq, uint8_t *instance, uint8_t *len, uint8_t *data) {
  if (seq >= diag_report_seq || diag_report_seq - seq > DIAG_REPORT_LOG) return false;
  diag_report_t *r = &diag_reports[seq % DIAG_REPORT_LOG];
  *instance = r->instance;
  *len = r->len;
  memcpy(data, r->data, DIAG_REPORT_BYTES);
  return true;
}

uint32_t usb_host_core_diag_report_seq(void) {
  return diag_report_seq;
}

static hid_keyboard_report_t prev_report = {0};

void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
  diag_log_report(instance, report, len);
  if (tuh_hid_interface_protocol(dev_addr, instance) == HID_ITF_PROTOCOL_KEYBOARD &&
      len == sizeof(hid_keyboard_report_t)) {
    hid_keyboard_report_t const *kbd_report = (hid_keyboard_report_t const *)report;

    // Process Modifiers
    uint8_t changed_mod = kbd_report->modifier ^ prev_report.modifier;
    for (int i = 0; i < 8; i++) {
      if (changed_mod & (1 << i)) c_kb_process_usb_key_extern(0xE0 + i, (kbd_report->modifier >> i) & 1);
    }

    // Process Keypresses
    for (int i = 0; i < 6; i++) {
        uint8_t key = kbd_report->keycode[i];
        if (key) {
            bool is_new = true;
            for (int j = 0; j < 6; j++) if (key == prev_report.keycode[j]) { is_new = false; break; }
            if (is_new) c_kb_process_usb_key_extern(key, true);
        }
    }

    // Process Releases
    for (int i = 0; i < 6; i++) {
        uint8_t key = prev_report.keycode[i];
        if (key) {
            bool released = true;
            for (int j = 0; j < 6; j++) if (key == kbd_report->keycode[j]) { released = false; break; }
            if (released) c_kb_process_usb_key_extern(key, false);
        }
    }
    prev_report = *kbd_report;
  }
  tuh_hid_receive_report(dev_addr, instance);
}

#ifdef PB1000_USE_PIO_USB
/* Drives pio_usb's 1ms SOF/keepalive/transfer tick ourselves, via a raw
 * hardware_alarm_* one-shot alarm rather than letting the library (or
 * pico_time's alarm_pool API) create its own timer — see
 * src/usb_host/pio_usb/PROVENANCE.md's "skip_alarm_pool" entry for why
 * this is mandatory, not optional hardening: pio_usb_host_init()
 * defaults to alarm_pool_create(2, 1) for this, which collides with
 * MicroPython's rp2 port permanently claiming hardware alarm #2 for its
 * own soft-timer/scheduler at boot (a hard crash, confirmed on a sibling
 * RP2350 MicroPython project's real hardware).
 *
 * This handler deliberately does ONLY pio_usb_host_frame() (the one
 * thing pio_usb's own docs say must happen every 1ms) — NOT tuh_task(),
 * which stays on the existing repeating-timer poll
 * (usb_host_core_start_bg_timer(), ~60Hz is plenty for keyboard
 * responsiveness and keeps enumeration/control-transfer processing out
 * of a 1kHz ISR). */
static volatile uint32_t pio_usb_sof_tick_count = 0;
static int pio_usb_sof_alarm_num = -1;
static absolute_time_t pio_usb_sof_next;

/* The next frame is timed from the previous target, not from the end of
 * this one, so SOFs stay exactly 1ms apart however long the frame's
 * transactions took. Devices without a crystal trim their clock to the SOF
 * interval, and a hub disables a port whose device answers inside the end
 * of the frame it expects from the SOF interval. The alarm therefore fires
 * PIO_USB_SOF_LEAD_US early and the handler waits for the exact
 * microsecond, which absorbs interrupt latency. */
#define PIO_USB_SOF_LEAD_US 20

static void __not_in_flash_func(pio_usb_sof_alarm_handler)(uint alarm_num) {
  while (!time_reached(pio_usb_sof_next)) {
    tight_loop_contents();
  }
  pio_usb_sof_tick_count++;
  pio_usb_host_frame();
  pio_usb_sof_next = delayed_by_us(pio_usb_sof_next, 1000);
  if (hardware_alarm_set_target(alarm_num,
                                from_us_since_boot(to_us_since_boot(pio_usb_sof_next) - PIO_USB_SOF_LEAD_US))) {
    /* Target already passed: restart the cadence from now. */
    pio_usb_sof_next = delayed_by_us(get_absolute_time(), 1000);
    hardware_alarm_set_target(alarm_num,
                              from_us_since_boot(to_us_since_boot(pio_usb_sof_next) - PIO_USB_SOF_LEAD_US));
  }
}

/* PIO-USB host init (GP4/GP5 by default) — see
 * src/usb_host/pio_usb/PROVENANCE.md and
 * src/usb_host/hcd_pio_usb_pb1000.c's header comment for the full
 * rationale and the facts this relies on. Built only when USE_PIO_USB
 * is ON (src/micropython.cmake); the native-controller path below is
 * used otherwise and is unchanged.
 *
 * PB1000_PIO_USB_DP_PIN: 2026-10-21, added for the Waveshare RP2350-
 * PiZero port (doc/hardware_guide.md). That board's USB-host port is
 * wired to GP28/29 (D-=D+ +1, same convention), not GP4/GP5 -- its
 * native/Type-C USB controller is permanently a different port used for
 * BOOTSEL/programming and (with USE_PIO_USB=ON) CDC REPL, and cannot be
 * repurposed as a keyboard host (confirmed against the vendor schematic
 * by the sibling MSX_emu_pico2 project's own identical board). Defaults
 * to 4 (GP4) for every other board, unchanged from before this macro
 * existed. bldfrm.sh's pizero target passes -DPB1000_PIO_USB_DP_PIN=28
 * via CFLAGS_EXTRA (same QSTR-extraction-pre-pass reasoning as
 * PB1000_BOARD_PIZERO -- see micropython.cmake's PB1000_IS_PIZERO
 * block). */
#ifndef PB1000_PIO_USB_DP_PIN
#define PB1000_PIO_USB_DP_PIN 4
#endif

/* 2026-10-23: which TinyUSB rhport number the PIO-USB host lives on.
 *
 * Pico 2 (W): rhport 1, as before -- this board's main firmware uses a
 * single, UNIFIED TinyUSB build (micropython.cmake sets
 * CFG_TUSB_RHPORT0_MODE=DEVICE + CFG_TUSB_RHPORT1_MODE=HOST globally,
 * reaching MicroPython's own CDC/device code too), real-hardware
 * confirmed working.
 *
 * Waveshare RP2350-PiZero: rhport 0. Real-hardware bring-up found that
 * the SAME unified dual-rhport (DEVICE+HOST) global declaration hangs
 * this board completely before any UART/USB output -- reproduced with
 * tuh_init() itself never even called (USB_HOST_SKIP_INIT), DVI
 * excluded, and mp/*.py freezing excluded, isolating the global
 * RHPORT0=DEVICE + RHPORT1=HOST combination itself (not any runtime
 * call) as the cause. Fix: for this board only, micropython.cmake no
 * longer declares CFG_TUSB_RHPORT0_MODE/CFG_TUSB_RHPORT1_MODE/
 * CFG_TUH_ENABLED at the global scope at all -- the main firmware's own
 * CDC build falls back to TinyUSB's stock default (RHPORT0=DEVICE, the
 * only rhport it knows about), and the PIO-USB host gets its own fully
 * SEPARATE, isolated TinyUSB build instead, scoped PRIVATE to
 * usb_host_core_lib via usb_host/tusb_config.h (-include'd there only --
 * see that header's CFG_TUSB_RHPORT0_MODE=(OPT_MODE_HOST|...) fallback,
 * which now actually takes effect for this board since nothing global
 * pre-empts it).
 *
 * 2026-10-30: rhport value changed from 0 to 1 to EXACTLY match the
 * sibling MSX_emu_pico2 project's own pizero usb_host_core.c (confirmed
 * working on real hardware there, including the just-completed decisive
 * test of flashing MSX's own firmware onto this exact board/keyboard --
 * see 調査用/RP2350-PiZero_実機認識しない問題調査.md). The previous
 * 2026-10-23 comment's reasoning ("isolated host build is the only
 * TinyUSB-host code in that compile unit, so it uses rhport 0") was this
 * project's own assumption, not something MSX's working implementation
 * actually relies on: MSX's own private usb_host/tusb_config.h (same
 * isolated-library pattern) ALSO only defines CFG_TUSB_RHPORT0_MODE
 * (never CFG_TUSB_RHPORT1_MODE) and yet calls tuh_configure(1, ...)/
 * tuh_init(1) successfully on real hardware -- proof that TinyUSB's
 * host core doesn't require a RHPORTx_MODE macro matching the specific
 * rhport number passed to a virtual (non-native-silicon) HCD like
 * pio_usb, as long as CFG_TUH_ENABLED=1. The actual 2026-10-23 fix that
 * mattered was removing the GLOBAL-scope CFG_TUH_ENABLED/RHPORTx_MODE
 * declarations that leaked into MicroPython's own main-firmware CDC
 * build (see micropython.cmake's PB1000_IS_PIZERO comment) -- MSX's own
 * micropython_msx.cmake never declared those globally for ANY board in
 * the first place, so it never needed this board-specific rhport-number
 * workaround at all. Aligning the number itself to 1 removes one more
 * difference from the proven-working reference while keeping the
 * isolation fix (no global TinyUSB macros) intact.
 *
 * 2026-10-31: REVERTED back to 0 for pizero. The 2026-10-30 change above
 * (0->1) turned out to correlate with this board's native USB CDC (the
 * REPL/BOOTSEL port) never enumerating at all after a clean rebuild+
 * reflash -- no ttyACM device, no dmesg USB activity whatsoever, even
 * though usb_host_core_init()/tuh_init() are only ever reached from
 * Python's explicit usb_host.init() call (never automatically at boot),
 * so this is suspicious on its face but was the only change in this
 * investigation window and reverting it is the cheapest way to test
 * whether it's responsible. See
 * 調査用/RP2350-PiZero_実機認識しない問題調査.md for the real-hardware
 * test this is part of -- NOT yet confirmed as the actual root cause,
 * just the first thing being ruled in/out after the regression was
 * noticed. */
#ifdef PB1000_BOARD_PIZERO
#define PB1000_PIO_USB_RHPORT 0
#else
#define PB1000_PIO_USB_RHPORT 1
#endif

static void usb_host_core_init_pio_usb(void) {
  pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
  pio_cfg.pin_dp = PB1000_PIO_USB_DP_PIN;  /* D- = +1, same convention regardless of board */
#ifdef PB1000_BOARD_PIZERO
  /* pio1, not pio0: this board's onboard DVI output (dvi_display.c's
     pizero_dvi_cfg) occupies pio0. A PIO block's IRQ flags and
     instruction memory are shared across all 4 of its state machines,
     not per-SM, so running PIO-USB on pio0 alongside DVI would risk a
     real conflict the same way Pico 2's own pio0/pio1 split below
     avoids one with mp/pio_uart.py, which uses pio2 on this board
     instead (see mp/main_boot.py). */
  pio_cfg.pio_tx_num = 1;
  pio_cfg.sm_tx = 0;
  pio_cfg.pio_rx_num = 1;
  pio_cfg.sm_rx = 1;
#else
  /* pio0, not pio1: mp/pio_uart.py's RS-232C soft-UART uses pio1 (SM6/7).
     A PIO block's IRQ flags and instruction memory are shared across all
     4 of its state machines, not per-SM, so running PIO-USB on pio1
     alongside PioUart risks real conflicts -- confirmed on real hardware
     (full emulator boot hung right after "PIO UART support enabled in
     system."; the standalone bring-up test never loads pio_uart.py, so
     this never showed up there). PIO-USB's own keyboard function was
     confirmed working equally on pio0 and pio1 during bring-up (the
     actual fix for that was the D+/D- wiring, not the PIO block), so
     pio0 is safe and avoids this conflict entirely. */
  pio_cfg.pio_tx_num = 0;
  pio_cfg.sm_tx = 0;
  pio_cfg.pio_rx_num = 0;
  pio_cfg.sm_rx = 1;
#endif
  pio_cfg.sm_eop = 2;
  pio_cfg.tx_ch = 15;      /* Highest DMA channel (RP2350 has 0-15),
                            * claimed via dma_claim_mask() inside
                            * pio_usb.c — picked defensively far from
                            * whatever channel number any future DMA
                            * user in this project claims first. Ruled
                            * out as the TXSTALL cause (2026-10-29):
                            * switching to channel 2 reproduced the
                            * identical permanent TXSTALL, so the DMA
                            * channel number itself isn't the problem --
                            * see 調査用/RP2350-PiZero_実機認識しない問題調査.md. */
  pio_cfg.skip_alarm_pool = true;  /* mandatory -- see this function's header comment */

  if (!tuh_configure(PB1000_PIO_USB_RHPORT, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &pio_cfg)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_configure (PIO-USB) failed!\n");
    return;
  }
  if (!tuh_init(PB1000_PIO_USB_RHPORT)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_init failed!\n");
    return;
  }

  pio_usb_sof_alarm_num = hardware_alarm_claim_unused(true);
  hardware_alarm_set_callback(pio_usb_sof_alarm_num, pio_usb_sof_alarm_handler);
  pio_usb_sof_next = delayed_by_us(get_absolute_time(), 1000);
  hardware_alarm_set_target(pio_usb_sof_alarm_num,
                            from_us_since_boot(to_us_since_boot(pio_usb_sof_next) - PIO_USB_SOF_LEAD_US));
}

/* Real-hardware bring-up diagnostic: lets Python poll the raw PIO-USB
 * root-port state directly, below TinyUSB's own enumeration layer --
 * i.e. "is the SOF alarm actually firing, and does the hardware see a
 * device attached at all" even before/regardless of whether a HID
 * report ever arrives. Exposed as usb_host.debug(). Kept as a
 * permanent, low-cost tool rather than removed after bring-up, matching
 * the sibling project this was ported from. */
extern void hcd_pio_usb_pb1000_get_event_counts(uint32_t *attach, uint32_t *detach,
                                                 uint32_t *ep_complete,
                                                 uint32_t *ep_stalled,
                                                 uint32_t *ep_error);
extern void hcd_pio_usb_pb1000_get_last_ep(uint32_t *last_complete_ep,
                                            uint32_t *last_error_ep);
extern void hcd_pio_usb_pb1000_get_setup_info(uint32_t *setup_send_count,
                                               uint32_t *last_setup_word,
                                               uint32_t *port_reset_count,
                                               uint32_t *port_reset_end_count);
extern uint32_t hcd_pio_usb_pb1000_get_is_fullspeed(void);
extern void hcd_pio_usb_pb1000_get_event_history(uint32_t *count,
                                                   uint32_t *out,
                                                   uint32_t out_len);

void usb_host_core_debug_pio_usb(uint32_t *tick_count, uint32_t *connected,
                                  uint32_t *suspended, uint32_t *ints,
                                  uint32_t *attach, uint32_t *detach,
                                  uint32_t *ep_complete, uint32_t *ep_stalled,
                                  uint32_t *ep_error) {
  *tick_count = pio_usb_sof_tick_count;
  root_port_t *root = PIO_USB_ROOT_PORT(0);
  *connected = root->connected;
  *suspended = root->suspended;
  *ints = root->ints;
  hcd_pio_usb_pb1000_get_event_counts(attach, detach, ep_complete, ep_stalled, ep_error);
}

void usb_host_core_debug_pio_usb_ep(uint32_t *last_complete_ep,
                                     uint32_t *last_error_ep) {
  hcd_pio_usb_pb1000_get_last_ep(last_complete_ep, last_error_ep);
}

void usb_host_core_debug_pio_usb_setup(uint32_t *setup_send_count,
                                        uint32_t *last_setup_word,
                                        uint32_t *port_reset_count,
                                        uint32_t *port_reset_end_count,
                                        uint32_t *is_fullspeed) {
  hcd_pio_usb_pb1000_get_setup_info(setup_send_count, last_setup_word,
                                     port_reset_count, port_reset_end_count);
  *is_fullspeed = hcd_pio_usb_pb1000_get_is_fullspeed();
}

void usb_host_core_debug_pio_usb_history(uint32_t *count, uint32_t *out,
                                          uint32_t out_len) {
  hcd_pio_usb_pb1000_get_event_history(count, out, out_len);
}

/* TEMPORARY DIAGNOSTIC (2026-10-28, USB keyboard not enumerating over
 * PIO-USB investigation -- see
 * 調査用/RP2350-PiZero_実機認識しない問題調査.md) -- same technique that
 * already confirmed a genuine hardware-level PIO TX FIFO underrun for
 * the unrelated DVI "No Signal" issue (dvi_display_pio_debug_info()):
 * read the PIO block's own FDEBUG register directly (TXSTALL/TXOVER/
 * RXUNDER/RXSTALL, one bit per state machine, sticky/write-1-to-clear)
 * to tell "the control transfer's SETUP packet genuinely failed at the
 * hardware TX/RX level" apart from "TX/RX looked fine to the PIO itself,
 * the problem is elsewhere (device never responding, enumeration logic,
 * etc)." Reads whichever PIO block pizero's build actually configures
 * for PIO-USB (pio1, see usb_host_core_init_pio_usb()'s PB1000_BOARD_PIZERO
 * branch) -- pio0 for every other PIO-USB board (GP4/GP5). Remove once
 * isolated. */
void usb_host_core_debug_pio_fdebug(uint32_t *fdebug) {
#ifdef PB1000_BOARD_PIZERO
  *fdebug = pio1->fdebug;
#else
  *fdebug = pio0->fdebug;
#endif
}

extern void hcd_pio_usb_pb1000_get_setup_log(uint8_t out[][9], uint32_t *count);

void usb_host_core_debug_setup_log(uint8_t out[][9], uint32_t *count) {
  hcd_pio_usb_pb1000_get_setup_log(out, count);
}

#else /* !PB1000_USE_PIO_USB */
/* No-op stubs for the native-controller build -- see these functions'
 * declarations in usb_host_core.h for why they always exist. */
void usb_host_core_debug_pio_usb(uint32_t *tick_count, uint32_t *connected,
                                  uint32_t *suspended, uint32_t *ints,
                                  uint32_t *attach, uint32_t *detach,
                                  uint32_t *ep_complete, uint32_t *ep_stalled,
                                  uint32_t *ep_error) {
  *tick_count = 0;
  *connected = 0;
  *suspended = 0;
  *ints = 0;
  *attach = 0;
  *detach = 0;
  *ep_complete = 0;
  *ep_stalled = 0;
  *ep_error = 0;
}

void usb_host_core_debug_pio_usb_ep(uint32_t *last_complete_ep,
                                     uint32_t *last_error_ep) {
  *last_complete_ep = 0xFFFFFFFFu;
  *last_error_ep = 0xFFFFFFFFu;
}

void usb_host_core_debug_pio_usb_setup(uint32_t *setup_send_count,
                                        uint32_t *last_setup_word,
                                        uint32_t *port_reset_count,
                                        uint32_t *port_reset_end_count,
                                        uint32_t *is_fullspeed) {
  *setup_send_count = 0;
  *last_setup_word = 0xFFFFFFFFu;
  *port_reset_count = 0;
  *port_reset_end_count = 0;
  *is_fullspeed = 0;
}

void usb_host_core_debug_pio_usb_history(uint32_t *count, uint32_t *out,
                                          uint32_t out_len) {
  *count = 0;
  (void)out;
  (void)out_len;
}

void usb_host_core_debug_pio_fdebug(uint32_t *fdebug) {
  *fdebug = 0;
}

void usb_host_core_debug_setup_log(uint8_t out[][9], uint32_t *count) {
  (void)out;
  *count = 0;
}

#endif /* PB1000_USE_PIO_USB */

void usb_host_core_init(uint32_t sys_khz) {
#ifdef PB1000_BOARD_PIZERO
  (void)sys_khz;
  /* 2026-10-28 real-hardware finding (USB keyboard not enumerating over
   * PIO-USB -- see 調査用/RP2350-PiZero_実機認識しない問題調査.md):
   * pizero must NOT reach the set_sys_clock_khz()/stdio_uart_init() calls
   * below at all -- not even the (already-present) "skip if already at
   * the target rate" guard around set_sys_clock_khz() was enough, because
   * stdio_uart_init() itself still ran unconditionally every time. This
   * exactly matches a real-hardware finding already made and fixed on
   * the sibling MSX_emu_pico2 project for this identical board/feature
   * (its own usb_host_core_init_pizero(), confirmed working with a real
   * USB keyboard on 2026-09-29): stdio_uart_init() (pico-sdk's low-level
   * UART0 setup) reinitializes the same physical UART0 pizero's REPL may
   * already be actively using via MicroPython's own UART driver
   * (MICROPY_HW_ENABLE_UART_REPL) -- calling pico-sdk's stdio_uart_init()
   * on top of that, mid-connection, corrupts whatever is in flight over
   * the SAME physical UART. pio_usb_host_init() (called from
   * usb_host_core_init_pio_usb() below) computes its own PIO clock
   * dividers dynamically from clk_sys at call time, so there is no clock
   * adjustment needed here either -- dvi_display_init() (which runs
   * before this, see main_entry.py's boot order) has already set clk_sys
   * to the rate DVI needs, and that's what PIO-USB will use. */
  usb_host_core_init_pio_usb();
  return;
#endif

  /* 240MHz = 12MHz x 20, a clean multiple required for PIO-USB's
     full-speed(12Mbit)/low-speed(1.5Mbit) bit timing. Raised from the
     previous 144MHz (12MHz x 12) after confirming on a sibling MSX-emulator
     project (same RP2350 target) that 240MHz runs stably and meaningfully
     speeds up CPU emulation, since this sys_clock is shared by the whole
     chip, not just the USB host PIO state machines. */
  set_sys_clock_khz(sys_khz ? sys_khz : 252000, true);
  sleep_ms(10);
  stdio_uart_init();

#ifdef PB1000_USE_PIO_USB
  usb_host_core_init_pio_usb();
#else
  if (!tuh_init(0)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_init failed!\n");
    return;
  }
#endif
}

void usb_host_core_start_bg_timer(int interval_ms) {
#ifdef PB1000_USE_PIO_USB
  (void)interval_ms;  /* PIO-USB is serviced from the main loop; see usb_host_core_task() */
#else
  if (usb_bg_timer_active) return;
  if (interval_ms < 1) interval_ms = 8;
  if (!add_repeating_timer_ms(-interval_ms, usb_bg_timer_callback, NULL, &usb_bg_timer)) {
    DEBUG_PRINTF("[USB Host] start_bg_timer FAILED (alarm pool full?)\n");
    return;
  }
  usb_bg_timer_active = true;
#endif
}

void usb_host_core_task(void) {
#ifdef PB1000_USE_PIO_USB
  tuh_task();
#endif
}

void usb_host_core_stop_bg_timer(void) {
  if (!usb_bg_timer_active) return;
  cancel_repeating_timer(&usb_bg_timer);
  usb_bg_timer_active = false;
}
