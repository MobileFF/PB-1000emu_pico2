# CMake file for Waveshare RP2350-PiZero
#
# 2026-10-21: ported from the sibling MSX_emu_pico2 project's own
# WAVESHARE_RP2350_PIZERO board (same physical board). Modeled on
# MicroPython's stock WAVESHARE_RP2350B_CORE board
# (~/projects/micropython.pb1000/ports/rp2/boards/WAVESHARE_RP2350B_CORE/)
# -- same RP2350B chip, same "no official pico-sdk support, supply our own
# board header" pattern. This directory is passed to the rp2 port build via
# BOARD_DIR=<...>/src/boards/WAVESHARE_RP2350_PIZERO (see bldfrm.sh's
# pizero target), not copied into the MicroPython checkout itself, so it
# stays version-controlled with the rest of this project like
# micropython.cmake does.
#
# Board facts confirmed against the vendor schematic + a working reference
# port (github.com/ugufru/xroar-waveshare-rp2350-pizero, README "Target
# hardware" section, 2026-09 snapshot, via MSX_emu_pico2's own port): RP2350B,
# 48 GPIO, 520KB SRAM, 16MB onboard QSPI flash, PSRAM pad present on the PCB
# but NOT populated on stock boards -- treated as SRAM-only, unlike
# WAVESHARE_RP2350B_CORE which does populate/enable PSRAM.
#
# PB-1000_emu_AG2-SPECIFIC CAVEAT (2026-10-21, NOT YET REAL-HARDWARE
# VERIFIED on this board for THIS project): unlike MSX_emu_pico2 (which only
# lazily-imports a couple of menu modules and never froze them), this
# project already freezes almost every mp/*.py file into the firmware
# image (src/manifest_freeze.py, established 2026-10-17 -- see CLAUDE.md).
# MSX_emu_pico2 found that adding frozen bytecode content on this exact
# board shifted its hand-tuned RAM/flash layout enough to cause intermittent
# mpremote/UART "No such device" failures (see that project's own
# mpconfigboard.cmake history) -- it is NOT yet known whether this
# project's much larger existing freeze set hits the same class of problem
# on this board. Watch for this specifically during first real-hardware
# bring-up; if it reproduces, the next thing to try is what MSX_emu_pico2's
# own comment suggests (keep DVI quiet during first draw) rather than
# un-freezing modules (which would reintroduce the cross-session dev-
# workflow problems freezing was originally adopted to solve -- see
# CLAUDE.md's "2026-10-17" note).

set(PICO_NUM_GPIOS 48)

# No official pico-sdk board support for this board -- add our own header
# search path and PICO_BOARD name so pico-sdk looks for
# waveshare_rp2350_pizero.h in this same directory.
list(APPEND PICO_BOARD_HEADER_DIRS ${MICROPY_BOARD_DIR})
set(PICO_BOARD "waveshare_rp2350_pizero")

if(NOT DEFINED MICROPY_HW_FLASH_STORAGE_BYTES)
    set(MICROPY_HW_FLASH_STORAGE_BYTES 14680064)  # 14 * 1024 * 1024 (16MB flash, same split as WAVESHARE_RP2350B_CORE)
endif()
