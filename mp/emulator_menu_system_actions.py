"""
EMULATOR MENU — System category action handlers (Reset, Reboot MCU, NEW ALL,
CPU Steps/Slice, Loop Idle). Hook Status / CPU Status live in
emulator_menu_debug.py, split out separately (they're diagnostic screens,
not simple state changes).

Split out of the former monolithic emulator_menu.py so that opening the
menu doesn't have to pay the compile cost of every category's handlers up
front -- each category file is lazily imported only when a specific item
in that category is actually selected. Lazily imported from
emulator_menu.py's _dispatch_system() only when a specific item is
actually selected.

_number_input() and the INI-persistence helper are duplicated from
emulator_menu_display_actions.py rather than imported from there -- they're
small and self-contained, and importing across category files would mean
opening System's step_count also compiles Display's fg/bg color code (and
vice versa), defeating the point of splitting by category in the first
place.
"""
import time

from emulator_menu import _draw_text, _confirm, _BG, _FG, _HDR, _FTR, _S_ON, _WARN
from hdmi_menu_mirror import hdmi_flush

_SC_DIGIT = {
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5',
    0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
}


def _number_input(display, title, current=0, min_v=0, max_v=255):
    """Integer input within [min_v, max_v]. Returns int or None on cancel.
    (Duplicated from emulator_menu_display_actions.py -- see this file's
    module docstring for why.)"""
    import hd61700
    W, H = display.width, display.height
    max_digits = len(str(max_v))
    buf = list(str(max(min_v, min(max_v, current))))
    range_hint = "%d-%d" % (min_v, max_v)

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4,  4, title, _HDR)
        _draw_text(display, 4, 16, "0-9  BS:del  EXE:ok  BRK:cancel", _FTR)
        _draw_text(display, 4, H // 2 - 4, "".join(buf) + "_", _FG)

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc in _SC_DIGIT and len(buf) < max_digits:
                buf.append(_SC_DIGIT[sc])
                _redraw()
            elif sc == 0x2A and buf:        # Backspace
                buf.pop()
                _redraw()
            elif sc == 0x28 and buf:        # EXE
                val = int("".join(buf))
                if min_v <= val <= max_v:
                    return val
                display.fill_rect(0, H // 2 - 6, W, 20, _BG)
                _draw_text(display, 4, H // 2 - 4, "!! %s only" % range_hint, _WARN)
            elif sc == 0x29:                # BRK
                return None
        hdmi_flush(display)
        time.sleep_ms(30)


def _update_ini(path, section, kv):
    """Update [section] keys in an INI file, preserving all other content.
    (Duplicated from emulator_menu_display_actions.py -- see this file's
    module docstring for why.)"""
    lines = []
    try:
        with open(path, "r") as f:
            for line in f:
                lines.append(line)
    except OSError:
        pass

    sec_header = "[" + section + "]"
    sec_start = -1
    sec_end = len(lines)
    for i in range(len(lines)):
        stripped = lines[i].strip()
        if stripped.lower() == sec_header.lower():
            sec_start = i
        elif sec_start >= 0 and i > sec_start and stripped.startswith("[") and stripped.endswith("]"):
            sec_end = i
            break

    new_block = [sec_header + "\n"]
    if sec_start >= 0:
        for line in lines[sec_start + 1:sec_end]:
            s = line.strip()
            if not s or s[0] in ("#", ";"):
                new_block.append(line)
                continue
            if "=" in s:
                k = s.split("=", 1)[0].strip().lower()
                if k not in kv:
                    new_block.append(line)
    for k, v in kv.items():
        new_block.append(k + " = " + str(v) + "\n")
    new_block.append("\n")

    if sec_start >= 0:
        out = lines[:sec_start] + new_block + lines[sec_end:]
    else:
        out = lines
        if out and out[-1].strip():
            out.append("\n")
        out += new_block

    with open(path, "w") as f:
        for line in out:
            f.write(line)


def _save_speed_settings(active_step_count, loop_idle_ms):
    """Write [emulator] active_step_count/loop_idle_ms to pb1000.ini.
    Returns save path or error string."""
    import os
    try:
        os.listdir("/sd")
        path = "/sd/pb1000.ini"
    except OSError:
        path = "/pb1000.ini"
    kv = {"active_step_count": str(active_step_count), "loop_idle_ms": str(loop_idle_ms)}
    try:
        _update_ini(path, "emulator", kv)
        return path
    except Exception as e:
        return "ERR:" + str(e)


def _do_reset(system):
    system.reset_emulator()
    return "Reset executed"


def _do_reboot_mcu(display):
    """Full hardware reboot of the Pico itself (machine.reset()) -- distinct
    from _do_reset() above, which only resets the emulated PB-1000 CPU core
    and leaves the Pico/MicroPython session running. This re-runs the whole
    boot sequence from scratch (profile picker, ROM/RAM load, etc.), so any
    progress not already written out via RAM Save is lost."""
    if not _confirm(display, "Reboot emulator (MCU)?",
                     "Unsaved progress will be lost"):
        return ""
    display.fill_rect(0, 0, display.width, display.height, _BG)
    _draw_text(display, 4, display.height // 2 - 4, "Rebooting...", _S_ON)
    hdmi_flush(display)
    time.sleep_ms(400)
    import machine
    machine.reset()
    return ""  # unreachable


# ROM entry point for the "new all" handler (references/rom1.src, labelled
# "; new all" right above &H8D38). Reached on real hardware via
# `94C0: jp z,&H8D38 ;jump if key code = &H9A, new all` inside the function-key
# dispatch routine (&H94A0-&H94DF) once CRTKY (&H9158) has decoded the
# Win+F12 matrix combo into key code &H9A.
_NEWALL_ENTRY = 0x8D38


def _do_newall(system, display):
    """Force PC straight to the ROM's own NEW ALL handler after confirmation,
    instead of simulating the Win+F12 key combo through the keyboard queue.

    `94C0: jp z,&H8D38` is a plain jump, not a call -- nothing about &H8D38's
    own code depends on register/flag state left behind by the dispatch
    routine that jumps there (it starts by unconditionally setting IE itself:
    `8D38: pst ie,&H10`). So landing PC there directly, with no other state
    changes, reproduces exactly what real hardware does at the moment it
    recognizes NEW ALL -- and sidesteps every way the old key-queue
    simulation could fail to actually land: KEY_INT timing, matrix-scan
    cycle alignment, or the main loop not resuming CPU stepping before the
    queued press/release had a chance to complete.
    """
    if not _confirm(display, "NEW ALL: erase all memory?", "This cannot be undone"):
        return ""
    system.pc = _NEWALL_ENTRY
    return "NEW ALL executed"


def _do_step_count(system, display):
    """Adjust CPU steps executed per main-loop slice (see main.py's
    run_cpu_slice(active_steps=system._active_step_count)) -- the main lever
    for emulation speed. Takes effect immediately since main.py reads this
    attribute fresh every loop iteration; also persisted to pb1000.ini
    (like fg/bg color and HDMI) so it survives a reboot."""
    cur = getattr(system, '_active_step_count', 12000)
    val = _number_input(display, "CPU Steps/Slice (1-65535)", cur, min_v=1, max_v=65535)
    if val is None:
        return ""
    system._active_step_count = val
    result = _save_speed_settings(val, getattr(system, '_loop_idle_ms', 0))
    if result.startswith("ERR:"):
        return "Steps/Slice:%d save %s" % (val, result)
    return "Steps/Slice:%d -> %s" % (val, result)


def _do_loop_idle(system, display):
    """Adjust the main loop's per-iteration idle sleep (see main.py's
    time.sleep_ms(system._loop_idle_ms)). Lower = faster/more CPU-hungry,
    higher = slower/more power-efficient. Same live+persist behavior as
    _do_step_count()."""
    cur = getattr(system, '_loop_idle_ms', 0)
    val = _number_input(display, "Loop Idle ms (0-1000)", cur, min_v=0, max_v=1000)
    if val is None:
        return ""
    system._loop_idle_ms = val
    result = _save_speed_settings(getattr(system, '_active_step_count', 12000), val)
    if result.startswith("ERR:"):
        return "Loop Idle:%dms save %s" % (val, result)
    return "Loop Idle:%dms -> %s" % (val, result)
