/*
 * PB-1000 LCD Controller Emulation - C Implementation
 * C port of lcd_controller.py for MicroPython integration.
 * Handles VRAM management and LCD command processing.
 */
#include "lcd_controller.h"
#include <stdio.h>
#include <string.h>

/* ======== Internal helpers ======== */

static uint8_t reverse_bits8(uint8_t v) {
  uint8_t r = 0;
  for (int i = 0; i < 8; i++) {
    r = (r << 1) | (v & 1);
    v >>= 1;
  }
  return r;
}

static int command_length(uint8_t mode_byte) {
  uint8_t cmd = mode_byte & 0x0F;
  switch (cmd) {
  case LCDC_CMD_READ:
  case LCDC_CMD_DRAW_BITIMAGE:
  case LCDC_CMD_DRAW_CHAR:
  case LCDC_CMD_SPRITE_DEF_GRAPHIC:
  case LCDC_CMD_SPRITE_DEF_CHAR:
  case LCDC_CMD_USER_CHAR_DEF:
  case LCDC_CMD_CONTRAST:
  case LCDC_CMD_SET_SPRITE_POS:
    return 3;
  default:
    return 1;
  }
}

static int mode_to_chip(uint8_t mode_byte) {
  return (mode_byte & 0x10) ? 1 : 0;
}

static void mark_all_dirty(lcd_state_t *lcd) {
  lcd->dirty = true;
  for (int i = 0; i < lcd->active_pages; i++) {
    lcd->dirty_pages[i] = true;
  }
}

static void set_display_on_state(lcd_state_t *lcd, bool enabled) {
  if (lcd->display_on == enabled)
    return;
  lcd->display_on = enabled;
  /* ON/OFF transition should be immediately visible on panel. */
  mark_all_dirty(lcd);
}

static void write_vram_pixel_byte(lcd_state_t *lcd, int chip, int x_local,
                                  int y_page, uint8_t data) {
  if (y_page < 0 || y_page >= (int)lcd->active_pages)
    return;
  if (x_local < 0 || x_local >= 96)
    return;
  if (lcd->x_mirror) {
    x_local = 95 - x_local;
  }
  int x = x_local + (chip ? 96 : 0);
  int off = y_page * LCD_WIDTH + x;
  if (off >= 0 && off < LCD_VRAM_SIZE) {
    uint8_t old = lcd->vram[off];
    lcd->vram[off] = data;
    /* Update color_vram when:
       - pixel data changed (new content), OR
       - data is zero (CLS / blank write) so bg color is always current */
    if (data != old || data == 0) {
      lcd->dirty = true;
      lcd->dirty_pages[y_page] = true;
      /* Only stamp color_vram when VDP is active.  While VDP is disabled
         (e.g. right after a reset) we leave color_vram untouched so that
         program-set VDP colors (0xFF white) cannot corrupt the cleared buffer. */
      if (lcd->vdp_enabled) {
        uint8_t fg = lcd->current_fg_rgb332;
        uint8_t bg = lcd->current_bg_rgb332;
        int row_base = y_page * 8 * LCD_WIDTH + x;
        for (int bit = 0; bit < 8; bit++) {
          lcd->color_vram[row_base + bit * LCD_WIDTH] =
              (data & (1 << bit)) ? fg : bg;
        }
      }
    }
  }
}

static uint8_t read_vram_pixel_byte(lcd_state_t *lcd, int chip, int x_local,
                                    int y_page) {
  if (y_page < 0 || y_page >= (int)lcd->active_pages)
    return 0xFF;
  if (x_local < 0 || x_local >= 96)
    return 0xFF;
  if (lcd->x_mirror) {
    x_local = 95 - x_local;
  }
  int x = x_local + (chip ? 96 : 0);
  int off = y_page * LCD_WIDTH + x;
  if (off >= 0 && off < LCD_VRAM_SIZE) {
    return lcd->vram[off];
  }
  return 0xFF;
}

static void advance_xy(lcd_state_t *lcd, lcd_chip_state_t *st, bool draw_char,
                       bool reverse) {
  int step = draw_char ? lcd->char_width : 1;
  if (reverse && !draw_char) {
    st->x -= step;
    if (st->x < 0) {
      st->x = 95;
      st->y = (st->y + 1) % lcd->active_pages;
    }
  } else {
    st->x += step;
    if (st->x >= 96) {
      st->x = 0;
      st->y = (st->y + 1) % lcd->active_pages;
    }
  }
}

static void char_code_to_bitmap(lcd_state_t *lcd, uint8_t data, uint8_t *cols,
                                int *out_width) {
  /* PB-1000 character data is 4-bit swapped in many BIOS paths */
  uint8_t code = ((data & 0x0F) << 4) | (data >> 4);
  int width = lcd->char_width;
  if (width < 5)
    width = 5;
  if (width > 8)
    width = 8;

  if (lcd->charset_loaded) {
    int base = (code & 0xFF) * 8;
    const uint8_t *g = &lcd->charset_buf[base];

    if (width <= 6) {
      /* charset.bin: 8-byte glyph rows with 1-col margins */
      for (int i = 0; i < width; i++) {
        cols[i] = reverse_bits8(g[1 + i]);
      }
    } else {
      for (int i = 0; i < width; i++) {
        cols[i] = reverse_bits8(g[i]);
      }
    }
  } else {
    /* Fallback if charset is unavailable */
    for (int i = 0; i < width; i++) {
      cols[i] = 0;
    }
  }

  *out_width = width;
}

static void apply_lcdc_command(lcd_state_t *lcd, const uint8_t *cmd,
                               int cmd_len) {
  uint8_t mode = cmd[0];
  uint8_t cmd_id = mode & 0x0F;
  int chip = mode_to_chip(mode);
  lcd->active_chip = (uint8_t)chip;
  lcd_chip_state_t *st = &lcd->chip_state[chip];
  st->mode = cmd_id;
  st->attr = mode & 0xE0; /* Store decoration bits (Bit 5: Inverse, Bit 6: Underline) */

  if (cmd_id == LCDC_CMD_DISPLAY_ON_OFF) {
    set_display_on_state(lcd, (mode & 0x10) != 0);
    return;
  }

  if (cmd_id == LCDC_CMD_SET_ROW_TOP_AND_WIDTH) {
    int width_sel = (mode >> 4) & 0x03;
    static const uint8_t widths[] = {8, 7, 6, 5};
    lcd->char_width = widths[width_sel];
    return;
  }

  if (cmd_id == LCDC_CMD_CURSOR_FLASH) {
    return;
  }

  if (cmd_len >= 3) {
    uint8_t col = cmd[1];
    uint8_t row = cmd[2];
    if (row >= lcd->active_pages) row = lcd->active_pages - 1;
    int block_off = (col & 0x80) ? 48 : 0;
    int col7 = col & 0x7F;
    st->y = row;
    if (cmd_id == LCDC_CMD_DRAW_CHAR) {
      st->x = block_off + ((col7 / 16) * lcd->char_width);
    } else {
      st->x = block_off + (col7 / 2);
    }
  }
}

/* ======== Public API ======== */

void lcd_init(lcd_state_t *lcd) {
  memset(lcd, 0, sizeof(lcd_state_t));
  lcd->pixel_size = 1;
  lcd->scale = 1;
  lcd->scale_num = 1;
  lcd->scale_den = 1;
  lcd->char_width = 6;
  lcd->display_on = false;
  lcd->color_on = LCD_COLOR_ON;
  lcd->color_off = LCD_COLOR_OFF;
  lcd->color_lcd_off = LCD_COLOR_LCD_OFF;
  lcd->spi_baudrate = 26000000;
  lcd->active_pages = LCD_PAGES;
  lcd->fill_pages   = LCD_PAGES;
  lcd->dirty = true;
  for (int i = 0; i < LCD_PAGES_MAX; i++) {
    lcd->dirty_pages[i] = true;
  }
  lcd->x_mirror = false;
  lcd->draw_bitimage_reverse = false;
  lcd->active_chip = 0;
  lcd->selected_ce = 0x00;
  lcd->op_command = false;
  lcd->cmd_buf_len = 0;
  lcd->cmd_expected = 0;
  lcd->charset_loaded = false;
  lcd->debug = false;
  /* current_fg_rgb332: initial fg = black (LCD_COLOR_ON = 0x0000) → RGB332 0x00 */
  lcd->current_fg_rgb332 = 0x00;
  /* current_bg_rgb332: initial bg = LCD_COLOR_OFF = 0xB5E6 → RGB332 */
  {
    uint8_t r = (LCD_COLOR_OFF >> 13) & 0x07;
    uint8_t g = (LCD_COLOR_OFF >>  8) & 0x07;
    uint8_t b = (LCD_COLOR_OFF >>  3) & 0x03;
    lcd->current_bg_rgb332 = (uint8_t)((r << 5) | (g << 2) | b);
  }

  /* Initialize chip states */
  for (int i = 0; i < 2; i++) {
    lcd->chip_state[i].x = 0;
    lcd->chip_state[i].y = 0;
    lcd->chip_state[i].mode = LCDC_CMD_DRAW_BITIMAGE;
  }

  /* Initialize RGB332→RGB565 lookup table */
  for (int i = 0; i < 256; i++) {
    uint8_t r = (i >> 5) & 0x07;
    uint8_t g = (i >> 2) & 0x07;
    uint8_t b = i & 0x03;
    lcd->rgb332_to_565_table[i] =
        ((uint16_t)(r * 31 / 7) << 11) |
        ((uint16_t)(g * 63 / 7) <<  5) |
         (uint16_t)(b * 31 / 3);
  }
  /* color_vram: all pixels start as OFF → fill with initial bg color */
  memset(lcd->color_vram, lcd->current_bg_rgb332, LCD_COLOR_VRAM_SIZE);

  lcd->vdp_addr = 0;
  lcd->vdp_enabled = true;

  /* Clear VRAM */
  lcd_clear(lcd);
}

/* ── VDP enable/disable ───────────────────────────────────────────────────── */

void lcd_set_vdp_enable(lcd_state_t *lcd, bool enabled) {
  lcd->vdp_enabled = enabled;
  if (!enabled) {
    lcd->vdp_init_fill_done = false;
    lcd->vdp_any_write      = false;
    lcd->vdp_write_count    = 0;
    lcd->vdp_ff_run         = 0;
  }
  lcd->dirty = true;
  for (int i = 0; i < lcd->active_pages; i++) lcd->dirty_pages[i] = true;
}

/* Force vdp_init_fill_done so _pixel_color() starts reading color_vram
   immediately.  Needed by callers (e.g. vram_loader) that write color_vram
   directly via get_color_vram() rather than through lcd_vdp_write(), since
   that path never sets the flag on its own. */
void lcd_set_vdp_init_done(lcd_state_t *lcd, bool done) {
  lcd->vdp_init_fill_done = done;
  if (done) {
    lcd->dirty = true;
    for (int i = 0; i < lcd->active_pages; i++) lcd->dirty_pages[i] = true;
  }
}

bool     lcd_get_vdp_init_done(const lcd_state_t *lcd)  { return lcd->vdp_init_fill_done; }
bool     lcd_get_vdp_any_write(const lcd_state_t *lcd)  { return lcd->vdp_any_write; }
uint32_t lcd_get_vdp_write_count(const lcd_state_t *lcd){ return lcd->vdp_write_count; }

bool lcd_get_vdp_enable(const lcd_state_t *lcd) {
  return lcd->vdp_enabled;
}

/* Called by Python after reset to switch the renderer to VDP mode once the
   running program has completed its VDP initialisation (0xFF clear +
   real drawing for pages 4-7).  We sync vram → color_vram for pages 0-3
   using the configured reset colours, then enable VDP rendering. */
void lcd_vdp_sync_enable(lcd_state_t *lcd) {
  /* Derive sync colors from color_on/color_off (the configured reset colors),
     NOT from current_fg/bg_rgb332.  The program writes to VDP reg3/reg4 during
     its init sequence (often setting bg=0xFF white for the initial clear) while
     VDP is disabled, which corrupts current_fg/bg_rgb332 before we get here.
     color_on/color_off are only updated when VDP is enabled, so they retain
     the values set by reset_emulator() → lcd_set_colors() throughout the
     1000 ms boot window. */
  uint8_t fg = (uint8_t)(((lcd->color_on  >> 13) & 0x07) << 5 |
                           ((lcd->color_on  >>  8) & 0x07) << 2 |
                           ((lcd->color_on  >>  3) & 0x03));
  uint8_t bg = (uint8_t)(((lcd->color_off >> 13) & 0x07) << 5 |
                           ((lcd->color_off >>  8) & 0x07) << 2 |
                           ((lcd->color_off >>  3) & 0x03));
  for (int pg = 0; pg < (int)lcd->active_pages; pg++) {
    for (int col = 0; col < LCD_WIDTH; col++) {
      uint8_t vbyte = lcd->vram[pg * LCD_WIDTH + col];
      int row_base = pg * 8 * LCD_WIDTH + col;
      for (int bit = 0; bit < 8; bit++) {
        lcd->color_vram[row_base + bit * LCD_WIDTH] =
            (vbyte & (1 << bit)) ? fg : bg;
      }
    }
  }
  lcd->color_on  = lcd->rgb332_to_565_table[fg];
  lcd->color_off = lcd->rgb332_to_565_table[bg];
  /* Also sync current_fg/bg_rgb332 so that any mono-LCD write that arrives
     AFTER VDP is enabled (before the ROM sets the real VDP colors via reg3/4)
     stamps color_vram with the reset colors rather than the 0xFF-white that
     the ROM wrote to reg3/reg4 during its VDP clear phase (while VDP was
     disabled).  Without this, write_vram_pixel_byte() would paint every
     ON and OFF pixel white, causing the all-white screen on ST7796. */
  lcd->current_fg_rgb332 = fg;
  lcd->current_bg_rgb332 = bg;
  lcd->vdp_enabled = true;
  lcd->dirty = true;
  for (int i = 0; i < lcd->active_pages; i++) lcd->dirty_pages[i] = true;
}

uint8_t lcd_get_num_pages(const lcd_state_t *lcd) {
  return lcd->active_pages;
}

void lcd_set_num_pages(lcd_state_t *lcd, uint8_t pages) {
  if (pages != 4 && pages != 8) return;
  /* When shrinking (64→32), keep fill_pages at the old value so the next
     LCD-OFF fill covers the full previous area and clears the lower half. */
  lcd->fill_pages   = (lcd->active_pages > pages) ? lcd->active_pages : pages;
  lcd->active_pages = pages;
  lcd->dirty = true;
  for (int i = 0; i < LCD_PAGES_MAX; i++) lcd->dirty_pages[i] = true;
}

/* Inline helper used by both render paths */
static inline uint16_t _pixel_color(const lcd_state_t *lcd, int col, int sy) {
  /* Use VDP color_vram only after the ROM has finished its initial 0xFF clear
     and written at least one real color value (vdp_init_fill_done).  During
     the clear phase color_vram[addr]==0xFF maps to white (0xFFFF) which would
     cause a full-screen white flash on every reset or sleep-wakeup. */
  if (lcd->vdp_enabled && lcd->vdp_init_fill_done) {
    return lcd->rgb332_to_565_table[lcd->color_vram[sy * LCD_WIDTH + col]];
  }
  int page = sy >> 3;
  if (page >= (int)lcd->active_pages) return lcd->color_off;
  int bit  = sy & 7;
  bool on  = (lcd->vram[page * LCD_WIDTH + col] >> bit) & 1;
  return on ? lcd->color_on : lcd->color_off;
}

void lcd_clear(lcd_state_t *lcd) {
  memset(lcd->vram, 0, LCD_VRAM_SIZE);
  memset(lcd->color_vram, lcd->current_bg_rgb332, LCD_COLOR_VRAM_SIZE);
  lcd->dirty = true;
  for (int i = 0; i < lcd->active_pages; i++) {
    lcd->dirty_pages[i] = true;
  }
}

void lcd_ctrl(lcd_state_t *lcd, uint8_t data) {
  /* New LCD.s-like protocol */
  lcd->ctrl_reg = data;
  lcd->selected_ce = (data >> 1) & 0x03;
  lcd->op_command = (data & 0x01) != 0;
  lcd->cmd_buf_len = 0;
  lcd->cmd_expected = 0;
}

void lcd_write(lcd_state_t *lcd, uint8_t data) {
  /* Protocol data write mode */

  /* Command mode (OP=1) */
  if (lcd->op_command) {
    if (lcd->cmd_buf_len == 0) {
      lcd->cmd_expected = (uint8_t)command_length(data);
    }
    if (lcd->cmd_buf_len < 4) {
      lcd->cmd_buf[lcd->cmd_buf_len++] = data;
    }
    if (lcd->cmd_buf_len >= lcd->cmd_expected) {
      apply_lcdc_command(lcd, lcd->cmd_buf, lcd->cmd_expected);
      lcd->cmd_buf_len = 0;
      lcd->cmd_expected = 0;
    }
    return;
  }

  /* Data-RAM mode (OP=0) */
  int chip = lcd->active_chip;
  if (lcd->selected_ce & (1 << chip)) {
    lcd_chip_state_t *st = &lcd->chip_state[chip];
    int mode = st->mode;

    if (mode == LCDC_CMD_DRAW_CHAR) {
      /* Character draw mode */
      uint8_t cols[8];
      int glyph_width = 0;
      char_code_to_bitmap(lcd, data, cols, &glyph_width);
      for (int i = 0; i < glyph_width; i++) {
        uint8_t pix = (st->attr & 0x20) ? ~cols[i] : cols[i];
        if (st->attr & 0x40) pix |= 0x80;
        write_vram_pixel_byte(lcd, chip, st->x + i, st->y, pix);
      }
      advance_xy(lcd, st, true, false);
    } else {
      /* Bitimage or other data mode */
      uint8_t pixel_byte = data;
      if (mode == LCDC_CMD_DRAW_BITIMAGE) {
        pixel_byte = reverse_bits8(data);
      }
      write_vram_pixel_byte(lcd, chip, st->x, st->y, pixel_byte);
      advance_xy(
          lcd, st, false,
          (mode == LCDC_CMD_DRAW_BITIMAGE && lcd->draw_bitimage_reverse));
    }
  }
}

uint8_t lcd_read(lcd_state_t *lcd) {
  int chip = lcd->active_chip;
  if (lcd->selected_ce & (1 << chip)) {
    lcd_chip_state_t *st = &lcd->chip_state[chip];
    uint8_t raw = read_vram_pixel_byte(lcd, chip, st->x, st->y);
    advance_xy(
        lcd, st, false,
        (st->mode == LCDC_CMD_DRAW_BITIMAGE && lcd->draw_bitimage_reverse));
    /* LCD.s notes readback nibble swap */
    return ((raw & 0x0F) << 4) | ((raw >> 4) & 0x0F);
  }
  return 0xFF;
}

bool lcd_get_pixel(lcd_state_t *lcd, int x, int y) {
  if (x < 0 || x >= LCD_WIDTH || y < 0 || y >= (int)(lcd->active_pages * 8))
    return false;
  int page = y / 8;
  int bit = y % 8;
  int offset = page * LCD_WIDTH + x;
  return (lcd->vram[offset] & (1 << bit)) != 0;
}

void lcd_set_x_mirror(lcd_state_t *lcd, bool enabled) {
  lcd->x_mirror = enabled;
}

void lcd_set_draw_bitimage_reverse(lcd_state_t *lcd, bool enabled) {
  lcd->draw_bitimage_reverse = enabled;
}

void lcd_load_charset(lcd_state_t *lcd, const uint8_t *data, int len) {
  if (len > LCD_CHARSET_SIZE)
    len = LCD_CHARSET_SIZE;
  if (len >= LCD_CHARSET_SIZE) {
    memcpy(lcd->charset_buf, data, LCD_CHARSET_SIZE);
    lcd->charset_loaded = true;
  }
}

void lcd_set_bg_colors(lcd_state_t *lcd, uint16_t on_bg, uint16_t off_bg) {
  lcd->color_off = on_bg;
  lcd->color_lcd_off = off_bg;
  uint8_t r = (on_bg >> 13) & 0x07;
  uint8_t g = (on_bg >>  8) & 0x07;
  uint8_t b = (on_bg >>  3) & 0x03;
  lcd->current_bg_rgb332 = (uint8_t)((r << 5) | (g << 2) | b);
  /* Refresh color_vram: ON pixels keep current fg, OFF pixels get new bg */
  uint8_t fg = lcd->current_fg_rgb332;
  uint8_t bg = lcd->current_bg_rgb332;
  for (int page = 0; page < (int)lcd->active_pages; page++) {
    for (int col = 0; col < LCD_WIDTH; col++) {
      uint8_t vbyte = lcd->vram[page * LCD_WIDTH + col];
      for (int bit = 0; bit < 8; bit++) {
        lcd->color_vram[(page * 8 + bit) * LCD_WIDTH + col] =
            (vbyte & (1 << bit)) ? fg : bg;
      }
    }
  }
  mark_all_dirty(lcd);
}

void lcd_set_colors(lcd_state_t *lcd, uint16_t fg, uint16_t bg) {
  lcd->color_on  = fg;
  lcd->color_off = bg;
  /* Convert RGB565 → RGB332 for per-pixel stamping */
  lcd->current_fg_rgb332 = (uint8_t)(((fg >> 13) & 0x07) << 5 |
                                      ((fg >>  8) & 0x07) << 2 |
                                      ((fg >>  3) & 0x03));
  lcd->current_bg_rgb332 = (uint8_t)(((bg >> 13) & 0x07) << 5 |
                                      ((bg >>  8) & 0x07) << 2 |
                                      ((bg >>  3) & 0x03));
  /* No mark_all_dirty: only pages dirtied by subsequent LCD writes
     are re-rendered with the new colors. */
}

/* ======== VDP (Color Extension) Registers ======== */

void lcd_vdp_write(lcd_state_t *lcd, uint32_t reg, uint8_t data) {
  switch (reg) {
  case 0:
    lcd->vdp_addr = (uint16_t)((lcd->vdp_addr & 0x3F00u) | (data & 0xFFu));
    break;
  case 1:
    lcd->vdp_addr = (uint16_t)((lcd->vdp_addr & 0x00FFu) | ((data & 0x3Fu) << 8));
    break;
  case 2:
    /* Always write to color_vram regardless of vdp_enabled so that both the
       ROM's initial 0xFF clear and its subsequent real draws land in the buffer.
       Dirty marking is gated on vdp_init_fill_done (see below) to suppress the
       white flash caused by 0xFF → 0xFFFF (white in RGB565) during the clear. */
    lcd->vdp_any_write = true;
    lcd->vdp_write_count++;
    if (lcd->vdp_addr < LCD_COLOR_VRAM_SIZE) {
      /* Detect VDP clear cycle — two triggers:
         1. ROM starts writing 0xFF from address 0  (most common pattern)
         2. 192+ consecutive 0xFF writes at any address (one full row = clear in progress)
         Either trigger suppresses dirty marking until real color data arrives,
         preventing the 0xFF→0xFFFF white flash on reset and sleep-wakeup. */
      if (data == 0xFF) {
        if (lcd->vdp_addr == 0 || lcd->vdp_ff_run >= 191)
          lcd->vdp_init_fill_done = false;
        if (lcd->vdp_ff_run < 0xFFFFu)
          lcd->vdp_ff_run++;
      } else {
        lcd->vdp_ff_run = 0;
      }
      lcd->color_vram[lcd->vdp_addr] = data;
      if (!lcd->vdp_init_fill_done && data != 0xFF)
        lcd->vdp_init_fill_done = true;
      /* Only mark dirty after the 0xFF clear phase ends. */
      if (lcd->vdp_enabled && lcd->vdp_init_fill_done) {
        int page = (int)(lcd->vdp_addr / LCD_WIDTH) / 8;
        if (page < (int)lcd->active_pages) {
          lcd->dirty = true;
          lcd->dirty_pages[page] = true;
        }
      }
    }
    lcd->vdp_addr = (uint16_t)((lcd->vdp_addr + 1u) & 0x3FFFu);
    break;
  case 3:
    /* Track fg colour regardless of VDP state so the value is ready when
       VDP auto-enables.  Propagate to color_on (used by non-VDP renderer)
       only when VDP is active — otherwise a white fg would flash immediately. */
    lcd->current_fg_rgb332 = data;
    if (lcd->vdp_enabled)
      lcd->color_on = lcd->rgb332_to_565_table[data];
    break;
  case 4:
    /* Same for bg: track current_bg_rgb332 always (needed by
       write_vram_pixel_byte once VDP re-enables and by the staged sync in
       case 2 above), but keep color_off at the configured value while VDP
       is disabled so the non-VDP renderer never shows a white background. */
    lcd->current_bg_rgb332 = data;
    if (lcd->vdp_enabled)
      lcd->color_off = lcd->rgb332_to_565_table[data];
    break;
  default:
    break;
  }
}

uint8_t lcd_vdp_read(lcd_state_t *lcd, uint32_t reg) {
  switch (reg) {
  case 0: return (uint8_t)(lcd->vdp_addr & 0xFFu);
  case 1: return (uint8_t)((lcd->vdp_addr >> 8) & 0x3Fu);
  case 2: {
    uint8_t val = 0xFF;
    if (lcd->vdp_addr < LCD_COLOR_VRAM_SIZE)
      val = lcd->color_vram[lcd->vdp_addr];
    lcd->vdp_addr = (uint16_t)((lcd->vdp_addr + 1u) & 0x3FFFu);
    return val;
  }
  case 3: return lcd->current_fg_rgb332;
  case 4: return lcd->current_bg_rgb332;
  default: return 0xFF;
  }
}

/* ======== SPI Display Rendering ======== */

#ifdef __arm__
/* RP2350 hardware SPI rendering via pico-sdk */
#include "hardware/gpio.h"
#include "hardware/spi.h"
#include "hardware/dma.h"

static int lcd_dma_chan = -1;
/* One page (8 source rows) at max scale 2.5 on ST7796 (480px wide): 20 rows x 480 x 2 */
static uint8_t dma_buffer[480 * 20 * 2];

void lcd_wait_for_idle(lcd_state_t *lcd) {
  if (lcd_dma_chan >= 0) {
    dma_channel_wait_for_finish_blocking(lcd_dma_chan);
  }
#ifdef __arm__
  if (lcd->spi_inst) {
      spi_inst_t *spi = (spi_inst_t *)lcd->spi_inst;
      /* dma_channel_wait_for_finish_blocking() returns when DMA has deposited all
         bytes into the SPI TX FIFO, but the SPI may still be clocking out the last
         1-4 bytes.  Wait for BSY to clear (TX FIFO empty AND shift register idle)
         so that CS is never deasserted while the SPI is still transmitting. */
      while (spi_get_hw(spi)->sr & SPI_SSPSR_BSY_BITS) {
          tight_loop_contents();
      }
      /* Drain RX FIFO (fills with MISO line noise during TX-only DMA). */
      while (spi_is_readable(spi)) {
          (void)spi_get_hw(spi)->dr;
      }
  }
  gpio_put(lcd->pin_cs, 1);
#endif
}

void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset, uint32_t spi_baudrate) {
  lcd->spi_inst = spi_inst;
  lcd->pin_cs = pin_cs;
  lcd->pin_dc = pin_dc;
  if (scale > 0 && scale != lcd->scale) {
    lcd->scale = scale;
    lcd->scale_num = scale;
    lcd->scale_den = 1;
  }
  lcd->disp_x_offset = x_offset;
  lcd->disp_y_offset = y_offset;
  if (spi_baudrate > 0)
    lcd->spi_baudrate = spi_baudrate;
  lcd->spi_initialized = true;
}

void lcd_set_scale_ratio(lcd_state_t *lcd, uint8_t num, uint8_t den) {
  if (num == 0)
    num = 1;
  if (den == 0)
    den = 1;
  lcd->scale_num = num;
  lcd->scale_den = den;
  /* Sync integer scale for fallbacks/logic that checks scale directly */
  if (den == 1) {
    lcd->scale = num;
  } else {
    /* For fractional scale, we keep the integer 'scale' as 1 or the closest
     * lower int. */
    lcd->scale = num / den;
    if (lcd->scale == 0)
      lcd->scale = 1;
  }
  lcd->dirty = true;
  for (int i = 0; i < lcd->active_pages; i++) {
    lcd->dirty_pages[i] = true;
  }
}

/* Send a single command byte to the SPI display */
static void disp_cmd(lcd_state_t *lcd, uint8_t cmd) {
  gpio_put(lcd->pin_dc, 0); /* command mode */
  gpio_put(lcd->pin_cs, 0);
  spi_write_blocking((spi_inst_t *)lcd->spi_inst, &cmd, 1);
  gpio_put(lcd->pin_cs, 1);
}

/* Send data bytes to the SPI display */
static void disp_data(lcd_state_t *lcd, const uint8_t *data, size_t len) {
  gpio_put(lcd->pin_dc, 1); /* data mode */
  gpio_put(lcd->pin_cs, 0);
  spi_write_blocking((spi_inst_t *)lcd->spi_inst, data, len);
  gpio_put(lcd->pin_cs, 1);
}

/* Set SPI display address window (column + page range) then begin RAM write */
static void disp_set_window(lcd_state_t *lcd, uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1) {
  uint8_t buf[4];
  disp_cmd(lcd, LCD_CMD_CASET);
  buf[0] = x0 >> 8;
  buf[1] = x0 & 0xFF;
  buf[2] = x1 >> 8;
  buf[3] = x1 & 0xFF;
  disp_data(lcd, buf, 4);

  disp_cmd(lcd, LCD_CMD_PASET);
  buf[0] = y0 >> 8;
  buf[1] = y0 & 0xFF;
  buf[2] = y1 >> 8;
  buf[3] = y1 & 0xFF;
  disp_data(lcd, buf, 4);

  disp_cmd(lcd, LCD_CMD_RAMWR);
}

void lcd_render_to_display(lcd_state_t *lcd) {
  if (!lcd->spi_initialized || !lcd->dirty)
    return;

  /* SPI Bus Arbitration: Ensure SD Card (GP15) and Touch (GP16) are deselected */
#ifdef __arm__
  gpio_put(15, 1);
  gpio_put(16, 1);
  /* Explicitly re-enforce SPI settings in case they were changed by Python */
  spi_set_baudrate((spi_inst_t *)lcd->spi_inst, lcd->spi_baudrate);
  spi_set_format((spi_inst_t *)lcd->spi_inst, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
#endif

  /* Safety guard: restore LCD-off color if corrupted */
  if (lcd->color_lcd_off == 0xFFFF || lcd->color_lcd_off == 0x0000) {
    lcd->color_lcd_off = 0x8410; /* Gray */
  }

  /* Find range of dirty pages to minimize disp_set_window calls.
     Scan the full LCD_PAGES_MAX range (not just active_pages): after a
     64→32 page-count shrink, pages 4-7 are marked dirty by
     lcd_set_num_pages() so the vacated lower half gets physically
     repainted (to background, via _pixel_color()'s active_pages check)
     instead of retaining stale on-screen content. */
  int first_page = -1, last_page = -1;
  for (int i = 0; i < LCD_PAGES_MAX; i++) {
    if (lcd->dirty_pages[i]) {
      if (first_page == -1)
        first_page = i;
      last_page = i;
    }
  }

  if (first_page == -1) {
    lcd->dirty = false;
    return;
  }

  uint8_t s = lcd->scale;
  uint8_t scale_num = lcd->scale_num ? lcd->scale_num : 1;
  uint8_t scale_den = lcd->scale_den ? lcd->scale_den : 1;
  uint16_t xo = lcd->disp_x_offset;
  uint16_t yo = lcd->disp_y_offset;
  bool frac_scale = (scale_den != 1);
  uint16_t fill_h     = (uint16_t)(lcd->fill_pages * 8); /* may exceed active_pages*8 after 64→32 switch */
  uint16_t out_w      = frac_scale ? (uint16_t)((LCD_WIDTH * scale_num) / scale_den)
                                   : (uint16_t)(LCD_WIDTH * s);
  uint16_t fill_out_h = frac_scale ? (uint16_t)((fill_h * scale_num) / scale_den)
                                   : (uint16_t)(fill_h * s);

  /* Pre-split LCD-off color for 8-bit Big-Endian DMA */
  uint8_t lcd_off_h = (uint8_t)(lcd->color_lcd_off >> 8);
  uint8_t lcd_off_l = (uint8_t)(lcd->color_lcd_off & 0xFF);

  /* Wait for previous DMA before starting new one or touching SPI */
  lcd_wait_for_idle(lcd);

  /* LCD Power OFF: fill whole area and exit.
     Use fill_out_h instead of out_h to cover the lower half when transitioning
     from 64-dot to 32-dot mode (fill_pages retains the old page count until
     the first LCD-OFF fill completes). */
  if (!lcd->display_on) {
    disp_set_window(lcd, xo, yo, xo + out_w - 1, yo + fill_out_h - 1);
    gpio_put(lcd->pin_dc, 1);
    gpio_put(lcd->pin_cs, 0);
    uint32_t total_pixels = (uint32_t)out_w * fill_out_h;
    uint32_t buf_pixels   = sizeof(dma_buffer) / 2;
    uint32_t prefill      = (total_pixels < buf_pixels) ? total_pixels : buf_pixels;
    for (uint32_t i = 0; i < prefill; i++) {
      dma_buffer[i*2]   = lcd_off_h;
      dma_buffer[i*2+1] = lcd_off_l;
    }
    if (lcd_dma_chan < 0) lcd_dma_chan = dma_claim_unused_channel(true);
    uint32_t sent = 0;
    while (sent < total_pixels) {
      uint32_t chunk = total_pixels - sent;
      if (chunk > buf_pixels) chunk = buf_pixels;
      /* Wait for the PREVIOUS DMA without deasseting CS — the display must remain
         selected (CS=LOW) for the entire RAMWR pixel stream.  Do NOT call
         lcd_wait_for_idle() here because that function always deasserts CS. */
      dma_channel_wait_for_finish_blocking(lcd_dma_chan);
      dma_channel_config cd = dma_channel_get_default_config(lcd_dma_chan);
      channel_config_set_transfer_data_size(&cd, DMA_SIZE_8);
      channel_config_set_dreq(&cd, spi_get_dreq((spi_inst_t *)lcd->spi_inst, true));
      dma_channel_configure(lcd_dma_chan, &cd, &spi_get_hw((spi_inst_t *)lcd->spi_inst)->dr,
                            dma_buffer, chunk * 2, true);
      sent += chunk;
    }
    lcd_wait_for_idle(lcd); /* Final wait: drains SPI TX FIFO and deasserts CS */
    lcd->fill_pages = lcd->active_pages; /* reset: next fill uses current page count */
    goto clear_dirty;
  }

  /* Render each dirty page separately — keeps dma_buffer small (one page at a time) */
  uint32_t step_fp = frac_scale
    ? (uint32_t)(((uint32_t)scale_den << 16) / scale_num)
    : 0;
  for (int page = first_page; page <= last_page; page++) {
    if (!lcd->dirty_pages[page]) continue;

    uint16_t pg_dy_start, pg_dy_end;
    if (frac_scale) {
      pg_dy_start = (uint16_t)((page * 8 * scale_num) / scale_den);
      pg_dy_end   = (uint16_t)(((page + 1) * 8 * scale_num) / scale_den) - 1;
    } else {
      pg_dy_start = (uint16_t)(page * 8 * s);
      pg_dy_end   = (uint16_t)((page + 1) * 8 * s - 1);
    }

    lcd_wait_for_idle(lcd);
    disp_set_window(lcd, xo, yo + pg_dy_start, xo + out_w - 1, yo + pg_dy_end);
    gpio_put(lcd->pin_dc, 1);
    gpio_put(lcd->pin_cs, 0);

    uint32_t buf_idx = 0;
    if (frac_scale) {
      for (int dy = pg_dy_start; dy <= (int)pg_dy_end; dy++) {
        uint16_t sy = (uint16_t)((dy * scale_den) / scale_num);
        uint32_t sx_fp = 0;
        for (int dx = 0; dx < out_w; dx++) {
          uint16_t sx = (uint16_t)(sx_fp >> 16);
          if (sx >= LCD_WIDTH) sx = LCD_WIDTH - 1;
          uint16_t c = _pixel_color(lcd, (int)sx, (int)sy);
          dma_buffer[buf_idx++] = (uint8_t)(c >> 8);
          dma_buffer[buf_idx++] = (uint8_t)(c & 0xFF);
          sx_fp += step_fp;
        }
      }
    } else {
      for (int sy = page * 8; sy <= page * 8 + 7; sy++) {
        for (int col = 0; col < LCD_WIDTH; col++) {
          uint16_t c = _pixel_color(lcd, col, sy);
          uint8_t h = (uint8_t)(c >> 8);
          uint8_t l = (uint8_t)(c & 0xFF);
          for (int v = 0; v < s; v++) {
            dma_buffer[buf_idx++] = h;
            dma_buffer[buf_idx++] = l;
          }
        }
        if (s > 1) {
          uint8_t *line_start = &dma_buffer[buf_idx - out_w * 2];
          for (int v = 1; v < s; v++) {
            memcpy(&dma_buffer[buf_idx], line_start, out_w * 2);
            buf_idx += out_w * 2;
          }
        }
      }
    }

    if (lcd_dma_chan < 0) lcd_dma_chan = dma_claim_unused_channel(true);
    dma_channel_config c = dma_channel_get_default_config(lcd_dma_chan);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_8);
    channel_config_set_dreq(&c, spi_get_dreq((spi_inst_t *)lcd->spi_inst, true));
    dma_channel_configure(lcd_dma_chan, &c, &spi_get_hw((spi_inst_t *)lcd->spi_inst)->dr,
                         dma_buffer, buf_idx, true);
  }

clear_dirty:
  lcd_wait_for_idle(lcd);
  lcd->dirty = false;
  for (int i = 0; i < LCD_PAGES_MAX; i++) {
    lcd->dirty_pages[i] = false;
  }
}
#else
/* Stub implementations for non-ARM builds */
void lcd_wait_for_idle(lcd_state_t *lcd) { (void)lcd; }
void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset, uint32_t spi_baudrate) {
  (void)lcd; (void)spi_inst; (void)pin_cs; (void)pin_dc; (void)scale; (void)x_offset; (void)y_offset; (void)spi_baudrate;
}
void lcd_set_scale_ratio(lcd_state_t *lcd, uint8_t num, uint8_t den) {
  (void)lcd; (void)num; (void)den;
}
void lcd_render_to_display(lcd_state_t *lcd) { (void)lcd; }
#endif
