/*
 * MicroPython C Module wrapper for PB-1000 LCD Controller
 * Exposes LCD controller core to MicroPython as 'lcd_c' module
 */
#include "lcd_controller.h"
#include "py/binary.h"
#include "py/obj.h"
#include "py/objarray.h"
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

/* lcd_c.get_vram_view() -> bytearray (direct writable reference to C static
   array, mirrors get_color_vram()). Lets Python bulk-write the mono LCD
   framebuffer (e.g. via memoryview slice assignment) without per-byte
   write()/ctrl() protocol calls — needed for high-frequency callers like a
   DOTDS call_hook override. Remember to call mark_dirty() after writing. */
static mp_obj_t mod_lcd_get_vram_view(void) {
  return mp_obj_new_bytearray_by_ref(LCD_VRAM_SIZE, lcd_state.vram);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_vram_view_obj,
                                  mod_lcd_get_vram_view);

/* lcd_c.blit_reversed(src, dst_offset) — bulk-copy src (any buffer-protocol
   object, e.g. a memoryview slice) into lcd_state.vram starting at
   dst_offset, bit-reversing every byte on the way in.
   Data written via the real LCD protocol (lcd_write(), mode
   LCDC_CMD_DRAW_BITIMAGE) is bit-reversed before landing in vram — see
   lcd_write()'s `reverse_bits8(data)` call. A raw memcpy from a
   ROM-format source buffer (e.g. LEDTP) into vram skips that step and
   renders each 8-pixel column upside down. This gives callers that bypass
   the per-byte protocol (for speed) a bulk equivalent.
   Remember to call mark_dirty() after writing. */
static mp_obj_t mod_lcd_blit_reversed(mp_obj_t src_obj, mp_obj_t dst_off_obj) {
  mp_buffer_info_t bufinfo;
  mp_get_buffer_raise(src_obj, &bufinfo, MP_BUFFER_READ);
  mp_int_t dst_off = mp_obj_get_int(dst_off_obj);
  mp_int_t len = (mp_int_t)bufinfo.len;
  if (dst_off < 0 || len < 0 || dst_off + len > LCD_VRAM_SIZE) {
    mp_raise_ValueError(MP_ERROR_TEXT("blit_reversed: out of range"));
  }
  const uint8_t *src = (const uint8_t *)bufinfo.buf;
  for (mp_int_t i = 0; i < len; i++) {
    lcd_state.vram[dst_off + i] = lcd_reverse_bits8(src[i]);
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_lcd_blit_reversed_obj,
                                  mod_lcd_blit_reversed);

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

/* lcd_c.mark_dirty() — mark all pages dirty so next render repaints the display */
static mp_obj_t mod_lcd_mark_dirty(void) {
  lcd_state.dirty = true;
  for (int i = 0; i < lcd_state.active_pages; i++)
    lcd_state.dirty_pages[i] = true;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_mark_dirty_obj, mod_lcd_mark_dirty);

/* lcd_c.get_num_pages() -> int — returns active page count (4=32-dot, 8=64-dot) */
static mp_obj_t mod_lcd_get_num_pages(void) {
  return MP_OBJ_NEW_SMALL_INT(lcd_get_num_pages(&lcd_state));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_num_pages_obj, mod_lcd_get_num_pages);

/* lcd_c.set_num_pages(pages) — set active page count (4 or 8) */
static mp_obj_t mod_lcd_set_num_pages(mp_obj_t pages_obj) {
  uint8_t pages = (uint8_t)mp_obj_get_int(pages_obj);
  lcd_set_num_pages(&lcd_state, pages);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_num_pages_obj, mod_lcd_set_num_pages);

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

/* lcd_c.set_bg_colors(on_bg, off_bg) */
static mp_obj_t mod_lcd_set_bg_colors(mp_obj_t on_bg_obj, mp_obj_t off_bg_obj) {
  uint16_t on_bg = (uint16_t)mp_obj_get_int(on_bg_obj);
  uint16_t off_bg = (uint16_t)mp_obj_get_int(off_bg_obj);
  lcd_set_bg_colors(&lcd_state, on_bg, off_bg);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_lcd_set_bg_colors_obj,
                                 mod_lcd_set_bg_colors);

/* lcd_c.set_colors(fg, bg) — set ON-pixel color (fg) and OFF-pixel color (bg).
   Does not force a full repaint; only pages dirtied by subsequent LCD writes
   are re-rendered with the new colors. */
static mp_obj_t mod_lcd_set_colors(mp_obj_t fg_obj, mp_obj_t bg_obj) {
  uint16_t fg = (uint16_t)mp_obj_get_int(fg_obj);
  uint16_t bg = (uint16_t)mp_obj_get_int(bg_obj);
  lcd_set_colors(&lcd_state, fg, bg);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_lcd_set_colors_obj, mod_lcd_set_colors);

/* lcd_c.set_scale(num, den=1) */
static mp_obj_t mod_lcd_set_scale(size_t n_args, const mp_obj_t *args) {
  uint8_t num = (uint8_t)mp_obj_get_int(args[0]);
  uint8_t den = (n_args > 1) ? (uint8_t)mp_obj_get_int(args[1]) : 1;
  lcd_set_scale_ratio(&lcd_state, num, den);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lcd_set_scale_obj, 1, 2,
                                           mod_lcd_set_scale);

/* lcd_c.setup_display(spi_id, cs_pin, dc_pin, scale, x_offset, y_offset, baudrate=0) */
static mp_obj_t mod_lcd_setup_display(size_t n_args, const mp_obj_t *args) {
  int spi_id = mp_obj_get_int(args[0]);
  uint8_t cs_pin = (uint8_t)mp_obj_get_int(args[1]);
  uint8_t dc_pin = (uint8_t)mp_obj_get_int(args[2]);
  uint8_t scale = (n_args > 3) ? (uint8_t)mp_obj_get_int(args[3]) : 1;
  uint16_t x_off = (n_args > 4) ? (uint16_t)mp_obj_get_int(args[4]) : 0;
  uint16_t y_off = (n_args > 5) ? (uint16_t)mp_obj_get_int(args[5]) : 0;
  uint32_t baud  = (n_args > 6) ? (uint32_t)mp_obj_get_int(args[6]) : 0;

  void *spi_inst_ptr = NULL;
#ifdef __arm__
  spi_inst_ptr = (spi_id == 0) ? (void *)spi0 : (void *)spi1;
#endif

  lcd_setup_display(&lcd_state, spi_inst_ptr, cs_pin, dc_pin, scale, x_off,
                    y_off, baud);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lcd_setup_display_obj, 3, 7,
                                           mod_lcd_setup_display);

/* lcd_c.render() */
static mp_obj_t mod_lcd_render(void) {
  lcd_render_to_display(&lcd_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_render_obj, mod_lcd_render);

/* lcd_c.get_color_vram() -> bytearray (direct reference to C static array) */
static mp_obj_t mod_lcd_get_color_vram(void) {
  return mp_obj_new_bytearray_by_ref(LCD_COLOR_VRAM_SIZE, lcd_state.color_vram);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_color_vram_obj, mod_lcd_get_color_vram);

/* lcd_c.vdp_write(reg, data) — reg is 0-4 (offset - 0x0C20) */
static mp_obj_t mod_lcd_vdp_write(mp_obj_t reg_obj, mp_obj_t data_obj) {
  uint32_t reg  = (uint32_t)mp_obj_get_int(reg_obj);
  uint8_t  data = (uint8_t)mp_obj_get_int(data_obj);
  lcd_vdp_write(&lcd_state, reg, data);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_lcd_vdp_write_obj, mod_lcd_vdp_write);

/* lcd_c.vdp_read(reg) -> int — reg is 0-4 (offset - 0x0C20) */
static mp_obj_t mod_lcd_vdp_read(mp_obj_t reg_obj) {
  uint32_t reg = (uint32_t)mp_obj_get_int(reg_obj);
  return MP_OBJ_NEW_SMALL_INT(lcd_vdp_read(&lcd_state, reg));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_vdp_read_obj, mod_lcd_vdp_read);

/* lcd_c.set_vdp_enable(bool) — enable/disable per-pixel color VRAM rendering */
static mp_obj_t mod_lcd_set_vdp_enable(mp_obj_t enabled_obj) {
  lcd_set_vdp_enable(&lcd_state, mp_obj_is_true(enabled_obj));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_vdp_enable_obj, mod_lcd_set_vdp_enable);

/* lcd_c.get_vdp_enable() -> bool */
static mp_obj_t mod_lcd_get_vdp_enable(void) {
  return mp_obj_new_bool(lcd_get_vdp_enable(&lcd_state));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_vdp_enable_obj, mod_lcd_get_vdp_enable);

/* lcd_c.vdp_sync_enable() — sync vram→color_vram pages 0-3 then enable VDP. */
static mp_obj_t mod_lcd_vdp_sync_enable(void) {
  lcd_vdp_sync_enable(&lcd_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_vdp_sync_enable_obj, mod_lcd_vdp_sync_enable);

/* lcd_c.vdp_init_done() -> bool  (legacy; kept for compatibility) */
static mp_obj_t mod_lcd_vdp_init_done(void) {
  return mp_obj_new_bool(lcd_get_vdp_init_done(&lcd_state));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_vdp_init_done_obj, mod_lcd_vdp_init_done);

/* lcd_c.set_vdp_init_done(bool) — force the "VDP has real color data" flag.
   Needed after writing color_vram directly via get_color_vram() (bypassing
   vdp_write()), e.g. vram_loader, so _pixel_color() renders from color_vram
   immediately instead of falling back to the mono vram bitmap. */
static mp_obj_t mod_lcd_set_vdp_init_done(mp_obj_t done_obj) {
  lcd_set_vdp_init_done(&lcd_state, mp_obj_is_true(done_obj));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_set_vdp_init_done_obj, mod_lcd_set_vdp_init_done);

/* lcd_c.vdp_any_write() -> bool
   True if any VDP reg2 write has occurred since the last VDP disable (reset).
   Python uses this together with vdp_write_count() to detect write activity. */
static mp_obj_t mod_lcd_vdp_any_write(void) {
  return mp_obj_new_bool(lcd_get_vdp_any_write(&lcd_state));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_vdp_any_write_obj, mod_lcd_vdp_any_write);

/* lcd_c.vdp_write_count() -> int
   Monotone counter incremented on every VDP reg2 write since the last VDP
   disable (reset).  Python polls this; when the count stops changing for
   >= 300 ms, the ROM has finished its VDP init and vdp_sync_enable() is safe. */
static mp_obj_t mod_lcd_vdp_write_count(void) {
  return mp_obj_new_int_from_uint(lcd_get_vdp_write_count(&lcd_state));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_vdp_write_count_obj, mod_lcd_vdp_write_count);

/* lcd_c.wait_for_idle() */
static mp_obj_t mod_lcd_wait_for_idle(void) {
#ifdef __arm__
  lcd_wait_for_idle(&lcd_state);
#endif
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_wait_for_idle_obj, mod_lcd_wait_for_idle);

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
    {MP_ROM_QSTR(MP_QSTR_get_vram_view), MP_ROM_PTR(&mod_lcd_get_vram_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_blit_reversed), MP_ROM_PTR(&mod_lcd_blit_reversed_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_vram_byte),
     MP_ROM_PTR(&mod_lcd_get_vram_byte_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_dirty), MP_ROM_PTR(&mod_lcd_is_dirty_obj)},
    {MP_ROM_QSTR(MP_QSTR_clear_dirty), MP_ROM_PTR(&mod_lcd_clear_dirty_obj)},
    {MP_ROM_QSTR(MP_QSTR_mark_dirty), MP_ROM_PTR(&mod_lcd_mark_dirty_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_display_on),
     MP_ROM_PTR(&mod_lcd_is_display_on_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_x_mirror), MP_ROM_PTR(&mod_lcd_set_x_mirror_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_draw_bitimage_reverse),
     MP_ROM_PTR(&mod_lcd_set_draw_bitimage_reverse_obj)},
    {MP_ROM_QSTR(MP_QSTR_load_charset), MP_ROM_PTR(&mod_lcd_load_charset_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_debug), MP_ROM_PTR(&mod_lcd_set_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_bg_colors),
     MP_ROM_PTR(&mod_lcd_set_bg_colors_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_colors),
     MP_ROM_PTR(&mod_lcd_set_colors_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_scale), MP_ROM_PTR(&mod_lcd_set_scale_obj)},
    {MP_ROM_QSTR(MP_QSTR_setup_display),
     MP_ROM_PTR(&mod_lcd_setup_display_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_color_vram), MP_ROM_PTR(&mod_lcd_get_color_vram_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_vdp_enable), MP_ROM_PTR(&mod_lcd_set_vdp_enable_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_vdp_enable), MP_ROM_PTR(&mod_lcd_get_vdp_enable_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_sync_enable), MP_ROM_PTR(&mod_lcd_vdp_sync_enable_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_init_done), MP_ROM_PTR(&mod_lcd_vdp_init_done_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_vdp_init_done), MP_ROM_PTR(&mod_lcd_set_vdp_init_done_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_any_write), MP_ROM_PTR(&mod_lcd_vdp_any_write_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_write_count), MP_ROM_PTR(&mod_lcd_vdp_write_count_obj)},
    {MP_ROM_QSTR(MP_QSTR_render), MP_ROM_PTR(&mod_lcd_render_obj)},
    {MP_ROM_QSTR(MP_QSTR_wait_for_idle), MP_ROM_PTR(&mod_lcd_wait_for_idle_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_write), MP_ROM_PTR(&mod_lcd_vdp_write_obj)},
    {MP_ROM_QSTR(MP_QSTR_vdp_read),  MP_ROM_PTR(&mod_lcd_vdp_read_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_num_pages), MP_ROM_PTR(&mod_lcd_get_num_pages_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_num_pages), MP_ROM_PTR(&mod_lcd_set_num_pages_obj)},
    /* Constants */
    {MP_ROM_QSTR(MP_QSTR_WIDTH), MP_ROM_INT(LCD_WIDTH)},
    {MP_ROM_QSTR(MP_QSTR_HEIGHT), MP_ROM_INT(LCD_HEIGHT)},
    {MP_ROM_QSTR(MP_QSTR_VRAM_SIZE), MP_ROM_INT(LCD_VRAM_SIZE)},
    {MP_ROM_QSTR(MP_QSTR_COLOR_VRAM_SIZE), MP_ROM_INT(LCD_COLOR_VRAM_SIZE)},
};
static MP_DEFINE_CONST_DICT(lcd_c_module_globals, lcd_c_module_globals_table);

const mp_obj_module_t lcd_c_user_cmodule = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&lcd_c_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_lcd_c, lcd_c_user_cmodule);
