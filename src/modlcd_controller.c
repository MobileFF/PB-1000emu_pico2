/*
 * MicroPython C Module wrapper for PB-1000 LCD Controller
 * Exposes LCD controller core to MicroPython as 'lcd_c' module
 */
#include "lcd_controller.h"
#include "py/binary.h"
#include "py/obj.h"
#include "py/runtime.h"

#ifdef __arm__
#include "hardware/spi.h"
#endif

/* Static LCD state */
static lcd_state_t lcd_state;

/* Public accessor for C-to-C direct integration */
lcd_state_t *lcd_c_get_state(void) { return &lcd_state; }

/* ====== Module functions exposed to Python ====== */

/* lcd_c.init() */
static mp_obj_t mod_lcd_init(void) {
  lcd_init(&lcd_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_init_obj, mod_lcd_init);

/* lcd_c.clear() */
static mp_obj_t mod_lcd_clear(void) {
  lcd_clear(&lcd_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_clear_obj, mod_lcd_clear);

/* lcd_c.ctrl(data) */
static mp_obj_t mod_lcd_ctrl(mp_obj_t data_obj) {
  uint8_t data = (uint8_t)mp_obj_get_int(data_obj);
  lcd_ctrl(&lcd_state, data);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_ctrl_obj, mod_lcd_ctrl);

/* lcd_c.write(data) */
static mp_obj_t mod_lcd_write(mp_obj_t data_obj) {
  uint8_t data = (uint8_t)mp_obj_get_int(data_obj);
  lcd_write(&lcd_state, data);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_write_obj, mod_lcd_write);

/* lcd_c.read() -> int */
static mp_obj_t mod_lcd_read(void) {
  uint8_t val = lcd_read(&lcd_state);
  return MP_OBJ_NEW_SMALL_INT(val);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_read_obj, mod_lcd_read);

/* lcd_c.get_pixel(x, y) -> bool */
static mp_obj_t mod_lcd_get_pixel(mp_obj_t x_obj, mp_obj_t y_obj) {
  int x = mp_obj_get_int(x_obj);
  int y = mp_obj_get_int(y_obj);
  return mp_obj_new_bool(lcd_get_pixel(&lcd_state, x, y));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_lcd_get_pixel_obj, mod_lcd_get_pixel);

/* lcd_c.get_vram() -> bytes (copy of VRAM) */
static mp_obj_t mod_lcd_get_vram(void) {
  return mp_obj_new_bytes(lcd_state.vram, LCD_VRAM_SIZE);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_vram_obj, mod_lcd_get_vram);

/* lcd_c.get_vram_byte(offset) -> int */
static mp_obj_t mod_lcd_get_vram_byte(mp_obj_t off_obj) {
  int off = mp_obj_get_int(off_obj);
  if (off < 0 || off >= LCD_VRAM_SIZE) {
    return MP_OBJ_NEW_SMALL_INT(0);
  }
  return MP_OBJ_NEW_SMALL_INT(lcd_state.vram[off]);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_get_vram_byte_obj,
                                 mod_lcd_get_vram_byte);

/* lcd_c.is_dirty() -> bool */
static mp_obj_t mod_lcd_is_dirty(void) {
  return mp_obj_new_bool(lcd_state.dirty);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_is_dirty_obj, mod_lcd_is_dirty);

/* lcd_c.clear_dirty() */
static mp_obj_t mod_lcd_clear_dirty(void) {
  lcd_state.dirty = false;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_clear_dirty_obj, mod_lcd_clear_dirty);

/* lcd_c.is_display_on() -> bool */
static mp_obj_t mod_lcd_is_display_on(void) {
  return mp_obj_new_bool(lcd_state.display_on);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_is_display_on_obj,
                                 mod_lcd_is_display_on);

/* lcd_c.set_x_mirror(enabled) */
static mp_obj_t mod_lcd_set_x_mirror(mp_obj_t enabled_obj) {
  lcd_set_x_mirror(&lcd_state, mp_obj_is_true(enabled_obj));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_x_mirror_obj,
                                 mod_lcd_set_x_mirror);

/* lcd_c.set_draw_bitimage_reverse(enabled) */
static mp_obj_t mod_lcd_set_draw_bitimage_reverse(mp_obj_t enabled_obj) {
  lcd_set_draw_bitimage_reverse(&lcd_state, mp_obj_is_true(enabled_obj));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_draw_bitimage_reverse_obj,
                                 mod_lcd_set_draw_bitimage_reverse);

/* lcd_c.load_charset(bytes_data) */
static mp_obj_t mod_lcd_load_charset(mp_obj_t buf_obj) {
  mp_buffer_info_t buf;
  mp_get_buffer_raise(buf_obj, &buf, MP_BUFFER_READ);
  lcd_load_charset(&lcd_state, buf.buf, (int)buf.len);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_load_charset_obj,
                                 mod_lcd_load_charset);

/* lcd_c.set_debug(enabled) */
static mp_obj_t mod_lcd_set_debug(mp_obj_t enabled_obj) {
  lcd_state.debug = mp_obj_is_true(enabled_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_debug_obj, mod_lcd_set_debug);

/* lcd_c.setup_display(spi_id, cs_pin, dc_pin, scale, x_offset, y_offset) */
static mp_obj_t mod_lcd_setup_display(size_t n_args, const mp_obj_t *args) {
  int spi_id = mp_obj_get_int(args[0]);
  uint8_t cs_pin = (uint8_t)mp_obj_get_int(args[1]);
  uint8_t dc_pin = (uint8_t)mp_obj_get_int(args[2]);
  uint8_t scale = (n_args > 3) ? (uint8_t)mp_obj_get_int(args[3]) : 1;
  uint16_t x_off = (n_args > 4) ? (uint16_t)mp_obj_get_int(args[4]) : 0;
  uint16_t y_off = (n_args > 5) ? (uint16_t)mp_obj_get_int(args[5]) : 0;

  void *spi_inst_ptr = NULL;
#ifdef __arm__
  spi_inst_ptr = (spi_id == 0) ? (void *)spi0 : (void *)spi1;
#endif

  lcd_setup_display(&lcd_state, spi_inst_ptr, cs_pin, dc_pin, scale, x_off,
                    y_off);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lcd_setup_display_obj, 3, 6,
                                           mod_lcd_setup_display);

/* lcd_c.render() */
static mp_obj_t mod_lcd_render(void) {
  lcd_render_to_display(&lcd_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_render_obj, mod_lcd_render);

/* lcd_c.set_page(page) - for legacy test compatibility */
static mp_obj_t mod_lcd_set_page(mp_obj_t page_obj) {
  lcd_state.page = (uint8_t)(mp_obj_get_int(page_obj) & 0x03);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_page_obj, mod_lcd_set_page);

/* lcd_c.set_column(column) - for legacy test compatibility */
static mp_obj_t mod_lcd_set_column(mp_obj_t col_obj) {
  int col = mp_obj_get_int(col_obj);
  lcd_state.column = (uint8_t)(col % LCD_WIDTH);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_column_obj, mod_lcd_set_column);

/* ====== Module definition ====== */
static const mp_rom_map_elem_t lcd_c_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_lcd_c)},
    {MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&mod_lcd_init_obj)},
    {MP_ROM_QSTR(MP_QSTR_clear), MP_ROM_PTR(&mod_lcd_clear_obj)},
    {MP_ROM_QSTR(MP_QSTR_ctrl), MP_ROM_PTR(&mod_lcd_ctrl_obj)},
    {MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mod_lcd_write_obj)},
    {MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mod_lcd_read_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_pixel), MP_ROM_PTR(&mod_lcd_get_pixel_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_vram), MP_ROM_PTR(&mod_lcd_get_vram_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_vram_byte),
     MP_ROM_PTR(&mod_lcd_get_vram_byte_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_dirty), MP_ROM_PTR(&mod_lcd_is_dirty_obj)},
    {MP_ROM_QSTR(MP_QSTR_clear_dirty), MP_ROM_PTR(&mod_lcd_clear_dirty_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_display_on),
     MP_ROM_PTR(&mod_lcd_is_display_on_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_x_mirror), MP_ROM_PTR(&mod_lcd_set_x_mirror_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_draw_bitimage_reverse),
     MP_ROM_PTR(&mod_lcd_set_draw_bitimage_reverse_obj)},
    {MP_ROM_QSTR(MP_QSTR_load_charset), MP_ROM_PTR(&mod_lcd_load_charset_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_debug), MP_ROM_PTR(&mod_lcd_set_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_page), MP_ROM_PTR(&mod_lcd_set_page_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_column), MP_ROM_PTR(&mod_lcd_set_column_obj)},
    {MP_ROM_QSTR(MP_QSTR_setup_display),
     MP_ROM_PTR(&mod_lcd_setup_display_obj)},
    {MP_ROM_QSTR(MP_QSTR_render), MP_ROM_PTR(&mod_lcd_render_obj)},
    /* Constants */
    {MP_ROM_QSTR(MP_QSTR_WIDTH), MP_ROM_INT(LCD_WIDTH)},
    {MP_ROM_QSTR(MP_QSTR_HEIGHT), MP_ROM_INT(LCD_HEIGHT)},
    {MP_ROM_QSTR(MP_QSTR_VRAM_SIZE), MP_ROM_INT(LCD_VRAM_SIZE)},
};
static MP_DEFINE_CONST_DICT(lcd_c_module_globals, lcd_c_module_globals_table);

const mp_obj_module_t lcd_c_user_cmodule = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&lcd_c_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_lcd_c, lcd_c_user_cmodule);
