#!/usr/bin/env python3
"""
PB-1000 Tools Launcher
Pick one of the PC-side GUI tools and launch it as its own process.

Each tool (md100_gui.py, keymap_editor.py, pb1000_ini_editor.py) is a
standalone Tk application, so this launcher spawns each one as a separate
subprocess rather than embedding it — you can have several open at once,
and a crash in one tool never takes the launcher (or the others) down.

Usage:
    python launcher.py
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

TOOLS = [
    {
        'name': 'MD-100 Disk Image Utility',
        'script': 'md100_gui.py',
        'description': 'Browse/edit MD-100 virtual floppy disk images (.img)',
        'file_label': 'Disk image (optional):',
        'filetypes': [('Disk images', '*.img'), ('All files', '*.*')],
    },
    {
        'name': 'Keymap Editor',
        'script': 'keymap_editor.py',
        'description': 'Edit keymap.json USB/advanced key mappings',
        'file_label': 'keymap.json (optional):',
        'filetypes': [('JSON files', '*.json'), ('All files', '*.*')],
    },
    {
        'name': 'pb1000.ini Editor',
        'script': 'pb1000_ini_editor.py',
        'description': 'Edit pb1000.ini emulator configuration',
        'file_label': 'pb1000.ini (optional):',
        'filetypes': [('INI files', '*.ini'), ('All files', '*.*')],
    },
]


class ToolRow(ttk.Frame):
    def __init__(self, parent, tool, status_cb):
        super().__init__(parent, padding=(0, 6))
        self.tool = tool
        self.status_cb = status_cb
        script_path = os.path.join(HERE, tool['script'])
        self.available = os.path.exists(script_path)

        header = ttk.Frame(self)
        header.pack(fill='x')
        name_lbl = ttk.Label(header, text=tool['name'], font=('TkDefaultFont', 11, 'bold'))
        name_lbl.pack(side='left')
        if not self.available:
            ttk.Label(header, text='(script not found)', foreground='red').pack(side='left', padx=(8, 0))

        ttk.Label(self, text=tool['description'], foreground='#555').pack(anchor='w')

        file_row = ttk.Frame(self)
        file_row.pack(fill='x', pady=(4, 0))
        ttk.Label(file_row, text=tool['file_label'], width=22, anchor='w').pack(side='left')
        self.path_var = tk.StringVar()
        ttk.Entry(file_row, textvariable=self.path_var, width=40).pack(side='left', padx=(0, 4))
        ttk.Button(file_row, text='Browse...', command=self._browse).pack(side='left', padx=(0, 4))
        launch_btn = ttk.Button(file_row, text='Launch', command=self._launch)
        launch_btn.pack(side='left', padx=(8, 0))
        if not self.available:
            launch_btn.state(['disabled'])

        ttk.Separator(self, orient='horizontal').pack(fill='x', pady=(8, 0))

    def _browse(self):
        path = filedialog.askopenfilename(
            title=f"Select file for {self.tool['name']}",
            filetypes=self.tool['filetypes'],
        )
        if path:
            self.path_var.set(path)

    def _launch(self):
        script_path = os.path.join(HERE, self.tool['script'])
        args = [sys.executable, script_path]
        chosen = self.path_var.get().strip()
        if chosen:
            args.append(chosen)
        try:
            subprocess.Popen(args)
            self.status_cb(f"Launched {self.tool['name']}")
        except Exception as e:
            messagebox.showerror('Launch Error', f"Couldn't launch {self.tool['name']}:\n{e}")


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PB-1000 Tools Launcher')
        self.resizable(False, False)

        container = ttk.Frame(self, padding=14)
        container.pack(fill='both', expand=True)

        ttk.Label(container, text='PB-1000 Emulator — PC-side Tools',
                  font=('TkDefaultFont', 13, 'bold')).pack(anchor='w', pady=(0, 4))
        ttk.Label(container, text='Choose a tool and optionally pick a file to open with it.',
                  foreground='#555').pack(anchor='w', pady=(0, 8))

        self.status_var = tk.StringVar(value='Ready')
        for tool in TOOLS:
            ToolRow(container, tool, self._set_status).pack(fill='x')

        ttk.Label(self, textvariable=self.status_var, anchor='w',
                  relief='sunken').pack(side='bottom', fill='x')

    def _set_status(self, text):
        self.status_var.set(text)


if __name__ == '__main__':
    app = LauncherApp()
    app.mainloop()
