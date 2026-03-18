#ifndef USB_HOST_CORE_H
#define USB_HOST_CORE_H

#include <stdbool.h>
#include <stdint.h>


void usb_host_core_init(void);
void usb_host_core_task(void);
bool usb_host_core_get_event(uint8_t *scancode, bool *pressed);
void usb_host_core_set_c_kb_routing(bool enabled);
void usb_host_core_start_bg_timer(int interval_ms);
void usb_host_core_stop_bg_timer(void);

#endif
