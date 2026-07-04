#include "usb_host_core.h"
#include "py/mpprint.h"
#include "tusb.h"
#include "host/hcd.h"
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include <string.h>

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

void tu_print_mem(void const *buf, uint32_t count, uint8_t indent) {
  uint8_t const *p = (uint8_t const *)buf;
  for (uint32_t i = 0; i < count; i++) {
    if (i % 16 == 0) {
      if (i > 0) mp_printf(&mp_plat_print, "\n");
      for (uint8_t j = 0; j < indent; j++) mp_printf(&mp_plat_print, " ");
    }
    mp_printf(&mp_plat_print, "%02X ", p[i]);
  }
  mp_printf(&mp_plat_print, "\n");
}

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

void usb_host_core_init(void) {
  set_sys_clock_khz(144000, true);
  sleep_ms(10);
  stdio_uart_init();

  if (!tuh_init(0)) {
    DEBUG_PRINTF("[USB Host] ERROR: tuh_init failed!\n");
    return;
  }
}

void usb_host_core_task(void) {
  tuh_task();
}

void usb_host_core_start_bg_timer(int interval_ms) {
  if (usb_bg_timer_active) return;
  if (interval_ms < 1) interval_ms = 8;
  if (!add_repeating_timer_ms(-interval_ms, usb_bg_timer_callback, NULL, &usb_bg_timer)) {
    DEBUG_PRINTF("[USB Host] start_bg_timer FAILED (alarm pool full?)\n");
    return;
  }
  usb_bg_timer_active = true;
}

void usb_host_core_stop_bg_timer(void) {
  if (!usb_bg_timer_active) return;
  cancel_repeating_timer(&usb_bg_timer);
  usb_bg_timer_active = false;
}
