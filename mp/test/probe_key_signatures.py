"""
Probe PB-1000 key matrix signatures (KEYCM/KEYIN) and display side effects.

Purpose:
- Identify which (row,col) generates formula-usable key codes.
- Detect keys that only update status-tail area (MENU/LCK-like side effect).

Run:
    %Run -c $EDITOR_CONTENT
or:
    import probe_key_signatures as p; p.main()
"""

import time
from pb1000 import PB1000System

STEP_CHUNK = 4000
TIMER_TICK_STEPS = 40000
BOOT_TIMEOUT_STEPS = 1_200_000
POST_BOOT_SETTLE_STEPS = 120_000
POST_KEY_SETTLE_STEPS = 24_000
KEY_COMMIT_TIMEOUT_STEPS = 120_000

EDTOP_START = 0x6100
EDTOP_END = 0x61FF
LEDTP_START = 0x6201
LEDTP_END = 0x6850
STATUS_TAIL_ADDR = 0x6800

SCAN_ROWS = range(13)
SCAN_COLS = range(12)


def _step_runtime(system, steps, timer_accum):
    remain = steps
    while remain > 0:
        run = STEP_CHUNK if remain > STEP_CHUNK else remain
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        if not system.is_sleeping:
            system.step(run)
        else:
            time.sleep_ms(1)
        timer_accum += run
        while timer_accum >= TIMER_TICK_STEPS:
            system.tick_timer()
            timer_accum -= TIMER_TICK_STEPS
        remain -= run
    return timer_accum


def _load_roms(system):
    for r0, r1 in (("/roms/rom0.bin", "/roms/rom1.bin"), ("roms/rom0.bin", "roms/rom1.bin")):
        try:
            system.load_rom(r0, slot=0)
            system.load_rom(r1, slot=1)
            if len(system.rom0) > 0 and len(system.rom1) > 0:
                return True
        except Exception:
            pass
    return False


def _wait_key_enable(system):
    timer_accum = 0
    done = 0
    while done < BOOT_TIMEOUT_STEPS:
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)
        done += STEP_CHUNK
        if hasattr(system, "is_key_input_enabled") and system.is_key_input_enabled():
            return True, timer_accum
    return False, timer_accum


def _ram_slice(system, start_addr, end_addr):
    buf = bytearray()
    for a in range(start_addr, end_addr + 1):
        buf.append(system._mem_read_impl(0, a))
    return bytes(buf)


def _count_diff(a, b):
    n = 0
    for i in range(len(a)):
        if a[i] != b[i]:
            n += 1
    return n


def _collect_changed_addrs(before, after, base_addr):
    out = []
    for i in range(len(before)):
        if before[i] != after[i]:
            out.append(base_addr + i)
    return out


def _press_probe_key(system, key, timer_accum):
    st0 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {"chata": 0, "keycm": 0, "keyin": 0}

    if hasattr(system, "key_reassert_enabled"):
        system.key_reassert_enabled = True

    system.press_key(key)

    waited = 0
    while waited < KEY_COMMIT_TIMEOUT_STEPS:
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)
        waited += STEP_CHUNK
        if hasattr(system, "can_release_active_key") and system.can_release_active_key():
            break

    system.release_key(key)
    if hasattr(system, "key_reassert_enabled"):
        system.key_reassert_enabled = False

    timer_accum = _step_runtime(system, POST_KEY_SETTLE_STEPS, timer_accum)

    st1 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {"chata": 0, "keycm": 0, "keyin": 0}
    return timer_accum, st0, st1


def main():
    print("KEY signature probe start")

    system = PB1000System(
        display=None,
        debug={"sys": False, "lcd": False, "kb": False},
        restore_registers=True,
    )
    if not _load_roms(system):
        print("FAIL: ROM load failed")
        return

    system.power_on()
    ok, timer_accum = _wait_key_enable(system)
    if not ok:
        print("FAIL: key input not enabled")
        return

    timer_accum = _step_runtime(system, POST_BOOT_SETTLE_STEPS, timer_accum)
    print("row,col,CM0,IN0,MD0,RP0,CM1,IN1,MD1,RP1,EDTOP_DIFF,LEDTP_DIFF,TAIL_ONLY")

    for row in SCAN_ROWS:
        for col in SCAN_COLS:
            key = (row, col)

            ed0 = _ram_slice(system, EDTOP_START, EDTOP_END)
            ld0 = _ram_slice(system, LEDTP_START, LEDTP_END)

            timer_accum, st0, st1 = _press_probe_key(system, key, timer_accum)

            ed1 = _ram_slice(system, EDTOP_START, EDTOP_END)
            ld1 = _ram_slice(system, LEDTP_START, LEDTP_END)

            ed_diff = _count_diff(ed0, ed1)
            ld_diff = _count_diff(ld0, ld1)

            ld_changed = _collect_changed_addrs(ld0, ld1, LEDTP_START)
            tail_only = 0
            if ld_changed and ed_diff == 0:
                all_tail = True
                for addr in ld_changed:
                    if addr < STATUS_TAIL_ADDR:
                        all_tail = False
                        break
                if all_tail:
                    tail_only = 1

            print(
                f"{row},{col},"
                f"{st0.get('keycm', 0):02X},{st0.get('keyin', 0):02X},{st0.get('keymd', 0):02X},{st0.get('kyrep', 0):02X},"
                f"{st1.get('keycm', 0):02X},{st1.get('keyin', 0):02X},{st1.get('keymd', 0):02X},{st1.get('kyrep', 0):02X},"
                f"{ed_diff},{ld_diff},{tail_only}"
            )


if __name__ == "__main__":
    main()
