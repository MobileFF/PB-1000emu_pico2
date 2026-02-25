"""
PB-1000 minimal key input test.

固定シーケンス "1+2[EXE]" を入力するだけの簡易スクリプト。
推定/探索ロジックは含みません。

Run on device:
    %Run -c $EDITOR_CONTENT
or:
    import test_cal_mode_e2e as t; t.main()
"""

import time
from ili9341 import ILI9341
from pb1000 import PB1000System
from main import init_display

SCRIPT_VERSION = "cal-e2e-min-1plus2"

STEP_CHUNK = 4000
TIMER_TICK_STEPS = 40000
BOOT_TIMEOUT_STEPS = 1_200_000
POST_BOOT_SETTLE_STEPS = 200_000
POST_INPUT_SETTLE_STEPS = 160_000
KEY_HOLD_MS = 120
INTER_KEY_GAP_MS = 1000
INPUT_LOOP_TIMEOUT_MS = 25_000
KEY_START_TIMEOUT_MS = 600
KEY_COMMIT_TIMEOUT_MS = 5_000
KEY_SCAN_WINDOW_WAIT_MS = 3_000
MAX_RETRIES_PER_KEY = 6

# 固定入力: 1 + 2 [EXE]
KEY_SEQUENCE = [
    ((9,2), "1"),
#     ((9,5), "+"),
#     ((9,3), "2"),
#     ((10, 4), "EXE"),
]


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
    candidates = [
        ("/roms/rom0.bin", "/roms/rom1.bin"),
        ("roms/rom0.bin", "roms/rom1.bin"),
    ]
    for r0, r1 in candidates:
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
            return True, timer_accum, done
    return False, timer_accum, done


def _run_sequence_main_style(system, timer_accum, sequence):
    queue = [{"key": key, "label": label, "retry": 0} for key, label in sequence]
    active_key = None
    active_label = None
    active_retry = 0
    active_committed = False
    release_at_ms = 0
    pressed_at_ms = 0
    base_keycm = 0
    base_keyin16 = 0x0080
    next_press_at_ms = 0
    active_key_started = False
    started_ms = time.ticks_ms()
    scan_window_wait_from_ms = 0

    while queue or active_key is not None:
        now = time.ticks_ms()

        if active_key is not None:
            st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
            chata = st["chata"] if st else 0x00
            keyin = st["keyin"] if st else 0x80
            keycm = st["keycm"] if st else 0x00
            keyin16 = st.get("keyin16", keyin) if st else keyin
            committed = (keycm != base_keycm) or (keyin16 != base_keyin16)
            if committed:
                active_committed = True

            if chata != 0x07 or keyin != 0x80:
                active_key_started = True

            should_release = False
            if active_key_started and committed:
                if hasattr(system, "can_release_active_key"):
                    should_release = system.can_release_active_key()
                else:
                    should_release = (chata == 0x20)

            scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))
            if scan_gated and active_key_started and committed and (not should_release):
                if chata == 0x07 and keyin != 0x80:
                    should_release = True

            timed_out = time.ticks_diff(now, release_at_ms) >= 0
            if scan_gated and not should_release:
                # In scan-gated mode, avoid early host-timeout release while
                # ROM has not yet consumed the key. But if scan start is not
                # observed for too long, force release and continue.
                start_timed_out = time.ticks_diff(now, pressed_at_ms) >= KEY_START_TIMEOUT_MS
                commit_timed_out = time.ticks_diff(now, pressed_at_ms) >= KEY_COMMIT_TIMEOUT_MS
                timed_out = start_timed_out if (not active_key_started) else commit_timed_out

            if timed_out and not should_release:
                should_release = True

            if should_release:
                if not active_key_started:
                    print(f"WARN: no scan start observed for {active_label}; force release")
                elif not active_committed:
                    print(f"WARN: no KEYCM/KEYIN commit observed for {active_label}; force release")
                system.release_key(active_key)
                print(f"[AUTO] release key: {active_label}")
                if not active_committed and active_retry < MAX_RETRIES_PER_KEY:
                    next_retry = active_retry + 1
                    print(f"[AUTO] retry key: {active_label} ({next_retry}/{MAX_RETRIES_PER_KEY})")
                    queue.insert(0, {"key": active_key, "label": active_label, "retry": next_retry})
                active_key = None
                active_label = None
                active_retry = 0
                active_committed = False
                active_key_started = False
                next_press_at_ms = time.ticks_add(now, INTER_KEY_GAP_MS)

        if active_key is None and queue:
            if time.ticks_diff(now, next_press_at_ms) >= 0:
                if hasattr(system, "is_key_input_enabled") and not system.is_key_input_enabled():
                    timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)
                    continue
                if hasattr(system, "get_key_scan_state"):
                    stp = system.get_key_scan_state()
                    chata_p = stp.get("chata", 0x00)
                    # Press only at CHATA=00. Starting at 0x07 is often too
                    # early and can miss KEYCM/KEYIN commit for the first key.
                    if chata_p != 0x00:
                        if scan_window_wait_from_ms == 0:
                            scan_window_wait_from_ms = now
                        elif time.ticks_diff(now, scan_window_wait_from_ms) >= KEY_SCAN_WINDOW_WAIT_MS:
                            print(f"WARN: scan window wait timeout (CHATA={chata_p:02X}); press anyway")
                            scan_window_wait_from_ms = 0
                        else:
                            timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)
                            continue
                    else:
                        scan_window_wait_from_ms = 0
                item = queue.pop(0)
                key = item["key"]
                label = item["label"]
                active_retry = item.get("retry", 0)
                print(f"[AUTO] press key: {label}")
                system.press_key(key)
                active_key = key
                active_label = label
                active_committed = False
                active_key_started = False
                pressed_at_ms = now
                st0 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
                base_keycm = st0.get("keycm", 0x00) if st0 else 0x00
                base_keyin16 = st0.get("keyin16", st0.get("keyin", 0x80)) if st0 else 0x0080
                release_at_ms = time.ticks_add(now, KEY_HOLD_MS)

        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)

        if time.ticks_diff(now, started_ms) >= INPUT_LOOP_TIMEOUT_MS:
            print("WARN: input loop timeout")
            if active_key is not None:
                system.release_key(active_key)
            break

    return timer_accum


def main():
    print(f"\n=== PB-1000 minimal key test ({SCRIPT_VERSION}) ===")

#     try:
#         display = ILI9341(use_framebuf=False)
#     except TypeError:
#         display = ILI9341()

    system = PB1000System(
        display=init_display(),
        debug={"sys": True, "lcd": False, "kb": True},
    )

    print(f"LCD backend: {getattr(system, '_LCD_BACKEND', 'unknown') if hasattr(system, '_LCD_BACKEND') else 'see pb1000.py'}")

    if not _load_roms(system):
        print("ERROR: ROM load failed")
        return

    enabled, timer_accum, boot_steps = _wait_key_enable(system)
    if enabled:
        print(f"Boot ready: KEY input enabled after {boot_steps} steps")
    else:
        print(f"WARN: KEY input not enabled within timeout ({boot_steps} steps)")

    timer_accum = _step_runtime(system, POST_BOOT_SETTLE_STEPS, timer_accum)

    timer_accum = _run_sequence_main_style(system, timer_accum, KEY_SEQUENCE)

    timer_accum = _step_runtime(system, POST_INPUT_SETTLE_STEPS, timer_accum)

    if hasattr(system, "get_key_scan_state"):
        st = system.get_key_scan_state()
        keyin16 = st.get("keyin16")
        if keyin16 is None:
            keyin16 = st.get("keyin", 0)
        print(
            "FINAL KEY STATE: "
            f"KYSTA={st.get('kysta', 0):02X} "
            f"CHATA={st.get('chata', 0):02X} "
            f"KEYCM={st.get('keycm', 0):02X} "
            f"KEYIN16={keyin16:04X}"
        )

    print("EDTOP VRAM dump:")
    system.dump_edtop_vram()

    print("Done: injected fixed sequence '1+2[EXE]'.")


if __name__ == "__main__":
    main()
