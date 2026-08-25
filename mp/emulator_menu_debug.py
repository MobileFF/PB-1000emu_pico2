"""
PB-1000 Emulator runtime menu — Hook Status / CPU Status diagnostics.

Split out of the former monolithic emulator_menu_ext.py (itself split
from emulator_menu.py) so that opening either diagnostic screen doesn't
have to pay the compile cost of the unrelated RAM Save/Load
(emulator_menu_ram.py) / VRAM Save / Full Capture (emulator_menu_capture.py)
handlers too. Lazily imported from
emulator_menu.py's _dispatch_system() only when 'hook_status'/'cpu_status'
is actually selected.
"""

import time

from emulator_menu import (
    _draw_text,
    _BG, _FG, _HDR, _FTR, _WARN, _S_ON, _S_OFF,
    _HDR_H, _FTR_H, _ROW_H,
)
from hdmi_menu_mirror import hdmi_flush


def _do_hook_status(system, display):
    """Read-only diagnostic screen listing every registered call_hook and
    mem_write_hook: address, owning module ("owner" passed to
    register_call_hook()/register_mem_write_hook()), and current enabled state.
    Scroll with UP/DOWN, BRK to close."""
    import hd61700

    call_hooks = system.list_call_hooks() if hasattr(system, 'list_call_hooks') else []
    mem_hooks = system.list_mem_write_hooks() if hasattr(system, 'list_mem_write_hooks') else []

    lines = []  # list of (text, color)
    lines.append(("-- Call Hooks --", _HDR))
    if call_hooks:
        for addr, owner, en in call_hooks:
            badge = "ON " if en else "OFF"
            color = _S_ON if en else _S_OFF
            lines.append(("&H%04X  %-13s %s" % (addr, owner[:13], badge), color))
    else:
        lines.append(("(none)", _FTR))

    lines.append(("", _FG))
    lines.append(("-- Mem Write Hooks --", _HDR))
    if mem_hooks:
        for addr, addr_end, owner, en in mem_hooks:
            badge = "ON " if en else "OFF"
            color = _S_ON if en else _S_OFF
            rng = ("&H%04X-%04X" % (addr, addr_end)) if addr_end != addr else ("&H%04X" % addr)
            lines.append(("%-11s %-13s %s" % (rng, owner[:13], badge), color))
    else:
        lines.append(("(none)", _FTR))

    W, H = display.width, display.height
    max_vis = max(1, (H - _HDR_H - _FTR_H) // _ROW_H)
    scroll = 0

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, "==  HOOK STATUS  ==", _HDR)
        _draw_text(display, 4, 14, "UP/DOWN:scroll  BRK:close", _FTR)
        y = _HDR_H
        vis_end = min(len(lines), scroll + max_vis)
        for i in range(scroll, vis_end):
            text, color = lines[i]
            if text:
                _draw_text(display, 4, y, text, color)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x29:  # BRK — close
                break
            elif sc == 0x52 and scroll > 0:               # UP
                scroll -= 1
                _redraw()
            elif sc == 0x51 and scroll + max_vis < len(lines):  # DOWN
                scroll += 1
                _redraw()
        hdmi_flush(display)


def _do_cpu_status(system, display):
    """Read-only diagnostic screen: PC/flags/UA/IA/IB/IE/irq_status/state,
    all 32 main registers, IX/IY/IZ/US/SS/KY, SX/SY/SZ, and a raw hex dump
    around PC.

    2026-08-11: added specifically so CPU state can be inspected via the menu
    when the game itself is hung (frozen display, no key input) — the menu
    is still reachable in that case since CPU stepping is paused while it's
    open (see module docstring in emulator_menu.py), and mpremote/REPL
    access can be unavailable if the device is deep in a tight C-level loop
    (Ctrl-C doesn't reliably interrupt hd61700.execute()). Also prints
    everything to the REPL (prefixed [CPU_STATUS]) in case a log capture is
    running, since screen space is limited even with scrolling. Scroll with
    UP/DOWN, BRK to close.

    2026-08-13: originally also included a disassembly section (debug.py's
    decode_basic()), dropped because importing debug.py on a long-running,
    fragmented heap could itself raise a MemoryError — the raw hex dump below
    covers the same "what's actually at PC" need without that import."""
    import hd61700
    c = system.cpu

    pc = c.get_pc()
    flags = c.get_flags()
    f_str = "".join((
        "Z" if flags & 0x80 else "-",
        "C" if flags & 0x40 else "-",
        "L" if flags & 0x20 else "-",
        "U" if flags & 0x10 else "-",
        "S" if flags & 0x08 else "-",
        "A" if flags & 0x04 else "-",
    ))
    ua = c.get_reg8(3)
    ia = c.get_reg8(4)
    ib = c.get_reg8(2)
    ie = c.get_reg8(5)
    have_irq_api = hasattr(c, 'get_irq_status') and hasattr(c, 'get_state')
    irq_status = c.get_irq_status() if have_irq_api else 0
    cpu_flow_state = c.get_state() if have_irq_api else 0

    lines = []  # list of (text, color)
    lines.append(("-- CPU Status --", _HDR))
    lines.append(("PC=&H%04X  UA=&H%02X" % (pc, ua), _FG))
    lines.append(("FLAGS=%s (&H%02X)" % (f_str, flags), _FG))
    lines.append(("IA=&H%02X IB=&H%02X IE=&H%02X" % (ia, ib, ie), _FG))
    if have_irq_api:
        lines.append(("IRQ_STATUS=&H%02X STATE=&H%02X" % (irq_status, cpu_flow_state), _FG))
    else:
        lines.append(("IRQ_STATUS=?? (old firmware)", _WARN))

    reg16 = [c.get_reg16(i) for i in range(6)]
    names16 = ("IX", "IY", "IZ", "US", "SS", "KY")
    lines.append(("", _FG))
    lines.append(("-- 16-bit Regs --", _HDR))
    # 3-per-line (IX IY IZ / US SS KY / SX SY SZ) so the whole screen
    # (16-bit regs + main regs) fits without scrolling — requested 2026-08-11.
    lines.append(("%s=%04X %s=%04X %s=%04X" % (
        names16[0], reg16[0], names16[1], reg16[1], names16[2], reg16[2]), _FG))
    lines.append(("%s=%04X %s=%04X %s=%04X" % (
        names16[3], reg16[3], names16[4], reg16[4], names16[5], reg16[5]), _FG))

    if hasattr(c, 'get_sreg'):
        sx, sy, sz = c.get_sreg(0), c.get_sreg(1), c.get_sreg(2)
        lines.append(("SX=%02X SY=%02X SZ=%02X" % (sx, sy, sz), _FG))

    lines.append(("", _FG))
    lines.append(("-- Main Regs $0-$31 --", _HDR))
    regs = [c.get_reg(i) for i in range(32)]
    for i in range(0, 32, 4):
        lines.append(("$%-2d=%02X $%-2d=%02X $%-2d=%02X $%-2d=%02X" % (
            i, regs[i], i + 1, regs[i + 1], i + 2, regs[i + 2], i + 3, regs[i + 3]), _FG))

    # Plain hex dump around PC — the ground-truth raw bytes actually in RAM
    # right now, for manually cross-checking against static .pbf analysis
    # (e.g. confirming a resumed PC really is sitting on the instruction
    # boundary static analysis predicted, or catching self-modified-code
    # divergence from the static image). Requested 2026-08-11 during the
    # RAM-Load-resume hang investigation.
    lines.append(("", _FG))
    lines.append(("-- Raw bytes @ PC-4..PC+15 --", _HDR))
    try:
        bank0 = ua & 0x03
        base_addr = (pc - 4) & 0xFFFF
        # Internal ROM (addr < 0x0C00) is fetched word-aligned — the CPU's
        # own fetch_addr is (addr<<1) in that range (see hd61700.c
        # read_internal_rom_byte()/hd61700.h fetch_addr comment) — and
        # hd61700.read_mem()'s addressing matches that same byte-offset
        # convention (c_mem_direct_read() in modhd61700.c indexes
        # rom0_buf[offset] directly for offset<0x2000). Above that boundary
        # addr and the byte offset are the same value, so this only matters
        # for low-ROM PCs.
        if base_addr < 0x0C00:
            byte_base = (base_addr << 1) & 0xFFFF
        else:
            byte_base = base_addr
        raw_bytes = [c.read_mem((byte_base + k) & 0xFFFF, bank0) for k in range(20)]
        for row in range(0, 20, 8):
            addr = (base_addr + row) & 0xFFFF
            chunk = raw_bytes[row:row + 8]
            marker = " <-PC" if addr <= pc < addr + 8 else ""
            lines.append(("&H%04X: %s%s" % (
                addr, " ".join("%02X" % b for b in chunk), marker), _FG))
    except Exception as e:
        lines.append(("raw dump failed: %s" % e, _WARN))

    print("[CPU_STATUS] ---- dump start ----")
    for text, _color in lines:
        print("[CPU_STATUS]", text)
    print("[CPU_STATUS] ---- dump end ----")

    W, H = display.width, display.height
    max_vis = max(1, (H - _HDR_H - _FTR_H) // _ROW_H)
    scroll = 0

    def _redraw():
        display.fill_rect(0, 0, W, H, _BG)
        _draw_text(display, 4, 4, "==  CPU STATUS  ==", _HDR)
        _draw_text(display, 4, 14, "UP/DOWN:scroll  BRK:close", _FTR)
        y = _HDR_H
        vis_end = min(len(lines), scroll + max_vis)
        for i in range(scroll, vis_end):
            text, color = lines[i]
            if text:
                _draw_text(display, 4, y, text, color)
            y += _ROW_H

    _redraw()
    prev_sc = -1
    while True:
        sc = hd61700.get_last_key()
        if sc != prev_sc:
            prev_sc = sc
            if sc == 0x29:  # BRK — close
                break
            elif sc == 0x52 and scroll > 0:               # UP
                scroll -= 1
                _redraw()
            elif sc == 0x51 and scroll + max_vis < len(lines):  # DOWN
                scroll += 1
                _redraw()
        hdmi_flush(display)
        time.sleep_ms(30)
