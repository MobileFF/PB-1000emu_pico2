"""
PB1000System's virtual FDD (MD-100) and general storage-path helpers, split
out of pb1000.py as PB1000FddMixin -- mixed into PB1000System via multiple
inheritance (`class PB1000System(PB1000FddMixin, PB1000StateIOMixin):`).

Why: pb1000.py itself (never lazily split before this) grew to ~1943 lines
as PB1000System's own single class body, and on 2026-08-22 real hardware hit
`MemoryError: memory allocation failed, allocating 5528 bytes` compiling it
inside create_system() -- despite ~193KB free at that point, confirming the
same fragmentation pattern already fixed this session for main_input.py,
emulator_menu.py, etc: MicroPython compiles each imported file as one unit
needing one contiguous block, so total free memory isn't the constraint,
the largest available *contiguous* hole is. Splitting the class across
multiple files via mixins turns that one big compile into several smaller
ones, each with much better odds of fitting a fragmented heap -- same fix,
applied to a single class body instead of independent functions.

PD_RES/PD_PWR/PD_STR mirror the copies in pb1000.py (still needed there by
_port_read()/_port_write()/_write_io_register(), which stay in the core
class) -- these are fixed MD-100 hardware register bit values that will
never change, so duplicating three lines here was judged simpler and more
robust than a circular `from pb1000 import ...` back-reference.
"""
import os
import sys
import hd61700 as cpu_core
from fdd_storage import ImageStorageBackend
from md100_dos import MD100Dos

PD_RES = 0x08
PD_PWR = 0x10
PD_STR = 0x04
ENABLE_VIRTUAL_FDD = True


def load_virtual_fdd_config(path):
    try:
        os.stat(path)
    except OSError:
        return None
    section = ""
    values = {}
    with open(path, "r") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line[0] in ("#", ";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if section != "disk" or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lower()
            v = v.split(";", 1)[0].split("#", 1)[0].strip()
            values[k] = v
    if not values:
        return None
    def _bool(s):
        return s.lower() in ("1", "true", "yes", "on")
    raw_path = values.get("path", "").strip()
    if raw_path and not raw_path.startswith("/"):
        parts = path.rsplit("/", 1)
        base = parts[0] if len(parts) > 1 else ""
        raw_path = base + "/" + raw_path if base else raw_path
    return {
        "config_path": path,
        "enabled": _bool(values.get("enabled", "false")),
        "backend": values.get("backend", "image").strip().lower() or "image",
        "path": raw_path,
        "readonly": _bool(values.get("readonly", "false")),
    }


class PB1000FddMixin:
    def _log_vfdd(self, msg):
        pass

    def _handle_virtual_fdd_port_write(self, data):
        if not self.has_virtual_fdd():
            self._port_last_write = data & 0xFF
            return

        current = data & 0xFF
        previous = self._port_last_write
        was_powered = (previous & PD_PWR) == 0
        powered_now = (current & PD_PWR) == 0
        self._virtual_fdd_interface_powered = powered_now
        # True only when RES is released in THIS same CTRL write (not a persistent flag).
        # Used to detect the boot pulse where STR falls simultaneously with RES release.
        res_released_now = (current & PD_RES) == 0 and (previous & PD_RES) != 0

        if (current & PD_PWR) != (previous & PD_PWR):
            try:
                _pc_dbg = f" PC={cpu_core.get_pc():#06x}"
            except Exception:
                _pc_dbg = ""
            print(f"[VFDD] Power: {'ON' if powered_now else 'OFF'}{_pc_dbg}")
            if powered_now:
                # Power just turned ON: pre-load 0x55 so boot detection works
                # even before any RES/STR pulse occurs
                self._io_rd_regs[4] = 0x55  # MD-100 identifier
                self._gpo_parity = 0         # Reset parity for clean D92E P1/P0 pairs
            else:
                self._virtual_fdd_ack = False
                self.virtual_fdd_controller.close()

        if powered_now:
            # Power is ON (Active Low)
            if (current & PD_RES) != 0 and (previous & PD_RES) == 0:
                # Rising edge of RES: Device Enters Reset (Active HIGH)
                self._log_vfdd(f"Reset Detected (Active HIGH)")
                self._virtual_fdd_ack = False
                self.virtual_fdd_controller.open()
            elif (current & PD_RES) == 0 and (previous & PD_RES) != 0:
                # Falling edge of RES: Reset Released (Run Mode)
                self._log_vfdd("Reset released")
                # Boot ROM reads 0x0C03, stores it in OPTCD, then checks
                # OPTCD==0x55 to confirm the MD-100 interface is present.
                self._io_rd_regs[4] = 0x55  # MD-100 identifier for boot detection
                if hasattr(cpu_core, "set_vfdd_data"):
                    cpu_core.set_vfdd_data(0x55)
                self._inject_optcd_signature("reset-release")

            # Allow transfer whenever power is on, ensuring P3 is always updated by STR
            # Handle STR (Strobe) toggling
            if (current & PD_STR) == 0 and (previous & PD_STR) != 0:
                # Falling edge of STR: Start of transfer cycle.
                # Data was already pre-fetched at the end of the previous cycle.
                self._virtual_fdd_ack = True

                # cpu_core.get_vfdd_write_data() is broken (always 0x00); use
                # _io_wr_regs[4], kept correct by the _write_io_register callback.
                val_in = self._io_wr_regs[4]

                if res_released_now:
                    # Boot pulse: RES released in this same CTRL write as STR
                    # fell (e.g. CTRL 1C->00). Don't call transfer() here — it
                    # would advance state before the real command is issued;
                    # just leave _io_rd_regs[4]=0x55 for the ROM's OPTCD read.
                    pass
                else:
                    val_out_next = self.virtual_fdd_controller.transfer(val_in)
                    self._io_rd_regs[4] = val_out_next
                    if hasattr(cpu_core, "set_vfdd_data"):
                        cpu_core.set_vfdd_data(val_out_next)

            elif (current & PD_STR) != 0 and (previous & PD_STR) == 0:
                # Rising edge of STR: End of transfer cycle.
                self._virtual_fdd_ack = False
        else:
            self._virtual_fdd_ack = False
            if was_powered and not powered_now:
                self._log_vfdd("Interface entered power-off state")
        if (current & PD_RES) != 0 and (previous & PD_RES) == 0:
            # FDD Reset rising edge outside powered block: reset state machine
            self.virtual_fdd_controller.open()

        self._port_last_write = current

    def _inject_optcd_signature(self, reason="runtime"):
        try:
            ram_idx = 0x6BFA - 0x6000
            if 0 <= ram_idx < len(self.ram):
                self.ram[ram_idx] = 0x55
                print(f"[VFDD] Injected OPTCD=0x55 at 0x6BFA ({reason})")
        except Exception as e:
            print(f"[VFDD] Failed to inject OPTCD ({reason}): {e}")
            sys.print_exception(e)

    def _register_dump_path(self):
        return "/roms/register.bin"

    def _restore_registers_from_dump(self):
        path = self._register_dump_path()
        try:
            with open(path, 'rb') as f:
                data = f.read(36)
            if len(data) >= 36:
                cpu_core.set_registers(data[:36])
                print(f"registers restored from {path}")
        except OSError:
            pass

    def _ensure_dir(self, path):
        """Create directory if it doesn't exist."""
        try:
            parts = path.strip("/").split("/")
            curr = ""
            for p in parts:
                curr += "/" + p
                try:
                    os.mkdir(curr)
                except OSError:
                    pass
        except Exception as _e:
            sys.print_exception(_e)

    def _get_storage_path(self, filename):
        """Return the best path for a file. Profile dir takes highest priority."""
        if self.profile_dir:
            return self.profile_dir + "/" + filename

        sd_path = "/sd/" + filename
        roms_path = "/roms/" + filename
        root_path = "/" + filename

        if self.sd_mounted:
            if filename in ("ram0.bin", "ram1.bin", "ram2.bin", "ram3.bin", "regs.json", "color_vram.bin"):
                if self._file_exists(sd_path):
                    return sd_path
                if self._file_exists(roms_path):
                    return roms_path
                return sd_path

            if self._file_exists(sd_path):
                return sd_path

        if self._file_exists(roms_path):
            return roms_path
        return root_path

    def _file_exists(self, path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def _virtual_fdd_config_candidates(self):
        return (
            "/sd/profile.ini",
            "/sd/virtual_fdd.ini",
            "/roms/profile.ini",
        )

    def discover_virtual_fdd_config(self):
        if not ENABLE_VIRTUAL_FDD:
            self.virtual_fdd_config = {"enabled": False, "reason": "disabled by flag"}
            self._pending_virtual_fdd_config = None
            return None

        # Use [disk] section from merged pb1000.ini config if available
        if self._config is not None and "disk" in self._config:
            disk = self._config["disk"]
            enabled = disk.get("enabled", "false").lower() in ("1", "true", "yes", "on")
            if not enabled:
                self.virtual_fdd_config = {"enabled": False, "reason": "disabled in pb1000.ini"}
                self._pending_virtual_fdd_config = None
                return None
            raw_path = disk.get("path", "").strip()
            if raw_path and not raw_path.startswith("/"):
                raw_path = "/sd/" + raw_path
            cfg = {
                "enabled": True,
                "backend": disk.get("backend", "raw").strip(),
                "path": raw_path,
                "readonly": disk.get("readonly", "false").lower() in ("1", "true", "yes", "on"),
            }
            print(f"[VFDD] Config from pb1000.ini: {raw_path}")
            self.virtual_fdd_config = cfg
            self._pending_virtual_fdd_config = cfg
            return cfg

        for config_path in self._virtual_fdd_config_candidates():
            cfg = load_virtual_fdd_config(config_path)
            if cfg:
                print(f"[VFDD] Found config: {config_path}")
                self.virtual_fdd_config = cfg
                self._pending_virtual_fdd_config = cfg
                return cfg

        if ENABLE_VIRTUAL_FDD:
            # Fallback to default
            default_path = "/sd/disks/disk1.img"
            print(f"[VFDD] No config file found. Using default: {default_path}")
            cfg = {
                "enabled": True,
                "backend": "image",
                "path": default_path,
                "readonly": False
            }
            self.virtual_fdd_config = cfg
            self._pending_virtual_fdd_config = cfg
            return cfg

        return None

    def configure_virtual_fdd(self, path=None, readonly=False, enabled=True):
        if not enabled:
            self.disable_virtual_fdd()
            return False

        if not path:
            raise ValueError("virtual FDD path is required")

        # Create image file if it does not exist yet
        new_disk = False
        try:
            os.stat(path)
        except OSError:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent:
                try:
                    os.stat(parent)
                except OSError:
                    print(f"[VFDD] Directory not found: {parent}. Disabling virtual FDD.")
                    self.disable_virtual_fdd()
                    return False
            ImageStorageBackend.create(path, 256)
            new_disk = True
            print(f"[VFDD] Created new disk image: {path}")

        # Inject MD-100 identifier into OPTCD (&H6BFA) in main RAM.
        # This may be overwritten later by ROM error paths, so we also refresh it
        # on reset-release.
        self._inject_optcd_signature("configure")

        backend = ImageStorageBackend(path, readonly=readonly)
        dos = MD100Dos()
        dos.dos_init(backend)
        if new_disk:
            dos.format_disk()
        self.virtual_fdd = backend
        self.virtual_fdd_controller.attach_dos(dos)
        self.virtual_fdd_controller.fdd_open()

        # New C-side Selective Hooking:
        # We stay in C-managed memory mode (Full Speed) and only hook 0x0C00 range.
        if hasattr(cpu_core, "set_io_callbacks"):
            # I/O callbacks are already registered during __init__.
            # Re-registering here is unnecessary and can destabilize boot on-device.
            print("[VFDD] C-side selective MMIO hooking already active")

        self.virtual_fdd_config = {
            "enabled": True,
            "backend": "image",
            "path": path,
            "readonly": bool(readonly),
        }
        # Ensure Python-side ROM copies exist for the callback path (Bug #1)
        self._ensure_rom_copies()

        print(
            "Virtual FDD enabled: "
            f"path={path} readonly={1 if readonly else 0}"
        )
        return True

    def _ensure_rom_copies(self):
        """Reload ROM data into Python copies when C direct memory is disabled."""
        if self.rom0 is None or len(self.rom0) == 0:
            self.load_rom('/roms/rom0.bin', slot=0, keep_copy=True)
        if self.rom1 is None or len(self.rom1) == 0:
            self.load_rom('/roms/rom1.bin', slot=1, keep_copy=True)

    def disable_virtual_fdd(self):
        if self.virtual_fdd is not None:
            try:
                self.virtual_fdd.close()
            except Exception:
                pass
        self.virtual_fdd_controller.attach_dos(None)
        self.virtual_fdd = None
        self.virtual_fdd_config = {"enabled": False}

    def swap_disk(self, new_path):
        """実行中にディスクイメージを差し替える。new_path=None でイジェクト。"""
        if self.has_virtual_fdd():
            self.disable_virtual_fdd()
        if new_path is None:
            print("[VFDD] Disk ejected.")
            return True
        try:
            return self.configure_virtual_fdd(new_path)
        except Exception as e:
            print(f"[VFDD] swap_disk failed: {e}")
            sys.print_exception(e)
            return False

    def activate_pending_virtual_fdd(self):
        cfg = self._pending_virtual_fdd_config
        if not cfg or not cfg.get("enabled", False):
            self._pending_virtual_fdd_config = None
            return False
        try:
            result = self.configure_virtual_fdd(
                path=cfg.get("path"),
                readonly=cfg.get("readonly", False),
                enabled=True,
            )
            if result:
                self._pending_virtual_fdd_config = None
            return result
        except Exception as exc:
            print(f"[VFDD] Auto-config failed: {exc}")
            import sys
            sys.print_exception(exc)
            return False

    def boot_virtual_fdd(self):
        """Robust initialization: Discovery + Activation in one call."""
        cfg = self.discover_virtual_fdd_config()
        if cfg:
            return self.activate_pending_virtual_fdd()
        print("[VFDD] No config found")
        return False

    def has_virtual_fdd(self):
        return self.virtual_fdd is not None

    def _ram_path(self, slot=0):
        return f"/roms/ram{slot}.bin"

    def load_ram(self):
        path0 = self._ram_path(0)
        try:
            with open(path0, 'rb') as f:
                val = f.read(self.RAM_SIZE)
            if val:
                for i in range(len(val)):
                    self.ram[i] = val[i]
                print(f"Standard RAM restored from {path0}")
        except OSError:
            pass

        if self.has_exp:
            path1 = self._ram_path(1)
            try:
                with open(path1, 'rb') as f:
                    val = f.read(self.EXP_RAM_SIZE)
                if val:
                    for i in range(len(val)):
                        self.exp_ram[i] = val[i]
                    print(f"Expanded RAM restored from {path1}")
            except OSError:
                pass
