#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include "mpconfigport.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "hd61700.h"
#include "lcd_controller.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "hardware/clocks.h"

/* Static CPU state */
static hd61700_state_t cpu_state;
static bool cpu_debug_enabled = false;
static bool cpu_key_debug_enabled = false;
static bool cpu_lcd_debug_enabled = false;

extern lcd_state_t *lcd_c_get_state(void);

/* C-side memory map buffers */
static uint8_t rom0_buf[0x2000]; // 8KB Internal ROM
static size_t rom0_size = 0;
static uint8_t rom1_buf[0x8000]; // 32KB System ROM
static size_t rom1_size = 0;
static uint8_t ram_buf[0x2000];     // 8KB RAM (0x6000-0x7FFF)
static uint8_t bank1_buf[0x8000]; // 32KB RAM Bank 1 (0x8000-0xFFFF)
static uint8_t bank2_buf[0x8000]; // 32KB RAM Bank 2 (0x8000-0xFFFF)
static uint8_t bank3_buf[0x8000]; // 32KB RAM Bank 3 (0x8000-0xFFFF)
static uint8_t ext_work_buf[0x100]; // 256B Extension API work area (0x5F00-0x5FFF)

/* LCD write log / read queue for unit-test intercept */
#define LCD_INTERCEPT_SIZE 16
static uint8_t lcd_write_log[LCD_INTERCEPT_SIZE];
static uint8_t lcd_write_log_cnt = 0;
static uint8_t lcd_read_queue[LCD_INTERCEPT_SIZE];
static uint8_t lcd_read_q_head = 0; /* producer */
static uint8_t lcd_read_q_tail = 0; /* consumer */
/* has_bank[0]=ROM1 always present; [1..3] set when load_ram(slot,data) called */
static bool has_bank[4] = {true, false, false, false};
static bool has_bank_forced = false; /* true = Python override via set_has_exp_ram */

/* MMIO DMA: Bank RAM → color_vram block transfer (0x0C30-0x0C37) */
static uint8_t  dma_src_bank = 0;
static uint16_t dma_src_addr = 0;
static uint16_t dma_dst_addr = 0;
static uint16_t dma_len      = 0;
static uint8_t  dma_status   = 0x00; /* bit0=error */

static void _dma_execute(void) {
    static uint8_t * const bufs[3] = {bank1_buf, bank2_buf, bank3_buf};
    if (dma_src_bank < 1 || dma_src_bank > 3 || !has_bank[dma_src_bank]
        || dma_len == 0
        || (uint32_t)dma_src_addr + dma_len > 0x8000u
        || (uint32_t)dma_dst_addr + dma_len > LCD_COLOR_VRAM_SIZE) {
        dma_status = 0x01;
        return;
    }
    lcd_state_t *lcd = lcd_c_get_state();
    memcpy(lcd->color_vram + dma_dst_addr,
           bufs[dma_src_bank - 1] + dma_src_addr, dma_len);
    dma_status = 0x00;
}

/* Dedup trace state for SSTOP/SBOT (0x6931-0x6934). */
static uint8_t sstop_sbot_last[4];
static bool sstop_sbot_last_valid[4];


/* LCD char output callback */
static mp_obj_t py_lcd_char_cb    = MP_OBJ_NULL;
static int8_t   c_lcd_char_last_y = -1;

/* Raw pixel character accumulator (handles DRAW_BITIMAGE and other modes) */
static uint8_t  _cdet_buf[6];
static uint8_t  _cdet_col   = 0;
static uint8_t  _cdet_chip  = 0;
static uint8_t  _cdet_page  = 0;
static uint8_t  _cdet_x0    = 0;
static int8_t   _cdet_lpage = -1;
static uint8_t  _cdet_mode  = 0;  /* mode at sequence start (for rb8 direction) */
/* Per-position shadow: last char code emitted at [page][global_col 0-31].
   0x00 = never written (no printable char has code 0x00). */
static uint8_t  _cdet_shadow[4][32];
/* Per-page text context flag: set when the first non-space char is detected on
   a page.  Used to gate space emission so that blank LCD areas (all-zero pixels
   match the space glyph) do not contaminate the shadow and suppress real spaces
   in subsequent text output. */
static bool     _cdet_row_has_text[4];

/* ====== C Keyboard Matrix ====== */
#define KB_ROWS 13
#define KB_COLS 12
static bool c_kb_matrix[KB_ROWS][KB_COLS]; /* [row][col_index] */
static uint8_t c_kb_ia_select = 0;
/* Physical state trackers for host modifiers */
static bool host_shift_physical = false;
static bool host_alt_physical   = false;
static bool host_ctrl_physical  = false;
static bool host_gui_physical   = false;

/* Track scancode->coords for correct release */
#define MAX_ACTIVE_USB_KEYS 8
typedef struct {
  uint8_t scancode;
  uint8_t n_coords;
  uint8_t coords[4][2]; /* Increase to 4 if needed for complex combinations */
} usb_active_key_t;
static usb_active_key_t c_kb_active_usb[MAX_ACTIVE_USB_KEYS];
static int c_kb_active_usb_count = 0;
/* Scancode-level debounce: track last release time to skip immediate re-presses */
static uint32_t last_release_ms[256];

/* KEY_INT pulse state */
static bool c_kb_key_line_state = false;
static bool c_kb_pulse_release_pending = false;
static uint32_t c_kb_next_pulse_ms = 0;
#define C_KB_PULSE_INTERVAL_MS 25
static uint32_t c_kb_pulse_interval_ms = C_KB_PULSE_INTERVAL_MS;
static int c_kb_post_release_pulses_remaining = 0;
static const int C_KB_POST_RELEASE_PULSES_MAX = 4;

/* Two-phase SFT combo: press SFT alone first, add target key after KEY_INT fires */
static bool c_kb_phase2_pending = false;
static uint8_t c_kb_phase2_scancode = 0;
static uint8_t c_kb_phase2_coords[3][2];
static int c_kb_phase2_n_coords = 0;

/* Deferred combo release: hold SFT+target in matrix until ROM has processed them */
static uint32_t c_kb_deferred_combo_release_ms = 0;
static uint8_t  c_kb_deferred_combo_coords[4][2];
static int      c_kb_deferred_combo_n_coords = 0;
#define C_KB_DEFERRED_COMBO_HOLD_MS 60u

/* Python callbacks (set from Python) */
static mp_obj_t py_f11_callback = MP_OBJ_NULL;
static mp_obj_t py_f9_callback = MP_OBJ_NULL;
static mp_obj_t py_io_read_callback = MP_OBJ_NULL;
static mp_obj_t py_io_write_callback = MP_OBJ_NULL;

/* CAL hook table (address → Python/native callable) */
#define CALL_HOOK_MAX 16
static uint16_t call_hook_addrs[CALL_HOOK_MAX];
static mp_obj_t  call_hook_fns[CALL_HOOK_MAX];  /* static = scanned by conservative GC */
static bool      call_hook_enabled[CALL_HOOK_MAX];
static int       call_hook_count = 0;
static bool c_call_hook_dispatcher(void *ctx, uint16_t addr); /* forward decl */

/* UART RX/TX FIFO (Internal) */
#define UART_RX_FIFO_SIZE 256
static uint8_t uart_rx_fifo[UART_RX_FIFO_SIZE];
static uint8_t uart_tx_fifo[256];
static uint8_t uart_rx_head = 0;
static uint8_t uart_rx_tail = 0;
static uint8_t uart_tx_head = 0;
static uint8_t uart_tx_tail = 0;

static uint8_t vfdd_data_reg = 0x55; // Default to MD-100 ID (Read)
static uint8_t vfdd_write_reg = 0x00; // Captured write data

/* C-side port direct control state (RP2350 GPIO / PWM) */
static uint8_t  c_port_data       = 0;      /* last value written to port by CPU */
static uint32_t c_port_read_count = 0;      /* boot-sequence ON key counter */
static int      c_tx_pin          = -1;     /* RS-232C TXD (-1 = not configured) */
static int      c_rx_pin          = -1;     /* RS-232C RXD (-1 = not configured) */
static int      c_beep_pin        = -1;     /* PWM BEEP pin (-1 = disabled) */
static uint     c_beep_pwm_slice  = 0;
static uint     c_beep_channel    = 0;
static uint32_t c_beep_duty       = 0;      /* 0-65535 */
static bool     c_beep_on         = false;
/* After reset, block beep-ON until ROM writes 0xC0 (explicit silence) or
   until c_port_read_count > C_BEEP_GUARD_READS.  Prevents stuck-beep programs
   from restarting the buzz through the ROM's boot-time PD writes. */
static bool     c_beep_post_reset_guard = false;
#define C_BEEP_GUARD_READS 500u

/* Polling-based key notification (ISR-safe: no mp_sched_schedule) */
static volatile int16_t c_kb_last_pressed_scancode = -1;
/* Physical hold state for cursor keys only (set on press, cleared on release) */
static volatile uint8_t c_kb_held_cursor = 0;


/* Forward declaration */
static void c_kb_process_usb_key(uint8_t scancode, bool pressed);

/* Convert KI line number (1-12) to column index (0-11) */
static inline int ki_to_col(int ki) {
  if (ki >= 1 && ki <= 8) return 8 - ki;
  if (ki >= 9 && ki <= 12) return 20 - ki;
  return -1;
}

/* Press a key by (row, ki) */
static void c_kb_press(int row, int ki) {
  int col = ki_to_col(ki);
  if (row >= 0 && row < KB_ROWS && col >= 0 && col < KB_COLS) {
    c_kb_matrix[row][col] = true;
  }
}

/* Release a key by (row, ki) */
static void c_kb_release(int row, int ki) {
  int col = ki_to_col(ki);
  if (row >= 0 && row < KB_ROWS && col >= 0 && col < KB_COLS) {
    c_kb_matrix[row][col] = false;
  }
}

static bool c_kb_has_key_pressed(void) {
  for (int r = 0; r < KB_ROWS; r++)
    for (int c = 0; c < KB_COLS; c++)
      if (c_kb_matrix[r][c]) return true;
  return false;
}

static uint16_t c_kb_compute_ky(void) {
  uint16_t result = 0;
  int sel = c_kb_ia_select & 0x0F;
  int start, end;
  if (sel == 0x0D) { start = 0; end = 13; }
  else if (sel >= 0 && sel <= 12) { start = sel; end = sel + 1; }
  else { return 0; }
  for (int row = start; row < end; row++) {
    for (int col = 0; col < KB_COLS; col++) {
      if (c_kb_matrix[row][col]) {
        if (col < 8) result |= (1u << col);
        else         result |= (1u << (col + 4));
      }
    }
  }
  return result & 0xFFFF;
}

/* I/O callback wrappers for selective MMIO hooking */
static uint8_t c_io_read_wrapper(void *ctx, uint8_t segment, uint32_t offset) {
    /* VDP registers (0x0C20-0x0C24): handle entirely in C, no Python call */
    if (offset >= 0x0C20 && offset <= 0x0C24) {
        return lcd_vdp_read(lcd_c_get_state(), offset - 0x0C20);
    }
    /* DMA status register (0x0C37) */
    if (offset == 0x0C37) {
        return dma_status;
    }
    if (py_io_read_callback != MP_OBJ_NULL) {
        mp_obj_t args[2];
        args[0] = MP_OBJ_NEW_SMALL_INT(segment);
        args[1] = MP_OBJ_NEW_SMALL_INT(offset);
        mp_obj_t res = mp_call_function_n_kw(py_io_read_callback, 2, 0, args);
        return (uint8_t)mp_obj_get_int(res);
    }
    return 0;
}

static void c_io_write_wrapper(void *ctx, uint8_t segment, uint32_t offset, uint8_t data) {
    /* VDP registers (0x0C20-0x0C24): handle entirely in C, no Python call */
    if (offset >= 0x0C20 && offset <= 0x0C24) {
        lcd_vdp_write(lcd_c_get_state(), offset - 0x0C20, data);
        return;
    }
    /* DMA registers (0x0C30-0x0C37): handle entirely in C, no Python call */
    if (offset >= 0x0C30 && offset <= 0x0C37) {
        switch (offset) {
        case 0x0C30: dma_src_bank =  data & 0x03u; break;
        case 0x0C31: dma_src_addr = (dma_src_addr & 0x7F00u) | data; break;
        case 0x0C32: dma_src_addr = (dma_src_addr & 0x00FFu) | ((uint16_t)(data & 0x7Fu) << 8); break;
        case 0x0C33: dma_dst_addr = (dma_dst_addr & 0x3F00u) | data; break;
        case 0x0C34: dma_dst_addr = (dma_dst_addr & 0x00FFu) | ((uint16_t)(data & 0x3Fu) << 8); break;
        case 0x0C35: dma_len      = (dma_len & 0x3F00u) | data; break;
        case 0x0C36: dma_len      = (dma_len & 0x00FFu) | ((uint16_t)(data & 0x3Fu) << 8); break;
        case 0x0C37: _dma_execute(); break;
        }
        return;
    }
    if (py_io_write_callback != MP_OBJ_NULL) {
        mp_obj_t args[3];
        args[0] = MP_OBJ_NEW_SMALL_INT(segment);
        args[1] = MP_OBJ_NEW_SMALL_INT(offset);
        args[2] = MP_OBJ_NEW_SMALL_INT(data);
        mp_call_function_n_kw(py_io_write_callback, 3, 0, args);
    }
}


/* 
 * Advanced USB Mapping
 * host_mod: bit0=Shift, bit1=Alt.
 */
typedef struct {
  uint8_t scancode;
  uint8_t host_mod;
  uint8_t coords[4][2]; /* 0xFF terminated */
} adv_usb_map_t;

#define MAX_ADV_MAP_ENTRIES 64
static adv_usb_map_t dynamic_adv_map[MAX_ADV_MAP_ENTRIES];
static size_t dynamic_adv_map_count = 0;

/* Standard keys fallback (scancode -> row, ki) */
typedef struct { uint8_t scancode; uint8_t row; uint8_t ki; } base_usb_key_t;

#define MAX_BASE_MAP_ENTRIES 128
static base_usb_key_t dynamic_base_map[MAX_BASE_MAP_ENTRIES];
static size_t dynamic_base_map_count = 0;

/* 
 * Standard Default Mappings (initialized in module init or first call)
 */
static const adv_usb_map_t default_adv_map[] = {
  {0xE2, 2, {{11, 2}, {0xFF, 0}}}, /* L_ALT -> SFT */
  {0xE6, 2, {{11, 2}, {0xFF, 0}}}, /* R_ALT -> SFT */
  {0x1F, 1, {{2, 3}, {0xFF, 0}}},  /* Shift + 2 -> " */
  {0x21, 1, {{2, 4}, {0xFF, 0}}},  /* Shift + 4 -> $ */
  {0x23, 1, {{2, 5}, {0xFF, 0}}},  /* Shift + 6 -> & */
  {0x25, 1, {{7, 4}, {0xFF, 0}}},  /* Shift + 8 -> ( */
  {0x26, 1, {{6, 3}, {0xFF, 0}}},  /* Shift + 9 -> ) */
  {0x2D, 1, {{2, 6}, {0xFF, 0}}},  /* Shift + - -> = */
  {0x33, 1, {{9, 3}, {0xFF, 0}}},  /* Shift + ; -> + */
  {0x34, 1, {{8, 3}, {0xFF, 0}}},  /* Shift + : -> * */
  {0x24, 1, {{11, 2}, {3, 1}, {0xFF, 0}}}, /* Shift + 7 -> SFT + U (') */
  {0x32, 1, {{11, 2}, {7, 3}, {0xFF, 0}}}, /* Shift + ] -> SFT + / */
  {0x37, 0, {{10, 6}, {0xFF, 0}}}, /* . */
  {0x36, 0, {{2, 1}, {0xFF, 0}}},  /* , */
  {0x2E, 0, {{7, 1}, {0xFF, 0}}},  /* ^ */
  {0x2D, 0, {{10, 3}, {0xFF, 0}}}, /* - */
  {0x33, 0, {{2, 7}, {0xFF, 0}}},  /* ; */
  {0x34, 0, {{2, 8}, {0xFF, 0}}},  /* : */
};
#define DEFAULT_ADV_MAP_SIZE (sizeof(default_adv_map)/sizeof(default_adv_map[0]))

static const base_usb_key_t default_base_map[] = {
  {0x04, 4, 4}, {0x05, 5, 7}, {0x06, 5, 5}, {0x07, 4, 6}, /* a,b,c,d */
  {0x08, 3, 5}, {0x09, 4, 7}, {0x0A, 4, 8}, {0x0B, 4, 1}, /* e,f,g,h */
  {0x0C, 8, 1}, {0x0D, 9, 1}, {0x0E, 9, 8}, {0x0F, 9, 7}, /* i,j,k,l */
  {0x10, 5, 1}, {0x11, 5, 8}, {0x12, 8, 8}, {0x13, 8, 7}, /* m,n,o,p */
  {0x14, 3, 3}, {0x15, 3, 6}, {0x16, 4, 5}, {0x17, 3, 7}, /* q,r,s,t */
  {0x18, 3, 1}, {0x19, 5, 6}, {0x1A, 3, 4}, {0x1B, 5, 4}, /* u,v,w,x */
  {0x1C, 3, 8}, {0x1D, 5, 3}, /* y,z */
  {0x1E, 9, 6}, {0x1F, 9, 5}, {0x20, 9, 4}, {0x21, 8, 6}, /* 1,2,3,4 */
  {0x22, 8, 5}, {0x23, 8, 4}, {0x24, 7, 7}, {0x25, 7, 6}, /* 5,6,7,8 */
  {0x26, 7, 5}, {0x27, 10, 7}, /* 9,0 */
  {0x2C, 10, 1}, /* Space */
  {0x28, 10, 4}, {0x29, 1, 1},  {0x2A, 6, 7},
  {0x4F, 3, 9},  {0x50, 5, 10}, {0x51, 4, 9},  {0x52, 5, 9},
  {0x49, 6, 5},  {0x4C, 6, 7},  {0x45, 6, 6},
  {0x3A, 7, 9},  {0x3B, 8, 9},  {0x3C, 9, 9},  {0x3D, 10, 9},
  {0x3E, 5, 11}, {0x3F, 6, 11}, {0x40, 4, 11},
};
#define DEFAULT_BASE_MAP_SIZE (sizeof(default_base_map)/sizeof(default_base_map[0]))

static void c_kb_init_defaults(void) {
  if (dynamic_adv_map_count == 0) {
    memcpy(dynamic_adv_map, default_adv_map, sizeof(default_adv_map));
    dynamic_adv_map_count = DEFAULT_ADV_MAP_SIZE;
  }
  if (dynamic_base_map_count == 0) {
    memcpy(dynamic_base_map, default_base_map, sizeof(default_base_map));
    dynamic_base_map_count = DEFAULT_BASE_MAP_SIZE;
  }
}

static void c_kb_process_usb_key(uint8_t scancode, bool pressed) {
  /* 0. Record last pressed scancode for Python polling (ISR-safe) */
  if (pressed) {
    c_kb_last_pressed_scancode = (int16_t)scancode;
  }
  /* Track cursor key physical hold state for Python key-repeat */
  if (scancode == 0x4F || scancode == 0x50 || scancode == 0x51 || scancode == 0x52) {
    c_kb_held_cursor = pressed ? scancode : 0;
  }
  /* 1. Update modifier state trackers */
  if (scancode == 0xE1 || scancode == 0xE5) {
    host_shift_physical = pressed;
  }
  if (scancode == 0xE2 || scancode == 0xE6) {
    host_alt_physical = pressed;
  }
  if (scancode == 0xE0 || scancode == 0xE4) {
    host_ctrl_physical = pressed;
  }
  if (scancode == 0xE3 || scancode == 0xE7) {
    host_gui_physical = pressed;
  }

  /* 2. Handle F11: Win+F11 = Reset (Python detects via get_last_key + gui window),
   *                plain F11 = KEY_OUT via normal map lookup (falls through). */
  if (scancode == 0x44 && pressed) {
    if (host_gui_physical) {
      /* Win+F11: do NOT fire save-state; Python main loop handles reset */
      return;
    }
    /* plain F11: fall through to ADV/base map → KEY_OUT */
  }

  /* 2B. F9 no longer intercepted here; falls through to base map → KEY_MEMO */

  /* 3. Handle key release */
  if (!pressed) {
    for (int i = 0; i < c_kb_active_usb_count; i++) {
      if (c_kb_active_usb[i].scancode == scancode) {
        /* SFT combo fire-and-hold: if coords[0]==(11,2) with targets, ensure all keys
           are pressed and hold the matrix state for ROM to process. If phase 2 had not
           fired yet, fire it now so the character is not lost on a fast tap. */
        bool had_sft_combo = (c_kb_active_usb[i].n_coords > 1 &&
                              c_kb_active_usb[i].coords[0][0] == 11 &&
                              c_kb_active_usb[i].coords[0][1] == 2);
        if (had_sft_combo) {
          if (c_kb_phase2_pending && c_kb_phase2_scancode == scancode) {
            /* Phase 2 never fired — press targets now (fire-on-release) */
            for (int j = 0; j < c_kb_phase2_n_coords; j++) {
              c_kb_press(c_kb_phase2_coords[j][0], c_kb_phase2_coords[j][1]);
            }
            c_kb_phase2_pending = false;
            c_kb_phase2_n_coords = 0;
          }
          /* Store all combo coords for deferred release; keys stay in matrix */
          c_kb_deferred_combo_n_coords = c_kb_active_usb[i].n_coords;
          for (int j = 0; j < c_kb_active_usb[i].n_coords; j++) {
            c_kb_deferred_combo_coords[j][0] = c_kb_active_usb[i].coords[j][0];
            c_kb_deferred_combo_coords[j][1] = c_kb_active_usb[i].coords[j][1];
          }
          c_kb_deferred_combo_release_ms = mp_hal_ticks_ms() + C_KB_DEFERRED_COMBO_HOLD_MS;
          c_kb_next_pulse_ms = 0; /* ensure KEY_INT fires promptly for ROM to process */
        } else {
          /* Non-SFT combo or single key: cancel any pending phase 2 and release now */
          if (c_kb_phase2_pending && c_kb_phase2_scancode == scancode) {
            c_kb_phase2_pending = false;
            c_kb_phase2_n_coords = 0;
          }
          for (int j = 0; j < c_kb_active_usb[i].n_coords; j++) {
            c_kb_release(c_kb_active_usb[i].coords[j][0], c_kb_active_usb[i].coords[j][1]);
          }
        }
        c_kb_active_usb[i] = c_kb_active_usb[--c_kb_active_usb_count];
        break;
      }
    }
    if (!c_kb_has_key_pressed()) {
      if (c_kb_key_line_state) {
        hd61700_set_input(&cpu_state, HD61700_KEY_INT, 0);
        c_kb_key_line_state = false;
      }
      c_kb_pulse_release_pending = false;
      c_kb_post_release_pulses_remaining = C_KB_POST_RELEASE_PULSES_MAX;
    }
    if (scancode == 0x29) { /* Break */
      hd61700_set_input(&cpu_state, HD61700_INT1, 0);
      hd61700_set_input(&cpu_state, HD61700_ON_INT, 0);
    }
    /* Record release time for debounce */
    last_release_ms[scancode] = mp_hal_ticks_ms();
    return;
  }

  /* 4. Handle key press */
  /* Prevent duplicate press tracking if already active */
  for (int i = 0; i < c_kb_active_usb_count; i++) {
    if (c_kb_active_usb[i].scancode == scancode) return;
  }

  /* Debounce: Skip press if it happens too soon after previous release (e.g. 50ms) */
  if (mp_hal_ticks_ms() - last_release_ms[scancode] < 50) {
    return;
  }

  uint8_t current_mod = 0;
  if (host_shift_physical) current_mod |= 1;
  if (host_alt_physical)   current_mod |= 2;
  if (host_ctrl_physical)  current_mod |= 4;
  if (host_gui_physical)   current_mod |= 8;

  usb_active_key_t *ak = 0;
  if (c_kb_active_usb_count < MAX_ACTIVE_USB_KEYS) {
    ak = &c_kb_active_usb[c_kb_active_usb_count++];
    ak->scancode = scancode;
    ak->n_coords = 0;
  } else {
    return; /* too many keys */
  }

  bool found = false;
  /* A. Search advanced map first */
  c_kb_init_defaults();
  for (size_t i = 0; i < dynamic_adv_map_count; i++) {
    if (dynamic_adv_map[i].scancode == scancode && dynamic_adv_map[i].host_mod == current_mod) {
      /* Collect all coords into ak (needed for correct release regardless of phase) */
      for (int j = 0; j < 4; j++) {
        uint8_t r = dynamic_adv_map[i].coords[j][0];
        uint8_t k = dynamic_adv_map[i].coords[j][1];
        if (r == 0xFF) break;
        ak->coords[ak->n_coords][0] = r;
        ak->coords[ak->n_coords][1] = k;
        ak->n_coords++;
      }
      /* Two-phase press: if first coord is SFT (11,2) and more coords follow,
         press SFT alone first so ROM can latch it before the target key arrives. */
      bool is_sft_combo = (ak->n_coords > 1 &&
                           ak->coords[0][0] == 11 &&
                           ak->coords[0][1] == 2);
      if (is_sft_combo) {
        c_kb_press(11, 2); /* phase 1: SFT only */
        c_kb_phase2_pending = true;
        c_kb_phase2_scancode = scancode;
        c_kb_phase2_n_coords = ak->n_coords - 1;
        for (int j = 0; j < c_kb_phase2_n_coords; j++) {
          c_kb_phase2_coords[j][0] = ak->coords[j + 1][0];
          c_kb_phase2_coords[j][1] = ak->coords[j + 1][1];
        }
      } else {
        for (int j = 0; j < ak->n_coords; j++) {
          c_kb_press(ak->coords[j][0], ak->coords[j][1]);
        }
      }
      found = true;
      break;
    }
  }

  /* B. Falling back to base map (unshifted logic by default) */
  if (!found) {
    for (size_t i = 0; i < dynamic_base_map_count; i++) {
      if (dynamic_base_map[i].scancode == scancode) {
        ak->coords[0][0] = dynamic_base_map[i].row;
        ak->coords[0][1] = dynamic_base_map[i].ki;
        ak->n_coords = 1;
        c_kb_press(ak->coords[0][0], ak->coords[0][1]);
        found = true;
        break;
      }
    }
  }

  if (!found) {
    /* If nothing found, just remove from active list since we didn't press anything */
    c_kb_active_usb_count--;
    return;
  }

  /* 5. Post-press handling */
  /* BREAK/ON interrupts assert on press to ensure wake (ON_INT is unmasked during sleep) */
  if (scancode == 0x29) {
    hd61700_set_input(&cpu_state, HD61700_INT1, 1);
    hd61700_set_input(&cpu_state, HD61700_ON_INT, 1);
  }

  /* Allow immediate first KEY_INT pulse */
  c_kb_next_pulse_ms = 0;
}

/* Extern alias for usb_host_core.c */
void c_kb_process_usb_key_extern(uint8_t scancode, bool pressed) {
  c_kb_process_usb_key(scancode, pressed);
}


/* Service KEY_INT pulses - called from hd61700_execute wrapper */
static void c_kb_service_input_lines(void) {
  /* Deferred combo release: release SFT+target together after hold period expires */
  if (c_kb_deferred_combo_release_ms != 0 &&
      (int32_t)(mp_hal_ticks_ms() - c_kb_deferred_combo_release_ms) >= 0) {
    for (int j = 0; j < c_kb_deferred_combo_n_coords; j++) {
      c_kb_release(c_kb_deferred_combo_coords[j][0], c_kb_deferred_combo_coords[j][1]);
    }
    c_kb_deferred_combo_n_coords = 0;
    c_kb_deferred_combo_release_ms = 0;
    if (!c_kb_has_key_pressed()) {
      c_kb_post_release_pulses_remaining = C_KB_POST_RELEASE_PULSES_MAX;
    }
  }
  /* Release pulse on next call (provides ~1 frame pulse duration) */
  if (c_kb_pulse_release_pending && c_kb_key_line_state) {
    hd61700_set_input(&cpu_state, HD61700_KEY_INT, 0);
    c_kb_key_line_state = false;
    c_kb_pulse_release_pending = false;
    /* Phase 2: SFT-only KEY_INT has fired; now add the target key to the matrix.
       Reset the timer so the next KEY_INT fires immediately (not after 25ms),
       giving the ROM SFT+target before the key can be released. */
    if (c_kb_phase2_pending && c_kb_phase2_n_coords > 0) {
      for (int j = 0; j < c_kb_phase2_n_coords; j++) {
        c_kb_press(c_kb_phase2_coords[j][0], c_kb_phase2_coords[j][1]);
      }
      c_kb_phase2_pending = false;
      c_kb_next_pulse_ms = 0;  /* fire next KEY_INT immediately */
    }
  }
  /* Check if IE allows KEY interrupts (bit 6) */
  bool key_ie_enabled = (cpu_state.reg8bit[5] & 0x40) != 0;
  if (!key_ie_enabled) return;

  uint8_t ia = cpu_state.reg8bit[4];
  bool should_pulse = false;
  bool has_pressed = c_kb_has_key_pressed();
  uint32_t now = mp_hal_ticks_ms();

  if ((int32_t)(now - c_kb_next_pulse_ms) < 0) return;

  if (!(ia & 0x80)) {
    /* IA bit 7 is 0: constant pulse every 25ms regardless of keys (matches Python) */
    should_pulse = true;
  } else if (has_pressed && c_kb_compute_ky() != 0) {
    /* IA bit 7 is 1: pulse only if row matches */
    should_pulse = true;
  } else if (!has_pressed && c_kb_post_release_pulses_remaining > 0) {
    /* Send remaining pulses after key release to ensure BIOS sees it */
    should_pulse = true;
    c_kb_post_release_pulses_remaining--;
  }

  if (should_pulse) {
    if (!c_kb_key_line_state) {
      hd61700_set_input(&cpu_state, HD61700_KEY_INT, 1);
      c_kb_key_line_state = true;
      c_kb_pulse_release_pending = true;
      c_kb_next_pulse_ms = now + c_kb_pulse_interval_ms;
    }
  } else {
    /* Not pressing any keys matching current scan, and no post-release pending */
    c_kb_post_release_pulses_remaining = 0;
  }
}

static bool is_key_trace_addr(uint32_t offset) {
  return offset == 0x68D2 || offset == 0x68D3 || offset == 0x68D4 ||
         offset == 0x68D5 || offset == 0x68D6 || offset == 0x68D7 ||
         offset == 0x68D8;
}

static bool is_key_buffer_trace_addr(uint32_t offset) {
  return offset >= 0x68D9 && offset <= 0x68EC;
}

/* Python callback objects */
static mp_obj_t py_mem_read_cb = MP_OBJ_NULL;
static mp_obj_t py_mem_write_cb = MP_OBJ_NULL;
static mp_obj_t py_port_read_cb = MP_OBJ_NULL;
static mp_obj_t py_port_write_cb = MP_OBJ_NULL;

static mp_obj_t py_callback_anchor_list = MP_OBJ_NULL;

/* Accept either bank index (0-3) or raw UA value (e.g. 0x10 for bank 1). */
static inline uint8_t normalize_bank(uint8_t segment) {
  return (segment <= 3) ? (segment & 0x03u) : ((segment >> 4) & 0x03u);
}

/* Focused write-tracing for PBFTOBIN source area in RAM. */
#define PROG_TRACE_START 0xB5D6u
#define PROG_TRACE_END 0xB7E6u
#define SSTOP_SBOT_TRACE_START 0x6931u
#define SSTOP_SBOT_TRACE_END 0x6934u
#define ENABLE_PROG_WRITE_TRACE 1
#define ENABLE_SSTOP_SBOT_WRITE_TRACE 1
#define DIR_ENTRY_TRACE_START 0x6F54u
#define DIR_ENTRY_TRACE_END 0x6F95u
#define ENABLE_DIR_ENTRY_TRACE 1

static inline bool is_prog_trace_addr(uint32_t offset) {
  return offset >= PROG_TRACE_START && offset <= PROG_TRACE_END;
}

static inline bool is_sstop_sbot_trace_addr(uint32_t offset) {
  return offset >= SSTOP_SBOT_TRACE_START && offset <= SSTOP_SBOT_TRACE_END;
}


static inline bool is_dir_entry_trace_addr(uint32_t offset) {
  return offset >= DIR_ENTRY_TRACE_START && offset <= DIR_ENTRY_TRACE_END;
}
static inline bool should_log_sstop_sbot_write(uint32_t offset, uint8_t data) {
  if (!is_sstop_sbot_trace_addr(offset)) {
    return false;
  }
  uint32_t idx = offset - SSTOP_SBOT_TRACE_START;
  if (idx >= 4u) {
    return false;
  }
  if (sstop_sbot_last_valid[idx] && sstop_sbot_last[idx] == data) {
    return false;
  }
  sstop_sbot_last_valid[idx] = true;
  sstop_sbot_last[idx] = data;
  return true;
}

static void format_last_opcodes(char *out, size_t out_size) {
  static const char hex[] = "0123456789ABCDEF";
  if (out_size == 0) {
    return;
  }

  size_t op_len = cpu_state.last_op_len;
  if (op_len > sizeof(cpu_state.last_opcodes)) {
    op_len = sizeof(cpu_state.last_opcodes);
  }
  if (op_len == 0) {
    if (out_size >= 2) {
      out[0] = '-';
      out[1] = '\0';
    } else {
      out[0] = '\0';
    }
    return;
  }

  size_t pos = 0;
  for (size_t i = 0; i < op_len; i++) {
    if (i > 0) {
      if (pos + 1 >= out_size) {
        break;
      }
      out[pos++] = ' ';
    }
    if (pos + 2 >= out_size) {
      break;
    }
    uint8_t b = cpu_state.last_opcodes[i];
    out[pos++] = hex[(b >> 4) & 0x0F];
    out[pos++] = hex[b & 0x0F];
  }
  out[pos] = '\0';
}

static void log_watch_write(const char *tag, uint8_t bank, uint32_t offset,
                            uint8_t data, const char *note) {
  char op_hex[(sizeof(cpu_state.last_opcodes) * 3)];
  uint8_t op_len = cpu_state.last_op_len;
  if (op_len > sizeof(cpu_state.last_opcodes)) {
    op_len = sizeof(cpu_state.last_opcodes);
  }
  format_last_opcodes(op_hex, sizeof(op_hex));

  /*
  if (note != NULL && note[0] != '\0') {
    mp_printf(&mp_plat_print,
              "[HD61700] %s PC=%04X UA=%02X BANK=%u RAM[%04X] <= %02X OPLEN=%u OP=%s (%s)\n",
              tag, (unsigned int)cpu_state.pc,
              (unsigned int)cpu_state.reg8bit[3], (unsigned int)bank,
              (unsigned int)offset, (unsigned int)data,
              (unsigned int)op_len, op_hex, note);
  } else {
    mp_printf(&mp_plat_print,
              "[HD61700] %s PC=%04X UA=%02X BANK=%u RAM[%04X] <= %02X OPLEN=%u OP=%s\n",
              tag, (unsigned int)cpu_state.pc,
              (unsigned int)cpu_state.reg8bit[3], (unsigned int)bank,
              (unsigned int)offset, (unsigned int)data,
              (unsigned int)op_len, op_hex);
  }
  */
}

/* Auto-detect expanded RAM image from common paths. */
static bool detect_bank_file(int slot) {
  struct stat st;
  char path[32];
  snprintf(path, sizeof(path), "/roms/ram%d.bin", slot);
  if (stat(path, &st) == 0) return true;
  snprintf(path, sizeof(path), "roms/ram%d.bin", slot);
  if (stat(path, &st) == 0) return true;
  snprintf(path, sizeof(path), "/sd/ram%d.bin", slot);
  if (stat(path, &st) == 0) return true;
  return false;
}

static void detect_all_banks(void) {
  for (int i = 1; i <= 3; i++) {
    has_bank[i] = detect_bank_file(i);
  }
}

static void anchor_callbacks(mp_obj_t obj) {
  if (obj == mp_const_none)
    return;
  if (py_callback_anchor_list == MP_OBJ_NULL) {
    py_callback_anchor_list = mp_obj_new_list(0, NULL);
  }
  mp_obj_list_append(py_callback_anchor_list, obj);
}

static uint16_t c_kb_read(void *ctx) {
  (void)ctx;
  return c_kb_compute_ky();
}

static void c_kb_write(void *ctx, uint8_t data) {
  (void)ctx;
  c_kb_ia_select = data;
}

/* Apply BEEP on/off state to PWM hardware without redundant register writes. */
static void c_beep_apply(bool on) {
  if (c_beep_on == on || c_beep_pin < 0) return;
  c_beep_on = on;
  pwm_set_chan_level(c_beep_pwm_slice, c_beep_channel,
                    on ? (uint16_t)c_beep_duty : 0u);
}

static uint8_t c_port_read(void *ctx) {
  (void)ctx;
  /* PD_PWR = bit4, active LOW: FDD interface is powered when bit4=0 */
  bool fdd_powered = (c_port_data & 0x10u) == 0;
  if (fdd_powered && py_port_read_cb != MP_OBJ_NULL) {
    /* Delegate to Python for FDD transfer-direction ACK */
    mp_obj_t result = mp_call_function_0(py_port_read_cb);
    return (uint8_t)mp_obj_get_int(result);
  }
  /* Normal mode: read RX GPIO + boot-sequence ON key simulation */
  uint8_t rx_bit = (c_rx_pin >= 0) ? (gpio_get((uint)c_rx_pin) ? 1u : 0u) : 0u;
  c_port_read_count++;
  /* Fallback: clear beep post-reset guard once boot sequence is well past */
  if (c_beep_post_reset_guard && c_port_read_count > C_BEEP_GUARD_READS) {
    c_beep_post_reset_guard = false;
  }
  uint8_t on_key = (c_port_read_count < 100u) ? 0u : 1u;
  return on_key | (uint8_t)(rx_bit << 3);
}

static void c_port_write(void *ctx, uint8_t data) {
  (void)ctx;
  uint8_t prev = c_port_data;
  c_port_data  = data;
  bool was_fdd = (prev & 0x10u) == 0;  /* PD_PWR was low (active) */
  bool is_fdd  = (data & 0x10u) == 0;  /* PD_PWR is  low (active) */

  /* TX GPIO for RS-232C bit-banging — suppress when FDD interface is powered */
  if (!is_fdd && c_tx_pin >= 0) {
    gpio_put((uint)c_tx_pin, (data >> 2) & 1u);
  }

  /* BEEP: bit6=0x40 or bit7=0x80 → sound; both set (0xC0) → silence
     Post-reset guard: block beep-ON until ROM explicitly writes 0xC0.
     This prevents stuck-beep programs from re-activating the buzzer through
     the ROM's boot-time PD writes after a reset. */
  if (c_beep_pin >= 0) {
    uint8_t beep_bits = data & 0xC0u;
    if (beep_bits == 0xC0u) {
      c_beep_post_reset_guard = false;  /* ROM silenced beep: normal ops resume */
      c_beep_apply(false);
    } else if (beep_bits == 0x40u || beep_bits == 0x80u) {
      if (!c_beep_post_reset_guard) {
        c_beep_apply(true);
      }
      /* guard active: skip beep-ON to prevent stuck-beep restart after reset */
    } else {
      /* beep_bits == 0x00: both pins LOW = no potential difference = silence.
         ROM BEEP uses 0xC0 to silence, but programs like SOS.ASM use
         AN PD,0x3F (clearing both bits to 0x00) as the off-phase. */
      c_beep_apply(false);
    }
  }

  /* FDD state machine: delegate to Python when interface was or is powered */
  if ((was_fdd || is_fdd) && py_port_write_cb != MP_OBJ_NULL) {
    mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
    mp_call_function_n_kw(py_port_write_cb, 1, 0, args);
  }
}

static void c_log_write(void *ctx, const char *msg) {
  (void)ctx;
  mp_printf(&mp_plat_print, "[HD61700] %s\n", msg);
}

/* ====== Direct C-to-C callbacks (High Performance) ====== */

static uint8_t c_mem_direct_read(void *ctx, uint8_t segment, uint32_t offset) {
  (void)ctx;
  
  /* Emulate hardware level-triggered interrupt for UART RX */
  if (uart_rx_head != uart_rx_tail) {
    if (!(cpu_state.reg8bit[2] & (1 << HD61700_INT1))) {
      cpu_state.reg8bit[2] |= (1 << HD61700_INT1); /* Assert REG_IB */
      cpu_state.state &= ~CPU_SLP; /* Wake from sleep */
    }
  }

  uint8_t bank = normalize_bank(segment);
  uint32_t logical_addr = offset & 0xFFFF;

  /* Priority 1: MMIO UART/FDD (0x0C00-0x0C0F) 
     We trap this first regardless of bank/segment because these ports are 
     always mapped to the logical IO space in PB-1000. */
  if (logical_addr >= 0x0C00 && logical_addr <= 0x0C04) {
    if (logical_addr == 0x0C00 || logical_addr == 0x0C01 || logical_addr == 0x0C03 || logical_addr == 0x0C04) {
      return c_io_read_wrapper(NULL, bank, logical_addr);
    }
    if (logical_addr == 0x0C02) {
      uint8_t val = 0;
      if (uart_rx_head != uart_rx_tail) {
        val = uart_rx_fifo[uart_rx_tail++];
        /* DEBUG log for C core consumption: focus on ':' (0x3A) and 0x8F */
        if (val == 0x3A || val == 0x8F) {
          mp_printf(&mp_plat_print, "DB: UART RX READ (0x0C02) val=%02X PC=%04X tail=%d\n", val, cpu_state.pc, uart_rx_tail-1);
        }
        if (uart_rx_head == uart_rx_tail) {
          /* Clear INT1 both in IRQ controller and input state */
          hd61700_set_input(&cpu_state, HD61700_INT1, 0);
          cpu_state.irq_status &= ~(1 << HD61700_INT1);
          cpu_state.reg8bit[2] &= ~(1 << HD61700_INT1); /* REG_IB */
        }
        return val;
      }
      return 0x00;
    }
  }

  /* Priority 2: Fixed Bank 0 / Internal ROM area (0x0000-0x1FFF) */
  if (offset < 0x2000) {
    return (offset < rom0_size) ? rom0_buf[offset] : 0xFF;
  }
  /* 0x6000-0x7FFF: RAM */
  if (offset >= 0x6000 && offset < 0x8000) {
    return ram_buf[offset - 0x6000];
  }
  /* 0x8000-0xFFFF: Banked Memory */
  if (offset >= 0x8000) {
    uint32_t off = offset - 0x8000u;
    if (off >= 0x8000) return 0xFF;
    if (bank == 0) {
      /* Bank 0: System ROM */
      return (rom1_size > 0) ? rom1_buf[off % rom1_size] : 0xFF;
    }
    /* Banks 1-3: RAM */
    static uint8_t * const bank_bufs[3] = {bank1_buf, bank2_buf, bank3_buf};
    if (bank >= 1 && bank <= 3 && has_bank[bank])
      return bank_bufs[bank - 1][off];
    return 0xFF;
  }
  /* Extension work area 0x5F00-0x5FFF */
  if (logical_addr >= 0x5F00 && logical_addr < 0x6000) {
    return ext_work_buf[logical_addr - 0x5F00];
  }
  return 0xFF;
}

static void c_mem_direct_write(void *ctx, uint8_t segment, uint32_t offset,
                               uint8_t data) {
  (void)ctx;
  uint8_t bank = normalize_bank(segment);
  uint32_t logical_addr = offset & 0xFFFF;

  /* Priority 1: MMIO Trap for Writes (0x0C00-0x0C0F) */
  if (logical_addr >= 0x0C00 && logical_addr <= 0x0C0F) {
    c_io_write_wrapper(NULL, bank, logical_addr, data);
    return;
  }

  /* Priority 2: Main RAM (0x6000-0x7FFF) */
  if (offset >= 0x6000 && offset < 0x8000) {
    ram_buf[offset - 0x6000] = data;
    /*
    if (ENABLE_PROG_WRITE_TRACE && is_prog_trace_addr(offset)) {
      log_watch_write("PROG-WR", bank, offset, data, NULL);
    }
    if (ENABLE_DIR_ENTRY_TRACE && is_dir_entry_trace_addr(offset)) {
      log_watch_write("DIR-WR", bank, offset, data, NULL);
    }
    if (ENABLE_SSTOP_SBOT_WRITE_TRACE &&
        should_log_sstop_sbot_write(offset, data)) {
      log_watch_write("SSTOP/SBOT-WR", bank, offset, data, NULL);
    }
    */
    if (cpu_debug_enabled && cpu_key_debug_enabled &&
        is_key_trace_addr(offset)) {
      mp_printf(&mp_plat_print, "[HD61700] C RAM WRITE: %04X <= %02X\n",
                (unsigned int)offset, (unsigned int)data);
    } else if (cpu_debug_enabled &&
               (cpu_key_debug_enabled || is_key_buffer_trace_addr(offset)) &&
               is_key_buffer_trace_addr(offset)) {
      mp_printf(&mp_plat_print, "KEY BUF WRITE: [%04X] <= %02X\n",
                (unsigned int)offset, (unsigned int)data);
    }
  }
  /* 0x0C00-0x0C04: MMIO UART/FDD area (High Speed Trap) */
  if (offset >= 0x0C00 && offset <= 0x0C04) {
    if (offset == 0x0C03 || offset == 0x0C04) {
      vfdd_write_reg = data;
      if (offset == 0x0C03) {
        /* TX Data Register: Push to TX FIFO (Legacy UART path) */
        if ((uint8_t)(uart_tx_head + 1) != uart_tx_tail) {
          uart_tx_fifo[uart_tx_head++] = data;
        }
      }
    }
    return;
  }
  /* Extension work area 0x5F00-0x5FFF */
  if (offset >= 0x5F00 && offset < 0x6000) {
    ext_work_buf[offset - 0x5F00] = data;
    return;
  }
  /* Banks 1-3: RAM Write (0x8000-0xFFFF) */
  else if (offset >= 0x8000 && bank >= 1 && bank <= 3) {
    uint32_t off = offset - 0x8000;
    if (off < 0x8000 && has_bank[bank]) {
      static uint8_t * const bank_bufs[3] = {bank1_buf, bank2_buf, bank3_buf};
      bank_bufs[bank - 1][off] = data;
      if (ENABLE_PROG_WRITE_TRACE && is_prog_trace_addr(offset)) {
        log_watch_write("PROG-WR", bank, offset, data, NULL);
      }
    } else if (is_prog_trace_addr(offset)) {
      log_watch_write("PROG-WR-IGN", bank, offset, data,
                      has_bank[bank] ? "out of range" : "no RAM");
    }
  } else if (is_prog_trace_addr(offset)) {
    log_watch_write("PROG-WR-IGN", bank, offset, data, "bank not writable");
  }
}

/* Bit-reverse one byte (bit7=top in charset.bin → bit0=top in VRAM) */
static inline uint8_t cdet_rb8(uint8_t b) {
  b = (b & 0xF0u) >> 4 | (b & 0x0Fu) << 4;
  b = (b & 0xCCu) >> 2 | (b & 0x33u) << 2;
  b = (b & 0xAAu) >> 1 | (b & 0x55u) << 1;
  return b;
}

/* Match accumulated 6-byte pattern against charset.bin (printable ASCII).
   DRAW_BITIMAGE data arrives bit7=top (direct match);
   all other modes arrive bit0=top (rb8 needed before compare).
   Returns ASCII code on match, -1 otherwise. */
static int cdet_match_charset(lcd_state_t *lcd) {
  if (!lcd->charset_loaded) return -1;
  bool bitimg = (_cdet_mode == LCDC_CMD_DRAW_BITIMAGE);
  for (int code = 0x20; code <= 0x7E; code++) {
    const uint8_t *g = &lcd->charset_buf[code * 8 + 1];
    bool match = true;
    for (int i = 0; i < 6; i++) {
      uint8_t b = bitimg ? _cdet_buf[i] : cdet_rb8(_cdet_buf[i]);
      if (b != g[i]) { match = false; break; }
    }
    if (match) return code;
  }
  return -1;
}

static void c_lcd_direct_ctrl(void *ctx, uint8_t data) {
  (void)ctx;
  lcd_ctrl(lcd_c_get_state(), data);
}

static void c_lcd_direct_write(void *ctx, uint8_t data) {
  (void)ctx;
  lcd_state_t *lcd = lcd_c_get_state();
  if (py_lcd_char_cb != MP_OBJ_NULL && !lcd->op_command) {
    int chip = lcd->active_chip;
    if (lcd->selected_ce & (1 << chip)) {
      int mode = lcd->chip_state[chip].mode;
      if (mode == LCDC_CMD_DRAW_CHAR) {
        /* Legacy DRAW_CHAR path */
        int8_t cur_y = (int8_t)(lcd->chip_state[chip].y & 0x03);
        if (c_lcd_char_last_y >= 0 && cur_y != c_lcd_char_last_y) {
          mp_call_function_1(py_lcd_char_cb, mp_const_none);
        }
        c_lcd_char_last_y = cur_y;
        mp_call_function_1(py_lcd_char_cb, MP_OBJ_NEW_SMALL_INT(data));
      } else {
        /* Pixel write path: DRAW_BITIMAGE and all other modes */
        uint8_t x    = (uint8_t)(lcd->chip_state[chip].x & 0xFF);
        uint8_t page = (uint8_t)(lcd->chip_state[chip].y & 0x03);
        /* Page change → signal row end, reset text context for old page */
        if (_cdet_lpage >= 0 && page != (uint8_t)_cdet_lpage) {
          _cdet_row_has_text[(uint8_t)_cdet_lpage] = false;
          mp_call_function_1(py_lcd_char_cb, mp_const_none);
          _cdet_col = 0;
        }
        _cdet_lpage = (int8_t)page;
        /* Column accumulation */
        if (x % 6 == 0) {
          _cdet_buf[0] = data;
          _cdet_col  = 1;
          _cdet_chip = (uint8_t)chip;
          _cdet_page = page;
          _cdet_x0   = x;
          _cdet_mode = (uint8_t)mode;  /* remember mode for rb8 direction */
        } else if (_cdet_col > 0 && _cdet_col < 6
                   && (uint8_t)chip == _cdet_chip
                   && page == _cdet_page
                   && x == (uint8_t)(_cdet_x0 + _cdet_col)) {
          _cdet_buf[_cdet_col++] = data;
          if (_cdet_col == 6) {
            int code = cdet_match_charset(lcd);
            _cdet_col = 0;
            if (code >= 0) {
              uint8_t gcol = (uint8_t)((_cdet_chip ? 16u : 0u) + (_cdet_x0 / 6u));
              if (code == 0x20) {
                /* Space: the space glyph (all-zero pixels) is indistinguishable
                   from a blank LCD area.  Only emit in text context (at least one
                   non-space char was already detected on this page), and only
                   when the shadow shows the position was not already space.
                   This prevents blank screen areas from contaminating the shadow
                   and then suppressing real spaces in subsequent text output. */
                if (_cdet_row_has_text[_cdet_page]
                    && (gcol >= 32 || _cdet_shadow[_cdet_page][gcol] != 0x20)) {
                  if (gcol < 32) _cdet_shadow[_cdet_page][gcol] = 0x20;
                  mp_call_function_1(py_lcd_char_cb, MP_OBJ_NEW_SMALL_INT(code));
                }
              } else {
                /* Non-space: update text context flag.
                   On the first non-space of a row, reset any shadow positions
                   that were set to 0x20 by a prior screen-clear pass, so that
                   subsequent spaces in this text run are not incorrectly
                   suppressed by the dedup. */
                if (!_cdet_row_has_text[_cdet_page]) {
                  for (int sc = 0; sc < 32; sc++) {
                    if (_cdet_shadow[_cdet_page][sc] == 0x20)
                      _cdet_shadow[_cdet_page][sc] = 0x00;
                  }
                  _cdet_row_has_text[_cdet_page] = true;
                }
                /* Shadow dedup: skip if same char was last emitted here */
                if (gcol < 32 && _cdet_shadow[_cdet_page][gcol] == (uint8_t)code) {
                  /* same content refresh – suppress */
                } else {
                  if (gcol < 32) _cdet_shadow[_cdet_page][gcol] = (uint8_t)code;
                  mp_call_function_1(py_lcd_char_cb, MP_OBJ_NEW_SMALL_INT(code));
                }
              }
            }
          }
        } else {
          _cdet_col = 0;
        }
      }
    }
  }
  lcd_write(lcd, data);
  /* Test intercept: log the raw byte */
  if (lcd_write_log_cnt < LCD_INTERCEPT_SIZE)
    lcd_write_log[lcd_write_log_cnt++] = data;
}

static uint8_t c_lcd_direct_read(void *ctx) {
  (void)ctx;
  /* Test intercept: pop from read queue when non-empty */
  if (lcd_read_q_head != lcd_read_q_tail) {
    uint8_t val = lcd_read_queue[lcd_read_q_tail];
    lcd_read_q_tail = (lcd_read_q_tail + 1) & (LCD_INTERCEPT_SIZE - 1);
    return val;
  }
  return lcd_read(lcd_c_get_state());
}

/* ====== Module functions exposed to Python ====== */

/* hd61700.reset([debug]) */
static mp_obj_t mod_reset(size_t n_args, const mp_obj_t *args) {
  if (n_args >= 1) {
    cpu_debug_enabled = mp_obj_is_true(args[0]);
  }
  hd61700_init(&cpu_state);
  hd61700_reset(&cpu_state);
  hd61700_set_debug(&cpu_state, cpu_debug_enabled);
  
  /* Reset UART buffers */
  uart_rx_head = 0;
  uart_rx_tail = 0;
  /* Reset C-port state */
  c_port_read_count = 0;
  c_port_data = 0;
  c_beep_on = false;
  c_beep_post_reset_guard = true;  /* block beep-ON until ROM writes 0xC0 */
  if (c_beep_pin >= 0) {
    pwm_set_chan_level(c_beep_pwm_slice, c_beep_channel, 0); /* silence on reset */
  }
  hd61700_set_key_debug(&cpu_state, cpu_debug_enabled && cpu_key_debug_enabled);
  hd61700_set_lcd_debug(&cpu_state, cpu_debug_enabled && cpu_lcd_debug_enabled);
  memset(sstop_sbot_last, 0, sizeof(sstop_sbot_last));
  memset(sstop_sbot_last_valid, 0, sizeof(sstop_sbot_last_valid));
  /* Fallback auto-detection so C direct-memory mode also works without
     explicit Python-side set_has_exp_ram(). */
  if (!has_bank_forced) {
    detect_all_banks();
  }
  /* Register C callbacks */
  cpu_state.mem_read = c_mem_direct_read;
  cpu_state.mem_write = c_mem_direct_write;
  cpu_state.io_read = c_io_read_wrapper;
  cpu_state.io_write = c_io_write_wrapper;
  cpu_state.lcd_read = c_lcd_direct_read;
  cpu_state.lcd_write = c_lcd_direct_write;
  cpu_state.lcd_ctrl = c_lcd_direct_ctrl;
  cpu_state.kb_read = c_kb_read;
  cpu_state.kb_write = c_kb_write;
  cpu_state.port_read = c_port_read;
  cpu_state.port_write = c_port_write;
  cpu_state.log_write = c_log_write;
  cpu_state.log_ctx = NULL;
  /* Restore call_hook dispatcher — hd61700_init() zeroed the whole struct */
  if (call_hook_count > 0) {
    cpu_state.call_hook = c_call_hook_dispatcher;
    cpu_state.cb_ctx    = NULL;
  }

  /* Connect direct memory pointers for high-performance path */
  {
    static uint8_t * const bank_bufs[3] = {bank1_buf, bank2_buf, bank3_buf};
    cpu_state.rom0_ptr    = rom0_buf;
    cpu_state.ram_ptr     = ram_buf;
    cpu_state.bank_ptr[0] = rom1_buf;
    cpu_state.bank_is_ram[0] = false;
    for (int i = 1; i <= 3; i++) {
      cpu_state.bank_ptr[i]    = has_bank[i] ? bank_bufs[i - 1] : NULL;
      cpu_state.bank_is_ram[i] = true;
    }
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_reset_obj, 0, 1, mod_reset);

/* hd61700.set_debug(enabled) */
static mp_obj_t mod_set_debug(mp_obj_t enabled_obj) {
  cpu_debug_enabled = mp_obj_is_true(enabled_obj);
  hd61700_set_debug(&cpu_state, cpu_debug_enabled);
  hd61700_set_key_debug(&cpu_state, cpu_debug_enabled && cpu_key_debug_enabled);
  hd61700_set_lcd_debug(&cpu_state, cpu_debug_enabled && cpu_lcd_debug_enabled);
  memset(sstop_sbot_last, 0, sizeof(sstop_sbot_last));
  memset(sstop_sbot_last_valid, 0, sizeof(sstop_sbot_last_valid));
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_debug_obj, mod_set_debug);

/* hd61700.set_key_debug(enabled) */
static mp_obj_t mod_set_key_debug(mp_obj_t enabled_obj) {
  cpu_key_debug_enabled = mp_obj_is_true(enabled_obj);
  hd61700_set_key_debug(&cpu_state, cpu_debug_enabled && cpu_key_debug_enabled);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_key_debug_obj, mod_set_key_debug);

/* hd61700.set_lcd_debug(enabled) */
static mp_obj_t mod_set_lcd_debug(mp_obj_t enabled_obj) {
  cpu_lcd_debug_enabled = mp_obj_is_true(enabled_obj);
  hd61700_set_lcd_debug(&cpu_state, cpu_debug_enabled && cpu_lcd_debug_enabled);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_lcd_debug_obj, mod_set_lcd_debug);

/* hd61700.execute(cycles, stop_pc=-1) -> int (cycles consumed) */
static mp_obj_t mod_execute(size_t n_args, const mp_obj_t *args) {
  int cycles = mp_obj_get_int(args[0]);
  int32_t stop_pc = -1;
  if (n_args > 1) {
    stop_pc = (int32_t)mp_obj_get_int(args[1]);
  }

  c_kb_service_input_lines();
  int consumed = hd61700_execute(&cpu_state, cycles, stop_pc);
  return MP_OBJ_NEW_SMALL_INT(consumed);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_execute_obj, 1, 2, mod_execute);

/* hd61700.execute_steps(steps) -> int (cycles consumed) */
static mp_obj_t mod_execute_steps(mp_obj_t steps_obj) {
  int steps = mp_obj_get_int(steps_obj);
  int consumed = hd61700_execute_steps(&cpu_state, steps);
  return MP_OBJ_NEW_SMALL_INT(consumed);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_execute_steps_obj, mod_execute_steps);

/* hd61700.set_mem_callbacks(read_fn, write_fn) */
static mp_obj_t mod_set_mem_callbacks(mp_obj_t read_fn, mp_obj_t write_fn) {
  py_mem_read_cb = read_fn;
  py_mem_write_cb = write_fn;
  anchor_callbacks(read_fn);
  anchor_callbacks(write_fn);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_mem_callbacks_obj,
                                 mod_set_mem_callbacks);

/* hd61700.set_lcd_char_callback(fn)
   Register a Python callable called for each character written to the LCD in
   DRAW_CHAR mode.  Called as fn(code) where code is
   an integer byte value, or fn(None) to signal a row change (newline).
   Pass None to unregister. */
static mp_obj_t mod_set_lcd_char_callback(mp_obj_t cb_obj) {
  if (cb_obj == mp_const_none) {
    py_lcd_char_cb = MP_OBJ_NULL;
  } else {
    py_lcd_char_cb = cb_obj;
    anchor_callbacks(cb_obj);
    c_lcd_char_last_y = -1;
    memset(_cdet_shadow, 0, sizeof(_cdet_shadow));
    memset(_cdet_row_has_text, 0, sizeof(_cdet_row_has_text));
    _cdet_col  = 0;
    _cdet_lpage = -1;
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_lcd_char_callback_obj,
                                  mod_set_lcd_char_callback);

/* hd61700.set_port_callbacks(read_fn, write_fn) */
static mp_obj_t mod_set_port_callbacks(mp_obj_t read_fn, mp_obj_t write_fn) {
  py_port_read_cb = read_fn;
  py_port_write_cb = write_fn;
  anchor_callbacks(read_fn);
  anchor_callbacks(write_fn);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_port_callbacks_obj,
                                 mod_set_port_callbacks);

/* hd61700.set_input(line, state) */
static mp_obj_t mod_set_input(mp_obj_t line_obj, mp_obj_t state_obj) {
  int line = mp_obj_get_int(line_obj);
  int state = mp_obj_get_int(state_obj);
  hd61700_set_input(&cpu_state, line, state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_input_obj, mod_set_input);

/* hd61700.timer_tick() */
static mp_obj_t mod_timer_tick(void) {
  hd61700_timer_tick(&cpu_state);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_timer_tick_obj, mod_timer_tick);

/* hd61700.get_pc() -> int */
static mp_obj_t mod_get_pc(void) { return MP_OBJ_NEW_SMALL_INT(cpu_state.pc); }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_pc_obj, mod_get_pc);

/* hd61700.set_pc(addr) */
static mp_obj_t mod_set_pc(mp_obj_t pc_obj) {
  int pc = mp_obj_get_int(pc_obj) & 0xffff;
  hd61700_set_pc(&cpu_state, (uint16_t)pc);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_pc_obj, mod_set_pc);

/* hd61700.get_flags() -> int */
static mp_obj_t mod_get_flags(void) {
  return MP_OBJ_NEW_SMALL_INT(cpu_state.flags);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_flags_obj, mod_get_flags);

/* hd61700.set_flags(flags) */
static mp_obj_t mod_set_flags(mp_obj_t flags_obj) {
  cpu_state.flags = (uint8_t)mp_obj_get_int(flags_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_flags_obj, mod_set_flags);

/* hd61700.is_sleeping() -> bool */
static mp_obj_t mod_is_sleeping(void) {
  return mp_obj_new_bool(cpu_state.state & CPU_SLP);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_is_sleeping_obj, mod_is_sleeping);

/* hd61700.get_reg(index) -> int (main register) */
static mp_obj_t mod_get_reg(mp_obj_t idx_obj) {
  int idx = mp_obj_get_int(idx_obj) & 0x1f;
  return MP_OBJ_NEW_SMALL_INT(cpu_state.regmain[idx]);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_reg_obj, mod_get_reg);

/* hd61700.set_reg(index, value) */
static mp_obj_t mod_set_reg(mp_obj_t idx_obj, mp_obj_t val_obj) {
  int idx = mp_obj_get_int(idx_obj) & 0x1f;
  cpu_state.regmain[idx] = (uint8_t)mp_obj_get_int(val_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_reg_obj, mod_set_reg);

/* hd61700.get_reg8(index) -> int (8-bit special register)
 * 0=PE, 1=PD, 2=IB, 3=UA, 4=IA, 5=IE, 6=TM, 7=reserved */
static mp_obj_t mod_get_reg8(mp_obj_t idx_obj) {
  int idx = mp_obj_get_int(idx_obj);
  if (idx < 0 || idx > 7) {
    mp_raise_ValueError(MP_ERROR_TEXT("reg8 index must be 0..7"));
  }
  return MP_OBJ_NEW_SMALL_INT(cpu_state.reg8bit[idx]);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_reg8_obj, mod_get_reg8);

/* hd61700.set_reg8(index, value) */
static mp_obj_t mod_set_reg8(mp_obj_t idx_obj, mp_obj_t val_obj) {
  int idx = mp_obj_get_int(idx_obj);
  if (idx < 0 || idx > 7) {
    mp_raise_ValueError(MP_ERROR_TEXT("reg8 index must be 0..7"));
  }
  cpu_state.reg8bit[idx] = (uint8_t)mp_obj_get_int(val_obj);
  if (idx == 3) {
    /* Keep fetch bank source in sync when UA is changed via API. */
    cpu_state.prev_ua = cpu_state.reg8bit[3];
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_reg8_obj, mod_set_reg8);

/* hd61700.get_reg16(name) -> int
 * Names: 0=IX, 1=IY, 2=IZ, 3=US, 4=SS, 5=KY */
static mp_obj_t mod_get_reg16(mp_obj_t idx_obj) {
  int idx = mp_obj_get_int(idx_obj) & 0x07;
  return MP_OBJ_NEW_SMALL_INT(cpu_state.reg16bit[idx]);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_reg16_obj, mod_get_reg16);

/* hd61700.set_reg16(index, value) */
static mp_obj_t mod_set_reg16(mp_obj_t idx_obj, mp_obj_t val_obj) {
  int idx = mp_obj_get_int(idx_obj) & 0x07;
  cpu_state.reg16bit[idx] = (uint16_t)mp_obj_get_int(val_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_reg16_obj, mod_set_reg16);

/* hd61700.get_sreg(index) -> int
 * Names: 0=SX, 1=SY, 2=SZ */
static mp_obj_t mod_get_sreg(mp_obj_t idx_obj) {
  int idx = mp_obj_get_int(idx_obj);
  if (idx < 0 || idx > 2) {
    mp_raise_ValueError(MP_ERROR_TEXT("sreg index must be 0..2"));
  }
  return MP_OBJ_NEW_SMALL_INT(cpu_state.regsir[idx] & 0x1f);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_sreg_obj, mod_get_sreg);

/* hd61700.set_sreg(index, value) */
static mp_obj_t mod_set_sreg(mp_obj_t idx_obj, mp_obj_t val_obj) {
  int idx = mp_obj_get_int(idx_obj);
  if (idx < 0 || idx > 2) {
    mp_raise_ValueError(MP_ERROR_TEXT("sreg index must be 0..2"));
  }
  cpu_state.regsir[idx] = (uint8_t)mp_obj_get_int(val_obj) & 0x1f;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_sreg_obj, mod_set_sreg);

/* hd61700.set_registers(bytes) */
static mp_obj_t mod_set_registers(mp_obj_t buf_obj) {
  mp_buffer_info_t buf;
  mp_get_buffer_raise(buf_obj, &buf, MP_BUFFER_READ);
  if (buf.len < 36) {
    mp_raise_ValueError(
        MP_ERROR_TEXT("register dump must be at least 36 bytes"));
  }
  memcpy(cpu_state.regmain, buf.buf, 32);
  uint8_t *data = buf.buf;
  cpu_state.reg16bit[4] = (uint16_t)data[32] | ((uint16_t)data[33] << 8);
  cpu_state.reg16bit[3] = (uint16_t)data[34] | ((uint16_t)data[35] << 8);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_registers_obj, mod_set_registers);

// hd61700.step() -> 陞ｳ貅ｯ・｡蠕鯉ｼ邵ｺ貅ｷ螟夊脂・､邵ｺ・ｮ bytes 郢ｧ・ｪ郢晄じ縺夂ｹｧ・ｧ郢ｧ・ｯ郢晏現・帝恆譁絶・
static mp_obj_t mod_step(void) {
  hd61700_step(&cpu_state);

  // 陞ｳ貅ｯ・｡蠕鯉ｼ邵ｺ貅倥Σ郢ｧ・､郢昜ｺ･繝ｻ郢ｧ繝ｻMicroPython 邵ｺ・ｮ bytes 陜吩ｹ昶・邵ｺ蜉ｱ窶ｻ髴第鱒笘・
  return mp_obj_new_bytes(cpu_state.last_opcodes, cpu_state.last_op_len);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_step_obj, mod_step);

/* hd61700.load_rom(slot, data) */
static mp_obj_t mod_load_rom(mp_obj_t slot_obj, mp_obj_t data_obj) {
  int slot = mp_obj_get_int(slot_obj);
  mp_buffer_info_t bufinfo;
  mp_get_buffer_raise(data_obj, &bufinfo, MP_BUFFER_READ);

  if (slot == 0) {
    rom0_size = (bufinfo.len > 0x2000) ? 0x2000 : bufinfo.len;
    memcpy(rom0_buf, bufinfo.buf, rom0_size);
  } else {
    rom1_size = (bufinfo.len > 0x8000) ? 0x8000 : bufinfo.len;
    memcpy(rom1_buf, bufinfo.buf, rom1_size);
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_load_rom_obj, mod_load_rom);

/* hd61700.load_ram(slot, data)  slot: 0=main RAM, 1..3=bank RAM */
static mp_obj_t mod_load_ram(mp_obj_t slot_obj, mp_obj_t data_obj) {
  int slot = mp_obj_get_int(slot_obj);
  mp_buffer_info_t bufinfo;
  mp_get_buffer_raise(data_obj, &bufinfo, MP_BUFFER_READ);

  uint8_t *dst = NULL;
  size_t   cap = 0;
  if (slot == 0) {
    dst = ram_buf;   cap = sizeof(ram_buf);
  } else if (slot == 1) {
    dst = bank1_buf; cap = sizeof(bank1_buf);
  } else if (slot == 2) {
    dst = bank2_buf; cap = sizeof(bank2_buf);
  } else if (slot == 3) {
    dst = bank3_buf; cap = sizeof(bank3_buf);
  } else {
    return mp_const_none; /* unknown slot */
  }

  size_t to_copy = (bufinfo.len < cap) ? bufinfo.len : cap;
  memcpy(dst, bufinfo.buf, to_copy);

  if (slot >= 1 && slot <= 3) {
    has_bank[slot] = true;
    /* Update bank_ptr immediately so subsequent reads without reset() also work */
    static uint8_t * const bank_bufs[3] = {bank1_buf, bank2_buf, bank3_buf};
    cpu_state.bank_ptr[slot] = bank_bufs[slot - 1];
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_load_ram_obj, mod_load_ram);

/* hd61700.get_ram_view() */
static mp_obj_t mod_get_ram_view(void) {
  return mp_obj_new_bytearray_by_ref(sizeof(ram_buf), ram_buf);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_ram_view_obj, mod_get_ram_view);

/* hd61700.get_exp_ram_view()  — backward-compatible alias for get_bank_view(1) */
static mp_obj_t mod_get_exp_ram_view(void) {
  return mp_obj_new_bytearray_by_ref(sizeof(bank1_buf), bank1_buf);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_exp_ram_view_obj, mod_get_exp_ram_view);

/* hd61700.get_bank_view(bank)  bank: 1..3 */
static mp_obj_t mod_get_bank_view(mp_obj_t bank_obj) {
  int bank = mp_obj_get_int(bank_obj);
  uint8_t *ptr = NULL;
  size_t   sz  = 0;
  if      (bank == 1) { ptr = bank1_buf; sz = sizeof(bank1_buf); }
  else if (bank == 2) { ptr = bank2_buf; sz = sizeof(bank2_buf); }
  else if (bank == 3) { ptr = bank3_buf; sz = sizeof(bank3_buf); }
  else return mp_const_none;
  return mp_obj_new_bytearray_by_ref(sz, ptr);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_bank_view_obj, mod_get_bank_view);

/* hd61700.get_ext_work_view() — returns writable bytearray backed by ext_work_buf */
static mp_obj_t mod_get_ext_work_view(void) {
  return mp_obj_new_bytearray_by_ref(sizeof(ext_work_buf), ext_work_buf);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_ext_work_view_obj, mod_get_ext_work_view);

/* hd61700.lcd_get_write_log() — returns bytearray of bytes written to LCD since last clear */
static mp_obj_t mod_lcd_get_write_log(void) {
  return mp_obj_new_bytes(lcd_write_log, lcd_write_log_cnt);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_get_write_log_obj, mod_lcd_get_write_log);

/* hd61700.lcd_clear_write_log() */
static mp_obj_t mod_lcd_clear_write_log(void) {
  lcd_write_log_cnt = 0;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_clear_write_log_obj, mod_lcd_clear_write_log);

/* hd61700.lcd_push_read(byte) — push a byte to the read queue (consumed by LDL etc.) */
static mp_obj_t mod_lcd_push_read(mp_obj_t byte_obj) {
  uint8_t next = (lcd_read_q_head + 1) & (LCD_INTERCEPT_SIZE - 1);
  if (next != lcd_read_q_tail) {
    lcd_read_queue[lcd_read_q_head] = (uint8_t)mp_obj_get_int(byte_obj);
    lcd_read_q_head = next;
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_lcd_push_read_obj, mod_lcd_push_read);

/* hd61700.lcd_clear_read_queue() */
static mp_obj_t mod_lcd_clear_read_queue(void) {
  lcd_read_q_head = 0;
  lcd_read_q_tail = 0;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_clear_read_queue_obj, mod_lcd_clear_read_queue);

/* hd61700.set_has_exp_ram(bool)  — sets Bank 1 presence flag (backward compat) */
static mp_obj_t mod_set_has_exp_ram(mp_obj_t enable_obj) {
  has_bank[1]    = mp_obj_is_true(enable_obj);
  has_bank_forced = true;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_has_exp_ram_obj, mod_set_has_exp_ram);

/* ---- CAL hook dispatcher ---- */

/* Called from hd61700.c CAL handler.
 * Returns true if address is registered (intercept), false otherwise. */
static bool c_call_hook_dispatcher(void *ctx, uint16_t addr) {
  (void)ctx;
  for (int i = 0; i < call_hook_count; i++) {
    if (call_hook_addrs[i] == addr && call_hook_enabled[i]) {
      mp_call_function_0(call_hook_fns[i]);
      return true;
    }
  }
  return false;
}

/* hd61700.set_call_hook(address, callable)
 * Register a Python function or native C function for the given CAL address.
 * Overwrites any existing entry for the same address. */
static mp_obj_t mod_set_call_hook(mp_obj_t addr_obj, mp_obj_t fn_obj) {
  uint16_t addr = (uint16_t)mp_obj_get_int(addr_obj);
  /* Overwrite existing entry */
  for (int i = 0; i < call_hook_count; i++) {
    if (call_hook_addrs[i] == addr) {
      call_hook_fns[i] = fn_obj;
      return mp_const_none;
    }
  }
  /* New entry */
  if (call_hook_count < CALL_HOOK_MAX) {
    call_hook_addrs[call_hook_count]   = addr;
    call_hook_fns[call_hook_count]     = fn_obj;
    call_hook_enabled[call_hook_count] = true;
    call_hook_count++;
  }
  cpu_state.call_hook = c_call_hook_dispatcher;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_call_hook_obj, mod_set_call_hook);

/* hd61700.clear_call_hook(address)
 * Unregister the hook for the given address. */
static mp_obj_t mod_clear_call_hook(mp_obj_t addr_obj) {
  uint16_t addr = (uint16_t)mp_obj_get_int(addr_obj);
  for (int i = 0; i < call_hook_count; i++) {
    if (call_hook_addrs[i] == addr) {
      /* Fill hole with last entry */
      call_hook_count--;
      call_hook_addrs[i]   = call_hook_addrs[call_hook_count];
      call_hook_fns[i]     = call_hook_fns[call_hook_count];
      call_hook_enabled[i] = call_hook_enabled[call_hook_count];
      call_hook_fns[call_hook_count] = MP_OBJ_NULL; /* release GC ref */
      break;
    }
  }
  if (call_hook_count == 0) {
    cpu_state.call_hook = NULL;
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_clear_call_hook_obj, mod_clear_call_hook);

/* hd61700.set_call_hook_enabled(address, enabled)
 * Enable or disable the hook for the given address without unregistering it.
 * The callable is preserved; pass enabled=False to suppress firing. */
static mp_obj_t mod_set_call_hook_enabled(mp_obj_t addr_obj, mp_obj_t enabled_obj) {
  uint16_t addr = (uint16_t)mp_obj_get_int(addr_obj);
  bool enabled = mp_obj_is_true(enabled_obj);
  for (int i = 0; i < call_hook_count; i++) {
    if (call_hook_addrs[i] == addr) {
      call_hook_enabled[i] = enabled;
      return mp_const_none;
    }
  }
  return mp_const_none;  /* address not registered — silently ignore */
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_call_hook_enabled_obj, mod_set_call_hook_enabled);

/* hd61700.read_mem(addr, [segment]) */
static mp_obj_t mod_read_mem(size_t n_args, const mp_obj_t *args) {
  uint32_t addr = (uint32_t)mp_obj_get_int(args[0]);
  uint8_t segment = (n_args > 1) ? (uint8_t)mp_obj_get_int(args[1]) : 0;
  uint8_t bank = normalize_bank(segment);
  uint8_t data = c_mem_direct_read(NULL, bank, addr);
  return MP_OBJ_NEW_SMALL_INT(data);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_read_mem_obj, 1, 2,
                                           mod_read_mem);

/* hd61700.write_mem(addr, data, [segment]) */
static mp_obj_t mod_write_mem(size_t n_args, const mp_obj_t *args) {
  uint32_t addr = (uint32_t)mp_obj_get_int(args[0]);
  uint8_t data = (uint8_t)mp_obj_get_int(args[1]);
  uint8_t segment = (n_args > 2) ? (uint8_t)mp_obj_get_int(args[2]) : 0;
  uint8_t bank = normalize_bank(segment);
  c_mem_direct_write(NULL, bank, addr, data);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_write_mem_obj, 2, 3,
                                           mod_write_mem);

/* hd61700._anchor_callbacks(obj) - internal use to prevent GC */
static mp_obj_t mod_anchor_callbacks(mp_obj_t obj) {
  anchor_callbacks(obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_anchor_callbacks_obj,
                                 mod_anchor_callbacks);

// Internal function to ensure anchor list is known to GC
static mp_obj_t mod_init_anchor(void) {
  if (py_callback_anchor_list == mp_const_none) {
    py_callback_anchor_list = mp_obj_new_list(0, NULL);
  }
  return py_callback_anchor_list;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_init_anchor_obj, mod_init_anchor);

/* hd61700.set_f11_callback(fn) */
static mp_obj_t mod_set_f11_callback(mp_obj_t fn_obj) {
  py_f11_callback = fn_obj;
  anchor_callbacks(fn_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_f11_callback_obj, mod_set_f11_callback);

/* hd61700.set_f9_callback(fn) */
static mp_obj_t mod_set_f9_callback(mp_obj_t fn_obj) {
  py_f9_callback = fn_obj;
  anchor_callbacks(fn_obj);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_f9_callback_obj, mod_set_f9_callback);

/* hd61700.uart_tx_get() -> int or None */
static mp_obj_t mod_uart_tx_get(void) {
  if (uart_tx_head != uart_tx_tail) {
    return MP_OBJ_NEW_SMALL_INT(uart_tx_fifo[uart_tx_tail++]);
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_uart_tx_get_obj, mod_uart_tx_get);

/* hd61700.set_uart_tx_callback(fn) - Kept for compatibility */
static mp_obj_t mod_set_uart_tx_callback(mp_obj_t fn_obj) {
  (void)fn_obj;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_uart_tx_callback_obj, mod_set_uart_tx_callback);

/* hd61700.uart_rx_put(byte) */
static mp_obj_t mod_uart_rx_put(mp_obj_t byte_obj) {
  uint8_t b = (uint8_t)mp_obj_get_int(byte_obj);
  uart_rx_fifo[uart_rx_head++] = b;
  /* Assert INT1 to notify BIOS of incoming data */
  hd61700_set_input(&cpu_state, HD61700_INT1, 1);
  cpu_state.reg8bit[2] |= (1 << HD61700_INT1); /* Force REG_IB for robustness */
  cpu_state.state &= ~CPU_SLP;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_uart_rx_put_obj, mod_uart_rx_put);

/* hd61700.uart_rx_any() -> int: bytes pending in C UART RX FIFO */
static mp_obj_t mod_uart_rx_any(void) {
  return MP_OBJ_NEW_SMALL_INT((uint8_t)(uart_rx_head - uart_rx_tail));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_uart_rx_any_obj, mod_uart_rx_any);

/* hd61700.uart_signal_rx(): Assert INT1 to wake CPU when Python PIO buffer
   has data. Does NOT store data in C FIFO — bytes remain in Python buffer
   and are served by the Python MMIO callback for 0x0C02 (IO read path). */
static mp_obj_t mod_uart_signal_rx(void) {
  hd61700_set_input(&cpu_state, HD61700_INT1, 1);
  cpu_state.reg8bit[2] |= (1 << HD61700_INT1);
  cpu_state.state &= ~CPU_SLP;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_uart_signal_rx_obj, mod_uart_signal_rx);

/* hd61700.uart_clear_rx_signal(): Deassert INT1 when Python PIO buffer
   has been fully consumed. Called from _read_io_register(2) after the
   last byte is read and the Python buffer is empty. */
static mp_obj_t mod_uart_clear_rx_signal(void) {
  hd61700_set_input(&cpu_state, HD61700_INT1, 0);
  cpu_state.irq_status &= ~(1 << HD61700_INT1);
  cpu_state.reg8bit[2] &= ~(1 << HD61700_INT1);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_uart_clear_rx_signal_obj, mod_uart_clear_rx_signal);

/* hd61700.set_kb_pulse_interval_ms(ms) */
static mp_obj_t mod_set_kb_pulse_interval_ms(mp_obj_t ms_obj) {
  int ms = mp_obj_get_int(ms_obj);
  if (ms < 1) ms = 1;
  if (ms > 2000) ms = 2000;
  c_kb_pulse_interval_ms = (uint32_t)ms;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_kb_pulse_interval_ms_obj,
                                 mod_set_kb_pulse_interval_ms);

/* hd61700.process_usb_key(scancode, pressed) - manual C keyboard event injection */
static mp_obj_t mod_process_usb_key(mp_obj_t sc_obj, mp_obj_t pressed_obj) {
  uint8_t scancode = (uint8_t)mp_obj_get_int(sc_obj);
  bool pressed = mp_obj_is_true(pressed_obj);
  c_kb_process_usb_key(scancode, pressed);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_process_usb_key_obj, mod_process_usb_key);

/* hd61700.keyboard_config_adv([(scancode, mod, [(row,ki), ...]), ...]) */
static mp_obj_t mod_keyboard_config_adv(mp_obj_t list_obj) {
  size_t len;
  mp_obj_t *items;
  mp_obj_get_array(list_obj, &len, &items);

  dynamic_adv_map_count = 0;
  for (size_t i = 0; i < len && i < MAX_ADV_MAP_ENTRIES; i++) {
    size_t inner_len;
    mp_obj_t *inner_items;
    mp_obj_get_array(items[i], &inner_len, &inner_items);
    if (inner_len < 3) continue;

    adv_usb_map_t *entry = &dynamic_adv_map[dynamic_adv_map_count++];
    entry->scancode = (uint8_t)mp_obj_get_int(inner_items[0]);
    entry->host_mod = (uint8_t)mp_obj_get_int(inner_items[1]);
    
    size_t coord_len;
    mp_obj_t *coord_items;
    mp_obj_get_array(inner_items[2], &coord_len, &coord_items);
    
    int j = 0;
    for (; j < (int)coord_len && j < 4; j++) {
      size_t pair_len;
      mp_obj_t *pair_items;
      mp_obj_get_array(coord_items[j], &pair_len, &pair_items);
      if (pair_len < 2) continue;
      entry->coords[j][0] = (uint8_t)mp_obj_get_int(pair_items[0]);
      entry->coords[j][1] = (uint8_t)mp_obj_get_int(pair_items[1]);
    }
    if (j < 4) entry->coords[j][0] = 0xFF; /* Terminate */
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_keyboard_config_adv_obj, mod_keyboard_config_adv);

/* hd61700.keyboard_config_base([(scancode, row, ki), ...]) */
static mp_obj_t mod_keyboard_config_base(mp_obj_t list_obj) {
  size_t len;
  mp_obj_t *items;
  mp_obj_get_array(list_obj, &len, &items);

  dynamic_base_map_count = 0;
  for (size_t i = 0; i < len && i < MAX_BASE_MAP_ENTRIES; i++) {
    size_t inner_len;
    mp_obj_t *inner_items;
    mp_obj_get_array(items[i], &inner_len, &inner_items);
    if (inner_len < 3) continue;

    base_usb_key_t *entry = &dynamic_base_map[dynamic_base_map_count++];
    entry->scancode = (uint8_t)mp_obj_get_int(inner_items[0]);
    entry->row = (uint8_t)mp_obj_get_int(inner_items[1]);
    entry->ki = (uint8_t)mp_obj_get_int(inner_items[2]);
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_keyboard_config_base_obj, mod_keyboard_config_base);

// module.get_last_key() -> int (-1 if none, read-and-clear)
static mp_obj_t mod_get_last_key(void) {
  int16_t sc = c_kb_last_pressed_scancode;
  c_kb_last_pressed_scancode = -1;
  return MP_OBJ_NEW_SMALL_INT(sc);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_last_key_obj, mod_get_last_key);

// module.get_held_cursor_key() -> int (0 if none, else scancode of held cursor key)
static mp_obj_t mod_get_held_cursor_key(void) {
  return MP_OBJ_NEW_SMALL_INT(c_kb_held_cursor);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_held_cursor_key_obj, mod_get_held_cursor_key);

/* hd61700.steer_next_key_int(row)
 * Steer the next KEY_INT pulse to fire for the given keyboard row.
 * Used by cursor-key repeat: after a synthetic press_row_ki(), the ROM's IA
 * scan sequence may be pointing at a different row, so KY would return 0 and
 * the ROM would miss the key.  This call:
 *   1. Updates c_kb_ia_select / IA register so the ROM scans the right row.
 *   2. Resets c_kb_next_pulse_ms to 0 so KEY_INT fires on the next
 *      c_kb_service_input_lines() call without waiting for the 25 ms interval.
 * The mode bit (IA bit 7) is preserved; only the row-select bits are changed.
 * When ia_select is already in all-rows mode (0x0D) the row is still forced so
 * the ROM reads KY for exactly the cursor key's row and identifies it correctly.
 */

static mp_obj_t mod_steer_next_key_int(mp_obj_t row_obj) {
  int row = mp_obj_get_int(row_obj);
  if (row < 0 || row > 12) return mp_const_none;
  c_kb_ia_select = (c_kb_ia_select & 0x80) | (uint8_t)(row & 0x0F);
  cpu_state.reg8bit[4] = c_kb_ia_select;
  c_kb_next_pulse_ms = 0;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_steer_next_key_int_obj, mod_steer_next_key_int);

/* hd61700.press_row_ki(row, ki) */
static mp_obj_t mod_press_row_ki(mp_obj_t row_obj, mp_obj_t ki_obj) {
  int row = mp_obj_get_int(row_obj);
  int ki = mp_obj_get_int(ki_obj);
  c_kb_press(row, ki);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_press_row_ki_obj, mod_press_row_ki);

/* hd61700.release_row_ki(row, ki) */
static mp_obj_t mod_release_row_ki(mp_obj_t row_obj, mp_obj_t ki_obj) {
  int row = mp_obj_get_int(row_obj);
  int ki = mp_obj_get_int(ki_obj);
  c_kb_release(row, ki);
  /* Ensure KEY_INT fires during the release gap even in IA scan-filtered mode,
     so the ROM sees the key-up before the synthetic re-press. */
  if (!c_kb_has_key_pressed()) {
    c_kb_post_release_pulses_remaining = C_KB_POST_RELEASE_PULSES_MAX;
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_release_row_ki_obj, mod_release_row_ki);


/* hd61700.lcd_sync() */
static mp_obj_t mod_lcd_sync(void) {
    lcd_wait_for_idle(lcd_c_get_state());
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lcd_sync_obj, mod_lcd_sync);


/* hd61700.set_io_callbacks(read_fn, write_fn) */
static mp_obj_t mod_set_io_callbacks(mp_obj_t read_fn, mp_obj_t write_fn) {
    if (read_fn != mp_const_none && !mp_obj_is_callable(read_fn)) {
        mp_raise_TypeError(MP_ERROR_TEXT("read_fn must be callable"));
    }
    if (write_fn != mp_const_none && !mp_obj_is_callable(write_fn)) {
        mp_raise_TypeError(MP_ERROR_TEXT("write_fn must be callable"));
    }

    py_io_read_callback = read_fn;
    py_io_write_callback = write_fn;
    
    if (read_fn != mp_const_none) {
        anchor_callbacks(read_fn);
        cpu_state.io_read = c_io_read_wrapper;
    } else {
        cpu_state.io_read = NULL;
    }
    
    if (write_fn != mp_const_none) {
        anchor_callbacks(write_fn);
        cpu_state.io_write = c_io_write_wrapper;
    } else {
        cpu_state.io_write = NULL;
    }
    
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_io_callbacks_obj, mod_set_io_callbacks);


/* hd61700.set_port_direct(tx_pin, rx_pin, beep_pin [, freq_hz [, duty_pct]])
 * Configure RP2350 GPIO and PWM for C-side port_read/write.
 * beep_pin = -1 disables BEEP.  Only re-initialises TX/RX GPIO when the pin
 * numbers actually change — prevents clobbering PIO ownership after the first
 * call (e.g. when called again from the menu just to toggle the beep pin). */
static mp_obj_t mod_set_port_direct(size_t n_args, const mp_obj_t *args) {
  int new_tx   = mp_obj_get_int(args[0]);
  int new_rx   = mp_obj_get_int(args[1]);
  c_beep_pin   = mp_obj_get_int(args[2]);
  int freq_hz  = (n_args > 3) ? mp_obj_get_int(args[3]) : 1000;
  int duty_pct = (n_args > 4) ? mp_obj_get_int(args[4]) : 50;

  /* TX: output, idle HIGH — only (re)init when pin changes */
  if (new_tx >= 0 && new_tx != c_tx_pin) {
    c_tx_pin = new_tx;
    gpio_init((uint)c_tx_pin);
    gpio_set_dir((uint)c_tx_pin, GPIO_OUT);
    gpio_put((uint)c_tx_pin, 1u);
  }

  /* RX: input with pull-up — only (re)init when pin changes */
  if (new_rx >= 0 && new_rx != c_rx_pin) {
    c_rx_pin = new_rx;
    gpio_init((uint)c_rx_pin);
    gpio_set_dir((uint)c_rx_pin, GPIO_IN);
    gpio_pull_up((uint)c_rx_pin);
  }

  if (c_beep_pin >= 0) {
    gpio_set_function((uint)c_beep_pin, GPIO_FUNC_PWM);
    uint slice   = pwm_gpio_to_slice_num((uint)c_beep_pin);
    uint channel = pwm_gpio_to_channel((uint)c_beep_pin);
    c_beep_pwm_slice = slice;
    c_beep_channel   = channel;

    /* clkdiv = sys_clk / (freq_hz * 65536)  [16-bit wrap maximises duty resolution] */
    uint32_t sys_hz = clock_get_hz(clk_sys);
    float clkdiv = (float)sys_hz / ((float)freq_hz * 65536.0f);
    if (clkdiv < 1.0f) clkdiv = 1.0f;
    pwm_set_clkdiv(slice, clkdiv);
    pwm_set_wrap(slice, 65535);

    if (duty_pct < 0)   duty_pct = 0;
    if (duty_pct > 100) duty_pct = 100;
    c_beep_duty = (uint32_t)duty_pct * 65535u / 100u;

    pwm_set_chan_level(slice, channel, 0u); /* start silent */
    pwm_set_enabled(slice, true);
    c_beep_on = false;

    mp_printf(&mp_plat_print,
              "[PORT] BEEP: GP%d PWM slice=%u ch=%u freq=%dHz duty=%d%%\n",
              c_beep_pin, (unsigned)slice, (unsigned)channel,
              freq_hz, duty_pct);
  }
  mp_printf(&mp_plat_print,
            "[PORT] Direct C: TX=GP%d RX=GP%d BEEP=%s\n",
            new_tx, new_rx,
            (c_beep_pin >= 0) ? "enabled" : "disabled");
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_set_port_direct_obj, 3, 5,
                                           mod_set_port_direct);

/* hd61700.get_port_data() -> int — last port byte written by the emulated CPU */
static mp_obj_t mod_get_port_data(void) {
  return MP_OBJ_NEW_SMALL_INT(c_port_data);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_port_data_obj, mod_get_port_data);

/* hd61700.set_vfdd_data(val) */
static mp_obj_t mod_set_vfdd_data(mp_obj_t val_obj) {
    vfdd_data_reg = (uint8_t)mp_obj_get_int(val_obj);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_vfdd_data_obj, mod_set_vfdd_data);


/* hd61700.get_vfdd_write_data() -> int */
static mp_obj_t mod_get_vfdd_write_data(void) {
    return MP_OBJ_NEW_SMALL_INT(vfdd_write_reg);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_vfdd_write_data_obj, mod_get_vfdd_write_data);


static mp_obj_t mod_build_time(void) {
  return mp_obj_new_str(__DATE__ " " __TIME__, sizeof(__DATE__ " " __TIME__) - 1);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_build_time_obj, mod_build_time);

/* ====== Module definition ====== */
static const mp_rom_map_elem_t hd61700_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_hd61700)},
    {MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&mod_reset_obj)},
    {MP_ROM_QSTR(MP_QSTR_execute), MP_ROM_PTR(&mod_execute_obj)},
    {MP_ROM_QSTR(MP_QSTR_execute_steps), MP_ROM_PTR(&mod_execute_steps_obj)},
    {MP_ROM_QSTR(MP_QSTR_lcd_sync), MP_ROM_PTR(&mod_lcd_sync_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_debug), MP_ROM_PTR(&mod_set_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_key_debug), MP_ROM_PTR(&mod_set_key_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_lcd_debug), MP_ROM_PTR(&mod_set_lcd_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_mem_callbacks),
     MP_ROM_PTR(&mod_set_mem_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_lcd_char_callback),
     MP_ROM_PTR(&mod_set_lcd_char_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_port_callbacks),
     MP_ROM_PTR(&mod_set_port_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_vfdd_data),
     MP_ROM_PTR(&mod_set_vfdd_data_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_vfdd_write_data),
     MP_ROM_PTR(&mod_get_vfdd_write_data_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_io_callbacks),
     MP_ROM_PTR(&mod_set_io_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_input), MP_ROM_PTR(&mod_set_input_obj)},
    {MP_ROM_QSTR(MP_QSTR_timer_tick), MP_ROM_PTR(&mod_timer_tick_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_pc), MP_ROM_PTR(&mod_get_pc_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_pc), MP_ROM_PTR(&mod_set_pc_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_flags), MP_ROM_PTR(&mod_get_flags_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_flags), MP_ROM_PTR(&mod_set_flags_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_sleeping), MP_ROM_PTR(&mod_is_sleeping_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_reg), MP_ROM_PTR(&mod_get_reg_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_reg8), MP_ROM_PTR(&mod_get_reg8_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_reg16), MP_ROM_PTR(&mod_get_reg16_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_sreg), MP_ROM_PTR(&mod_get_sreg_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_reg), MP_ROM_PTR(&mod_set_reg_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_reg8), MP_ROM_PTR(&mod_set_reg8_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_reg16), MP_ROM_PTR(&mod_set_reg16_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_sreg), MP_ROM_PTR(&mod_set_sreg_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_registers), MP_ROM_PTR(&mod_set_registers_obj)},
    /* 郢ｧ・ｹ郢昴・繝｣郢晄懶ｽｮ貅ｯ・｡讙守舞 */
    {MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&mod_step_obj)},
    /* Optimization APIs */
    {MP_ROM_QSTR(MP_QSTR_load_rom), MP_ROM_PTR(&mod_load_rom_obj)},
    {MP_ROM_QSTR(MP_QSTR_load_ram), MP_ROM_PTR(&mod_load_ram_obj)},
    /* LCD test intercept APIs */
    {MP_ROM_QSTR(MP_QSTR_lcd_get_write_log),
     MP_ROM_PTR(&mod_lcd_get_write_log_obj)},
    {MP_ROM_QSTR(MP_QSTR_lcd_clear_write_log),
     MP_ROM_PTR(&mod_lcd_clear_write_log_obj)},
    {MP_ROM_QSTR(MP_QSTR_lcd_push_read),
     MP_ROM_PTR(&mod_lcd_push_read_obj)},
    {MP_ROM_QSTR(MP_QSTR_lcd_clear_read_queue),
     MP_ROM_PTR(&mod_lcd_clear_read_queue_obj)},
    /* C Port APIs */
    {MP_ROM_QSTR(MP_QSTR_set_port_direct), MP_ROM_PTR(&mod_set_port_direct_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_port_data), MP_ROM_PTR(&mod_get_port_data_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_ram_view), MP_ROM_PTR(&mod_get_ram_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_exp_ram_view),
     MP_ROM_PTR(&mod_get_exp_ram_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_bank_view),
     MP_ROM_PTR(&mod_get_bank_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_ext_work_view),
     MP_ROM_PTR(&mod_get_ext_work_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_has_exp_ram),
     MP_ROM_PTR(&mod_set_has_exp_ram_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_call_hook),
     MP_ROM_PTR(&mod_set_call_hook_obj)},
    {MP_ROM_QSTR(MP_QSTR_clear_call_hook),
     MP_ROM_PTR(&mod_clear_call_hook_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_call_hook_enabled),
     MP_ROM_PTR(&mod_set_call_hook_enabled_obj)},
    {MP_ROM_QSTR(MP_QSTR_read_mem), MP_ROM_PTR(&mod_read_mem_obj)},
    {MP_ROM_QSTR(MP_QSTR_write_mem), MP_ROM_PTR(&mod_write_mem_obj)},
    {MP_ROM_QSTR(MP_QSTR__anchor_callbacks),
     MP_ROM_PTR(&mod_anchor_callbacks_obj)},
    /* Constants */
    {MP_ROM_QSTR(MP_QSTR_ON_INT), MP_ROM_INT(HD61700_ON_INT)},
    {MP_ROM_QSTR(MP_QSTR_TIMER_INT), MP_ROM_INT(HD61700_TIMER_INT)},
    {MP_ROM_QSTR(MP_QSTR_INT2), MP_ROM_INT(HD61700_INT2)},
    {MP_ROM_QSTR(MP_QSTR_KEY_INT), MP_ROM_INT(HD61700_KEY_INT)},
    {MP_ROM_QSTR(MP_QSTR_INT1), MP_ROM_INT(HD61700_INT1)},
    {MP_ROM_QSTR(MP_QSTR_SW), MP_ROM_INT(HD61700_SW)},
    {MP_ROM_QSTR(MP_QSTR__init_anchor), MP_ROM_PTR(&mod_init_anchor_obj)},
    /* C Keyboard APIs */
    {MP_ROM_QSTR(MP_QSTR_set_f11_callback),
     MP_ROM_PTR(&mod_set_f11_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_f9_callback),
     MP_ROM_PTR(&mod_set_f9_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_tx_get), MP_ROM_PTR(&mod_uart_tx_get_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_uart_tx_callback), MP_ROM_PTR(&mod_set_uart_tx_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_rx_put),
     MP_ROM_PTR(&mod_uart_rx_put_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_rx_any),
     MP_ROM_PTR(&mod_uart_rx_any_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_signal_rx),
     MP_ROM_PTR(&mod_uart_signal_rx_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_clear_rx_signal),
     MP_ROM_PTR(&mod_uart_clear_rx_signal_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_kb_pulse_interval_ms),
     MP_ROM_PTR(&mod_set_kb_pulse_interval_ms_obj)},
    {MP_ROM_QSTR(MP_QSTR_process_usb_key),
     MP_ROM_PTR(&mod_process_usb_key_obj)},
    {MP_ROM_QSTR(MP_QSTR_keyboard_config_adv),
     MP_ROM_PTR(&mod_keyboard_config_adv_obj)},
    {MP_ROM_QSTR(MP_QSTR_keyboard_config_base),
     MP_ROM_PTR(&mod_keyboard_config_base_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_last_key),
     MP_ROM_PTR(&mod_get_last_key_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_held_cursor_key),
     MP_ROM_PTR(&mod_get_held_cursor_key_obj)},
    {MP_ROM_QSTR(MP_QSTR_steer_next_key_int),
     MP_ROM_PTR(&mod_steer_next_key_int_obj)},
    {MP_ROM_QSTR(MP_QSTR_press_row_ki),
     MP_ROM_PTR(&mod_press_row_ki_obj)},
    {MP_ROM_QSTR(MP_QSTR_release_row_ki),
     MP_ROM_PTR(&mod_release_row_ki_obj)},
    {MP_ROM_QSTR(MP_QSTR_build_time), MP_ROM_PTR(&mod_build_time_obj)},
};
static MP_DEFINE_CONST_DICT(hd61700_module_globals,
                            hd61700_module_globals_table);

const mp_obj_module_t hd61700_user_cmodule = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&hd61700_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_hd61700, hd61700_user_cmodule);

// Since we can't easily run code on module load in usermod without a custom
// init, we'll add this to the table and call it from Python.
// Actually, we can add it to the globals and the user will call it.











