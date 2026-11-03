#!/usr/bin/env python3
"""
device_files_gui.py — browse and transfer files on the emulator's SD card
from the PC, over the device's PC Link mode (mp/pc_link.py, protocol client
in pc_link.py next to this file).

On the device: press F2 at the boot-time profile picker. Then connect here,
either to its serial port or -- on a Pico 2 W with [wifi] ssid set -- over
WiFi: pick "WiFi: pb1000.local" (or type the IP address shown on the
device's PC Link screen) and enter the PIN shown there. The address of
the last few successful WiFi connections is remembered (in
~/.pb1000_device_files.json) and offered in the list.

Tabs:
  Disk Images  — .img files in /sd/disks, /sd and each profile folder.
                 "Edit in MD-100 Utility" downloads the image, opens it in
                 md100_gui.py, and offers to write it back when that window
                 is closed.
  Screenshots  — /sd/screenshots: PNG preview, download, delete.
  RAM Profiles — /sd/rams/<profile> folders: download, upload, delete.

Requires pyserial (pip install pyserial) for the serial port; WiFi needs
nothing extra. Uses tkinter for everything else.

Usage:
    python device_files_gui.py
"""

import base64
import collections
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pc_link
from pc_link import PCLink, LinkError

SCREENSHOT_DIR = "/sd/screenshots"
PROFILE_ROOT = "/sd/rams"
DISK_DIRS = ["/sd/disks", "/sd"]  # plus every profile folder, like mp/disk_select_ui.py
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".pb1000_device_files.json")
MAX_WIFI_HOSTS = 5


def _load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass  # only a convenience: the next session just starts without it


def _fmt_size(n):
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1024 / 1024)
    if n >= 1024:
        return "%.1f KB" % (n / 1024)
    return "%d B" % n


def _sha1(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PB-1000 Device Files (PC Link)")
        self.geometry("760x520")
        self.link = None
        self._busy = False
        self._pending = collections.deque()
        self._results = queue.Queue()

        self._build_connection_bar()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.disk_tab = DiskTab(self.tabs, self)
        self.shot_tab = ScreenshotTab(self.tabs, self)
        self.profile_tab = ProfileTab(self.tabs, self)
        self.tabs.add(self.disk_tab, text="Disk Images")
        self.tabs.add(self.shot_tab, text="Screenshots")
        self.tabs.add(self.profile_tab, text="RAM Profiles")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bottom, length=160, mode="determinate")
        self.progress.pack(side="right", padx=6, pady=2)
        self.status_var = tk.StringVar(value="Not connected. On the device, press F2 at the profile picker.")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w", relief="sunken").pack(
            side="left", fill="x", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_results)
        self._rescan_ports()
        if pc_link.serial is None:
            messagebox.showerror("pyserial missing",
                                 "This tool needs pyserial.\n\npip install pyserial")

    # ── connection ───────────────────────────────────────────────────────────

    def _build_connection_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Port / WiFi:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=34)
        self.port_box.pack(side="left", padx=4)
        ttk.Button(bar, text="Rescan", command=self._rescan_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=(8, 0))
        self.free_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.free_var, foreground="#555").pack(side="right")

    def _rescan_ports(self):
        ports = pc_link.find_ports()
        # Serial ports first, then WiFi: addresses that worked before (most
        # recent first), then the device's mDNS name. Any host/IP can also
        # be typed in by hand (pc_link.is_network_target()).
        hosts = [h for h in _load_settings().get("wifi_hosts", []) if isinstance(h, str)]
        if pc_link.DEFAULT_HOST not in hosts:
            hosts.append(pc_link.DEFAULT_HOST)
        self.port_box["values"] = ["%s  %s" % p for p in ports] + ["WiFi: " + h for h in hosts]
        if not self.port_var.get() and (ports or len(hosts) > 1):
            self.port_box.current(0)

    def _remember_wifi_host(self, host):
        data = _load_settings()
        hosts = [h for h in data.get("wifi_hosts", []) if isinstance(h, str) and h != host]
        data["wifi_hosts"] = ([host] + hosts)[:MAX_WIFI_HOSTS]
        _save_settings(data)
        self._rescan_ports()

    def _toggle_connect(self):
        if self.link is not None:
            self._disconnect(send_bye=True)
            return
        port = self.port_var.get().split("  ")[0].strip()
        if not port:
            messagebox.showwarning("No port", "Choose the device's serial port.")
            return

        if not pc_link.is_network_target(port):
            self._connect(port, None)
            return

        # WiFi: look the name up first (in the background -- an mDNS lookup
        # that fails can take a few seconds), so an unresolvable name is
        # reported before the PIN is asked for, not after.
        def ask_pin(host_port):
            pin = simpledialog.askstring("PIN", "PIN shown on the device's PC Link screen:",
                                         parent=self)
            if pin:
                self._connect(port, pin.strip(), host_port[0])
            else:
                self.status_var.set("Not connected.")

        self.run_task("Looking up %s..." % port,
                      lambda progress: pc_link.check_host(port), ask_pin, need_link=False)

    def _connect(self, port, pin, wifi_host=None):
        def work(progress):
            link = PCLink(port, pin=pin)
            return link, link.disk_free()

        def done(res):
            self.link, (total, free) = res
            self.connect_btn.configure(text="Finish")
            self.free_var.set("SD: %s free of %s" % (_fmt_size(free), _fmt_size(total)))
            self.status_var.set("Connected to %s" % port)
            if wifi_host is not None:
                self._remember_wifi_host(wifi_host)
            self.refresh_all()

        self.run_task("Connecting to %s..." % port, work, done, need_link=False)

    def _disconnect(self, send_bye):
        self._pending.clear()
        link, self.link = self.link, None
        if link is not None:
            if send_bye and not self._busy:
                try:
                    link.bye()
                except Exception:
                    pass
            link.close()
        self.connect_btn.configure(text="Connect")
        self.free_var.set("")
        self.status_var.set("Disconnected. The device is back at its profile picker." if send_bye
                            else "Disconnected.")

    def _on_close(self):
        if self.disk_tab.editing:
            if not messagebox.askyesno("Editing in progress",
                                       "A disk image is still open in the MD-100 Utility.\n"
                                       "Close anyway? (Its changes won't be written back.)"):
                return
        self._disconnect(send_bye=True)
        self.destroy()

    def refresh_all(self):
        self.disk_tab.refresh()
        self.shot_tab.refresh()
        self.profile_tab.refresh()

    # ── background tasks ─────────────────────────────────────────────────────
    #
    # Every device operation runs in a worker thread so the window stays
    # responsive during a transfer. Tk itself is only touched from the main
    # thread: workers post (callback, value) pairs to self._results, which
    # _poll_results() picks up. Tasks run one at a time, in the order they
    # were requested -- the serial link is a single request/response stream.

    def run_task(self, text, work, done=None, need_link=True):
        if need_link and self.link is None:
            messagebox.showinfo("Not connected", "Connect to the device first.")
            return
        self._pending.append((text, work, done))
        if not self._busy:
            self._start_next()

    def _start_next(self):
        if not self._pending:
            return
        text, work, done = self._pending.popleft()
        self._busy = True
        self.status_var.set(text)
        self.progress["value"] = 0

        def progress(done_n, total):
            self._results.put((self._set_progress, (done_n, total)))

        def runner():
            try:
                res = work(progress)
                self._results.put((self._task_done, (done, res, None)))
            except Exception as e:
                self._results.put((self._task_done, (done, None, e)))

        threading.Thread(target=runner, daemon=True).start()

    def _set_progress(self, args):
        done_n, total = args
        self.progress["value"] = 100.0 * done_n / total if total else 100

    def _task_done(self, args):
        done, res, err = args
        self.progress["value"] = 0
        if err is not None:
            self._busy = False
            self._pending.clear()
            self.status_var.set("Error: %s" % err)
            if self.link is not None and isinstance(err, LinkError) and \
                    ("no response" in str(err) or "timed out" in str(err) or
                     "connection closed" in str(err)):
                # The stream is out of sync (or the device left PC Link
                # mode); a fresh Connect re-syncs with HELLO.
                self._disconnect(send_bye=False)
            messagebox.showerror("Error", str(err))
            return
        self.status_var.set("Done")
        # Still marked busy while done() runs: anything it queues (connect's
        # refresh_all(), for one) must wait its turn instead of starting a
        # second worker on the same serial link right away.
        if done is not None:
            done(res)
        self._busy = False
        self._start_next()

    def _poll_results(self):
        try:
            while True:
                fn, arg = self._results.get_nowait()
                fn(arg)
        except queue.Empty:
            pass
        self.after(50, self._poll_results)


class _ListTab(ttk.Frame):
    """A tab with a file list (Treeview) on the left and buttons on the right."""

    columns = ("size",)

    def __init__(self, parent, app, select_mode="browse"):
        super().__init__(parent, padding=6)
        self.app = app
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(left, columns=self.columns, selectmode=select_mode)
        self.tree.heading("#0", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.column("size", width=90, anchor="e", stretch=False)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.buttons = ttk.Frame(self, padding=(8, 0, 0, 0))
        self.buttons.pack(side="left", fill="y")

    def button(self, text, command):
        ttk.Button(self.buttons, text=text, command=command, width=24).pack(fill="x", pady=2)

    def selected(self):
        return list(self.tree.selection())


# ── Disk Images ───────────────────────────────────────────────────────────────

class DiskTab(_ListTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.tree.heading("#0", text="Disk image (path on SD)")
        self.button("Edit in MD-100 Utility", self.edit)
        self.button("Download...", self.download)
        self.button("Upload...", self.upload)
        self.button("Delete", self.delete)
        self.button("Refresh", self.refresh)
        self.editing = None  # (remote path, local path, sha1 at download, Popen)

    def refresh(self):
        def work(progress):
            link = self.app.link
            dirs = list(DISK_DIRS)
            try:
                dirs += [PROFILE_ROOT + "/" + e.name for e in link.listdir(PROFILE_ROOT) if e.is_dir]
            except LinkError:
                pass
            found = []
            for d in dirs:
                try:
                    for e in link.listdir(d):
                        if not e.is_dir and e.name.lower().endswith(".img"):
                            found.append((d + "/" + e.name, e.size))
                except LinkError:
                    pass
            return found

        def done(found):
            self.tree.delete(*self.tree.get_children())
            for path, size in found:
                self.tree.insert("", "end", iid=path, text=path, values=(_fmt_size(size),))

        self.app.run_task("Listing disk images...", work, done)

    def _one(self):
        sel = self.selected()
        if not sel:
            messagebox.showinfo("Disk Images", "Select a disk image first.")
            return None
        return sel[0]

    def edit(self):
        remote = self._one()
        if remote is None:
            return
        if self.editing is not None:
            messagebox.showinfo("Disk Images", "Close the MD-100 Utility window that is already open first.")
            return
        local = os.path.join(tempfile.mkdtemp(prefix="pb1000_"), remote.rsplit("/", 1)[1])

        def work(progress):
            data = self.app.link.get(remote, progress)
            with open(local, "wb") as f:
                f.write(data)

        def done(_):
            before = _sha1(local)  # before launching, so no save can slip past it
            proc = subprocess.Popen([sys.executable, os.path.join(HERE, "md100_gui.py"), local])
            self.editing = (remote, local, before, proc)
            self.app.status_var.set("Editing %s -- close the MD-100 Utility window to write it back." % remote)
            self.after(500, self._watch_editor)

        self.app.run_task("Downloading %s..." % remote, work, done)

    def _watch_editor(self):
        remote, local, before, proc = self.editing
        if proc.poll() is None:
            self.after(500, self._watch_editor)
            return
        self.editing = None
        if _sha1(local) == before:
            self.app.status_var.set("%s: no changes." % remote)
            return
        if self.app.link is None:
            messagebox.showwarning("Not connected",
                                   "The edited image could not be written back because the device\n"
                                   "is no longer connected. It is kept at:\n\n" + local)
            return
        if not messagebox.askyesno("Write back?", "Write the edited image back to the device?\n\n" + remote):
            self.app.status_var.set("Edited copy kept at %s" % local)
            return
        with open(local, "rb") as f:
            data = f.read()
        self.app.run_task("Writing %s..." % remote,
                          lambda progress: self.app.link.put(remote, data, progress),
                          lambda _: self.refresh())

    def download(self):
        remote = self._one()
        if remote is None:
            return
        local = filedialog.asksaveasfilename(initialfile=remote.rsplit("/", 1)[1],
                                             defaultextension=".img",
                                             filetypes=[("Disk images", "*.img"), ("All files", "*.*")])
        if not local:
            return

        def work(progress):
            data = self.app.link.get(remote, progress)
            with open(local, "wb") as f:
                f.write(data)

        self.app.run_task("Downloading %s..." % remote, work)

    def upload(self):
        local = filedialog.askopenfilename(filetypes=[("Disk images", "*.img"), ("All files", "*.*")])
        if not local:
            return
        remote = simpledialog.askstring("Upload disk image", "Path on the device SD card:",
                                        initialvalue="/sd/disks/" + os.path.basename(local), parent=self)
        if not remote:
            return
        if remote in self.tree.get_children() and not messagebox.askyesno(
                "Overwrite?", "%s already exists. Replace it?" % remote):
            return
        with open(local, "rb") as f:
            data = f.read()

        def work(progress):
            self.app.link.makedirs(remote.rsplit("/", 1)[0])
            self.app.link.put(remote, data, progress)

        self.app.run_task("Uploading %s..." % remote, work, lambda _: self.refresh())

    def delete(self):
        remote = self._one()
        if remote is None or not messagebox.askyesno("Delete?", "Delete %s from the device?" % remote):
            return
        self.app.run_task("Deleting %s..." % remote,
                          lambda progress: self.app.link.remove(remote), lambda _: self.refresh())


# ── Screenshots ───────────────────────────────────────────────────────────────

class ScreenshotTab(_ListTab):
    def __init__(self, parent, app):
        super().__init__(parent, app, select_mode="extended")
        self.tree.heading("#0", text="File in " + SCREENSHOT_DIR)
        self.button("Download selected...", self.download)
        self.button("Delete selected", self.delete)
        self.button("Refresh", self.refresh)
        self.preview = ttk.Label(self.buttons, text="(select a PNG to preview)", anchor="center")
        self.preview.pack(fill="both", expand=True, pady=(12, 0))
        self._image = None
        self._cache = {}  # remote path -> bytes, so download after preview is instant
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def refresh(self):
        self._cache.clear()

        def work(progress):
            try:
                return sorted(((e.name, e.size) for e in self.app.link.listdir(SCREENSHOT_DIR)
                               if not e.is_dir), reverse=True)
            except LinkError:
                return []  # no screenshots folder yet

        def done(files):
            self.tree.delete(*self.tree.get_children())
            for name, size in files:
                self.tree.insert("", "end", iid=SCREENSHOT_DIR + "/" + name, text=name,
                                 values=(_fmt_size(size),))

        self.app.run_task("Listing screenshots...", work, done)

    def _on_select(self, _event):
        sel = self.selected()
        if len(sel) != 1 or not sel[0].lower().endswith(".png") or self.app._busy:
            return
        remote = sel[0]
        if remote in self._cache:
            self._show(self._cache[remote])
            return

        def done(data):
            self._cache[remote] = data
            self._show(data)

        self.app.run_task("Loading %s..." % remote,
                          lambda progress: self.app.link.get(remote, progress), done)

    def _show(self, data):
        try:
            img = tk.PhotoImage(data=base64.b64encode(data))
        except tk.TclError:
            self.preview.configure(image="", text="(can't preview this file)")
            return
        factor = max(1, -(-img.width() // 240))  # fit the preview into ~240px wide
        if factor > 1:
            img = img.subsample(factor)
        self._image = img
        self.preview.configure(image=img, text="")

    def download(self):
        sel = self.selected()
        if not sel:
            messagebox.showinfo("Screenshots", "Select one or more files first.")
            return
        folder = filedialog.askdirectory(title="Save screenshots to")
        if not folder:
            return

        def work(progress):
            for remote in sel:
                data = self._cache.get(remote) or self.app.link.get(remote, progress)
                with open(os.path.join(folder, remote.rsplit("/", 1)[1]), "wb") as f:
                    f.write(data)
            return len(sel)

        self.app.run_task("Downloading %d file(s)..." % len(sel), work,
                          lambda n: self.app.status_var.set("Saved %d file(s) to %s" % (n, folder)))

    def delete(self):
        sel = self.selected()
        if not sel or not messagebox.askyesno("Delete?", "Delete %d file(s) from the device?" % len(sel)):
            return

        def work(progress):
            for remote in sel:
                self.app.link.remove(remote)

        self.app.run_task("Deleting...", work, lambda _: self.refresh())


# ── RAM Profiles ──────────────────────────────────────────────────────────────

class ProfileTab(_ListTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.tree.heading("#0", text="Profile (" + PROFILE_ROOT + ")")
        self.button("Download...", self.download)
        self.button("Upload...", self.upload)
        self.button("Delete", self.delete)
        self.button("Refresh", self.refresh)
        ttk.Label(self.buttons, foreground="#555", wraplength=180, justify="left",
                  text="Download saves the whole profile folder (RAM images, registers, "
                       "settings). Upload replaces a profile with a local folder, or adds "
                       "it as a new profile.").pack(fill="x", pady=(12, 0))

    def _total(self, link, path):
        total = 0
        for e in link.listdir(path):
            total += self._total(link, path + "/" + e.name) if e.is_dir else e.size
        return total

    def refresh(self):
        def work(progress):
            link = self.app.link
            try:
                names = sorted(e.name for e in link.listdir(PROFILE_ROOT) if e.is_dir)
            except LinkError:
                return []
            return [(n, self._total(link, PROFILE_ROOT + "/" + n)) for n in names]

        def done(profiles):
            self.tree.delete(*self.tree.get_children())
            for name, size in profiles:
                self.tree.insert("", "end", iid=name, text=name, values=(_fmt_size(size),))

        self.app.run_task("Listing profiles...", work, done)

    def _one(self):
        sel = self.selected()
        if not sel:
            messagebox.showinfo("RAM Profiles", "Select a profile first.")
            return None
        return sel[0]

    def download(self):
        name = self._one()
        if name is None:
            return
        folder = filedialog.askdirectory(title="Save profile '%s' into" % name)
        if not folder:
            return
        dest = os.path.join(folder, name)
        self.app.run_task("Downloading profile %s..." % name,
                          lambda progress: self.app.link.get_tree(PROFILE_ROOT + "/" + name, dest, progress),
                          lambda _: self.app.status_var.set("Saved profile to %s" % dest))

    def upload(self):
        folder = filedialog.askdirectory(title="Choose a local profile folder to upload", mustexist=True)
        if not folder:
            return
        name = simpledialog.askstring("Upload profile", "Profile name on the device:",
                                      initialvalue=os.path.basename(folder.rstrip("/\\")), parent=self)
        if not name:
            return
        if "/" in name or name in ("", ".", ".."):
            messagebox.showerror("Upload profile", "Invalid profile name.")
            return
        exists = name in self.tree.get_children()
        if exists and not messagebox.askyesno(
                "Replace?", "Profile '%s' already exists on the device.\n"
                            "Replace its contents with the local folder?" % name):
            return
        remote = PROFILE_ROOT + "/" + name

        def work(progress):
            link = self.app.link
            link.put_tree(folder, remote, progress)
            # Replacing: drop device files the local folder doesn't have, so
            # the profile ends up exactly like the folder (e.g. a stale
            # ram2.bin from an older save would otherwise still be loaded).
            # Done after the upload, so a failed upload never leaves the
            # profile with files already removed.
            if exists:
                _prune(link, folder, remote)

        self.app.run_task("Uploading profile %s..." % name, work, lambda _: self.refresh())

    def delete(self):
        name = self._one()
        if name is None or not messagebox.askyesno(
                "Delete?", "Delete profile '%s' and all its files from the device?" % name):
            return
        self.app.run_task("Deleting profile %s..." % name,
                          lambda progress: self.app.link.remove_tree(PROFILE_ROOT + "/" + name),
                          lambda _: self.refresh())


def _prune(link, local_dir, remote_dir):
    for e in link.listdir(remote_dir):
        lp = os.path.join(local_dir, e.name)
        rp = remote_dir + "/" + e.name
        if e.is_dir:
            if os.path.isdir(lp):
                _prune(link, lp, rp)
            else:
                link.remove_tree(rp)
        elif not os.path.isfile(lp):
            link.remove(rp)


if __name__ == "__main__":
    App().mainloop()
