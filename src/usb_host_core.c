#include "usb_host_core.h"
#include "py/mpprint.h"
#include "tusb.h"
#include "host/hcd.h"
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include <string.h>

#ifdef PB1000_USE_PIO_USB
#include "hardware/timer.h"
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

void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *desc_report, uint16_t desc_len) {
  (void)desc_report; (void)desc_len;
  tuh_hid_receive_report(dev_addr, instance);
}

void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
  (void)dev_addr; (void)instance;
}

static hid_keyboard_report_t prev_report = {0};

void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
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

static void pio_usb_sof_alarm_handler(uint alarm_num) {
  pio_usb_sof_tick_count++;
  pio_usb_host_frame();
  hardware_alarm_set_target(alarm_num, delayed_by_us(get_absolute_time(), 1000));
}

/* PIO-USB host init (GP4/GP5) — see src/usb_host/pio_usb/PROVENANCE.md
 * and src/usb_host/hcd_pio_usb_pb1000.c's header comment for the full
 * rationale and the facts this relies on. Built only when USE_PIO_USB
 * is ON (src/micropython.cmake); the native-controller path below is
 * used otherwise and is unchanged. */
static void usb_host_core_init_pio_usb(void) {
  pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
  pio_cfg.pin_dp = 4;      /* GP4; D- = +1 = GP5 */
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
  pio_cfg.sm_eop = 2;
  pio_cfg.tx_ch = 15;      /* Highest DMA channel (RP2350 has 0-15),
                            * claimed via dma_claim_mask() inside
                            * pio_usb.c — picked defensively far from
                            * whatever channel number any future DMA
                            * user in this project claims first. */
  pio_cfg.skip_alarm_pool = true;  /* mandatory -- see this function's header comment */

  if (!tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &pio_cfg)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_configure (PIO-USB) failed!\n");
    return;
  }
  if (!tuh_init(1)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_init failed!\n");
    return;
  }

  pio_usb_sof_alarm_num = hardware_alarm_claim_unused(true);
  hardware_alarm_set_callback(pio_usb_sof_alarm_num, pio_usb_sof_alarm_handler);
  hardware_alarm_set_target(pio_usb_sof_alarm_num, delayed_by_us(get_absolute_time(), 1000));
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
#endif /* PB1000_USE_PIO_USB */

void usb_host_core_init(void) {
  /* 240MHz = 12MHz x 20, a clean multiple required for PIO-USB's
     full-speed(12Mbit)/low-speed(1.5Mbit) bit timing. Raised from the
     previous 144MHz (12MHz x 12) after confirming on a sibling MSX-emulator
     project (same RP2350 target) that 240MHz runs stably and meaningfully
     speeds up CPU emulation, since this sys_clock is shared by the whole
     chip, not just the USB host PIO state machines. */
  set_sys_clock_khz(252000, true);
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
