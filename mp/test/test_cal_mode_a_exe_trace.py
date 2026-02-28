"""
CAL モードでキー "A" を押し、続いて EXE を押した直後から 1000 ステップ分の
トレースをファイルに残す簡易テストスクリプト。
"""

import time
from ili9341 import ILI9341
from pb1000 import PB1000System
from main import init_display, draw_bezel
from script_common import create_script_runtime
import force_gc

"""
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


def draw_bezel(display):
    display.fill_rect(12, 36, 296, 72, 0x4228)
    display.fill_rect(14, 38, 292, 68, 0x8410)
    display.fill_rect(16, 40, 288, 64, 0xB5E6)
"""


# --- copy relevant constants from existing script ---
SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22

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

# キーシーケンス: A と EXE
KEY_SEQUENCE = [
    ((4, 4), "A"),      # 行4, 列4 は 'A' キー
    ((10, 4), "EXE"),   # 以前のシーケンスで使った座標
]

# 以下のヘルパー関数は元スクリプトと同じ
# 以下のヘルパー関数は元スクリプトと同じ

def _step_runtime(system, steps, timer_accum):
    remain = steps
    while remain > 0:
        run = STEP_CHUNK if remain > STEP_CHUNK else remain
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        if not system.is_sleeping:
            pc = system.pc
            system.step(run,stop_pc=0x9A3C)
            if pc==0x9A3C:
                print(f"[{pc:04x}]",end="")
                system.print_registers()
        else:
            time.sleep_ms(1)
        timer_accum += run
        while timer_accum >= TIMER_TICK_STEPS:
            system.tick_timer()
            timer_accum -= TIMER_TICK_STEPS
        remain -= run
        # print(".",end="")
    return timer_accum


# def _step_runtime(system, steps, timer_accum, trace_file=None):
#     """Advance ``steps`` ticks, optionally logging each step.
# 
#     ``trace_file`` may be a file-like object; if provided we switch to
#     ``debug_step`` mode on each iteration and write the resulting text.
#     This ensures no cycles are dropped when we perform long waits inside the
#     run-sequence helpers.
# 
#     Additionally, when we're tracing we look for a few PC values that
#     indicate imminent OM error (A8AA, 9A2F, 9A3C, ABBD) and emit a register
#     snapshot with a bit of RAM context so that the failure conditions can be
#     diagnosed more easily in the log.
#     """
#     interesting = {0xA8AA, 0x9A2F, 0x9A3C, 0xABBD}
#     remain = steps
#     while remain > 0:
#         run = STEP_CHUNK if remain > STEP_CHUNK else remain
#         if hasattr(system, "service_input_lines"):
#             system.service_input_lines()
#         if trace_file is not None:
#             # slow but unavoidable when tracing every step
#             for _ in range(run):
#                 line = system.debug_step(pause=False, trace=True, prt=False)
#                 try:
#                     #trace_file.write(line)
#                     #trace_file.write("\n")
#                     pass
#                 except OSError as e:
#                     # disk full or other I/O error; stop tracing but continue
#                     print(f"WARN: trace file write failed ({e}); disabling further logging")
#                     # trace_file = None
#                     break
#                 # check PC from the returned string (``[hhhh]`` prefix)
#                 try:
#                     pc = int(line.split("]")[0].lstrip("["), 16)
#                     #print(f"{pc:x}")
#                 except Exception as e:
#                     #import sys
#                     #sys.print_exception(e)
#                     pc = None
#                 if pc in interesting:
#                     #print(f"{pc:4x} in interesting")
#                     # dump registers and a few RAM words around IZ+SX
#                     snap = system.get_register_snapshot()
#                     # always echo to console so we don't lose the info
#                     #print(f"# snapshot at PC={pc:04X} {snap}")
#                     # compute IZ+SX pointer
#                     iz = snap.get("iz", 0)
#                     sx = snap.get("sx", 0)
#                     addr = (iz + sx) & 0xFFFF
#                     if 0 <= addr - system.RAM_START < len(system.ram):
#                         off = addr - system.RAM_START
#                         chunk = bytes(system.ram[off:off+8])
#                         #print(f"addr={addr:04X}")
#                         print(f"# RAM[{addr:04X}..]=" + " ".join(f"{b:02X}" for b in chunk))
#                     # attempt to write to trace file but ignore failures
#                     #if trace_file is not None:
#                     try:
#                         trace_file.write(f"# snapshot at PC={pc:04X} {snap}\n")
#                         print("snapshot")
#                     except OSError:
#                         # trace_file = None
#                         print("file write failed")
#                     else:
#                         if 0 <= addr - system.RAM_START < len(system.ram):
#                             #try:
#                             trace_file.write(f"# RAM[{addr:04X}..]=" + " ".join(f"{b:02X}" for b in chunk) + "\n")
#                             #except OSError:
#                             #    trace_file = None
#         else:
#             if not system.is_sleeping:
#                 system.step(run)
#             else:
#                 time.sleep_ms(1)
#         timer_accum += run
#         while timer_accum >= TIMER_TICK_STEPS:
#             system.tick_timer()
#             timer_accum -= TIMER_TICK_STEPS
#         remain -= run
#         # print(".",end="")
#     return timer_accum


def _load_roms(system):
    candidates = [
        ("/roms/rom0.bin", "/roms/rom1.bin"),
        ("roms/rom0.bin", "roms/rom1.bin"),
    ]
    for r0, r1 in candidates:
        try:
            # keep copy for later assertions
            system.load_rom(r0, slot=0, keep_copy=True)
            system.load_rom(r1, slot=1, keep_copy=True)
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


# 以下は元スクリプトと同一の補助関数群をそのままコピーしています

def _keybuf_snapshot(system):
    base = 0x68D9 - system.RAM_START
    return {
        "kycnt": system.ram[base + 0],
        "rd": system.ram[base + 1],
        "wr": system.ram[base + 2],
    }


def _wait_key_idle(system, timer_accum, timeout_ms=KEY_IDLE_WAIT_MS, trace_file=None):
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
        # timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum, trace_file)
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)


def _run_sequence_main_style(
    system,
    timer_accum,
    sequence,
    *,
    pre_trace_steps=0,
    exe_trace_steps=0,
    exe_trace_base=None,
    key_hold_ms=None,
    key_hold_max_ms=None,
    key_commit_timeout_ms=None,
    scan_window_wait_ms=None,
    key_idle_wait_ms=None,
):
    """Run key sequence using main-style algorithm.

    * ``pre_trace_steps`` – if >0, take this many debug steps
      **before** the first key press and write them to the trace file.
    * ``exe_trace_steps`` – number of steps to record **after** EXE is
      pressed (the previous behaviour).
    * ``exe_trace_base`` – base pathname used for both pieces of the trace;
      ``_steps.log`` will be appended to it.
    * ``key_hold_ms`` – how many milliseconds minimum to keep a key held
      before attempting release; defaults to global ``KEY_HOLD_MS``.
    * ``key_hold_max_ms`` – maximum hold time before forced release;
      defaults to ``KEY_HOLD_MAX_MS``.
    * ``key_commit_timeout_ms`` – timeout used when scanning; defaults to
      ``KEY_COMMIT_TIMEOUT_MS``.

    A single file handle is used for both pre‑ and post‑tracing so that the
    output forms one continuous block.
    """
    """Run key sequence using main-style algorithm.

    * ``pre_trace_steps`` – if >0, take this many debug steps
      **before** the first key press and write them to the trace file.
    * ``exe_trace_steps`` – number of steps to record **after** EXE is
      pressed (the previous behaviour).
    * ``exe_trace_base`` – base pathname used for both pieces of the trace;
      ``_steps.log`` will be appended to it.

    A single file handle is used for both pre‑ and post‑tracing so that the
    output forms one continuous block.
    """
    queue = [{"key": key, "label": label, "retry": 0} for key, label in sequence]
    # list of PCs where we also want to log registers during the main loop
    interesting = {0xA8AA, 0x9A2F, 0x9A3C, 0xABBD}
    pre_file = None
    pre_file = None
    exe_tracing = False
    exe_file = None
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
    hold_timed_out = 4000

    # open trace file once if either phase requested
    current_trace_file = None
    if (pre_trace_steps or exe_trace_steps) and exe_trace_base:
        # write all steps into the base path itself, not a _steps suffix
        try:
            pre_file = open(exe_trace_base, "w")
            current_trace_file = pre_file
        except OSError:
            pre_file = None
    # perform pre-trace before entering loop
    if current_trace_file is not None and pre_trace_steps > 0:
        for _ in range(pre_trace_steps):
            current_trace_file.write(system.debug_step(pause=False, trace=True,prt=False))
            current_trace_file.write("\n")
        # leave file open for exe tracing

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
            if time.ticks_diff(now, hold_min_at_ms) >= 0 and active_buffered:
                should_release = True

            scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))

            timed_out = time.ticks_diff(now, release_at_ms) >= 0
            if scan_gated and not should_release:
                start_timed_out = time.ticks_diff(now, pressed_at_ms) >= KEY_START_TIMEOUT_MS
                cto = key_commit_timeout_ms if key_commit_timeout_ms is not None else KEY_COMMIT_TIMEOUT_MS
                commit_timed_out = time.ticks_diff(now, pressed_at_ms) >= cto
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
                idle_ok, timer_accum = _wait_key_idle(system, timer_accum, trace_file=current_trace_file)
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
                    timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum, current_trace_file)
                    continue
                if hasattr(system, "get_key_scan_state"):
                    stp = system.get_key_scan_state()
                    chata_p = stp.get("chata", 0x00)
                    if chata_p != 0x00:
                        if scan_window_wait_from_ms == 0:
                            scan_window_wait_from_ms = now
                        elif time.ticks_diff(now, scan_window_wait_from_ms) >= swms:
                            print(f"WARN: scan window wait timeout (CHATA={chata_p:02X}); press anyway")
                            scan_window_wait_from_ms = 0
                        else:
                            timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum, current_trace_file)
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
                # allow caller to override timing
                hms = key_hold_ms if key_hold_ms is not None else KEY_HOLD_MS
                hmax = key_hold_max_ms if key_hold_max_ms is not None else KEY_HOLD_MAX_MS
                hold_min_at_ms = time.ticks_add(now, hms)
                release_at_ms = time.ticks_add(now, hmax)

        # check PC every iteration in case the interesting address is hit
        try:
            pc = system.get_register_snapshot().get("pc")
        except Exception:
            pc = None
        if pc in interesting:
            snap = system.get_register_snapshot()
            print(f"# snapshot at PC={pc:04X} {snap}")
            iz = snap.get("iz", 0)
            sx = snap.get("sx", 0)
            addr = (iz + sx) & 0xFFFF
            if 0 <= addr - system.RAM_START < len(system.ram):
                off = addr - system.RAM_START
                chunk = bytes(system.ram[off:off+8])
                print(f"# RAM[{addr:04X}..]=" + " ".join(f"{b:02X}" for b in chunk))
        # timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum, current_trace_file)
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)

        if time.ticks_diff(now, started_ms) >= INPUT_LOOP_TIMEOUT_MS:
            print("WARN: input loop timeout")
            if active_key is not None:
                system.release_key(active_key)
            break

    # if we exited loop before completing post‑EXE trace, continue now
    while pre_file is not None and exe_trace_steps > 0:
        try:
            pre_file.write(system.debug_step(pause=False, trace=True,prt=False))
            pre_file.write("\n")
        except Exception:
            pass
        exe_trace_steps -= 1
        timer_accum += 1
    if pre_file is not None:
        pre_file.close()
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
    import gc
    print('free', gc.mem_free(), 'alloc', gc.mem_alloc())
    # create a timestamped base path for trace outputs
    t = time.localtime()
    stamp = "{:04d}{:02d}{:02d}{:02d}{:02d}{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )
    trace_base = f"/log/trace_cal_a_exe_{stamp}.log"
    runtime = create_script_runtime(trace_base)
    # ensure INI cannot force a different trace file (e.g. trace_after_key)
    if runtime.get("ini_data") and "trace" in runtime["ini_data"]:
        tcfg = runtime["ini_data"]["trace"]
        tcfg["trace_output"] = "file"
        tcfg["trace_output_path"] = trace_base
        tcfg["trace_output_rotate_per_run"] = False
    # rebuild logger with updated configuration
    from script_common import LazyLogger
    runtime["logger"] = LazyLogger(runtime.get("ini_data"), trace_base)
    import gc
    print('free', gc.mem_free(), 'alloc', gc.mem_alloc())
    logger = runtime["logger"]
    if logger is not None:
        logger.install_print_hook()
    try:
        if runtime["ini_path"]:
            print(f"Debug config loaded: {runtime['ini_path']}")
        if logger.trace_mode == "file":
            print(f"Trace output mode=file path={logger.trace_path}")
        else:
            print("Trace output mode=console")

        banner = "=== PB-1000 CAL A[EXE] trace ==="
        print(f"\n{banner}")

        display = init_display()

        debug_overrides = runtime["debug_overrides"]
        system = PB1000System(
            display=display,
            debug={
                "sys": False,
                "lcd": False,
                "kb": False,
                #"c_memory": debug_overrides["c_memory"],
                #"c_lcd": debug_overrides["c_lcd"],
            },
            restore_registers=False,
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

        # allow INI to override timing parameters for key hold/release
        run_cfg = runtime.get("ini_data", {}).get("run", {}) or {}
        try:
            ihold = int(run_cfg.get("key_hold_ms", KEY_HOLD_MS))
        except Exception:
            ihold = KEY_HOLD_MS
        try:
            iholdmax = int(run_cfg.get("key_hold_max_ms", KEY_HOLD_MAX_MS))
        except Exception:
            iholdmax = KEY_HOLD_MAX_MS
        try:
            icto = int(run_cfg.get("key_commit_timeout_ms", KEY_COMMIT_TIMEOUT_MS))
        except Exception:
            icto = KEY_COMMIT_TIMEOUT_MS

        timer_accum = _run_sequence_main_style(
            system,
            timer_accum,
            KEY_SEQUENCE,
            exe_trace_steps=5000,
            exe_trace_base=trace_base,
            key_hold_ms=ihold,
            key_hold_max_ms=iholdmax,
            key_commit_timeout_ms=icto,
        )


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

        print("Done")
    finally:
        logger.close()


if __name__ == "__main__":
    main()


