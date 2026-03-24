/*
 * PB-1000 LCD Controller Emulation - C Implementation
 * C port of lcd_controller.py for MicroPython integration.
 * Handles VRAM management and LCD command processing.
 */
#include "lcd_controller.h"
#include <stdio.h>

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
  for (int i = 0; i < LCD_PAGES; i++) {
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
  if (y_page < 0 || y_page >= 4)
    return;
  if (x_local < 0 || x_local >= 96)
    return;
  if (lcd->x_mirror) {
    x_local = 95 - x_local;
  }
  int x = x_local + (chip ? 96 : 0);
  int off = y_page * LCD_WIDTH + x;
  if (off >= 0 && off < LCD_VRAM_SIZE) {
    lcd->vram[off] = data;
    lcd->dirty = true;
    lcd->dirty_pages[y_page] = true;
  }
}

static uint8_t read_vram_pixel_byte(lcd_state_t *lcd, int chip, int x_local,
                                    int y_page) {
  if (y_page < 0 || y_page >= 4)
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
      st->y = (st->y + 1) & 0x03;
    }
  } else {
    st->x += step;
    if (st->x >= 96) {
      st->x = 0;
      st->y = (st->y + 1) & 0x03;
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
    uint8_t row = cmd[2] & 0x03;
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
  lcd->dirty = true;
  for (int i = 0; i < LCD_PAGES; i++) {
    lcd->dirty_pages[i] = true;
  }
  lcd->x_mirror = false;
  lcd->draw_bitimage_reverse = false;
  lcd->active_chip = 0;
  lcd->selected_ce = 0x00;
  lcd->op_command = false;
  lcd->legacy_mode = false;
  lcd->cmd_buf_len = 0;
  lcd->cmd_expected = 0;
  lcd->charset_loaded = false;
  lcd->debug = false;

  /* Initialize chip states */
  for (int i = 0; i < 2; i++) {
    lcd->chip_state[i].x = 0;
    lcd->chip_state[i].y = 0;
    lcd->chip_state[i].mode = LCDC_CMD_DRAW_BITIMAGE;
  }

  /* Clear VRAM */
  lcd_clear(lcd);
}

void lcd_clear(lcd_state_t *lcd) {
  memset(lcd->vram, 0, LCD_VRAM_SIZE);
  lcd->page = 0;
  lcd->column = 0;
  lcd->dirty = true;
  for (int i = 0; i < LCD_PAGES; i++) {
    lcd->dirty_pages[i] = true;
  }
}

void lcd_ctrl(lcd_state_t *lcd, uint8_t data) {
  /* Legacy direct-control commands */
  if (data == LCD_CMD_DISPLAY_ON) {
    set_display_on_state(lcd, true);
    lcd->legacy_mode = true;
    return;
  }
  if (data == LCD_CMD_DISPLAY_OFF) {
    set_display_on_state(lcd, false);
    lcd->legacy_mode = true;
    return;
  }
  if ((data & 0xF8) == LCD_CMD_SET_PAGE) {
    lcd->page = data & 0x03;
    lcd->legacy_mode = true;
    return;
  }
  if (data < 0x40) {
    lcd->column = data % LCD_WIDTH;
    lcd->legacy_mode = true;
    return;
  }

  /* New LCD.s-like protocol */
  lcd->ctrl_reg = data;
  lcd->selected_ce = (data >> 1) & 0x03;
  lcd->op_command = (data & 0x01) != 0;
  lcd->cmd_buf_len = 0;
  lcd->cmd_expected = 0;
  lcd->legacy_mode = false;
}

void lcd_write(lcd_state_t *lcd, uint8_t data) {
  /* Legacy mode */
  if (lcd->legacy_mode) {
    if (lcd->page < 4 && lcd->column < LCD_WIDTH) {
      int offset = lcd->page * LCD_WIDTH + lcd->column;
      if (offset < LCD_VRAM_SIZE) {
        lcd->vram[offset] = data;
        lcd->dirty = true;
        lcd->dirty_pages[lcd->page] = true;
      }
    }
    lcd->column = (lcd->column + 1) % LCD_WIDTH;
    return;
  }

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
  bool active = false;
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
      active = true;
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
      active = true;
    }
  }

  if (active)
    return;

  /* Legacy fallback */
  if (lcd->page < 4 && lcd->column < LCD_WIDTH) {
    int offset = lcd->page * LCD_WIDTH + lcd->column;
    if (offset < LCD_VRAM_SIZE) {
      lcd->vram[offset] = data;
      lcd->dirty = true;
      lcd->dirty_pages[lcd->page] = true;
    }
  }
  lcd->column = (lcd->column + 1) % LCD_WIDTH;
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

  /* Legacy fallback */
  if (lcd->page < 4 && lcd->column < LCD_WIDTH) {
    int offset = lcd->page * LCD_WIDTH + lcd->column;
    if (offset < LCD_VRAM_SIZE) {
      uint8_t val = lcd->vram[offset];
      lcd->column = (lcd->column + 1) % LCD_WIDTH;
      return val;
    }
  }
  return 0xFF;
}

bool lcd_get_pixel(lcd_state_t *lcd, int x, int y) {
  if (x < 0 || x >= LCD_WIDTH || y < 0 || y >= LCD_HEIGHT)
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
  mark_all_dirty(lcd);
}

/* ======== SPI Display Rendering ======== */

#ifdef __arm__
/* RP2350 hardware SPI rendering via pico-sdk */
#include "hardware/gpio.h"
#include "hardware/spi.h"

/* Byte-swap a 16-bit value for big-endian SPI transfer */
static inline uint16_t bswap16(uint16_t v) { return (v >> 8) | (v << 8); }

void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset) {
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
  for (int i = 0; i < LCD_PAGES; i++) {
    lcd->dirty_pages[i] = true;
  }
}

/* Send a single command byte to ILI9341 */
static void ili_cmd(lcd_state_t *lcd, uint8_t cmd) {
  gpio_put(lcd->pin_dc, 0); /* command mode */
  gpio_put(lcd->pin_cs, 0);
  spi_write_blocking((spi_inst_t *)lcd->spi_inst, &cmd, 1);
  gpio_put(lcd->pin_cs, 1);
}

/* Send data bytes to ILI9341 */
static void ili_data(lcd_state_t *lcd, const uint8_t *data, size_t len) {
  gpio_put(lcd->pin_dc, 1); /* data mode */
  gpio_put(lcd->pin_cs, 0);
  spi_write_blocking((spi_inst_t *)lcd->spi_inst, data, len);
  gpio_put(lcd->pin_cs, 1);
}

/* Set ILI9341 address window */
static void ili_set_window(lcd_state_t *lcd, uint16_t x0, uint16_t y0,
                           uint16_t x1, uint16_t y1) {
  uint8_t buf[4];
  ili_cmd(lcd, ILI9341_CASET);
  buf[0] = x0 >> 8;
  buf[1] = x0 & 0xFF;
  buf[2] = x1 >> 8;
  buf[3] = x1 & 0xFF;
  ili_data(lcd, buf, 4);

  ili_cmd(lcd, ILI9341_PASET);
  buf[0] = y0 >> 8;
  buf[1] = y0 & 0xFF;
  buf[2] = y1 >> 8;
  buf[3] = y1 & 0xFF;
  ili_data(lcd, buf, 4);

  ili_cmd(lcd, ILI9341_RAMWR);
}

void lcd_render_to_display(lcd_state_t *lcd) {
  if (!lcd->spi_initialized || !lcd->dirty)
    return;

  uint8_t s = lcd->scale;
  uint8_t scale_num = lcd->scale_num ? lcd->scale_num : 1;
  uint8_t scale_den = lcd->scale_den ? lcd->scale_den : 1;
  uint16_t xo = lcd->disp_x_offset;
  uint16_t yo = lcd->disp_y_offset;
  bool frac_scale = (scale_den != 1);
  uint16_t out_w =
      frac_scale ? (uint16_t)((LCD_WIDTH * scale_num) / scale_den)
                 : (uint16_t)(LCD_WIDTH * s);
  uint16_t out_h =
      frac_scale ? (uint16_t)((LCD_HEIGHT * scale_num) / scale_den)
                 : (uint16_t)(LCD_HEIGHT * s);
  uint16_t pw = out_w;

  /* Pre-compute big-endian color values */
  uint16_t be_on = bswap16(lcd->color_on);
  uint16_t be_off = bswap16(lcd->color_off);
  uint16_t be_lcd_off = bswap16(lcd->color_lcd_off);

  uint16_t row_buf[LCD_WIDTH * 4]; /* max integer scale=4 */
  if (s > 4)
    s = 4;

  /* LCD OFF: render full panel background and skip VRAM pixels. */
  if (!lcd->display_on) {
    int idx = 0;
    for (int col = 0; col < out_w; col++) {
      row_buf[idx++] = be_lcd_off;
    }
    size_t row_bytes = (size_t)idx * 2;
    ili_set_window(lcd, xo, yo, xo + pw - 1, yo + out_h - 1);
    gpio_put(lcd->pin_dc, 1);
    gpio_put(lcd->pin_cs, 0);
    for (int y = 0; y < out_h; y++) {
      spi_write_blocking((spi_inst_t *)lcd->spi_inst, (const uint8_t *)row_buf,
                         row_bytes);
    }
    gpio_put(lcd->pin_cs, 1);
    lcd->dirty = false;
    for (int i = 0; i < LCD_PAGES; i++) {
      lcd->dirty_pages[i] = false;
    }
    return;
  }

  if (frac_scale) {
    /* Fixed-point scale for coordinates (16.16) */
    uint32_t step_fp = (uint32_t)(((uint32_t)scale_den << 16) / scale_num);

    for (int page = 0; page < LCD_PAGES; page++) {
      if (!lcd->dirty_pages[page])
        continue;

      /* Calculate target Y window for this source page */
      uint16_t dy_start = (uint16_t)((page * 8 * scale_num) / scale_den);
      uint16_t dy_end = (uint16_t)(((page + 1) * 8 * scale_num) / scale_den) - 1;

      ili_set_window(lcd, xo, yo + dy_start, xo + out_w - 1, yo + dy_end);
      gpio_put(lcd->pin_dc, 1);
      gpio_put(lcd->pin_cs, 0);

      for (int dy = dy_start; dy <= dy_end; dy++) {
        uint16_t sy = (uint16_t)((dy * scale_den) / scale_num);
        uint8_t bit = (uint8_t)(sy & 0x07);
        int base = (sy >> 3) * LCD_WIDTH;

        uint32_t sx_fp = 0;
        for (int dx = 0; dx < out_w; dx++) {
          uint16_t sx = (uint16_t)(sx_fp >> 16);
          if (sx >= LCD_WIDTH) sx = LCD_WIDTH - 1;
          uint8_t vbyte = lcd->vram[base + sx];
          row_buf[dx] = (vbyte & (1 << bit)) ? be_on : be_off;
          sx_fp += step_fp;
        }
        spi_write_blocking((spi_inst_t *)lcd->spi_inst, (const uint8_t *)row_buf,
                           (size_t)out_w * 2);
      }
      gpio_put(lcd->pin_cs, 1);
      lcd->dirty_pages[page] = false;
    }
    lcd->dirty = false;
    return;
  }

  for (int page = 0; page < LCD_PAGES; page++) {
    if (!lcd->dirty_pages[page])
      continue;

    /* Set window for this page (8 rows) */
    uint16_t page_y = yo + (page * 8 * s);
    ili_set_window(lcd, xo, page_y, xo + pw - 1, page_y + (8 * s) - 1);

    gpio_put(lcd->pin_dc, 1); /* data mode */
    gpio_put(lcd->pin_cs, 0);

    for (int bit = 0; bit < 8; bit++) {
      /* Build one row of scaled pixels */
      int idx = 0;
      for (int col = 0; col < LCD_WIDTH; col++) {
        uint8_t vbyte = lcd->vram[page * LCD_WIDTH + col];
        uint16_t color = (vbyte & (1 << bit)) ? be_on : be_off;
        for (int sx = 0; sx < s; sx++) {
          row_buf[idx++] = color;
        }
      }
      /* Send the row `scale` times for vertical scaling */
      size_t row_bytes = (size_t)idx * 2;
      for (int sy = 0; sy < s; sy++) {
        spi_write_blocking((spi_inst_t *)lcd->spi_inst,
                           (const uint8_t *)row_buf, row_bytes);
      }
    }
    gpio_put(lcd->pin_cs, 1);
    lcd->dirty_pages[page] = false;
  }

  lcd->dirty = false;
}

#else
/* Stub implementations for non-ARM builds (CPython testing, etc.) */
void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset) {
  (void)lcd;
  (void)spi_inst;
  (void)pin_cs;
  (void)pin_dc;
  (void)scale;
  (void)x_offset;
  (void)y_offset;
}
void lcd_set_scale_ratio(lcd_state_t *lcd, uint8_t num, uint8_t den) {
  (void)lcd;
  (void)num;
  (void)den;
}
void lcd_render_to_display(lcd_state_t *lcd) { (void)lcd; }
#endif
