/*
 * HD61700 CPU Emulator - Complete Implementation
 * Based on MAME hd61700.cpp by Sandro Ronco (BSD-3-Clause license)
 * Ported to standalone C for MicroPython integration on RP2350
 */
#include "hd61700.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/* ======== Helper Functions ======== */

static void cpu_log(hd61700_state_t *cpu, const char *fmt, ...) {
  if (!cpu->debug_log)
    return;
  if (!cpu->log_write)
    return;
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n < 0)
    return;
  cpu->log_write(cpu->log_ctx, buf);
}

static inline void set_pc(hd61700_state_t *cpu, int32_t new_pc) {
  uint16_t old_pc = cpu->pc;
  cpu->pc = (uint16_t)(new_pc & 0xffff);
  if (cpu->pc < INT_ROM)
    cpu->fetch_addr = (uint32_t)cpu->pc << 1;
  else
    cpu->fetch_addr = (uint32_t)cpu->pc;
  cpu->curpc = cpu->pc;
  cpu->ppc = cpu->curpc;

  if (cpu->debug_log && (cpu->pc == 0x00F9 || old_pc == 0xE40C)) {
    cpu_log(cpu, "SET_PC: 0x%04X -> 0x%04X (fetch: 0x%08X)", old_pc, cpu->pc, cpu->fetch_addr);
  }
}

static uint8_t debug_read_bank0_u8(hd61700_state_t *cpu, uint32_t offset) {
  if (!cpu->mem_read)
    return 0;
  return cpu->mem_read(cpu->cb_ctx, 0, offset);
}

static uint16_t debug_read_bank0_u16(hd61700_state_t *cpu, uint32_t offset) {
  uint8_t lo = debug_read_bank0_u8(cpu, offset);
  uint8_t hi = debug_read_bank0_u8(cpu, offset + 1);
  return (uint16_t)(lo | (hi << 8));
}

static uint32_t debug_calc_free_span(uint16_t hi, uint16_t lo) {
  return (uint32_t)((uint16_t)(hi - lo));
}

static uint32_t debug_calc_needed_entry_space(uint16_t free_ptr) {
  return (uint32_t)((uint16_t)(free_ptr - 0x0021u));
}

static uint8_t mem_readbyte(hd61700_state_t *cpu, uint8_t segment,
                            uint32_t offset) {
  uint8_t bank = (segment >> 4) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_read) return cpu->io_read(cpu->cb_ctx, bank, offset);
  }

  if (offset < ((uint32_t)INT_ROM << 1)) {
    if (cpu->rom0_ptr) return cpu->rom0_ptr[offset];
  } else if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) return cpu->ram_ptr[offset - 0x6000];
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_ptr[bank]) return cpu->bank_ptr[bank][offset - 0x8000];
  }

  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte(hd61700_state_t *cpu, uint8_t segment,
                          uint32_t offset, uint8_t data) {
  uint8_t bank = (segment >> 4) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_write) {
      cpu->io_write(cpu->cb_ctx, bank, offset, data);
      return;
    }
  }

  if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) {
      cpu->ram_ptr[offset - 0x6000] = data;
      return;
    }
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_is_ram[bank] && cpu->bank_ptr[bank]) {
      cpu->bank_ptr[bank][offset - 0x8000] = data;
      return;
    }
  }

  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t mem_readbyte_iz(hd61700_state_t *cpu, uint8_t segment,
                               uint32_t offset) {
  uint8_t bank = (segment >> 6) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_read) return cpu->io_read(cpu->cb_ctx, bank, offset);
  }

  if (offset < ((uint32_t)INT_ROM << 1)) {
    if (cpu->rom0_ptr) return cpu->rom0_ptr[offset];
  } else if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) return cpu->ram_ptr[offset - 0x6000];
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_ptr[bank]) return cpu->bank_ptr[bank][offset - 0x8000];
  }

  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte_iz(hd61700_state_t *cpu, uint8_t segment,
                             uint32_t offset, uint8_t data) {
  uint8_t bank = (segment >> 6) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_write) {
      cpu->io_write(cpu->cb_ctx, bank, offset, data);
      return;
    }
  }

  if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) {
      cpu->ram_ptr[offset - 0x6000] = data;
      return;
    }
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_is_ram[bank] && cpu->bank_ptr[bank]) {
      cpu->bank_ptr[bank][offset - 0x8000] = data;
      return;
    }
  }

  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t mem_readbyte_stack(hd61700_state_t *cpu, uint8_t segment,
                                  uint32_t offset) {
  uint8_t bank = (segment >> 2) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_read) return cpu->io_read(cpu->cb_ctx, bank, offset);
  }

  if (offset < ((uint32_t)INT_ROM << 1)) {
    if (cpu->rom0_ptr) return cpu->rom0_ptr[offset];
  } else if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) return cpu->ram_ptr[offset - 0x6000];
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_ptr[bank]) return cpu->bank_ptr[bank][offset - 0x8000];
  }

  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte_stack(hd61700_state_t *cpu, uint8_t segment,
                                uint32_t offset, uint8_t data) {
  uint8_t bank = (segment >> 2) & 0x03;
  offset &= 0xFFFF;

  if (offset >= 0x0C00 && offset <= 0x0CFF) {
    if (cpu->io_write) {
      cpu->io_write(cpu->cb_ctx, bank, offset, data);
      return;
    }
  }

  if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) {
      cpu->ram_ptr[offset - 0x6000] = data;
      return;
    }
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_is_ram[bank] && cpu->bank_ptr[bank]) {
      cpu->bank_ptr[bank][offset - 0x8000] = data;
      return;
    }
  }

  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t prog_readbyte(hd61700_state_t *cpu, uint8_t segment,
                             uint32_t offset) {
  uint8_t bank = segment & 0x03;
  offset &= 0xFFFF;

  if (offset < ((uint32_t)INT_ROM << 1)) {
    if (cpu->rom0_ptr) return cpu->rom0_ptr[offset];
  } else if (offset >= 0x6000 && offset < 0x8000) {
    if (cpu->ram_ptr) return cpu->ram_ptr[offset - 0x6000];
  } else if (offset >= 0x8000) {
    if (bank < 4 && cpu->bank_ptr[bank]) return cpu->bank_ptr[bank][offset - 0x8000];
  }

  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

/* Forward declaration */
static uint8_t read_op(hd61700_state_t *cpu);

static uint8_t read_internal_rom_byte(hd61700_state_t *cpu, uint32_t addr) {
  uint32_t base = addr & ~1u;
  uint8_t hi = prog_readbyte(cpu, cpu->prev_ua, base);
  uint8_t lo = prog_readbyte(cpu, cpu->prev_ua, base + 1);
  return (addr & 1) ? lo : hi;
}

static uint8_t read_program_byte(hd61700_state_t *cpu, uint32_t addr) {
  if (addr < ((uint32_t)INT_ROM << 1)) {
    return read_internal_rom_byte(cpu, addr);
  }
  return prog_readbyte(cpu, cpu->prev_ua, addr);
}

/* Read a 16-bit immediate operand following an opcode.
 * On the internal ROM the encoding is word-aligned (opcode hi|lo, then hi|lo),
 * so the high byte of the immediate sits in the low byte of the next word.
 * To reproduce that layout we skip one byte between low and high when
 * executing from internal ROM. */
static uint16_t read_imm16_aligned(hd61700_state_t *cpu) {
  uint8_t lo = read_op(cpu);
  if (cpu->pc < INT_ROM) {
    cpu->fetch_addr += 1; /* move to low byte of next word */
    cpu->pc = (uint16_t)(cpu->fetch_addr >> 1);
  }
  uint8_t hi = read_op(cpu);
  return (uint16_t)(lo | (hi << 8));
}

static uint8_t read_op(hd61700_state_t *cpu) {
  uint32_t addr = cpu->fetch_addr;
  uint8_t data = read_program_byte(cpu, addr);

  if (cpu->debug_log && (addr >= 0xE40C && addr <= 0xE40F)) {
    cpu_log(cpu, "READ_OP: [0x%08X] -> 0x%02X", addr, data);
  }

  // 鬯ｯ・ｮ繝ｻ・ｯ髫ｶ蜴・ｽｽ・ｸ郢晢ｽｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｳ鬯ｮ・ｮ闕ｵ譏ｴ繝ｻ郢晢ｽｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｯ鬩幢ｽ｢隴趣ｽ｢繝ｻ・ｽ繝ｻ・ｻ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・｡鬯ｮ・ｯ雋顔§謫郢晢ｽｻ繝ｻ・ｯ髣費ｽｨ陞滂ｽｲ繝ｻ・ｽ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｼ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｰ鬯ｯ・ｩ隰ｳ・ｾ繝ｻ・ｽ繝ｻ・ｵ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｺ鬯ｮ・ｮ闕ｵ譏ｴ繝ｻ繝ｻ縺､ﾂ郢晢ｽｻ繝ｻ・･鬩包ｽｯ繝ｻ・ｶ郢晢ｽｻ繝ｻ・ｲ鬯ｯ・ｩ陝ｷ・｢繝ｻ・ｽ繝ｻ・｢鬮ｫ・ｴ陷ｿ髢・ｾ蜉ｱ繝ｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・｣驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｹ鬯ｩ謳ｾ・ｽ・ｵ郢晢ｽｻ繝ｻ・ｺ鬮ｫ・ｲ繝ｻ・ｷ髯具ｽｹ郢晢ｽｻ繝ｻ・ｽ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｹ鬮ｫ・ｴ髮懶ｽ｣繝ｻ・ｽ繝ｻ・｢驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｽ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｼ鬯ｯ・ｩ陝ｷ・｢繝ｻ・ｽ繝ｻ・｢鬮ｫ・ｴ陷ｿ髢・ｾ蜉ｱ繝ｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｳ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｨ鬩幢ｽ｢隴趣ｽ｢繝ｻ・ｽ繝ｻ・ｻ鬮ｯ譎｢・ｽ・ｶ髫ｴ諠ｹ・ｹ諤懌┌鬯ｮ・ｯ陋ｹ・ｺ繝ｻ・ｻ郢ｧ謇假ｽｽ・ｽ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｬ鬯ｯ・ｲ郢晢ｽｻ驕倪・繝ｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｸ鬯ｯ・ｩ隰ｳ・ｾ繝ｻ・ｽ繝ｻ・ｵ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｺ鬯ｮ・ｯ繝ｻ・ｷ髣費ｽｨ陞滂ｽｲ繝ｻ・ｽ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｱ鬯ｩ蛹・ｽｽ・ｯ郢晢ｽｻ繝ｻ・ｶ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｻ鬯ｯ・ｩ隰ｳ・ｾ繝ｻ・ｽ繝ｻ・ｵ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・ｺ鬯ｯ・ｩ隲､諞ｺ笳冗ｹ晢ｽｻ繝ｻ・ｽ郢晢ｽｻ繝ｻ・ｫ鬩幢ｽ｢隴趣ｽ｢繝ｻ・ｽ繝ｻ・ｻ驛｢譎｢・ｽ・ｻ郢晢ｽｻ繝ｻ・･
  if (cpu->last_op_len < sizeof(cpu->last_opcodes)) {
    cpu->last_opcodes[cpu->last_op_len++] = data;
  }

  cpu->prev_ua = REG_UA;
  cpu->fetch_addr += 1;
  if (cpu->pc < INT_ROM)
    cpu->pc = (uint16_t)(cpu->fetch_addr >> 1);
  else
    cpu->pc = (uint16_t)cpu->fetch_addr;
  return data;
}

static void push(hd61700_state_t *cpu, uint16_t *offset, uint8_t data) {
  (*offset)--;
  mem_writebyte_stack(cpu, REG_UA, *offset, data);
}

static uint8_t pop(hd61700_state_t *cpu, uint16_t *offset) {
  uint8_t data = mem_readbyte_stack(cpu, REG_UA, *offset);
  (*offset)++;
  return data;
}

static int check_cond(hd61700_state_t *cpu, uint32_t op) {
  switch (op & 0x07) {
  case 0x00:
    return (cpu->flags & FLAG_Z) ? 1 : 0;
  case 0x01:
    return !(cpu->flags & FLAG_C) ? 1 : 0;
  case 0x02:
    return (cpu->flags & FLAG_LZ) ? 1 : 0;
  case 0x03:
    return (cpu->flags & FLAG_UZ) ? 1 : 0;
  case 0x04:
    return !(cpu->flags & FLAG_Z) ? 1 : 0;
  case 0x05:
    return (cpu->flags & FLAG_C) ? 1 : 0;
  case 0x06:
    return !(cpu->flags & FLAG_LZ) ? 1 : 0;
  case 0x07:
    return 1;
  }
  return 0;
}

static uint8_t make_logic(uint8_t type, uint8_t d1, uint8_t d2) {
  switch (type & 3) {
  case 0:
    return d1 & d2;
  case 1:
    return ~(d1 & d2);
  case 2:
    return d1 | d2;
  case 3:
    return d1 ^ d2;
  }
  return 0;
}

static uint8_t read_gst_value(hd61700_state_t *cpu, uint8_t idx) {
  switch (idx) {
  case 0:
  case 1:
  case 4:
    return READ_REG8(idx);
  case 2:
    return REG_IB;
  case 5:
    return REG_IE;
  default:
    return READ_REG8(idx);
  }
}

static uint16_t *get_pre_target(hd61700_state_t *cpu, uint8_t op, uint8_t arg) {
  uint8_t idx = GET_REG_IDX(op, arg);
  if (idx < 6)
    return &cpu->reg16bit[idx];
  /* idx=6/7 aliases map to KY on HD61700 PRE/GRE register selector */
  return &cpu->reg16bit[5];
}

static void write_sreg_or_reg(hd61700_state_t *cpu, uint8_t arg, uint8_t data) {
  if (((arg >> 5) & 0x03) == 0x03)
    WRITE_REG(arg, data);
  else
    WRITE_SREG(arg, data);
}

static uint16_t make_bcd_add(uint8_t arg1, uint8_t arg2) {
  uint32_t ret = (arg1 & 0x0f) + (arg2 & 0x0f);
  uint8_t carry;
  if (ret > 0x09) {
    ret = (ret + 0x06) & 0x0f;
    carry = 1;
  } else
    carry = 0;
  ret += ((arg1 & 0xf0) + (arg2 & 0xf0) + (carry << 4));
  if (ret > 0x9f) {
    ret = (uint8_t)(ret + 0x60);
    carry = 1;
  } else
    carry = 0;
  ret += carry << 8;
  return (uint16_t)ret;
}

static uint16_t make_bcd_sub(uint8_t arg1, uint8_t arg2) {
  uint32_t ret = (arg1 & 0x0f) - (arg2 & 0x0f);
  uint8_t carry;
  if (ret > 0x09) {
    ret = (ret - 0x06) & 0x0f;
    carry = 1;
  } else
    carry = 0;
  ret += ((arg1 & 0xf0) - (arg2 & 0xf0) - (carry << 4));
  if (ret > 0x9f) {
    ret = (uint8_t)(ret - 0x60);
    carry = 1;
  } else
    carry = 0;
  ret -= carry << 8;
  return (uint16_t)ret;
}

static int get_im_7(uint8_t data) {
  if (data & 0x80)
    return 0x80 - data;
  else
    return data;
}

/* Call this only for instruction forms where arg bit7 encodes optional-JR. */
static void check_optional_jr(hd61700_state_t *cpu, uint8_t arg) {
  if (arg & 0x80) {
    /* Internal ROM is word-aligned: skip padding byte before JR offset if
     * fetch_addr is EVEN. */
    if (cpu->pc < INT_ROM && ((cpu->fetch_addr & 0x01) == 0)) {
      (void)read_op(cpu);
    }
    uint8_t arg1 = read_op(cpu);
    uint32_t new_pc = ((cpu->pc - 1) + get_im_7(arg1));
    set_pc(cpu, (int32_t)new_pc);
    cpu->icount -= 3;
  }
}

static uint8_t get_sir_im8(hd61700_state_t *cpu, uint8_t arg) {
  if (((arg >> 5) & 0x03) == 0x03)
    return read_op(cpu) & 0x1f;
  return READ_SREG(arg);
}

static uint8_t get_sir_im8_arg1(hd61700_state_t *cpu, uint8_t arg,
                                uint8_t arg1) {
  if (((arg >> 5) & 0x03) == 0x03)
    return arg1 & 0x1f;
  return READ_SREG(arg);
}

static int get_sign_mreg(hd61700_state_t *cpu, uint8_t arg) {
  int res = READ_REG(get_sir_im8(cpu, arg));
  if (arg & 0x80)
    res = -res;
  return res;
}

static int get_sign_im8(hd61700_state_t *cpu, uint8_t arg) {
  int res = read_op(cpu);
  if (arg & 0x80)
    res = -res;
  return res;
}

static bool check_irqs(hd61700_state_t *cpu) {
  if (!(REG_IB & 0x80)) return false; // Global Interrupt Enable (GIE)
#define IRQ_ENABLED(line) ((REG_IE & (1u << ((line) + 3))) != 0)
  for (int i = 4; i >= 0; i--) {
    bool off_wake_on_int =
        (i == HD61700_ON_INT) && (cpu->pc == 0x8F6A || cpu->pc == 0x8F6B);
    bool irq_allowed = IRQ_ENABLED(i) || off_wake_on_int;
    if ((REG_IB & (1 << i)) && irq_allowed &&
        !(cpu->irq_status & (1 << i))) {
      cpu->irq_status |= (1 << i);
      push(cpu, &REG_SS, (uint8_t)(cpu->pc >> 8));
      push(cpu, &REG_SS, (uint8_t)cpu->pc);
      set_pc(cpu, irq_vector[i]);
      cpu->icount -= 12;
      return true;
    }
  }
#undef IRQ_ENABLED
  return false;
}

void hd61700_init(hd61700_state_t *cpu) {
  memset(cpu, 0, sizeof(hd61700_state_t));
}

void hd61700_reset(hd61700_state_t *cpu) {
  set_pc(cpu, 0x0000);
  cpu->flags = FLAG_SW;
  cpu->state = 0;
  cpu->irq_status = 0;
  cpu->prev_ua = 0;
  memset(cpu->regsir, 0, sizeof(cpu->regsir));
  memset(cpu->reg8bit, 0, sizeof(cpu->reg8bit));
  memset(cpu->reg16bit, 0, sizeof(cpu->reg16bit));
  memset(cpu->regmain, 0, sizeof(cpu->regmain));
}

void hd61700_set_debug(hd61700_state_t *cpu, bool enable) {
  cpu->debug_log = enable;
}

void hd61700_set_key_debug(hd61700_state_t *cpu, bool enable) {
  cpu->key_debug_log = enable;
}

void hd61700_set_lcd_debug(hd61700_state_t *cpu, bool enable) {
  cpu->lcd_debug_log = enable;
}

void hd61700_set_pc(hd61700_state_t *cpu, uint16_t pc) { set_pc(cpu, pc); }

void hd61700_timer_tick(hd61700_state_t *cpu) {
  REG_TM++;
  if ((REG_TM & 0x3f) == 60) {
    REG_TM = (REG_TM & 0xc0) + 0x40;
    if (REG_IE & (1u << (HD61700_TIMER_INT + 3))) {
      REG_IB |= (1 << HD61700_TIMER_INT);
      cpu->state &= ~CPU_SLP;
    }
  }
}

void hd61700_set_input(hd61700_state_t *cpu, int line, int state) {
#define IRQ_ENABLED(line) ((REG_IE & (1u << ((line) + 3))) != 0)
  switch (line) {
  case HD61700_ON_INT:
    if (state) {
      /* Wake source: ON key should bring CPU out of OFF/SLP state. */
      cpu->state &= ~CPU_SLP;
      /* ON event should be pending even if IE is currently masked by OFF flow. */
      REG_IB |= (1 << HD61700_ON_INT);
    }
    if (IRQ_ENABLED(line) && state)
      REG_IB |= (1 << line);
    REG_KY = (REG_KY & 0xfdff) | ((state ? 1 : 0) << 9);
    break;
  case HD61700_TIMER_INT:
  case HD61700_INT2:
  case HD61700_INT1:
    if (state) {
      cpu->state &= ~CPU_SLP;
      REG_IB |= (1 << line);
    }
    break;
  case HD61700_KEY_INT:
    /* Real machine wakes from OFF by BRK event when SW flag is ON. */
    if (state && (cpu->flags & FLAG_SW)) {
      cpu->state &= ~CPU_SLP;
      REG_IB |= (1 << HD61700_ON_INT);
    }
    if (IRQ_ENABLED(line) && state) {
      uint8_t mask = (uint8_t)(1u << line);
      REG_IB |= mask;
    }
    break;
  case HD61700_SW:
    if (state) {
      cpu->flags |= FLAG_SW;
      /* Power switch ON event can wake from OFF/sleep. */
      cpu->state &= ~CPU_SLP;
      REG_IB |= (1 << HD61700_ON_INT);
    } else {
      cpu->flags &= (uint8_t)~FLAG_SW;
    }
    break;
  }
#undef IRQ_ENABLED
}

int hd61700_execute(hd61700_state_t *cpu, int cycles, int32_t stop_pc) {
  cpu->icount = cycles;
  do {
    if (cpu->pc == (uint16_t)stop_pc)
      break;

    if (cpu->state & CPU_SLP) {
      cpu->icount -= 6;
    } else {
      check_irqs(cpu);
      uint16_t instr_pc = cpu->pc;
      cpu->last_op_len = 0; /* reset per instruction for reliable trace snapshots */
      uint8_t op = read_op(cpu);
      if (cpu->debug_log && cpu->key_debug_log && instr_pc == 0x062C) {
        cpu_log(cpu,
                "TRACE 062C: OP=0x%02X F=0x%02X IA=0x%02X IB=0x%02X IE=0x%02X "
                "KY=0x%04X IX=0x%04X IY=0x%04X IZ=0x%04X US=0x%04X SS=0x%04X "
                "R0=0x%02X R1=0x%02X R2=0x%02X R3=0x%02X",
                op, cpu->flags, REG_IA, REG_IB, REG_IE, REG_KY, REG_IX, REG_IY,
                REG_IZ, REG_US, REG_SS, READ_REG(0), READ_REG(1), READ_REG(2),
                READ_REG(3));
      }

      if (cpu->debug_log) {
        if (instr_pc == 0x9A2F || instr_pc == 0x9A3C) {
          uint16_t sbot = debug_read_bank0_u16(cpu, 0x6933u);
          uint16_t forsk = debug_read_bank0_u16(cpu, 0x6935u);
          cpu_log(cpu,
                  "BSAVE-OM %04X: OP=%02X F=%02X SBOT=%04X FORSK=%04X FREE=%04X R0=%02X R1=%02X R2=%02X R3=%02X R4=%02X R5=%02X",
                  instr_pc, op, cpu->flags, sbot, forsk,
                  (unsigned int)debug_calc_free_span(forsk, sbot), READ_REG(0),
                  READ_REG(1), READ_REG(2), READ_REG(3), READ_REG(4),
                  READ_REG(5));
        } else if (instr_pc == 0xB2A3 || instr_pc == 0xB2AB) {
          uint16_t memen = debug_read_bank0_u16(cpu, 0x6945u);
          uint16_t datdi = debug_read_bank0_u16(cpu, 0x6947u);
          cpu_log(cpu,
                  "BSAVE-OM %04X: OP=%02X F=%02X MEMEN=%04X DATDI=%04X FREE=%04X REQ=%04X R0=%02X R1=%02X R2=%02X R3=%02X R4=%02X R5=%02X R6=%02X R7=%02X",
                  instr_pc, op, cpu->flags, memen, datdi,
                  (unsigned int)debug_calc_free_span(datdi, memen),
                  (unsigned int)REG_GET16(0), READ_REG(0), READ_REG(1),
                  READ_REG(2), READ_REG(3), READ_REG(4), READ_REG(5),
                  READ_REG(6), READ_REG(7));
        } else if (instr_pc == 0xB34A || instr_pc == 0xB353) {
          uint16_t memen = debug_read_bank0_u16(cpu, 0x6945u);
          uint16_t datdi = debug_read_bank0_u16(cpu, 0x6947u);
          uint16_t basdi = debug_read_bank0_u16(cpu, 0x6949u);
          cpu_log(cpu,
                  "BSAVE-OM %04X: OP=%02X F=%02X MEMEN=%04X DATDI=%04X BASDI=%04X DIRFREE=%04X NEED=%04X R0=%02X R1=%02X R2=%02X R3=%02X R6=%02X R7=%02X",
                  instr_pc, op, cpu->flags, memen, datdi, basdi,
                  (unsigned int)debug_calc_needed_entry_space(datdi), 0x0021u,
                  READ_REG(0), READ_REG(1), READ_REG(2), READ_REG(3),
                  READ_REG(6), READ_REG(7));
        } else if (instr_pc == 0xABBD) {
          cpu_log(cpu,
                  "BSAVE-OM TRAP %04X: F=%02X R0=%02X R1=%02X R2=%02X R3=%02X R4=%02X R5=%02X R6=%02X R7=%02X R15=%02X R16=%02X IX=%04X IZ=%04X",
                  instr_pc, cpu->flags, READ_REG(0), READ_REG(1), READ_REG(2),
                  READ_REG(3), READ_REG(4), READ_REG(5), READ_REG(6),
                  READ_REG(7), READ_REG(15), READ_REG(16), REG_IX, REG_IZ);
        }
      }
      if (cpu->debug_log && cpu->key_debug_log &&
          (instr_pc == 0x060F || instr_pc == 0x0632 || instr_pc == 0x0634 ||
           instr_pc == 0x063D || instr_pc == 0x063F || instr_pc == 0x0640 ||
           instr_pc == 0x0641 || instr_pc == 0x0643 || instr_pc == 0x0646 ||
           instr_pc == 0x0668 || instr_pc == 0x066A || instr_pc == 0x0674 ||
           instr_pc == 0x0677 || instr_pc == 0x0679 || instr_pc == 0x067A)) {
        cpu_log(
            cpu,
            "KEYPATH %04X: OP=%02X F=%02X R0=%02X R1=%02X R2=%02X R3=%02X "
            "R4=%02X R5=%02X SX=%02X SY=%02X SZ=%02X IA=%02X KY=%04X IX=%04X",
            instr_pc, op, cpu->flags, READ_REG(0), READ_REG(1), READ_REG(2),
            READ_REG(3), READ_REG(4), READ_REG(5), cpu->regsir[0],
            cpu->regsir[1], cpu->regsir[2], REG_IA, REG_KY, REG_IX);
      }
      switch (op) {
      /* 0x00 - 0x0F */
      case 0x00:   /* ADC $,$ */
      case 0x01: { /* SBC $,$ */
        uint8_t arg = read_op(cpu);
        uint8_t src = READ_REG(get_sir_im8(cpu, arg));
        uint16_t res;
        if (op & 1) {
          res = READ_REG(arg) - src;
          CLEAR_FLAGS;
          CHECK_FLAG_C((int)src, READ_REG(arg)); // Borrow
        } else {
          res = READ_REG(arg) + src;
          CLEAR_FLAGS;
          CHECK_FLAG_C(res, 0xff); // Carry
        }
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ((uint8_t)res);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x08:   /* AD $,$ */
      case 0x09: { /* SB $,$ */
        uint8_t arg = read_op(cpu);
        uint8_t src = READ_REG(get_sir_im8(cpu, arg));
        uint16_t res;
        if (op & 1) {
          res = READ_REG(arg) - src;
          CLEAR_FLAGS;
          CHECK_FLAG_C((int)src, READ_REG(arg)); // Borrow
        } else {
          res = READ_REG(arg) + src;
          CLEAR_FLAGS;
          CHECK_FLAG_C(res, 0xff); // Carry
        }
        WRITE_REG(arg, (uint8_t)res);
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ((uint8_t)res);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x02: { /* LD $,$ */
        uint8_t arg = read_op(cpu);
        COPY_REG(arg, get_sir_im8(cpu, arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x03: { /* LDC $,$ */
        uint8_t arg = read_op(cpu);
        (void)get_sir_im8(cpu, arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break; 
      case 0x04:   /* ANC $,$ */
      case 0x05:   /* NAC $,$ */
      case 0x06:   /* ORC $,$ */
      case 0x07: { /* XRC $,$ */
        uint8_t arg = read_op(cpu);
        uint8_t res =
            make_logic(op, READ_REG(arg), READ_REG(get_sir_im8(cpu, arg)));
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x0a:   /* ADB $,$ */
      case 0x0b: { /* SBB $,$ */
        uint8_t arg = read_op(cpu);
        uint16_t res;
        if (op & 1)
          res = make_bcd_sub(READ_REG(arg), READ_REG(get_sir_im8(cpu, arg)));
        else
          res = make_bcd_add(READ_REG(arg), READ_REG(get_sir_im8(cpu, arg)));
        WRITE_REG(arg, res & 0xff);
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x0c:   /* AN $,$ */
      case 0x0d:   /* NA $,$ */
      case 0x0e:   /* OR $,$ */
      case 0x0f: { /* XR $,$ */
        uint8_t arg = read_op(cpu);
        uint8_t res =
            make_logic(op, READ_REG(arg), READ_REG(get_sir_im8(cpu, arg)));
        WRITE_REG(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      /* 0x10 - 0x1F */
      case 0x10: { /* ST $,($) */
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, READ_REG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x11: { /* LD $,($) */
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, off));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x12: { /* STL $ : store to LCD data area*/
        uint8_t arg = read_op(cpu);
        uint8_t data = READ_REG(arg);
        if (cpu->debug_log && cpu->lcd_debug_log) {
          cpu_log(
              cpu,
              "PPO/STL executed: PC=0x%04X OP=0x%02X ARG=0x%02X DATA=0x%02X",
              instr_pc, op, arg, data);
        }
        if (cpu->lcd_write)
          cpu->lcd_write(cpu->cb_ctx, data);
        check_optional_jr(cpu, arg); /* JR extension */
        cpu->icount -= 11;
      } break;
      case 0x13: { /* LDL $ : load from LCD data area */
        uint8_t arg = read_op(cpu);
        uint8_t res = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        WRITE_REG(arg, res);
        check_optional_jr(cpu, arg); /* JR extension */
        cpu->icount -= 11;
      } break;
      case 0x14: { /* PPO : Put LCD Control Port / PFL : Put Flag register $ */
        uint8_t arg = read_op(cpu);
        if (arg & 0x40)
          cpu->flags = (cpu->flags & 0x0f) | (READ_REG(arg) & 0xf0);
        else {
          uint8_t data = READ_REG(arg);
          if (cpu->debug_log && cpu->lcd_debug_log) {
            cpu_log(cpu,
                    "PPO executed: PC=0x%04X OP=0x%02X ARG=0x%02X DATA=0x%02X",
                    instr_pc, op, arg, data);
          }
          if (cpu->lcd_ctrl)
            cpu->lcd_ctrl(cpu->cb_ctx, data);
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x15: { /* PSR SIR(SX/SY/SZ),$ : Put Specific index Register */
        uint8_t arg = read_op(cpu);
        WRITE_SREG(arg, READ_REG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x16:   /* PST Sreg(PE/PD/IB/UA),$ : Put Status */
      case 0x17: { /* PST Sreg(IA/IE),$ : Put Status */
        uint8_t arg = read_op(cpu);
        uint8_t src = READ_REG(arg);
        uint8_t idx = GET_REG_IDX(op, arg);
        switch (idx) {
        case 0:
        case 1:
          WRITE_REG8(idx, src);
          if (cpu->port_write)
            cpu->port_write(cpu->cb_ctx, REG_PD & REG_PE);
          break;
        case 2:
          REG_IB = (REG_IB & 0x1f) | (src & 0xe0);
          break;
        case 4:
          if (cpu->debug_log && cpu->key_debug_log &&
              (((src & 0x0f) == 0x0d) || instr_pc == 0x0828 ||
               instr_pc == 0x0629 || instr_pc == 0x0634 ||
               instr_pc == 0x063B)) {
            cpu_log(
                cpu,
                "PST IA executed: PC=0x%04X OP=0x%02X ARG=0x%02X IA<=0x%02X",
                instr_pc, op, arg, src);
          }
          if (cpu->kb_write)
            cpu->kb_write(cpu->cb_ctx, src);
          WRITE_REG8(idx, src);
          break;
        case 5:
          REG_IE = src;
          cpu_log(cpu,
                  "PST IE executed: PC=0x%04X OP=0x%02X ARG=0x%02X IE=0x%02X",
                  instr_pc, op, arg, REG_IE);
          break;
        default:
          WRITE_REG8(idx, src);
          break;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x18:
      case 0x19: { /* ROD/ROU/BID/BIU */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        if (op1 == 0x00 || op1 == 0x02) {
          uint8_t src = READ_REG(arg);
          uint8_t res = (uint8_t)(src >> 1);
          if (!(op1 & 0x02))
            res |= (cpu->flags & FLAG_C) ? 0x80 : 0x00;
          WRITE_REG(arg, res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(res);
          CHECK_FLAGB_UZ_LZ(res);
          if (src & 0x01)
            SET_FLAG_C;
        } else {
          uint8_t src = READ_REG(arg);
          uint8_t res = (uint8_t)(src << 1);
          if (!(op1 & 0x02))
            res |= (cpu->flags & FLAG_C) ? 0x01 : 0x00;
          WRITE_REG(arg, res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(res);
          CHECK_FLAGB_UZ_LZ(res);
          if (src & 0x80)
            SET_FLAG_C;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1a: { /* DID/DIU/BYD/BYU */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        if (op1 == 0x00 || op1 == 0x01) {
          uint8_t res = (op1 & 0x01) ? (uint8_t)(READ_REG(arg) << 4)
                                     : (uint8_t)(READ_REG(arg) >> 4);
          WRITE_REG(arg, res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(res);
          CHECK_FLAGB_UZ_LZ(res);
        } else {
          uint8_t src = READ_REG(arg);
          WRITE_REG(arg, 0);
          if (op1 == 0x02) {
            WRITE_REG((uint8_t)(arg - 1), src);
          } else {
            WRITE_REG((uint8_t)(arg + 1), src);
          }
          CLEAR_FLAGS;
          CHECK_FLAG_Z(src);
          CHECK_FLAGB_UZ_LZ(src);
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1b: { /* CMP/INV */
        uint8_t arg = read_op(cpu);
        uint8_t res = (uint8_t)~READ_REG(arg);
        if (!(arg & 0x40))
          res = (uint8_t)(res + 1);
        WRITE_REG(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if (res || (arg & 0x40))
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1c: { /* GPO/GFL : Get Port / Get Flag register $ */
        uint8_t arg = read_op(cpu);
        if (arg & 0x40)
          WRITE_REG(arg, cpu->flags);
        else if (cpu->port_read)
          WRITE_REG(arg, cpu->port_read(cpu->cb_ctx));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1d: { /* GSR SIR,$ : Get Specific index Register */
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, READ_SREG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1e:   /* GST Sreg(PE/PD/IB/UA),$ : Get Status Register -> $ */
      case 0x1f: { /* GST Sreg(IA/IE/TM),$ : Get Status Register -> $ */
        uint8_t arg = read_op(cpu);
        uint8_t idx = GET_REG_IDX(op, arg);
        uint8_t gst = read_gst_value(cpu, idx);
        if (idx == 4 && cpu->debug_log && cpu->key_debug_log &&
            ((gst & 0x0f) == 0x0d)) {
          cpu_log(cpu,
                  "KEYSCAN GST: PC=0x%04X OP=0x%02X ARG=0x%02X IDX=%u "
                  "IA=0x%02X IB=0x%02X IE=0x%02X",
                  instr_pc, op, arg, idx, gst, REG_IB, REG_IE);
        }
        WRITE_REG(arg, gst);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      /* 0x20 - 0x2F */
      case 0x20: { /* ST $,(IX) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x21: { /* ST $,(IZ) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x22: { /* STI $,(IX)+ */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        cpu->icount -= 8;
      } break;
      case 0x23: { /* STI $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        cpu->icount -= 8;
      } break;
      case 0x24: { /* STD $,(IX) */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX, READ_REG(arg));
        cpu->icount -= 8;
      } break;
      case 0x25: { /* STD $,(IZ) */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ, READ_REG(arg));
        cpu->icount -= 8;
      } break;
      case 0x26: { /* PHS */
        push(cpu, &REG_SS, READ_REG(read_op(cpu)));
        cpu->icount -= 9;
      } break;
      case 0x27: { /* PHU */
        push(cpu, &REG_US, READ_REG(read_op(cpu)));
        cpu->icount -= 9;
      } break;
      case 0x28: { /* LD $,(IX) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x29: { /* LD $,(IZ) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x2a: { /* LDI $,(IX)+ */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        cpu->icount -= 8;
      } break;
      case 0x2b: { /* LDI $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        cpu->icount -= 8;
      } break;
      case 0x2c: { /* LDD $,(IX) */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 6;
      } break;
      case 0x2d: { /* LDD $,(IZ) */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 6;
      } break;
      case 0x2e: { /* PPS : pop by system stack pointer */
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_SS));
        cpu->icount -= 11;
      } break;
      case 0x2f: { /* PPU : pop by user stack pointer */
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_US));
        cpu->icount -= 11;
      } break;

      /* 0x30 - 0x3F */
      case 0x30:   /* JP Z,IM16 */
      case 0x31:   /* JP NC,IM16 */
      case 0x32:   /* JP LZ,IM16 */
      case 0x33:   /* JP UZ,IM16 */
      case 0x34:   /* JP NZ,IM16 */
      case 0x35:   /* JP C,IM16 */
      case 0x36:   /* JP NLZ,IM16 */
      case 0x37: { /* JP IM16 */
        uint16_t addr = read_imm16_aligned(cpu);
        if (cpu->debug_log)
          cpu_log(cpu, "JP 0x%04X executed at 0x%04X", addr, instr_pc);
        if (check_cond(cpu, op))
          set_pc(cpu, addr);
        cpu->icount -= 3;
      } break;
      case 0x38:    /* ADC (IX+),$ */
      case 0x3a:    /* SBC (IX+),$ */
      case 0x3c:    /* AD (IX+),$ */
      case 0x3e: {  /* SB (IX+),$ */  
        uint8_t arg = read_op(cpu);
        uint16_t addr = (uint16_t)(REG_IX + get_sign_mreg(cpu, arg));
        uint8_t src = mem_readbyte(cpu, REG_UA, addr);
        uint16_t res = (uint16_t)(src + ((op & 0x02) ? -(int)READ_REG(arg)
                                                     : +(int)READ_REG(arg)));
        if (op & 0x04) {
          mem_writebyte(cpu, REG_UA, addr, (uint8_t)res);
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;
      case 0x39:   /* ADC (IZ+),$ */
      case 0x3b:   /* SBC (IZ+),$ */
      case 0x3d:   /* AD (IZ+),$ */
      case 0x3f: { /* SB (IZ+),$ */
        uint8_t arg = read_op(cpu);
        uint16_t addr = (uint16_t)(REG_IZ + get_sign_mreg(cpu, arg));
        uint8_t src = mem_readbyte_iz(cpu, REG_UA, addr);
        uint16_t res = (uint16_t)(src + ((op & 0x02) ? -(int)READ_REG(arg)
                                                     : +(int)READ_REG(arg)));
        if (op & 0x04) {
          mem_writebyte_iz(cpu, REG_UA, addr, (uint8_t)res);
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;

      /* 0x40 - 0x4F */
      case 0x40:   /* ADC $,IM8 */
      case 0x41:   /* SBC $,IM8 */
      case 0x48:   /* AD $,IM8 */
      case 0x49: { /* SB $,IM8 */
        uint8_t arg = read_op(cpu);
        uint8_t src = read_op(cpu);
        uint16_t res;
        if (op & 1) {
          res = READ_REG(arg) - src;
          CLEAR_FLAGS;
          CHECK_FLAG_C((int)src, READ_REG(arg)); // Borrow
        } else {
          res = READ_REG(arg) + src;
          CLEAR_FLAGS;
          CHECK_FLAG_C(res, 0xff); // Carry
        }
        if (op & 0x08)
          WRITE_REG(arg, (uint8_t)res);
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ((uint8_t)res);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x42: { /* LD $,IM8 */
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, read_op(cpu));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x43: { /* LDC $,IM8 */
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      // case 0x43: { uint8_t arg = read_op(cpu); (void)read_op(cpu);
      // cpu->icount -= 3; } break; /* LDC imm (no-op) */
      case 0x4a:   /* ADB $,IM8 */
      case 0x4b: { /* SBB $,IM8 */
        uint8_t arg = read_op(cpu);
        uint8_t src = read_op(cpu);
        uint16_t res;
        if (op & 0x01) {
          res = make_bcd_sub(READ_REG(arg), src);
        } else {
          res = make_bcd_add(READ_REG(arg), src);
        }
        WRITE_REG(arg, (uint8_t)res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x44:   /* ANC $,IM8 */
      case 0x45:   /* NAC $,IM8 */
      case 0x46:   /* ORC $,IM8 */
      case 0x47: { /* XRC $,IM8 */
        uint8_t arg = read_op(cpu);
        uint8_t res = make_logic(op, READ_REG(arg), read_op(cpu));
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x4c:   /* AN $,IM8 */
      case 0x4d:   /* NA $,IM8 */
      case 0x4e:   /* OR $,IM8 */
      case 0x4f: { /* XR $,IM8 */
        uint8_t arg = read_op(cpu);
        uint8_t res = make_logic(op, READ_REG(arg), read_op(cpu));
        WRITE_REG(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      /* 0x50 - 0x5F */
      case 0x50: { /* ST IM8,($SIR) */
        uint8_t arg = read_op(cpu);
        uint8_t imm = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, imm);
        cpu->icount -= 3;
      } break;
      case 0x51: { /* ST IM8,$ */
        uint8_t arg = read_op(cpu);
        uint8_t imm = read_op(cpu);
        write_sreg_or_reg(cpu, arg, imm);
        cpu->icount -= 3;
      } break;
      case 0x52: { /* STL IM8 : Store data to LCD */
        uint8_t imm = read_op(cpu);
        if (cpu->debug_log && cpu->lcd_debug_log) {
          cpu_log(cpu, "PPO/STL imm executed: PC=0x%04X OP=0x%02X IMM=0x%02X",
                  instr_pc, op, imm);
        }
        if (cpu->lcd_write)
          cpu->lcd_write(cpu->cb_ctx, imm);
        cpu->icount -= 12;
      } break;
      case 0x53: { /* Compatible with 13H no jump extension (LDL $) */
        uint8_t arg = read_op(cpu);
        uint8_t res = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        WRITE_REG(arg, res);
        /* No check_optional_jr(): 0x53 variant omits JR extension */
        cpu->icount -= 11;
      } break;
      case 0x54: { /* PPO/PFL IM8 */
        uint8_t arg = read_op(cpu);
        uint8_t imm = read_op(cpu);
        if (arg & 0x40)
          cpu->flags = (cpu->flags & 0x0f) | (imm & 0xf0);
        else {
          if (cpu->debug_log && cpu->lcd_debug_log) {
            cpu_log(
                cpu,
                "PPO(imm) executed: PC=0x%04X OP=0x%02X ARG=0x%02X IMM=0x%02X",
                instr_pc, op, arg, imm);
          }
          if (cpu->lcd_ctrl)
            cpu->lcd_ctrl(cpu->cb_ctx, imm);
        }
        cpu->icount -= 3;
      } break;
      case 0x55: { /* PSR SX/SY/SZ,IM5 : Put Specific Index Register */
        uint8_t arg = read_op(cpu);
        WRITE_SREG(arg, arg);
        cpu->icount -= 3;
      } break;
      case 0x56:   /* PST PE/PD/IB/UA,IM5 : Put Status Register */
      case 0x57: { /* PST IA/IE,IM5 : Put Status Register */
        uint8_t arg = read_op(cpu);
        uint8_t src = read_op(cpu);
        uint8_t idx = GET_REG_IDX(op, arg);
        switch (idx) {
        case 0:
        case 1:
          WRITE_REG8(idx, src);
          if (cpu->port_write)
            cpu->port_write(cpu->cb_ctx, REG_PD & REG_PE);
          break;
        case 2:
          REG_IB = (REG_IB & 0x1f) | (src & 0xe0);
          break;
        case 3:
          REG_UA = src;
          break;
        case 4:
          if (cpu->debug_log && cpu->key_debug_log &&
              (((src & 0x0f) == 0x0d) || instr_pc == 0x0828 ||
               instr_pc == 0x0629 || instr_pc == 0x0634 ||
               instr_pc == 0x063B)) {
            cpu_log(cpu,
                    "PST IA(imm) executed: PC=0x%04X OP=0x%02X ARG=0x%02X "
                    "IA<=0x%02X",
                    instr_pc, op, arg, src);
          }
          if (cpu->kb_write)
            cpu->kb_write(cpu->cb_ctx, src);
          WRITE_REG8(idx, src);
          break;
        case 5:
          REG_IB &= (uint8_t)(0xe0 | (src >> 3));
          cpu->irq_status &= (uint8_t)(src >> 3);
          REG_IE = src;
          break;
        case 6:
        case 7:
          break;
        default:
          WRITE_REG8(idx, src);
          break;
        }
        cpu->icount -= 3;
      } break;
      case 0x58: /* BUPS IM8 */
      case 0x59: /* BDNS IM8 */ {
        uint8_t arg = read_op(cpu);
        uint16_t res;
        for (;;) {
          uint8_t tmp = mem_readbyte(cpu, REG_UA, REG_IX);
          mem_writebyte_iz(cpu, REG_UA, REG_IZ, tmp);
          res = (uint16_t)(tmp - arg);
          if (REG_IX == REG_IY || !res)
            break;
          REG_IX += (op & 1) ? -1 : +1;
          REG_IZ += (op & 1) ? -1 : +1;
          cpu->icount -= 6;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;
      case 0x5a: { /* Compatible with 1AH no jump extension DID/DIU/BYD/BYU */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        if (op1 == 0x00 || op1 == 0x01) {
          uint8_t res = (op1 & 0x01) ? (uint8_t)(READ_REG(arg) << 4)
                                     : (uint8_t)(READ_REG(arg) >> 4);
          WRITE_REG(arg, res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(res);
          CHECK_FLAGB_UZ_LZ(res);
        } else {
          uint8_t src = READ_REG(arg);
          WRITE_REG(arg, 0);
          if (op1 == 0x02) {
            WRITE_REG((uint8_t)(arg - 1), src);
          } else {
            WRITE_REG((uint8_t)(arg + 1), src);
          }
          CLEAR_FLAGS;
          CHECK_FLAG_Z(src);
          CHECK_FLAGB_UZ_LZ(src);
        }
        /* no check_optional_jr: no jump extension variant */
        cpu->icount -= 3;
      } break;
      case 0x5b: {  /* Compatible with 1BH no jump extension CMP/INV */
        uint8_t arg = read_op(cpu);
        uint8_t res = (uint8_t)~READ_REG(arg);
        if (!(arg & 0x40))
          res = (uint8_t)(res + 1);
        WRITE_REG(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGB_UZ_LZ(res);
        if (res || (arg & 0x40))
          SET_FLAG_C;
        /* no check_optional_jr: no jump extension variant */
        cpu->icount -= 3;
      } break;
      case 0x5c: /* SUP IM8 : Speed UP */
      case 0x5d: /* SDN IM8 : Speed DowN */ {
        uint8_t arg = read_op(cpu);
        uint16_t res;
        for (;;) {
          res = (uint16_t)(mem_readbyte(cpu, REG_UA, REG_IX) - arg);
          if (REG_IX == REG_IY || !res)
            break;
          REG_IX += (op & 1) ? -1 : +1;
          cpu->icount -= 6;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;
      case 0x5e:   /* Compatible with 1EH no jump extension : GST PE/PD/IB/UA */
      case 0x5f: { /* Compatible with 1FH no jump extension : GST IA/IE/TM */
        uint8_t arg = read_op(cpu);
        uint8_t idx = GET_REG_IDX(op, arg);
        uint8_t gst = read_gst_value(cpu, idx);
        if (idx == 4 && cpu->debug_log && cpu->key_debug_log &&
            ((gst & 0x0f) == 0x0d)) {
          cpu_log(cpu,
                  "KEYSCAN GST: PC=0x%04X OP=0x%02X ARG=0x%02X IDX=%u "
                  "IA=0x%02X IB=0x%02X IE=0x%02X",
                  instr_pc, op, arg, idx, gst, REG_IB, REG_IE);
        }
        WRITE_REG(arg, gst);
        /* no check_optional_jr: no jump extension variant */
        cpu->icount -= 3;
      } break;

      /* 0x60 - 0x6F */
      case 0x60:   /* ST $,(IX) */
      case 0x62: { /* STI $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_im8(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x61:   /* ST $,(IZ) */
      case 0x63: { /* STI $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_im8(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x64: { /* STD $,(IX) */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_im8(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX, READ_REG(arg));
        cpu->icount -= 6;
      } break;
      case 0x65: { /* STD $,(IZ) */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_im8(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ, READ_REG(arg));
        cpu->icount -= 6;
      } break;
      case 0x66: { /* Compatible with 26H but 3byte instruction : PHS$ */
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        push(cpu, &REG_SS, READ_REG(arg));
        cpu->icount -= 9;
      } break;
      case 0x67: { /* Compatible with 27H but 3byte instruction : PHU$ */
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        push(cpu, &REG_US, READ_REG(arg));
        cpu->icount -= 9;
      } break;
      case 0x68: { /* LD $,(IX) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x69: { /* LD $,(IZ) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x6a: { /* LDI $,(IX)+ */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        cpu->icount -= 8;
      } break;
      case 0x6b: { /* LDI $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        cpu->icount -= 8;
      } break;
      case 0x6c: { /* LDD $,(IX) */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 6;
      } break;
      case 0x6d: { /* LDD $,(IZ) */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 6;
      } break;
      case 0x6e: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_SS));
        cpu->icount -= 11;
      } break;
      case 0x6f: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_US));
        cpu->icount -= 11;
      } break;

      /* 0x70 - 0x7F */
      case 0x70:   /* CAL Z,IM16 */
      case 0x71:   /* CAL NC,IM16 */
      case 0x72:   /* CAL LZ,IM16 */
      case 0x73:   /* CAL UZ,IM16 */
      case 0x74:   /* CAL NZ,IM16 */
      case 0x75:   /* CAL C,IM16 */
      case 0x76:   /* CAL NLZ,IM16 */
      case 0x77: { /* CAL IM16 */
        uint16_t addr = read_imm16_aligned(cpu);
        if (check_cond(cpu, op)) {
          /* CAL hook: if registered, intercept and skip normal push/set_pc.
           * PC already points to the next instruction after operands. */
          if (cpu->call_hook && cpu->call_hook(cpu->cb_ctx, addr)) {
            cpu->icount -= 12;
          } else {
            uint16_t ret = (uint16_t)(cpu->pc - 1);
            push(cpu, &REG_SS, (uint8_t)(ret >> 8));
            push(cpu, &REG_SS, (uint8_t)ret);
            set_pc(cpu, addr);
            cpu->icount -= 12;
          }
        }
        cpu->icount -= 3;
      } break;
      case 0x78:   /* ADC (IX+IM8),$ */
      case 0x7a:   /* SBC (IX+IM8),$ */
      case 0x7c:   /* AD (IX+IM8),$ */
      case 0x7e: { /* SB (IX+IM8),$ */
        uint8_t arg = read_op(cpu);
        uint16_t addr = (uint16_t)(REG_IX + get_sign_im8(cpu, arg));
        uint8_t src = mem_readbyte(cpu, REG_UA, addr);
        uint16_t res = (uint16_t)(src + ((op & 0x02) ? -(int)READ_REG(arg)
                                                     : +(int)READ_REG(arg)));
        if (op & 0x04) {
          mem_writebyte(cpu, REG_UA, addr, (uint8_t)res);
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;
      case 0x79:   /* ADC (IZ+IM8),$ */
      case 0x7b:   /* SBC (IZ+IM8),$ */
      case 0x7d:   /* AD (IZ+IM8),$ */
      case 0x7f: { /* SB (IZ+IM8),$ */
        uint8_t arg = read_op(cpu);
        uint16_t addr = (uint16_t)(REG_IZ + get_sign_im8(cpu, arg));
        uint8_t src = mem_readbyte_iz(cpu, REG_UA, addr);
        uint16_t res = (uint16_t)(src + ((op & 0x02) ? -(int)READ_REG(arg)
                                                     : +(int)READ_REG(arg)));
        if (op & 0x04) {
          mem_writebyte_iz(cpu, REG_UA, addr, (uint8_t)res);
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;

      /* 0x80 - 0x8F (16-bit arith) */
      case 0x80:   /* ADCW $,$/SIR */
      case 0x81: { /* SBCW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        uint32_t d = REG_GET16(arg);
        uint32_t s = REG_GET16(src);
        uint32_t res = (op & 1) ? (d - s) : (d + s);
        if (cpu->debug_log && cpu->key_debug_log && instr_pc == 0x063F) {
          cpu_log(
              cpu,
              "KEYCMP 063F: ARG=%02X SRC=%02X D=%04X S=%04X RES=%04X "
              "R0=%02X R1=%02X R2=%02X R3=%02X SZ=%02X SRC_LO=%02X SRC_HI=%02X",
              arg, src, (uint16_t)d, (uint16_t)s, (uint16_t)res, READ_REG(0),
              READ_REG(1), READ_REG(2), READ_REG(3), cpu->regsir[2],
              READ_REG(src), READ_REG((uint8_t)(src + 1)));
        }
        if (op & 0x08)
          REG_PUT16(arg, (uint16_t)res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint16_t)res);
        CHECK_FLAGW_UZ_LZ((uint16_t)res);
        CHECK_FLAG_C(res, 0xffff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x82: { /* LDW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        COPY_REG(arg, src);
        COPY_REG(arg + 1, src + 1);
        /* missing JR handling was causing subsequent bytes to be mis-fetched */
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x83: { /* LDCW $,$/SIR */
        uint8_t arg = read_op(cpu);
        (void)get_sir_im8(cpu, arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x84:   /* ANCW $,$/SIR */
      case 0x85:   /* NACW $,$/SIR */
      case 0x86:   /* ORCW $,$/SIR */
      case 0x87: { /* XRCW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        uint16_t d = REG_GET16(arg);
        uint16_t s = REG_GET16(src);
        uint16_t res = (uint16_t)(((op & 3) == 0)   ? (d & s)
                                  : ((op & 3) == 1) ? ~(d & s)
                                  : ((op & 3) == 2) ? (d | s)
                                                    : (d ^ s));
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGW_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x88:   /* ADW $,$/SIR */
      case 0x89: { /* SBW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        uint32_t d = REG_GET16(arg);
        uint32_t s = REG_GET16(src);
        uint32_t res = (op & 1) ? (d - s) : (d + s);
        if (op & 0x08)
          REG_PUT16(arg, (uint16_t)res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint16_t)res);
        CHECK_FLAGW_UZ_LZ((uint16_t)res);
        CHECK_FLAG_C(res, 0xffff);
        /* 0x88/0x89 keep optional-JR semantics; only 0xB8-0xBF differ. */
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x8a:   /* ADBW $,$/SIR */
      case 0x8b: { /* SBBW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        uint16_t res0;
        uint16_t res1;
        if (op & 0x01) {
          res0 = make_bcd_sub(READ_REG(arg), READ_REG(src));
        } else {
          res0 = make_bcd_add(READ_REG(arg), READ_REG(src));
        }
        WRITE_REG(arg, (uint8_t)res0);

        res1 = (res0 > 0xff) ? 1 : 0;
        if (op & 0x01) {
          res1 = make_bcd_sub(READ_REG(arg + 1),
                              (uint8_t)(READ_REG(src + 1) + res1));
        } else {
          res1 = make_bcd_add(READ_REG(arg + 1),
                              (uint8_t)(READ_REG(src + 1) + res1));
        }
        WRITE_REG(arg + 1, (uint8_t)res1);

        CLEAR_FLAGS;
        CHECK_FLAG_Z((res0 || res1));
        CHECK_FLAGB_UZ_LZ(res1);
        CHECK_FLAG_C(res1, 0xff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x8c:   /* ANW $,$/SIR */
      case 0x8d:   /* NAW $,$/SIR */
      case 0x8e:   /* ORW $,$/SIR */
      case 0x8f: { /* XRW $,$/SIR */
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        uint16_t d = REG_GET16(arg);
        uint16_t s = REG_GET16(src);
        uint16_t res = (uint16_t)(((op & 3) == 0)   ? (d & s)
                                  : ((op & 3) == 1) ? ~(d & s)
                                  : ((op & 3) == 2) ? (d | s)
                                                    : (d ^ s));
        REG_PUT16(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z(res);
        CHECK_FLAGW_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;

      /* 0x90 - 0x9F */
      case 0x90: { /* STW $,($/SIR) */
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, (uint16_t)(off + 1), READ_REG(arg + 1));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x91: { /* LDW $,($/SIR) */
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, off));
        WRITE_REG(arg + 1, mem_readbyte(cpu, REG_UA, (uint16_t)(off + 1)));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x92: { /* STLW $ */
        uint8_t arg = read_op(cpu);
        uint8_t data0 = READ_REG(arg);
        uint8_t data1 = READ_REG(arg + 1);
        if (cpu->debug_log && cpu->lcd_debug_log) {
          cpu_log(cpu,
                  "PPO/STLW executed: PC=0x%04X OP=0x%02X ARG=0x%02X "
                  "DATA=[0x%02X,0x%02X]",
                  instr_pc, op, arg, data0, data1);
        }
        if (cpu->lcd_write) {
          cpu->lcd_write(cpu->cb_ctx, data0);
          cpu->lcd_write(cpu->cb_ctx, data1);
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 19;
      } break;
      case 0x93: { /* LDLW $ : Load LCD Control Port Word */
        uint8_t arg = read_op(cpu);
        uint8_t d0 = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        uint8_t d1 = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        WRITE_REG(arg, d0);
        WRITE_REG(arg + 1, d1);
        check_optional_jr(cpu, arg);
        cpu->icount -= 19;
      } break;
      case 0x94:   /* PPOW $ : Put LCD Control Port Word */
      case 0x9c: { /* GPOW/GFLW $ : Get Port Word / Get Flag Register Word */
        uint8_t arg = read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x95:   /* PSRW SX/SY/SZ,$ : Put Specific Index Register Word */
      case 0x9d: { /* GSRW SX/SY/SZ,$ : Get Specific Index Register Word */
        uint8_t arg = read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x96:   /* PRE IX/IY/IZ/US,$ */
      case 0x97: { /* PRE SS,$*/
        uint8_t arg = read_op(cpu);
        uint16_t *t = get_pre_target(cpu, op, arg);
        *t = REG_GET16(arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x98:   /* RODW/ROUW/BIDW/BIUW $ */
      case 0x99: { /* Compatible with 98H RODW/ROUW/BIDW/BIUW */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        if (op1 == 0x00 || op1 == 0x02) {
          uint16_t src = REG_GET16((uint8_t)(arg - 1));
          uint16_t res = (uint16_t)(src >> 1);
          if (!(op1 & 0x02))
            res |= (cpu->flags & FLAG_C) ? 0x8000 : 0x0000;
          REG_PUT16((uint8_t)(arg - 1), res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z((uint16_t)res);
          CHECK_FLAGB_UZ_LZ(res);
          if (src & 0x0001)
            SET_FLAG_C;
        } else {
          uint16_t src = REG_GET16(arg);
          uint16_t res = (uint16_t)(src << 1);
          if (!(op1 & 0x02))
            res |= (cpu->flags & FLAG_C) ? 0x0001 : 0x0000;
          REG_PUT16(arg, res);
          CLEAR_FLAGS;
          CHECK_FLAG_Z((uint16_t)res);
          CHECK_FLAGW_UZ_LZ(res);
          if (src & 0x8000)
            SET_FLAG_C;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 11;
      } break;
      case 0x9a: { /* DIDW/DIUW/BYDW/BYUW */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        if (op1 == 0x00) {
          uint16_t src = (uint16_t)(REG_GET16((uint8_t)(arg - 1)) >> 4);
          REG_PUT16((uint8_t)(arg - 1), src);
          CLEAR_FLAGS;
          CHECK_FLAG_Z((uint16_t)src);
          CHECK_FLAGB_UZ_LZ(src);
        } else if (op1 == 0x01) {
          uint16_t src = (uint16_t)(REG_GET16(arg) << 4);
          REG_PUT16(arg, src);
          CLEAR_FLAGS;
          CHECK_FLAG_Z((uint16_t)src);
          CHECK_FLAGW_UZ_LZ(src);
        } else if (op1 == 0x02) {
          uint8_t src = READ_REG(arg);
          WRITE_REG(arg, 0);
          WRITE_REG((uint8_t)(arg - 1), src);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(src);
          CHECK_FLAGB_UZ_LZ(src);
        } else {
          uint8_t src = READ_REG(arg);
          WRITE_REG(arg, 0);
          WRITE_REG((uint8_t)(arg + 1), src);
          CLEAR_FLAGS;
          CHECK_FLAG_Z(src);
          CHECK_FLAGB_UZ_LZ(src);
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 11;
      } break;
      case 0x9b: { /* CMPW/INVW */
        uint8_t arg = read_op(cpu);
        uint16_t res = (uint16_t)~REG_GET16(arg);
        if (!(arg & 0x40))
          res = (uint16_t)(res + 1);
        REG_PUT16(arg, res);
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint16_t)res);
        CHECK_FLAGW_UZ_LZ(res);
        if (res || (arg & 0x40))
          SET_FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 11;
      } break;
      case 0x9e:   /* GRE IX/IY/IZ/US,$ : Get Status Register */
      case 0x9f: { /* GRE SS/KY,$ : Get Status Register */
        uint8_t arg = read_op(cpu);
        uint8_t idx = GET_REG_IDX(op, arg);
        uint16_t src;
        uint16_t port = 0;
        if (idx >= 5) {
          /* GRE KY: refresh key matrix and merge with KY status bits. */
          port = cpu->kb_read ? cpu->kb_read(cpu->cb_ctx) : 0;
          src = (REG_KY & 0x0f00) | (port & 0xf0ff);
          REG_KY = src;
          if (cpu->debug_log && cpu->key_debug_log &&
              (((src & 0xf0ff) != 0) || instr_pc == 0x0628 ||
               instr_pc == 0x063A)) {
            cpu_log(cpu,
                    "KEYSCAN GRE: PC=0x%04X OP=0x%02X ARG=0x%02X IDX=%u "
                    "IA=0x%02X PORT=0x%04X KY=0x%04X IB=0x%02X IE=0x%02X",
                    instr_pc, op, arg, idx, REG_IA, port, REG_KY, REG_IB,
                    REG_IE);
          }
        } else {
          uint16_t *t = get_pre_target(cpu, op, arg);
          src = *t;
        }
        REG_PUT16(arg, src);
        if (cpu->debug_log && cpu->key_debug_log && instr_pc == 0x063A) {
          cpu_log(cpu,
                  "KEYGRE 063A: OP=%02X ARG=%02X IDX=%u SRC=%04X PORT=%04X "
                  "KY=%04X R0=%02X R1=%02X SZ=%02X",
                  op, arg, idx, src, port, REG_KY, READ_REG(0), READ_REG(1),
                  cpu->regsir[2]);
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      /* 0xa0 - 0xaf */
      case 0xa0:   /* STW $,(IX) */
      case 0xa2: { /* STIW $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg + 1));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 11;
      } break;
      case 0xa1:   /* STW $,(IZ) */
      case 0xa3: { /* STIW $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg + 1));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 11;
      } break;
      case 0xa4: { /* STDW $,(IX)- */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX--, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, REG_IX, READ_REG(arg - 1));
        cpu->icount -= 9;
      } break;
      case 0xa5: { /* STDW $,(IZ)- */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ--, READ_REG(arg));
        mem_writebyte_iz(cpu, REG_UA, REG_IZ, READ_REG(arg - 1));
        cpu->icount -= 9;
      } break;
      case 0xa6:   /* PHSW $ */
      case 0xa7:   /* PHUW $ */
      case 0xae:   /* PPSW $ */
      case 0xaf: { /* PPUW $ */
        uint8_t arg = read_op(cpu);
        if (op == 0xa6) {
          push(cpu, &REG_SS, READ_REG(arg));
          push(cpu, &REG_SS, READ_REG(arg - 1));
        } else if (op == 0xa7) {
          push(cpu, &REG_US, READ_REG(arg));
          push(cpu, &REG_US, READ_REG(arg - 1));
        } else if (op == 0xae) {
          WRITE_REG(arg, pop(cpu, &REG_SS));
          WRITE_REG(arg + 1, pop(cpu, &REG_SS));
        } else {
          WRITE_REG(arg, pop(cpu, &REG_US));
          WRITE_REG(arg + 1, pop(cpu, &REG_US));
        }
        cpu->icount -= 3;
      } break;
      case 0xa8: { /* LDW $,(IX) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        WRITE_REG(arg + 1, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 11;
      } break;
      case 0xa9: { /* LDW $,(IZ) */
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        WRITE_REG(arg + 1, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 11;
      } break;
      case 0xaa: { /* LDIW $,(IX)+ */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        WRITE_REG(arg + 1, mem_readbyte(cpu, REG_UA, REG_IX++));
        cpu->icount -= 11;
      } break;
      case 0xab: { /* LDIW $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        WRITE_REG(arg + 1, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        cpu->icount -= 11;
      } break;
      case 0xac: { /* LDDW $,(IX)- */
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX--));
        WRITE_REG(arg - 1, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 9;
      } break;
      case 0xad: { /* LDDW $,(IZ)- */
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ--));
        WRITE_REG(arg - 1, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 9;
      } break;

      /* 0xB0 - 0xB7 (JR) */
      case 0xb0:    /* JR Z,+IM7 */
      case 0xb1:    /* JR NC,+IM7 */
      case 0xb2:    /* JR LZ,+IM7 */
      case 0xb3:    /* JR UZ,+IM7 */
      case 0xb4:    /* JR NZ,+IM7 */
      case 0xb5:    /* JR C,+IM7 */
      case 0xb6:    /* JR NLZ,+IM7 */
      case 0xb7: {  /* JR +IM7 */
        uint8_t arg = read_op(cpu);
        if (check_cond(cpu, op)) {
          uint32_t npc = (cpu->pc - 1) + get_im_7(arg);
          set_pc(cpu, (int32_t)npc);
        }
        cpu->icount -= 3;
      } break;
      case 0xb8:   /* ADCW (IX+$/SIR),$ */
      case 0xb9:   /* ADCW (IZ+$/SIR),$ */
      case 0xba:   /* SBCW (IX+$/SIR),$ */
      case 0xbb:   /* SBCW (IZ+$/SIR),$ */
      case 0xbc:   /* ADW (IX+$/SIR),$ */
      case 0xbd:   /* ADW (IZ+$/SIR),$ */
      case 0xbe:   /* SBW (IX+$/SIR),$ */
      case 0xbf: { /* SBW (IZ+$/SIR),$ */
        uint8_t arg = read_op(cpu);
        bool use_iz = (op & 0x01) != 0;
        uint16_t addr = use_iz ? REG_IZ : REG_IX;
        addr = (uint16_t)(addr + get_sign_mreg(cpu, arg));
        uint8_t m0 = use_iz ? mem_readbyte_iz(cpu, REG_UA, addr)
                            : mem_readbyte(cpu, REG_UA, addr);
        uint8_t m1 = use_iz ? mem_readbyte_iz(cpu, REG_UA, (uint16_t)(addr + 1))
                            : mem_readbyte(cpu, REG_UA, (uint16_t)(addr + 1));
        uint16_t y0;
        uint16_t y1;
        uint16_t carry;
        if (op & 0x02) {
          y0 = (uint16_t)(m0 - READ_REG(arg));
          carry = (y0 > 0xff) ? 1 : 0;
          y1 = (uint16_t)(m1 - READ_REG((uint8_t)(arg + 1)) - carry);
        } else {
          y0 = (uint16_t)(m0 + READ_REG(arg));
          carry = (y0 > 0xff) ? 1 : 0;
          y1 = (uint16_t)(m1 + READ_REG((uint8_t)(arg + 1)) + carry);
        }
        if (op & 0x04) {
          if (use_iz) {
            mem_writebyte_iz(cpu, REG_UA, addr, (uint8_t)y0);
            mem_writebyte_iz(cpu, REG_UA, (uint16_t)(addr + 1),
                             (uint8_t)y1);
          } else {
            mem_writebyte(cpu, REG_UA, addr, (uint8_t)y0);
            mem_writebyte(cpu, REG_UA, (uint16_t)(addr + 1),
                          (uint8_t)y1);
          }
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)((uint8_t)y0 | (uint8_t)y1));
        CHECK_FLAGB_UZ_LZ((uint8_t)y1);
        CHECK_FLAG_C(y1, 0xff);
        /* 0xB8-0xBF use arg bit7 as indexed-address sign, not optional-JR. */
        cpu->icount -= 8;
      } break;

      /* 0xC0 - 0xCF*/
      case 0xc0:   /* ADBCM $,$/SIR,IM3 */
      case 0xc1:   /* SBBCM $,$/SIR,IM3 */
      case 0xc8:   /* ADBM $,$/SIR,IM3 */
      case 0xc9: { /* SBBM $,$/SIR,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t sec = (arg >> 5) & 0x03;
        uint8_t src = (sec == 0x03) ? (ext & 0x1f) : READ_SREG(arg);
        uint8_t carry = 0;
        uint8_t f = 0;
        uint16_t res = 0;
        for (uint8_t n = 0; n < cnt; n++) {
          uint8_t d = READ_REG(arg + n);
          uint8_t s = READ_REG(src + n);
          if (op & 0x01)
            res = make_bcd_sub(d, (uint8_t)(s + carry));
          else
            res = make_bcd_add(d, (uint8_t)(s + carry));
          carry = (res > 0xff) ? 1 : 0;
          if (op >= 0xc8)
            WRITE_REG(arg + n, (uint8_t)res);
          f |= (uint8_t)res;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z(f);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xc2: { /* LDM $,$/SIR,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t sec = (arg >> 5) & 0x03;
        uint8_t src = (sec == 0x03) ? (ext & 0x1f) : READ_SREG(arg);
        for (uint8_t n = 0; n < cnt; n++)
          COPY_REG(arg + n, src + n);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xc3: { /* LDCM $,$/SIR,IM3 */
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xc4:   /* ANCM $,$/SIR,IM3 */
      case 0xc5:   /* NACM $,$/SIR,IM3 */
      case 0xc6:   /* ORCM $,$/SIR,IM3 */
      case 0xc7:   /* XRCM $,$/SIR,IM3 */
      case 0xcc:   /* ANM $,$/SIR,IM3 */
      case 0xcd:   /* NAM $,$/SIR,IM3 */
      case 0xce:   /* ORM $,$/SIR,IM3 */
      case 0xcf: { /* XRM $,$/SIR,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t sec = (arg >> 5) & 0x03;
        uint8_t src = (sec == 0x03) ? (ext & 0x1f) : READ_SREG(arg);
        uint8_t f = 0;
        uint8_t res = 0;
        for (uint8_t n = 0; n < cnt; n++) {
          res = make_logic(op, READ_REG(arg + n), READ_REG(src + n));
          if (op >= 0xcc)
            WRITE_REG(arg + n, res);
          f |= res;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z(f);
        CHECK_FLAGB_UZ_LZ(res);
        if ((op & 3) == 1 || (op & 3) == 2)
          cpu->flags |= FLAG_C;
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xca:   /* ADBM $,IM5,IM3*/
      case 0xcb: { /* SBBM $,IM5,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t imm = ext & 0x1f;
        uint8_t src = imm;
        uint8_t f = 0;
        uint16_t res = 0;
        for (uint8_t n = 0; n < cnt; n++) {
          uint8_t d = READ_REG(arg + n);
          if (op & 1)
            res = make_bcd_sub(d, src);
          else
            res = make_bcd_add(d, src);
          WRITE_REG(arg + n, (uint8_t)res);
          src = (res > 0xff) ? 1 : 0;
          f |= (uint8_t)res;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z(f);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;

      /* 0xD0 - 0xDF */
      case 0xd0: { /* STW IM16,(SIR) */
        uint8_t arg = read_op(cpu);
        uint8_t lo = read_op(cpu);
        uint8_t hi = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, lo);
        mem_writebyte(cpu, REG_UA, (uint16_t)(off + 1), hi);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0xd1: { /* LDW IM16,(SIR) */ 
        uint8_t arg = read_op(cpu);
        uint8_t lo = read_op(cpu);
        uint8_t hi = read_op(cpu);
        REG_PUT16(arg, (uint16_t)(lo | (hi << 8)));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      case 0xd2: { /* STLM $,IM3 : Store LCD Data Port Multibyte */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        if (cpu->debug_log && cpu->lcd_debug_log) {
          cpu_log(cpu,
                  "PPO/STLM executed: PC=0x%04X OP=0x%02X ARG=0x%02X "
                  "EXT=0x%02X CNT=%u",
                  instr_pc, op, arg, ext, cnt);
        }
        for (uint8_t n = 0; n < cnt; n++) {
          if (cpu->lcd_write)
            cpu->lcd_write(cpu->cb_ctx, READ_REG(arg + n));
          cpu->icount -= 8;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0xd3: { /* LDLM $,IM3 : Load LCD Data Port Multibyte */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        for (uint8_t n = 0; n < cnt; n++) {
          uint8_t d = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
          WRITE_REG(arg + n, d);
          cpu->icount -= 8;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0xd4: { /* PPOM $,IM3 : Put LCD Control Port Multibyte */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t idx = (arg >> 5) & 0x07;
        for (uint8_t n = 0; n < cnt; n++) {
          uint8_t data = READ_REG(arg + n);
          uint8_t target_idx = (idx + n) & 0x07;
          switch (target_idx) {
          case 0:
          case 1:
            WRITE_REG8(target_idx, data);
            if (target_idx == 1 && cpu->port_write)
              cpu->port_write(cpu->cb_ctx, REG_PD & REG_PE);
            break;
          case 2:
            REG_IB = (REG_IB & 0x1f) | (data & 0xe0);
            break;
          case 3:
            REG_UA = data;
            break;
          case 4:
            if (cpu->kb_write)
              cpu->kb_write(cpu->cb_ctx, data);
            WRITE_REG8(target_idx, data);
            break;
          case 5:
            REG_IB &= (uint8_t)(0xe0 | (data >> 3));
            cpu->irq_status &= (uint8_t)(data >> 3);
            REG_IE = data;
            break;
          case 6:
            break;
          case 7:
            REG_TM = data;
            break;
          }
          cpu->icount -= 8;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0xd5: { /* PSRM SX/SY/SZ,$,IM3 : Put SIR Multibyte */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t cnt = GET_IM3(ext);
        uint8_t idx = (arg >> 5) & 0x03;
        for (uint8_t n = 0; n < cnt; n++) {
          cpu->regsir[(idx + n) & 0x03] = READ_REG(arg + n) & 0x1f;
          cpu->icount -= 8;
        }
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;

      case 0xd6:   /* PRE IX/IY/IZ/US,IM16 : Put Register 16-bit */
      case 0xd7: { /* PRE SS,IM16 : Put Register 16-bit */
        uint8_t arg = read_op(cpu);
        uint8_t lo = read_op(cpu);
        uint8_t hi = read_op(cpu);
        uint16_t *t = get_pre_target(cpu, op, arg);
        *t = (uint16_t)(lo | (hi << 8));
        cpu->icount -= 3;
      } break;

      case 0xd8:   /* BUP : Block Transfer UP */
      case 0xd9: { /* BDN : Block Transfer Down */
        for (;;) {
          uint8_t src = mem_readbyte(cpu, REG_UA, REG_IX);
          mem_writebyte_iz(cpu, REG_UA, REG_IZ, src);
          if (REG_IX == REG_IY)
            break;
          REG_IX += (op & 1) ? -1 : +1;
          REG_IZ += (op & 1) ? -1 : +1;
          cpu->icount -= 6;
        }
        cpu->icount -= 9;
      } break;
      case 0xda: { /* DIDM/DIUM/BYDM/BYUM $,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t op1 = (arg >> 5) & 0x03;
        uint8_t arg1 = read_op(cpu);
        uint8_t r1 = 0;
        uint8_t r2 = 0;
        uint8_t f = 0;

        switch (op1) {
        case 0x00: { /* DIDM */
          for (int n = GET_IM3(arg1); n > 0; n--) {
            r2 = r1;
            r1 = READ_REG(arg);
            r2 = (uint8_t)((r1 >> 4) | (r2 << 4));
            WRITE_REG(arg--, r2);
            cpu->icount -= 5;
          }
          CLEAR_FLAGS;
          CHECK_FLAGB_UZ_LZ(r2);
          CHECK_FLAG_Z(r2);
        } break;
        case 0x01: { /* DIUM */
          for (int n = GET_IM3(arg1); n > 0; n--) {
            r2 = r1;
            r1 = READ_REG(arg);
            r2 = (uint8_t)((r1 << 4) | (r2 >> 4));
            WRITE_REG(arg++, r2);
            cpu->icount -= 5;
          }
          CLEAR_FLAGS;
          CHECK_FLAGB_UZ_LZ(r2);
          CHECK_FLAG_Z(r2);
        } break;
        case 0x02: { /* BYDM */
          for (int n = GET_IM3(arg1); n > 0; n--) {
            r2 = r1;
            r1 = READ_REG(arg);
            WRITE_REG(arg--, r2);
            f |= r2;
            cpu->icount -= 5;
          }
          CLEAR_FLAGS;
          CHECK_FLAGB_UZ_LZ(r2);
          CHECK_FLAG_Z(f);
        } break;
        case 0x03: { /* BYUM */
          for (int n = GET_IM3(arg1); n > 0; n--) {
            r2 = r1;
            r1 = READ_REG(arg);
            WRITE_REG(arg++, r2);
            f |= r2;
            cpu->icount -= 5;
          }
          CLEAR_FLAGS;
          CHECK_FLAGB_UZ_LZ(r2);
          CHECK_FLAG_Z(f);
        } break;
        }
      } break;
      case 0xdb: { /* CMPM/INVM */
        uint8_t arg = read_op(cpu);
        uint8_t arg1 = read_op(cpu);
        uint8_t r1 = 0;
        uint8_t f = 0;
        uint8_t r2 = (arg & 0x40) ? 0 : 1;

        for (int n = GET_IM3(arg1); n > 0; n--) {
          r1 = (uint8_t)(r2 + (uint8_t)~READ_REG(arg));
          WRITE_REG(arg++, r1);
          if (r1)
            r2 = 0;
          f |= r1;
          cpu->icount -= 5;
        }

        CLEAR_FLAGS;
        CHECK_FLAG_Z(f);
        CHECK_FLAGB_UZ_LZ(r1);
        if (f != 0 || (arg & 0x40))
          SET_FLAG_C;
      } break;

      case 0xdc:   /* SUP $ : Search UP */
      case 0xdd: { /* SDN $ : Search DowN */
        uint8_t arg = read_op(cpu);
        uint16_t res;
        for (;;) {
          res = (uint16_t)(mem_readbyte(cpu, REG_UA, REG_IX) - READ_REG(arg));
          if (REG_IX == REG_IY || !res)
            break;
          REG_IX += (op & 1) ? -1 : +1;
          cpu->icount -= 6;
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint8_t)res);
        CHECK_FLAGB_UZ_LZ(res);
        CHECK_FLAG_C(res, 0xff);
        cpu->icount -= 9;
      } break;
      case 0xde: { /* JP $ */
        uint8_t arg = read_op(cpu);
        set_pc(cpu, REG_GET16(arg));
        cpu->icount -= 5;
      } break;
      case 0xdf: { /* JP ($) */
        uint8_t arg = read_op(cpu);
        uint16_t off = REG_GET16(arg);
        uint8_t lo = mem_readbyte(cpu, REG_UA, off);
        uint8_t hi = mem_readbyte(cpu, REG_UA, (uint16_t)(off + 1));
        set_pc(cpu, (uint16_t)(lo | (hi << 8)));
        cpu->icount -= 5;
      } break;

      /* 0xE0 - 0xEF */
      case 0xe0:   /* STM $,(IX)+ */
      case 0xe2: { /* STIM $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        uint16_t prev = REG_IX;
        REG_IX += off;
        for (uint8_t n = 0; n < count; n++) {
          mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg + n));
        }
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0xe1:   /* STM $,(IZ)+ */
      case 0xe3: { /* STIM $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        uint16_t prev = REG_IZ;
        REG_IZ += off;
        for (uint8_t n = 0; n < count; n++) {
          mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg + n));
        }
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0xe4: { /* STDM $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IX += off;
        for (uint8_t n = 0; n < count; n++) {
          mem_writebyte(cpu, REG_UA, REG_IX--, READ_REG(arg--));
        }
        REG_IX++;
        cpu->icount -= 8;
      } break;
      case 0xe5: { /* STDM $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IZ += off;
        for (uint8_t n = 0; n < count; n++) {
          mem_writebyte_iz(cpu, REG_UA, REG_IZ--, READ_REG(arg--));
        }
        REG_IZ++;
        cpu->icount -= 8;
      } break;
      case 0xe6:   /* PHSM $,IM3 */
      case 0xe7:   /* PHUM $,IM3 */
      case 0xee:   /* PPSM $,IM3 */
      case 0xef: { /* PPUM $,IM3 */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        if (op == 0xe6) {
          for (uint8_t n = 0; n < count; n++) {
            push(cpu, &REG_SS, READ_REG(arg - n));
            cpu->icount -= 3;
          }
        } else if (op == 0xe7) {
          for (uint8_t n = 0; n < count; n++) {
            push(cpu, &REG_US, READ_REG(arg - n));
            cpu->icount -= 3;
          }
        } else if (op == 0xee) {
          for (uint8_t n = 0; n < count; n++) {
            WRITE_REG(arg + n, pop(cpu, &REG_SS));
            cpu->icount -= 3;
          }
        } else {
          for (uint8_t n = 0; n < count; n++) {
            WRITE_REG(arg + n, pop(cpu, &REG_US));
            cpu->icount -= 3;
          }
        }
        cpu->icount -= (op <= 0xe7) ? 3 : 5;
      } break;
      case 0xe8: { /* LDM $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        uint16_t prev = REG_IX;
        REG_IX += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg + n, mem_readbyte(cpu, REG_UA, REG_IX++));
          cpu->icount -= 3;
        }
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 5;
      } break;
      case 0xe9: { /* LDM $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        uint16_t prev = REG_IZ;
        REG_IZ += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg + n, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
          cpu->icount -= 3;
        }
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 5;
      } break;
      case 0xea: { /* LDIM $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IX += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg + n, mem_readbyte(cpu, REG_UA, REG_IX++));
          cpu->icount -= 3;
        }
        cpu->icount -= 5;
      } break;
      case 0xeb: { /* LDIM $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IZ += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg + n, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
          cpu->icount -= 3;
        }
        cpu->icount -= 5;
      } break;
      case 0xec: { /* LDDM $,(IX)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IX += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg--, mem_readbyte(cpu, REG_UA, REG_IX--));
        }
        REG_IX++;
        cpu->icount -= 8;
      } break;
      case 0xed: { /* LDDM $,(IZ)+ */
        uint8_t arg = read_op(cpu);
        uint8_t ext = read_op(cpu);
        uint8_t count = ((ext >> 5) & 0x07) + 1;
        int off = READ_REG(get_sir_im8_arg1(cpu, arg, ext));
        if (arg & 0x80)
          off = -off;
        REG_IZ += off;
        for (uint8_t n = 0; n < count; n++) {
          WRITE_REG(arg--, mem_readbyte_iz(cpu, REG_UA, REG_IZ--));
        }
        REG_IZ++;
        cpu->icount -= 8;
      } break;

      /* 0xF0 - 0xFF */
      case 0xf0:   /* RTN Z */
      case 0xf1:   /* RTN NZ */
      case 0xf2:   /* RTN LZ */
      case 0xf3:   /* RTN UZ */
      case 0xf4:   /* RTN NZ */
      case 0xf5:   /* RTN C */
      case 0xf6:   /* RTN NLZ */
      case 0xf7: { /* RTN */
        if (check_cond(cpu, op)) {
          uint8_t lo = pop(cpu, &REG_SS);
          uint8_t hi = pop(cpu, &REG_SS);
          set_pc(cpu, (uint16_t)(((hi << 8) | lo) + 1));
        }
        cpu->icount -= 3;
      } break;
      case 0xf8: { /* NOP*/
        cpu->icount -= 3;
      } break; /* nop */
      case 0xf9: { /* CLT : clear timer */
        REG_TM &= 0xc0;
        cpu->icount -= 3;
      } break; /* clt */
      case 0xfa: { /* FST : fast mode */
        cpu->state |= CPU_FAST;
        cpu->icount -= 3;
      } break; /* fst */
      case 0xfb: { /* SLW : slow mode */
        cpu->state &= ~CPU_FAST;
        cpu->icount -= 3;
      } break; /* slw */
      case 0xfc: { /* CANI : Cancel Interrupt */
        for (uint8_t bit = 0x10; bit > 0; bit >>= 1) {
          if (REG_IB & bit) {
            REG_IB &= (uint8_t)~bit;
            cpu->irq_status &= (uint8_t)~bit;
            break;
          }
        }
        cpu->icount -= 3;
      } break;
      case 0xfd: { /* RTNI : Return from Interrupt */
        uint8_t lo = pop(cpu, &REG_SS);
        uint8_t hi = pop(cpu, &REG_SS);
        set_pc(cpu, (hi << 8) | lo);
        cpu->icount -= 5;
        /* Equivalent to CANI: cancel the highest priority interrupt */
        for (uint8_t bit = 0x10; bit > 0; bit >>= 1) {
          if (REG_IB & bit) {
            REG_IB &= (uint8_t)~bit;
            cpu->irq_status &= (uint8_t)~bit;
            break;
          }
        }
      } break;
      case 0xfe: { /* OFF : Power OFF */
        /* OFF behavior per HD61700 documentation:
           PC=0, IX/IY/IZ=0, UA=0, IA=0, and IE bits 0,1,5,6,7 cleared. */
        set_pc(cpu, 0x0000);
        REG_IX = 0;
        REG_IY = 0;
        REG_IZ = 0;
        REG_UA = 0;
        REG_IA = 0;
        REG_IE &= 0x1c;
        cpu->state |= CPU_SLP;
        cpu->icount -= 3;
      } break; /* off */
      case 0xff: { /* TRP : Trap*/
        push(cpu, &REG_SS, (uint8_t)(cpu->pc >> 8));
        push(cpu, &REG_SS, (uint8_t)cpu->pc);
        set_pc(cpu, 0x6ffa);
        cpu->icount -= 9;
      } break;
      default:
        break;
      }
      if (!WORD_ALIGNED(cpu->fetch_addr) && cpu->pc < INT_ROM) {
        set_pc(cpu, (int32_t)((cpu->fetch_addr + 1) >> 1));
      }
    }
    cpu->icount -= 3;
  } while (cpu->icount > 0);
  return cycles - cpu->icount;
}

int hd61700_step(hd61700_state_t *cpu) {
  cpu->last_op_len = 0;
  /* Execute exactly one instruction worth of work.
     read_op() appends fetched bytes into last_opcodes/last_op_len. */
  hd61700_execute(cpu, 1, -1);
  return cpu->last_op_len;
}

int hd61700_execute_steps(hd61700_state_t *cpu, int steps) {
  if (steps <= 0) {
    return 0;
  }

  int total_cycles = 0;
  for (int i = 0; i < steps; i++) {
    cpu->last_op_len = 0;
    total_cycles += hd61700_execute(cpu, 1, -1);
  }
  return total_cycles;
}



