# DownloadX — free IDM-style download manager (Windows, Python, no extra packages)

**Step 1 (v0.2.0)** — desktop app: multi-connection, pause/resume, queue, progress.
Chrome auto-capture Step 2 mein aayega. Poori tafseel: **STATUS.md**.

## Chalana
1. Python 3 (python.org, *Add Python to PATH*).
2. `DownloadX.bat` double-click.
3. **Add URL** → link paste → **Download now**.

## Tests
`run_tests.bat`

## Folders
```
app.py / DownloadX.bat   app launcher
downloadx/               engine.py (download engine), gui.py (window), util.py
tests/test_engine.py     automatic tests
extension/, native_host/ purana prototype (Step 2 mein naye engine se judega)
STATUS.md                done / baqi list
```
Sirf wohi content download karein jo aapka apna ho ya jiski ijazat ho.
