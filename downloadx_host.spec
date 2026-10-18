
# -*- mode: python ; coding: utf-8 -*-
# Build from the DownloadX project root with this spec in the root.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH)

# Required FFmpeg executables
ffmpeg_exe = root / "ffmpeg" / "bin" / "ffmpeg.exe"
ffprobe_exe = root / "ffmpeg" / "bin" / "ffprobe.exe"

# Stop immediately if either required executable is missing
missing = [
    str(path)
    for path in (ffmpeg_exe, ffprobe_exe)
    if not path.is_file()
]

if missing:
    raise FileNotFoundError(
        "Required FFmpeg files are missing:\n" + "\n".join(missing)
    )

# Collect yt-dlp dependencies
yt_dlp_datas, yt_dlp_binaries, yt_dlp_hiddenimports = collect_all("yt_dlp")
yt_dlp_hiddenimports += collect_submodules("yt_dlp")

# Bundle FFmpeg and FFprobe inside the one-file host executable.
# PyInstaller extracts these to its temporary runtime directory.
all_binaries = list(yt_dlp_binaries)
all_binaries.extend([
    (str(ffmpeg_exe), "ffmpeg"),
    (str(ffprobe_exe), "ffmpeg"),
])

a = Analysis(
    [str(root / "native_host" / "downloadx_host.py")],
    pathex=[str(root)],
    binaries=all_binaries,
    datas=yt_dlp_datas,
    hiddenimports=yt_dlp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="downloadx_host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
