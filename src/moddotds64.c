/*
 * dotds64 — MicroPython module hosting the native call_hook fast path for
 * mp/ext/dotds_64dot.py.
 *
 * This is a standalone module deliberately kept OUT of the hd61700 CPU
 * core: it exists purely as a performance backend for one specific
 * extension module (64-dot display mode fix), not as core CPU
 * functionality. The hd61700 core exposes only two small, generic
 * accessors for this purpose (hd61700_get_reg / hd61700_mem_read,
 * declared extern below) — everything about *what* this module does with
 * them lives entirely here.
 *
 * Mirrors mp/ext/dotds_64dot.py's _dotds_override()/_char_display_override()
 * exactly (see that file's docstring for the full rationale of why the
 * override is needed and why it must stay disabled outside 64-dot normal
 * display mode). The Python module still owns hook registration and
 * enable/disable (via the DSPMD mem_write_hook); this module only supplies
 * the per-invocation hot-path logic, called by the C call_hook dispatcher
 * exactly as a Python callback would be, but without any MicroPython
 * object marshaling overhead on the way in or out.
 */
#include "py/runtime.h"
#include "py/mphal.h"
#include "lcd_controller.h"

/* Cross-module accessors (established pattern; see modlcd_controller.c's
   lcd_c_get_state() and modhd61700.c's matching extern declaration). */
extern lcd_state_t *lcd_c_get_state(void);
extern uint8_t hd61700_get_reg(int idx);
extern uint8_t hd61700_mem_read(uint16_t addr);
extern uint8_t hd61700_ram_read(uint16_t ram_offset);

#define DOTDS64_LEDTP_RAM_OFF 0x0201 /* 0x6201 - 0x6000: references/rom0.src LCD dot buffer, RAM-relative */
#define DOTDS64_EDCSR_ADDR 0x68C8

/* dotds64.dotds_hook() — native replacement for DOTDS (&H022C).
   Bulk-transfers LEDTP (current page count * 192 bytes) into monochrome
   VRAM, bit-reversing each byte the same way the real LCDC_CMD_DRAW_BITIMAGE
   write path does (lcd_write()'s reverse_bits8() call) — a raw copy would
   render every 8-pixel column upside down. Reads via hd61700_ram_read()
   (direct buffer access), not hd61700_mem_read(): this is a bulk VRAM
   refresh, not a sequence of individual CPU memory accesses, and must not
   re-trigger hd61700_mem_read()'s per-access UART-RX interrupt/sleep-wake
   side effect up to 1536 times per call. */
static mp_obj_t dotds64_dotds_hook(void) {
  lcd_state_t *lcd = lcd_c_get_state();
  uint32_t pages = lcd_get_num_pages(lcd);
  uint32_t length = pages * 192; /* 32-dot=768B (4 rows) / 64-dot=1536B (8 rows) */
  /* This bulk path bypasses write_vram_pixel_byte() for speed, so it must
     replicate that function's color_vram stamping itself — otherwise, once
     VDP color rendering is active, _pixel_color() reads only color_vram and
     every full-screen refresh done through DOTDS (the primary refresh path
     in 64-dot mode) would silently never reach the physical display. */
  bool vdp_on = lcd->vdp_enabled;
  uint8_t fg = lcd->current_fg_rgb332;
  uint8_t bg = lcd->current_bg_rgb332;
  for (uint32_t i = 0; i < length; i++) {
    uint8_t data = lcd_reverse_bits8(hd61700_ram_read((uint16_t)(DOTDS64_LEDTP_RAM_OFF + i)));
    lcd->vram[i] = data;
    if (vdp_on) {
      uint32_t row_base = (i / LCD_WIDTH) * 8 * LCD_WIDTH + (i % LCD_WIDTH);
      for (int bit = 0; bit < 8; bit++) {
        lcd->color_vram[row_base + (uint32_t)bit * LCD_WIDTH] = (data & (1 << bit)) ? fg : bg;
      }
    }
  }
  if (vdp_on && !lcd->vdp_init_fill_done)
    lcd->vdp_init_fill_done = true;
  lcd->dirty = true;
  for (uint32_t i = 0; i < lcd->active_pages; i++) lcd->dirty_pages[i] = true;

  /* Original DOTDS always finishes by sending the LCD-ON command (&H14);
     see references/rom0.src:570. Without it lcd->display_on stays false
     and the renderer keeps painting the LCD-OFF gray fill. */
  lcd_ctrl(lcd, 0xDF); /* OP=1 (command mode), CE=3 (both chips) */
  lcd_write(lcd, 0x14); /* LCD ON */
  lcd_ctrl(lcd, 0xDE); /* OP=0 (back to data mode) */
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dotds64_dotds_hook_obj, dotds64_dotds_hook);

/* dotds64.char_hook() — native replacement for the 1-char quick display
   routine (&H02BD). The caller (041B normal char display / 02BA-02BC
   cursor blink) pushes the 6-byte dot-pattern source address in registers
   $2 (lo) / $3 (hi) before jumping here (references/rom0.src:665-680).
   Uses the raw EDCSR (ignoring SCTOP) to compute the destination row/col,
   unlike the ROM original which uses (EDCSR-SCTOP) and is therefore
   confined to physical rows 0-3 — see dotds_64dot.py's docstring. */
static mp_obj_t dotds64_char_hook(void) {
  lcd_state_t *lcd = lcd_c_get_state();
  uint16_t src_addr = (uint16_t)hd61700_get_reg(2) | ((uint16_t)hd61700_get_reg(3) << 8);
  uint8_t src[6];
  for (int i = 0; i < 6; i++) {
    src[i] = hd61700_mem_read((uint16_t)(src_addr + i));
  }
  uint8_t edcsr = hd61700_mem_read(DOTDS64_EDCSR_ADDR);
  uint32_t row = edcsr >> 5;
  uint32_t col = edcsr & 0x1F;
  uint32_t dst_off = row * 192 + col * 6;
  uint32_t pages = lcd_get_num_pages(lcd);
  if (dst_off + 6 <= pages * 192) {
    /* Same color_vram sync requirement as dotds64_dotds_hook() above — this
       bypasses write_vram_pixel_byte() too. */
    bool vdp_on = lcd->vdp_enabled;
    uint8_t fg = lcd->current_fg_rgb332;
    uint8_t bg = lcd->current_bg_rgb332;
    for (int i = 0; i < 6; i++) {
      uint8_t data = lcd_reverse_bits8(src[i]);
      uint32_t off = dst_off + (uint32_t)i;
      lcd->vram[off] = data;
      if (vdp_on) {
        uint32_t row_base = (off / LCD_WIDTH) * 8 * LCD_WIDTH + (off % LCD_WIDTH);
        for (int bit = 0; bit < 8; bit++) {
          lcd->color_vram[row_base + (uint32_t)bit * LCD_WIDTH] = (data & (1 << bit)) ? fg : bg;
        }
      }
    }
    if (vdp_on && !lcd->vdp_init_fill_done)
      lcd->vdp_init_fill_done = true;
    lcd->dirty = true;
    for (uint32_t i = 0; i < lcd->active_pages; i++) lcd->dirty_pages[i] = true;
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(dotds64_char_hook_obj, dotds64_char_hook);

static const mp_rom_map_elem_t dotds64_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_dotds64)},
    {MP_ROM_QSTR(MP_QSTR_dotds_hook), MP_ROM_PTR(&dotds64_dotds_hook_obj)},
    {MP_ROM_QSTR(MP_QSTR_char_hook), MP_ROM_PTR(&dotds64_char_hook_obj)},
};
static MP_DEFINE_CONST_DICT(dotds64_module_globals, dotds64_module_globals_table);

const mp_obj_module_t mp_module_dotds64 = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&dotds64_module_globals,
};
MP_REGISTER_MODULE(MP_QSTR_dotds64, mp_module_dotds64);
