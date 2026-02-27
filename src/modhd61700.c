/*
 * MicroPython C Module wrapper for HD61700 CPU
 * Exposes HD61700 CPU core to MicroPython as 'hd61700' module
 */
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

/* Direct C mode flags */
static bool use_c_mem = false;
static bool use_c_lcd = false;

static bool is_key_trace_addr(uint32_t offset) {
  return offset == 0x68D2 || offset == 0x68D3 || offset == 0x68D4 ||
         offset == 0x68D5 || offset == 0x68D6 || offset == 0x68D7 ||
         offset == 0x68D8;
}

static bool is_key_buffer_trace_addr(uint32_t offset) {
  return offset >= 0x68D9 && offset <= 0x68EC;
}

/* Python callback objects */
static mp_obj_t py_mem_read_cb = mp_const_none;
static mp_obj_t py_mem_write_cb = mp_const_none;
static mp_obj_t py_lcd_read_cb = mp_const_none;
static mp_obj_t py_lcd_write_cb = mp_const_none;
static mp_obj_t py_lcd_ctrl_cb = mp_const_none;
static mp_obj_t py_kb_read_cb = mp_const_none;
static mp_obj_t py_kb_write_cb = mp_const_none;
static mp_obj_t py_port_read_cb = mp_const_none;
static mp_obj_t py_port_write_cb = mp_const_none;

static void anchor_callbacks(mp_obj_t obj) { (void)obj; }

static uint8_t c_mem_read(void *ctx, uint8_t segment, uint32_t offset) {
  (void)ctx;
  if (py_mem_read_cb == mp_const_none)
    return 0;
  mp_obj_t args[2] = {MP_OBJ_NEW_SMALL_INT(segment),
                      MP_OBJ_NEW_SMALL_INT(offset)};
  mp_obj_t result = mp_call_function_n_kw(py_mem_read_cb, 2, 0, args);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_mem_write(void *ctx, uint8_t segment, uint32_t offset,
                        uint8_t data) {
  (void)ctx;
  if (py_mem_write_cb == mp_const_none)
    return;
  mp_obj_t args[3] = {MP_OBJ_NEW_SMALL_INT(segment),
                      MP_OBJ_NEW_SMALL_INT(offset), MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_mem_write_cb, 3, 0, args);
}

static uint8_t c_lcd_read(void *ctx) {
  (void)ctx;
  if (py_lcd_read_cb == mp_const_none)
    return 0xff;
  mp_obj_t result = mp_call_function_0(py_lcd_read_cb);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_lcd_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_lcd_write_cb == mp_const_none)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_lcd_write_cb, 1, 0, args);
}

static void c_lcd_ctrl(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_lcd_ctrl_cb == mp_const_none)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_lcd_ctrl_cb, 1, 0, args);
}

static uint16_t c_kb_read(void *ctx) {
  (void)ctx;
  if (py_kb_read_cb == mp_const_none)
    return 0;
  mp_obj_t result = mp_call_function_0(py_kb_read_cb);
  return (uint16_t)mp_obj_get_int(result);
}

static void c_kb_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_kb_write_cb == mp_const_none)
    return;
  mp_obj_t args[1] = {MP_OBJ_NEW_SMALL_INT(data)};
  mp_call_function_n_kw(py_kb_write_cb, 1, 0, args);
}

static uint8_t c_port_read(void *ctx) {
  (void)ctx;
  if (py_port_read_cb == mp_const_none)
    return 0;
  mp_obj_t result = mp_call_function_0(py_port_read_cb);
  return (uint8_t)mp_obj_get_int(result);
}

static void c_port_write(void *ctx, uint8_t data) {
  (void)ctx;
  if (py_port_write_cb == mp_const_none)
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
  /* 0x0000-0x1FFF: Internal ROM */
  if (offset < 0x2000) {
    return (offset < rom0_size) ? rom0_buf[offset] : 0xFF;
  }
  /* 0x6000-0x7FFF: RAM */
  if (offset >= 0x6000 && offset < 0x8000) {
    return ram_buf[offset - 0x6000];
  }
  /* 0x8000-0xFFFF: Banked Memory */
  if (offset >= 0x8000) {
    uint32_t bank = (uint32_t)(segment & 0x03u);
    uint32_t off = offset - 0x8000u;
    if (bank == 0) {
      /* Bank 0: System ROM */
      if (rom1_size == 0)
        return 0xFF;
      return rom1_buf[off % rom1_size];
    } else if (bank == 1 && has_exp_ram) {
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
  (void)segment;
  /* Only write to RAM area (0x6000-0x7FFF) */
  if (offset >= 0x6000 && offset < 0x8000) {
    ram_buf[offset - 0x6000] = data;
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
  /* Bank 1 Expanded RAM Write (0x8000-0xFFFF) */
  else if (offset >= 0x8000 && (segment & 0x03) == 1 && has_exp_ram) {
    exp_ram_buf[offset - 0x8000] = data;
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
  hd61700_set_key_debug(&cpu_state, cpu_debug_enabled && cpu_key_debug_enabled);
  hd61700_set_lcd_debug(&cpu_state, cpu_debug_enabled && cpu_lcd_debug_enabled);
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

// hd61700.step() -> 実行した命令の bytes オブジェクトを返す
static mp_obj_t mod_step(void) {
  hd61700_step(&cpu_state);

  // 実行したバイト列を MicroPython の bytes 型として返す
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
  return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_has_exp_ram_obj, mod_set_has_exp_ram);

/* hd61700.read_mem(addr, [segment]) */
static mp_obj_t mod_read_mem(size_t n_args, const mp_obj_t *args) {
  uint32_t addr = (uint32_t)mp_obj_get_int(args[0]);
  uint8_t segment = (n_args > 1) ? (uint8_t)mp_obj_get_int(args[1]) : 0;
  return MP_OBJ_NEW_SMALL_INT(c_mem_direct_read(NULL, segment, addr));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_read_mem_obj, 1, 2,
                                           mod_read_mem);

/* hd61700.write_mem(addr, data, [segment]) */
static mp_obj_t mod_write_mem(size_t n_args, const mp_obj_t *args) {
  uint32_t addr = (uint32_t)mp_obj_get_int(args[0]);
  uint8_t data = (uint8_t)mp_obj_get_int(args[1]);
  uint8_t segment = (n_args > 2) ? (uint8_t)mp_obj_get_int(args[2]) : 0;
  c_mem_direct_write(NULL, segment, addr, data);
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
static mp_obj_t mod_init_anchor(void) { return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_init_anchor_obj, mod_init_anchor);

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
    /* ステップ実行用 */
    {MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&mod_step_obj)},
    /* Optimization APIs */
    {MP_ROM_QSTR(MP_QSTR_load_rom), MP_ROM_PTR(&mod_load_rom_obj)},
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
