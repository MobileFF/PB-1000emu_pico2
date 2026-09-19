# General-Purpose Subroutine Hook Samples

A collection of sample general-purpose subroutine hooks for the emulator, with BASIC usage
examples.

**No subroutine hook ships as standard equipment.** Every hook described in this document is
**sample code** — it only does anything once a BASIC program explicitly `CALL`s it, and has no
effect on the emulator's core operation (CPU, LCD, keyboard, etc.) otherwise. Whether any given
hook runs at all is entirely up to whether you choose to **place** its file on the SD card
(`/sd/ext/`) or in flash (`/ext/`); which hooks you use, if any, is completely up to you.

Hardware-specific hooks (the DHT20 temperature/humidity sensor, etc.) are included here too, as
just another sample.  
For the hook framework and how to write your own hooks, see [extension_api_en.md](extension_api_en.md).

> **Note**: In this repository's distributed state, `bank_loader.py` (`mp/ext/`) is already placed
> there, so it works with no extra steps. `ram_test.py` and `dht20.py`, by contrast, live under
> `sample/mp/ext/` and are **not** auto-loaded on a stock device unless copied into `mp/ext/` (or
> `/sd/ext/`) first. There is no functional difference between the two groups — the only
> difference is whether the file happens to be placed already. CALL &H5E41/51/61/71 (ram_test) and
> &H5E10 (dht20) below all require that copy step first.

---

## Common Rules

### Work Area

| Address | Direction | Content |
|---|---|---|
| `&H5F00` | OUT | Result code (read with PEEK after CALL) |
| `&H5F01` onwards | IN/OUT | Per-hook parameters and result data |

### Standard Result Check Pattern

```basic
CALL &H5Exx
IF PEEK(&H5F00)<>0 THEN PRINT "ERR:";PEEK(&H5F00): END
```

---

## bank_loader.py

Transfers a binary file from the SD card or from a file inside a virtual FDD
disk image into bank 1–3 RAM.

### CALL &H5E81 — SD Card → Bank RAM Transfer

#### Input Parameters (POKE before CALL)

| Offset | Content |
|---|---|
| `&H5F00` | Bank number (1–3) |
| `&H5F01` | Destination offset high byte (bank-relative, 0x0000–0x7FFF) |
| `&H5F02` | Destination offset low byte |
| `&H5F03` | File read start position high byte (0 = from beginning) |
| `&H5F04` | File read start position low byte |
| `&H5F05` | Max bytes to load high byte (0x0000 = load all that fits) |
| `&H5F06` | Max bytes to load low byte |
| `&H5F07`– | File path (null-terminated ASCII string) |

#### Output (PEEK after CALL)

| Offset | Content |
|---|---|
| `&H5F00` | Result code (see table below) |
| `&H5F01` | Bytes loaded high byte |
| `&H5F02` | Bytes loaded low byte |

| Result Code | Meaning |
|---|---|
| `0x00` | OK |
| `0x01` | Bank not present |
| `0x02` | File not found / read error |
| `0xFF` | Other error |

#### Example

```basic
1000 REM --- LOAD /sd/game.bin TO BANK 2 OFFSET 0 ---
1010 POKE &H5F00, 2      : REM BANK 2
1020 POKE &H5F01, &H00   : REM DEST OFFSET HI
1030 POKE &H5F02, &H00   : REM DEST OFFSET LO
1040 POKE &H5F03, 0      : REM FILE OFFSET HI
1050 POKE &H5F04, 0      : REM FILE OFFSET LO
1060 POKE &H5F05, 0      : REM MAX LEN HI (0=ALL)
1070 POKE &H5F06, 0      : REM MAX LEN LO
1080 REM SET FILENAME "/sd/game.bin" + NUL
1090 F$="/sd/game.bin"
1100 FOR I=1 TO LEN(F$)
1110   POKE &H5F06+I, ASC(MID$(F$,I,1))
1120 NEXT I
1130 POKE &H5F07+LEN(F$), 0   : REM NUL TERMINATOR
1140 CALL &H5E81
1150 IF PEEK(&H5F00)<>0 THEN PRINT "LOAD ERR:";PEEK(&H5F00): END
1160 N=PEEK(&H5F01)*256+PEEK(&H5F02)
1170 PRINT "LOADED:";N;"BYTES"
```

> **Note**: The file path starts at `&H5F07`.  
> In the loop above `&H5F06+1 = &H5F07`, so the path is written correctly.

---

### CALL &H5E91 — Virtual FDD → Bank RAM Transfer

Transfers a file from the virtual FDD disk image into the specified bank.  
The virtual FDD must be enabled and a disk image must be mounted.

#### Input Parameters (POKE before CALL)

| Offset | Content |
|---|---|
| `&H5F00` | Bank number (1–3) |
| `&H5F01` | Destination offset high byte |
| `&H5F02` | Destination offset low byte |
| `&H5F03` | Records to skip (0 = from start; 1 record = 256 bytes) |
| `&H5F04`–`&H5F0E` | Filename — 11 bytes (see "FDD Filename Format" below) |

#### Output (PEEK after CALL)

| Offset | Content |
|---|---|
| `&H5F00` | Result code (see table below) |
| `&H5F01` | Bytes loaded high byte |
| `&H5F02` | Bytes loaded low byte |

| Result Code | Meaning |
|---|---|
| `0x00` | OK |
| `0x01` | Bank not present |
| `0x02` | File not found |
| `0x03` | Virtual FDD not ready |
| `0xFF` | Other error |

#### FDD Filename Format

11-byte fixed-length format matching the MD-100 DOS directory entry:
8-byte name + 3-byte extension, both right-padded with spaces. No dot separator.

| Filename | 11-byte form | ASCII byte values |
|---|---|---|
| `GAME.BAS` | `GAME    BAS` | 71,65,77,69,32,32,32,32,66,65,83 |
| `DATA.BIN` | `DATA    BIN` | 68,65,84,65,32,32,32,32,66,73,78 |
| `PROGRAM.BAS` | `PROGRAM BAS` | 80,82,79,71,82,65,77,32,66,65,83 |

#### Example

```basic
1000 REM --- LOAD "GAME.BAS" FROM FDD TO BANK 2 OFFSET 0 ---
1010 POKE &H5F00, 2      : REM BANK 2
1020 POKE &H5F01, &H00   : REM DEST OFFSET HI
1030 POKE &H5F02, &H00   : REM DEST OFFSET LO
1040 POKE &H5F03, 0      : REM SKIP RECORDS
1050 REM FILENAME "GAME    BAS" (11 BYTES)
1060 FOR I=0 TO 10
1070   READ C
1080   POKE &H5F04+I, C
1090 NEXT I
1100 DATA 71,65,77,69,32,32,32,32,66,65,83
1110 CALL &H5E91
1120 IF PEEK(&H5F00)<>0 THEN PRINT "LOAD ERR:";PEEK(&H5F00): END
1130 N=PEEK(&H5F01)*256+PEEK(&H5F02)
1140 PRINT "LOADED:";N;"BYTES"
```

---

## ram_test.py (sample, copy required)

Provides bank-switching hooks and a RAM self-test for BANK2/3.  
The bank-switching hooks are used when directly reading/writing bank RAM with POKE/PEEK.

This is a sample module under `sample/mp/ext/ram_test.py` — it is not placed in `mp/ext/` by
default. To use it, copy it into `mp/ext/` (or `/sd/ext/`) first.

### CALL &H5E41 — Switch Data Bank to BANK2

Sets UA register bits [5:4] to `10` (BANK2).  
After this call, all accesses to `&H8000`–`&HFFFF` are routed to BANK2 RAM.

No parameters or return values.

```basic
CALL &H5E41     ' select BANK2
POKE &H8000, 42
PRINT PEEK(&H8000)   ' 42
```

---

### CALL &H5E51 — Switch Data Bank to BANK3

Sets UA register bits [5:4] to `11` (BANK3).  
After this call, all accesses to `&H8000`–`&HFFFF` are routed to BANK3 RAM.

No parameters or return values.

```basic
CALL &H5E51     ' select BANK3
POKE &H8000, 99
PRINT PEEK(&H8000)   ' 99
```

---

### CALL &H5E61 — Restore Data Bank

Clears UA register bits [5:4] to `00`, restoring normal ROM access.  
Always call this after finishing BANK2/3 operations.

No parameters or return values.

```basic
CALL &H5E41     ' select BANK2
POKE &H8000, 42
CALL &H5E61     ' restore bank
```

---

### CALL &H5E71 — Run BANK2/3 RAM Self-Test

Runs write/read-verify tests on all installed BANK2/3 RAM and prints results
to the serial console (REPL).

Test suite:
- **Test1**: Key addresses × multiple bit patterns (R/W)
- **Test2**: First 256 bytes — sequential pattern
- **Test3**: Last 256 bytes — reverse pattern
- **Test4**: BANK2/BANK3 isolation (different values at same address)

#### Output (PEEK after CALL)

| Offset | Content |
|---|---|
| `&H5F00` | `0x00` = all passed / `0x01` = one or more failures |
| `&H5F01` | Number of tests passed |
| `&H5F02` | Number of tests failed |

#### Example

```basic
1000 CALL &H5E71
1010 IF PEEK(&H5F00)=0 THEN PRINT "ALL PASSED": END
1020 PRINT "FAILED:";PEEK(&H5F02);" / PASSED:";PEEK(&H5F01)
```

---

## dht20.py (sample, copy required)

A hardware-specific hook that reads an I2C-connected DHT20 temperature/humidity sensor.  
This is a sample module under `sample/mp/ext/dht20.py` — it is not auto-loaded on a stock device
unless copied into `mp/ext/` (or `/sd/ext/`) first. The matching BASIC sample is
`sample/basic/DHT20.BAS`.

### Wiring

| Signal | Pico 2 pin |
|---|---|
| SDA | GP2 (I2C1) |
| SCL | GP3 (I2C1) |
| VDD | 3.3V |
| GND | GND |

Pull SDA/SCL up with 4.7 kΩ resistors (GP2/GP3 are the PR6-reserved pins in `gpio_pin_map.md`).
I2C initialization is deferred until the first CALL, so the sensor is recognized even if it is
connected after the emulator has already booted. If a read fails, the initialization state is
reset and re-initialization is retried on the next CALL.

### CALL &H5E10 — Read DHT20

#### Output (PEEK after CALL)

| Offset | Content |
|---|---|
| `&H5F00` | Result code: `0x00`=OK / `0xFF`=error (sensor not responding, measurement not ready, etc.) |
| `&H5F01` | Temperature×10 high byte (signed 16-bit, big-endian) |
| `&H5F02` | Temperature×10 low byte (e.g. `&H00EB`=235 → 23.5°C) |
| `&H5F03` | Humidity×10 high byte (unsigned 16-bit, big-endian) |
| `&H5F04` | Humidity×10 low byte (e.g. `&H025D`=605 → 60.5%) |

#### Example

```basic
1000 CALL &H5E10
1010 IF PEEK(&H5F00)<>0 THEN PRINT "SENSOR ERROR": END
1020 T=PEEK(&H5F01)*256+PEEK(&H5F02)
1030 IF T>32767 THEN T=T-65536
1040 H=PEEK(&H5F03)*256+PEEK(&H5F04)
1050 PRINT "TEMP:";T/10;"C"
1060 PRINT "HUMD:";H/10;"%"
```

---

## Hook Address Summary

| Address | Module | Function |
|---|---|---|
| `&H5E10` | dht20 (sample, copy required) | Read DHT20 temperature/humidity sensor |
| `&H5E41` | ram_test (sample, copy required) | Switch data bank → BANK2 |
| `&H5E51` | ram_test (sample, copy required) | Switch data bank → BANK3 |
| `&H5E61` | ram_test (sample, copy required) | Restore data bank |
| `&H5E71` | ram_test (sample, copy required) | Run BANK2/3 RAM self-test |
| `&H5E81` | bank_loader (sample, already placed) | SD card file → bank RAM |
| `&H5E91` | bank_loader (sample, already placed) | FDD file → bank RAM |
