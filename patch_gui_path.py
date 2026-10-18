from pathlib import Path
p = Path("downloadx/gui.py")
s = p.read_text(encoding="utf-8")
old = 'self.m = Manager(BASE / "data", default_download_dir())'
new = '''data_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DownloadX"
        data_root.mkdir(parents=True, exist_ok=True)
        self.m = Manager(data_root, default_download_dir())'''
if old not in s:
    raise SystemExit("STOP: GUI data-path line not found; original file left unchanged.")
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("GUI data folder updated.")
