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

/* LCD display colors (RGB565, common to ILI9341 / ST7796) */
#define LCD_COLOR_ON 0x0000  /* Black pixel */
#define LCD_COLOR_OFF 0xB5E6 /* Olive-green background */
#define LCD_COLOR_LCD_OFF 0x8410 /* Gray tint when LCD is powered off */

/* SPI display commands (standard across ILI9341, ST7796, etc.) */
#define LCD_CMD_CASET 0x2A
#define LCD_CMD_PASET 0x2B
#define LCD_CMD_RAMWR 0x2C
#define LCD_PAGES 4
#define LCD_PAGES_MAX 8                            /* max for 64-dot extended mode */
#define LCD_VRAM_SIZE (LCD_WIDTH * LCD_PAGES_MAX)  /* 1536 bytes (supports 4 or 8 pages) */
#define LCD_COLOR_VRAM_SIZE (LCD_WIDTH * 64)       /* 192 x 64 = 12,288 bytes */
#define LCD_CHARSET_SIZE 2048

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
  /* VRAM: 192 columns x 8 pages max = 1536 bytes; active_pages selects 4 or 8 */
  uint8_t vram[LCD_VRAM_SIZE];

  uint8_t start_line;

  /* Display state */
  bool display_on;
  bool dirty;
  bool dirty_pages[LCD_PAGES_MAX];
  uint8_t active_pages; /* 4 = 32-dot mode, 8 = 64-dot extended mode */
  uint8_t fill_pages;   /* LCD-OFF fill height; retains old value when pages decrease so the
                           full previous area is grayed out at least once after a mode switch */
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

  /* Command buffer */
  uint8_t cmd_buf[4];
  uint8_t cmd_buf_len;
  uint8_t cmd_expected;

  /* Per-chip state (2 chips) */
  lcd_chip_state_t chip_state[2];

  /* Character set data (2048 bytes) */
  uint8_t charset_buf[LCD_CHARSET_SIZE];
  bool charset_loaded;

  /* Per-pixel color VRAM: each byte is RGB332.
     ON pixel  → stamped with current_fg_rgb332 at write time.
     OFF pixel → stamped with current_bg_rgb332 at write time.
     Rendering reads color_vram directly; global color_on/color_off are
     only used for initial values and LCD-off fill. */
  uint8_t color_vram[LCD_COLOR_VRAM_SIZE];
  uint16_t rgb332_to_565_table[256];

  /* Current fg/bg colors in RGB332, updated by lcd_set_colors / lcd_set_bg_colors */
  uint8_t current_fg_rgb332;
  uint8_t current_bg_rgb332;

  /* VDP (Color extension) address pointer — 14-bit, auto-increments on reg2 access */
  uint16_t vdp_addr;

  /* When true, rendering uses per-pixel color_vram (VDP mode).
     When false, rendering uses global color_on / color_off from the mono VRAM bits. */
  bool vdp_enabled;

  /* VDP write-activity tracking (all reset when lcd_set_vdp_enable(false) called).
     Python polls vdp_write_count every frame; when the count stops changing
     for >= 300 ms, the ROM has finished its VDP init and vdp_sync_enable()
     can safely be called.  No C-side time tracking needed — Python does it. */
  bool     vdp_init_fill_done; /* first non-0xFF reg2 write seen since last clear */
  bool     vdp_any_write;      /* any reg2 write since VDP disabled */
  uint32_t vdp_write_count;    /* monotone counter, incremented on each reg2 write */
  uint16_t vdp_ff_run;         /* consecutive 0xFF writes — resets vdp_init_fill_done at >= 192 */

  /* Debug flag */
  bool debug;

  /* SPI display hardware state (set by setup_display) */
  bool spi_initialized;
  void *spi_inst; /* hardware_spi_inst_t* (SPI1 on RP2350) */
  uint8_t pin_cs;
  uint8_t pin_dc;
  uint32_t spi_baudrate;
  /* Pixel scale factor */
  uint8_t scale;
  uint8_t scale_num;
  uint8_t scale_den;
  /* Display offset */
  uint16_t disp_x_offset;
  uint16_t disp_y_offset;

  /* Optional HDMI bridge output (second Pico 2 + PICO-HDMI-PLUS, shares
     the same spi_inst as the LCD via a dedicated CS pin). Disabled
     (hdmi_ready=false) unless lcd_init_hdmi_output() has been called. */
  uint8_t pin_hdmi_cs;
  uint32_t hdmi_baudrate;
  bool hdmi_ready;
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
void lcd_set_colors(lcd_state_t *lcd, uint16_t fg, uint16_t bg);
uint8_t lcd_reverse_bits8(uint8_t v);

/* VDP (Color extension) register access — reg is (offset - 0x0C20), 0-4 */
void    lcd_vdp_write(lcd_state_t *lcd, uint32_t reg, uint8_t data);
uint8_t lcd_vdp_read(lcd_state_t *lcd, uint32_t reg);

/* VDP enable/disable toggle */
void lcd_set_vdp_enable(lcd_state_t *lcd, bool enabled);
bool lcd_get_vdp_enable(const lcd_state_t *lcd);
void lcd_vdp_sync_enable(lcd_state_t *lcd);
void     lcd_set_vdp_init_done(lcd_state_t *lcd, bool done);
bool     lcd_get_vdp_init_done(const lcd_state_t *lcd);
bool     lcd_get_vdp_any_write(const lcd_state_t *lcd);
uint32_t lcd_get_vdp_write_count(const lcd_state_t *lcd);

/* Active page count: 4 = 32-dot (default), 8 = 64-dot extended */
uint8_t lcd_get_num_pages(const lcd_state_t *lcd);
void    lcd_set_num_pages(lcd_state_t *lcd, uint8_t pages);


/* SPI display rendering (direct hardware access) */
void lcd_setup_display(lcd_state_t *lcd, void *spi_inst, uint8_t pin_cs,
                       uint8_t pin_dc, uint8_t scale, uint16_t x_offset,
                       uint16_t y_offset, uint32_t spi_baudrate);
void lcd_set_scale_ratio(lcd_state_t *lcd, uint8_t num, uint8_t den);
void lcd_render_to_display(lcd_state_t *lcd);
void lcd_wait_for_idle(lcd_state_t *lcd);

/* Optional HDMI bridge output — see doc/hardware_guide.md §7 (GP28 external
 * SPI device). Reuses the existing spi_inst set up by lcd_setup_display();
 * must be called after that. No-op (skipped) until this has been called. */
void lcd_init_hdmi_output(lcd_state_t *lcd, uint8_t cs_pin, uint32_t baudrate);
void lcd_render_to_hdmi(lcd_state_t *lcd);

/* Send an arbitrary caller-supplied RGB332 buffer (1 byte/pixel, row-major,
 * width*height bytes) to the HDMI bridge, bypassing the emulated VRAM/
 * _pixel_color() path lcd_render_to_hdmi() uses. For non-emulated-screen
 * content generated on the MicroPython side (e.g. the EMULATOR MENU's
 * HDMIMirrorDisplay in mp/hdmi_menu_mirror.py) that has no representation
 * in lcd->vram/color_vram. No-op if lcd_init_hdmi_output() was never
 * called. */
void lcd_send_hdmi_frame(lcd_state_t *lcd, const uint8_t *buf, uint16_t width,
                          uint16_t height, uint8_t scale, uint8_t bpp);

/* Send a palette (RGB332 entries) as a PKT_PALETTE packet, for use with
 * lcd_send_hdmi_frame() at bpp < 8 (palette-indexed pixels). Must be sent
 * before the corresponding lcd_send_hdmi_frame() call whenever the palette
 * changes; the receiver keeps the last-received palette across frames. */
void lcd_send_hdmi_palette(lcd_state_t *lcd, const uint8_t *palette_rgb332,
                            uint8_t count);

/* Send a variable-length command stream (PKT_TEXT_CMDS — see
 * ../../hdmi_bridge_receiver/main.c's protocol comment) instead of raw pixels:
 * a compact sequence of "fill rect" and "draw text string" commands that
 * the receiver interprets using its own embedded 8x8 font, avoiding the
 * need for a pixel-sized buffer on this (RAM-constrained) side. Used by
 * mp/hdmi_menu_mirror.py to mirror the EMULATOR MENU, which is composed
 * entirely of text and solid-color rectangles. width/height describe the
 * logical canvas the command coordinates are relative to (for the
 * receiver's centering); payload_len is buf's length in bytes. */
void lcd_send_hdmi_text_cmds(lcd_state_t *lcd, const uint8_t *buf,
                              uint16_t payload_len, uint16_t width,
                              uint16_t height, uint8_t scale);

/* Same wire format as lcd_send_hdmi_text_cmds() (PKT_BEZEL_CMDS, 0x03,
 * instead of PKT_TEXT_CMDS, 0x02) — the receiver tracks its window-
 * centering max-size independently from the EMULATOR MENU mirror, so the
 * bezel can be sent in the same logical coordinate space as the game
 * screen (see pb1000.py's _draw_bezel_hdmi()) and align with it on
 * screen, unaffected by the menu's much larger canvas. */
void lcd_send_hdmi_bezel_cmds(lcd_state_t *lcd, const uint8_t *buf,
                               uint16_t payload_len, uint16_t width,
                               uint16_t height, uint8_t scale);

/* Sends PKT_CLEAR_SCREEN (see ../../hdmi_bridge_receiver/main.c's protocol
 * comment) — clears the whole HDMI screen and resets its window-centering
 * tracking, independent of any specific content kind. Call as early as
 * possible in boot (right after init_hdmi_output()) so a receiver left
 * powered on across a sender reboot doesn't keep showing the previous
 * session's stale content. */
void lcd_send_hdmi_clear_screen(lcd_state_t *lcd);

#endif /* LCD_CONTROLLER_H */
