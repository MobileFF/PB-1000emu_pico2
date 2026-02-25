"""
PB-1000 Emulator - Debug Run Script (INI configurable)
"""
import machine
import sys
import time
import uselect
import builtins
try:
    import os
except ImportError:
    import uos as os
from ili9341 import ILI9341

SPI_ID = 1
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 9
DC_PIN = 8
RST_PIN = 7
BL_PIN = 22


def _to_bool(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def _to_int(text):
    return int(str(text).strip(), 0)


def _to_int_list(text):
    value = str(text).strip()
    if not value:
        return ()
    out = []
    for token in value.split(","):
        token = token.strip()
        if token:
            out.append(int(token, 0))
    return tuple(out)


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


def load_debug_config():
    cfg = {
        "enable_display_refresh": True,
        "frame_interval_ms": 1000,
        "step_service_chunk": 64,
        "step_block": 4000,
        "step_timer_tick_steps": 40000,
        "auto_exe_on_enter": False,
        "key_hold_ms": 120,
        "key_release_hard_timeout_ms": 1200,
        "inter_key_gap_ms": 80,
        "trace_every_steps": 0,
        "key_trace_pc": None,
        "key_trace_window_steps": 128,
        "key_trace_min_print_ms": 120,
        "auto_inject_key": "",
        "auto_inject_interval_ms": 2000,
        "trace_steps": 1000,
        "trace_steps_per_loop": 32,
        "trace_trigger_label": "",
        "trace_exit_on_finish": False,
        "trace_dump_on_finish": False,
        "trace_output": "console",
        "trace_output_path": "trace_output.log",
        "trace_output_rotate_per_run": True,
        "trace_reg_dump_pcs": (),
        "trace_loop_sample_pcs": (),
        "trace_loop_sample_max_per_pc": 12,
        "debug_sys": False,
        "debug_lcd": False,
        "debug_kb": False,
    }

    ini_paths = ("debug.ini", "/debug.ini", "/mp/debug.ini")
    ini_data = None
    for path in ini_paths:
        try:
            ini_data = _parse_ini(path)
            print(f"Debug config loaded: {path}")
            break
        except OSError:
            pass

    if not ini_data:
        print("Debug config not found. Using defaults.")
        return cfg

    run = ini_data.get("run", {})
    trace = ini_data.get("trace", {})
    debug = ini_data.get("debug", {})

    if "enable_display_refresh" in run:
        cfg["enable_display_refresh"] = _to_bool(run["enable_display_refresh"])
    if "frame_interval_ms" in run:
        cfg["frame_interval_ms"] = _to_int(run["frame_interval_ms"])
    if "step_service_chunk" in run:
        cfg["step_service_chunk"] = _to_int(run["step_service_chunk"])
    if "step_block" in run:
        cfg["step_block"] = _to_int(run["step_block"])
    if "step_timer_tick_steps" in run:
        cfg["step_timer_tick_steps"] = _to_int(run["step_timer_tick_steps"])
    if "auto_exe_on_enter" in run:
        cfg["auto_exe_on_enter"] = _to_bool(run["auto_exe_on_enter"])
    if "key_hold_ms" in run:
        cfg["key_hold_ms"] = _to_int(run["key_hold_ms"])
    if "key_release_hard_timeout_ms" in run:
        cfg["key_release_hard_timeout_ms"] = _to_int(run["key_release_hard_timeout_ms"])
    if "inter_key_gap_ms" in run:
        cfg["inter_key_gap_ms"] = _to_int(run["inter_key_gap_ms"])
    if "auto_inject_key" in run:
        cfg["auto_inject_key"] = run["auto_inject_key"]
    if "auto_inject_interval_ms" in run:
        cfg["auto_inject_interval_ms"] = _to_int(run["auto_inject_interval_ms"])

    if "trace_every_steps" in trace:
        cfg["trace_every_steps"] = _to_int(trace["trace_every_steps"])
    if "key_trace_pc" in trace:
        pc_text = trace["key_trace_pc"].strip()
        cfg["key_trace_pc"] = None if pc_text.lower() == "none" else int(pc_text, 0)
    if "key_trace_window_steps" in trace:
        cfg["key_trace_window_steps"] = _to_int(trace["key_trace_window_steps"])
    if "key_trace_min_print_ms" in trace:
        cfg["key_trace_min_print_ms"] = _to_int(trace["key_trace_min_print_ms"])
    if "trace_steps" in trace:
        cfg["trace_steps"] = _to_int(trace["trace_steps"])
    if "trace_steps_per_loop" in trace:
        cfg["trace_steps_per_loop"] = _to_int(trace["trace_steps_per_loop"])
    if "trace_trigger_label" in trace:
        cfg["trace_trigger_label"] = trace["trace_trigger_label"]
    elif "trace_triiger_label" in trace:
        # Backward-compatible typo alias.
        cfg["trace_trigger_label"] = trace["trace_triiger_label"]
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
    if "trace_reg_dump_pcs" in trace:
        cfg["trace_reg_dump_pcs"] = _to_int_list(trace["trace_reg_dump_pcs"])
    if "trace_loop_sample_pcs" in trace:
        cfg["trace_loop_sample_pcs"] = _to_int_list(trace["trace_loop_sample_pcs"])
    if "trace_loop_sample_max_per_pc" in trace:
        cfg["trace_loop_sample_max_per_pc"] = _to_int(trace["trace_loop_sample_max_per_pc"])

    if "sys" in debug:
        cfg["debug_sys"] = _to_bool(debug["sys"])
    if "lcd" in debug:
        cfg["debug_lcd"] = _to_bool(debug["lcd"])
    if "kb" in debug:
        cfg["debug_kb"] = _to_bool(debug["kb"])

    return cfg


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


def _keypos(row, ki_col):
    return (row, ki_col)


KEY_EXE = _keypos(10, 9)

_KEY_CANDIDATES = {
    "EXE": [KEY_EXE],
}

_spoll = uselect.poll()
_spoll.register(sys.stdin, uselect.POLLIN)


def _build_state(config):
    return {
        "key_queue": [],
        "active_key": None,
        "active_key_label": None,
        "active_key_candidates": None,
        "active_key_candidate_idx": 0,
        "active_key_started": False,
        "active_key_seen_scan_phase": False,
        "active_key_last_chata": -1,
        "active_key_last_chata_change_ms": 0,
        "active_key_abs_timeout_ms": 0,
        "release_at_ms": 0,
        "release_hard_at_ms": 0,
        "next_press_at_ms": 0,
        "typed_since_enter": False,
        "next_auto_inject_ms": time.ticks_add(time.ticks_ms(), config["auto_inject_interval_ms"]),
        "auto_inject_done": False,
        "last_key_trace_ms": 0,
        "trace_remaining": 0,
        "trace_step_index": 0,
        "trace_started_by_trigger": False,
        "exit_requested": False,
        "dump_requested": False,
        "loop_sample_counts": {},
        "trace_sys_debug_silenced": False,
    }


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


def _init_trace_output(config):
    mode = str(config.get("trace_output", "console")).strip().lower()
    path = str(config.get("trace_output_path", "trace_output.log")).strip() or "trace_output.log"
    rotate_per_run = bool(config.get("trace_output_rotate_per_run", True))

    if mode == "file":
        if rotate_per_run:
            path = _rotate_trace_output_path(path)
        try:
            stream = open(path, "w")

            def _trace_print(msg=""):
                stream.write(str(msg))
                stream.write("\n")
                stream.flush()

            print(f"[TRACE] output mode=file path={path}")
            return _trace_print, stream
        except OSError as e:
            print(f"[TRACE] output file open failed ({path}): {e}. Fallback to console.")

    def _trace_print_console(msg=""):
        print(msg)

    return _trace_print_console, None


def _install_stdout_redirect(stream):
    original_print = builtins.print

    def _redirected_print(*args, **kwargs):
        target = kwargs.get("file", None)
        if target is not None and target is not sys.stdout:
            original_print(*args, **kwargs)
            return

        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        flush = bool(kwargs.get("flush", False))
        stream.write(sep.join(str(arg) for arg in args))
        stream.write(end)
        if flush:
            stream.flush()

    builtins.print = _redirected_print
    return original_print


def _dump_vram_artifacts(system, reason, trace_print):
    trace_print(f"[TRACE] dumping VRAM ({reason})")
    system.dump_edtop_vram(printer=trace_print)
    system.dump_ledtp_vram(printer=trace_print)
    system.lcd.dump_vram()
    pbm_name = "lcd_dump_on_trace_finish.pbm"
    system.lcd.save_pbm(pbm_name)
    trace_print(f"[TRACE] saved PBM: {pbm_name}")


def _resolve_key_candidates(key, label):
    if label in _KEY_CANDIDATES:
        return _KEY_CANDIDATES[label]
    return [key]


def _map_input_char(char):
    if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char in " .+-*/=":
        return char, char
    if char == "!":
        return KEY_EXE, "EXE"
    if char == "@":
        return _keypos(5, 11), "MODE"
    if char == "[":
        return _keypos(6, 11), "LCKEY"
    if char == "]":
        return _keypos(4, 11), "CAL"
    if char == "`":
        return _keypos(7, 9), "&HFC"
    if char == "{":
        return _keypos(8, 9), "&HFD"
    if char == "^":
        return _keypos(1, 1), "BRK"
    return None, None


def _parse_auto_inject_sequence(text):
    raw = str(text).strip()
    if not raw:
        return []

    aliases = {
        "EXE": KEY_EXE,
        "MODE": _keypos(5, 11),
        "LCKEY": _keypos(6, 11),
        "CAL": _keypos(4, 11),
        "&HFC": _keypos(7, 9),
        "&HFD": _keypos(8, 9),
        "BRK": _keypos(1, 1),
    }

    out = []
    for token in raw.split("|"):
        token = token.strip()
        if not token:
            continue

        key = None
        label = token
        token_up = token.upper()
        if token_up in aliases:
            key = aliases[token_up]
            label = token_up
        elif len(token) == 1:
            key, label = _map_input_char(token)

        if key is None:
            return []
        out.append((key, label))
    return out


def _get_key_state_line(system):
    st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
    irq = system.get_irq_scan_state() if hasattr(system, "get_irq_scan_state") else None
    if not st:
        key_part = "KYSTA=-- CHATA=-- KEYIN=--"
    else:
        key_part = f"KYSTA={st['kysta']:02X} CHATA={st['chata']:02X} KEYIN={st['keyin']:02X}"
    if not irq:
        return key_part
    return f"{key_part} IA={irq['ia']:02X} IB={irq['ib']:02X} IE={irq['ie']:02X}"


def _get_trace_key_regs_line(system):
    st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
    if not st:
        return "CHATA=-- KEYCM=-- KEYINL=-- KEYINH=--"
    return (
        f"CHATA={st['chata']:02X} "
        f"KEYCM={st['keycm']:02X} "
        f"KEYINL={st['keyin']:02X} "
        f"KEYINH={st['keyinh']:02X}"
    )


def _format_trace_reg_diff(before, after):
    if not before or not after:
        return "reg-diff unavailable"

    parts = []

    rb = before.get("r", [])
    ra = after.get("r", [])
    n = len(rb) if len(rb) < len(ra) else len(ra)
    for i in range(n):
        if rb[i] != ra[i]:
            parts.append(f"${i:02X}:{rb[i]:02X}->{ra[i]:02X}")

    for name in ("ia", "ib", "ie", "sx", "sy", "sz"):
        vb = before.get(name, 0)
        va = after.get(name, 0)
        if vb != va:
            parts.append(f"{name.upper()}:{vb:02X}->{va:02X}")

    for name in ("ix", "iy", "iz", "us", "ss", "ky"):
        vb = before.get(name, 0)
        va = after.get(name, 0)
        if vb != va:
            parts.append(f"{name.upper()}:{vb:04X}->{va:04X}")

    vb = before.get("flags", 0)
    va = after.get("flags", 0)
    if vb != va:
        def _flags_to_str(flags):
            out = ""
            out += "Z" if flags & 0x80 else "-"
            out += "C" if flags & 0x40 else "-"
            out += "L" if flags & 0x20 else "-"
            out += "U" if flags & 0x10 else "-"
            out += "S" if flags & 0x08 else "-"
            out += "A" if flags & 0x04 else "-"
            return out

        parts.append(
            f"FLAGS:{vb:02X}->{va:02X} F:{_flags_to_str(vb)}->F:{_flags_to_str(va)}"
        )

    if not parts:
        return "(no register changes)"
    return " ".join(parts)


def _format_trace_key_diff(before, after):
    if not before or not after:
        return "(no key-state changes)"

    fields = (
        ("chata", "CHATA"),
        ("keycm", "KEYCM"),
        ("keyin", "KEYINL"),
        ("keyinh", "KEYINH"),
    )
    parts = []
    for key, label in fields:
        vb = before.get(key, 0)
        va = after.get(key, 0)
        if vb != va:
            parts.append(f"{label}:{vb:02X}->{va:02X}")
    if not parts:
        return "(no key-state changes)"
    return " ".join(parts)


def _step_with_input_service(system, steps, chunk):
    ran = 0
    while ran < steps:
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        n = chunk
        remain = steps - ran
        if n > remain:
            n = remain
        system.step(n)
        ran += n
    return ran


def _step_with_pc_watch(system, steps, target_pc, min_print_ms, state, trace_print):
    executed = 0
    while executed < steps:
        if hasattr(system, "service_input_lines") and (executed & 0x1F) == 0:
            system.service_input_lines()
        if system.pc == target_pc:
            now = time.ticks_ms()
            if time.ticks_diff(now, state["last_key_trace_ms"]) >= min_print_ms:
                trace_print(f"[PC={target_pc:04X}] {_get_key_state_line(system)}")
                state["last_key_trace_ms"] = now
        system.step(1)
        executed += 1
    return executed


def _run_trace(system, config, state, trace_print):
    if state["trace_remaining"] <= 0:
        return 0

    traced = config["trace_steps_per_loop"]
    if traced > state["trace_remaining"]:
        traced = state["trace_remaining"]

    for _ in range(traced):
        if hasattr(system, "service_input_lines"):
            system.service_input_lines()
        pc = system.pc
        dump_this_pc = pc in config["trace_reg_dump_pcs"]
        reg_before = None
        key_before = None

        if dump_this_pc:
            trace_print(f"[TRACE] PC={pc:04X}: register dump (before)")
            system.print_registers(printer=trace_print)
            trace_print(f"[TRACE] PC={pc:04X}: {_get_trace_key_regs_line(system)}")
            if hasattr(system, "get_register_snapshot"):
                reg_before = system.get_register_snapshot()
            key_before = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None

        if pc in config["trace_loop_sample_pcs"]:
            n = state["loop_sample_counts"].get(pc, 0)
            if n < config["trace_loop_sample_max_per_pc"]:
                state["loop_sample_counts"][pc] = n + 1
                trace_print(f"[TRACE] PC={pc:04X}: loop sample {state['loop_sample_counts'][pc]}/{config['trace_loop_sample_max_per_pc']}")
                system.print_registers(printer=trace_print)

        state["trace_step_index"] += 1
        mnemonic = system.debug_step(
            pause=False,
            trace=True,
            trace_index=state["trace_step_index"],
            out=trace_print,
        )

        if dump_this_pc:
            pc_after = system.pc
            reg_after = system.get_register_snapshot() if hasattr(system, "get_register_snapshot") else None
            key_after = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
            trace_print(
                f"[TRACE] PC={pc:04X}: register changes (after, next_pc={pc_after:04X}, op={mnemonic})"
            )
            trace_print(f"[TRACE] PC={pc:04X}: {_format_trace_reg_diff(reg_before, reg_after)}")
            trace_print(f"[TRACE] PC={pc:04X}: {_format_trace_key_diff(key_before, key_after)}")

        if mnemonic and mnemonic.startswith("PPO"):
            trace_print(f"[TRACE] PC={pc:04X}: PPO executed")
            system.print_registers(printer=trace_print)

    state["trace_remaining"] -= traced
    if state["trace_remaining"] == 0:
        trace_print("[TRACE] Trace finished")
        triggered = state["trace_started_by_trigger"]
        if config["trace_dump_on_finish"] and triggered:
            state["dump_requested"] = True
        if config["trace_exit_on_finish"] and triggered:
            trace_print("[TRACE] Exit requested by trace_exit_on_finish")
            state["exit_requested"] = True
        state["trace_started_by_trigger"] = False
    return traced


def _service_auto_inject(system, config, state, now):
    if state["auto_inject_done"]:
        return

    key_text = config["auto_inject_key"]
    if not key_text:
        state["auto_inject_done"] = True
        return
    if state["active_key"] is not None or state["key_queue"]:
        return
    if time.ticks_diff(now, state["next_auto_inject_ms"]) < 0:
        return

    sequence = _parse_auto_inject_sequence(key_text)
    if not sequence:
        print(f"[AUTO] invalid auto_inject_key: {key_text}")
        state["auto_inject_done"] = True
        return

    state["key_queue"].extend(sequence)
    state["auto_inject_done"] = True
    labels = "|".join(label for _, label in sequence)
    print(f"[AUTO] queued: {labels}")


def poll_keyboard(system, config, state, trace_print):
    while _spoll.poll(0):
        char = sys.stdin.read(1)
        if not char:
            break
        if char == "\r" or char == "\n":
            if config["auto_exe_on_enter"] and state["typed_since_enter"]:
                state["key_queue"].append((KEY_EXE, "EXE"))
                state["typed_since_enter"] = False
            continue

        key, label = _map_input_char(char)
        if key is not None:
            state["key_queue"].append((key, label))
            if key != KEY_EXE:
                state["typed_since_enter"] = True

    now = time.ticks_ms()

    if state["active_key"] is not None:
        st = system.get_key_scan_state() if hasattr(system, "get_key_scan_state") else None
        chata = st["chata"] if st else 0x00
        keyin = st["keyin"] if st else 0x80
        release_reason = ""

        if chata != state["active_key_last_chata"]:
            state["active_key_last_chata"] = chata
            state["active_key_last_chata_change_ms"] = now

        if chata != 0x07:
            state["active_key_started"] = True
        if state["active_key_started"] and chata != 0x20:
            state["active_key_seen_scan_phase"] = True

        should_release = False
        if state["active_key_started"]:
            scan_ready = system.can_release_active_key() if hasattr(system, "can_release_active_key") else (chata == 0x20)
            should_release = scan_ready and state["active_key_seen_scan_phase"]
            if should_release:
                release_reason = "scan_ready"

        scan_gated = bool(getattr(system, "key_interrupt_via_scan", False))
        if scan_gated and state["active_key_started"] and (not should_release):
            if chata == 0x07 and keyin != 0x80:
                should_release = True
                release_reason = "scan_gated_chata07"

        timed_out = time.ticks_diff(now, state["release_at_ms"]) >= 0
        if scan_gated and not should_release:
            chata_active = 0x01 <= chata <= 0x06
            stalled = (not chata_active) and (
                time.ticks_diff(now, state["active_key_last_chata_change_ms"])
                >= config["key_release_hard_timeout_ms"]
            )
            abs_timed_out = (not chata_active) and (
                time.ticks_diff(now, state["active_key_abs_timeout_ms"]) >= 0
            )
            timed_out = stalled or abs_timed_out

        if timed_out and not should_release:
            candidates = state["active_key_candidates"]
            if candidates is not None:
                next_idx = state["active_key_candidate_idx"] + 1
                if next_idx < len(candidates):
                    system.release_key(state["active_key"])
                    state["active_key_candidate_idx"] = next_idx
                    state["active_key"] = candidates[next_idx]
                    print(f"Key Retry: {state['active_key_label']} -> {state['active_key']}")
                    system.press_key(state["active_key"])
                    state["active_key_started"] = False
                    state["active_key_seen_scan_phase"] = False
                    state["active_key_last_chata"] = -1
                    state["active_key_last_chata_change_ms"] = now
                    state["active_key_abs_timeout_ms"] = time.ticks_add(now, max(config["key_release_hard_timeout_ms"] * 3, 3000))
                    state["release_at_ms"] = time.ticks_add(now, config["key_hold_ms"])
                    state["release_hard_at_ms"] = time.ticks_add(now, config["key_release_hard_timeout_ms"])
                    return
            should_release = True
            release_reason = "timeout"

        if should_release:
            if config.get("debug_kb", False):
                elapsed_ms = time.ticks_diff(now, state["release_at_ms"])
                hard_elapsed_ms = time.ticks_diff(now, state["release_hard_at_ms"])
                print(
                    f"KB RELEASE REASON: {release_reason or 'unknown'} "
                    f"label={state['active_key_label']} CHATA={chata:02X} KEYIN={keyin:02X} "
                    f"elapsed={elapsed_ms} hard_elapsed={hard_elapsed_ms}"
                )
            system.release_key(state["active_key"])
            state["active_key"] = None
            state["active_key_label"] = None
            state["active_key_candidates"] = None
            state["active_key_candidate_idx"] = 0
            state["active_key_started"] = False
            state["active_key_seen_scan_phase"] = False
            state["active_key_last_chata"] = -1
            state["active_key_last_chata_change_ms"] = 0
            state["active_key_abs_timeout_ms"] = 0
            state["next_press_at_ms"] = time.ticks_add(now, config["inter_key_gap_ms"])

    if state["active_key"] is None and state["key_queue"]:
        if time.ticks_diff(now, state["next_press_at_ms"]) < 0:
            return
        if hasattr(system, "is_key_input_enabled") and not system.is_key_input_enabled():
            return

        key, label = state["key_queue"].pop(0)
        candidates = _resolve_key_candidates(key, label)
        key = candidates[0]

        print(f"Key Press: {label}")
        system.press_key(key)

        state["active_key"] = key
        state["active_key_label"] = label
        state["active_key_candidates"] = candidates
        state["active_key_candidate_idx"] = 0
        state["active_key_started"] = False
        state["active_key_seen_scan_phase"] = False
        state["active_key_last_chata"] = -1
        state["active_key_last_chata_change_ms"] = now
        state["active_key_abs_timeout_ms"] = time.ticks_add(now, max(config["key_release_hard_timeout_ms"] * 3, 3000))
        state["release_at_ms"] = time.ticks_add(now, config["key_hold_ms"])
        state["release_hard_at_ms"] = time.ticks_add(now, config["key_release_hard_timeout_ms"])

        if label == config["trace_trigger_label"] and config["trace_steps"] > 0:
            state["trace_remaining"] = config["trace_steps"]
            state["trace_step_index"] = 0
            state["trace_started_by_trigger"] = True
            state["exit_requested"] = False
            state["dump_requested"] = False
            state["loop_sample_counts"] = {}
            if hasattr(system, "arm_display_write_probe"):
                system.arm_display_write_probe(config["trace_trigger_label"])
                trace_print(f"[PROBE] armed: first LCD VRAM write after {config['trace_trigger_label']}")
            trace_print(f"[TRACE] {config['trace_trigger_label']} pressed: tracing next {state['trace_remaining']} instructions")


def main():
    config = load_debug_config()
    trace_print, trace_stream = _init_trace_output(config)
    trace_to_file = str(config.get("trace_output", "console")).strip().lower() == "file"
    original_print = None

    if trace_to_file and trace_stream is not None:
        original_print = _install_stdout_redirect(trace_stream)
        print("[TRACE] stdout redirected to trace file")

    from pb1000 import PB1000System

    print("PB-1000 Emulator Debug Starting...")
    display = init_display()
    try:
        draw_bezel(display)
    except Exception:
        pass

    system = PB1000System(
        display,
        debug={
            "sys": config["debug_sys"],
            "lcd": config["debug_lcd"],
            "kb": config["debug_kb"],
        },
        restore_registers=False,
    )
    #system.lcd.setup_display(spi_id=1, cs_pin=9, dc_pin=8, scale=1, x_offset=16, y_offset=40)

    try:
        system.load_rom('/roms/rom0.bin', slot=0)
        system.load_rom('/roms/rom1.bin', slot=1)
    except Exception as e:
        print(f"ROM load warning: {e}")

    system.power_on()

    print(f"System initialized. PC={system.pc:#06x}")
    state = _build_state(config)
    tick_step_accum = 0
    frame_time = time.ticks_ms()
    step_count = 0

    try:
        while True:
            if hasattr(system, "service_input_lines"):
                system.service_input_lines()

            if not system.is_sleeping:
                if state["trace_remaining"] > 0:
                    if trace_to_file and config.get("debug_sys", False) and (not state["trace_sys_debug_silenced"]):
                        if hasattr(system, "set_sys_debug_output_enabled"):
                            system.set_sys_debug_output_enabled(False)
                            state["trace_sys_debug_silenced"] = True
                    ran = _run_trace(system, config, state, trace_print)
                    step_count += ran
                    tick_step_accum += ran
                    if state["dump_requested"]:
                        _dump_vram_artifacts(system, "trace_finish", trace_print)
                        state["dump_requested"] = False
                    if state["exit_requested"]:
                        break
                    if state["trace_remaining"] <= 0 and state["trace_sys_debug_silenced"]:
                        if hasattr(system, "set_sys_debug_output_enabled"):
                            system.set_sys_debug_output_enabled(True)
                        state["trace_sys_debug_silenced"] = False
                    now = time.ticks_ms()
                    poll_keyboard(system, config, state, trace_print)
                    _service_auto_inject(system, config, state, now)
                    continue

                if config["key_trace_pc"] is not None:
                    bulk = config["step_block"] - config["key_trace_window_steps"]
                    if bulk > 0:
                        ran = _step_with_input_service(system, bulk, config["step_service_chunk"])
                    else:
                        ran = 0
                    ran += _step_with_pc_watch(
                        system,
                        config["key_trace_window_steps"],
                        config["key_trace_pc"],
                        config["key_trace_min_print_ms"],
                        state,
                        trace_print,
                    )
                else:
                    ran = _step_with_input_service(system, config["step_block"], config["step_service_chunk"])

                step_count += ran
                tick_step_accum += ran

                if config["trace_every_steps"] > 0 and (step_count % config["trace_every_steps"]) == 0:
                    trace_print(f"[{step_count:8d}] PC={system.pc:04X} {_get_key_state_line(system)}")
            else:
                time.sleep_ms(10)

            now = time.ticks_ms()
            poll_keyboard(system, config, state, trace_print)
            _service_auto_inject(system, config, state, now)

            if config["enable_display_refresh"] and time.ticks_diff(now, frame_time) >= config["frame_interval_ms"]:
                system.update_display(x_offset=16, y_offset=40)
                frame_time = now

            if config["step_timer_tick_steps"] > 0:
                while tick_step_accum >= config["step_timer_tick_steps"]:
                    system.tick_timer()
                    tick_step_accum -= config["step_timer_tick_steps"]

            time.sleep_ms(1)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
        print("dump vrams")
        system.dump_edtop_vram()
        system.dump_ledtp_vram()
        system.lcd.dump_vram()
        print("save lcd.vram to pbm")
        system.lcd.save_pbm("lcd_dump_on_exit.pbm")
    finally:
        if original_print is not None:
            builtins.print = original_print
        if state.get("trace_sys_debug_silenced", False):
            if hasattr(system, "set_sys_debug_output_enabled"):
                system.set_sys_debug_output_enabled(True)
        if trace_stream is not None:
            trace_stream.close()


if __name__ == '__main__':
    main()
