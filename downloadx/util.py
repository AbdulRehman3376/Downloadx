import os
import re
import urllib.parse
from pathlib import Path

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def safe_filename(name):
    name = urllib.parse.unquote(name)
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name).strip(" .")
    return name[:150] or "download"


def filename_from(url, content_disposition=None):
    if content_disposition:
        m = (re.search(r"filename\*=(?:UTF-8'')?([^;]+)", content_disposition, re.I)
             or re.search(r'filename="?([^";]+)"?', content_disposition, re.I))
        if m:
            return safe_filename(m.group(1).strip('"'))
    path = urllib.parse.urlparse(url).path
    return safe_filename(os.path.basename(path) or "download")


def unique_path(directory, name):
    directory = Path(directory)
    p = directory / name
    stem, suffix, i = p.stem, p.suffix, 1
    while p.exists() or p.with_name(p.name + ".dxpart").exists():
        p = directory / f"{stem} ({i}){suffix}"
        i += 1
    return p


def human_size(n):
    if n is None or n < 0:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_time(sec):
    if sec is None or sec < 0 or sec > 86400 * 30:
        return "--"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def default_download_dir():
    env = os.environ.get("DOWNLOADX_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "downloads"
