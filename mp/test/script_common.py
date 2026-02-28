"""
Common helpers for test scripts:
- debug.ini loading
- debug override extraction
- trace-output-aware log tee
"""

import builtins
import time


def to_bool(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def parse_ini(path):
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


def load_debug_ini():
    ini_paths = ("debug.ini", "/debug.ini", "/mp/debug.ini")
    for path in ini_paths:
        try:
            return parse_ini(path), path
        except OSError:
            pass
    return None, ""


def get_debug_overrides(ini_data):
    cfg = {"c_memory": None, "c_lcd": None}
    if not ini_data:
        return cfg
    debug = ini_data.get("debug", {})
    if "c_memory" in debug:
        cfg["c_memory"] = to_bool(debug["c_memory"])
    if "c_lcd" in debug:
        cfg["c_lcd"] = to_bool(debug["c_lcd"])
    return cfg


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


def _path_exists(path):
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


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


class ScriptLogger:
    __slots__ = (
        "ini_data",
        "default_log_path",
        "trace_mode",
        "trace_path",
        # stream removed from slots, we will open on demand
        "_orig_print",
        "_installed",
    )

    def __init__(self, ini_data, default_log_path):
        # use slots so each instance omits a __dict__ (40‑60 bytes saved)
        self.ini_data = ini_data or {}
        self.default_log_path = default_log_path
        self.trace_mode = "console"
        self.trace_path = default_log_path
        self._orig_print = builtins.print
        self._installed = False
        self._setup_output()

    def _setup_output(self):
        trace = self.ini_data.get("trace", {})
        mode = str(trace.get("trace_output", "console")).strip().lower()
        path = str(trace.get("trace_output_path", self.default_log_path)).strip()
        rotate = to_bool(trace.get("trace_output_rotate_per_run", "true"))
        if not path:
            path = self.default_log_path

        self.trace_mode = mode
        self.trace_path = path

        # we no longer open a stream here; the file will be opened lazily in
        # :meth:`print` so that no long-lived VfsFile object resides in heap.
        if mode != "file":
            return

        # choose a rotated path now, but don't keep the file open
        candidates = [path]
        if path.startswith("/"):
            candidates.append(path[1:])
            candidates.append("/mp/" + path[1:])

        for cand in candidates:
            out_path = _rotate_trace_output_path(cand) if rotate else cand
            # if we can open once, just close immediately to test writability
            try:
                f = open(out_path, "w")
                f.close()
                self.trace_path = out_path
                return
            except OSError:
                pass

        self.trace_mode = "console"
        self.trace_path = ""

    def install_print_hook(self):
        if self._installed:
            return
        builtins.print = self.print
        self._installed = True

    def print(self, *args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        text = sep.join(str(a) for a in args)
        self._orig_print(*args, **kwargs)
        if self.trace_mode != "file":
            return
        # open, write, and close each time; this keeps no VfsFile around
        try:
            with open(self.trace_path, "a") as f:
                f.write(text)
                f.write(end)
        except OSError:
            pass

    def close(self):
        if self._installed:
            builtins.print = self._orig_print
            self._installed = False
#         if self.stream is not None:
#             self.stream.close()
#             self.stream = None


class LazyLogger:
    """Minimal proxy that only creates a ScriptLogger when first used."""
    __slots__ = ("_ini_data", "_path", "_real")

    def __init__(self, ini_data, default_log_path):
        self._ini_data = ini_data
        self._path = default_log_path
        self._real = None

    def _ensure(self):
        if self._real is None:
            self._real = ScriptLogger(self._ini_data, self._path)

    def install_print_hook(self):
        self._ensure()
        self._real.install_print_hook()

    def __getattr__(self, name):
        self._ensure()
        return getattr(self._real, name)


def create_script_runtime(default_log_path):
    """Return a runtime dictionary for test scripts.

    Instead of immediately instantiating :class:`ScriptLogger`, create a
    :class:`LazyLogger` proxy.  Memory used by the actual logger (INI parsing,
    file-path allocation, etc.) is deferred until the first time the caller
    accesses ``runtime['logger']`` or calls ``install_print_hook``.
    """
    ini_data, ini_path = load_debug_ini()
    logger = LazyLogger(ini_data, default_log_path)
    debug_overrides = get_debug_overrides(ini_data)
    return {
        "ini_data": ini_data,
        "ini_path": ini_path,
        "logger": logger,
        "debug_overrides": debug_overrides,
    }

