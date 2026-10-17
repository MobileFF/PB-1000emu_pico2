"""
RAM Autosave — a flash-resident cache of ram0 (+ram1 when in scope) kept
up to date from file create/update/delete activity inside the emulated
PB-1000, so that unsaved progress survives an unexpected reboot or power
loss. This is separate from the user's own explicit "RAM Save" (Emulator
Menu); see doc/dev_guide.md's "RAM Autosave" section for the full design
background.

Detection uses two different mechanisms:

  1. A small, fixed-address `hd61700.set_mem_write_hook()` watch (see
     mp/pb1000_state_io.py's register_mem_write_hook()) on the file
     system's own boundary pointers (HIMEM/BASEN/MEMEN/DATDI/BASDI/DIREN,
     &H6941-&H694C -- references/sysvars.txt). Any write here means a
     file was created, deleted, or resized (the file area's boundaries
     moved). This range is only 12 bytes, rarely written, so routing it
     through a Python callback (register_mem_write_hook()) costs nothing
     measurable.

     The actual flush this triggers (_flush_boundary_triggered()) is
     *not* a full rewrite of the slot in the common case: these 12 bytes
     live below HIMEM, outside everything watch 2 (below) observes, so
     they're unioned onto whatever watch 2 separately captured in the
     same window and only that combined range is patched in. This covers
     every way the file area's structure can change -- including the
     case watch 2 alone would completely miss, a KILL of the file area's
     own trailing file, which only moves these pointers and rewrites no
     content byte at all -- because any OTHER structural change (e.g. a
     KILL of a non-trailing file, which shifts everything after it) does
     rewrite content, and that content lives inside watch 2's range, so
     watch 2 sees it regardless of what triggered the write. A full
     rewrite is still used as a one-time fallback right when a dirty-
     range watch's own range just changed (_need_full_ram0/1), or when a
     slot's cache file doesn't exist yet.

  2. A native (C-side) `hd61700.set_native_dirty_watch()` watch on the
     file area's own content (HIMEM..DIREN), re-ranged every time watch 1
     fires, additionally catches same-size in-place overwrites (e.g. a
     game re-saving a fixed-format save slot) that never move the
     boundary pointers. Only the touched byte range (a single min/max
     interval, not a full dirty bitmap) is tracked, and only that range
     is patched into the existing cache file via seek+write -- not a
     full rewrite.

     DIREN > 0x7FFF means the file area has grown into bank-switched
     RAM (ram1); watch 2 then also covers 0x8000-0xFFFF, filtered to
     bank==1 writes only (ram2/ram3 stay out of scope).

     This one deliberately does NOT use set_mem_write_hook(): that range
     can span several KB of the PB-1000's active BASIC-file workspace,
     and a mem_write_hook's per-write callback into Python
     (mp_call_function_n_kw(), see src/modhd61700.c) was measured to
     cause a drastic, continuously-perceptible CPU emulation slowdown
     when armed over a range that size -- registering ANY mem_write_hook
     disables the CPU core's ram_ptr/bank_ptr direct-pointer fast path
     for the union of all registered hooks' ranges (src/hd61700.c), not
     just the bytes an individual hook actually matches.
     set_native_dirty_watch() (src/hd61700.c / src/modhd61700.c) tracks
     the same min/max dirty interval entirely in C, on every write,
     *without* disabling that fast path and *without* calling into
     Python -- it is polled from poll() below instead of pushing to a
     callback.

Watch 1's callback only updates a bool (cheap: it runs from inside the
CPU's per-instruction dispatch). The actual file I/O for both watches
happens in poll(), called once per main-loop iteration, so it never
blocks CPU emulation mid-instruction -- but poll() itself still runs
synchronously on the main loop, so a *flush* (not the write-time
bookkeeping) is still a multi-hundred-ms stall when it happens (internal
flash: ~130ms for ram0 alone, ~480ms with ram1 -- see
mp/test/bench_ram_autosave_write.py's real-hardware measurements).

Watch 1's range (BASEN in particular) moves on far more than just
explicit file create/delete: entering or editing a single BASIC program
line changes that *file's* tokenized byte length, which shifts BASEN --
i.e. watch 1 fires on essentially every line you type and commit while
writing a program, not just on SAVE/KILL. Flushing synchronously and
immediately every single time it fires would mean a ~130-500ms stall on
every ENTER keypress while editing -- so the flush is debounced (see
_FULL_FLUSH_DEBOUNCE_MS/_FULL_FLUSH_MAX_WAIT_MS below): a burst of rapid
edits collapses into one flush after things go quiet, with an absolute
cap so a long uninterrupted typing session still gets flushed
periodically rather than only at the very end.

Watch 2 needs exactly the same debouncing, for a reason that's easy to
miss: HIMEM..DIREN isn't just "the saved file's bytes at rest" -- it's
also the live working memory of whatever's actually loaded there right
now. A running machine-language game keeps its own score/sprite/timer
variables inside its own loaded file image, because that's the only RAM
a loaded program has -- so those bytes get rewritten continuously during
ordinary gameplay, with no save/load/KILL involved at all. Polling and
flushing the moment poll_native_dirty_watch() ever returns non-None (as
an earlier version of this module did) means a ~130-500ms synchronous
flash write on nearly every main-loop tick during active gameplay -- a
continuous, perceptible stall with no file access in sight -- and leaves
behind a cache file almost immediately after every boot, so the restore
prompt (confirm_restore_autosave() in boot_session.py) ends up firing on
every subsequent boot even when the user never intentionally changed any
file. Both watches therefore share one debounced pending-flush timer:
hd61700.peek_native_dirty_watch(slot) (src/modhd61700.c) is a
*non-destructive* check -- unlike poll_native_dirty_watch(), it doesn't
consume the pending range -- so it can be called every tick, while the
debounce window is running, purely to decide whether to keep extending
it, without losing track of what's actually dirty until the window
finally elapses and poll_native_dirty_watch() is called for real.
"""
import os

import time

import hd61700 as cpu_core

# How long to wait, with no further boundary-pointer writes, before
# actually flushing (coalesces a burst of rapid edits -- e.g. typing and
# committing several BASIC program lines in a row -- into one flush).
#
# 2026-10-15: briefly tried shortening this (800->300), reverted after a
# real-hardware report of empty file content after a restore, right after
# a SAVE + power cycle. The suspected cause at the time was a lack of
# atomicity between this module's two watches. 2026-10-16: root-caused
# instead to _resync_ranges()'s bank1 branch unconditionally re-arming
# watch 2 on every boundary-pointer write, discarding its own
# just-accumulated dirty range regardless of whether anything actually
# needed re-arming (see 調査用/ram_autosave_investigation.md) -- now
# fixed. That bug didn't depend on this debounce value at all (shortening
# it only changed how *often* the already-broken bank1 tracking got
# exercised, not whether it was broken), so there's no longer a known
# reason to keep this conservative. Shortened again now that the real
# cause is fixed, to reduce how much unflushed editing a real power cut
# can lose -- pending real-hardware re-confirmation that no new issue
# surfaces at this shorter interval.
_FULL_FLUSH_DEBOUNCE_MS = 300
# ...but never wait longer than this since the *first* pending write in a
# burst, so a long uninterrupted editing session still gets flushed
# periodically instead of only once it finally pauses.
_FULL_FLUSH_MAX_WAIT_MS = 1500

# Duration of the "Autosave: Cache saved" on-screen toast shown after a
# completed flush (_flush_pending()) -- same value as main.py's Win+F8
# "Autosave: ON/OFF" toast (_AUTOSAVE_TOAST_MS), for a consistent feel.
_FLUSH_TOAST_MS = 2000

# references/sysvars.txt -- file-area boundary pointers (2 bytes each,
# little-endian: low byte first -- see references/HD61700.TXT's EQU
# example, "&H4241を代入...左側から下位、上位").
_HIMEM_ADDR = 0x6941
_BASEN_ADDR = 0x6943
_MEMEN_ADDR = 0x6945
_DATDI_ADDR = 0x6947
_BASDI_ADDR = 0x6949
_DIREN_ADDR = 0x694B
_PTR_HOOK_START = 0x6941
_PTR_HOOK_END = 0x694C  # high byte of DIREN (0x694B/0x694C)

_RAM0_TOP = 0x7FFF
_BANK1_LO = 0x8000
_BANK1_HI = 0xFFFF

# hd61700.set_native_dirty_watch() slot numbers used by this module.
_SLOT_RAM0 = 0
_SLOT_BANK1 = 1

# Always on internal flash root vfs (same convention as config.py's
# "/pb1000.ini", distinct from "/sd/pb1000.ini") -- see _compute_cache_dir().
_AUTOSAVE_ROOT = "/.autosave"


def _read_u16(addr):
    return cpu_core.read_mem(addr) | (cpu_core.read_mem(addr + 1) << 8)


class RamAutosave:
    def __init__(self, system):
        self.system = system
        self.cache_dir = self._compute_cache_dir(system)
        system._ensure_dir(self.cache_dir)

        self._layout_dirty = False
        self._bank1_in_scope = False
        self._ram0_watch_start = None
        # bank1's own last-armed (lo, hi) -- see _resync_ranges()'s bank1
        # branch for why this must be tracked the same way _ram0_watch_start
        # is (2026-10-16 fix): re-arming unconditionally on every layout
        # event discarded already-accumulated dirty state for no reason.
        self._bank1_watch_lo = None
        self._bank1_watch_hi = None
        self._full_flush_due_at = None     # ticks_ms deadline, or None if idle
        self._full_flush_pending_since = None  # ticks_ms of first pending write
        # Forces one full (not partial) flush of the given slot the next
        # time _flush_boundary_triggered() runs -- set whenever a native
        # dirty-watch slot's own range just changed (HIMEM moved, or bank1
        # just entered scope), since bytes written right at that transition
        # may have landed before the new range took effect and so would be
        # invisible to the dirty-range tracking this slot's partial flush
        # would otherwise rely on. See _resync_ranges().
        self._need_full_ram0 = True
        self._need_full_ram1 = True
        # Whether the pending debounced flush must also touch the fixed
        # 12-byte pointer range -- true for the whole pending window once
        # any boundary-pointer write happened in it, even if later writes in
        # the same window are content-only (watch 2). Reset after each flush.
        self._pending_include_ptr = False
        # Runtime on/off toggle (main.py's Win+F8 hotkey) -- independent of
        # the `[emulator] ram_autosave` config switch, which only controls
        # whether this object gets constructed at all. poll() just returns
        # immediately while this is False: both watches (the boundary hook
        # and the native dirty-range watches) keep running regardless --
        # they cost nothing measurable on their own (that's the whole point
        # of watch 2 being native rather than a Python mem_write_hook, and
        # watch 1 is a fixed 12 bytes) -- so there's no need to tear them
        # down and re-arm them on every toggle. Only the actual flush I/O
        # (the real cost, ~130-500ms per occurrence) is skipped. Any writes
        # that happened while disabled are simply not protected -- turning
        # this off is an explicit, user-requested trade of the power-loss
        # safety net for less flash I/O, same as unchecking the config
        # option, just without a reboot.
        self.enabled = True

        # Runtime statistics, reference-only (main.py's Win+PageUp hotkey
        # displays these on screen -- see stats_summary_line()). Counts
        # since this RamAutosave was constructed (this boot session only;
        # never persisted to disk). Helps judge, for a given profile/
        # piece of software, how much flash I/O this feature is actually
        # causing -- e.g. a program that rewrites its save data every
        # frame will show up here as a much higher flush rate than one
        # that only writes on an explicit SAVE.
        self.stat_flush_count = 0
        self.stat_full_flush_count = 0
        self.stat_partial_flush_count = 0
        self.stat_bytes_written = 0
        self.stat_total_flush_ms = 0
        self.stat_last_flush_ms = 0

        # [emulator] ram_autosave_log in pb1000.ini (default false) --
        # per-flush print(), restored 2026-10-17 at the user's request
        # after being left out on heap-reduction grounds (see
        # _stat_record()'s docstring). Opt-in rather than always-on since
        # an actively-written profile can flush several times a second.
        _cfg = getattr(system, "_config", None) or {}
        self._log_flushes = _cfg.get("emulator", {}).get(
            "ram_autosave_log", "false").lower() in ("1", "true", "yes", "on")

        system.register_mem_write_hook(
            _PTR_HOOK_START, self._on_boundary_write, _PTR_HOOK_END,
            owner="ram_autosave",
        )
        self._resync_ranges()

    # -- setup helpers --------------------------------------------------

    def _compute_cache_dir(self, system):
        """Always internal flash (_AUTOSAVE_ROOT), regardless of where the
        active profile's own save data lives. profile_dir is normally
        under /sd/rams/... (boot_session.PROFILE_ROOT) -- using it (or
        _get_storage_path(), which also prefers /sd/ once a profile or an
        SD card is present) directly, as an earlier version of this method
        did, silently put the autosave cache on the SD card too. That
        defeats the point (the ~130/480ms internal-flash latency this
        module's debounce timing is based on -- mp/test/
        bench_ram_autosave_write.py -- doesn't apply to SD I/O, and a
        removable SD card isn't what "survive an unexpected power loss"
        should depend on) and was also seen as visible LCD flicker during
        a flush, from the SD card's SPI traffic colliding with the LCD's
        own SPI updates on shared hardware.

        Namespaced by profile name (profile_dir's last path segment) so
        multiple profiles' caches don't collide; a fixed name when no
        profile is selected."""
        if system.profile_dir:
            name = system.profile_dir.rstrip("/").rsplit("/", 1)[-1]
        else:
            name = "_default"
        return f"{_AUTOSAVE_ROOT}/{name}"

    # -- hook callback (runs mid-CPU-instruction -- stays cheap, no I/O) --

    def _on_boundary_write(self, addr, data, bank):
        # `bank` here is the CPU's current segment register, not where this
        # write actually lands -- the boundary-pointer range (0x6941-
        # 0x694C) is fixed storage inside 0x6000-0x7FFF, which src/
        # modhd61700.c's c_mem_direct_write() always resolves to ram_buf
        # regardless of `bank` (consulted only for offset>=0x8000).
        self._layout_dirty = True
        return False  # passthrough: never block the ROM's own write

    # -- resync (called once at init, and whenever the boundary hook fires) --

    def _resync_ranges(self):
        # Unclamped HIMEM, used below for BOTH slots' low bound -- see the
        # bank1 fix note just below. (2026-10-14 bug, found via a VAIO.BAS
        # investigation: this profile's HIMEM, 0x93FF, sits ABOVE 0x7FFF,
        # i.e. *inside* bank1's own address range, not in ram0 at all --
        # entirely possible since HIMEM/BASEND/.../DIREN are themselves
        # ordinary 16-bit pointers with no requirement to stay below
        # 0x8000. The file area is always HIMEM..DIREN regardless of which
        # bank(s) that happens to span.)
        himem_raw = _read_u16(_HIMEM_ADDR)
        himem = min(max(himem_raw, _HIMEM_ADDR), _RAM0_TOP)  # defensive clamp
        if self._ram0_watch_start != himem:
            cpu_core.set_native_dirty_watch(_SLOT_RAM0, himem, _RAM0_TOP)
            self._ram0_watch_start = himem
            self._need_full_ram0 = True  # see __init__'s comment

        diren = _read_u16(_DIREN_ADDR)
        want_bank1 = diren > _RAM0_TOP
        if want_bank1:
            # Bank1's own low bound must also start at HIMEM when HIMEM
            # itself is >= 0x8000 (inside bank1) -- NOT unconditionally at
            # _BANK1_LO (0x8000). Before this fix, whenever HIMEM landed
            # inside bank1 (as above), this watched the *entire* bank1 from
            # 0x8000 up, which wrongly includes everything BELOW the real
            # HIMEM too -- BASIC's own live numeric/string variable tables
            # (FORSK/GOSSK/TONDT/DTTB/TOSDT/PTSDT, references/sysvars.txt)
            # end up bank-switched into that same region once a profile's
            # file area has grown enough to push HIMEM above 0x7FFF, and
            # those get rewritten continuously during ordinary execution
            # with zero file access -- the exact "ALL BASIC, no POKE/file
            # I/O, yet FLUSH keeps climbing" symptom this was traced from
            # (2026-10-14, VAIO.BAS). When HIMEM is still below 0x8000 (the
            # far more common case -- the file area starts in ram0), this
            # is simply max(himem_raw, _BANK1_LO) == _BANK1_LO, i.e.
            # unchanged from before.
            bank1_lo = max(himem_raw, _BANK1_LO)
            bank1_hi = min(diren, _BANK1_HI)
            # Only re-arm when the watched range actually changed -- same
            # pattern as _ram0_watch_start just above, and for the same
            # reason this one was MISSING for (2026-10-16 fix):
            # set_native_dirty_watch() unconditionally clears the slot's
            # accumulated dirty state on every call, even when start/end
            # are identical to what's already armed (src/modhd61700.c,
            # mod_set_native_dirty_watch()'s `dirty = false`). _resync_
            # ranges() runs on every single boundary-pointer write (watch
            # 1 -- essentially every BASIC line committed, not just SAVE/
            # KILL, see this module's own docstring), so re-arming
            # unconditionally here discarded watch 2's own
            # just-accumulated dirty range on almost every call -- most
            # visibly right when a line's tokenized bytes are written into
            # the file area (bank1, when HIMEM sits >= 0x8000) immediately
            # before BASEN's pointer update, which is the ordinary case:
            # the content write set the dirty flag, then the very next
            # poll() tick's layout_event re-armed the same unchanged range
            # and wiped it before _flush_pending() ever got to read it.
            # The resulting cache held an up-to-date boundary-pointer
            # block over stale (or, on a first-ever SAVE, entirely absent)
            # file content -- almost certainly the mechanism behind the
            # 2026-10-15 "SAVE後に内容が空になる" report (see 調査用/
            # ram_autosave_investigation.md).
            if (bank1_lo, bank1_hi) != (self._bank1_watch_lo, self._bank1_watch_hi):
                cpu_core.set_native_dirty_watch(_SLOT_BANK1, bank1_lo, bank1_hi, 1)
                self._bank1_watch_lo = bank1_lo
                self._bank1_watch_hi = bank1_hi
                self._need_full_ram1 = True  # see __init__'s comment
        elif self._bank1_in_scope:
            cpu_core.clear_native_dirty_watch(_SLOT_BANK1)
            self._bank1_watch_lo = None
            self._bank1_watch_hi = None
        self._bank1_in_scope = want_bank1

    # -- per-main-loop-iteration poll ------------------------------------

    def _content_dirty(self):
        """Non-destructive peek (see module docstring) -- true if either
        in-scope native dirty-range slot has unconsumed writes right now.
        Never consumes anything, so it's safe to call every tick, including
        while a debounce window is already running."""
        if cpu_core.peek_native_dirty_watch(_SLOT_RAM0):
            return True
        return self._bank1_in_scope and cpu_core.peek_native_dirty_watch(_SLOT_BANK1)

    def poll(self, now=None):
        """Call once per main-loop iteration with time.ticks_ms(). Cheap
        no-op when nothing is dirty and no flush is pending (a couple of
        attribute checks plus one or two native-side peeks -- each a single
        C-level slot lookup, see peek_native_dirty_watch()'s docstring)."""
        if not self.enabled:
            return
        if now is None:
            now = time.ticks_ms()

        layout_event = self._layout_dirty
        if layout_event:
            self._layout_dirty = False
            self._resync_ranges()  # cheap: no file I/O, just re-arms the watches
            self._pending_include_ptr = True

        # Arm (if idle) or extend (if already pending) the shared debounce
        # window on either trigger -- a boundary-pointer write just now, or
        # the content watch(es) still showing unconsumed writes. See the
        # module docstring for why watch 2 needs this exactly as much as
        # watch 1 does (a running game's own live variables, not just
        # save/load/KILL, live inside its range).
        dirty_now = layout_event or self._content_dirty()
        if self._full_flush_due_at is None:
            if not dirty_now:
                return
            self._full_flush_pending_since = now
            self._full_flush_due_at = time.ticks_add(now, _FULL_FLUSH_DEBOUNCE_MS)
            return

        if dirty_now:
            debounce_at = time.ticks_add(now, _FULL_FLUSH_DEBOUNCE_MS)
            cap_at = time.ticks_add(self._full_flush_pending_since,
                                     _FULL_FLUSH_MAX_WAIT_MS)
            self._full_flush_due_at = (
                debounce_at if time.ticks_diff(debounce_at, cap_at) <= 0
                else cap_at
            )

        if time.ticks_diff(now, self._full_flush_due_at) >= 0:
            self._flush_pending(self._pending_include_ptr)
            self._pending_include_ptr = False
            self._full_flush_due_at = None
            self._full_flush_pending_since = None

    # -- actual I/O -------------------------------------------------------

    def _ram_buf(self, slot):
        if slot == 0:
            return self.system.ram
        bank_ram = getattr(self.system, "_bank_ram", None)
        return bank_ram[1] if bank_ram is not None else None

    def _cache_path(self, slot):
        return f"{self.cache_dir}/ram{slot}.bin"

    # -- performance tuning: trimming watch 2's dirty range before I/O ---

    def _active_zones(self):
        """The file area's two *in-use* sub-ranges right now, as absolute
        PB-1000 addresses: the file bodies (HIMEM..MEMEN -- BASIC files
        then data files, both grow upward) and the directories
        (min(DATDI,BASDI)..DIREN -- both grow downward from DIREN, the
        fixed top). Whatever sits strictly between MEMEN and that
        directory low bound is free, never-written space.

        Used only by _flush_dirty_range() to trim a watch 2 interval
        before writing it -- detection itself (poll()/_content_dirty())
        deliberately stays the simple single min/max interval described
        in this module's docstring; this function doesn't change what
        counts as dirty, only how much of the surrounding free area gets
        written to flash alongside it. 2026-10-16 real-hardware finding:
        a single debounce window can dirty both a file body edit (near
        HIMEM/BASEN) and a directory-pointer update (near DIREN) at once,
        and watch 2's bounding interval then spans the entire free gap
        between them too -- in one observed case, ~24000 bytes for what
        was actually a 2-line, ~20-byte program edit. Skipping that gap
        here keeps the *detection* side simple while still avoiding most
        of the wasted I/O (see 調査用/ram_autosave_investigation.md)."""
        himem = _read_u16(_HIMEM_ADDR)
        memen = _read_u16(_MEMEN_ADDR)
        datdi = _read_u16(_DATDI_ADDR)
        basdi = _read_u16(_BASDI_ADDR)
        diren = _read_u16(_DIREN_ADDR)
        dir_lo = min(datdi, basdi)
        zones = []
        if memen >= himem:
            zones.append((himem, memen))
        if diren >= dir_lo:
            zones.append((dir_lo, diren))
        return zones

    def _flush_dirty_range(self, slot, abs_lo, abs_hi):
        """Writes a watch 2 dirty interval (absolute PB-1000 addresses,
        inclusive) into `slot`'s cache file, trimmed against
        _active_zones() so the part of the interval that falls in the
        free gap between the file body and the directory is skipped. Each
        remaining piece becomes its own _flush_partial() call, so a
        single wide-but-mostly-empty interval can turn into two small
        writes instead of one big one -- see _active_zones()'s docstring
        for the real-hardware case this was written for.

        Falls back to writing the untrimmed interval unchanged if
        _active_zones() produces nothing overlapping it at all (only
        possible if the pointers themselves look inconsistent, e.g. mid-
        update when this is read -- defensive only, should not happen in
        practice since _flush_pending() calls this well outside the CPU's
        own read-modify-write of those pointers)."""
        base = self.system.RAM_START if slot == 0 else _BANK1_LO
        top = _RAM0_TOP if slot == 0 else _BANK1_HI
        lo = max(abs_lo, base)
        hi = min(abs_hi, top)
        pieces = []
        for zlo, zhi in self._active_zones():
            a, b = max(lo, zlo), min(hi, zhi)
            if a <= b:
                pieces.append((a, b))
        if not pieces:
            pieces = [(lo, hi)]
        for a, b in pieces:
            self._flush_partial(slot, a - base, b - base)

    def _flush_pending(self, include_ptr):
        """Called once the shared debounce window (poll()) elapses. Rather
        than rewriting the whole slot, write only what could possibly have
        changed: whatever watch 2 (set_native_dirty_watch) captured in the
        file body during the window, unioned -- only when `include_ptr` is
        set, i.e. a boundary-pointer write (watch 1: file create/delete/
        resize) happened somewhere in this same window -- with the fixed
        12-byte boundary-pointer range itself (HIMEM/BASEN/.../DIREN --
        below HIMEM, so watch 2's content tracking never sees it, e.g. a
        KILL of the file area's own last/trailing file moves these
        pointers without rewriting a single content byte). See the "why is
        the boundary-pointer hook needed at all" discussion in
        doc/dev_guide.md section 15 for the full reasoning. When
        `include_ptr` is false, only watch 2 fired this window (e.g. a game
        overwriting its own fixed-format save slot in place) and the
        pointer bytes are left untouched since they didn't move.

        Falls back to a full rewrite of a slot when its own dirty-range
        watch was just (re)armed this round (_need_full_ram0/1 -- see
        __init__'s comment) or its cache file doesn't exist yet."""
        d0 = cpu_core.poll_native_dirty_watch(_SLOT_RAM0)
        wrote = False
        if self._need_full_ram0 or not self.system._file_exists(self._cache_path(0)):
            self._flush_full(0)
            self._need_full_ram0 = False
            wrote = True
        else:
            if include_ptr:
                ptr_lo = _PTR_HOOK_START - self.system.RAM_START
                ptr_hi = _PTR_HOOK_END - self.system.RAM_START
                self._flush_partial(0, ptr_lo, ptr_hi)
                wrote = True
            if d0 is not None:
                self._flush_dirty_range(0, d0[0], d0[1])
                wrote = True

        if self._bank1_in_scope:
            d1 = cpu_core.poll_native_dirty_watch(_SLOT_BANK1)
            if self._need_full_ram1 or not self.system._file_exists(self._cache_path(1)):
                self._flush_full(1)
                self._need_full_ram1 = False
                wrote = True
            elif d1 is not None:
                self._flush_dirty_range(1, d1[0], d1[1])
                wrote = True
            # else: bank1 in scope but nothing in it changed this window --
            # nothing to do.

        # On-screen confirmation that the cache now reflects this round's
        # changes -- same shared toast mechanism as Win+F8's "Autosave:
        # ON/OFF" (see main.py's _AUTOSAVE_TOAST_MS comment for why
        # set_status() and not a hand-painted box). `wrote` can end up
        # false here in principle (poll() only calls this function when
        # something looked dirty, but that dirtiness could in theory have
        # already been consumed elsewhere by the time this runs) -- guard
        # against announcing a save that didn't actually happen.
        if wrote:
            self.system.set_status("Autosave: Cache saved", _FLUSH_TOAST_MS)

    def _stat_record(self, slot, nbytes, t0, full):
        """Update the running flush statistics (see __init__). Called from
        _flush_full()/_flush_partial() right after a successful write.

        2026-10-17: restored the per-flush print() at the user's request
        (previously removed on heap-reduction grounds -- an f-string print
        on every flush is itself a transient heap allocation, however
        small, and this runs on every autosave write). Re-added as a plain
        print(), not an f-string, to keep that allocation minimal; still
        genuine log spam on an actively-written profile (a flush can fire
        several times a second), so this is opt-in via [emulator]
        ram_autosave_log in pb1000.ini (default off)."""
        elapsed = time.ticks_diff(time.ticks_ms(), t0)
        self.stat_flush_count += 1
        if full:
            self.stat_full_flush_count += 1
        else:
            self.stat_partial_flush_count += 1
        self.stat_bytes_written += nbytes
        self.stat_total_flush_ms += elapsed
        self.stat_last_flush_ms = elapsed
        if self._log_flushes:
            print("[ram_autosave] flush #", self.stat_flush_count,
                  " slot=", slot, " ", nbytes, "B full=", full,
                  " ", elapsed, "ms", sep="")

    def stats_summary_line(self):
        """Short one-line summary for main.py's Win+PageUp hotkey, shown via
        system.set_status() -- the status bar is a single fixed-width
        (~33 narrow-font chars) line, uppercase-only (pb1000.py's
        _render_status_bar()/_draw_text() -- a different, blockier font
        than draw_text.py's), so this is deliberately terse. An earlier
        version of this hotkey hand-painted its own two-line box in the
        top-left margin instead -- repeatedly collided with whatever else
        was already using that corner (boot_status.py's profile-name
        label, 2026-10-14) because that margin isn't actually reserved for
        anything. Routing through set_status() reuses the one screen
        region already dedicated to transient messages like this, with no
        extra bookkeeping needed here for when/how to clear it."""
        avg = (self.stat_total_flush_ms // self.stat_flush_count
                if self.stat_flush_count else 0)
        return "FLUSH=%d B=%d AVG=%dMS" % (
            self.stat_flush_count, self.stat_bytes_written, avg)

    def _flush_full(self, slot):
        buf = self._ram_buf(slot)
        if buf is None:
            return
        t0 = time.ticks_ms()
        try:
            data = buf._view if hasattr(buf, '_view') else buf
            with open(self._cache_path(slot), "wb") as f:
                f.write(data)
            self._stat_record(slot, len(data), t0, full=True)
        except Exception as e:
            print(f"[ram_autosave] full flush slot {slot} failed: {e}")

    def _flush_partial(self, slot, off_min, off_max):
        buf = self._ram_buf(slot)
        if buf is None:
            return
        path = self._cache_path(slot)
        if not self.system._file_exists(path):
            # Nothing to patch into yet -- do a one-time full flush instead.
            self._flush_full(slot)
            return
        t0 = time.ticks_ms()
        nbytes = off_max - off_min + 1
        try:
            data = buf._view if hasattr(buf, '_view') else buf
            # Slice straight from the RAM buffer's own memoryview and write
            # that -- never `bytes(data[...])` first. A memoryview slice is
            # a zero-copy view (same backing memory, just different bounds),
            # same as what _flush_full() already writes directly; wrapping
            # it in bytes() instead makes a full fresh heap copy of the
            # slice, which is exactly the size of this write and so scales
            # with it -- fine for the common case (a handful to a few
            # hundred bytes), but watch 2's range is a single min/max
            # bounding interval, not a precise dirty set (see this module's
            # docstring and 調査用/ram_autosave_investigation.md), so one
            # content write near HIMEM and an unrelated one up near DIREN in
            # the same debounce window can bound a range spanning nearly
            # the whole bank1 file area. 2026-10-16 real-hardware finding:
            # that produced a ~24000-byte chunk, which the bytes() copy
            # above failed to allocate (`memory allocation failed,
            # allocating 23991 bytes`) even though the much larger full-
            # slot flush (32768 bytes, no bytes() copy) succeeds right
            # above in _flush_full() -- same lesson as restore_from_cache()'s
            # own f.read(n) vs f.readinto() finding.
            with open(path, "r+b") as f:
                f.seek(off_min)
                f.write(data[off_min:off_max + 1])
            self._stat_record(slot, nbytes, t0, full=False)
        except Exception as e:
            print(f"[ram_autosave] partial flush slot {slot} failed: {e}")

    # -- boot-time restore / explicit-RAM-Save-time cleanup --------------

    def has_pending_cache(self):
        """True if an autosave cache exists for this profile. An explicit
        RAM Save always discard()s the cache (see emulator_menu_ram.py), so
        existence alone means "there is unsaved progress from last session
        this profile's own save doesn't have" -- no mtime comparison
        needed."""
        return self.system._file_exists(self._cache_path(0))

    def restore_from_cache(self):
        """Overlay ram0 (+ram1) with ONLY the file-area range the cache
        actually keeps current -- the 12-byte boundary-pointer block plus
        the HIMEM..DIREN content range, using the HIMEM/DIREN values AS
        RECORDED IN THE CACHE itself (not the live system's current
        HIMEM/DIREN -- the cache is, by definition, a newer edit than
        whatever the profile's own last RAM Save/load_state() set those
        to) -- on top of whatever load_state() already loaded.

        An earlier version of this method did a flat
        `f.readinto(whole_ram0_view)`, treating the entire cache file as
        a trustworthy full snapshot. It isn't: _flush_full() (a full
        rewrite of the cache file) only runs once, at cache creation, and
        again whenever HIMEM/DIREN's own range changes
        (_need_full_ram0/1) -- every other flush is a _flush_partial(),
        patching ONLY the tracked ranges in place. So bytes outside those
        ranges in the cache file can be stale, frozen at whatever they
        were during the last full rewrite, even though the file-area
        content itself is kept current. Blindly overlaying the whole
        buffer therefore clobbered live, already-correct RAM (OS work
        variables, anything outside the file area) with that stale
        snapshot -- the actual cause of "key input is read but the
        emulated OS behaves erratically right after a restore" (found
        2026-10-14: restoring never actually crashed anything, and no
        later reset_emulator()/power_on() could fix it either, because
        the damage was in RAM *content*, which no reset touches).

        Deliberately does NOT go through load_state() (which would also
        reset has_bank[2]/[3]/VDP state to match the cache directory's
        contents -- this cache never has those files, since they're out
        of scope, and load_state() would wrongly treat their absence as
        "this profile doesn't have them").

        Reads via f.readinto() straight into the destination RAM buffer's
        own memoryview, never via f.read(n) into a fresh bytes object.
        2026-10-14 real-hardware finding: an earlier version used
        f.read(n) for ram1's content range, and even when n (32768)
        exactly matched the destination buffer's own length, it still
        raised `MemoryError: memory allocation failed, allocating 32769
        bytes` -- f.read(n) apparently needs n+1 bytes internally for a
        read this large, one more than a same-sized buffer already
        holding the result would need. readinto() writes into memory the
        caller already owns, so it sidesteps that allocation (and the
        extra byte) entirely, regardless of why f.read() needed it."""
        import gc

        def _read_into(slot, file_off, view, mem_off, length):
            if length <= 0:
                return False
            path = self._cache_path(slot)
            try:
                with open(path, "rb") as f:
                    f.seek(file_off)
                    got = f.readinto(view[mem_off:mem_off + length])
                return got == length
            except OSError:
                return False
            except Exception as e:
                print(f"[ram_autosave] restore read failed ({path}): {e}")
                return False

        view0 = self._buf_view(self._ram_buf(0))
        if view0 is None:
            print("[ram_autosave] restore failed: ram0 buffer unavailable.")
            return False

        # Boundary-pointer block (12 bytes): read it straight into place
        # in view0 first, then re-read HIMEM/DIREN back out of view0
        # itself -- no separate temp buffer needed for either step.
        ptr_lo = _PTR_HOOK_START - self.system.RAM_START
        ptr_hi = _PTR_HOOK_END - self.system.RAM_START
        ok0 = _read_into(0, ptr_lo, view0, ptr_lo, ptr_hi - ptr_lo + 1)
        if not ok0:
            print("[ram_autosave] restore failed: boundary-pointer bytes unreadable.")
            return False

        # HIMEM/DIREN as recorded in the cache's own pointer bytes (see
        # the docstring above) -- offsets 0 and 10 within the 12-byte
        # block, matching _resync_ranges()'s reads of the same two fields
        # (HIMEM at 0x6941, DIREN at 0x694B -- references/sysvars.txt).
        himem_raw = view0[ptr_lo] | (view0[ptr_lo + 1] << 8)
        himem = min(max(himem_raw, _HIMEM_ADDR), _RAM0_TOP)  # defensive clamp
        diren_off = ptr_lo + 10
        diren = view0[diren_off] | (view0[diren_off + 1] << 8)

        off_lo0 = himem - self.system.RAM_START
        off_hi0 = min(diren, _RAM0_TOP) - self.system.RAM_START
        off_hi0 = min(off_hi0, len(view0) - 1)  # never past view0's own end
        if off_hi0 >= off_lo0:
            ok0 = _read_into(0, off_lo0, view0, off_lo0, off_hi0 - off_lo0 + 1) or ok0

        ok1 = False
        if diren > _RAM0_TOP:
            view1 = self._buf_view(self._ram_buf(1))
            if view1 is None:
                # Known limitation: the cache has ram1 content, but bank1
                # isn't allocated for this profile (its own explicit save
                # never had ram1.bin, so PB1000System.__init__() never
                # built the RAM1 buffer/view for it -- unlike has_bank[]/
                # the C-side buffer, which ensure_bank_buf() can allocate
                # lazily, the Python-side RAMView wrapper this needs is
                # only ever built once, at __init__ time). Skipped rather
                # than guessed at.
                print("[ram_autosave] ram1 cache found but bank1 isn't set up "
                      "for this profile -- skipped (known limitation).")
            else:
                # Same reasoning as _resync_ranges()'s bank1 fix (see that
                # comment): when HIMEM itself is >= 0x8000, only HIMEM..
                # DIREN within bank1 is kept current by flushes -- bytes
                # below HIMEM there are BASIC's own live variable tables,
                # never re-flushed after the one-time full rewrite, so
                # restoring them from the cache would overlay stale data
                # onto live, already-correct RAM (the same bug restore_
                # from_cache() was rewritten to avoid for ram0, above).
                off_lo1 = max(himem_raw, _BANK1_LO) - _BANK1_LO
                off_hi1 = min(diren, _BANK1_HI) - _BANK1_LO
                off_hi1 = min(off_hi1, len(view1) - 1)  # never past view1's own end
                if off_hi1 >= off_lo1:
                    ok1 = _read_into(1, off_lo1, view1, off_lo1, off_hi1 - off_lo1 + 1)

        gc.collect()
        print(f"[ram_autosave] restored from cache (ram0={ok0}, ram1={ok1})")
        return ok0 or ok1

    def _buf_view(self, buf):
        if buf is None:
            return None
        return buf._view if hasattr(buf, '_view') else buf

    def commit_to_profile(self):
        """Write the now-restored ram0 (+ram1) content straight into the
        profile's own save files (ram0.bin/ram1.bin), so the profile stops
        being perpetually stale relative to the autosave cache.

        2026-10-17, added at the user's request: restore_from_cache() only
        ever overlaid the *in-memory* RAM (on top of what load_state()
        already loaded) -- the profile's own files on disk were untouched,
        so if the cache were ever lost (SD issue, discard() called some
        other way, etc.) before an explicit RAM Save, the profile's save
        would still only reflect whatever the *last* explicit save had,
        not what every boot-time restore had been showing the user in the
        meantime. Call this right after a successful restore_from_cache()
        to make that restore "stick" into the profile by default, then
        discard() the cache -- same invariant an explicit RAM Save already
        keeps (has_pending_cache()'s docstring: cache existence means
        "unsaved progress the profile's own save doesn't have yet").

        Deliberately writes only ram0.bin/ram1.bin (not regs.json/
        color_vram.bin via save_state()) -- this module's own scope is
        the file-area within those two slots only, and nothing has run
        yet at this point in boot, so a regs.json/color_vram.bin resave
        here would just rewrite the exact bytes load_state() loaded
        moments ago for no benefit."""
        try:
            view0 = self._buf_view(self._ram_buf(0))
            if view0 is not None:
                with open(self.system._get_storage_path("ram0.bin"), "wb") as f:
                    f.write(view0)
            if self._bank1_in_scope:
                view1 = self._buf_view(self._ram_buf(1))
                if view1 is not None:
                    with open(self.system._get_storage_path("ram1.bin"), "wb") as f:
                        f.write(view1)
            return True
        except Exception as e:
            print(f"[ram_autosave] commit to profile failed: {e}")
            return False

    def discard(self):
        """Delete the autosave cache. Called after an explicit RAM Save to
        the active profile (which supersedes it) and when the user declines
        the boot-time restore prompt."""
        # Cancel any debounced flush still pending -- otherwise poll() would
        # resurrect the very cache file just deleted on its next tick.
        self._full_flush_due_at = None
        self._full_flush_pending_since = None
        self._pending_include_ptr = False
        # No baseline left on disk -- the next flush of either slot must be
        # full again (the file-existence check in _flush_full()/_flush_
        # partial() would force this anyway, but set it explicitly too).
        self._need_full_ram0 = True
        self._need_full_ram1 = True
        for slot in (0, 1):
            try:
                os.remove(self._cache_path(slot))
            except OSError:
                pass
