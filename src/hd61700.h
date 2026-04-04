/*
 * HD61700 CPU Emulator - Header
 * Based on MAME hd61700.cpp by Sandro Ronco (BSD-3-Clause license)
 * Ported to standalone C for MicroPython integration
 */
#ifndef HD61700_H
#define HD61700_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Internal ROM boundary (word addresses) */
#define INT_ROM 0x0c00

/* CPU state flags */
#define CPU_SLP 0x02
#define CPU_FAST 0x01

/* Flag definitions (matches MAME) */
#define FLAG_Z 0x80
#define FLAG_C 0x40
#define FLAG_LZ 0x20
#define FLAG_UZ 0x10
#define FLAG_SW 0x08
#define FLAG_APO 0x04

/* Interrupt lines */
#define HD61700_ON_INT 0
#define HD61700_TIMER_INT 1
#define HD61700_INT2 2
#define HD61700_KEY_INT 3
#define HD61700_INT1 4
#define HD61700_SW 5

/* IRQ vectors */
static const uint16_t irq_vector[] = {0x0032, 0x0042, 0x0052, 0x0062, 0x0072};

/* Register access macros */
#define REG_SX cpu->regsir[0]
#define REG_SY cpu->regsir[1]
#define REG_SZ cpu->regsir[2]

#define REG_PE cpu->reg8bit[0]
#define REG_PD cpu->reg8bit[1]
#define REG_IB cpu->reg8bit[2]
#define REG_UA cpu->reg8bit[3]
#define REG_IA cpu->reg8bit[4]
#define REG_IE cpu->reg8bit[5]
#define REG_TM cpu->reg8bit[6]

#define REG_IX cpu->reg16bit[0]
#define REG_IY cpu->reg16bit[1]
#define REG_IZ cpu->reg16bit[2]
#define REG_US cpu->reg16bit[3]
#define REG_SS cpu->reg16bit[4]
#define REG_KY cpu->reg16bit[5]

/* Main register read/write */
#define READ_REG(a) (cpu->regmain[(a) & 0x1f])
#define WRITE_REG(a, d) (cpu->regmain[(a) & 0x1f] = (d))
#define COPY_REG(d, s) (cpu->regmain[(d) & 0x1f] = cpu->regmain[(s) & 0x1f])

/* SIR register read/write */
#define READ_SREG(a) (cpu->regsir[((a) >> 5) & 0x03])
#define WRITE_SREG(a, d) (cpu->regsir[((a) >> 5) & 0x03] = (d) & 0x1f)

/* 8-bit register read/write */
#define READ_REG8(a) (cpu->reg8bit[(a) & 0x07])
#define WRITE_REG8(a, d) (cpu->reg8bit[(a) & 0x07] = (d))

/* 16-bit register from main regs */
#define REG_GET16(r)                                                           \
  ((uint16_t)cpu->regmain[(r) & 0x1f] |                                        \
   ((uint16_t)cpu->regmain[((r) + 1) & 0x1f] << 8))
#define REG_PUT16(r, d)                                                        \
  do {                                                                         \
    cpu->regmain[(r) & 0x1f] = (uint8_t)(d);                                   \
    cpu->regmain[((r) + 1) & 0x1f] = (uint8_t)((d) >> 8);                      \
  } while (0)

/* Operand index calculation */
#define GET_REG_IDX(op, arg) ((((op) & 0x01) << 2) | (((arg) >> 5) & 0x03))
#define GET_IM3(d) ((((d) >> 5) & 0x07) + 1)

/* Flag macros */
#define CLEAR_FLAGS (cpu->flags &= ~(FLAG_Z | FLAG_C | FLAG_LZ | FLAG_UZ))
#define SET_FLAG_C (cpu->flags |= FLAG_C)
#define CHECK_FLAG_Z(d)                                                        \
  do {                                                                         \
    if (!(d))                                                                  \
      cpu->flags |= FLAG_Z;                                                    \
  } while (0)
#define CHECK_FLAG_C(d, m)                                                     \
  do {                                                                         \
    if ((d) > (m))                                                             \
      cpu->flags |= FLAG_C;                                                    \
  } while (0)
#define CHECK_FLAGB_UZ_LZ(d)                                                   \
  do {                                                                         \
    if (!((d) & 0x0f))                                                         \
      cpu->flags |= FLAG_LZ;                                                   \
    if (!((d) & 0xf0))                                                         \
      cpu->flags |= FLAG_UZ;                                                   \
  } while (0)
#define CHECK_FLAGW_LZ(d)                                                      \
  do {                                                                         \
    if (!((d) & 0x0fff))                                                       \
      cpu->flags |= FLAG_LZ;                                                   \
  } while (0)
#define CHECK_FLAGW_UZ(d)                                                      \
  do {                                                                         \
    if (!((d) & 0xf000))                                                       \
      cpu->flags |= FLAG_UZ;                                                   \
  } while (0)
#define CHECK_FLAGW_UZ_LZ(d)                                                   \
  do {                                                                         \
    CHECK_FLAGW_LZ(d);                                                         \
    CHECK_FLAGW_UZ(d);                                                         \
  } while (0)

/* Conditional write (for check-only ops: bit3 clear = write, set = check only)
 */
#define COND_WRITE_REG(op, a, d)                                               \
  do {                                                                         \
    if ((op) & 0x08)                                                           \
      WRITE_REG(a, d);                                                         \
  } while (0)
/* Restore IX/IZ register for non-increment ops */
#define RESTORE_REG(op, reg, prev)                                             \
  do {                                                                         \
    if (!((op) & 0x02))                                                        \
      reg = prev;                                                              \
  } while (0)
#define WORD_ALIGNED(a) (!((a) & 1))

/* Callback types */
typedef uint8_t (*hd61700_read_byte_cb)(void *ctx, uint8_t segment,
                                        uint32_t offset);
typedef void (*hd61700_write_byte_cb)(void *ctx, uint8_t segment,
                                      uint32_t offset, uint8_t data);
typedef uint8_t (*hd61700_lcd_read_cb)(void *ctx);
typedef void (*hd61700_lcd_write_cb)(void *ctx, uint8_t data);
typedef void (*hd61700_lcd_ctrl_cb)(void *ctx, uint8_t data);
typedef uint16_t (*hd61700_kb_read_cb)(void *ctx);
typedef void (*hd61700_kb_write_cb)(void *ctx, uint8_t data);
typedef uint8_t (*hd61700_port_read_cb)(void *ctx);
typedef void (*hd61700_port_write_cb)(void *ctx, uint8_t data);
typedef void (*hd61700_log_write_cb)(void *ctx, const char *msg);

/* CPU State */
typedef struct {
  /* Main registers: 32 x 8-bit */
  uint8_t regmain[0x20];
  /* SIR registers: SX, SY, SZ (5-bit each) */
  uint8_t regsir[3];
  /* 8-bit special registers: PE, PD, IB, UA, IA, IE, TM, TM */
  uint8_t reg8bit[8];
  /* 16-bit registers: IX, IY, IZ, US, SS, KY */
  uint16_t reg16bit[8];

  /* Program counter / fetch */
  uint16_t pc;
  uint32_t curpc;
  uint32_t ppc;
  uint32_t fetch_addr;
  uint8_t prev_ua;

  /* Flags & state */
  uint8_t flags;
  uint8_t irq_status;
  uint8_t state;
  bool debug_log;
  bool key_debug_log;
  bool lcd_debug_log;

  /* Cycle counter (decremented) */
  int icount;

  /* Callbacks (set by host) */
  void *cb_ctx;
  hd61700_read_byte_cb mem_read;
  hd61700_write_byte_cb mem_write;
  hd61700_lcd_read_cb lcd_read;
  hd61700_lcd_write_cb lcd_write;
  hd61700_lcd_ctrl_cb lcd_ctrl;
  hd61700_kb_read_cb kb_read;
  hd61700_kb_write_cb kb_write;
  hd61700_port_read_cb port_read;
  hd61700_port_write_cb port_write;
  hd61700_log_write_cb log_write;
  void *log_ctx;

  // ステップ実行デバッグ用に追加
  uint8_t last_opcodes[8]; // HD61700の最大命令長に合わせて確保
  uint8_t last_op_len;

  /* Direct memory pointers (optional optimization) */
  uint8_t *rom0_ptr;    /* Internal ROM (8KB): 0x0000-0x1FFF */
  uint8_t *rom1_ptr;    /* System ROM / Bank 0 (32KB): 0x8000-0xFFFF */
  uint8_t *ram_ptr;     /* Main RAM (8KB): 0x6000-0x7FFF */
  uint8_t *exp_ram_ptr; /* Expanded RAM (32KB): Bank 1 @ 0x8000-0xFFFF */

} hd61700_state_t;

/* API Functions */
void hd61700_init(hd61700_state_t *cpu);
void hd61700_reset(hd61700_state_t *cpu);
int hd61700_execute(hd61700_state_t *cpu, int cycles, int32_t stop_pc);
int hd61700_execute_steps(hd61700_state_t *cpu, int steps);
void hd61700_set_input(hd61700_state_t *cpu, int line, int state);
void hd61700_timer_tick(hd61700_state_t *cpu);
int hd61700_step(hd61700_state_t *cpu);
void hd61700_set_debug(hd61700_state_t *cpu, bool enable);
void hd61700_set_key_debug(hd61700_state_t *cpu, bool enable);
void hd61700_set_lcd_debug(hd61700_state_t *cpu, bool enable);
void hd61700_set_pc(hd61700_state_t *cpu, uint16_t pc);

#endif /* HD61700_H */
