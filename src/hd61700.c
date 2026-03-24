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

static inline void set_pc(hd61700_state_t *cpu, int32_t new_pc) {
  cpu->pc = (uint16_t)(new_pc & 0xffff);
  if (cpu->pc < INT_ROM)
    cpu->fetch_addr = (uint32_t)cpu->pc << 1;
  else
    cpu->fetch_addr = (uint32_t)cpu->pc;
  cpu->curpc = cpu->pc;
  cpu->ppc = cpu->curpc;
}

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

static uint8_t mem_readbyte(hd61700_state_t *cpu, uint8_t segment,
                            uint32_t offset) {
  /* Data-space accesses use UA bits 4-5 (IX/main bank). */
  uint8_t bank = (segment >> 4) & 0x03;
  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte(hd61700_state_t *cpu, uint8_t segment,
                          uint32_t offset, uint8_t data) {
  /* Data-space accesses use UA bits 4-5 (IX/main bank). */
  uint8_t bank = (segment >> 4) & 0x03;
  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t mem_readbyte_iz(hd61700_state_t *cpu, uint8_t segment,
                               uint32_t offset) {
  /* IZ accesses use UA bits 6-7. */
  uint8_t bank = (segment >> 6) & 0x03;
  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte_iz(hd61700_state_t *cpu, uint8_t segment,
                             uint32_t offset, uint8_t data) {
  /* IZ accesses use UA bits 6-7. */
  uint8_t bank = (segment >> 6) & 0x03;
  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t mem_readbyte_stack(hd61700_state_t *cpu, uint8_t segment,
                                  uint32_t offset) {
  /* SSP/USP accesses use UA bits 2-3. */
  uint8_t bank = (segment >> 2) & 0x03;
  if (cpu->mem_read)
    return cpu->mem_read(cpu->cb_ctx, bank, offset);
  return 0;
}

static void mem_writebyte_stack(hd61700_state_t *cpu, uint8_t segment,
                                uint32_t offset, uint8_t data) {
  /* SSP/USP accesses use UA bits 2-3. */
  uint8_t bank = (segment >> 2) & 0x03;
  if (cpu->mem_write)
    cpu->mem_write(cpu->cb_ctx, bank, offset, data);
}

static uint8_t prog_readbyte(hd61700_state_t *cpu, uint8_t segment,
                             uint32_t offset) {
  /* Program-space accesses use UA bits 0-1 (PC bank). */
  uint8_t bank = segment & 0x03;
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

  // 螳溯｡後＠縺溘が繝壹さ繝ｼ繝峨ｒ險倬鹸縺励※縺翫￥
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

static void check_optional_jr(hd61700_state_t *cpu, uint8_t arg) {
  if (arg & 0x80) {
    /* Internal ROM is word-aligned: skip padding byte before JR offset if
     * fetch_addr is EVEN. */
    if (cpu->pc < INT_ROM && ((cpu->fetch_addr & 0x01) == 0)) {
      (void)read_op(cpu);
    }
    uint8_t arg1 = read_op(cpu);
    uint32_t new_pc = (cpu->pc + get_im_7(arg1) - 1);
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
      case 0x00:
      case 0x01: { /* adc / sbc (check only: update flags, don't write back) */
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
      case 0x08:
      case 0x09: { /* ad / sb */
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
      case 0x02: {
        uint8_t arg = read_op(cpu);
        COPY_REG(arg, get_sir_im8(cpu, arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x03: {
        uint8_t arg = read_op(cpu);
        (void)get_sir_im8(cpu, arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break; /* LDC (no-op) */
      case 0x04:
      case 0x05:
      case 0x06:
      case 0x07: { /* logic check */
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
      case 0x0c:
      case 0x0d:
      case 0x0e:
      case 0x0f: { /* logic */
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
      case 0x0a:
      case 0x0b: { /* bcd */
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

      case 0x10: {
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, READ_REG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x11: {
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, off));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x12: {
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
        cpu->icount -= 11;
      } break;
      case 0x13: {
        uint8_t arg = read_op(cpu);
        uint8_t res = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        WRITE_REG(arg, res);
        cpu->icount -= 11;
      } break;
      case 0x14: {
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
      case 0x15: {
        uint8_t arg = read_op(cpu);
        WRITE_SREG(arg, READ_REG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x16:
      case 0x17: {
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
      case 0x19:
      case 0x5a:
      case 0x5b:
      case 0x99: {
        uint8_t arg = read_op(cpu);
        if ((op & 0xf0) == 0xd0)
          (void)read_op(cpu);
        if ((op & 0xf0) != 0x50)
          check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x18: { /* ROD/ROU/BID/BIU */
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
          WRITE_REG(arg, 0);
          CLEAR_FLAGS;
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
      case 0x98: { /* RODW/ROUW/BIDW/BIUW */
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
      case 0x1c: {
        uint8_t arg = read_op(cpu);
        if (arg & 0x40)
          WRITE_REG(arg, cpu->flags);
        else if (cpu->port_read)
          WRITE_REG(arg, cpu->port_read(cpu->cb_ctx));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1d: {
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, READ_SREG(arg));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x1e:
      case 0x1f: {
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
      case 0x52: { /* STL #imm8 */
        uint8_t imm = read_op(cpu);
        if (cpu->debug_log && cpu->lcd_debug_log) {
          cpu_log(cpu, "PPO/STL imm executed: PC=0x%04X OP=0x%02X IMM=0x%02X",
                  instr_pc, op, imm);
        }
        if (cpu->lcd_write)
          cpu->lcd_write(cpu->cb_ctx, imm);
        cpu->icount -= 12;
      } break;
      case 0x53: {
        /* Keep operand stream alignment for currently unsupported variant. */
        (void)read_op(cpu);
        (void)read_op(cpu);
        cpu->icount -= 3;
      } break;
      case 0x5e:
      case 0x5f: {
        (void)read_op(cpu);
        (void)read_op(cpu);
        cpu->icount -= 3;
      } break;
      case 0x58:
      case 0x59: { /* BUPS/BDNS */
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
      case 0x5c:
      case 0x5d: { /* SUP/SDN (imm) */
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
      case 0xdc:
      case 0xdd: { /* SUP/SDN (reg) */
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
      case 0xde: { /* JPW reg-pair */
        uint8_t arg = read_op(cpu);
        set_pc(cpu, REG_GET16(arg));
        cpu->icount -= 5;
      } break;
      case 0xdf: { /* JPW (reg-pair) */
        uint8_t arg = read_op(cpu);
        uint16_t off = REG_GET16(arg);
        uint8_t lo = mem_readbyte(cpu, REG_UA, off);
        uint8_t hi = mem_readbyte(cpu, REG_UA, (uint16_t)(off + 1));
        set_pc(cpu, (uint16_t)(lo | (hi << 8)));
        cpu->icount -= 5;
      } break;
      case 0xd8:
      case 0xd9: { /* BUP/BDN */
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
      case 0xda: {
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

      /* 0x20 - 0x2F */
      case 0x20:
      case 0x22:
      case 0x24: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x21:
      case 0x23:
      case 0x25: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x28:
      case 0x2a: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x29:
      case 0x2b: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x2c: {
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 6;
      } break;
      case 0x2d: {
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 6;
      } break;
      case 0x26:
        push(cpu, &REG_SS, READ_REG(read_op(cpu)));
        cpu->icount -= 9;
        break;
      case 0x27:
        push(cpu, &REG_US, READ_REG(read_op(cpu)));
        cpu->icount -= 9;
        break;
      case 0x66: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        push(cpu, &REG_SS, READ_REG(arg));
        cpu->icount -= 9;
      } break;
      case 0x67: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        push(cpu, &REG_US, READ_REG(arg));
        cpu->icount -= 9;
      } break;
      case 0x2e: {
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_SS));
        cpu->icount -= 11;
      } break;
      case 0x2f: {
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, pop(cpu, &REG_US));
        cpu->icount -= 11;
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

      /* 0x30 - 0x3F */
      case 0x30:
      case 0x31:
      case 0x32:
      case 0x33:
      case 0x34:
      case 0x35:
      case 0x36:
      case 0x37: {
        uint16_t addr = read_imm16_aligned(cpu);
        if (cpu->debug_log)
          cpu_log(cpu, "JP 0x%04X executed at 0x%04X", addr, instr_pc);
        if (check_cond(cpu, op))
          set_pc(cpu, addr);
        cpu->icount -= 3;
      } break;
      case 0x38:
      case 0x3a:
      case 0x3c:
      case 0x3e: {
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
      case 0x39:
      case 0x3b:
      case 0x3d:
      case 0x3f: {
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
      case 0x70:
      case 0x71:
      case 0x72:
      case 0x73:
      case 0x74:
      case 0x75:
      case 0x76:
      case 0x77: {
        uint16_t addr = read_imm16_aligned(cpu);
        if (check_cond(cpu, op)) {
          uint16_t ret = (uint16_t)(cpu->pc - 1);
          push(cpu, &REG_SS, (uint8_t)(ret >> 8));
          push(cpu, &REG_SS, (uint8_t)ret);
          set_pc(cpu, addr);
          cpu->icount -= 12;
        }
        cpu->icount -= 3;
      } break;
      case 0x78:
      case 0x7a:
      case 0x7c:
      case 0x7e: {
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
      case 0x79:
      case 0x7b:
      case 0x7d:
      case 0x7f: {
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

      /* 0x40 - 0x4F (RESTORED) */
      case 0x40:
      case 0x41:
      case 0x48:
      case 0x49: { /* imm arith */
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
      case 0x42: {
        uint8_t arg = read_op(cpu);
        WRITE_REG(arg, read_op(cpu));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x43: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      // case 0x43: { uint8_t arg = read_op(cpu); (void)read_op(cpu);
      // cpu->icount -= 3; } break; /* LDC imm (no-op) */
      case 0x4a:
      case 0x4b: { /* ADB/SBB imm */
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
      case 0x44:
      case 0x45:
      case 0x46:
      case 0x47: { /* imm logic check */
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
      case 0x4c:
      case 0x4d:
      case 0x4e:
      case 0x4f: { /* imm logic */
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
      case 0x50: {
        uint8_t arg = read_op(cpu);
        uint8_t imm = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, imm);
        cpu->icount -= 3;
      } break;
      case 0x51: {
        uint8_t arg = read_op(cpu);
        uint8_t imm = read_op(cpu);
        write_sreg_or_reg(cpu, arg, imm);
        cpu->icount -= 3;
      } break;
      case 0x54: {
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
      case 0x55: {
        uint8_t arg = read_op(cpu);
        WRITE_SREG(arg, arg);
        cpu->icount -= 3;
      } break;
      case 0x56:
      case 0x57: {
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
      case 0xd0: {
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
      case 0xd1: {
        uint8_t arg = read_op(cpu);
        uint8_t lo = read_op(cpu);
        uint8_t hi = read_op(cpu);
        REG_PUT16(arg, (uint16_t)(lo | (hi << 8)));
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x60:
      case 0x62:
      case 0x64: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_im8(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x61:
      case 0x63:
      case 0x65: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_im8(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x68:
      case 0x6a: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 8;
      } break;
      case 0x69:
      case 0x6b: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 8;
      } break;
      case 0x6c: {
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 6;
      } break;
      case 0x6d: {
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_im8(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 6;
      } break;
      case 0xa0:
      case 0xa2: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, REG_IX++, READ_REG(arg + 1));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 11;
      } break;
      case 0xa1:
      case 0xa3: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg));
        mem_writebyte_iz(cpu, REG_UA, REG_IZ++, READ_REG(arg + 1));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 11;
      } break;
      case 0xa4: {
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        mem_writebyte(cpu, REG_UA, REG_IX--, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, REG_IX, READ_REG(arg - 1));
        cpu->icount -= 9;
      } break;
      case 0xa5: {
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        mem_writebyte_iz(cpu, REG_UA, REG_IZ--, READ_REG(arg));
        mem_writebyte_iz(cpu, REG_UA, REG_IZ, READ_REG(arg - 1));
        cpu->icount -= 9;
      } break;
      case 0xa8:
      case 0xaa: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IX;
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX++));
        WRITE_REG(arg + 1, mem_readbyte(cpu, REG_UA, REG_IX++));
        RESTORE_REG(op, REG_IX, prev);
        cpu->icount -= 11;
      } break;
      case 0xa9:
      case 0xab: {
        uint8_t arg = read_op(cpu);
        uint16_t prev = REG_IZ;
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        WRITE_REG(arg + 1, mem_readbyte_iz(cpu, REG_UA, REG_IZ++));
        RESTORE_REG(op, REG_IZ, prev);
        cpu->icount -= 11;
      } break;
      case 0xac: {
        uint8_t arg = read_op(cpu);
        REG_IX += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, REG_IX--));
        WRITE_REG(arg - 1, mem_readbyte(cpu, REG_UA, REG_IX));
        cpu->icount -= 9;
      } break;
      case 0xad: {
        uint8_t arg = read_op(cpu);
        REG_IZ += get_sign_mreg(cpu, arg);
        WRITE_REG(arg, mem_readbyte_iz(cpu, REG_UA, REG_IZ--));
        WRITE_REG(arg - 1, mem_readbyte_iz(cpu, REG_UA, REG_IZ));
        cpu->icount -= 9;
      } break;
      case 0xe0:
      case 0xe2: { /* STM/STIM via IX (forward) */
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
      case 0xe1:
      case 0xe3: { /* STM/STIM via IZ (forward) */
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
      case 0xe8:
      case 0xea: { /* LDM/LDIM via IX (forward) */
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
      case 0xe9:
      case 0xeb: { /* LDM/LDIM via IZ (forward) */
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
      case 0xe4: { /* STDM via IX (reverse) */
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
      case 0xe5: { /* STDM via IZ (reverse) */
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
      case 0xec: { /* LDDM via IX (reverse) */
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
      case 0xed: { /* LDDM via IZ (reverse) */
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

      case 0x90: {
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        mem_writebyte(cpu, REG_UA, off, READ_REG(arg));
        mem_writebyte(cpu, REG_UA, (uint16_t)(off + 1), READ_REG(arg + 1));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x91: {
        uint8_t arg = read_op(cpu);
        uint8_t r = get_sir_im8(cpu, arg);
        uint16_t off = REG_GET16(r);
        WRITE_REG(arg, mem_readbyte(cpu, REG_UA, off));
        WRITE_REG(arg + 1, mem_readbyte(cpu, REG_UA, (uint16_t)(off + 1)));
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x92: { /* STLW */
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
      case 0x93: { /* LDLW */
        uint8_t arg = read_op(cpu);
        uint8_t d0 = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        uint8_t d1 = cpu->lcd_read ? cpu->lcd_read(cpu->cb_ctx) : 0xff;
        WRITE_REG(arg, d0);
        WRITE_REG(arg + 1, d1);
        check_optional_jr(cpu, arg);
        cpu->icount -= 19;
      } break;
      case 0x94:
      case 0x9c: {
        uint8_t arg = read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x95:
      case 0x9d: {
        uint8_t arg = read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x96:
      case 0x97: {
        uint8_t arg = read_op(cpu);
        uint16_t *t = get_pre_target(cpu, op, arg);
        *t = REG_GET16(arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 3;
      } break;
      case 0x9e:
      case 0x9f: {
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
      case 0xd6:
      case 0xd7: {
        uint8_t arg = read_op(cpu);
        uint8_t lo = read_op(cpu);
        uint8_t hi = read_op(cpu);
        uint16_t *t = get_pre_target(cpu, op, arg);
        *t = (uint16_t)(lo | (hi << 8));
        cpu->icount -= 3;
      } break;
      case 0xa6:
      case 0xa7:
      case 0xae:
      case 0xaf: {
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
      case 0xd2: { /* STLM */
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
      case 0xd3: { /* LDLM */
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
      case 0xd4: { /* PFLM */
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
      case 0xd5: { /* PSRM */
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
      case 0xe6:
      case 0xe7:
      case 0xee:
      case 0xef: {
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

      /* 0x80 - 0x8F (16-bit arith) */
      case 0x80:
      case 0x81: {
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
      case 0x82: {
        uint8_t arg = read_op(cpu);
        uint8_t src = get_sir_im8(cpu, arg);
        COPY_REG(arg, src);
        COPY_REG(arg + 1, src + 1);
        /* missing JR handling was causing subsequent bytes to be mis-fetched */
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x83: {
        uint8_t arg = read_op(cpu);
        (void)get_sir_im8(cpu, arg);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x84:
      case 0x85:
      case 0x86:
      case 0x87: {
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
      case 0x88:
      case 0x89: {
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
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0x8a:
      case 0x8b: {
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
      case 0x8c:
      case 0x8d:
      case 0x8e:
      case 0x8f: {
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
      /* 0xB0 - 0xB7 (JR) */
      case 0xb0:
      case 0xb1:
      case 0xb2:
      case 0xb3:
      case 0xb4:
      case 0xb5:
      case 0xb6:
      case 0xb7: {
        uint8_t arg = read_op(cpu);
        if (check_cond(cpu, op)) {
          uint32_t npc = (cpu->pc - 1) + get_im_7(arg);
          set_pc(cpu, (int32_t)npc);
        }
        cpu->icount -= 3;
      } break;
      case 0xb8:
      case 0xb9:
      case 0xba:
      case 0xbb:
      case 0xbc:
      case 0xbd:
      case 0xbe:
      case 0xbf: {
        uint8_t arg = read_op(cpu);
        bool use_iz = (op & 0x01) != 0;
        uint16_t addr = use_iz ? REG_IZ : REG_IX;
        addr = (uint16_t)(addr + get_sign_mreg(cpu, arg));
        uint8_t m0 = use_iz ? mem_readbyte_iz(cpu, REG_UA, addr)
                            : mem_readbyte(cpu, REG_UA, addr);
        uint8_t m1 = use_iz ? mem_readbyte_iz(cpu, REG_UA, (uint16_t)(addr + 1))
                            : mem_readbyte(cpu, REG_UA, (uint16_t)(addr + 1));
        uint16_t m = (uint16_t)(m0 | (m1 << 8));
        uint16_t r = REG_GET16(arg);
        uint32_t res = m + ((op & 0x02) ? -(int)r : +(int)r);
        if (op & 0x04) {
          if (use_iz) {
            mem_writebyte_iz(cpu, REG_UA, addr, (uint8_t)res);
            mem_writebyte_iz(cpu, REG_UA, (uint16_t)(addr + 1),
                             (uint8_t)(res >> 8));
          } else {
            mem_writebyte(cpu, REG_UA, addr, (uint8_t)res);
            mem_writebyte(cpu, REG_UA, (uint16_t)(addr + 1),
                          (uint8_t)(res >> 8));
          }
        }
        CLEAR_FLAGS;
        CHECK_FLAG_Z((uint16_t)res);
        CHECK_FLAGW_UZ_LZ((uint16_t)res);
        CHECK_FLAG_C(res, 0xffff);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xc0:
      case 0xc1:
      case 0xc8:
      case 0xc9: {
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
      case 0xc2: {
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
      case 0xc3: {
        uint8_t arg = read_op(cpu);
        (void)read_op(cpu);
        check_optional_jr(cpu, arg);
        cpu->icount -= 8;
      } break;
      case 0xc4:
      case 0xc5:
      case 0xc6:
      case 0xc7:
      case 0xcc:
      case 0xcd:
      case 0xce:
      case 0xcf: {
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
      case 0xca:
      case 0xcb: {
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

      case 0xf0:
      case 0xf1:
      case 0xf2:
      case 0xf3:
      case 0xf4:
      case 0xf5:
      case 0xf6:
      case 0xf7: {
        if (check_cond(cpu, op)) {
          uint8_t lo = pop(cpu, &REG_SS);
          uint8_t hi = pop(cpu, &REG_SS);
          set_pc(cpu, (uint16_t)(((hi << 8) | lo) + 1));
        }
        cpu->icount -= 3;
      } break;
      case 0xf8:
        cpu->icount -= 3;
        break; /* nop */
      case 0xf9:
        REG_TM &= 0xc0;
        cpu->icount -= 3;
        break; /* clt */
      case 0xfa:
        cpu->state |= CPU_FAST;
        cpu->icount -= 3;
        break; /* fst */
      case 0xfb:
        cpu->state &= ~CPU_FAST;
        cpu->icount -= 3;
        break; /* slw */
      case 0xfd: {
        uint8_t lo = pop(cpu, &REG_SS);
        uint8_t hi = pop(cpu, &REG_SS);
        set_pc(cpu, (hi << 8) | lo);
        cpu->icount -= 5;
        /* fallthrough to CANI */
      }
      case 0xfc: {
        for (uint8_t bit = 0x10; bit > 0; bit >>= 1) {
          if (REG_IB & bit) {
            REG_IB &= (uint8_t)~bit;
            cpu->irq_status &= (uint8_t)~bit;
            break;
          }
        }
        cpu->icount -= 3;
      } break;
      case 0xfe:
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
        break; /* off */
      case 0xff: {
        push(cpu, &REG_SS, (uint8_t)(cpu->pc >> 8));
        push(cpu, &REG_SS, (uint8_t)cpu->pc);
        set_pc(cpu, 0x0022);
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


