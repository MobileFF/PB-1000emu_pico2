def wake_diag_snapshot(system):
    snap = {
        "pc": None,
        "flags": None,
        "ia": None,
        "ib": None,
        "ie": None,
        "ua": None,
        "sleep": None,
    }
    try:
        snap["pc"] = system.pc
    except Exception:
        pass
    try:
        if hasattr(system, "is_sleeping"):
            snap["sleep"] = 1 if system.is_sleeping else 0
    except Exception:
        pass
    try:
        import hd61700 as cpu_core
        if hasattr(cpu_core, "get_flags"):
            snap["flags"] = cpu_core.get_flags() & 0xFF
        if hasattr(cpu_core, "get_reg8"):
            snap["ib"] = cpu_core.get_reg8(2) & 0xFF
            snap["ua"] = cpu_core.get_reg8(3) & 0xFF
            snap["ia"] = cpu_core.get_reg8(4) & 0xFF
            snap["ie"] = cpu_core.get_reg8(5) & 0xFF
    except Exception:
        pass
    return snap


def format_wake_diag(snap):
    def hx(v, w):
        return "--" if v is None else f"{v:0{w}X}"
    return (
        f"PC={hx(snap['pc'],4)} F={hx(snap['flags'],2)} "
        f"IA={hx(snap['ia'],2)} IB={hx(snap['ib'],2)} IE={hx(snap['ie'],2)} "
        f"UA={hx(snap['ua'],2)} SLP={snap['sleep'] if snap['sleep'] is not None else '-'}"
    )


def wake_diag_changed(prev_snap, cur_snap):
    for key in ("pc", "flags", "ia", "ib", "ie", "ua", "sleep"):
        if prev_snap.get(key) != cur_snap.get(key):
            return True
    return False


def trace_wake_path(system, reason, *, steps, vector_pc):
    if steps <= 0:
        return
    try:
        start_pc = system.pc
    except Exception:
        start_pc = 0
    print(f"[WAKE_TRACE] start reason={reason} pc={start_pc:04X} steps={steps}")
    saw_vector = False
    for i in range(steps):
        snap_before = wake_diag_snapshot(system)
        pc_before = snap_before.get("pc")
        if pc_before == vector_pc:
            saw_vector = True
        try:
            if hasattr(system, "service_input_lines"):
                system.service_input_lines()
            trace_line = system.debug_step(pause=False, trace=True, prt=True, trace_index=i + 1)
            if isinstance(trace_line, str) and f"[{vector_pc:04X}]" in trace_line:
                saw_vector = True
        except Exception as e:
            print(f"[WAKE_TRACE] aborted at step {i + 1}: {e}")
            break
        snap_after = wake_diag_snapshot(system)
        if wake_diag_changed(snap_before, snap_after):
            print(
                f"[WAKE_TRACE_STATE] step={i + 1:02d} "
                f"pre=({format_wake_diag(snap_before)}) "
                f"post=({format_wake_diag(snap_after)})"
            )
    try:
        end_pc = system.pc
    except Exception:
        end_pc = 0
    if end_pc == vector_pc:
        saw_vector = True
    print(f"[WAKE_TRACE] end pc={end_pc:04X} saw_vector={1 if saw_vector else 0}")
