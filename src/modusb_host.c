#include "py/mphal.h"
#include "py/runtime.h"
#include "usb_host_core.h"


// low‑level print helper (no buffering)
#define TRACE(str) mp_hal_stdout_tx_str(str "\n")

// Python API: usb_host.init([sys_khz])
// sys_khz overrides the clk_sys the USB host sets (default 252000), for
// testing PIO-USB timing with other clocks; ignored on pizero.
static mp_obj_t mod_usb_host_init(size_t n_args, const mp_obj_t *args) {
  uint32_t sys_khz = n_args > 0 ? mp_obj_get_int(args[0]) : 0;
//  TRACE("[USB Host] wrapper: before core_init");
//  mp_printf(&mp_plat_print, "[USB Host] wrapper: before core_init\n");
#ifdef DEBUG_SKIP_CORE_INIT
  TRACE("[USB Host] skipping core_init\n");
  mp_printf(&mp_plat_print, "[USB Host] skipping core_init\n");
#else
  usb_host_core_init(sys_khz);
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

// Python API: usb_host.debug5() -> fdebug
// Raw FDEBUG register of whichever PIO block services PIO-USB (pio1 on
// pizero, pio0 elsewhere) -- see usb_host_core.c's
// usb_host_core_debug_pio_fdebug() comment. TXSTALL = (fdebug>>24)&0xF,
// TXOVER = (fdebug>>16)&0xF, RXUNDER=(fdebug>>12)&0xF, RXSTALL=fdebug&0xF
// -- one bit per state machine, sticky (write-1-to-clear).
static mp_obj_t mod_usb_host_debug5(void) {
  uint32_t fdebug;
  usb_host_core_debug_pio_fdebug(&fdebug);
  return mp_obj_new_int_from_uint(fdebug);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_debug5_obj, mod_usb_host_debug5);

// Python API: usb_host.dev_info() -> (mount_count, umount_count, vid, pid)
// vid/pid are of the most recently mounted device.
static mp_obj_t mod_usb_host_dev_info(void) {
  uint32_t mount, umount;
  uint16_t vid, pid;
  usb_host_core_diag_dev(&mount, &umount, &vid, &pid);
  mp_obj_t items[4] = {
      mp_obj_new_int_from_uint(mount),
      mp_obj_new_int_from_uint(umount),
      MP_OBJ_NEW_SMALL_INT(vid),
      MP_OBJ_NEW_SMALL_INT(pid),
  };
  return mp_obj_new_tuple(4, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_dev_info_obj, mod_usb_host_dev_info);

// Python API: usb_host.hid_info() -> tuple of
//   (instance, dev_addr, itf_protocol, protocol_mode, desc_len, report_count,
//    report_descriptor_bytes)
// for each mounted HID interface. itf_protocol: 0=none 1=keyboard 2=mouse;
// protocol_mode: 0=boot 1=report. Only the first 128 descriptor bytes are kept.
static mp_obj_t mod_usb_host_hid_info(void) {
  mp_obj_t list = mp_obj_new_list(0, NULL);
  uint8_t desc[USB_HOST_DIAG_DESC_MAX];
  for (uint8_t i = 0; i < USB_HOST_DIAG_HID_SLOTS; i++) {
    uint8_t dev_addr, itf_protocol, protocol_mode;
    uint16_t desc_len, desc_copied;
    uint32_t report_count;
    if (!usb_host_core_diag_hid(i, &dev_addr, &itf_protocol, &protocol_mode,
                                &desc_len, &report_count, desc, &desc_copied)) {
      continue;
    }
    mp_obj_t items[7] = {
        MP_OBJ_NEW_SMALL_INT(i),
        MP_OBJ_NEW_SMALL_INT(dev_addr),
        MP_OBJ_NEW_SMALL_INT(itf_protocol),
        MP_OBJ_NEW_SMALL_INT(protocol_mode),
        MP_OBJ_NEW_SMALL_INT(desc_len),
        mp_obj_new_int_from_uint(report_count),
        mp_obj_new_bytes(desc, desc_copied),
    };
    mp_obj_list_append(list, mp_obj_new_tuple(7, items));
  }
  return list;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_hid_info_obj, mod_usb_host_hid_info);

// Python API: usb_host.hid_reports(since_seq) -> (next_seq, reports)
// reports: tuple of (seq, instance, len, first_16_bytes) for every raw HID
// report received since `since_seq` that is still in the 16-entry log.
static mp_obj_t mod_usb_host_hid_reports(mp_obj_t since_obj) {
  uint32_t since = mp_obj_get_int(since_obj);
  uint32_t next = usb_host_core_diag_report_seq();
  mp_obj_t list = mp_obj_new_list(0, NULL);
  for (uint32_t seq = since; seq < next; seq++) {
    uint8_t instance, len, data[USB_HOST_DIAG_REPORT_BYTES];
    if (!usb_host_core_diag_report(seq, &instance, &len, data)) continue;
    mp_obj_t items[4] = {
        mp_obj_new_int_from_uint(seq),
        MP_OBJ_NEW_SMALL_INT(instance),
        MP_OBJ_NEW_SMALL_INT(len),
        mp_obj_new_bytes(data, len < USB_HOST_DIAG_REPORT_BYTES ? len : USB_HOST_DIAG_REPORT_BYTES),
    };
    mp_obj_list_append(list, mp_obj_new_tuple(4, items));
  }
  mp_obj_t result[2] = {mp_obj_new_int_from_uint(next), list};
  return mp_obj_new_tuple(2, result);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_usb_host_hid_reports_obj, mod_usb_host_hid_reports);

// Python API: usb_host.setup_log() -> tuple of 9-byte bytes objects
// [dev_addr, bmRequestType, bRequest, wValue(2), wIndex(2), wLength(2)],
// oldest first, last 16 setup packets sent. PIO-USB builds only (empty on
// the native-controller build).
static mp_obj_t mod_usb_host_setup_log(void) {
  uint8_t log[16][9];
  uint32_t count;
  usb_host_core_debug_setup_log(log, &count);
  mp_obj_t list = mp_obj_new_list(0, NULL);
  for (uint32_t i = 0; i < count; i++) {
    mp_obj_list_append(list, mp_obj_new_bytes(log[i], 9));
  }
  return list;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_usb_host_setup_log_obj, mod_usb_host_setup_log);


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
    {MP_ROM_QSTR(MP_QSTR_debug5), MP_ROM_PTR(&mod_usb_host_debug5_obj)},
    {MP_ROM_QSTR(MP_QSTR_dev_info), MP_ROM_PTR(&mod_usb_host_dev_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_hid_info), MP_ROM_PTR(&mod_usb_host_hid_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_hid_reports), MP_ROM_PTR(&mod_usb_host_hid_reports_obj)},
    {MP_ROM_QSTR(MP_QSTR_setup_log), MP_ROM_PTR(&mod_usb_host_setup_log_obj)},
};
static MP_DEFINE_CONST_DICT(usb_host_module_globals,
                            usb_host_module_globals_table);

const mp_obj_module_t mp_module_usb_host = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&usb_host_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_usb_host, mp_module_usb_host);
