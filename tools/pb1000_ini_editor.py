#!/usr/bin/env python3
"""
PB-1000 pb1000.ini Editor
GUI tool for editing pb1000.ini configuration files used by the PB-1000 emulator.

Edits are applied line-by-line on top of the original file: comments, blank
lines, section ordering and commented-out example settings are preserved
byte-for-byte except for the specific lines you actually change. Enabling a
currently-commented setting uncomments its existing line (keeping it in its
documented position); disabling an active setting re-comments it rather than
deleting it.

Usage:
    python pb1000_ini_editor.py [pb1000.ini]
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import re
import shutil
import subprocess
import sys

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_INI = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mp', 'pb1000.ini')
)

BOOL_TRUE = ('1', 'true', 'yes', 'on')

# type: 'bool' | 'int' | 'float' | 'str' | 'password' | 'choice'
# schema[section][key] = (type, extra)
#   extra: for 'choice' -> tuple of allowed string values; for 'int'/'float' -> (min, max) or None
SCHEMA = {
    'display': {
        'driver':       ('choice', ('ILI9341', 'ST7796')),
        'spi_baudrate': ('int', None),
        'scale':        ('float', None),
        'lcd_height':   ('choice', ('32', '64')),
        'x_offset':     ('int', None),
        'y_offset':     ('int', None),
        'rotation':     ('choice', ('0', '180')),
        'fg_color':     ('int', (0, 255)),
        'bg_color':     ('int', (0, 255)),
    },
    'keyboard': {
        'enable_usb_kbd':              ('bool', None),
        'enable_uart_kbd':             ('bool', None),
        'uart_baudrate':               ('int', None),
        'uart_tx_pin':                 ('int', None),
        'uart_rx_pin':                 ('int', None),
        'key_hold_ms':                 ('int', None),
        'key_release_hard_timeout_ms': ('int', None),
        'inter_key_gap_ms':            ('int', None),
        'uart_enter_always_exe':       ('bool', None),
    },
    'emulator': {
        'enable_repl_uart':     ('bool', None),
        'frame_interval_ms':    ('int', None),
        'active_step_count':    ('int', None),
        'sleep_poll_ms':        ('int', None),
        'step_timer_tick_steps': ('int', None),
        'timer_tick_ms':        ('int', None),
        'loop_idle_ms':         ('int', None),
        'step_chunk':           ('int', None),
    },
    'disk': {
        'enabled':  ('bool', None),
        'backend':  ('choice', ('raw',)),
        'path':     ('str', None),
        'readonly': ('bool', None),
    },
    'profile': {
        'default_profile': ('str', None),
        'ui_timeout_ms':   ('int', None),
    },
    'joystick': {
        'enable':           ('bool', None),
        'enable_fire2':     ('bool', None),
        'debounce_ms':      ('int', None),
        'poll_interval_ms': ('int', None),
        'key_up':    ('str', None),
        'key_down':  ('str', None),
        'key_left':  ('str', None),
        'key_right': ('str', None),
        'key_fire1': ('str', None),
        'key_fire2': ('str', None),
    },
    'beep': {
        'enable':   ('bool', None),
        'gpio_pin': ('int', None),
        'freq_hz':  ('int', None),
        'duty':     ('int', (0, 100)),
    },
    'touch': {
        'x_offset':          ('int', None),
        'y_offset':          ('int', None),
        'funckey_x_offset':  ('int', None),
        'funckey_y_offset':  ('int', None),
    },
    'pio_uart': {
        'baudrate': ('int', None),
    },
    'wifi': {
        'ssid':     ('str', None),
        'password': ('password', None),
    },
    'ntp': {
        'enable':      ('bool', None),
        'server':      ('str', None),
        'tz_offset_h': ('int', None),
        'timeout_ms':  ('int', None),
    },
}

# Order sections are shown in, even if the file doesn't contain them yet.
SECTION_ORDER = ['display', 'keyboard', 'emulator', 'disk', 'profile',
                 'joystick', 'beep', 'touch', 'pio_uart', 'wifi', 'ntp']

_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_KV_RE = re.compile(r'^(\s*)([\w.]+)(\s*=\s*)([^;#]*)(.*)$')
_COMMENTED_KV_RE = re.compile(r'^(\s*)[;#]\s*([\w.]+)(\s*=\s*)([^;#]*)(.*)$')


# ─── Data Model ───────────────────────────────────────────────────────────────

class IniDocument:
    """A pb1000.ini file, edited line-by-line so untouched lines (comments,
    blank lines, commented-out examples) survive a save byte-for-byte."""

    def __init__(self):
        self.lines = []          # list[str], no trailing newline
        self.filepath = ''
        self.dirty = False
        # (section, key) -> {'active': line_idx or None, 'shadow': line_idx or None}
        self.entries = {}
        self.sections_seen = []  # section names in file order

    # ── Loading ──────────────────────────────────────────────────────────────

    def load(self, path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
        self.lines = text.splitlines()
        self.filepath = path
        self.dirty = False
        self._reindex()

    def new_from_scratch(self):
        self.lines = []
        self.filepath = ''
        self.dirty = False
        self._reindex()

    def _reindex(self):
        self.entries = {}
        self.sections_seen = []
        section = None
        for i, line in enumerate(self.lines):
            m = _SECTION_RE.match(line)
            if m:
                section = m.group(1).strip().lower()
                if section not in self.sections_seen:
                    self.sections_seen.append(section)
                continue
            if section is None:
                continue
            m = _KV_RE.match(line)
            if m:
                key = m.group(2).strip().lower()
                slot = self.entries.setdefault((section, key), {'active': None, 'shadow': None})
                slot['active'] = i
                continue
            m = _COMMENTED_KV_RE.match(line)
            if m:
                key = m.group(2).strip().lower()
                slot = self.entries.setdefault((section, key), {'active': None, 'shadow': None})
                slot['shadow'] = i  # last commented example wins if there are several

    # ── Reading ──────────────────────────────────────────────────────────────

    def is_active(self, section, key):
        slot = self.entries.get((section, key))
        return bool(slot and slot['active'] is not None)

    def has_shadow(self, section, key):
        slot = self.entries.get((section, key))
        return bool(slot and slot['shadow'] is not None)

    def get_value(self, section, key):
        slot = self.entries.get((section, key))
        if not slot or slot['active'] is None:
            return None
        m = _KV_RE.match(self.lines[slot['active']])
        return m.group(4).strip() if m else None

    def get_shadow_value(self, section, key):
        """Value written in the commented-out example line, if any (a hint of
        the documented alternative/default, not necessarily what's active)."""
        slot = self.entries.get((section, key))
        if not slot or slot['shadow'] is None:
            return None
        m = _COMMENTED_KV_RE.match(self.lines[slot['shadow']])
        return m.group(4).strip() if m else None

    def hint_for(self, section, key):
        """Comment lines immediately above the key's line (active or shadow),
        used as a short help tooltip."""
        slot = self.entries.get((section, key))
        if not slot:
            return ''
        idx = slot['active'] if slot['active'] is not None else slot['shadow']
        if idx is None:
            return ''
        out = []
        i = idx - 1
        while i >= 0:
            s = self.lines[i].strip()
            if not s or not (s.startswith(';') or s.startswith('#')):
                break
            if _COMMENTED_KV_RE.match(self.lines[i]) and i != slot['shadow']:
                # A commented-out example for a *different* key, not a
                # descriptive comment paragraph — stop here, don't include it.
                break
            out.append(s.lstrip(';#').strip())
            i -= 1
        out.reverse()
        return '\n'.join(out)

    # ── Editing ──────────────────────────────────────────────────────────────

    def set_value(self, section, key, value):
        """Update the value of an already-active key in place, preserving
        the line's original key/`=` spacing and any trailing inline comment."""
        slot = self.entries[(section, key)]
        idx = slot['active']
        m = _KV_RE.match(self.lines[idx])
        prefix, val, suffix = m.group(1) + m.group(2) + m.group(3), m.group(4), m.group(5)
        trailing_ws = val[len(val.rstrip()):]
        self.lines[idx] = prefix + str(value) + trailing_ws + suffix
        self.dirty = True

    def activate(self, section, key, value):
        """Turn a key on: uncomment its shadow line if one exists, otherwise
        append a fresh `key = value` line at the end of the section."""
        slot = self.entries.setdefault((section, key), {'active': None, 'shadow': None})
        if slot['shadow'] is not None:
            idx = slot['shadow']
            m = _COMMENTED_KV_RE.match(self.lines[idx])
            key_part, eq, val, suffix = m.group(2), m.group(3), m.group(4), m.group(5)
            trailing_ws = val[len(val.rstrip()):]
            self.lines[idx] = key_part + eq + str(value) + trailing_ws + suffix
            slot['active'] = idx
        else:
            insert_at = self._section_end(section)
            self.lines.insert(insert_at, f'{key} = {value}')
            self._reindex()  # line indices shifted; cheapest correct fix
        self.dirty = True

    def deactivate(self, section, key):
        """Turn a key off: comment out its active line rather than deleting it."""
        slot = self.entries[(section, key)]
        idx = slot['active']
        line = self.lines[idx]
        stripped = line.lstrip()
        leading_ws = line[:len(line) - len(stripped)]
        self.lines[idx] = leading_ws + '; ' + stripped
        slot['active'] = None
        self.dirty = True

    def _section_end(self, section):
        """Index to insert a new line at the end of `section`'s block.
        Creates the section (appended at EOF) if it doesn't exist yet."""
        section_start = None
        for i, line in enumerate(self.lines):
            m = _SECTION_RE.match(line)
            if m and m.group(1).strip().lower() == section:
                section_start = i
                break
        if section_start is None:
            if self.lines and self.lines[-1].strip() != '':
                self.lines.append('')
            self.lines.append(f'[{section}]')
            self.sections_seen.append(section)
            return len(self.lines)
        for i in range(section_start + 1, len(self.lines)):
            if _SECTION_RE.match(self.lines[i]):
                return i
        return len(self.lines)

    def add_custom_key(self, section, key, value):
        key = key.strip().lower()
        if not key:
            raise ValueError('Key name cannot be empty')
        if (section, key) in self.entries and self.entries[(section, key)]['active'] is not None:
            raise ValueError(f'{key} already exists in [{section}]')
        self.activate(section, key, value)

    # ── Saving ───────────────────────────────────────────────────────────────

    def serialize(self):
        return '\n'.join(self.lines) + '\n'

    def save(self, path):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(self.serialize())
        self.filepath = path
        self.dirty = False


# ─── Tooltip helper ───────────────────────────────────────────────────────────

class Tooltip:
    """Minimal hover tooltip for a single widget."""

    def __init__(self, widget, text_fn):
        self.widget = widget
        self.text_fn = text_fn
        self.tip = None
        widget.bind('<Enter>', self._show)
        widget.bind('<Leave>', self._hide)

    def _show(self, _event=None):
        text = self.text_fn()
        if not text or self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{x}+{y}')
        lbl = tk.Label(self.tip, text=text, justify='left', background='#ffffe0',
                       relief='solid', borderwidth=1, font=('TkDefaultFont', 9),
                       padx=6, pady=3, wraplength=420)
        lbl.pack()

    def _hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


# ─── Field row widget ─────────────────────────────────────────────────────────

class FieldRow(ttk.Frame):
    """One editable setting: [enabled checkbox] label [value widget]."""

    def __init__(self, parent, doc, section, key, kind, extra, dirty_cb):
        super().__init__(parent)
        self.doc = doc
        self.section = section
        self.key = key
        self.kind = kind
        self.extra = extra
        self.dirty_cb = dirty_cb
        self._building = True

        active = doc.is_active(section, key)
        raw_value = doc.get_value(section, key)
        if raw_value is None:
            raw_value = doc.get_shadow_value(section, key) or self._default_for_kind()

        self.enabled_var = tk.BooleanVar(value=active)
        chk = ttk.Checkbutton(self, variable=self.enabled_var, command=self._on_toggle,
                               width=2)
        chk.grid(row=0, column=0, padx=(0, 4))

        lbl = ttk.Label(self, text=key, width=22, anchor='w')
        lbl.grid(row=0, column=1, sticky='w')
        Tooltip(lbl, lambda: doc.hint_for(section, key))

        self.value_widget, self.value_get = self._make_value_widget(raw_value)
        self.value_widget.grid(row=0, column=2, sticky='w', padx=(4, 0))

        if kind == 'password':
            self._show_var = tk.BooleanVar(value=False)
            show_chk = ttk.Checkbutton(self, text='show', variable=self._show_var,
                                        command=self._toggle_show)
            show_chk.grid(row=0, column=3, padx=(6, 0))

        self._set_enabled_state(active)
        self._building = False

    def _default_for_kind(self):
        if self.kind == 'bool':
            return 'false'
        if self.kind == 'choice' and self.extra:
            return self.extra[0]
        if self.kind in ('int', 'float'):
            return '0'
        return ''

    def _make_value_widget(self, raw_value):
        if self.kind == 'bool':
            var = tk.StringVar(value='true' if raw_value.lower() in BOOL_TRUE else 'false')
            w = ttk.Checkbutton(self, variable=var, onvalue='true', offvalue='false',
                                 command=self._on_change)
            return w, (lambda: var.get())
        if self.kind == 'choice':
            var = tk.StringVar(value=raw_value)
            w = ttk.Combobox(self, textvariable=var, values=self.extra, width=14,
                              state='readonly')
            w.bind('<<ComboboxSelected>>', lambda _e: self._on_change())
            return w, (lambda: var.get())
        var = tk.StringVar(value=raw_value)
        show = '*' if self.kind == 'password' else ''
        w = ttk.Entry(self, textvariable=var, width=24, show=show)
        var.trace_add('write', lambda *_: self._on_change())
        return w, (lambda: var.get())

    def _toggle_show(self):
        self.value_widget.configure(show='' if self._show_var.get() else '*')

    def _set_enabled_state(self, on):
        state = 'normal' if on else 'disabled'
        try:
            self.value_widget.configure(state=('readonly' if (on and self.kind == 'choice')
                                                 else state))
        except tk.TclError:
            pass

    def _validate(self, value):
        if self.kind == 'int':
            try:
                n = int(value)
            except ValueError:
                return False, 'must be an integer'
            if self.extra and not (self.extra[0] <= n <= self.extra[1]):
                return False, f'must be between {self.extra[0]} and {self.extra[1]}'
        elif self.kind == 'float':
            try:
                float(value)
            except ValueError:
                return False, 'must be a number'
        return True, ''

    def _on_toggle(self):
        if self._building:
            return
        on = self.enabled_var.get()
        self._set_enabled_state(on)
        if on:
            self.doc.activate(self.section, self.key, self.value_get())
        else:
            if self.doc.is_active(self.section, self.key):
                self.doc.deactivate(self.section, self.key)
        self.dirty_cb()

    def _on_change(self):
        if self._building or not self.enabled_var.get():
            return
        value = self.value_get()
        ok, _msg = self._validate(value)
        if not ok:
            return  # leave the underlying line untouched until it's valid
        if not self.doc.is_active(self.section, self.key):
            self.doc.activate(self.section, self.key, value)
        else:
            self.doc.set_value(self.section, self.key, value)
        self.dirty_cb()


# ─── Main application ─────────────────────────────────────────────────────────

class IniEditorApp(tk.Tk):
    def __init__(self, initial_file=''):
        super().__init__()
        self.title('PB-1000 pb1000.ini Editor')
        self.geometry('720x560')
        self.doc = IniDocument()
        self._build_menu()

        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(self, textvariable=self.status_var, anchor='w',
                  relief='sunken').pack(side='bottom', fill='x')

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=6, pady=6)

        self.protocol('WM_DELETE_WINDOW', self._on_close)

        path = initial_file or (DEFAULT_INI if os.path.exists(DEFAULT_INI) else '')
        if path:
            self._load(path)
        else:
            self.doc.new_from_scratch()
            self._rebuild_tabs()
        self._update_title()

    # ── Menu ─────────────────────────────────────────────────────────────────

    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label='Open...', command=self._open, accelerator='Ctrl+O')
        filemenu.add_command(label='Reload', command=self._reload)
        filemenu.add_separator()
        filemenu.add_command(label='Save', command=self._save, accelerator='Ctrl+S')
        filemenu.add_command(label='Save As...', command=self._save_as)
        filemenu.add_separator()
        filemenu.add_command(label='Exit', command=self._on_close)
        menubar.add_cascade(label='File', menu=filemenu)

        toolsmenu = tk.Menu(menubar, tearoff=0)
        toolsmenu.add_command(label='View Raw pb1000.ini...', command=self._view_raw)
        toolsmenu.add_separator()
        toolsmenu.add_command(label='Send to Pico (mpremote cp)', command=self._mpremote_cp)
        menubar.add_cascade(label='Tools', menu=toolsmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label='About', command=self._about)
        menubar.add_cascade(label='Help', menu=helpmenu)
        self.config(menu=menubar)

        self.bind_all('<Control-o>', lambda _e: self._open())
        self.bind_all('<Control-s>', lambda _e: self._save())

    # ── Tabs ─────────────────────────────────────────────────────────────────

    def _rebuild_tabs(self):
        for tab in self.notebook.tabs():
            self.notebook.forget(tab)

        sections = list(SECTION_ORDER)
        for s in self.doc.sections_seen:
            if s not in sections:
                sections.append(s)

        for section in sections:
            frame = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(frame, text=section)
            self._build_section_tab(frame, section)

    def _build_section_tab(self, frame, section):
        keys = list(SCHEMA.get(section, {}).keys())
        # Include any keys present in the file but not in our schema, as
        # plain string fields, so nothing in an existing file is hidden.
        for (sec, key) in self.doc.entries:
            if sec == section and key not in keys:
                keys.append(key)

        for row_i, key in enumerate(keys):
            kind, extra = SCHEMA.get(section, {}).get(key, ('str', None))
            row = FieldRow(frame, self.doc, section, key, kind, extra, self._mark_dirty)
            row.grid(row=row_i, column=0, sticky='w', pady=2)

        sep_row = len(keys)
        ttk.Separator(frame, orient='horizontal').grid(
            row=sep_row, column=0, sticky='ew', pady=8)
        self._build_add_key_row(frame, section, sep_row + 1)

    def _build_add_key_row(self, frame, section, row_i):
        add_frame = ttk.Frame(frame)
        add_frame.grid(row=row_i, column=0, sticky='w')
        ttk.Label(add_frame, text='Add key:').grid(row=0, column=0, padx=(0, 4))
        key_var = tk.StringVar()
        val_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=key_var, width=18).grid(row=0, column=1)
        ttk.Label(add_frame, text='=').grid(row=0, column=2, padx=4)
        ttk.Entry(add_frame, textvariable=val_var, width=18).grid(row=0, column=3)

        def _add():
            try:
                self.doc.add_custom_key(section, key_var.get(), val_var.get())
            except ValueError as e:
                messagebox.showerror('Add key', str(e))
                return
            self._mark_dirty()
            self._rebuild_tabs()

        ttk.Button(add_frame, text='Add', command=_add).grid(row=0, column=4, padx=(6, 0))

    # ── File operations ──────────────────────────────────────────────────────

    def _mark_dirty(self):
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self.doc.filepath) if self.doc.filepath else '(new file)'
        dirty = ' *' if self.doc.dirty else ''
        self.title(f'PB-1000 pb1000.ini Editor — {name}{dirty}')

    def _set_status(self, text):
        self.status_var.set(text)

    def _load(self, path):
        try:
            self.doc.load(path)
        except Exception as e:
            messagebox.showerror('Open Error', str(e))
            return
        self._rebuild_tabs()
        self._update_title()
        self._set_status(f'Opened: {path}')

    def _open(self):
        if self.doc.dirty and not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title='Open pb1000.ini',
            filetypes=[('INI files', '*.ini'), ('All files', '*.*')],
            initialdir=os.path.dirname(self.doc.filepath or DEFAULT_INI),
        )
        if path:
            self._load(path)

    def _reload(self):
        if not self.doc.filepath:
            return
        if self.doc.dirty and not self._confirm_discard():
            return
        self._load(self.doc.filepath)

    def _save(self):
        if not self.doc.filepath:
            self._save_as()
            return
        try:
            self.doc.save(self.doc.filepath)
            self._update_title()
            self._set_status(f'Saved: {self.doc.filepath}')
        except Exception as e:
            messagebox.showerror('Save Error', str(e))

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title='Save pb1000.ini',
            defaultextension='.ini',
            filetypes=[('INI files', '*.ini')],
            initialdir=os.path.dirname(self.doc.filepath or DEFAULT_INI),
            initialfile='pb1000.ini',
        )
        if not path:
            return
        try:
            self.doc.save(path)
            self._update_title()
            self._set_status(f'Saved as: {path}')
        except Exception as e:
            messagebox.showerror('Save Error', str(e))

    # ── Tools ────────────────────────────────────────────────────────────────

    def _view_raw(self):
        """Show the exact bytes that Save would write, in a read-only window."""
        win = tk.Toplevel(self)
        name = os.path.basename(self.doc.filepath) if self.doc.filepath else '(new file)'
        win.title(f'Raw content — {name}')
        win.geometry('700x560')
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)

        text = tk.Text(win, wrap='none', font=('TkFixedFont', 10), undo=False)
        vsb = ttk.Scrollbar(win, orient='vertical', command=text.yview)
        hsb = ttk.Scrollbar(win, orient='horizontal', command=text.xview)
        text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        text.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        text.insert('1.0', self.doc.serialize())
        text.configure(state='disabled')
        text.focus_set()

    def _mpremote_cp(self):
        """Copy the on-disk pb1000.ini to the connected Pico's filesystem root
        via `mpremote cp <file> :`. Saves first if there are unsaved edits,
        since mpremote reads from disk, not from the in-memory document."""
        if not self.doc.filepath:
            messagebox.showerror('Send to Pico', 'ファイルが保存されていません。先に Save してください。')
            return

        if self.doc.dirty:
            if not messagebox.askyesno(
                    'Send to Pico',
                    '未保存の変更があります。保存してから Pico へ送信しますか？'):
                return
            self._save()
            if self.doc.dirty:
                return  # save failed or was cancelled

        mpremote = shutil.which('mpremote')
        if mpremote is None:
            messagebox.showerror('Send to Pico', 'mpremote が見つかりません。PATH を確認してください。')
            return

        self._set_status('mpremote cp を実行中...')
        self.config(cursor='watch')
        self.update_idletasks()
        try:
            result = subprocess.run(
                [mpremote, 'cp', self.doc.filepath, ':'],
                capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            self.config(cursor='')
            self._set_status('mpremote cp: タイムアウト')
            messagebox.showerror(
                'Send to Pico',
                'mpremote cp がタイムアウトしました。デバイスの接続を確認してください。')
            return
        except OSError as e:
            self.config(cursor='')
            self._set_status('mpremote cp: 失敗')
            messagebox.showerror('Send to Pico', str(e))
            return
        self.config(cursor='')

        output = ((result.stdout or '') + (result.stderr or '')).strip()
        if result.returncode == 0:
            self._set_status(f'Pico へ送信しました: {self.doc.filepath}')
            messagebox.showinfo(
                'Send to Pico',
                'pb1000.ini を Pico へコピーしました。\n\n' + output)
        else:
            self._set_status('mpremote cp: エラー')
            messagebox.showerror(
                'Send to Pico',
                f'mpremote cp が失敗しました (code {result.returncode})\n\n{output}')

    # ── Misc ─────────────────────────────────────────────────────────────────

    def _confirm_discard(self):
        return messagebox.askyesno('Unsaved changes', 'Discard unsaved changes?')

    def _on_close(self):
        if self.doc.dirty and not self._confirm_discard():
            return
        self.destroy()

    def _about(self):
        messagebox.showinfo(
            'About',
            'PB-1000 pb1000.ini Editor\n\n'
            'Edits pb1000.ini for the PB-1000 emulator.\n\n'
            'Load order on Pico (low -> high priority):\n'
            '  /pb1000.ini\n'
            '  /sd/pb1000.ini\n'
            '  <profile>/pb1000.ini\n\n'
            'Unchecking a setting comments it out (kept, not deleted);\n'
            'checking it uncomments the existing example line or adds\n'
            'a new one. Only touched lines change on save — everything\n'
            'else in the file is preserved as-is.\n\n'
            'Tools menu:\n'
            '  View Raw pb1000.ini... — shows the exact file content.\n'
            '  Send to Pico (mpremote cp) — saves if needed, then runs\n'
            '  `mpremote cp pb1000.ini :` to copy it to the device.',
        )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    initial = sys.argv[1] if len(sys.argv) > 1 else ''
    app = IniEditorApp(initial)
    app.mainloop()
