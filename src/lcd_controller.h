/*
 * PB-1000 LCD Controller Emulation - Header
 * C port of lcd_controller.py for MicroPython integration.
 * Handles VRAM management and LCD command processing.
 */
#ifndef LCD_CONTROLLER_H
#define LCD_CONTROLLER_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Display dimensions */
#define LCD_WIDTH 192
#define LCD_HEIGHT 32

/* ILI9341 display colors (RGB565) */
#define LCD_COLOR_ON 0x0000  /* Black pixel */
#define LCD_COLOR_OFF 0xB5E6 /* Olive-green background */
#define LCD_COLOR_LCD_OFF 0x8410 /* Gray tint when LCD is powered off */

/* ILI9341 commands */
#define ILI9341_CASET 0x2A
#define ILI9341_PASET 0x2B
#define ILI9341_RAMWR 0x2C
#define LCD_PAGES 4
#define LCD_VRAM_SIZE (LCD_WIDTH * LCD_PAGES) /* 768 bytes */
#define LCD_CHARSET_SIZE 2048

/* Legacy direct-control commands */
#define LCD_CMD_DISPLAY_ON 0x39
#define LCD_CMD_DISPLAY_OFF 0x38
#define LCD_CMD_SET_PAGE 0xB8
#define LCD_CMD_SET_COL 0x00
#define LCD_CMD_SET_START 0xC0

/* LCD.s command IDs (mode low nibble) */
#define LCDC_CMD_READ 0x01
#define LCDC_CMD_DRAW_BITIMAGE 0x02
#define LCDC_CMD_DRAW_CHAR 0x03
#define LCDC_CMD_DISPLAY_ON_OFF 0x04
#define LCDC_CMD_SPRITE_DEF_GRAPHIC 0x06
#define LCDC_CMD_SPRITE_DEF_CHAR 0x07
#define LCDC_CMD_SET_ROW_TOP_AND_WIDTH 0x08
#define LCDC_CMD_SPRITE_ON_OFF 0x09
#define LCDC_CMD_USER_CHAR_DEF 0x0B
#define LCDC_CMD_CONTRAST 0x0C
#define LCDC_CMD_CURSOR_FLASH 0x0D
#define LCDC_CMD_SET_SPRITE_POS 0x0E

/* Per-chip state */
typedef struct {
  int x;
  int y;
  int mode;
  uint8_t attr;
} lcd_chip_state_t;

/* LCD controller state */
typedef struct {
  /* VRAM: 192 columns x 4 pages = 768 bytes */
  uint8_t vram[LCD_VRAM_SIZE];

  /* Current addressing */
  uint8_t page;   /* 0-3 */
  uint8_t column; /* 0-191 */
  uint8_t start_line;

  /* Display state */
  bool display_on;
  bool dirty;
  bool dirty_pages[LCD_PAGES];
  /* Configurable RGB565 colors */
  uint16_t color_on;
  uint16_t color_off;
  uint16_t color_lcd_off;

  /* Control register */
  uint8_t ctrl_reg;
  int pixel_size;

  /* Configuration toggles */
  bool x_mirror;
  bool draw_bitimage_reverse;

  /* Chip / command state */
  uint8_t active_chip;
  uint8_t char_width;
  uint8_t selected_ce;
  bool op_command;
  bool legacy_mode;

  /* Command buffer */
  uint8_t cmd_buf[4];
  uint8_t cmd_buf_len;
  uint8_t cmd_expected;

  /* Per-chip state (2 chips) */
  lcd_chip_state_t chip_state[2];

  /* Character set data (2048 bytes) */
  uint8_t charset_buf[LCD_CHARSET_SIZE];
  bool charset_loaded;

  /* Debug flag */
  bool debug;

  /* SPI display hardware state (set by setup_display) */
  bool spi_initialized;
  void *spi_inst; /* hardware_spi_inst_t* (SPI1 on RP2350) */
  uint8_t pin_cs;
  uint8_t pin_dc;
  /* Pixel scale factor */
  uint8_t scale;
  uint8_t scale_num;
  uint8_t scale_den;
  /* Display offset */
  uint16_t disp_x_offset;
  uint16_t disp_y_offset;
} lcd_state_t;

/* API Functions */
void lcd_init(lcd_state_t *lcd);
void lcd_clear(lcd_state_t *lcd);
void lcd_ctrl(lcd_state_t *lcd, uint8_t data);
void lcd_write(lcd_state_t *lcd, uint8_t data);
uint8_t lcd_read(lcd_state_t *lcd);
bool lcd_get_pixel(lcd_state_t *lcd, int x, int y);
void lcd_set_x_mirror(lcd_state_t *lcd, bool enabled);
void lcd_set_draw_bitimage_reverse(lcd_state_t *lcd, bool enabled);
void lcd_load_charset(lcd_state_t *lcd, const uint8_t *data, int len);
void lcd_set_bg_colors(lcd_state_t *lcd, uint16_t on_bg, uint16_t off_bg);

/* SPI display rendering (direct hardware access) */
void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset);
void lcd_set_scale_ratio(lcd_state_t *lcd, uint8_t num, uint8_t den);
void lcd_render_to_display(lcd_state_t *lcd);

#endif /* LCD_CONTROLLER_H */
