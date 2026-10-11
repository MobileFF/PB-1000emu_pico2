/*
 * hcd_pio_usb_pb1000.c — TinyUSB host-controller-driver glue for this
 * project's PIO-USB port (GP4/GP5), built only when USE_PIO_USB is ON
 * (src/micropython.cmake).
 *
 * 2026-10-04. Ported from a sibling MicroPython/RP2350 project's own
 * validated implementation (`msx_emu_pico2`'s
 * `src/usb_host/hcd_pio_usb_pizero.c`, written there against
 * Pico-PIO-USB 0.7.2's real API and confirmed working on real RP2350
 * hardware — see src/usb_host/pio_usb/PROVENANCE.md). The hcd_*
 * functions below are board-independent (pure forwarders to
 * pio_usb_host_* with an rhport-offset calculation); only
 * usb_host_core.c's usb_host_core_init_pio_usb() carries this project's
 * own board-specific pin/PIO/DMA choices.
 *
 * This project has its own earlier, unfinished attempt at this exact
 * glue (src/usb_host/hcd_pio_usb_custom.c, a copy of TinyUSB's own
 * hw/bsp/rp2040/family.c-adjacent example) that was never actually
 * vendored/built against a real pio_usb checkout and was abandoned in
 * favor of the native RP2350 host controller — see
 * src/micropython.cmake's "Pico-PIO-USB removed for Native Host mode"
 * comment and that file's own header. This file supersedes it; ending
 * up structurally similar to it is expected (that's what a correct
 * TinyUSB<->pio_usb bridge looks like), not because it was copied.
 *
 * Key facts this file relies on (see PROVENANCE.md for the full detail):
 *   - PIO-USB's host mode is entirely self-contained once
 *     pio_usb_host_init() returns: it registers its own internal event
 *     delivery via pio_usb_host_irq_handler() (a *weak* symbol in
 *     pio_usb_host.c), called once per millisecond from
 *     pio_usb_host_frame() (itself driven by usb_host_core.c's own
 *     manual hardware_alarm_* callback, NOT pio_usb's default internal
 *     timer — see that file's usb_host_core_init_pio_usb() for why).
 *     This file's own strong, non-weak definition of
 *     pio_usb_host_irq_handler() below overrides that weak default at
 *     link time — this is the only hook TinyUSB-side event delivery
 *     needs; nothing here has to touch interrupts, register a PIO IRQ,
 *     or run a task loop by hand.
 *   - Because of the above, hcd_int_enable()/hcd_int_disable() are true
 *     no-ops (matches upstream's own pio-usb HCD example) and there is
 *     no hcd_task()-equivalent to call — usb_host_core.c's existing
 *     repeating-timer-driven tuh_task() call is only for TinyUSB's own
 *     higher-level event queue (attach/detach/xfer-complete), which is
 *     fed by hcd_event_handler() calls from this file's IRQ handler.
 *   - Runs on the same single core as MicroPython (this project doesn't
 *     use the second core at all).
 */

#include "tusb_option.h"

#if CFG_TUH_ENABLED && CFG_TUH_RPI_PIO_USB

#include <string.h>

#include "pico.h"
#include "pio_usb.h"
#include "pio_usb_ll.h"

#include "host/hcd.h"
#include "host/usbh.h"

/* TinyUSB reserves rhport 0 for this project's native USB controller
 * (kept available for a future USB-device/CDC-REPL use, once this is
 * proven working); pio_usb's single root port is addressed as rhport 1
 * here (tuh_init(1) at the call site, usb_host_core.c), matching
 * pio_usb's own upstream convention (its example glue uses the same
 * offset). This project only ever uses rhport 1 for pio_usb. */
#define PIO_USB_RHPORT 1
#define PIO_USB_ROOT_IDX(rhport) ((rhport) - PIO_USB_RHPORT)

static pio_usb_configuration_t pio_host_cfg = PIO_USB_DEFAULT_CONFIG;

/* Bring-up diagnostic counters, incremented directly in
 * pio_usb_host_irq_handler() below so they count every event this file
 * actually sees and dispatches to TinyUSB, independent of (and a finer
 * breakdown than) usb_host_core_debug_pio_usb()'s raw root->ints
 * snapshot, which only catches events still pending at the moment it's
 * read. Exposed via hcd_pio_usb_pb1000_get_event_counts() ->
 * usb_host_core_debug_pio_usb() -> usb_host.debug(). */
static volatile uint32_t pio_usb_attach_count = 0;
static volatile uint32_t pio_usb_detach_count = 0;
static volatile uint32_t pio_usb_ep_complete_count = 0;
static volatile uint32_t pio_usb_ep_stalled_count = 0;
static volatile uint32_t pio_usb_ep_error_count = 0;

/* Which endpoint most recently completed/failed -- (dev_addr << 8) |
 * ep_num, where ep_num's 0x80 bit is the direction (matches TinyUSB's
 * own ep_addr convention), so ep_num == 0x00 means EP0 OUT (i.e. a
 * control transfer, meaning enumeration itself hasn't finished yet)
 * while e.g. 0x81 is a typical keyboard interrupt-IN endpoint. Lets a
 * bring-up test distinguish "control transfer failing" from "interrupt
 * poll failing after enumeration succeeded" -- see
 * mp/test/pio_usb_bringup.py. */
static volatile uint32_t pio_usb_last_complete_ep = 0xFFFFFFFFu;
static volatile uint32_t pio_usb_last_error_ep = 0xFFFFFFFFu;

/* Which standard control request was most recently sent via
 * hcd_setup_send(), and how many bus resets have been issued -- lets a
 * bring-up test see e.g. "last setup was bRequest=5 (SET_ADDRESS),
 * which then shows up as the failing EP0 transfer" instead of only
 * knowing "EP0 failed" with no idea which request. Packed as
 * (bmRequestType << 24) | (bRequest << 16) | wLength (standard bRequest
 * codes: 0=GET_STATUS 5=SET_ADDRESS 6=GET_DESCRIPTOR 9=SET_CONFIGURATION).
 * See mp/test/pio_usb_bringup.py. */
static volatile uint32_t pio_usb_setup_send_count = 0;
static volatile uint32_t pio_usb_last_setup_word = 0xFFFFFFFFu;
static volatile uint32_t pio_usb_port_reset_count = 0;
static volatile uint32_t pio_usb_port_reset_end_count = 0;

void hcd_pio_usb_pb1000_get_event_counts(uint32_t *attach, uint32_t *detach,
                                          uint32_t *ep_complete,
                                          uint32_t *ep_stalled,
                                          uint32_t *ep_error) {
  *attach = pio_usb_attach_count;
  *detach = pio_usb_detach_count;
  *ep_complete = pio_usb_ep_complete_count;
  *ep_stalled = pio_usb_ep_stalled_count;
  *ep_error = pio_usb_ep_error_count;
}

void hcd_pio_usb_pb1000_get_last_ep(uint32_t *last_complete_ep,
                                     uint32_t *last_error_ep) {
  *last_complete_ep = pio_usb_last_complete_ep;
  *last_error_ep = pio_usb_last_error_ep;
}

void hcd_pio_usb_pb1000_get_setup_info(uint32_t *setup_send_count,
                                        uint32_t *last_setup_word,
                                        uint32_t *port_reset_count,
                                        uint32_t *port_reset_end_count) {
  *setup_send_count = pio_usb_setup_send_count;
  *last_setup_word = pio_usb_last_setup_word;
  *port_reset_count = pio_usb_port_reset_count;
  *port_reset_end_count = pio_usb_port_reset_end_count;
}

uint32_t hcd_pio_usb_pb1000_get_is_fullspeed(void) {
  root_port_t *root = PIO_USB_ROOT_PORT(0);
  return root->is_fullspeed ? 1 : 0;
}

/* Short history of recent EP0 completion events, each packed as
 * (result << 28) | (ep_num << 16) | (data_id << 8) | dev_addr, where
 * result: 1=SUCCESS 2=STALLED 3=ERROR. Lets a bring-up test see the
 * exact SETUP/DATA/STATUS stage sequence (data_id: 2=SETUP, 1=DATA1,
 * 0=DATA0) across repeated control-transfer attempts -- e.g.
 * distinguishing "SETUP succeeded, DATA failed, then every retry's own
 * SETUP also failed" from other orderings. See
 * mp/test/pio_usb_bringup.py. USB_PID_SETUP (usb_definitions.h) is 0x2D,
 * which is unambiguous against the real data_id values 0 and 1. */
#define PIO_USB_EVENT_HISTORY_LEN 8
static volatile uint32_t pio_usb_event_history[PIO_USB_EVENT_HISTORY_LEN];
static volatile uint32_t pio_usb_event_history_idx = 0;

static void pio_usb_record_event(uint32_t result, endpoint_t *ep) {
  uint32_t packed = (result << 28) | ((uint32_t)ep->ep_num << 16) |
                     ((uint32_t)ep->data_id << 8) | ep->dev_addr;
  pio_usb_event_history[pio_usb_event_history_idx % PIO_USB_EVENT_HISTORY_LEN] = packed;
  pio_usb_event_history_idx++;
}

void hcd_pio_usb_pb1000_get_event_history(uint32_t *count, uint32_t *out,
                                           uint32_t out_len) {
  *count = pio_usb_event_history_idx;
  uint32_t n = pio_usb_event_history_idx < PIO_USB_EVENT_HISTORY_LEN
                   ? pio_usb_event_history_idx
                   : PIO_USB_EVENT_HISTORY_LEN;
  if (n > out_len) n = out_len;
  /* Oldest-surviving entry is at (idx - n) % LEN; read forward from there
   * so out[] is in chronological order regardless of ring wraparound. */
  uint32_t start = (pio_usb_event_history_idx - n) % PIO_USB_EVENT_HISTORY_LEN;
  for (uint32_t i = 0; i < n; i++) {
    out[i] = pio_usb_event_history[(start + i) % PIO_USB_EVENT_HISTORY_LEN];
  }
}

/* -----------------------------------------------------------------------
 * hcd_configure / hcd_init
 *
 * usb_host_core.c calls tuh_configure(PIO_USB_RHPORT,
 * TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &cfg) with pin_dp/pio_tx_num/
 * pio_rx_num/tx_ch already filled in (see
 * usb_host_core_init_pio_usb()) before tuh_init(PIO_USB_RHPORT);
 * TinyUSB forwards that configuration struct here verbatim via
 * hcd_configure(), and hcd_init() is where pio_usb_host_init() actually
 * brings the port up.
 * ----------------------------------------------------------------------- */
bool hcd_configure(uint8_t rhport, uint32_t cfg_id, const void *cfg_param) {
  (void)rhport;
  TU_VERIFY(cfg_id == TUH_CFGID_RPI_PIO_USB_CONFIGURATION);
  memcpy(&pio_host_cfg, cfg_param, sizeof(pio_usb_configuration_t));
  return true;
}

bool hcd_init(uint8_t rhport, const tusb_rhport_init_t *rh_init) {
  (void)rhport;
  (void)rh_init;
  pio_usb_host_init(&pio_host_cfg);
  return true;
}

void hcd_port_reset(uint8_t rhport) {
  pio_usb_port_reset_count++;
  pio_usb_host_port_reset_start(PIO_USB_ROOT_IDX(rhport));
}

void hcd_port_reset_end(uint8_t rhport) {
  pio_usb_port_reset_end_count++;
  pio_usb_host_port_reset_end(PIO_USB_ROOT_IDX(rhport));
}

bool hcd_port_connect_status(uint8_t rhport) {
  root_port_t *root = PIO_USB_ROOT_PORT(PIO_USB_ROOT_IDX(rhport));
  return pio_usb_bus_get_line_state(root) != PORT_PIN_SE0;
}

tusb_speed_t hcd_port_speed_get(uint8_t rhport) {
  root_port_t *root = PIO_USB_ROOT_PORT(PIO_USB_ROOT_IDX(rhport));
  return root->is_fullspeed ? TUSB_SPEED_FULL : TUSB_SPEED_LOW;
}

void hcd_device_close(uint8_t rhport, uint8_t dev_addr) {
  pio_usb_host_close_device(PIO_USB_ROOT_IDX(rhport), dev_addr);
}

uint32_t hcd_frame_number(uint8_t rhport) {
  (void)rhport;
  return pio_usb_host_get_frame_number();
}

/* No real interrupt to mask/unmask — see this file's header comment
 * ("hcd_int_enable()/hcd_int_disable() are true no-ops"). */
void hcd_int_enable(uint8_t rhport) { (void)rhport; }
void hcd_int_disable(uint8_t rhport) { (void)rhport; }

/* -----------------------------------------------------------------------
 * Endpoint API — thin forwarders to pio_usb_host_*.
 * ----------------------------------------------------------------------- */
bool hcd_edpt_open(uint8_t rhport, uint8_t dev_addr,
                    tusb_desc_endpoint_t const *desc_ep) {
  /* Low-speed devices behind a full-speed hub need PRE (preamble)
   * packets — hcd_devtree_get_info() (TinyUSB's own device-tree walker,
   * already built here via usbh.c/hub.c, unchanged) reports whether
   * that applies to this device. */
  hcd_devtree_info_t dev_tree;
  hcd_devtree_get_info(dev_addr, &dev_tree);
  bool const need_pre = (dev_tree.hub_addr != 0 &&
                         dev_tree.speed == TUSB_SPEED_LOW);

  return pio_usb_host_endpoint_open(PIO_USB_ROOT_IDX(rhport), dev_addr,
                                    (uint8_t const *)desc_ep, need_pre);
}

bool hcd_edpt_xfer(uint8_t rhport, uint8_t dev_addr, uint8_t ep_addr,
                    uint8_t *buffer, uint16_t buflen) {
  return pio_usb_host_endpoint_transfer(PIO_USB_ROOT_IDX(rhport), dev_addr,
                                       ep_addr, buffer, buflen);
}

bool hcd_edpt_abort_xfer(uint8_t rhport, uint8_t dev_addr, uint8_t ep_addr) {
  return pio_usb_host_endpoint_abort_transfer(PIO_USB_ROOT_IDX(rhport),
                                              dev_addr, ep_addr);
}

bool hcd_setup_send(uint8_t rhport, uint8_t dev_addr,
                     uint8_t const setup_packet[8]) {
  pio_usb_setup_send_count++;
  pio_usb_last_setup_word = ((uint32_t)setup_packet[0] << 24) |
                             ((uint32_t)setup_packet[1] << 16) |
                             ((uint32_t)setup_packet[6]) |
                             ((uint32_t)setup_packet[7] << 8);
  return pio_usb_host_send_setup(PIO_USB_ROOT_IDX(rhport), dev_addr,
                                 setup_packet);
}

bool hcd_edpt_clear_stall(uint8_t rhport, uint8_t dev_addr, uint8_t ep_addr) {
  /* pio_usb has no separate stall-clear primitive (a keyboard's plain
   * interrupt-IN endpoint never stalls in practice); matches upstream's
   * own pio-usb HCD example, which also just reports success here. */
  (void)rhport; (void)dev_addr; (void)ep_addr;
  return true;
}

/* -----------------------------------------------------------------------
 * pio_usb_host_irq_handler — overrides the __attribute__((weak)) default
 * in pio_usb_host.c (see this file's header comment). Called
 * synchronously from inside pio_usb_host_frame(), itself called once per
 * millisecond from usb_host_core.c's own hardware_alarm_* callback — NOT
 * from a real hardware PIO/DMA interrupt, but the naming is pio_usb's
 * own and kept as-is since this function's signature/linkage (the weak-
 * symbol override) is fixed by the library, not a naming choice made
 * here.
 * ----------------------------------------------------------------------- */
static void handle_endpoint_irq(uint8_t tu_rhport, xfer_result_t result,
                                volatile uint32_t *ep_reg) {
  uint32_t const ep_all = *ep_reg;

  for (uint8_t ep_idx = 0; ep_idx < PIO_USB_EP_POOL_CNT; ep_idx++) {
    uint32_t const mask = (1u << ep_idx);
    if (!(ep_all & mask)) {
      continue;
    }

    endpoint_t *ep = PIO_USB_ENDPOINT(ep_idx);
    uint32_t const ep_key = ((uint32_t)ep->dev_addr << 8) | ep->ep_num;
    if (result == XFER_RESULT_FAILED) {
      pio_usb_last_error_ep = ep_key;
    } else if (result == XFER_RESULT_SUCCESS) {
      pio_usb_last_complete_ep = ep_key;
    }
    /* Must record (result, data_id) BEFORE hcd_event_handler() below --
     * that call may synchronously trigger usbh.c's next hcd_setup_send()/
     * hcd_edpt_xfer() for this same ep, which overwrites ep->data_id for
     * the NEXT stage before we'd get a chance to read this stage's. */
    pio_usb_record_event(
        result == XFER_RESULT_SUCCESS ? 1u
        : result == XFER_RESULT_STALLED ? 2u
                                         : 3u,
        ep);
    hcd_event_t event = {
        .rhport = tu_rhport,
        .event_id = HCD_EVENT_XFER_COMPLETE,
        .dev_addr = ep->dev_addr,
    };
    event.xfer_complete.ep_addr = ep->ep_num;
    event.xfer_complete.result = result;
    event.xfer_complete.len = ep->actual_len;
    hcd_event_handler(&event, true);
  }

  *ep_reg &= ~ep_all;
}

void pio_usb_host_irq_handler(uint8_t root_id) {
  uint8_t const tu_rhport = root_id + PIO_USB_RHPORT;
  root_port_t *root = PIO_USB_ROOT_PORT(root_id);
  uint32_t const ints = root->ints;

  if (ints & PIO_USB_INTS_ENDPOINT_COMPLETE_BITS) {
    pio_usb_ep_complete_count++;
    handle_endpoint_irq(tu_rhport, XFER_RESULT_SUCCESS, &root->ep_complete);
  }
  if (ints & PIO_USB_INTS_ENDPOINT_STALLED_BITS) {
    pio_usb_ep_stalled_count++;
    handle_endpoint_irq(tu_rhport, XFER_RESULT_STALLED, &root->ep_stalled);
  }
  if (ints & PIO_USB_INTS_ENDPOINT_ERROR_BITS) {
    pio_usb_ep_error_count++;
    handle_endpoint_irq(tu_rhport, XFER_RESULT_FAILED, &root->ep_error);
  }
  if (ints & PIO_USB_INTS_CONNECT_BITS) {
    pio_usb_attach_count++;
    hcd_event_t event = {.rhport = tu_rhport, .event_id = HCD_EVENT_DEVICE_ATTACH};
    hcd_event_handler(&event, true);
  }
  if (ints & PIO_USB_INTS_DISCONNECT_BITS) {
    pio_usb_detach_count++;
    hcd_event_t event = {.rhport = tu_rhport, .event_id = HCD_EVENT_DEVICE_REMOVE};
    hcd_event_handler(&event, true);
  }

  root->ints &= ~ints;
}

#endif /* CFG_TUH_ENABLED && CFG_TUH_RPI_PIO_USB */
