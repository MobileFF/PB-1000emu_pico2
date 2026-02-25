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
from main import init_display,draw_bezel
from script_common import create_script_runtime

SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22

SCRIPT_VERSION = "cal-e2e-min-1plus2"

STEP_CHUNK = 512
TIMER_TICK_STEPS = 40000
BOOT_TIMEOUT_STEPS = 1_200_000
POST_BOOT_SETTLE_STEPS = 200_000
POST_INPUT_SETTLE_STEPS = 160_000
KEY_HOLD_MS = 120
KEY_HOLD_MAX_MS = 4000
INTER_KEY_GAP_MS = 1000
INPUT_LOOP_TIMEOUT_MS = 25_000
KEY_START_TIMEOUT_MS = 600
KEY_COMMIT_TIMEOUT_MS = 5_000
KEY_SCAN_WINDOW_WAIT_MS = 3_000
KEY_IDLE_WAIT_MS = 3_000
MAX_RETRIES_PER_KEY = 6

# 固定入力: 1 + 2 [EXE]
KEY_SEQUENCE = [
    ((9,6), "1"),
    ((9,3), "+"),
    ((9,5), "2"),
    ((10, 4), "EXE"),
]

def _step_runtime(system, steps, timer_accum):
    remain = steps
    while remain > 0:
        run = STEP_CHUNK if remain > STEP_CHUNK else remain
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        if not system.is_sleeping:
            system.step(run)
            #system.debug_step(pause=False,trace=True)
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


def _keybuf_snapshot(system):
    base = 0x68D9 - system.RAM_START
    return {
        "kycnt": system.ram[base + 0],
        "rd": system.ram[base + 1],
        "wr": system.ram[base + 2],
    }


def _wait_key_idle(system, timer_accum, timeout_ms=KEY_IDLE_WAIT_MS):
    started = time.ticks_ms()
    settle_started = None
    last_kycnt = None
    while True:
        now = time.ticks_ms()
        kb = _keybuf_snapshot(system)
        kycnt = kb["kycnt"]
        no_pressed = (not system.keyboard.has_key_pressed()) if hasattr(system, "keyboard") else True
        if no_pressed:
            if last_kycnt is None or kycnt != last_kycnt:
                last_kycnt = kycnt
                settle_started = now
            elif settle_started is not None and time.ticks_diff(now, settle_started) >= 300:
                return True, timer_accum
        else:
            settle_started = None
            last_kycnt = None
        if time.ticks_diff(now, started) >= timeout_ms:
            return False, timer_accum
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)


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
    active_buffered = False
    base_kycnt = 0
    hold_min_at_ms = 0
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
            kb_now = _keybuf_snapshot(system)["kycnt"]
            if kb_now != base_kycnt:
                active_buffered = True

            if chata != 0x07 or keyin != 0x80:
                active_key_started = True

            should_release = False
            # Release primarily when keyboard buffer enqueue is observed.
            # Releasing at CHATA/KEYCM milestones is too early and can drop keys.
            if time.ticks_diff(now, hold_min_at_ms) >= 0 and active_buffered:
                should_release = True

            scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))

            timed_out = time.ticks_diff(now, release_at_ms) >= 0
            if scan_gated and not should_release:
                # In scan-gated mode, avoid early host-timeout release while
                # ROM has not yet consumed the key. But if scan start is not
                # observed for too long, force release and continue.
                start_timed_out = time.ticks_diff(now, pressed_at_ms) >= KEY_START_TIMEOUT_MS
                commit_timed_out = time.ticks_diff(now, pressed_at_ms) >= KEY_COMMIT_TIMEOUT_MS
                hold_timed_out = time.ticks_diff(now, release_at_ms) >= 0
                if not active_key_started:
                    timed_out = start_timed_out
                elif active_buffered:
                    timed_out = hold_timed_out
                else:
                    timed_out = commit_timed_out or hold_timed_out

            if timed_out and not should_release:
                should_release = True

            if should_release:
                if not active_key_started:
                    print(f"WARN: no scan start observed for {active_label}; force release")
                elif not active_committed:
                    print(f"WARN: no KEYCM/KEYIN commit observed for {active_label}; force release")
                elif not active_buffered:
                    print(f"WARN: no key-buffer enqueue observed for {active_label}; force release")
                system.release_key(active_key)
                print(f"[AUTO] release key: {active_label}")
                idle_ok, timer_accum = _wait_key_idle(system, timer_accum)
                if not idle_ok:
                    st_idle = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {}
                    print(
                        "WARN: key idle wait timeout: "
                        f"CHATA={st_idle.get('chata', 0):02X} "
                        f"KEYCM={st_idle.get('keycm', 0):02X} "
                        f"KEYIN16={st_idle.get('keyin16', st_idle.get('keyin', 0)):04X}"
                    )
                if not active_committed and active_retry < MAX_RETRIES_PER_KEY:
                    next_retry = active_retry + 1
                    print(f"[AUTO] retry key: {active_label} ({next_retry}/{MAX_RETRIES_PER_KEY})")
                    queue.insert(0, {"key": active_key, "label": active_label, "retry": next_retry})
                active_key = None
                active_label = None
                active_retry = 0
                active_committed = False
                active_key_started = False
                active_buffered = False
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
                active_buffered = False
                pressed_at_ms = now
                st0 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
                base_keycm = st0.get("keycm", 0x00) if st0 else 0x00
                base_keyin16 = st0.get("keyin16", st0.get("keyin", 0x80)) if st0 else 0x0080
                base_kycnt = _keybuf_snapshot(system)["kycnt"]
                hold_min_at_ms = time.ticks_add(now, KEY_HOLD_MS)
                release_at_ms = time.ticks_add(now, KEY_HOLD_MAX_MS)

        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)

        if time.ticks_diff(now, started_ms) >= INPUT_LOOP_TIMEOUT_MS:
            print("WARN: input loop timeout")
            if active_key is not None:
                system.release_key(active_key)
            break

    return timer_accum

def init_display():
    spi = machine.SPI(
        SPI_ID,
        baudrate=40_000_000,
        sck=machine.Pin(SCK_PIN),
        mosi=machine.Pin(MOSI_PIN),
        miso=machine.Pin(MISO_PIN),
    )
    cs = machine.Pin(CS_PIN, machine.Pin.OUT)
    dc = machine.Pin(DC_PIN, machine.Pin.OUT)
    rst = machine.Pin(RST_PIN, machine.Pin.OUT)
    machine.Pin(BL_PIN, machine.Pin.OUT, value=1)

    display = ILI9341(spi, cs, dc, rst, width=320, height=240)
    display.fill_rect(0, 0, 320, 240, 0x0000)
    return display

def main():
    runtime = create_script_runtime("/log/trace_cal_mode_e2e.log")
    logger = runtime["logger"]
    logger.install_print_hook()
    try:
        if runtime["ini_path"]:
            print(f"Debug config loaded: {runtime['ini_path']}")
        if logger.trace_mode == "file":
            print(f"Trace output mode=file path={logger.trace_path}")
        else:
            print("Trace output mode=console")

        banner = f"=== PB-1000 minimal key test ({SCRIPT_VERSION}) ==="
        print(f"\n{banner}")

        display = init_display()

        debug_overrides = runtime["debug_overrides"]
        system = PB1000System(
            display=display,
            debug={
                "sys": True,
                "lcd": False,
                "kb": True,
                "c_memory": debug_overrides["c_memory"],
                "c_lcd": debug_overrides["c_lcd"],
            },
        )
        
        draw_bezel(display)
        
        print("Initialize")
        system.step(40000)

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

        system.update_display()

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
    finally:
        logger.close()


if __name__ == "__main__":
    main()
