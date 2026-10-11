#ifndef USB_HOST_CORE_H
#define USB_HOST_CORE_H

#include <stdbool.h>
#include <stdint.h>


void usb_host_core_init(void);
void usb_host_core_start_bg_timer(int interval_ms);
void usb_host_core_stop_bg_timer(void);

/* Services the USB host from the main loop. PIO-USB builds need this
 * (their background timer is disabled); native builds do nothing here. */
void usb_host_core_task(void);

/* Real-hardware bring-up diagnostic for the PIO-USB (GP4/GP5) host path
 * -- see usb_host_core.c's own comment. Exposed unconditionally (as
 * usb_host.debug()) so modusb_host.c -- built as part of the main
 * firmware target, which never sees USE_PIO_USB's PRIVATE compile
 * definitions on usb_host_core_lib -- doesn't need to know which mode
 * this firmware was built in; usb_host_core.c provides a no-op stub for
 * the native-controller build (all-zero output) and the real
 * implementation when PB1000_USE_PIO_USB is defined. */
void usb_host_core_debug_pio_usb(uint32_t *tick_count, uint32_t *connected,
                                  uint32_t *suspended, uint32_t *ints,
                                  uint32_t *attach, uint32_t *detach,
                                  uint32_t *ep_complete, uint32_t *ep_stalled,
                                  uint32_t *ep_error);

/* Which endpoint (dev_addr<<8 | ep_num, 0xFFFFFFFF if none yet) most
 * recently completed/failed a transfer -- see hcd_pio_usb_pb1000.c's
 * pio_usb_last_complete_ep/pio_usb_last_error_ep comment. Same
 * always-declared/no-op-stub pattern as usb_host_core_debug_pio_usb(). */
void usb_host_core_debug_pio_usb_ep(uint32_t *last_complete_ep,
                                     uint32_t *last_error_ep);

/* Which standard control request was last sent via hcd_setup_send(),
 * plus bus-reset counts -- see hcd_pio_usb_pb1000.c's
 * pio_usb_last_setup_word comment for the packing format. Same
 * always-declared/no-op-stub pattern as the other debug getters. */
void usb_host_core_debug_pio_usb_setup(uint32_t *setup_send_count,
                                        uint32_t *last_setup_word,
                                        uint32_t *port_reset_count,
                                        uint32_t *port_reset_end_count,
                                        uint32_t *is_fullspeed);

/* Chronological history of recent EP0 completion events -- see
 * hcd_pio_usb_pb1000.c's pio_usb_record_event()/pio_usb_event_history
 * comment for the packed format. Writes up to out_len entries into out
 * (chronological order) and sets *count to the total ever recorded
 * (may exceed out_len / the ring buffer's own capacity). */
void usb_host_core_debug_pio_usb_history(uint32_t *count, uint32_t *out,
                                          uint32_t out_len);

#endif
