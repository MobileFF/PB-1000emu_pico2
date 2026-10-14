"""
Development script to safely verify how far display_init.py's SD_DATA_BAUD
can be raised. Ported 2026-10-14 from the sibling MSX-emulator project's
own sweep_sd_baud.py (same investigation, same sdcard.py base, same SD
wiring) -- see display_init.py's SD_DATA_BAUD comment for the background.

Throughput alone isn't enough: a marginal baud rate can finish a transfer
"successfully" (right byte count, no OSError) while the data itself is
subtly wrong (see references/sdcard_spi_mode_bug.md). So each candidate
baud rate is checked in two steps:
    1. Re-read the same block range several times and compare byte-for-byte
       against a baseline captured at the lowest (safest) candidate.
    2. Only once that matches, measure throughput.
A mismatch or OSError stops the sweep right there -- nothing higher is
tried.

Read-only (never writes), so there's no risk of corrupting the SD card's
contents.

Usage:
    mpremote run mp/test/sweep_sd_baud.py

NOTE: this only tests reads *after* the card is already initialized and
has been read from several times. The sibling project's own historical
20MHz mount failure happened on uos.mount()'s very *first* readblocks()
call (right after CMD16, a cold card) -- a different, less forgiving
moment this sweep does not reproduce. After picking a new SD_DATA_BAUD
value from this sweep's result, always also do a full cold power-cycle
boot test (the real uos.mount() at startup) before relying on it.
"""

import time
import machine
import sdcard

from display_init import (
    SPI_ID, SCK_PIN, MOSI_PIN, MISO_PIN, SD_CS_PIN,
    SD_INIT_BAUD, SD_DATA_BAUD,
)

# Lowest candidate (the current value) doubles as the safest baseline;
# step up conservatively from there. Stop at/before 20MHz -- the sibling
# project's own real-hardware mount-failure point.
CANDIDATE_BAUDS = [
    SD_DATA_BAUD,   # current value (baseline)
    6_000_000,
    8_000_000,
    10_000_000,
    12_000_000,
    16_000_000,
]

VERIFY_BLOCKS = 16          # 8KB region used for the byte-exact check
VERIFY_REPEATS = 3          # re-reads per candidate baud to confirm a match
PERF_CALLS = 256            # number of calls for the throughput measurement
BLOCKS_PER_CALL = 8         # 4KB chunks (512 x 8), matching real usage
START_BLOCK = 20000         # clear of filesystem metadata


def mount_raw_sd():
    sd_spi = machine.SPI(SPI_ID,
                          baudrate=SD_INIT_BAUD,
                          sck=machine.Pin(SCK_PIN),
                          mosi=machine.Pin(MOSI_PIN),
                          miso=machine.Pin(MISO_PIN))
    sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
    sd = sdcard.SDCard(sd_spi, sd_cs,
                        baudrate=SD_DATA_BAUD, restore_baudrate=SD_DATA_BAUD)
    return sd


def read_region(sd, start_block, nblocks):
    buf = bytearray(nblocks * 512)
    sd.readblocks(start_block, buf)
    return buf


def measure_throughput(sd, start_block, perf_calls):
    buf = bytearray(BLOCKS_PER_CALL * 512)
    t0 = time.ticks_ms()
    for i in range(perf_calls):
        sd.readblocks(start_block + i * BLOCKS_PER_CALL, buf)
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    total_bytes = perf_calls * BLOCKS_PER_CALL * 512
    kbps = (total_bytes / 1024) / (elapsed / 1000) if elapsed > 0 else float('inf')
    return elapsed, kbps


def sweep(sd, start_block=START_BLOCK):
    print("=== SD_DATA_BAUD sweep ===")
    print("candidates:", CANDIDATE_BAUDS)
    print("verify region: block {} .. {} ({} bytes)".format(
        start_block, start_block + VERIFY_BLOCKS - 1, VERIFY_BLOCKS * 512))
    print()

    baseline_baud = CANDIDATE_BAUDS[0]
    sd.baudrate = baseline_baud
    ref = read_region(sd, start_block, VERIFY_BLOCKS)
    print("[baseline {} Hz] captured {} bytes".format(baseline_baud, len(ref)))
    print()

    last_good = None
    for baud in CANDIDATE_BAUDS:
        sd.baudrate = baud
        ok = True
        err = None

        # 1. Consistency check: re-read and compare against the baseline.
        for attempt in range(VERIFY_REPEATS):
            try:
                got = read_region(sd, start_block, VERIFY_BLOCKS)
            except OSError as e:
                ok = False
                err = "OSError: {}".format(e)
                break
            if got != ref:
                ok = False
                err = "data mismatch on attempt {}".format(attempt)
                break

        if not ok:
            print("[{} Hz] NG -- {}".format(baud, err))
            print()
            print("=== stopping sweep: last known-good = {} Hz ===".format(last_good))
            break

        # 2. Throughput (only once consistency is confirmed).
        try:
            elapsed, kbps = measure_throughput(sd, start_block, PERF_CALLS)
        except OSError as e:
            print("[{} Hz] verify OK, but throughput run failed: {}".format(baud, e))
            print()
            print("=== stopping sweep: last known-good = {} Hz ===".format(last_good))
            break

        print("[{} Hz] OK -- verify x{} passed, throughput {:.1f} KB/s ({} ms / {} bytes)".format(
            baud, VERIFY_REPEATS, kbps, elapsed, PERF_CALLS * BLOCKS_PER_CALL * 512))
        last_good = baud
        print()

    # Always leave the card at the safest (baseline) baud rate when done.
    sd.baudrate = baseline_baud

    print("=== summary ===")
    print("highest verified-safe SD_DATA_BAUD in this sweep: {} Hz".format(last_good))
    print("NOTE: this only tests mid-session reads after the card is already")
    print("initialized. Before committing a new SD_DATA_BAUD value, also do a")
    print("full cold power-cycle boot test (real uos.mount() at startup).")


def main():
    sd = mount_raw_sd()
    print("SD sectors: {} (~{} MB)".format(sd.sectors, sd.sectors // 2048))
    print()
    sweep(sd)


main()
