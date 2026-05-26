#!/usr/bin/env python3
"""
PB-1000 Keymap Editor
GUI tool for editing keymap.json files used by the PB-1000 emulator.

Usage:
    python keymap_editor.py [keymap.json]
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import os
import sys
from copy import deepcopy

# ─── Constants ────────────────────────────────────────────────────────────────

HID_NAMES = {
    0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F',
    0x0A: 'G', 0x0B: 'H', 0x0C: 'I', 0x0D: 'J', 0x0E: 'K', 0x0F: 'L',
    0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P', 0x14: 'Q', 0x15: 'R',
    0x16: 'S', 0x17: 'T', 0x18: 'U', 0x19: 'V', 0x1A: 'W', 0x1B: 'X',
    0x1C: 'Y', 0x1D: 'Z',
    0x1E: '1/!', 0x1F: '2/@', 0x20: '3/#', 0x21: '4/$', 0x22: '5/%',
    0x23: '6/^', 0x24: '7/&', 0x25: '8/*', 0x26: '9/(', 0x27: '0/)',
    0x28: 'Enter', 0x29: 'Escape', 0x2A: 'Backspace', 0x2B: 'Tab',
    0x2C: 'Space', 0x2D: '-/_', 0x2E: '=/+', 0x2F: '[/{', 0x30: ']/}',
    0x31: '\\|', 0x32: '\\|', 0x33: ';/:', 0x34: '\'/"', 0x35: '`/~',
    0x36: ',/<', 0x37: './>',  0x38: '/?',
    0x39: 'CapsLk',
    0x3A: 'F1',  0x3B: 'F2',  0x3C: 'F3',  0x3D: 'F4',
    0x3E: 'F5',  0x3F: 'F6',  0x40: 'F7',  0x41: 'F8',
    0x42: 'F9',  0x43: 'F10', 0x44: 'F11', 0x45: 'F12',
    0x46: 'PrintSc', 0x47: 'ScrollLk', 0x48: 'Pause',
    0x49: 'Insert', 0x4A: 'Home',   0x4B: 'PageUp',
    0x4C: 'Delete', 0x4D: 'End',    0x4E: 'PageDown',
    0x4F: 'Right',  0x50: 'Left',   0x51: 'Down', 0x52: 'Up',
    0x53: 'NumLk',  0x65: 'App/Menu',
    0x87: 'Int1(¥)', 0x88: 'Int2', 0x89: 'Int3(¥)', 0x8A: 'Int4',
    0xE0: 'L-Ctrl',  0xE1: 'L-Shift', 0xE2: 'L-Alt',  0xE3: 'L-GUI',
    0xE4: 'R-Ctrl',  0xE5: 'R-Shift', 0xE6: 'R-Alt',  0xE7: 'R-GUI',
}
MOD_FLAGS = [(1, 'Shift'), (2, 'Alt'), (4, 'Ctrl'), (8, 'GUI')]

DEFAULT_JSON = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mp', 'keymap.json')
)


def hid_name(sc: int) -> str:
    return HID_NAMES.get(sc, f'0x{sc:02X}')


def mod_str(mod: int) -> str:
    if mod == 0:
        return '(none)'
    return '+'.join(name for bit, name in MOD_FLAGS if mod & bit)


def coords_str(coords: list) -> str:
    return '  '.join(f'({r},{k})' for r, k in coords)


# ─── Data Model ───────────────────────────────────────────────────────────────

class KeymapData:
    def __init__(self):
        self.usb_map: dict = {}   # sc(int) → (row, ki, label)
        self.adv_map: dict = {}   # (sc, mod) → ([list of (row,ki)], label)
        self.filepath: str = ''
        self.dirty: bool = False

    def load(self, path: str):
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        usb: dict = {}
        for sc_str, v in raw.get('usb_map', {}).items():
            sc = int(sc_str, 16)
            usb[sc] = (v['row'], v['ki'], v['label'])
        adv: dict = {}
        for key_str, v in raw.get('adv_map', {}).items():
            parts = key_str.split(',', 1)
            sc = int(parts[0], 16)
            mod = int(parts[1])
            adv[(sc, mod)] = ([tuple(c) for c in v['coords']], v['label'])
        self.usb_map = usb
        self.adv_map = adv
        self.filepath = path
        self.dirty = False

    def save(self, path: str):
        usb_out = {}
        for sc in sorted(self.usb_map):
            row, ki, label = self.usb_map[sc]
            usb_out[f'0x{sc:02X}'] = {'row': row, 'ki': ki, 'label': label}
        adv_out = {}
        for (sc, mod) in sorted(self.adv_map):
            coords, label = self.adv_map[(sc, mod)]
            adv_out[f'0x{sc:02X},{mod}'] = {'coords': [list(c) for c in coords], 'label': label}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'usb_map': usb_out, 'adv_map': adv_out}, f,
                      indent=2, ensure_ascii=False)
        self.filepath = path
        self.dirty = False


# ─── Edit Dialogs ─────────────────────────────────────────────────────────────

class BaseEditDialog(tk.Toplevel):
    """Edit dialog for a single USB_MAP entry."""

    def __init__(self, parent, sc=None, row=1, ki=1, label='', title='Edit Base Key'):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky='nsew')

        # SC
        ttk.Label(frm, text='Scancode (hex):').grid(row=0, column=0, sticky='e', padx=4, pady=4)
        self._sc_var = tk.StringVar(value=f'0x{sc:02X}' if sc is not None else '')
        sc_entry = ttk.Entry(frm, textvariable=self._sc_var, width=8)
        sc_entry.grid(row=0, column=1, sticky='w', padx=4)
        self._sc_hint = ttk.Label(frm, text=hid_name(sc) if sc is not None else '', foreground='gray')
        self._sc_hint.grid(row=0, column=2, sticky='w', padx=4)
        self._sc_var.trace_add('write', self._on_sc_change)

        # Label
        ttk.Label(frm, text='Label:').grid(row=1, column=0, sticky='e', padx=4, pady=4)
        self._lbl_var = tk.StringVar(value=label)
        ttk.Entry(frm, textvariable=self._lbl_var, width=16).grid(row=1, column=1, columnspan=2, sticky='w', padx=4)

        # Row / KI
        ttk.Label(frm, text='PB-1000 Row (KO):').grid(row=2, column=0, sticky='e', padx=4, pady=4)
        self._row_var = tk.IntVar(value=row)
        ttk.Spinbox(frm, from_=1, to=12, textvariable=self._row_var, width=5).grid(row=2, column=1, sticky='w', padx=4)

        ttk.Label(frm, text='PB-1000 KI:').grid(row=3, column=0, sticky='e', padx=4, pady=4)
        self._ki_var = tk.IntVar(value=ki)
        ttk.Spinbox(frm, from_=1, to=12, textvariable=self._ki_var, width=5).grid(row=3, column=1, sticky='w', padx=4)

        # Buttons
        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=4, column=0, columnspan=3, pady=8)
        ttk.Button(btn_frm, text='OK', command=self._ok, width=8).pack(side='left', padx=4)
        ttk.Button(btn_frm, text='Cancel', command=self.destroy, width=8).pack(side='left', padx=4)

        self.bind('<Return>', lambda e: self._ok())
        self.bind('<Escape>', lambda e: self.destroy())
        self.wait_window()

    def _on_sc_change(self, *_):
        try:
            sc = int(self._sc_var.get(), 16)
            self._sc_hint.config(text=hid_name(sc))
        except ValueError:
            self._sc_hint.config(text='')

    def _ok(self):
        try:
            sc = int(self._sc_var.get(), 16)
        except ValueError:
            messagebox.showerror('Error', 'Scancode must be a hex number (e.g. 0x04)', parent=self)
            return
        label = self._lbl_var.get().strip()
        if not label:
            messagebox.showerror('Error', 'Label cannot be empty.', parent=self)
            return
        self.result = (sc, self._row_var.get(), self._ki_var.get(), label)
        self.destroy()


class AdvEditDialog(tk.Toplevel):
    """Edit dialog for a single ADV_MAP entry."""

    def __init__(self, parent, sc=None, mod=0, coords=None, label='', title='Edit Advanced Key'):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None
        if coords is None:
            coords = [(1, 1)]

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky='nsew')

        # SC
        ttk.Label(frm, text='Scancode (hex):').grid(row=0, column=0, sticky='e', padx=4, pady=4)
        self._sc_var = tk.StringVar(value=f'0x{sc:02X}' if sc is not None else '')
        ttk.Entry(frm, textvariable=self._sc_var, width=8).grid(row=0, column=1, sticky='w', padx=4)
        self._sc_hint = ttk.Label(frm, text=hid_name(sc) if sc is not None else '', foreground='gray')
        self._sc_hint.grid(row=0, column=2, sticky='w', padx=4)
        self._sc_var.trace_add('write', self._on_sc_change)

        # Modifier
        ttk.Label(frm, text='Modifier:').grid(row=1, column=0, sticky='ne', padx=4, pady=4)
        mod_frm = ttk.Frame(frm)
        mod_frm.grid(row=1, column=1, columnspan=2, sticky='w')
        self._mod_vars = []
        for bit, name in MOD_FLAGS:
            v = tk.BooleanVar(value=bool(mod & bit))
            self._mod_vars.append((bit, v))
            ttk.Checkbutton(mod_frm, text=name, variable=v).pack(side='left', padx=2)

        # Label
        ttk.Label(frm, text='Label:').grid(row=2, column=0, sticky='e', padx=4, pady=4)
        self._lbl_var = tk.StringVar(value=label)
        ttk.Entry(frm, textvariable=self._lbl_var, width=16).grid(row=2, column=1, columnspan=2, sticky='w', padx=4)

        # Coord list
        ttk.Label(frm, text='PB-1000 key sequence\n(press order):').grid(
            row=3, column=0, sticky='ne', padx=4, pady=4)
        coord_frm = ttk.Frame(frm)
        coord_frm.grid(row=3, column=1, columnspan=2, sticky='w')

        self._coord_list = tk.Listbox(coord_frm, height=5, width=16, selectmode='single')
        self._coord_list.pack(side='left')
        for r, k in coords:
            self._coord_list.insert('end', f'({r}, {k})')

        edit_frm = ttk.Frame(coord_frm)
        edit_frm.pack(side='left', padx=6, anchor='n')
        ttk.Label(edit_frm, text='Row:').grid(row=0, column=0, sticky='e')
        self._erow = tk.IntVar(value=1)
        ttk.Spinbox(edit_frm, from_=1, to=12, textvariable=self._erow, width=5).grid(row=0, column=1)
        ttk.Label(edit_frm, text='KI:').grid(row=1, column=0, sticky='e')
        self._eki = tk.IntVar(value=1)
        ttk.Spinbox(edit_frm, from_=1, to=12, textvariable=self._eki, width=5).grid(row=1, column=1)
        ttk.Button(edit_frm, text='Add', command=self._add_coord, width=6).grid(row=2, column=0, columnspan=2, pady=2)
        ttk.Button(edit_frm, text='Delete', command=self._del_coord, width=6).grid(row=3, column=0, columnspan=2, pady=2)
        ttk.Button(edit_frm, text='Up', command=lambda: self._move_coord(-1), width=6).grid(row=4, column=0, columnspan=2, pady=2)
        ttk.Button(edit_frm, text='Down', command=lambda: self._move_coord(1), width=6).grid(row=5, column=0, columnspan=2, pady=2)

        self._coord_list.bind('<<ListboxSelect>>', self._on_coord_select)

        # Buttons
        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=4, column=0, columnspan=3, pady=8)
        ttk.Button(btn_frm, text='OK', command=self._ok, width=8).pack(side='left', padx=4)
        ttk.Button(btn_frm, text='Cancel', command=self.destroy, width=8).pack(side='left', padx=4)

        self.bind('<Escape>', lambda e: self.destroy())
        self.wait_window()

    def _on_sc_change(self, *_):
        try:
            sc = int(self._sc_var.get(), 16)
            self._sc_hint.config(text=hid_name(sc))
        except ValueError:
            self._sc_hint.config(text='')

    def _on_coord_select(self, _=None):
        idx = self._coord_list.curselection()
        if not idx:
            return
        text = self._coord_list.get(idx[0])
        r, k = [int(x.strip(' ()')) for x in text.split(',')]
        self._erow.set(r)
        self._eki.set(k)

    def _add_coord(self):
        self._coord_list.insert('end', f'({self._erow.get()}, {self._eki.get()})')

    def _del_coord(self):
        idx = self._coord_list.curselection()
        if idx:
            self._coord_list.delete(idx[0])

    def _move_coord(self, direction):
        idx = self._coord_list.curselection()
        if not idx:
            return
        i = idx[0]
        j = i + direction
        if j < 0 or j >= self._coord_list.size():
            return
        a, b = self._coord_list.get(i), self._coord_list.get(j)
        self._coord_list.delete(i)
        self._coord_list.insert(i, b)
        self._coord_list.delete(j)
        self._coord_list.insert(j, a)
        self._coord_list.selection_set(j)

    def _ok(self):
        try:
            sc = int(self._sc_var.get(), 16)
        except ValueError:
            messagebox.showerror('Error', 'Scancode must be hex (e.g. 0xE2)', parent=self)
            return
        mod = sum(bit for bit, v in self._mod_vars if v.get())
        label = self._lbl_var.get().strip()
        if not label:
            messagebox.showerror('Error', 'Label cannot be empty.', parent=self)
            return
        if self._coord_list.size() == 0:
            messagebox.showerror('Error', 'At least one coord is required.', parent=self)
            return
        coords = []
        for i in range(self._coord_list.size()):
            text = self._coord_list.get(i)
            r, k = [int(x.strip(' ()')) for x in text.split(',')]
            coords.append((r, k))
        self.result = (sc, mod, coords, label)
        self.destroy()


# ─── Tab Widgets ──────────────────────────────────────────────────────────────

class BaseMapTab(ttk.Frame):
    COLS = ('sc', 'hid', 'label', 'row', 'ki')
    HEADS = ('SC', 'HID Key', 'Label', 'Row (KO)', 'KI')
    WIDTHS = (60, 110, 120, 75, 75)

    def __init__(self, parent, data: KeymapData, status_cb):
        super().__init__(parent)
        self.data = data
        self.status = status_cb
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add('write', lambda *_: self.refresh())
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=4)
        ttk.Label(top, text='Filter:').pack(side='left')
        ttk.Entry(top, textvariable=self._filter_var, width=20).pack(side='left', padx=4)
        ttk.Button(top, text='Clear', command=lambda: self._filter_var.set('')).pack(side='left')

        tree_frm = ttk.Frame(self)
        tree_frm.pack(fill='both', expand=True, padx=6)
        self.tv = ttk.Treeview(tree_frm, columns=self.COLS, show='headings', selectmode='browse')
        for col, head, w in zip(self.COLS, self.HEADS, self.WIDTHS):
            self.tv.heading(col, text=head, command=lambda c=col: self._sort(c))
            self.tv.column(col, width=w, anchor='center')
        vsb = ttk.Scrollbar(tree_frm, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tv.bind('<Double-1>', lambda e: self._edit())

        btn_frm = ttk.Frame(self)
        btn_frm.pack(fill='x', padx=6, pady=4)
        ttk.Button(btn_frm, text='Add',       command=self._add,  width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Edit',      command=self._edit, width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Delete',    command=self._del,  width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Duplicate', command=self._dup,  width=8).pack(side='left', padx=2)

        self._sort_col = 'sc'
        self._sort_rev = False
        self.refresh()

    def refresh(self):
        flt = self._filter_var.get().lower()
        self.tv.delete(*self.tv.get_children())
        rows = []
        for sc, (row, ki, label) in self.data.usb_map.items():
            if flt and flt not in f'{sc:02x}{label.lower()}{hid_name(sc).lower()}':
                continue
            rows.append((sc, hid_name(sc), label, row, ki))
        rows.sort(key=lambda r: r[self.COLS.index(self._sort_col)],
                  reverse=self._sort_rev)
        for r in rows:
            self.tv.insert('', 'end', iid=str(r[0]),
                           values=(f'0x{r[0]:02X}', r[1], r[2], r[3], r[4]))

    def _sort(self, col):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self.refresh()

    def _selected_sc(self):
        sel = self.tv.selection()
        return int(sel[0]) if sel else None

    def _add(self):
        dlg = BaseEditDialog(self, title='Add Base Key')
        if dlg.result is None:
            return
        sc, row, ki, label = dlg.result
        if sc in self.data.usb_map:
            messagebox.showwarning('Duplicate', f'0x{sc:02X} already exists. Use Edit to modify.', parent=self)
            return
        self.data.usb_map[sc] = (row, ki, label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(str(sc))
        self.status(f'Added 0x{sc:02X} → {label}')

    def _edit(self):
        sc = self._selected_sc()
        if sc is None:
            return
        row, ki, label = self.data.usb_map[sc]
        dlg = BaseEditDialog(self, sc=sc, row=row, ki=ki, label=label)
        if dlg.result is None:
            return
        new_sc, row, ki, label = dlg.result
        if new_sc != sc:
            del self.data.usb_map[sc]
        self.data.usb_map[new_sc] = (row, ki, label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(str(new_sc))
        self.status(f'Updated 0x{new_sc:02X} → {label}')

    def _del(self):
        sc = self._selected_sc()
        if sc is None:
            return
        if not messagebox.askyesno('Delete', f'Delete 0x{sc:02X} ({self.data.usb_map[sc][2]})?', parent=self):
            return
        del self.data.usb_map[sc]
        self.data.dirty = True
        self.refresh()
        self.status(f'Deleted 0x{sc:02X}')

    def _dup(self):
        sc = self._selected_sc()
        if sc is None:
            return
        row, ki, label = self.data.usb_map[sc]
        dlg = BaseEditDialog(self, row=row, ki=ki, label=label + ' (copy)', title='Duplicate Base Key')
        if dlg.result is None:
            return
        new_sc, row, ki, label = dlg.result
        self.data.usb_map[new_sc] = (row, ki, label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(str(new_sc))
        self.status(f'Duplicated → 0x{new_sc:02X}')


class AdvMapTab(ttk.Frame):
    COLS = ('sc', 'hid', 'mod', 'modname', 'label', 'coords')
    HEADS = ('SC', 'HID Key', 'Mod', 'Modifier', 'Label', 'PB-1000 Keys')
    WIDTHS = (60, 100, 40, 100, 100, 200)

    def __init__(self, parent, data: KeymapData, status_cb):
        super().__init__(parent)
        self.data = data
        self.status = status_cb
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add('write', lambda *_: self.refresh())
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=4)
        ttk.Label(top, text='Filter:').pack(side='left')
        ttk.Entry(top, textvariable=self._filter_var, width=20).pack(side='left', padx=4)
        ttk.Button(top, text='Clear', command=lambda: self._filter_var.set('')).pack(side='left')

        tree_frm = ttk.Frame(self)
        tree_frm.pack(fill='both', expand=True, padx=6)
        self.tv = ttk.Treeview(tree_frm, columns=self.COLS, show='headings', selectmode='browse')
        for col, head, w in zip(self.COLS, self.HEADS, self.WIDTHS):
            self.tv.heading(col, text=head, command=lambda c=col: self._sort(c))
            self.tv.column(col, width=w, anchor='center' if col in ('sc','mod') else 'w')
        vsb = ttk.Scrollbar(tree_frm, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tv.bind('<Double-1>', lambda e: self._edit())

        btn_frm = ttk.Frame(self)
        btn_frm.pack(fill='x', padx=6, pady=4)
        ttk.Button(btn_frm, text='Add',       command=self._add,  width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Edit',      command=self._edit, width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Delete',    command=self._del,  width=8).pack(side='left', padx=2)
        ttk.Button(btn_frm, text='Duplicate', command=self._dup,  width=8).pack(side='left', padx=2)

        self._sort_col = 'sc'
        self._sort_rev = False
        self.refresh()

    def _row_iid(self, sc, mod):
        return f'{sc},{mod}'

    def refresh(self):
        flt = self._filter_var.get().lower()
        self.tv.delete(*self.tv.get_children())
        rows = []
        for (sc, mod), (coords, label) in self.data.adv_map.items():
            mname = mod_str(mod)
            cs = coords_str(coords)
            if flt and flt not in f'{sc:02x}{hid_name(sc).lower()}{label.lower()}{mname.lower()}':
                continue
            rows.append((sc, hid_name(sc), mod, mname, label, cs))
        ci = self.COLS.index(self._sort_col)
        rows.sort(key=lambda r: r[ci], reverse=self._sort_rev)
        for r in rows:
            iid = self._row_iid(r[0], r[2])
            self.tv.insert('', 'end', iid=iid,
                           values=(f'0x{r[0]:02X}', r[1], r[2], r[3], r[4], r[5]))

    def _sort(self, col):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self.refresh()

    def _selected_key(self):
        sel = self.tv.selection()
        if not sel:
            return None, None
        sc_str, mod_str_val = sel[0].split(',')
        return int(sc_str), int(mod_str_val)

    def _add(self):
        dlg = AdvEditDialog(self, title='Add Advanced Key')
        if dlg.result is None:
            return
        sc, mod, coords, label = dlg.result
        key = (sc, mod)
        if key in self.data.adv_map:
            messagebox.showwarning('Duplicate', f'0x{sc:02X}, mod={mod} already exists.', parent=self)
            return
        self.data.adv_map[key] = (coords, label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(self._row_iid(sc, mod))
        self.status(f'Added 0x{sc:02X} mod={mod} → {label}')

    def _edit(self):
        sc, mod = self._selected_key()
        if sc is None:
            return
        coords, label = self.data.adv_map[(sc, mod)]
        dlg = AdvEditDialog(self, sc=sc, mod=mod, coords=list(coords), label=label)
        if dlg.result is None:
            return
        new_sc, new_mod, new_coords, new_label = dlg.result
        old_key = (sc, mod)
        new_key = (new_sc, new_mod)
        if new_key != old_key:
            del self.data.adv_map[old_key]
        self.data.adv_map[new_key] = (new_coords, new_label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(self._row_iid(new_sc, new_mod))
        self.status(f'Updated 0x{new_sc:02X} mod={new_mod} → {new_label}')

    def _del(self):
        sc, mod = self._selected_key()
        if sc is None:
            return
        label = self.data.adv_map[(sc, mod)][1]
        if not messagebox.askyesno('Delete', f'Delete 0x{sc:02X} mod={mod} ({label})?', parent=self):
            return
        del self.data.adv_map[(sc, mod)]
        self.data.dirty = True
        self.refresh()
        self.status(f'Deleted 0x{sc:02X} mod={mod}')

    def _dup(self):
        sc, mod = self._selected_key()
        if sc is None:
            return
        coords, label = self.data.adv_map[(sc, mod)]
        dlg = AdvEditDialog(self, sc=sc, mod=mod, coords=list(coords), label=label, title='Duplicate Advanced Key')
        if dlg.result is None:
            return
        new_sc, new_mod, new_coords, new_label = dlg.result
        self.data.adv_map[(new_sc, new_mod)] = (new_coords, new_label)
        self.data.dirty = True
        self.refresh()
        self.tv.selection_set(self._row_iid(new_sc, new_mod))
        self.status(f'Duplicated → 0x{new_sc:02X} mod={new_mod}')


# ─── Main Application ─────────────────────────────────────────────────────────

class KeymapEditorApp(tk.Tk):
    def __init__(self, initial_file: str = ''):
        super().__init__()
        self.title('PB-1000 Keymap Editor')
        self.geometry('820x520')
        self.data = KeymapData()
        self._build_menu()
        self._build_notebook()
        self._build_statusbar()
        if initial_file and os.path.isfile(initial_file):
            self._do_open(initial_file)
        elif os.path.isfile(DEFAULT_JSON):
            self._do_open(DEFAULT_JSON)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── Menu ─────────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = tk.Menu(self)
        self.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='File', menu=fm)
        fm.add_command(label='Open…',       accelerator='Ctrl+O', command=self._open)
        fm.add_command(label='Save',         accelerator='Ctrl+S', command=self._save)
        fm.add_command(label='Save As…',     accelerator='Ctrl+Shift+S', command=self._save_as)
        fm.add_separator()
        fm.add_command(label='Export defaults as JSON…', command=self._export_defaults)
        fm.add_separator()
        fm.add_command(label='Exit', command=self._on_close)

        self.bind_all('<Control-o>', lambda e: self._open())
        self.bind_all('<Control-s>', lambda e: self._save())
        self.bind_all('<Control-S>', lambda e: self._save_as())

        hm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='Help', menu=hm)
        hm.add_command(label='About', command=self._about)

    # ── Notebook ─────────────────────────────────────────────────────────────

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=4, pady=4)
        self.base_tab = BaseMapTab(self.nb, self.data, self._set_status)
        self.adv_tab  = AdvMapTab (self.nb, self.data, self._set_status)
        self.nb.add(self.base_tab, text='Base Map  (USB_MAP)')
        self.nb.add(self.adv_tab,  text='Advanced Map  (ADV_MAP)')

    # ── Status bar ───────────────────────────────────────────────────────────

    def _build_statusbar(self):
        self._status_var = tk.StringVar(value='Ready')
        ttk.Label(self, textvariable=self._status_var, relief='sunken', anchor='w').pack(
            fill='x', side='bottom', padx=2, pady=1)

    def _set_status(self, msg: str):
        self._status_var.set(msg)

    # ── Title ────────────────────────────────────────────────────────────────

    def _update_title(self):
        name = os.path.basename(self.data.filepath) if self.data.filepath else '(untitled)'
        dirty = ' *' if self.data.dirty else ''
        self.title(f'PB-1000 Keymap Editor — {name}{dirty}')

    # ── File operations ──────────────────────────────────────────────────────

    def _open(self):
        if self.data.dirty and not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title='Open keymap.json',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            initialdir=os.path.dirname(self.data.filepath or DEFAULT_JSON),
        )
        if path:
            self._do_open(path)

    def _do_open(self, path: str):
        try:
            self.data.load(path)
            self.base_tab.refresh()
            self.adv_tab.refresh()
            self._update_title()
            self._set_status(
                f'Loaded {os.path.basename(path)} — '
                f'{len(self.data.usb_map)} base, {len(self.data.adv_map)} adv entries')
        except Exception as e:
            messagebox.showerror('Open Error', str(e))

    def _save(self):
        if not self.data.filepath:
            self._save_as()
            return
        try:
            self.data.save(self.data.filepath)
            self._update_title()
            self._set_status(f'Saved: {self.data.filepath}')
        except Exception as e:
            messagebox.showerror('Save Error', str(e))

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title='Save keymap.json',
            defaultextension='.json',
            filetypes=[('JSON files', '*.json')],
            initialdir=os.path.dirname(self.data.filepath or DEFAULT_JSON),
            initialfile='keymap.json',
        )
        if not path:
            return
        try:
            self.data.save(path)
            self._update_title()
            self._set_status(f'Saved as: {path}')
        except Exception as e:
            messagebox.showerror('Save Error', str(e))

    def _export_defaults(self):
        """Load and save the built-in defaults from keymap.py."""
        path = filedialog.asksaveasfilename(
            title='Export built-in defaults',
            defaultextension='.json',
            filetypes=[('JSON files', '*.json')],
            initialfile='keymap_defaults.json',
        )
        if not path:
            return
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mp'))
            import importlib, keymap as km_mod
            importlib.reload(km_mod)
            usb_out = {}
            for sc, (coord, label) in km_mod.USB_MAP.items():
                usb_out[f'0x{sc:02X}'] = {'row': coord[0], 'ki': coord[1], 'label': label}
            adv_out = {}
            for (sc, mod), (coords, label) in km_mod.ADV_MAP.items():
                adv_out[f'0x{sc:02X},{mod}'] = {'coords': [list(c) for c in coords], 'label': label}
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'usb_map': usb_out, 'adv_map': adv_out}, f, indent=2, ensure_ascii=False)
            self._set_status(f'Defaults exported to {os.path.basename(path)}')
        except Exception as e:
            messagebox.showerror('Export Error', str(e))

    # ── Misc ─────────────────────────────────────────────────────────────────

    def _confirm_discard(self) -> bool:
        return messagebox.askyesno('Unsaved changes', 'Discard unsaved changes?')

    def _on_close(self):
        if self.data.dirty and not self._confirm_discard():
            return
        self.destroy()

    def _about(self):
        messagebox.showinfo(
            'About',
            'PB-1000 Keymap Editor\n\n'
            'Edits keymap.json for the PB-1000 emulator.\n\n'
            'JSON search order on Pico:\n'
            '  /sd/roms/keymap.json\n'
            '  /sd/keymap.json\n'
            '  /roms/keymap.json\n\n'
            'Modifier bits: Shift=1, Alt=2, Ctrl=4, GUI=8\n'
            'PB-1000 coords: (Row=KO line, KI=KI line)',
        )


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    initial = sys.argv[1] if len(sys.argv) > 1 else ''
    app = KeymapEditorApp(initial)
    app.mainloop()
