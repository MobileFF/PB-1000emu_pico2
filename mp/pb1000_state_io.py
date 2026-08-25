"""
PB1000System's save_state()/load_state() and CALL/mem-write hook registry,
split out of pb1000.py as PB1000StateIOMixin -- mixed into PB1000System via
multiple inheritance. See pb1000_fdd.py's module docstring for why this
class got split at all (2026-08-22 real-hardware MemoryError compiling
pb1000.py as one ~1943-line unit).

load_state() alone is ~230 lines (RAM/bank/color-VRAM/register restore with
several fallback paths), so together with save_state() and the hook
registry this is the second-largest removable chunk of the class after the
virtual-FDD block (pb1000_fdd.py).

Uses `hasattr(x, '_view')` instead of `isinstance(x, RAMView)` (the
original in-class version's check) to decide whether a RAM buffer is a
RAMView wrapping a C-side buffer -- avoids importing RAMView from pb1000.py
into this file (which pb1000.py itself imports, i.e. a circular import)
for what was already duck-typing in spirit (RAMView is the only thing in
this codebase exposing `._view`).
"""
import sys
import hd61700 as cpu_core

try:
    import lcd_c
except ImportError:
    lcd_c = None


class PB1000StateIOMixin:
    def save_state(self, path=None):
        import json
        import gc
        gc.collect()

        if path is None:
            path0 = self._get_storage_path("ram0.bin")
            reg_path = self._get_storage_path("regs.json")
        else:
            path0 = f"{path}/ram0.bin"
            reg_path = f"{path}/regs.json"

        # Ensure the target directory exists (handles profile subdirs like /sd/rams/work/)
        dir_path = path0.rsplit("/", 1)[0]
        if dir_path.startswith("/sd"):
            self._ensure_dir(dir_path)

        try:
            with open(path0, "wb") as f:
                buf = self.ram._view if hasattr(self.ram, '_view') else self.ram
                f.write(buf)
            print(f"RAM0 saved: {path0} ({len(buf)} bytes)")
        except Exception as e:
            print(f"Error saving RAM0: {e}")
            sys.print_exception(e)

        for slot in range(1, 4):
            if not self.has_bank[slot]:
                continue
            rp = (self._get_storage_path(f"ram{slot}.bin") if path is None else f"{path}/ram{slot}.bin")
            try:
                sbuf = self._bank_ram[slot]
                data = sbuf._view if hasattr(sbuf, '_view') else sbuf
                if len(data) == 0:
                    print(f"RAM{slot} skipped: buffer empty")
                    continue
                with open(rp, "wb") as f:
                    f.write(data)
                print(f"RAM{slot} saved: {rp} ({len(data)} bytes)")
            except Exception as e:
                print(f"Error saving RAM{slot}: {e}")
                sys.print_exception(e)

        # Color VRAM (VDP extension, src/lcd_controller.c's lcd_state.color_vram)
        # is, like mono vram, a separate C buffer never touched by ram0.bin/
        # regs.json -- see refresh_lcd_from_ledtp()'s docstring for the mono-
        # vram equivalent of this gap. Unlike mono vram there's no ROM routine
        # that rebuilds it from something already in RAM (it's stamped
        # incrementally by write_vram_pixel_byte() as the program draws, or
        # bulk-loaded via the bank-RAM DMA registers / vram_loader.py's SD/FDD
        # loader), so it has to be captured as its own file. Only written when
        # VDP is actually active, to avoid a 12KB file for the common case of
        # a program that never uses it.
        try:
            if self.lcd.vdp_enabled and lcd_c is not None:
                cv_path = self._get_storage_path("color_vram.bin") if path is None else f"{path}/color_vram.bin"
                cvram = lcd_c.get_color_vram()
                with open(cv_path, "wb") as f:
                    f.write(cvram)
                print(f"Color VRAM saved: {cv_path} ({len(cvram)} bytes)")
        except Exception as e:
            print(f"Error saving Color VRAM: {e}")
            sys.print_exception(e)

        try:
            regs = {
                "pc": int(cpu_core.get_pc()),
                "flags": int(cpu_core.get_flags()),
                "ia": int(cpu_core.get_reg8(4)),
                "ib": int(cpu_core.get_reg8(2)),
                "ie": int(cpu_core.get_reg8(5)),
                "ua": int(cpu_core.get_reg8(3)),
                "regmain": [int(cpu_core.get_reg(i)) for i in range(32)],
                "regsir": [int(cpu_core.get_sreg(i)) for i in range(3)],
                "reg16": [int(cpu_core.get_reg16(i)) for i in range(6)],
                # irq_status/state: which interrupt handler (if any) is active,
                # and CPU_SLP/CPU_FAST — see get_irq_status()/get_state() in
                # modhd61700.c. A save taken mid-interrupt-handler (interrupts
                # fire on their own schedule regardless of what the loaded
                # program does, e.g. the periodic keyboard-scan interrupt) has
                # PC/UA pointing into ROM handler code that only makes sense
                # with these restored too, or resume can crash via a stale/
                # inconsistent fetch-bank state. Guarded with hasattr() so old
                # firmware without these C exports still round-trips the rest.
                "irq_status": int(cpu_core.get_irq_status()) if hasattr(cpu_core, "get_irq_status") else 0,
                "cpu_flow_state": int(cpu_core.get_state()) if hasattr(cpu_core, "get_state") else 0,
            }
            with open(reg_path, "w") as f:
                json.dump(regs, f)
            print(f"State saved to {reg_path}")
        except Exception as e:
            print(f"Error saving registers: {e}")
            sys.print_exception(e)

    def register_call_hook(self, address, fn, owner=None):
        """Register a callable for the given destination address.
        fn may be a Python function or a native C MicroPython function.
        Fires when CAL, JP, or JR targets this exact address — some ROM
        routines reach a given entry point via a plain JP/JR (a tail-call
        style jump) rather than CAL, so all three must be caught for the
        hook to reliably intercept every path in. CAL pushes a return
        address before jumping (interception pops it and returns as if
        RTN had executed); JP/JR push nothing, so interception simply
        skips the jump and continues at the next instruction instead.

        fn's return value chooses intercept vs. passthrough: returning
        nothing (None) or a truthy value intercepts -- fn fully replaces
        the target routine, exactly as above. Returning False passes
        through instead: the real push+jump (CAL) or jump (JP/JR) still
        happens right after fn runs, so fn acts as a pre-processing step
        in front of the original ROM code rather than a replacement for
        it. There is no built-in way to run code *after* the original
        routine returns (post-processing) -- that needs a second hook on
        its return address, or fn manually rewriting the pushed return
        address to a trampoline.

        owner: optional human-readable label (e.g. "dotds_64dot") shown by
        the emulator menu's Hook Status screen. Extension modules should
        pass their own module name; if omitted, fn.__name__ is used as a
        best-effort fallback (may be unavailable on some MicroPython builds).
        """
        if not hasattr(self, "_call_hook_refs"):
            self._call_hook_refs = {}
            self._call_hook_owner = {}
            self._call_hook_enabled = {}
        self._call_hook_refs[address] = fn  # Python-side GC anchor
        self._call_hook_owner[address] = owner or getattr(fn, "__name__", "?")
        self._call_hook_enabled[address] = True  # new entries are enabled by default
        if hasattr(cpu_core, "set_call_hook"):
            cpu_core.set_call_hook(address, fn)

    def enable_call_hook(self, address):
        """Enable a previously registered hook. No-op if not registered."""
        if hasattr(self, "_call_hook_enabled") and address in self._call_hook_enabled:
            self._call_hook_enabled[address] = True
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, True)

    def disable_call_hook(self, address):
        """Disable a registered hook without unregistering it."""
        if hasattr(self, "_call_hook_enabled") and address in self._call_hook_enabled:
            self._call_hook_enabled[address] = False
        if hasattr(cpu_core, "set_call_hook_enabled"):
            cpu_core.set_call_hook_enabled(address, False)

    def unregister_call_hook(self, address):
        """Unregister a previously registered CALL/JP/JR hook. No-op if not
        registered. See extension_api.md's hook-toggle table -- this was
        documented there before it actually existed; disable_call_hook()
        (keeps the registration, just suppresses firing) covers the
        "temporarily off" case and predates this, which is for "gone for
        good"."""
        if hasattr(cpu_core, "clear_call_hook"):
            cpu_core.clear_call_hook(address)
        if hasattr(self, "_call_hook_refs"):
            self._call_hook_refs.pop(address, None)
            self._call_hook_owner.pop(address, None)
            self._call_hook_enabled.pop(address, None)

    def list_call_hooks(self):
        """Return [(address, owner, enabled), ...] sorted by address, for
        diagnostic display (e.g. the emulator menu's Hook Status screen)."""
        refs = getattr(self, "_call_hook_refs", {})
        owners = getattr(self, "_call_hook_owner", {})
        enabled = getattr(self, "_call_hook_enabled", {})
        return sorted(
            (addr, owners.get(addr, "?"), enabled.get(addr, True))
            for addr in refs
        )

    def register_mem_write_hook(self, addr_start, fn, addr_end=None, owner=None):
        """Call fn(addr, data, bank) before a byte is written to memory.
        Omit addr_end to watch a single address; pass addr_end to watch a
        range (addr_start..addr_end inclusive). fn returning True cancels
        the write. Registering again with the same addr_start overwrites
        the previous entry (range and callable included).

        owner: optional human-readable label shown by the emulator menu's
        Hook Status screen; see register_call_hook() for details."""
        if addr_end is None:
            addr_end = addr_start
        if not hasattr(self, "_mem_write_hook_refs"):
            self._mem_write_hook_refs = {}
            self._mem_write_hook_range = {}
            self._mem_write_hook_owner = {}
            self._mem_write_hook_enabled = {}
        self._mem_write_hook_refs[addr_start] = fn  # Python-side GC anchor
        self._mem_write_hook_range[addr_start] = addr_end
        self._mem_write_hook_owner[addr_start] = owner or getattr(fn, "__name__", "?")
        self._mem_write_hook_enabled[addr_start] = True  # new entries are enabled by default
        if hasattr(cpu_core, "set_mem_write_hook"):
            cpu_core.set_mem_write_hook(addr_start, addr_end, fn)

    def unregister_mem_write_hook(self, addr_start):
        """Unregister a previously registered memory-write hook. No-op if
        not registered. See unregister_call_hook()'s docstring."""
        if hasattr(cpu_core, "clear_mem_write_hook"):
            cpu_core.clear_mem_write_hook(addr_start)
        if hasattr(self, "_mem_write_hook_refs"):
            self._mem_write_hook_refs.pop(addr_start, None)
            self._mem_write_hook_range.pop(addr_start, None)
            self._mem_write_hook_owner.pop(addr_start, None)
            self._mem_write_hook_enabled.pop(addr_start, None)

    def list_mem_write_hooks(self):
        """Return [(addr_start, addr_end, owner, enabled), ...] sorted by
        addr_start, for diagnostic display (e.g. the emulator menu's Hook
        Status screen)."""
        refs = getattr(self, "_mem_write_hook_refs", {})
        ranges = getattr(self, "_mem_write_hook_range", {})
        owners = getattr(self, "_mem_write_hook_owner", {})
        enabled = getattr(self, "_mem_write_hook_enabled", {})
        return sorted(
            (addr, ranges.get(addr, addr), owners.get(addr, "?"), enabled.get(addr, True))
            for addr in refs
        )

    def load_state(self, path=None, restore_cpu_state=True):
        """restore_cpu_state=True (default): full resume, including PC/UA/
        all registers, as captured by save_state() — used by the emulator
        menu's user-initiated "RAM Load".
        restore_cpu_state=False: RAM contents only, CPU forced to a clean
        reset (PC=0x0000, IB/IE/IA/UA cleared) — the old, conservative
        behavior. Used for the automatic boot-time restore (main.py), since
        an unattended boot must never be able to get stuck resuming a bad/
        inconsistent save with no way to reach the menu to recover (see
        2026-08-11 FOREX_PB boot-hang report: a save taken mid-VFDD-access
        resumed into a TRP whose 0x6FFA jump table pointed into the unmapped
        dead zone, hanging every subsequent boot until this split existed)."""
        import json
        if path is None:
            path0 = self._get_storage_path("ram0.bin")
            reg_path = self._get_storage_path("regs.json")
        else:
            path0 = f"{path}/ram0.bin"
            reg_path = f"{path}/regs.json"

        print(f"Loading state: RAM={path0}, REGS={reg_path}")
        import gc
        gc.collect()

        def _load_direct(f, view):
            """readinto でCバッファに直接書き込む。Pythonヒープ割り当てゼロ。"""
            return f.readinto(view)

        def _load_chunked(f, ram_target, chunk_size=256):
            """チャンク単位で書き込む。chunk_size を小さくしてヒープ断片化に対応。"""
            offset = 0
            buf = bytearray(chunk_size)
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                ram_target[offset:offset + n] = buf[:n]
                offset += n
                gc.collect()
            return offset

        def _load_to_ram(file_path, ram_target, slot):
            if not self._file_exists(file_path):
                print(f"RAM file not found: {file_path}")
                return False
            try:
                gc.collect()
                with open(file_path, "rb") as f:
                    if slot == 0 and hasattr(cpu_core, "load_ram"):
                        # メインRAM(8KB): C-API 経由で一括ロード
                        gc.collect()
                        try:
                            data = f.read()
                            cpu_core.load_ram(slot, data)
                            print(f"RAM slot {slot} loaded via C-API ({len(data)}B)")
                            del data
                            gc.collect()
                        except MemoryError:
                            f.seek(0)
                            n = _load_chunked(f, ram_target)
                            print(f"RAM slot {slot} loaded chunked ({n}B)")
                    elif hasattr(ram_target, '_view'):
                        # バンクRAM(32KB): readinto でCバッファに直接書き込み
                        # f.readinto(memoryview) はPythonヒープを一切消費しない
                        gc.collect()
                        try:
                            n = _load_direct(f, ram_target._view)
                            print(f"RAM slot {slot} loaded direct ({n}B) from {file_path}")
                        except Exception as _e:
                            print(f"RAM direct load failed ({_e}), retrying chunked")
                            f.seek(0)
                            n = _load_chunked(f, ram_target)
                            print(f"RAM slot {slot} loaded chunked ({n}B) from {file_path}")
                    else:
                        # fallback: bytearray バッファへのチャンク書き込み
                        n = _load_chunked(f, ram_target)
                        print(f"RAM slot {slot} loaded chunked ({n}B) from {file_path}")
                return True
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
                sys.print_exception(e)
                return False

        _load_to_ram(path0, self.ram, 0)
        gc.collect()
        for slot in range(1, 4):
            # has_bank[slot]/_bank_ram[slot] were already determined once in
            # __init__ (same file-existence check against the same profile
            # dir) and the C-side buffer only exists at all for banks that
            # were present then -- load_state() is only ever called once per
            # boot now (no more mid-session profile switch, see
            # [[project_ram_load_profile_switch_ext]]), so this just
            # reconfirms the same value rather than reacting to a changed
            # profile. Absent banks need no explicit fill: their C buffer
            # doesn't exist (self._bank_ram[slot] is None), and
            # c_mem_direct_read() already returns 0xFF for any bank with
            # has_bank[]==false regardless of buffer contents (see
            # modhd61700.c) -- there is no "previous profile's stale
            # contents" to guard against within a single process lifetime
            # any more either.
            rp = (self._get_storage_path(f"ram{slot}.bin") if path is None else f"{path}/ram{slot}.bin")
            present = self._file_exists(rp)
            self.has_bank[slot] = present
            if hasattr(cpu_core, "set_bank_present"):
                cpu_core.set_bank_present(slot, present)
            if present:
                _load_to_ram(rp, self._bank_ram[slot], slot)
            gc.collect()

        # Color VRAM (VDP extension) -- see save_state()'s comment for why this
        # needs its own file. Only restored if this profile actually saved one;
        # otherwise explicitly disable VDP (not just "leave it as-is") so a mid-
        # session profile switch via RAM Load can't leave a previous profile's
        # VDP state active over content that never used it.
        try:
            cv_path = self._get_storage_path("color_vram.bin") if path is None else f"{path}/color_vram.bin"
            if lcd_c is not None and self._file_exists(cv_path):
                cvram = lcd_c.get_color_vram()
                with open(cv_path, "rb") as f:
                    n = f.readinto(cvram)
                self.lcd.set_vdp_enable(True)
                if hasattr(lcd_c, "set_vdp_init_done"):
                    lcd_c.set_vdp_init_done(True)
                print(f"Color VRAM restored: {cv_path} ({n} bytes)")
            elif lcd_c is not None:
                self.lcd.set_vdp_enable(False)
        except Exception as e:
            print(f"Error loading Color VRAM: {e}")
            sys.print_exception(e)

        try:
            if self._file_exists(reg_path):
                if reg_path.endswith(".json"):
                    with open(reg_path, "r") as f:
                        regs = json.load(f)
                    # cpu_core.reset() first regardless of restore_cpu_state:
                    # hd61700_init() zeroes the whole C struct including callback
                    # pointers/bank buffers that must be re-wired either way.
                    cpu_core.reset(self.debug_cfg["sys"])
                    if not restore_cpu_state:
                        cpu_core.set_pc(0x0000)
                        cpu_core.set_reg8(2, 0)  # Clear IB
                        cpu_core.set_reg8(5, 0)  # Clear IE
                        cpu_core.set_reg8(4, 0)  # Clear IA
                        cpu_core.set_reg8(3, 0)  # Clear UA
                        print("CPU reset after RAM load (saved registers ignored, PC=0x0000)")
                    else:
                        # Full resume: restore the exact CPU state save_state()
                        # captured (pc/flags/ia/ib/ie/ua/regmain/regsir/reg16), not
                        # just the RAM contents.
                        #
                        # set_reg8(3, ua) (UA) already re-syncs the internal fetch_ua/
                        # prev_ua fetch-bank pipeline as a side effect (see the comment
                        # on mod_set_reg8 in modhd61700.c, written specifically for this
                        # save-state-restore case) — without that, the first instruction
                        # executed after resume would fetch from bank 0 regardless of
                        # the restored UA (reset() leaves fetch_ua/prev_ua at 0), which
                        # is exactly the class of UA-bank corruption bug this project
                        # spent a long investigation on for FOREX_PB (see
                        # 調査用/FOREX_PB/investigation_notes.md) — so UA must be
                        # restored via set_reg8, not written directly into a save
                        # format that bypasses it.
                        #
                        # 2026-08-11 FOREX_PB boot-hang report: a menu-triggered RAM
                        # Load resumed at PC=0x9318/UA=0x50 — ROM handler code, not
                        # FOREX_PB's own (FOREX_PB does no disk access, ruling out
                        # the game itself having caused the VFDD activity seen right
                        # after resume) — straight into a TRP whose 0x6FFA jump table
                        # resolved to 0x3720 (unmapped dead zone). The likely
                        # explanation: the save was taken while a periodic interrupt
                        # handler (e.g. keyboard-scan, which fires on its own schedule
                        # regardless of the loaded program) was mid-flight, temporarily
                        # in ROM/UA=0x50 territory — but irq_status wasn't part of the
                        # saved format, so resume left it at 0 (reset() default) even
                        # though PC was sitting inside what was an active handler,
                        # leaving fetch_bank_ua()'s "force bank 0 while a handler is
                        # active" protection incorrectly disengaged for any interrupt
                        # nesting/RTNI bookkeeping that follows. get_irq_status()/
                        # get_state() (added same day) close this gap; hasattr() guards
                        # keep old saves (and old firmware without these C exports)
                        # loading fine, just without this restored.
                        cpu_core.set_reg8(2, int(regs.get("ib", 0)))   # IB
                        cpu_core.set_reg8(5, int(regs.get("ie", 0)))   # IE
                        cpu_core.set_reg8(4, int(regs.get("ia", 0)))   # IA
                        cpu_core.set_reg8(3, int(regs.get("ua", 0)))   # UA (syncs fetch_ua/prev_ua)
                        cpu_core.set_flags(int(regs.get("flags", 0)))
                        for i, v in enumerate(regs.get("regmain", [])):
                            cpu_core.set_reg(i, int(v))
                        for i, v in enumerate(regs.get("regsir", [])):
                            cpu_core.set_sreg(i, int(v))
                        for i, v in enumerate(regs.get("reg16", [])):
                            cpu_core.set_reg16(i, int(v))
                        if hasattr(cpu_core, "set_irq_status"):
                            cpu_core.set_irq_status(int(regs.get("irq_status", 0)))
                        if hasattr(cpu_core, "set_state"):
                            cpu_core.set_state(int(regs.get("cpu_flow_state", 0)))
                        cpu_core.set_pc(int(regs.get("pc", 0)))
                        print("CPU state restored from RAM load (PC=0x%04X UA=0x%02X irq_status=0x%02X)" %
                              (int(regs.get("pc", 0)), int(regs.get("ua", 0)),
                               int(regs.get("irq_status", 0))))
                        # Known limitation: peripheral-side state driven by the
                        # emulated program (e.g. an in-progress virtual-FDD transfer)
                        # is still not captured — a save taken mid-transfer can resume
                        # into a CPU state that no longer matches what the peripheral
                        # emulation expects. Unlike irq_status this isn't CPU state,
                        # so it isn't something get_irq_status()/get_state() can help
                        # with; a full fix would need VFDD's own controller state
                        # snapshotted too.
                else:
                    self._restore_registers_from_dump()
            else:
                print(f"Register file not found: {reg_path}")
        except Exception as e:
            print(f"Error loading registers: {e}")
            sys.print_exception(e)
