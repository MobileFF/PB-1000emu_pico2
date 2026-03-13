#ifndef USB_HOST_CORE_H
#define USB_HOST_CORE_H

#include <stdbool.h>
#include <stdint.h>


void usb_host_core_init(void);
void usb_host_core_task(void);
bool usb_host_core_get_event(uint8_t *scancode, bool *pressed);

#endif
