# HD61700 CPU Core - Implementation Notes

> **Note:** This file predates the current implementation and originally described an
> early, partial CPU core. The core in `src/hd61700.c` has since grown into a full
> implementation (see below) capable of running the real PB-1000 ROM/BASIC. For
> authoritative, actively-maintained documentation see `doc/architecture.md`,
> `doc/dev_guide.md`, and `doc/memory_map.md`; this file is kept mainly as historical
> background on the opcode encoding.

## Current Implementation Status

### Completed
- Full CPU state structure (general registers, index registers, SIR, PC, flags, IA/IB/IE/UA)
- Memory/IO callback system (C-managed and Python-managed modes; see `doc/dev_guide.md`)
- Fetch/decode/execute cycle with a large opcode dispatch table (`src/hd61700.c`,
  roughly 270 `case` branches) covering data transfer, arithmetic/logical, control
  flow, stack, and I/O instructions — sufficient to boot and run the PB-1000 ROM
  and BASIC interpreter.
- Bank-switched memory (`0x8000`–`0xFFFF`, banks 1-3) and MMIO peripherals
  (LCD/VDP, keyboard matrix, PIO UART, virtual FDD, DMA) are implemented and
  wired through `modhd61700.c`.

Exact per-opcode coverage is not tracked in this file; consult `src/hd61700.c`
directly (or MAME's `hd61700_device` for the reference encoding) if you need to
verify a specific instruction.

## Opcode Encoding

The HD61700 uses variable-length instructions (1-4 bytes):
- Byte 1: Opcode
- Byte 2-4: Operands (register numbers, immediates, addresses)

### Register Encoding
- Main registers: $00-$1F (32 registers)
- SIR (Index registers): SX, SY, SZ (5-bit values)
- Index registers: IX, IY, IZ (16-bit)

## Memory Map (PB-1000)

The authoritative, up-to-date memory map (bank-common area, bank-switched area,
MMIO addresses, etc.) lives in `doc/memory_map.md` — refer to it instead of
duplicating the layout here.

## References

- `src/hd61700.c` / `src/hd61700.h` — CPU core implementation
- `src/modhd61700.c` — MicroPython ↔ CPU core wrapper module
- `HD61700.TXT` — Assembly language manual
- MAME `hd61700_device` — reference implementation used during development
- `doc/architecture.md`, `doc/dev_guide.md`, `doc/memory_map.md` — current design docs
