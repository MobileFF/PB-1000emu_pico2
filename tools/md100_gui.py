#!/usr/bin/env python3
"""
md100_gui.py — GUI for Casio MD-100 Floppy Disk Image Utility

Requires md100.py in the same directory.
Uses Python standard library only (tkinter).

Usage:
    python md100_gui.py [image.img]
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os, sys, io, re
from typing import Optional, List, Tuple

# ── Import from md100.py ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from md100 import (
    MD100Disk, DirEntry,
    read_file_data, _write_data_blocks, delete_file_chain,
    find_files, name_to_11bytes, pc_name_to_md100,
    _put_one, _get_one, _dest_path,
    pc_to_disk_bytes, disk_to_pc_bytes,
    print_basic, print_text, print_machine, print_random,
    TYPE_M, TYPE_B, TYPE_S, TYPE_R, TYPE_C,
    TYPE_BY_LETTER, LETTER_BY_TYPE, EXT_TYPE,
    TOKENS, MAX_DIR_ENTRIES, DEF_UNUSED,
    DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, BLOCK_SIZE,
    _ESC_NONE, EOF_CHAR,
)

# Optional drag-and-drop support
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False


# ─── BASIC Tokenizer ──────────────────────────────────────────────────────────

def _build_kw_list() -> List[Tuple[str, Tuple[int, int]]]:
    """Build sorted keyword list (longest first) from TOKENS table."""
    kw_map: dict = {}
    for prefix_idx, table in enumerate(TOKENS):
        pb = prefix_idx + 4
        for j, kw in enumerate(table):
            if kw is not None:
                kw_map[kw] = (pb, 0x40 + j)
    return sorted(kw_map.items(), key=lambda x: -len(x[0]))

_KW_LIST = _build_kw_list()

# Keywords after which bare integers are encoded as line-number references
_GOTO_KWS = frozenset({
    'GOTO ', 'GOSUB ', 'RESUME ', 'RESTORE ', 'RUN ',
    ' THEN ', 'ELSE ',
})


def _tokenize_stmt(stmt: str) -> bytes:
    """Convert one BASIC statement (after the line number) to token bytes."""
    out = bytearray()
    i = 0
    n = len(stmt)
    after_goto = False

    while i < n:
        c = stmt[i]

        # ── String literal ── pass through unchanged
        if c == '"':
            out.append(0x22)
            i += 1
            while i < n and stmt[i] != '"':
                b = ord(stmt[i])
                out.append(b if b < 0x80 else 0x3F)
                i += 1
            if i < n:
                out.append(0x22)
                i += 1
            after_goto = False
            continue

        # ── Apostrophe comment ── rest of line as ASCII
        if c == "'":
            out.append(0x02)
            i += 1
            while i < n:
                b = ord(stmt[i])
                out.append(b if b < 0x80 else 0x3F)
                i += 1
            break

        # ── Statement separator
        if c == ':':
            out.append(0x01)
            i += 1
            after_goto = False
            continue

        # ── Line-number reference (after GOTO / GOSUB / etc.)
        if after_goto:
            if c == ' ':
                i += 1
                continue
            if c.isdigit():
                j = i
                while j < n and stmt[j].isdigit():
                    j += 1
                num = int(stmt[i:j])
                out += bytes([0x03, num & 0xFF, (num >> 8) & 0xFF])
                i = j
                # Skip trailing spaces; keep after_goto for ON…GOTO commas
                while i < n and stmt[i] == ' ':
                    i += 1
                if i < n and stmt[i] == ',':
                    out.append(0x2C)   # comma stays as ASCII
                    i += 1
                else:
                    after_goto = False
                continue
            after_goto = False   # not a digit — cancel goto mode

        # ── Keyword match (longest first, case-insensitive)
        upper_rest = stmt[i:].upper()
        matched = False
        for kw, (pb, tb) in _KW_LIST:
            match_len = 0
            if upper_rest.startswith(kw):
                match_len = len(kw)
            elif kw.endswith(' '):
                # Also match keyword without trailing space when at a word
                # boundary (end-of-statement, operator, open-paren, etc.)
                kw_core = kw.rstrip(' ')
                if upper_rest.startswith(kw_core):
                    j = len(kw_core)
                    nxt = upper_rest[j] if j < len(upper_rest) else ''
                    if not nxt or (not nxt.isalnum() and nxt not in '$_#'):
                        match_len = j
            if match_len == 0:
                continue
            out.append(pb)
            out.append(tb)
            i += match_len
            # After REM: copy rest of line as literal ASCII
            if kw.startswith('REM'):
                while i < n:
                    b = ord(stmt[i])
                    out.append(b if b < 0x80 else 0x3F)
                    i += 1
            elif kw in _GOTO_KWS:
                after_goto = True
            matched = True
            break

        if not matched:
            b = ord(c)
            out.append(b if b < 0x80 else 0x3F)
            i += 1

    return bytes(out)


def tokenize_basic(source: str) -> bytes:
    """
    Convert text BASIC source to PB-1000 binary BASIC (Type B).

    Input:
        10 PRINT "HELLO"
        20 FOR I=1 TO 10
        30 NEXT I

    Binary layout:
        [256 bytes header, all 0xFF]
        For each line:
            [LEN][line_nr_lo][line_nr_hi][token bytes...][0x00]
        LEN = total record size including the LEN byte itself.
    """
    header = bytes([0xFF] * 256)
    body = bytearray()

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)\s*(.*)', line)
        if not m:
            continue   # skip non-BASIC text (e.g. blank comments)
        line_nr = int(m.group(1))
        if line_nr > 65535:
            continue
        toks = _tokenize_stmt(m.group(2))
        # Record format: [LEN][lo][hi][toks][0x00]
        # LEN = total bytes in record (including LEN itself)
        #      = 1(LEN) + 1(lo) + 1(hi) + len(toks) + 1(0x00)
        LEN = len(toks) + 4
        body.append(LEN)
        body.append(line_nr & 0xFF)
        body.append((line_nr >> 8) & 0xFF)
        body.extend(toks)
        body.append(0x00)

    return header + bytes(body)


# ─── Disk helpers ─────────────────────────────────────────────────────────────

def put_raw_to_disk(disk: MD100Disk, raw: bytes,
                    name_8: bytes, ext_3: bytes, type_byte: int) -> Tuple[str, int]:
    """
    Write raw bytes to the disk image.
    Handles overwrite if a file with the same name already exists.
    Returns (display_name, block_count).
    """
    name_ext = name_8 + ext_3
    found_idx = free_idx = -1
    for i in range(MAX_DIR_ENTRIES):
        e = disk.get_entry(i)
        if not e.is_free and e.name_ext == name_ext:
            found_idx = i
            break
        if e.is_free and free_idx == -1:
            free_idx = i

    if found_idx >= 0:
        delete_file_chain(disk, found_idx, disk.get_entry(found_idx))
        slot = found_idx
    elif free_idx >= 0:
        slot = free_idx
    else:
        raise RuntimeError("ディレクトリが満杯です")

    needed = (len(raw) // BLOCK_SIZE) + 1
    if needed > disk.disk_free():
        raise RuntimeError("ディスク容量が不足しています")

    first_blk = _write_data_blocks(disk, raw)
    entry = DirEntry(type_byte, name_8, ext_3, DEF_UNUSED, first_blk, 0)
    disk.set_entry(slot, entry)
    disk.flush()
    _, blks = disk.file_blocks_and_size(first_blk)
    return entry.display_name(), blks


def hexdump(data: bytes, width: int = 16) -> str:
    """Return hex+ASCII dump of data."""
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part  = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
        lines.append(f'{offset:06X}: {hex_part:<{width * 3}}  {ascii_part}')
    return '\n'.join(lines)


def decode_file_for_view(disk: MD100Disk, entry: DirEntry) -> str:
    """Decode a disk file's content to a displayable string."""
    raw = read_file_data(disk, entry)
    buf = io.StringIO()
    t = entry.type_byte
    try:
        if t in (TYPE_S, TYPE_C):
            print_text(raw, _ESC_NONE, buf)
        elif t == TYPE_B:
            print_basic(raw, _ESC_NONE, buf)
        elif t == TYPE_R:
            print_random(raw, buf)
        else:
            buf.write(hexdump(raw))
    except Exception as exc:
        buf.write(f"[デコードエラー: {exc}]\n\n")
        buf.write(hexdump(raw))
    return buf.getvalue()


# ─── Dialogs ──────────────────────────────────────────────────────────────────

class BasicModeDialog(simpledialog.Dialog):
    """Ask the user how to store .bas files."""

    def __init__(self, parent, filenames: List[str]):
        self.filenames = filenames
        self.result: Optional[str] = None
        super().__init__(parent, title="BASICファイルの格納方法")

    def body(self, frame: tk.Frame) -> None:
        names = ', '.join(os.path.basename(f) for f in self.filenames[:3])
        if len(self.filenames) > 3:
            names += f' ... ({len(self.filenames)}件)'
        tk.Label(frame, text=f"対象ファイル: {names}", anchor='w',
                 wraplength=380).pack(fill='x', pady=(0, 8))

        self._var = tk.StringVar(value='S')
        opts = [
            ('S', 'テキスト形式のまま格納  (Type S)\n'
                  '   PC上で書いたソーステキストをそのまま保存します'),
            ('B', 'BASICバイナリに変換して格納  (Type B)\n'
                  '   キーワードをトークン化し、PB-1000が直接実行できる形式にします'),
        ]
        for val, lbl in opts:
            tk.Radiobutton(frame, text=lbl, variable=self._var, value=val,
                           justify='left', anchor='w').pack(fill='x', pady=2)

    def apply(self) -> None:
        self.result = self._var.get()


class NewImageDialog(simpledialog.Dialog):
    """Collect parameters for a new disk image."""

    def __init__(self, parent):
        self.result: Optional[Tuple[str, int]] = None
        super().__init__(parent, title="新しいディスクイメージを作成")

    def body(self, frame: tk.Frame) -> tk.Widget:
        tk.Label(frame, text="ブロック数 (5〜512, デフォルト 320):").grid(
            row=0, column=0, sticky='w', padx=4, pady=4)
        self._size_var = tk.IntVar(value=DEFAULT_SIZE)
        e = tk.Spinbox(frame, from_=5, to=512, textvariable=self._size_var, width=6)
        e.grid(row=0, column=1, sticky='w', padx=4, pady=4)
        return e

    def validate(self) -> bool:
        try:
            v = self._size_var.get()
        except tk.TclError:
            messagebox.showerror("入力エラー", "ブロック数は整数で入力してください", parent=self)
            return False
        if not (MIN_SIZE <= v <= MAX_SIZE):
            messagebox.showerror("範囲外", f"ブロック数は {MIN_SIZE}〜{MAX_SIZE} の範囲で入力してください",
                                 parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = self._size_var.get()


class ViewWindow(tk.Toplevel):
    """Popup window for displaying file contents."""

    def __init__(self, parent, title: str, content: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("700x500")
        self.minsize(400, 300)

        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(side='top', fill='x', padx=4, pady=2)
        ttk.Button(bar, text="閉じる", command=self.destroy).pack(side='right')
        ttk.Button(bar, text="すべてコピー", command=self._copy_all).pack(side='right', padx=4)

        # Text area
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self._text = tk.Text(frame, wrap='none', font=('Courier New', 10),
                             undo=False, state='normal')
        vsb = ttk.Scrollbar(frame, orient='vertical',   command=self._text.yview)
        hsb = ttk.Scrollbar(frame, orient='horizontal', command=self._text.xview)
        self._text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side='bottom', fill='x')
        vsb.pack(side='right',  fill='y')
        self._text.pack(fill='both', expand=True)

        # Insert content (limit to 256 KB to avoid UI freeze)
        MAX = 256 * 1024
        display = content if len(content) <= MAX else content[:MAX] + '\n[... 表示省略 ...]'
        self._text.insert('1.0', display)
        self._text.configure(state='disabled')
        self.transient(parent)
        self.focus_set()

    def _copy_all(self):
        self.clipboard_clear()
        self.clipboard_append(self._text.get('1.0', 'end-1c'))


# ─── Main Application ─────────────────────────────────────────────────────────

_BaseClass = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk  # type: ignore[misc]


class MD100App(_BaseClass):
    """Main window for the MD-100 Disk Image GUI."""

    def __init__(self, initial_image: Optional[str] = None):
        super().__init__()
        self.title("MD-100 Disk Image Utility")
        self.geometry("800x480")
        self.minsize(600, 320)

        self._disk: Optional[MD100Disk] = None
        self._disk_path: Optional[str] = None

        self._build_menu()
        self._build_statusbar()   # bottom first so toolbar/list pack correctly
        self._build_toolbar()
        self._build_filelist()
        self._update_buttons()

        self.bind('<Delete>', lambda e: self.delete_files())
        self.bind('<F2>',     lambda e: self.rename_file())
        self.bind('<F5>',     lambda e: self.refresh())

        if initial_image:
            self.after(100, lambda: self._load_disk(initial_image))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        # File menu
        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label="開く...          Ctrl+O", command=self.open_image)
        m.add_command(label="新規作成...      Ctrl+N", command=self.new_image)
        m.add_command(label="閉じる",          command=self._close_disk)
        m.add_separator()
        m.add_command(label="終了",            command=self.quit)
        menubar.add_cascade(label="ファイル", menu=m)
        self._menu_file = m

        # Disk menu
        m2 = tk.Menu(menubar, tearoff=0)
        m2.add_command(label="更新              F5",    command=self.refresh)
        m2.add_separator()
        m2.add_command(label="PCから追加...",           command=self.put_files)
        m2.add_command(label="PCに取り出し...",         command=self.get_files)
        m2.add_separator()
        m2.add_command(label="削除             Delete", command=self.delete_files)
        m2.add_command(label="名前変更         F2",     command=self.rename_file)
        m2.add_separator()
        m2.add_command(label="内容表示         Enter",  command=self.view_file)
        menubar.add_cascade(label="操作", menu=m2)
        self._menu_disk = m2

        self.config(menu=menubar)
        self.bind('<Control-o>', lambda e: self.open_image())
        self.bind('<Control-n>', lambda e: self.new_image())

    def _build_statusbar(self) -> None:
        self._status_var = tk.StringVar(value="ディスクイメージを開いてください")
        bar = ttk.Label(self, textvariable=self._status_var,
                        relief='sunken', anchor='w', padding=(4, 2))
        bar.pack(side='bottom', fill='x')

    def _build_toolbar(self) -> None:
        tb = ttk.Frame(self, relief='raised', padding=(2, 2))
        tb.pack(side='top', fill='x')

        def btn(text, cmd, ref=None):
            b = ttk.Button(tb, text=text, command=cmd, width=len(text) + 1)
            b.pack(side='left', padx=2)
            return b

        btn("開く",    self.open_image)
        btn("新規",    self.new_image)
        btn("更新",    self.refresh)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=4, pady=2)
        self._btn_put  = btn("PCから追加",  self.put_files)
        self._btn_get  = btn("PCに取出し",  self.get_files)
        self._btn_del  = btn("削除",        self.delete_files)
        self._btn_ren  = btn("名前変更",    self.rename_file)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=4, pady=2)
        self._btn_view = btn("内容表示",    self.view_file)

    def _build_filelist(self) -> None:
        cols = ('name', 'ext', 'type', 'size', 'blocks', 'protect', 'start')
        headers = {
            'name':    'ファイル名',
            'ext':     '拡張子',
            'type':    '種別',
            'size':    'サイズ(B)',
            'blocks':  'ブロック',
            'protect': '保護',
            'start':   '先頭Blk',
        }
        widths = {
            'name': 110, 'ext': 55, 'type': 50,
            'size': 80,  'blocks': 65, 'protect': 50, 'start': 65,
        }

        container = ttk.Frame(self)
        container.pack(fill='both', expand=True)

        self._tree = ttk.Treeview(container, columns=cols, show='headings',
                                  selectmode='extended')
        for col in cols:
            self._tree.heading(col, text=headers[col],
                               command=lambda c=col: self._sort_by(c))
            anchor = 'e' if col in ('size', 'blocks', 'start') else 'w'
            self._tree.column(col, width=widths[col], anchor=anchor, minwidth=40)

        vsb = ttk.Scrollbar(container, orient='vertical',   command=self._tree.yview)
        hsb = ttk.Scrollbar(container, orient='horizontal', command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side='bottom', fill='x')
        vsb.pack(side='right',  fill='y')
        self._tree.pack(fill='both', expand=True)

        self._tree.bind('<<TreeviewSelect>>', self._on_select)
        self._tree.bind('<Double-1>',         lambda e: self.view_file())
        self._tree.bind('<Return>',           lambda e: self.view_file())
        self._tree.bind('<Button-3>',         self._show_context_menu)

        # Context menu
        self._ctx = tk.Menu(self, tearoff=0)
        self._ctx.add_command(label="内容表示",     command=self.view_file)
        self._ctx.add_separator()
        self._ctx.add_command(label="PCに取り出し", command=self.get_files)
        self._ctx.add_command(label="削除",         command=self.delete_files)
        self._ctx.add_command(label="名前変更",     command=self.rename_file)

        # Drag-and-drop
        if _DND_AVAILABLE:
            self._tree.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self._tree.dnd_bind('<<Drop>>', self._on_dnd_drop)

        self._sort_col = 'name'
        self._sort_rev = False

    # ── Sort ─────────────────────────────────────────────────────────────────

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._populate_tree(sort_col=col, reverse=self._sort_rev)

    # ── Disk operations ───────────────────────────────────────────────────────

    def _load_disk(self, path: str) -> None:
        try:
            disk = MD100Disk(path)
        except Exception as e:
            messagebox.showerror("エラー", f"ディスクイメージを開けません:\n{e}")
            return
        self._disk = disk
        self._disk_path = path
        self.title(f"MD-100 Disk Image Utility — {os.path.basename(path)}")
        self._populate_tree()
        self._update_status()
        self._update_buttons()

    def _close_disk(self) -> None:
        if self._disk:
            self._disk.flush()
        self._disk = None
        self._disk_path = None
        self.title("MD-100 Disk Image Utility")
        self._tree.delete(*self._tree.get_children())
        self._status_var.set("ディスクイメージを開いてください")
        self._update_buttons()

    def _populate_tree(self, sort_col: str = None, reverse: bool = False) -> None:
        if not self._disk:
            return
        self._tree.delete(*self._tree.get_children())

        rows = []
        for idx, entry in self._disk.all_entries():
            if entry.is_free:
                continue
            sz, blks = self._disk.file_blocks_and_size(entry.start_block)
            rows.append((idx, entry, sz, blks))

        sc = sort_col or self._sort_col
        key_fn = {
            'name':    lambda r: r[1].name.rstrip(b' ').decode('latin-1').upper(),
            'ext':     lambda r: r[1].ext.rstrip(b' ').decode('latin-1').upper(),
            'type':    lambda r: r[1].type_str(),
            'size':    lambda r: r[2],
            'blocks':  lambda r: r[3],
            'protect': lambda r: r[1].protect,
            'start':   lambda r: r[1].start_block,
        }.get(sc, lambda r: r[1].name)
        rows.sort(key=key_fn, reverse=reverse)

        for idx, entry, sz, blks in rows:
            name = entry.name.rstrip(b' ').decode('latin-1')
            ext  = entry.ext.rstrip(b' ').decode('latin-1')
            self._tree.insert('', 'end', iid=str(idx), values=(
                name, ext, entry.type_str(), sz, blks,
                '●' if entry.protect else '', entry.start_block,
            ))

    def _selected_entries(self) -> List[Tuple[int, DirEntry]]:
        """Return list of (dir_idx, DirEntry) for selected tree rows."""
        result = []
        for iid in self._tree.selection():
            idx = int(iid)
            result.append((idx, self._disk.get_entry(idx)))
        return result

    def _update_status(self) -> None:
        if not self._disk:
            self._status_var.set("ディスクイメージを開いてください")
            return
        free_blk  = self._disk.disk_free()
        total_blk = self._disk.num_blocks - 4   # data blocks
        used_blk  = total_blk - free_blk
        n_files   = sum(1 for i in range(MAX_DIR_ENTRIES)
                        if not self._disk.get_entry(i).is_free)
        self._status_var.set(
            f"{self._disk_path}   |   "
            f"ファイル: {n_files}件   "
            f"使用: {used_blk}ブロック   "
            f"空き: {free_blk}ブロック ({free_blk * BLOCK_SIZE:,} bytes)"
        )

    def _update_buttons(self) -> None:
        disk_open = self._disk is not None
        selected  = disk_open and bool(self._tree.selection())

        state_disk = 'normal' if disk_open else 'disabled'
        state_sel  = 'normal' if selected  else 'disabled'

        self._btn_put['state']  = state_disk
        self._btn_get['state']  = state_sel
        self._btn_del['state']  = state_sel
        self._btn_ren['state']  = ('normal' if selected and len(self._tree.selection()) == 1
                                   else 'disabled')
        self._btn_view['state'] = ('normal' if selected and len(self._tree.selection()) == 1
                                   else 'disabled')

    def _on_select(self, _event=None) -> None:
        self._update_buttons()

    def _show_context_menu(self, event) -> None:
        iid = self._tree.identify_row(event.y)
        if iid:
            if iid not in self._tree.selection():
                self._tree.selection_set(iid)
            self._ctx.tk_popup(event.x_root, event.y_root)

    # ── Commands ──────────────────────────────────────────────────────────────

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="ディスクイメージを開く",
            filetypes=[("Disk images", "*.img *.dsk *.bin"), ("All files", "*.*")],
        )
        if path:
            self._load_disk(path)

    def new_image(self) -> None:
        dialog = NewImageDialog(self)
        if dialog.result is None:
            return
        size = dialog.result
        path = filedialog.asksaveasfilename(
            title="新しいディスクイメージを保存",
            defaultextension=".img",
            filetypes=[("Disk images", "*.img *.dsk"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            MD100Disk(path, create=True, size=size)
        except Exception as e:
            messagebox.showerror("エラー", f"ディスクイメージの作成に失敗しました:\n{e}")
            return
        self._load_disk(path)

    def refresh(self) -> None:
        if self._disk_path:
            self._load_disk(self._disk_path)

    # ── Put ───────────────────────────────────────────────────────────────────

    def put_files(self) -> None:
        if not self._disk:
            return
        paths = filedialog.askopenfilenames(
            title="ディスクに追加するファイルを選択",
            filetypes=[("All files", "*.*")],
        )
        if not paths:
            return
        self._do_put(list(paths))

    def _on_dnd_drop(self, event) -> None:
        """Handle drag-and-drop file drop (requires tkinterdnd2)."""
        if not self._disk:
            return
        # Parse the drop data (space/brace separated file paths)
        raw = event.data
        if raw.startswith('{'):
            paths = re.findall(r'\{([^}]*)\}', raw)
        else:
            paths = raw.split()
        if paths:
            self._do_put(paths)

    def _do_put(self, paths: List[str]) -> None:
        """Core put logic: determine mode for .bas files and write all files."""
        bas_paths = [p for p in paths
                     if os.path.splitext(p)[1].lower() == '.bas']

        bas_mode = 'S'   # default: store as text
        if bas_paths:
            dlg = BasicModeDialog(self, bas_paths)
            if dlg.result is None:
                return
            bas_mode = dlg.result

        errors = []
        written = []
        for path in paths:
            if self._disk_path and os.path.abspath(path) == os.path.abspath(self._disk_path):
                continue
            try:
                name, ts, blks = self._put_file(path, bas_mode)
                written.append(f"{name}  [{ts}]  {blks}ブロック")
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")

        self._disk.flush()
        self._populate_tree()
        self._update_status()

        if errors:
            messagebox.showerror("エラー",
                                 "一部のファイルで書き込みに失敗しました:\n" + "\n".join(errors))
        elif written:
            messagebox.showinfo("完了",
                                f"{len(written)} 件のファイルを書き込みました:\n" +
                                "\n".join(written))

    def _put_file(self, path: str, bas_mode: str) -> Tuple[str, str, int]:
        """
        Write one PC file to the disk image.
        Returns (md100_name, type_str, blocks).
        """
        ext = os.path.splitext(path)[1].lower()

        if ext == '.bas' and bas_mode == 'B':
            # Tokenize BASIC source → Type B
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
            binary = tokenize_basic(source)
            name_8, ext_3 = pc_name_to_md100(path, None, 'AS_IS')
            display, blks = put_raw_to_disk(self._disk, binary, name_8, ext_3, TYPE_B)
            return display, 'B', blks
        else:
            # Regular put (auto type/mode via _put_one)
            type_byte = 0   # auto-detect
            mode      = 'AUTO'
            if ext == '.bas':
                # Store as text TYPE_S instead of TYPE_B
                type_byte = TYPE_S
                mode      = 'ASCII'
            name, ts, blks = _put_one(
                self._disk, path, None, mode, _ESC_NONE, 'AS_IS',
                type_byte, 0, False,
            )
            return name, ts, blks

    # ── Get ───────────────────────────────────────────────────────────────────

    def get_files(self) -> None:
        if not self._disk:
            return
        selected = self._selected_entries()
        if not selected:
            return

        if len(selected) == 1:
            idx, entry = selected[0]
            default_name = entry.display_name('LOWER')
            dest = filedialog.asksaveasfilename(
                title="保存先を指定",
                initialfile=default_name,
                filetypes=[("All files", "*.*")],
            )
            if not dest:
                return
            dests = [dest]
        else:
            dest_dir = filedialog.askdirectory(title="取り出し先フォルダを選択")
            if not dest_dir:
                return
            dests = [dest_dir] * len(selected)

        errors = []
        saved  = []
        for (idx, entry), dest in zip(selected, dests):
            try:
                out_path = _get_one(self._disk, idx, entry, dest,
                                    'AUTO', _ESC_NONE, 'LOWER')
                saved.append(out_path)
            except Exception as e:
                errors.append(f"{entry.display_name()}: {e}")

        if errors:
            messagebox.showerror("エラー",
                                 "一部のファイルで取り出しに失敗しました:\n" + "\n".join(errors))
        elif saved:
            messagebox.showinfo("完了",
                                f"{len(saved)} 件のファイルを取り出しました:\n" +
                                "\n".join(saved))

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_files(self) -> None:
        if not self._disk:
            return
        selected = self._selected_entries()
        if not selected:
            return

        names = ", ".join(e.display_name() for _, e in selected)
        if not messagebox.askyesno(
                "削除の確認",
                f"以下のファイルを削除しますか？\n\n{names}",
                icon='warning'):
            return

        errors = []
        for idx, entry in selected:
            try:
                delete_file_chain(self._disk, idx, entry)
            except Exception as e:
                errors.append(f"{entry.display_name()}: {e}")

        self._disk.flush()
        self._populate_tree()
        self._update_status()
        self._update_buttons()

        if errors:
            messagebox.showerror("エラー",
                                 "一部のファイルで削除に失敗しました:\n" + "\n".join(errors))

    # ── Rename ────────────────────────────────────────────────────────────────

    def rename_file(self) -> None:
        if not self._disk:
            return
        selected = self._selected_entries()
        if len(selected) != 1:
            return
        idx, entry = selected[0]
        old_name = entry.display_name()

        new_name = simpledialog.askstring(
            "名前変更",
            f"新しいファイル名を入力してください (8.3形式):\n現在: {old_name}",
            initialvalue=old_name,
            parent=self,
        )
        if not new_name or new_name == old_name:
            return

        # Validate: no path separators, reasonable length
        new_name = new_name.strip()
        if any(c in new_name for c in r'\/:*?"<>|'):
            messagebox.showerror("エラー", "ファイル名に使用できない文字が含まれています")
            return

        try:
            new_8, new_3 = name_to_11bytes(new_name, 'AS_IS', entry)
            new_ne = new_8 + new_3
            # Check for duplicate
            for i, e in self._disk.all_entries():
                if not e.is_free and i != idx and e.name_ext == new_ne:
                    messagebox.showerror("エラー",
                                         f"同名のファイルが既に存在します: {e.display_name()}")
                    return
            entry.name = new_8
            entry.ext  = new_3
            self._disk.set_entry(idx, entry)
            self._disk.flush()
        except Exception as e:
            messagebox.showerror("エラー", f"名前変更に失敗しました:\n{e}")
            return

        self._populate_tree()
        self._update_status()

    # ── View ─────────────────────────────────────────────────────────────────

    def view_file(self) -> None:
        if not self._disk:
            return
        selected = self._selected_entries()
        if len(selected) != 1:
            return
        _, entry = selected[0]

        title = f"{entry.display_name()}  [{entry.type_str()}]  — 内容表示"
        content = decode_file_for_view(self._disk, entry)
        ViewWindow(self, title, content)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    app = MD100App(initial_image=initial)
    app.mainloop()


if __name__ == '__main__':
    main()
