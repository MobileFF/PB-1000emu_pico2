/*
 * dvi_display — MicroPython module wrapper around dvi_display.c.
 *
 * Only meaningful on the WAVESHARE_RP2350_PIZERO board build (see
 * dvi_display.c's header comment and micropython.cmake's PB1000_IS_PIZERO
 * block); on every other board this module still exists (so `import
 * dvi_display` never raises ImportError) but init() always returns False,
 * since dvi_display_init() itself is a no-op stub there (see
 * dvi_display.c's #else branch).
 *
 * Phase 1 (2026-10-21): init() only brings up a static color-bar test
 * pattern -- see dvi_display.c's own header comment for scope.
 *
 * Phase 2a (2026-10-23): set_live_content(True) switches the scanline
 * producer to live PB-1000 VRAM content -- see mp/display_init.py's DVI
 * branch (the only call site so far) and dvi_display.h's own comment.
 */
#include "py/runtime.h"
#include "py/mphal.h"
#include "dvi_display.h"

static mp_obj_t dvi_display_init_(void) {
  return dvi_display_init() ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_init_obj, dvi_display_init_);

/* Returns (heartbeat, late_ctr) -- see dvi_display.h's own comment on
 * dvi_display_debug_info() for how to use these to tell "core1 crashed"
 * apart from "core1 alive but producing wrong content" without a fresh
 * reflash, while bringing this up on real hardware. */
static mp_obj_t dvi_display_debug_info_(void) {
  uint32_t heartbeat, late_ctr;
  dvi_display_debug_info(&heartbeat, &late_ctr);
  mp_obj_t tuple[2] = {
      mp_obj_new_int_from_uint(heartbeat),
      mp_obj_new_int_from_uint(late_ctr),
  };
  return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_debug_info_obj, dvi_display_debug_info_);

/* Returns (resyncs, pump_max_us) -- see dvi_display.h. */
static mp_obj_t dvi_display_timing_info_(void) {
  uint32_t resyncs, pump_max_us;
  dvi_display_timing_info(&resyncs, &pump_max_us);
  mp_obj_t tuple[2] = {
      mp_obj_new_int_from_uint(resyncs),
      mp_obj_new_int_from_uint(pump_max_us),
  };
  return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_timing_info_obj, dvi_display_timing_info_);

static mp_obj_t dvi_display_set_live_content_(mp_obj_t enable_obj) {
  dvi_display_set_live_content(mp_obj_is_true(enable_obj));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dvi_display_set_live_content_obj, dvi_display_set_live_content_);

/* TEMPORARY DIAGNOSTIC (2026-10-25) -- see dvi_display.c's isr_hardfault
 * override comment. Remove once isolated. */
static mp_obj_t dvi_display_fault_info_(void) {
  uint32_t occurred, cfsr, bfar, hfsr;
  dvi_display_fault_info(&occurred, &cfsr, &bfar, &hfsr);
  mp_obj_t tuple[4] = {
      mp_obj_new_int_from_uint(occurred),
      mp_obj_new_int_from_uint(cfsr),
      mp_obj_new_int_from_uint(bfar),
      mp_obj_new_int_from_uint(hfsr),
  };
  return mp_obj_new_tuple(4, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_fault_info_obj, dvi_display_fault_info_);

/* Permanent diagnostic (added 2026-10-28) -- see dvi_display.c's
 * dvi_fault_log_persist() comment. Returns (magic_ok, cfsr, bfar, hfsr,
 * pc, lr, core_num) from a PREVIOUS boot's crash, persisted to flash
 * since the RAM-only fault_info() above doesn't survive the power cycle
 * needed to recover from a hang where USB/REPL never comes back. */
static mp_obj_t dvi_display_read_fault_log_(void) {
  uint32_t magic_ok, cfsr, bfar, hfsr, pc, lr, core_num;
  dvi_display_read_fault_log(&magic_ok, &cfsr, &bfar, &hfsr, &pc, &lr, &core_num);
  mp_obj_t tuple[7] = {
      mp_obj_new_int_from_uint(magic_ok),
      mp_obj_new_int_from_uint(cfsr),
      mp_obj_new_int_from_uint(bfar),
      mp_obj_new_int_from_uint(hfsr),
      mp_obj_new_int_from_uint(pc),
      mp_obj_new_int_from_uint(lr),
      mp_obj_new_int_from_uint(core_num),
  };
  return mp_obj_new_tuple(7, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_read_fault_log_obj, dvi_display_read_fault_log_);

static mp_obj_t dvi_display_clear_fault_log_(void) {
  dvi_display_clear_fault_log();
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_clear_fault_log_obj, dvi_display_clear_fault_log_);

/* Permanent diagnostic -- see dvi_display.h's dvi_display_pio_debug_info()
 * comment. Raw FDEBUG register value; decode in Python
 * (TXSTALL = (fdebug >> 24) & 0xF, one bit per PIO SM). */
static mp_obj_t dvi_display_pio_debug_info_(void) {
  uint32_t fdebug;
  dvi_display_pio_debug_info(&fdebug);
  return mp_obj_new_int_from_uint(fdebug);
}
static MP_DEFINE_CONST_FUN_OBJ_0(dvi_display_pio_debug_info_obj, dvi_display_pio_debug_info_);

/* ui_fill_rect(x, y, w, h, color) -- see dvi_display.h. Same signature as
 * the SPI panel drivers' fill_rect(), so mp/display_init.py binds it
 * directly as its DVI display object's fill_rect. */
static mp_obj_t dvi_display_ui_fill_rect_(size_t n_args, const mp_obj_t *args) {
  dvi_display_ui_fill_rect(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                           mp_obj_get_int(args[2]), mp_obj_get_int(args[3]),
                           (uint16_t)mp_obj_get_int(args[4]));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dvi_display_ui_fill_rect_obj, 5, 5, dvi_display_ui_fill_rect_);

/* ui_blit(x0, y0, x1, y1, buf) -- see dvi_display.h. */
static mp_obj_t dvi_display_ui_blit_(size_t n_args, const mp_obj_t *args) {
  mp_buffer_info_t bufinfo;
  mp_get_buffer_raise(args[4], &bufinfo, MP_BUFFER_READ);
  dvi_display_ui_blit(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                      mp_obj_get_int(args[2]), mp_obj_get_int(args[3]),
                      (const uint8_t *)bufinfo.buf, bufinfo.len);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dvi_display_ui_blit_obj, 5, 5, dvi_display_ui_blit_);

/* set_lcd_window(x, y, scale_num, scale_den) -- see dvi_display.h. */
static mp_obj_t dvi_display_set_lcd_window_(size_t n_args, const mp_obj_t *args) {
  dvi_display_set_lcd_window(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                             mp_obj_get_int(args[2]), mp_obj_get_int(args[3]));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dvi_display_set_lcd_window_obj, 4, 4, dvi_display_set_lcd_window_);

static const mp_rom_map_elem_t dvi_display_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_dvi_display)},
    {MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&dvi_display_init_obj)},
    {MP_ROM_QSTR(MP_QSTR_debug_info), MP_ROM_PTR(&dvi_display_debug_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_timing_info), MP_ROM_PTR(&dvi_display_timing_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_live_content), MP_ROM_PTR(&dvi_display_set_live_content_obj)},
    {MP_ROM_QSTR(MP_QSTR_fault_info), MP_ROM_PTR(&dvi_display_fault_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_read_fault_log), MP_ROM_PTR(&dvi_display_read_fault_log_obj)},
    {MP_ROM_QSTR(MP_QSTR_clear_fault_log), MP_ROM_PTR(&dvi_display_clear_fault_log_obj)},
    {MP_ROM_QSTR(MP_QSTR_pio_debug_info), MP_ROM_PTR(&dvi_display_pio_debug_info_obj)},
    {MP_ROM_QSTR(MP_QSTR_ui_fill_rect), MP_ROM_PTR(&dvi_display_ui_fill_rect_obj)},
    {MP_ROM_QSTR(MP_QSTR_ui_blit), MP_ROM_PTR(&dvi_display_ui_blit_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_lcd_window), MP_ROM_PTR(&dvi_display_set_lcd_window_obj)},
};
static MP_DEFINE_CONST_DICT(dvi_display_module_globals, dvi_display_module_globals_table);

const mp_obj_module_t mp_module_dvi_display = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&dvi_display_module_globals,
};
MP_REGISTER_MODULE(MP_QSTR_dvi_display, mp_module_dvi_display);
