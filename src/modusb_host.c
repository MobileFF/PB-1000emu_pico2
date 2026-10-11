#include "py/mphal.h"
#include "py/runtime.h"
#include "usb_host_core.h"


// low‑level print helper (no buffering)
#define TRACE(str) mp_hal_stdout_tx_str(str "\n")

// Python API: usb_host.init()
static mp_obj_t mod_usb_host_init(size_t n_args, const mp_obj_t *args) {
  (void)n_args;
  (void)args;
//  TRACE("[USB Host] wrapper: before core_init");
//  mp_printf(&mp_plat_print, "[USB Host] wrapper: before core_init\n");
#ifdef DEBUG_SKIP_CORE_INIT
  TRACE("[USB Host] skipping core_init\n");
  mp_printf(&mp_plat_print, "[USB Host] skipping core_init\n");
#else
  usb_host_core_init();
#endif
  //  TRACE("[USB Host] wrapper: after core_init");
  //  mp_printf(&mp_plat_print, "[USB Host] wrapper: after core_init\n");
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_usb_host_init_obj, 0, 1,
                                           mod_usb_host_init);

// Python API: usb_host.start_bg_timer(interval_ms=8)
static mp_obj_t mod_usb_host_start_bg_timer(size_t n_args, const mp_obj_t *args) {
  int interval = (n_args > 0) ? mp_obj_get_int(args[0]) : 8;
  usb_host_core_start_bg_timer(interval);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_usb_host_start_bg_timer_obj,
                                            0, 1, mod_usb_host_start_bg_timer);

// Python API: usb_host.stop_bg_timer()
static mp_obj_t mod_usb_host_stop_bg_timer(void) {
  usb_host_core_stop_bg_timer();
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_stop_bg_timer_obj,
                                  mod_usb_host_stop_bg_timer);

// Python API: usb_host.task()
// Services the USB host from the main loop (PIO-USB builds); a no-op in
// native builds, where the background timer does this.
static mp_obj_t mod_usb_host_task(void) {
  usb_host_core_task();
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_task_obj, mod_usb_host_task);

// Python API: usb_host.debug() ->
//   (tick_count, connected, suspended, ints,
//    attach_count, detach_count, ep_complete_count, ep_stalled_count, ep_error_count)
// Real-hardware bring-up diagnostic for the PIO-USB (GP4/GP5) host path --
// see usb_host_core.c's usb_host_core_debug_pio_usb() and
// hcd_pio_usb_pb1000.c's event counters. Always callable; returns all
// zeros when the firmware wasn't built with USE_PIO_USB.
static mp_obj_t mod_usb_host_debug(void) {
  uint32_t tick_count, connected, suspended, ints;
  uint32_t attach, detach, ep_complete, ep_stalled, ep_error;
  usb_host_core_debug_pio_usb(&tick_count, &connected, &suspended, &ints,
                               &attach, &detach, &ep_complete, &ep_stalled, &ep_error);
  mp_obj_t items[9] = {
      mp_obj_new_int_from_uint(tick_count),
      mp_obj_new_int_from_uint(connected),
      mp_obj_new_int_from_uint(suspended),
      mp_obj_new_int_from_uint(ints),
      mp_obj_new_int_from_uint(attach),
      mp_obj_new_int_from_uint(detach),
      mp_obj_new_int_from_uint(ep_complete),
      mp_obj_new_int_from_uint(ep_stalled),
      mp_obj_new_int_from_uint(ep_error),
  };
  return mp_obj_new_tuple(9, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_debug_obj, mod_usb_host_debug);

// Python API: usb_host.debug2() -> (last_complete_ep, last_error_ep)
// Each is (dev_addr << 8) | ep_num (0xFFFFFFFF = none yet observed).
// ep_num's 0x80 bit is direction (TinyUSB convention); ep_num 0x00
// means EP0 (a control transfer -- enumeration itself), e.g. 0x81 is a
// typical keyboard interrupt-IN endpoint. Distinguishes "control
// transfer failing" (enumeration never finishes) from "interrupt poll
// failing after enumeration succeeded". See mp/test/pio_usb_bringup.py.
static mp_obj_t mod_usb_host_debug2(void) {
  uint32_t last_complete_ep, last_error_ep;
  usb_host_core_debug_pio_usb_ep(&last_complete_ep, &last_error_ep);
  mp_obj_t items[2] = {
      mp_obj_new_int_from_uint(last_complete_ep),
      mp_obj_new_int_from_uint(last_error_ep),
  };
  return mp_obj_new_tuple(2, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_debug2_obj, mod_usb_host_debug2);

// Python API: usb_host.debug3() ->
//   (setup_send_count, last_setup_word, port_reset_count, port_reset_end_count)
// last_setup_word = (bmRequestType<<24)|(bRequest<<16)|wLength -- the
// most recently sent standard control request (bRequest: 0=GET_STATUS
// 5=SET_ADDRESS 6=GET_DESCRIPTOR 9=SET_CONFIGURATION). Shows WHICH
// request is associated with the EP0 failures reported by debug()/
// debug2(). See mp/test/pio_usb_bringup.py.
static mp_obj_t mod_usb_host_debug3(void) {
  uint32_t setup_send_count, last_setup_word, port_reset_count, port_reset_end_count;
  uint32_t is_fullspeed;
  usb_host_core_debug_pio_usb_setup(&setup_send_count, &last_setup_word,
                                     &port_reset_count, &port_reset_end_count,
                                     &is_fullspeed);
  mp_obj_t items[5] = {
      mp_obj_new_int_from_uint(setup_send_count),
      mp_obj_new_int_from_uint(last_setup_word),
      mp_obj_new_int_from_uint(port_reset_count),
      mp_obj_new_int_from_uint(port_reset_end_count),
      mp_obj_new_int_from_uint(is_fullspeed),
  };
  return mp_obj_new_tuple(5, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_debug3_obj, mod_usb_host_debug3);

// Python API: usb_host.debug4() -> (count, (event0, event1, ...))
// Chronological history of recent EP0 completion events (up to the last
// 8). Each event is packed as (result<<28)|(ep_num<<16)|(data_id<<8)|
// dev_addr -- result: 1=SUCCESS 2=STALLED 3=ERROR; data_id: 0x2D=SETUP
// (USB_PID_SETUP), 1=DATA1, 0=DATA0. Shows the exact stage-by-stage
// sequence across repeated control-transfer attempts. See
// mp/test/pio_usb_bringup.py.
static mp_obj_t mod_usb_host_debug4(void) {
  uint32_t count;
  uint32_t history[8];
  usb_host_core_debug_pio_usb_history(&count, history, 8);
  uint32_t n = count < 8 ? count : 8;
  mp_obj_t event_items[8];
  for (uint32_t i = 0; i < n; i++) {
    event_items[i] = mp_obj_new_int_from_uint(history[i]);
  }
  mp_obj_t events_tuple = mp_obj_new_tuple(n, event_items);
  mp_obj_t items[2] = {
      mp_obj_new_int_from_uint(count),
      events_tuple,
  };
  return mp_obj_new_tuple(2, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_debug4_obj, mod_usb_host_debug4);


static const mp_rom_map_elem_t usb_host_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_usb_host)},
    {MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&mod_usb_host_init_obj)},
    {MP_ROM_QSTR(MP_QSTR_start_bg_timer),
     MP_ROM_PTR(&mod_usb_host_start_bg_timer_obj)},
    {MP_ROM_QSTR(MP_QSTR_stop_bg_timer),
     MP_ROM_PTR(&mod_usb_host_stop_bg_timer_obj)},
    {MP_ROM_QSTR(MP_QSTR_task), MP_ROM_PTR(&mod_usb_host_task_obj)},
    {MP_ROM_QSTR(MP_QSTR_debug), MP_ROM_PTR(&mod_usb_host_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_debug2), MP_ROM_PTR(&mod_usb_host_debug2_obj)},
    {MP_ROM_QSTR(MP_QSTR_debug3), MP_ROM_PTR(&mod_usb_host_debug3_obj)},
    {MP_ROM_QSTR(MP_QSTR_debug4), MP_ROM_PTR(&mod_usb_host_debug4_obj)},
};
static MP_DEFINE_CONST_DICT(usb_host_module_globals,
                            usb_host_module_globals_table);

const mp_obj_module_t mp_module_usb_host = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&usb_host_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_usb_host, mp_module_usb_host);
