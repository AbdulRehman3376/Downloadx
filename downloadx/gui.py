"""DownloadX desktop window (tkinter - ships with Python, nothing to install)."""
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .engine import COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED, Manager
from .util import default_download_dir, human_size, human_time

BASE = Path(__file__).resolve().parent.parent
STATUS_TEXT = {QUEUED: "Queued", DOWNLOADING: "Downloading", PAUSED: "Paused",
               COMPLETED: "Complete", ERROR: "Error"}


def open_path(p):
    p = str(p)
    if sys.platform == "win32":
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def bar(pct, width=16):
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {pct:5.1f}%"


class AddDialog(tk.Toplevel):
    def __init__(self, parent, manager, clip=""):
        super().__init__(parent)
        self.title("Add download")
        self.transient(parent)
        self.resizable(False, False)
        self.m = manager
        self.result = None
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="URL(s) - one per line:").grid(row=0, column=0, sticky="w")
        self.txt = tk.Text(f, width=70, height=6)
        self.txt.grid(row=1, column=0, columnspan=3, pady=4)
        if clip.lower().startswith(("http://", "https://")):
            self.txt.insert("1.0", clip.strip())
        ttk.Label(f, text="Save to:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.dir = tk.StringVar(value=manager.settings["dest_dir"])
        ttk.Entry(f, textvariable=self.dir, width=58).grid(row=3, column=0, columnspan=2, sticky="we")
        ttk.Button(f, text="Browse…", command=self.browse).grid(row=3, column=2, padx=4)
        ttk.Label(f, text="Connections:").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.conn = tk.IntVar(value=manager.settings["connections"])
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.conn, width=5).grid(row=5, column=0, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Download now", command=lambda: self.ok(True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Add paused", command=lambda: self.ok(False)).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self.txt.focus_set()
        self.grab_set()

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.dir.get())
        if d:
            self.dir.set(d)

    def ok(self, start):
        urls = [u.strip() for u in self.txt.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            return
        try:
            for u in urls:
                self.m.add(u, self.dir.get(), connections=int(self.conn.get()), start=start)
        except Exception as e:
            messagebox.showerror("DownloadX", str(e), parent=self)
            return
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"DownloadX {__version__}")
        self.geometry("980x520")
        self.minsize(760, 380)
        data_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DownloadX"
        data_root.mkdir(parents=True, exist_ok=True)
        self.m = Manager(data_root, default_download_dir())
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-n>", lambda e: self.add())
        self.bind("<Delete>", lambda e: self.remove())
        self.refresh()

    def _build(self):
        bar_ = ttk.Frame(self, padding=(6, 6))
        bar_.pack(fill="x")
        for text, cmd in (("➕ Add URL", self.add), ("▶ Resume", self.resume), ("⏸ Pause", self.pause),
                          ("⏯ Resume all", self.m.resume_all), ("⏹ Pause all", self.m.pause_all),
                          ("🗑 Remove", self.remove), ("📂 Open folder", self.open_folder),
                          ("⚙ Settings", self.settings)):
            ttk.Button(bar_, text=text, command=cmd).pack(side="left", padx=2)
        cols = ("name", "size", "progress", "speed", "eta", "status")
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=6)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for c, w, anc in (("name", 320, "w"), ("size", 90, "e"), ("progress", 190, "w"),
                          ("speed", 95, "e"), ("eta", 70, "e"), ("status", 150, "w")):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor=anc, stretch=(c == "name"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Button-3>", self.on_right)
        self.menu = tk.Menu(self, tearoff=0)
        for label, cmd in (("Open file", self.open_file), ("Open folder", self.open_folder),
                           ("Resume", self.resume), ("Pause", self.pause),
                           ("Copy URL", self.copy_url), ("Remove", self.remove),
                           ("Remove + delete file", lambda: self.remove(True))):
            self.menu.add_command(label=label, command=cmd)
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")

    # ---- actions
    def sel(self):
        return list(self.tree.selection())

    def add(self):
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = ""
        AddDialog(self, self.m, clip)

    def pause(self):
        for i in self.sel():
            self.m.pause(i)

    def resume(self):
        for i in self.sel():
            self.m.resume(i)

    def remove(self, delete=False):
        ids = self.sel()
        if not ids:
            return
        if not messagebox.askyesno("DownloadX", f"Remove {len(ids)} item(s) from the list?"
                                   + ("\nCompleted files will be deleted too." if delete else "")
                                   + "\nUnfinished partial files are deleted."):
            return
        for i in ids:
            self.m.remove(i, delete_files=delete)

    def _task(self):
        ids = self.sel()
        return self.m.tasks.get(ids[0]) if ids else None

    def open_file(self):
        t = self._task()
        if t and t.status == COMPLETED and t.path and Path(t.path).exists():
            open_path(t.path)

    def open_folder(self):
        t = self._task()
        d = Path(t.dest_dir) if t else Path(self.m.settings["dest_dir"])
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    def copy_url(self):
        t = self._task()
        if t:
            self.clipboard_clear()
            self.clipboard_append(t.url)

    def on_double(self, _e):
        t = self._task()
        if t and t.status == COMPLETED:
            self.open_file()
        elif t and t.status in (PAUSED, ERROR):
            self.m.resume(t.id)
        elif t:
            self.m.pause(t.id)

    def on_right(self, e):
        row = self.tree.identify_row(e.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(e.x_root, e.y_root)

    def settings(self):
        d = tk.Toplevel(self)
        d.title("Settings")
        d.transient(self)
        d.grab_set()
        f = ttk.Frame(d, padding=12)
        f.pack()
        s = self.m.settings
        dir_v, conn_v, act_v = tk.StringVar(value=s["dest_dir"]), tk.IntVar(value=s["connections"]), \
            tk.IntVar(value=s["max_active"])
        ttk.Label(f, text="Default save folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=dir_v, width=50).grid(row=1, column=0)
        ttk.Button(f, text="Browse…", command=lambda: dir_v.set(
            filedialog.askdirectory(initialdir=dir_v.get()) or dir_v.get())).grid(row=1, column=1, padx=4)
        ttk.Label(f, text="Connections per download (1-32):").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(f, from_=1, to=32, textvariable=conn_v, width=5).grid(row=3, column=0, sticky="w")
        ttk.Label(f, text="Max simultaneous downloads:").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(f, from_=1, to=10, textvariable=act_v, width=5).grid(row=5, column=0, sticky="w")

        def save():
            s.update(dest_dir=dir_v.get(), connections=int(conn_v.get()), max_active=int(act_v.get()))
            self.m.save_settings()
            d.destroy()
        ttk.Button(f, text="Save", command=save).grid(row=6, column=0, sticky="e", pady=(12, 0))

    # ---- refresh loop
    def refresh(self):
        tasks = sorted(self.m.tasks.values(), key=lambda t: t.added)
        have = set(self.tree.get_children())
        ids = {t.id for t in tasks}
        for i in have - ids:
            self.tree.delete(i)
        for idx, t in enumerate(tasks):
            name = (Path(t.path).name if t.path else (t.filename or t.url[:60]))
            st = STATUS_TEXT.get(t.status, t.status)
            if t.status == ERROR:
                st = "Error: " + t.error[:40]
            elif t.status == DOWNLOADING and not t.resumable and t.size:
                st += " (1 stream)"
            vals = (name, human_size(t.size), bar(t.percent()),
                    human_size(t.speed) + "/s" if t.status == DOWNLOADING else "",
                    human_time(t.eta()) if t.status == DOWNLOADING else "", st)
            if t.id in have:
                self.tree.item(t.id, values=vals)
            else:
                self.tree.insert("", idx, iid=t.id, values=vals)
        act = sum(1 for t in tasks if t.status == DOWNLOADING)
        self.status.set(f"{len(tasks)} item(s)  |  {act} active  |  "
                        f"{human_size(self.m.total_speed())}/s  |  Save to: {self.m.settings['dest_dir']}")
        self.after(500, self.refresh)

    def on_close(self):
        self.m.shutdown()
        self.destroy()


def main():
    App().mainloop()
