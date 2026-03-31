/*
 * MicroPython C Module wrapper for HD61700 CPU
 * Exposes HD61700 CPU core to MicroPython as 'hd61700' module
 */
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include "hd61700.h"
#include "lcd_controller.h"
#include "py/binary.h"
#include "py/mphal.h"
#include "py/obj.h"
#include "py/runtime.h"

/* Static CPU state */
static hd61700_state_t cpu_state;
static bool cpu_debug_enabled = false;
static bool cpu_key_debug_enabled = false;
static bool cpu_lcd_debug_enabled = false;

/* Extern from modlcd_controller.c */
extern lcd_state_t *lcd_c_get_state(void);

/* C-side memory map buffers */
static uint8_t rom0_buf[0x2000]; // 8KB Internal ROM
static size_t rom0_size = 0;
static uint8_t rom1_buf[0x8000]; // 32KB System ROM
static size_t rom1_size = 0;
static uint8_t ram_buf[0x2000];     // 8KB RAM (0x6000-0x7FFF)
static uint8_t exp_ram_buf[0x8000]; // 32KB Expanded RAM (Bank 1: 0x8000-0xFFFF)
static bool has_exp_ram = false;
static bool has_exp_ram_forced = false;

/* Dedup trace state for SSTOP/SBOT (0x6931-0x6934). */
static uint8_t sstop_sbot_last[4];
static bool sstop_sbot_last_valid[4];

/* Direct C mode flags */
static bool use_c_mem = false;
static bool use_c_lcd = false;
static bool use_c_kb = false;

/* ====== C Keyboard Matrix ====== */
#define KB_ROWS 13
#define KB_COLS 12
static bool c_kb_matrix[KB_ROWS][KB_COLS]; /* [row][col_index] */
static uint8_t c_kb_ia_select = 0;
/* Physical state trackers for host modifiers */
static bool host_shift_physical = false;
static bool host_alt_physical = false;

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

/* Python callbacks (set from Python) */
static mp_obj_t py_f11_callback = MP_OBJ_NULL;
static mp_obj_t py_f9_callback = MP_OBJ_NULL;

/* UART RX/TX FIFO (Internal) */
#define UART_RX_FIFO_SIZE 256
static uint8_t uart_rx_fifo[UART_RX_FIFO_SIZE];
static uint8_t uart_tx_fifo[256];
static uint8_t uart_rx_head = 0;
static uint8_t uart_rx_tail = 0;
static uint8_t uart_tx_head = 0;
static uint8_t uart_tx_tail = 0;

/* Polling-based key notification (ISR-safe: no mp_sched_schedule) */
static volatile int16_t c_kb_last_pressed_scancode = -1;

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
  /* 1. Update modifier state trackers */
  if (scancode == 0xE1 || scancode == 0xE5) {
    host_shift_physical = pressed;
    /* We don't return here yet, we might need to search the map if Shift itself mapping matters, 
       but usually we just record it. However, Alt is special. */
  }
  if (scancode == 0xE2 || scancode == 0xE6) {
    host_alt_physical = pressed;
  }

  /* 2. Handle F11 specifically (Save state) */
  if (scancode == 0x44 && pressed) {
    mp_printf(&mp_plat_print, "C: F11 detect (Save Request)\n");
    if (py_f11_callback != MP_OBJ_NULL && py_f11_callback != mp_const_none) {
      mp_sched_schedule(py_f11_callback, mp_const_none);
    }
    return;
  }

  /* 2B. Handle F9 specifically (Reset Request) */
  if (scancode == 0x42 && pressed) {
    mp_printf(&mp_plat_print, "C: F9 detect (Reset Request)\n");
    if (py_f9_callback != MP_OBJ_NULL && py_f9_callback != mp_const_none) {
      mp_sched_schedule(py_f9_callback, mp_const_none);
    }
    return;
  }

  /* 3. Handle key release */
  if (!pressed) {
    for (int i = 0; i < c_kb_active_usb_count; i++) {
      if (c_kb_active_usb[i].scancode == scancode) {
        for (int j = 0; j < c_kb_active_usb[i].n_coords; j++) {
          c_kb_release(c_kb_active_usb[i].coords[j][0], c_kb_active_usb[i].coords[j][1]);
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
      for (int j = 0; j < 4; j++) {
        uint8_t r = dynamic_adv_map[i].coords[j][0];
        uint8_t k = dynamic_adv_map[i].coords[j][1];
        if (r == 0xFF) break;
        ak->coords[ak->n_coords][0] = r;
        ak->coords[ak->n_coords][1] = k;
        ak->n_coords++;
        c_kb_press(r, k);
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
  if (!use_c_kb) return;
  /* Release pulse on next call (provides ~1 frame pulse duration) */
  if (c_kb_pulse_release_pending && c_kb_key_line_state) {
    hd61700_set_input(&cpu_state, HD61700_KEY_INT, 0);
    c_kb_key_line_state = false;
    c_kb_pulse_release_pending = false;
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
static mp_obj_t py_lcd_read_cb = MP_OBJ_NULL;
static mp_obj_t py_lcd_write_cb = MP_OBJ_NULL;
static mp_obj_t py_lcd_ctrl_cb = MP_OBJ_NULL;
static mp_obj_t py_kb_read_cb = MP_OBJ_NULL;
static mp_obj_t py_kb_write_cb = MP_OBJ_NULL;
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
static bool detect_exp_ram_file(void) {
  struct stat st;
  if (stat("/roms/ram1.bin", &st) == 0) {
    return true;
  }
  if (stat("roms/ram1.bin", &st) == 0) {
    return true;
  }
  return false;
}

static void anchor_callbacks(mp_obj_t obj) {
  if (obj == mp_const_none)
    return;
  if (py_callback_anchor_list == MP_OBJ_NULL) {
    py_callback_anchor_list = mp_obj_new_list(0, NULL);
  }
  mp_obj_list_append(py_callback_anchor_list, obj);
}

static uint8_t c_mem_read(void *ctx, uint8_t segment, uint32_t offset) {
  (void)ctx;
  if (py_mem_read_cb == MP_OBJ_NULL)
    return 0;
  mp_obj_t args[2] = {MP_OBJ_NEW_SMALL_INT(segment),
                      MP_OBJ_NEW_SMALL_INT(offset)};
  mp_obj_t result = mp_call_function_n_kw(py_mem_read_cb, 2, 0, args);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_mem_write(void *ctx, uint8_t segment, uint32_t offset,
                        uint8_t data) {
  (void)ctx;
  if (py_mem_write_cb == MP_OBJ_NULL)
    return;
  mp_obj_t args[3] = {MP_OBJ_NEW_SMALL_INT(segment),
                      MP_OBJ_NEW_SMALL_INT(offset), MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_mem_write_cb, 3, 0, args);
}

static uint8_t c_lcd_read(void *ctx) {
  (void)ctx;
  if (py_lcd_read_cb == MP_OBJ_NULL)
    return 0xff;
  mp_obj_t result = mp_call_function_0(py_lcd_read_cb);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_lcd_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_lcd_write_cb == MP_OBJ_NULL)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_lcd_write_cb, 1, 0, args);
}

static void c_lcd_ctrl(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_lcd_ctrl_cb == MP_OBJ_NULL)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_lcd_ctrl_cb, 1, 0, args);
}

static uint16_t c_kb_read(void *ctx) {
  (void)ctx;
  if (use_c_kb) return c_kb_compute_ky();
  if (py_kb_read_cb == MP_OBJ_NULL)
    return 0;
  mp_obj_t result = mp_call_function_0(py_kb_read_cb);
  return (uint16_t)mp_obj_get_int(result);
}

static void c_kb_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (use_c_kb) { c_kb_ia_select = data; return; }
  if (py_kb_write_cb == MP_OBJ_NULL)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_kb_write_cb, 1, 0, args);
}

static uint8_t c_port_read(void *ctx) {
  (void)ctx;
  if (py_port_read_cb == MP_OBJ_NULL)
    return 0;
  mp_obj_t result = mp_call_function_0(py_port_read_cb);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_port_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_port_write_cb == MP_OBJ_NULL)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_port_write_cb, 1, 0, args);
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
  /* 0x0000-0x1FFF: Internal ROM / IO area */
  if (offset < 0x2000) {
    if (offset >= 0x0C00 && offset <= 0x0C03) {
      /* MMIO UART: Trap in all banks (matching Python behavior) */
      if (offset == 0x0C00) {
        /* Status register:
         * Bit 0: TX Busy (1 = Full/Busy, 0 = Ready) -> PB-1000 technical ref says Bit 0 is TX Busy
         * Bit 1: RX Ready (1 = Data available)
         * Bit 2: CTS (1 = Ready)
         * Bit 3: DSR (1 = Ready)
         * Bit 4: DCD (1 = Ready)
         */
        uint8_t status = 0x1C; /* Hardcode modem lines to ready (Bits 2,3,4) */
        if ((uint8_t)(uart_tx_head + 1) == uart_tx_tail) {
          status |= 0x01; // TX Busy
        }
        if (uart_rx_head != uart_rx_tail) {
          status |= 0x02; // RX Ready
        }
        return status;
      }
      if (offset == 0x0C01) {
        return 0x00;
      }
      if (offset == 0x0C02) {
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
            // mp_printf(&mp_plat_print, "DB: UART RX EMPTY -> INT1 CLEARED\n");
          }
          return val;
        }
        /* DEBUG log for empty read */
        // mp_printf(&mp_plat_print, "DB: UART RX READ EMPTY (0x0C02) (PC=%04X)\n", cpu_state.pc);
        return 0x00;
      }
    }
    return (offset < rom0_size) ? rom0_buf[offset] : 0xFF;
  }
  /* 0x6000-0x7FFF: RAM */
  if (offset >= 0x6000 && offset < 0x8000) {
    return ram_buf[offset - 0x6000];
  }
  /* 0x8000-0xFFFF: Banked Memory */
  if (offset >= 0x8000) {
    uint32_t bank_u32 = (uint32_t)bank;
    uint32_t off = offset - 0x8000u;
    if (bank_u32 == 0) {
      /* Bank 0: System ROM */
      if (rom1_size == 0)
        return 0xFF;
      return rom1_buf[off % rom1_size];
    } else if (bank_u32 == 1 && has_exp_ram) {
      /* Bank 1: Expanded RAM */
      return exp_ram_buf[off];
    }
    return 0xFF;
  }
  return 0xFF;
}

static void c_mem_direct_write(void *ctx, uint8_t segment, uint32_t offset,
                               uint8_t data) {
  (void)ctx;
  uint8_t bank = normalize_bank(segment);
  /* Only write to RAM area (0x6000-0x7FFF) */
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
  /* 0x0C00-0x0C03: MMIO UART area */
  else if (offset < 0x2000) {
    if (offset >= 0x0C00 && offset <= 0x0C03) {
      if (offset == 0x0C03) {
        /* TX Data Register: Push to TX FIFO */
        if ((uint8_t)(uart_tx_head + 1) != uart_tx_tail) {
          uart_tx_fifo[uart_tx_head++] = data;
        }
      }
    }
    return;
  }
  /* Bank 1 Expanded RAM Write (0x8000-0xFFFF) */
  else if (offset >= 0x8000 && bank == 1) {
    if (has_exp_ram) {
      exp_ram_buf[offset - 0x8000] = data;
      if (ENABLE_PROG_WRITE_TRACE && is_prog_trace_addr(offset)) {
        log_watch_write("PROG-WR", bank, offset, data, NULL);
      }
    } else if (is_prog_trace_addr(offset)) {
      log_watch_write("PROG-WR-IGN", bank, offset, data, "no exp RAM");
    }
  } else if (is_prog_trace_addr(offset)) {
    log_watch_write("PROG-WR-IGN", bank, offset, data, "bank not writable");
  }
}

static void c_lcd_direct_ctrl(void *ctx, uint8_t data) {
  (void)ctx;
  lcd_ctrl(lcd_c_get_state(), data);
}

static void c_lcd_direct_write(void *ctx, uint8_t data) {
  (void)ctx;
  lcd_write(lcd_c_get_state(), data);
}

static uint8_t c_lcd_direct_read(void *ctx) {
  (void)ctx;
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
  hd61700_set_key_debug(&cpu_state, cpu_debug_enabled && cpu_key_debug_enabled);
  hd61700_set_lcd_debug(&cpu_state, cpu_debug_enabled && cpu_lcd_debug_enabled);
  memset(sstop_sbot_last, 0, sizeof(sstop_sbot_last));
  memset(sstop_sbot_last_valid, 0, sizeof(sstop_sbot_last_valid));
  /* Fallback auto-detection so C direct-memory mode also works without
     explicit Python-side set_has_exp_ram(). */
  if (!has_exp_ram_forced) {
    has_exp_ram = detect_exp_ram_file();
  }
  /* Register C callbacks */
  cpu_state.mem_read = use_c_mem ? c_mem_direct_read : c_mem_read;
  cpu_state.mem_write = use_c_mem ? c_mem_direct_write : c_mem_write;
  cpu_state.lcd_read = use_c_lcd ? c_lcd_direct_read : c_lcd_read;
  cpu_state.lcd_write = use_c_lcd ? c_lcd_direct_write : c_lcd_write;
  cpu_state.lcd_ctrl = use_c_lcd ? c_lcd_direct_ctrl : c_lcd_ctrl;
  cpu_state.kb_read = c_kb_read;
  cpu_state.kb_write = c_kb_write;
  cpu_state.port_read = c_port_read;
  cpu_state.port_write = c_port_write;
  cpu_state.log_write = c_log_write;
  cpu_state.log_ctx = NULL;
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
  /* Service C keyboard KEY_INT pulses before executing */
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

/* hd61700.set_lcd_callbacks(read_fn, write_fn, ctrl_fn) */
static mp_obj_t mod_set_lcd_callbacks(mp_obj_t read_fn, mp_obj_t write_fn,
                                      mp_obj_t ctrl_fn) {
  py_lcd_read_cb = read_fn;
  py_lcd_write_cb = write_fn;
  py_lcd_ctrl_cb = ctrl_fn;
  anchor_callbacks(read_fn);
  anchor_callbacks(write_fn);
  anchor_callbacks(ctrl_fn);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_set_lcd_callbacks_obj,
                                 mod_set_lcd_callbacks);

/* hd61700.set_kb_callbacks(read_fn, write_fn) */
static mp_obj_t mod_set_kb_callbacks(mp_obj_t read_fn, mp_obj_t write_fn) {
  py_kb_read_cb = read_fn;
  py_kb_write_cb = write_fn;
  anchor_callbacks(read_fn);
  anchor_callbacks(write_fn);
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_set_kb_callbacks_obj,
                                 mod_set_kb_callbacks);

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

/* hd61700.load_ram(slot, data) */
static mp_obj_t mod_load_ram(mp_obj_t slot_obj, mp_obj_t data_obj) {
  int slot = mp_obj_get_int(slot_obj);
  mp_buffer_info_t bufinfo;
  mp_get_buffer_raise(data_obj, &bufinfo, MP_BUFFER_READ);

  if (slot == 0) {
    size_t to_copy = (bufinfo.len > sizeof(ram_buf)) ? sizeof(ram_buf) : bufinfo.len;
    memcpy(ram_buf, bufinfo.buf, to_copy);
  } else {
    size_t to_copy = (bufinfo.len > sizeof(exp_ram_buf)) ? sizeof(exp_ram_buf) : bufinfo.len;
    memcpy(exp_ram_buf, bufinfo.buf, to_copy);
  }
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_load_ram_obj, mod_load_ram);

/* hd61700.use_c_memory(bool) */
static mp_obj_t mod_use_c_memory(mp_obj_t enable_obj) {
  use_c_mem = mp_obj_is_true(enable_obj);
  cpu_state.mem_read = use_c_mem ? c_mem_direct_read : c_mem_read;
  cpu_state.mem_write = use_c_mem ? c_mem_direct_write : c_mem_write;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_use_c_memory_obj, mod_use_c_memory);

/* hd61700.use_c_lcd(bool) */
static mp_obj_t mod_use_c_lcd(mp_obj_t enable_obj) {
  use_c_lcd = mp_obj_is_true(enable_obj);
  cpu_state.lcd_read = use_c_lcd ? c_lcd_direct_read : c_lcd_read;
  cpu_state.lcd_write = use_c_lcd ? c_lcd_direct_write : c_lcd_write;
  cpu_state.lcd_ctrl = use_c_lcd ? c_lcd_direct_ctrl : c_lcd_ctrl;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_use_c_lcd_obj, mod_use_c_lcd);

/* hd61700.get_ram_view() */
static mp_obj_t mod_get_ram_view(void) {
  return mp_obj_new_memoryview('B', sizeof(ram_buf), ram_buf);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_ram_view_obj, mod_get_ram_view);

/* hd61700.get_exp_ram_view() */
static mp_obj_t mod_get_exp_ram_view(void) {
  return mp_obj_new_memoryview('B', sizeof(exp_ram_buf), exp_ram_buf);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_exp_ram_view_obj,
                                 mod_get_exp_ram_view);

/* hd61700.set_has_exp_ram(bool) */
static mp_obj_t mod_set_has_exp_ram(mp_obj_t enable_obj) {
  has_exp_ram = mp_obj_is_true(enable_obj);
  has_exp_ram_forced = true;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_has_exp_ram_obj, mod_set_has_exp_ram);

/* hd61700.read_mem(addr, [segment]) */
static mp_obj_t mod_read_mem(size_t n_args, const mp_obj_t *args) {
  uint32_t addr = (uint32_t)mp_obj_get_int(args[0]);
  uint8_t segment = (n_args > 1) ? (uint8_t)mp_obj_get_int(args[1]) : 0;
  uint8_t bank = normalize_bank(segment);
  uint8_t data;
  if (use_c_mem) {
    data = c_mem_direct_read(NULL, bank, addr);
  } else {
    data = c_mem_read(NULL, bank, addr);
  }
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
  if (use_c_mem) {
    c_mem_direct_write(NULL, bank, addr, data);
  } else {
    c_mem_write(NULL, bank, addr, data);
  }
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

/* hd61700.use_c_keyboard(enabled) */
static mp_obj_t mod_use_c_keyboard(mp_obj_t enabled_obj) {
  use_c_kb = mp_obj_is_true(enabled_obj);
  /* Clear C matrix state on mode change */
  memset(c_kb_matrix, 0, sizeof(c_kb_matrix));
  c_kb_active_usb_count = 0;
  c_kb_key_line_state = false;
  c_kb_pulse_release_pending = false;
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_use_c_keyboard_obj, mod_use_c_keyboard);

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

// module.get_last_key() -> int (-1 if none)
static mp_obj_t mod_get_last_key(void) {
  int16_t sc = c_kb_last_pressed_scancode;
  c_kb_last_pressed_scancode = -1;
  return MP_OBJ_NEW_SMALL_INT(sc);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_get_last_key_obj, mod_get_last_key);


/* ====== Module definition ====== */
static const mp_rom_map_elem_t hd61700_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_hd61700)},
    {MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&mod_reset_obj)},
    {MP_ROM_QSTR(MP_QSTR_execute), MP_ROM_PTR(&mod_execute_obj)},
    {MP_ROM_QSTR(MP_QSTR_execute_steps), MP_ROM_PTR(&mod_execute_steps_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_debug), MP_ROM_PTR(&mod_set_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_key_debug), MP_ROM_PTR(&mod_set_key_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_lcd_debug), MP_ROM_PTR(&mod_set_lcd_debug_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_mem_callbacks),
     MP_ROM_PTR(&mod_set_mem_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_lcd_callbacks),
     MP_ROM_PTR(&mod_set_lcd_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_kb_callbacks),
     MP_ROM_PTR(&mod_set_kb_callbacks_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_port_callbacks),
     MP_ROM_PTR(&mod_set_port_callbacks_obj)},
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
    {MP_ROM_QSTR(MP_QSTR_use_c_memory), MP_ROM_PTR(&mod_use_c_memory_obj)},
    {MP_ROM_QSTR(MP_QSTR_use_c_lcd), MP_ROM_PTR(&mod_use_c_lcd_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_ram_view), MP_ROM_PTR(&mod_get_ram_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_get_exp_ram_view),
     MP_ROM_PTR(&mod_get_exp_ram_view_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_has_exp_ram),
     MP_ROM_PTR(&mod_set_has_exp_ram_obj)},
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
    {MP_ROM_QSTR(MP_QSTR_use_c_keyboard),
     MP_ROM_PTR(&mod_use_c_keyboard_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_f11_callback),
     MP_ROM_PTR(&mod_set_f11_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_f9_callback),
     MP_ROM_PTR(&mod_set_f9_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_tx_get), MP_ROM_PTR(&mod_uart_tx_get_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_uart_tx_callback), MP_ROM_PTR(&mod_set_uart_tx_callback_obj)},
    {MP_ROM_QSTR(MP_QSTR_uart_rx_put),
     MP_ROM_PTR(&mod_uart_rx_put_obj)},
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











