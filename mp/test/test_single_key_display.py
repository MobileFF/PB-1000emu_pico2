"""
PB-1000 single-key display test.

Purpose:
- Boot emulator to CAL mode runtime.
- Inject one key press only (default: "1").
- Check whether key commit and display-side buffers changed.

Run on device:
    %Run -c $EDITOR_CONTENT
or:
    import test_single_key_display as t; t.main()
"""

import time
from pb1000 import PB1000System
from main import init_display,draw_bezel
try:
    import os
except ImportError:
    import uos as os

SCRIPT_VERSION = "single-key-display-v1"

STEP_CHUNK = 4000
TIMER_TICK_STEPS = 40000
BOOT_TIMEOUT_STEPS = 1_200_000
POST_BOOT_SETTLE_STEPS = 160_000
POST_INPUT_SETTLE_STEPS = 120_000
KEY_HOLD_MS = 140
KEY_HOLD_MAX_MS = 4000
COMMIT_TIMEOUT_MS = 6000
SCAN_WINDOW_WAIT_MS = 3000
KEY_IDLE_WAIT_MS = 3000

# Default target key: "1"
KEY_TO_TEST = ((9, 6), "1")


def _to_bool(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def _to_int(text):
    return int(str(text).strip(), 0)


def _parse_ini(path):
    data = {}
    current = ""
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip().lower()
                if current not in data:
                    data[current] = {}
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.split(";", 1)[0].split("#", 1)[0].strip()
            if current not in data:
                data[current] = {}
            data[current][key] = value
    return data


def load_trace_config():
    cfg = {
        "trace_steps": 1000,
        "trace_steps_per_loop": 16,
        "trace_trigger_label": "1",
        "trace_exit_on_finish": True,
        "trace_dump_on_finish": True,
        "trace_output": "console",
        "trace_output_path": "/log/trace_after_key.log",
        "trace_output_rotate_per_run": True,
        "trace_force_on_no_commit": True,
        # 0: disable service_input_lines during instruction trace
        # N>0: call service_input_lines every N traced instructions
        "trace_service_input_every": 0,
    }
    ini_paths = ("debug.ini", "/debug.ini", "/mp/debug.ini")
    ini_data = None
    loaded_path = ""
    for path in ini_paths:
        try:
            ini_data = _parse_ini(path)
            loaded_path = path
            break
        except OSError:
            pass
    if not ini_data:
        print("Trace config not found. Using defaults.")
        cfg["loaded_path"] = ""
        return cfg

    trace = ini_data.get("trace", {})
    if "trace_steps" in trace:
        cfg["trace_steps"] = _to_int(trace["trace_steps"])
    if "trace_steps_per_loop" in trace:
        cfg["trace_steps_per_loop"] = _to_int(trace["trace_steps_per_loop"])
    if "trace_trigger_label" in trace:
        cfg["trace_trigger_label"] = str(trace["trace_trigger_label"]).strip()
    if "trace_exit_on_finish" in trace:
        cfg["trace_exit_on_finish"] = _to_bool(trace["trace_exit_on_finish"])
    if "trace_dump_on_finish" in trace:
        cfg["trace_dump_on_finish"] = _to_bool(trace["trace_dump_on_finish"])
    if "trace_output" in trace:
        cfg["trace_output"] = str(trace["trace_output"]).strip().lower()
    if "trace_output_path" in trace:
        cfg["trace_output_path"] = str(trace["trace_output_path"]).strip()
    if "trace_output_rotate_per_run" in trace:
        cfg["trace_output_rotate_per_run"] = _to_bool(trace["trace_output_rotate_per_run"])
    if "trace_force_on_no_commit" in trace:
        cfg["trace_force_on_no_commit"] = _to_bool(trace["trace_force_on_no_commit"])
    if "trace_service_input_every" in trace:
        cfg["trace_service_input_every"] = _to_int(trace["trace_service_input_every"])
    cfg["loaded_path"] = loaded_path
    return cfg


def _path_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _split_path_dir_file(path):
    idx = path.rfind("/")
    if idx < 0:
        return "", path
    return path[:idx], path[idx + 1:]


def _join_path(dir_path, file_name):
    if not dir_path:
        return file_name
    if dir_path.endswith("/"):
        return dir_path + file_name
    return dir_path + "/" + file_name


def _rotate_trace_output_path(base_path):
    dir_path, file_name = _split_path_dir_file(base_path)
    dot = file_name.rfind(".")
    if dot > 0:
        stem = file_name[:dot]
        ext = file_name[dot:]
    else:
        stem = file_name
        ext = ""
    for i in range(1, 10000):
        cand = _join_path(dir_path, f"{stem}_{i:04d}{ext}")
        if not _path_exists(cand):
            return cand
    return _join_path(dir_path, f"{stem}_{time.ticks_ms()}{ext}")


def _init_trace_output(trace_cfg):
    mode = trace_cfg["trace_output"]
    path = trace_cfg["trace_output_path"] or "trace_output.log"
    rotate = bool(trace_cfg["trace_output_rotate_per_run"])
    if mode == "file":
        cand_paths = [path]
        if path.startswith("/"):
            cand_paths.append(path[1:])
            cand_paths.append("/mp/" + path[1:])
        for cand in cand_paths:
            out_path = cand
            if rotate:
                out_path = _rotate_trace_output_path(out_path)
            try:
                stream = open(out_path, "w")

                def _trace_print(msg=""):
                    stream.write(str(msg))
                    stream.write("\n")
                    stream.flush()

                print(f"[TRACE] output mode=file path={out_path}")
                return _trace_print, stream
            except OSError as e:
                print(f"[TRACE] output file open failed ({out_path}): {e}")
        print("[TRACE] all file candidates failed. Fallback to console.")

    def _trace_print_console(msg=""):
        print(msg)

    return _trace_print_console, None


def _run_instruction_trace(system, timer_accum, trace_cfg, trace_print):
    remain = trace_cfg["trace_steps"]
    step_idx = 0
    trace_print(f"[TRACE] tracing next {remain} instructions")
    while remain > 0:
        block = trace_cfg["trace_steps_per_loop"]
        if block > remain:
            block = remain
        for _ in range(block):
            every = int(trace_cfg.get("trace_service_input_every", 0))
            if every > 0 and hasattr(system, "service_input_lines"):
                if (step_idx % every) == 0:
                    system.service_input_lines()
            if not system.is_sleeping:
                step_idx += 1
                system.debug_step(pause=False, trace=True, trace_index=step_idx, out=trace_print)
            else:
                time.sleep_ms(1)
            timer_accum += 1
            while timer_accum >= TIMER_TICK_STEPS:
                system.tick_timer()
                timer_accum -= TIMER_TICK_STEPS
        remain -= block
    trace_print("[TRACE] Trace finished")
    return timer_accum


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


def _edtop_bytes(system, length=64):
    base = 0x6100 - system.RAM_START
    out = []
    for i in range(length):
        out.append(system.ram[base + i])
    return bytes(out)


def _diff_bytes(before, after, base_addr):
    changed = []
    n = len(before)
    for i in range(n):
        if before[i] != after[i]:
            changed.append((base_addr + i, before[i], after[i]))
    return changed


def _keybuf_snapshot(system):
    base = 0x68D9 - system.RAM_START
    kycnt = system.ram[base + 0]
    rd = system.ram[base + 1]
    wr = system.ram[base + 2]
    size = system.ram[base + 3]
    buf_addr = system.ram[base + 4] | (system.ram[base + 5] << 8)
    buf = bytes(system.ram[base + 6: base + 6 + 16])
    return {
        "kycnt": kycnt,
        "rd": rd,
        "wr": wr,
        "size": size,
        "buf_addr": buf_addr,
        "buf": buf,
    }


def _wait_for_scan_window(system, timer_accum):
    started = time.ticks_ms()
    while True:
        if not hasattr(system, "get_key_scan_state"):
            return timer_accum
        st = system.get_key_scan_state()
        if st.get("chata", 0xFF) == 0x00:
            return timer_accum
        if time.ticks_diff(time.ticks_ms(), started) >= SCAN_WINDOW_WAIT_MS:
            print(f"WARN: scan window wait timeout (CHATA={st.get('chata', 0):02X}); proceed")
            return timer_accum
        timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)


def _wait_key_idle(system, timer_accum, timeout_ms=KEY_IDLE_WAIT_MS):
    # CHATA/KEYCM are not guaranteed to return to fixed constants after a key.
    # Use practical idle criteria:
    # - no host-side key is held
    # - keyboard buffer count stays unchanged for a short settle window
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


def _press_single_key(system, timer_accum, key, label, trace_cfg, trace_print):
    timer_accum = _wait_for_scan_window(system, timer_accum)

    st0 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {}
    base_keycm = st0.get("keycm", 0x00)
    base_keyin16 = st0.get("keyin16", st0.get("keyin", 0x80))
    edtop_before = _edtop_bytes(system, 64)
    lcd_on_before = sum(1 for b in system.lcd.vram if b)
    idle_after_release_ok = False

    def _attempt_once(tag):
        nonlocal timer_accum, idle_after_release_ok
        print(f"[AUTO] press key: {label} ({tag})")
        system.press_key(key)
        kb0 = _keybuf_snapshot(system)["kycnt"]

        # Keep key pressed until key is actually enqueued to keyboard buffer.
        # CHATA=0x20 is debounce start, not a safe release point.
        hold_until = time.ticks_add(time.ticks_ms(), KEY_HOLD_MS)
        hold_max_until = time.ticks_add(time.ticks_ms(), KEY_HOLD_MAX_MS)
        forced_release = False
        released_on_buffer = False
        while True:
            timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)
            now = time.ticks_ms()
            if time.ticks_diff(now, hold_until) < 0:
                continue
            kb_now = _keybuf_snapshot(system)["kycnt"]
            if kb_now != kb0:
                released_on_buffer = True
                break
            if time.ticks_diff(now, hold_max_until) >= 0:
                forced_release = True
                break
        if forced_release:
            print(f"[AUTO] release guard timeout: {label} ({tag})")
        elif released_on_buffer:
            print(f"[AUTO] buffer enqueue observed: {label} ({tag})")

        ok = False
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < COMMIT_TIMEOUT_MS:
            st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {}
            keycm = st.get("keycm", base_keycm)
            keyin16 = st.get("keyin16", st.get("keyin", base_keyin16))
            if (keycm != base_keycm) or (keyin16 != base_keyin16):
                ok = True
                break
            timer_accum = _step_runtime(system, STEP_CHUNK, timer_accum)

        system.release_key(key)
        print(f"[AUTO] release key: {label} ({tag})")
        idle_ok, timer_accum = _wait_key_idle(system, timer_accum)
        idle_after_release_ok = idle_ok
        if not idle_ok:
            st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {}
            print(
                "[AUTO] idle wait timeout: "
                f"CHATA={st.get('chata', 0):02X} "
                f"KEYCM={st.get('keycm', 0):02X} "
                f"KEYIN16={st.get('keyin16', st.get('keyin', 0)):04X}"
            )
        return ok

    commit_ok = _attempt_once("scan")

    fallback_used = False
    if not commit_ok:
        # Fallback for diagnosis: disable scan-gated KEY_INT and retry once.
        # If this succeeds, issue is likely in scan-gate timing/conditions.
        fallback_used = True
        prev_scan_mode = bool(getattr(system, "key_interrupt_via_scan", True))
        system.key_interrupt_via_scan = False
        print("[AUTO] fallback retry: key_interrupt_via_scan=False")
        commit_ok = _attempt_once("legacy")
        system.key_interrupt_via_scan = prev_scan_mode
        print(f"[AUTO] fallback result: commit_ok={commit_ok}")

    timer_accum = _step_runtime(system, POST_INPUT_SETTLE_STEPS, timer_accum)

    # Important:
    # Run instruction trace after key commit/release path so trace stepping
    # does not interfere with KEY state progression (CHATA/KEYCM/KEYIN).
    if label == trace_cfg["trace_trigger_label"] and trace_cfg["trace_steps"] > 0:
        if commit_ok:
            timer_accum = _run_instruction_trace(system, timer_accum, trace_cfg, trace_print)
        else:
            if trace_cfg["trace_force_on_no_commit"]:
                trace_print("[TRACE] key commit was not observed; forcing trace for diagnosis")
                timer_accum = _run_instruction_trace(system, timer_accum, trace_cfg, trace_print)
            else:
                trace_print("[TRACE] skipped because key commit was not observed")
        if trace_cfg["trace_dump_on_finish"]:
            trace_print("[TRACE] dumping VRAM snapshot")
            system.dump_edtop_vram(printer=trace_print)
            system.dump_ledtp_vram(printer=trace_print)
            system.lcd.save_pbm("single_key_trace_finish.pbm")
            trace_print("[TRACE] saved PBM: single_key_trace_finish.pbm")

    st1 = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else {}
    kb1 = _keybuf_snapshot(system)
    edtop_after = _edtop_bytes(system, 64)
    lcd_on_after = sum(1 for b in system.lcd.vram if b)
    edtop_diff = _diff_bytes(edtop_before, edtop_after, 0x6100)

    trace_print("RESULT:")
    trace_print(f"  commit_ok={commit_ok}")
    trace_print(f"  fallback_used={fallback_used}")
    trace_print(f"  idle_after_release_ok={idle_after_release_ok}")
    trace_print(
        "  key state: "
        f"CHATA={st1.get('chata', 0):02X} "
        f"KEYCM={st1.get('keycm', 0):02X} "
        f"KEYIN16={st1.get('keyin16', st1.get('keyin', 0)):04X}"
    )
    trace_print(
        "  key buffer: "
        f"KYCNT={kb1['kycnt']:02X} RD={kb1['rd']:02X} WR={kb1['wr']:02X} "
        f"SIZE={kb1['size']:02X} ADDR={kb1['buf_addr']:04X}"
    )
    trace_print(
        "  key buffer bytes: "
        + " ".join(f"{b:02X}" for b in kb1["buf"])
    )
    trace_print(f"  EDTOP changed bytes={len(edtop_diff)}")
    for addr, oldv, newv in edtop_diff[:16]:
        trace_print(f"    {addr:04X}: {oldv:02X} -> {newv:02X}")
    if len(edtop_diff) > 16:
        trace_print(f"    ... ({len(edtop_diff) - 16} more)")
    trace_print(f"  LCD VRAM non-zero: before={lcd_on_before}, after={lcd_on_after}")

    system.update_display()
    system.dump_edtop_vram()
    system.lcd.save_pbm("single_key_display.pbm")
    trace_print("Saved PBM: single_key_display.pbm")
    return timer_accum


def main():
    print(f"\n=== PB-1000 single key display test ({SCRIPT_VERSION}) ===")

    trace_cfg = load_trace_config()
    print(
        "TRACE CONFIG: "
        f"loaded='{trace_cfg.get('loaded_path', '')}' "
        f"trigger='{trace_cfg['trace_trigger_label']}' "
        f"steps={trace_cfg['trace_steps']} "
        f"per_loop={trace_cfg['trace_steps_per_loop']} "
        f"output={trace_cfg['trace_output']} "
        f"path={trace_cfg['trace_output_path']} "
        f"force_on_no_commit={trace_cfg['trace_force_on_no_commit']} "
        f"service_input_every={trace_cfg['trace_service_input_every']}"
    )
    trace_print, trace_stream = _init_trace_output(trace_cfg)
    display = init_display()
    draw_bezel(display)
    system = PB1000System(
        display=display,
        debug={"sys": True, "lcd": False, "kb": True},
    )

    if not _load_roms(system):
        print("ERROR: ROM load failed")
        return

    enabled, timer_accum, boot_steps = _wait_key_enable(system)
    if enabled:
        print(f"Boot ready: KEY input enabled after {boot_steps} steps")
    else:
        print(f"WARN: KEY input not enabled within timeout ({boot_steps} steps)")

    timer_accum = _step_runtime(system, POST_BOOT_SETTLE_STEPS, timer_accum)
    key, label = KEY_TO_TEST
    timer_accum = _press_single_key(system, timer_accum, key, label, trace_cfg, trace_print)
    if trace_cfg["trace_exit_on_finish"] and label == trace_cfg["trace_trigger_label"]:
        print("[TRACE] Exit requested by trace_exit_on_finish")
        if trace_stream is not None:
            trace_stream.close()
        return
    if trace_stream is not None:
        trace_stream.close()
    print("Done.")


if __name__ == "__main__":
    main()
