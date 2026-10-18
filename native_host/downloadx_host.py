# DownloadX Native Messaging Host — 0.8.0
# - Exactly ONE temp file per download; it becomes the final file.
# - Video/audio intermediates live in a hidden per-task folder and are deleted.
# - Cancel / error / crash => every temp file is removed.
# - Every event carries exact sizes (B / KB / MB / GB) as numbers and text.
# stdout is reserved exclusively for Chrome Native Messaging frames.

import concurrent.futures
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

VERSION = "0.8.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

if getattr(sys, "frozen", False):
    HOST_DIR = Path(sys.executable).resolve().parent
    BASE_DIR = HOST_DIR.parent
else:
    HOST_DIR = Path(__file__).resolve().parent
    BASE_DIR = HOST_DIR.parent


def find_tool(exe_name, env_name, extra_candidates=()):
    """Find FFmpeg tools bundled with DownloadX or installed separately."""
    candidates = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg" / exe_name)
        candidates.append(Path(meipass) / exe_name)

    configured = os.environ.get(env_name, "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(
            configured_path / exe_name
            if configured_path.is_dir()
            else configured_path
        )

    candidates.extend(Path(p) for p in extra_candidates)

    candidates.extend([
        HOST_DIR / "ffmpeg" / exe_name,
        HOST_DIR / "ffmpeg" / "bin" / exe_name,
        BASE_DIR / "ffmpeg" / exe_name,
        BASE_DIR / "ffmpeg" / "bin" / exe_name,
        BASE_DIR / "native_host" / "ffmpeg" / exe_name,
        BASE_DIR / "native_host" / "ffmpeg" / "bin" / exe_name,
    ])

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except (OSError, RuntimeError):
            pass

    from_path = shutil.which(exe_name)
    if from_path:
        return Path(from_path).resolve()

    return candidates[0] if candidates else Path(exe_name)


FFMPEG_EXE = find_tool("ffmpeg.exe", "DOWNLOADX_FFMPEG")
FFPROBE_EXE = find_tool(
    "ffprobe.exe",
    "DOWNLOADX_FFPROBE",
    extra_candidates=(FFMPEG_EXE.with_name("ffprobe.exe"),),
)

ARIA2_DIR = HOST_DIR / "aria2-1.37.0-win-64bit-build1"
ARIA2_EXE = ARIA2_DIR / "aria2c.exe"

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "host.log"
PARTS_FILE = DATA_DIR / "active_parts.json"

configured_download_dir = os.environ.get(
    "DOWNLOADX_DOWNLOAD_DIR", ""
).strip()
DOWNLOAD_DIR = (
    Path(configured_download_dir).expanduser()
    if configured_download_dir
    else BASE_DIR / "downloads"
)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Hidden work area for video/audio streams (never shows partial junk
# in the real downloads folder).
TMP_ROOT = DOWNLOAD_DIR / ".downloadx_tmp"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(TMP_ROOT), 0x02)
    except Exception:
        pass

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)
log = logging.getLogger("DownloadX")

DIRECT_MAX_CONNECTIONS = max(
    2, min(16, int(os.environ.get("DOWNLOADX_CONNECTIONS", "8")))
)
DIRECT_CHUNK_SIZE = 8 * 1024 * 1024
DIRECT_PARALLEL_MIN_SIZE = 8 * 1024 * 1024
READ_CHUNK_SIZE = 512 * 1024
PROGRESS_INTERVAL = 0.35
MEDIA_STEM = "dx_media"

WRITE_LOCK = threading.Lock()
TASKS = {}
TASKS_LOCK = threading.Lock()
INFO_CACHE = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 180

NAME_LOCK = threading.Lock()
RESERVED = set()
PARTS_LOCK = threading.Lock()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z|\[]")


# --------------------------------------------------------------------------
# Native messaging framing
# --------------------------------------------------------------------------

def send_message(payload):
    data = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with WRITE_LOCK:
        sys.stdout.buffer.write(struct.pack("<I", len(data)))
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def read_exact(stream, size):
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message():
    header = read_exact(sys.stdin.buffer, 4)
    if header is None:
        return None
    length = struct.unpack("<I", header)[0]
    if length > 16 * 1024 * 1024:
        raise ValueError("Native message exceeds 16 MB limit")
    body = read_exact(sys.stdin.buffer, length)
    if body is None:
        raise EOFError("Incomplete native message")
    return json.loads(body.decode("utf-8"))


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def validate_url(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL is missing")
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Only valid HTTP and HTTPS URLs are supported")
    return value


def safe_title(value):
    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        str(value or "DownloadX download"),
    )
    return value.strip(" .")[:150].strip(" .") or "DownloadX download"


def clean_error(exc):
    return ANSI_RE.sub("", str(exc)).strip()


def human_size(n):
    """Bytes -> '512 B', '12.40 KB', '245.30 MB', '1.24 GB'."""
    if n is None:
        return None
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{int(n)} B" if i == 0 else f"{n:.2f} {units[i]}"


def is_partial_name(name):
    """Recognize yt-dlp leftovers and fragment files."""
    if name.endswith((".part", ".ytdl")) or ".part-" in name:
        return True
    return bool(re.search(r"\.f\d+[^.]*\.\w+$", name))


def remove_tree(path):
    """Delete a folder, retrying because Windows may still hold handles."""
    for _ in range(10):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.4)
    log.error("Could not fully remove %s", path)
    return False


def remove_file(path):
    for _ in range(10):
        try:
            Path(path).unlink(missing_ok=True)
            return True
        except OSError:
            time.sleep(0.4)
    log.error("Could not remove file %s", path)
    return False


def is_inside(root, target):
    root_s = os.path.normcase(str(Path(root).resolve()))
    target_s = os.path.normcase(str(Path(target).resolve()))
    return target_s.startswith(root_s + os.sep)


def release_name(path):
    if path is not None:
        with NAME_LOCK:
            RESERVED.discard(str(path).lower())


def reserve_path(stem, ext):
    """Pick a final path nobody uses (on disk, as .part, or by a running task)."""
    with NAME_LOCK:
        candidate = DOWNLOAD_DIR / f"{stem}{ext}"
        number = 2
        while (
            candidate.exists()
            or Path(str(candidate) + ".part").exists()
            or str(candidate).lower() in RESERVED
        ):
            candidate = DOWNLOAD_DIR / f"{stem} ({number}){ext}"
            number += 1
        RESERVED.add(str(candidate).lower())
        return candidate


# Visible .part files are tracked so a crash can never leave them behind.

def _read_parts():
    try:
        data = json.loads(PARTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_parts(parts):
    try:
        PARTS_FILE.write_text(json.dumps(parts), encoding="utf-8")
    except OSError:
        log.exception("Could not write parts manifest")


def register_part(path):
    with PARTS_LOCK:
        parts = _read_parts()
        if str(path) not in parts:
            parts.append(str(path))
            _write_parts(parts)


def unregister_part(path):
    try:
        with PARTS_LOCK:
            parts = [p for p in _read_parts() if p != str(path)]
            _write_parts(parts)
    except Exception:
        log.exception("Could not update parts manifest")


def cleanup_orphans():
    """Remove everything an earlier (dead) host instance left behind."""
    with PARTS_LOCK:
        for item in _read_parts():
            try:
                Path(item).unlink(missing_ok=True)
                log.info("Removed orphan part file: %s", item)
            except OSError:
                log.exception("Could not remove orphan %s", item)
        _write_parts([])
    try:
        for child in TMP_ROOT.iterdir():
            if child.is_dir():
                remove_tree(child)
            else:
                remove_file(child)
    except OSError:
        pass


def filename_from_disposition(value):
    if not value:
        return None
    match = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", value, re.I)
    if match:
        try:
            return unquote(
                match.group(2).strip().strip('"'),
                encoding=match.group(1) or "utf-8",
            )
        except (LookupError, UnicodeError):
            pass
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', value, re.I)
    return unquote(match.group(1).strip()) if match else None


def fmt_size(item, duration=None):
    size = item.get("filesize") or item.get("filesize_approx")
    if not size and item.get("tbr") and duration:
        size = item["tbr"] * 1000 / 8 * duration
    return int(size) if size else None


def best_audio_format(info):
    audios = [
        f for f in info.get("formats", [])
        if f.get("vcodec") in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    m4a = [f for f in audios if f.get("ext") == "m4a"] or audios
    return max(
        m4a,
        key=lambda f: (f.get("abr") or f.get("tbr") or 0),
        default=None,
    )


class YtDlpLogger:
    def debug(self, message):
        if message:
            log.debug("yt-dlp: %s", message)

    def info(self, message):
        if message:
            log.info("yt-dlp: %s", message)

    def warning(self, message):
        if message:
            log.warning("yt-dlp: %s", message)

    def error(self, message):
        if message:
            log.error("yt-dlp: %s", message)


# --------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------

class Task:
    def __init__(self, task_id, request_id):
        self.id = task_id
        self.request_id = request_id
        self.resume_gate = threading.Event()
        self.resume_gate.set()
        self.cancel_event = threading.Event()
        self.paused = False
        self.last_emit = 0.0
        self.progress_lock = threading.Lock()

        self.tmp_dir = TMP_ROOT / re.sub(r"[^\w.-]", "_", str(task_id))[:80]
        self.part_path = None       # the single visible temp file (direct)
        self.reserved = None        # reserved final path
        self.expected_total = None  # exact combined size (video + audio)
        self.stream_bytes = {}
        self.stream_totals = {}

    def make_tmp(self):
        remove_tree(self.tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.stream_bytes.clear()
        self.stream_totals.clear()

    def cleanup(self, failed):
        remove_tree(self.tmp_dir)
        if self.part_path is not None:
            if failed:
                remove_file(self.part_path)
            unregister_part(self.part_path)
            self.part_path = None
        release_name(self.reserved)

    def emit(self, event, **data):
        payload = {
            "event": event,
            "request_id": self.request_id,
            "task_id": self.id,
            **data,
        }
        try:
            send_message(payload)
        except (BrokenPipeError, OSError):
            log.exception("Unable to send task event")

    def check_control(self):
        while not self.resume_gate.wait(0.25):
            if self.cancel_event.is_set():
                raise DownloadCancelled("Download cancelled by user")
        if self.cancel_event.is_set():
            raise DownloadCancelled("Download cancelled by user")

    def postprocessor_hook(self, data):
        name = data.get("postprocessor", "?")
        status = data.get("status")
        log.info("postprocessor %s: %s", name, status)
        if status == "started":
            self.emit(
                "progress",
                status="processing",
                percent=100,
                downloaded=None,
                total=None,
                speed=None,
                eta=None,
                message="Merging/converting with FFmpeg…",
            )

    def progress_hook(self, data):
        self.check_control()
        status = data.get("status")
        key = (
            (data.get("info_dict") or {}).get("format_id")
            or data.get("filename")
            or "stream"
        )

        if status == "finished":
            done = int(
                data.get("downloaded_bytes")
                or data.get("total_bytes")
                or 0
            )
            self.stream_bytes[key] = done
            self.stream_totals[key] = done
            return  # another stream may follow; merge announced by postprocessor

        if status != "downloading":
            return

        now = time.monotonic()
        with self.progress_lock:
            if (
                now - self.last_emit < PROGRESS_INTERVAL
                and data.get("downloaded_bytes", 0) != 0
            ):
                return
            self.last_emit = now

        self.stream_bytes[key] = int(data.get("downloaded_bytes") or 0)
        stream_total = (
            data.get("total_bytes") or data.get("total_bytes_estimate")
        )
        if stream_total:
            self.stream_totals[key] = int(stream_total)

        downloaded = sum(self.stream_bytes.values())
        total = (
            self.expected_total or sum(self.stream_totals.values()) or None
        )
        if total and downloaded > total:
            total = downloaded

        speed = data.get("speed")
        eta = None
        if total and speed:
            eta = int(max(0, total - downloaded) / speed)

        self.emit(
            "progress",
            status="downloading",
            percent=(
                min(99.9, round(downloaded * 100 / total, 1))
                if total else None
            ),
            downloaded=downloaded,
            total=int(total) if total else None,
            downloaded_text=human_size(downloaded),
            total_text=human_size(total),
            speed=float(speed) if speed else None,
            speed_text=(human_size(speed) + "/s") if speed else None,
            eta=eta,
            message="Downloading…",
        )


# --------------------------------------------------------------------------
# yt-dlp (video / audio)
# --------------------------------------------------------------------------

def base_ydl_options(task=None, referer=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    }
    if referer:
        headers["Referer"] = str(referer)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": YtDlpLogger(),
        "no_color": True,
        "color": {"stdout": "no_color", "stderr": "no_color"},
        "retries": 5,
        "fragment_retries": 8,
        "file_access_retries": 3,
        "continuedl": True,
        "overwrites": False,
        "updatetime": False,
        "http_headers": headers,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 25,
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "web_embedded"],
            },
        },
    }

    deno_path = shutil.which("deno")
    if deno_path:
        opts["js_runtimes"] = {"deno": {"path": deno_path}}
        log.info("Deno JavaScript runtime enabled: %s", deno_path)
    else:
        log.warning(
            "Deno was not found on PATH; YouTube JS challenge solving may fail"
        )

    if task:
        opts["progress_hooks"] = [task.progress_hook]
        opts["postprocessor_hooks"] = [task.postprocessor_hook]

    if FFMPEG_EXE.is_file():
        opts["ffmpeg_location"] = str(FFMPEG_EXE)

    return opts


def cache_key(url, referer):
    return url, str(referer or "")


def get_info(url, referer=None, force_refresh=False):
    key = cache_key(url, referer)
    now = time.monotonic()

    if not force_refresh:
        with CACHE_LOCK:
            cached = INFO_CACHE.get(key)
            if cached and now - cached[0] < CACHE_TTL:
                return cached[1]

    with yt_dlp.YoutubeDL(base_ydl_options(referer=referer)) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError("Could not read video information")

    with CACHE_LOCK:
        INFO_CACHE[key] = (now, info)
        for old_key, (created, _) in list(INFO_CACHE.items()):
            if now - created > CACHE_TTL:
                INFO_CACHE.pop(old_key, None)

    return info


def list_formats(url, referer=None):
    url = validate_url(url)
    info = get_info(url, referer)
    duration = info.get("duration")
    audio_best = best_audio_format(info)
    formats = []
    seen = set()

    for item in info.get("formats", []):
        raw_id = str(item.get("format_id", ""))
        if not raw_id:
            continue

        has_video = item.get("vcodec") not in (None, "none")
        has_audio = item.get("acodec") not in (None, "none")
        if not has_video and not has_audio:
            continue

        height = item.get("height")
        ext = str(item.get("ext") or "unknown")
        bitrate = item.get("tbr")

        if has_video:
            prefixed = f"video::{raw_id}"
            size = fmt_size(item, duration)
            exact = bool(item.get("filesize"))
            if not has_audio and audio_best:
                a_size = fmt_size(audio_best, duration)
                size = ((size or 0) + (a_size or 0)) or None
                exact = exact and bool(audio_best.get("filesize"))
            label = f"{height}p" if height else str(
                item.get("format_note") or "Video"
            )
            label += f" · {ext} · " + (
                "audio included" if has_audio else "audio will be merged"
            )
            kind = "video"
        else:
            prefixed = f"audio::{raw_id}"
            abr = item.get("abr")
            size = int(duration * 192000 / 8) if duration else None
            exact = False
            label = (
                f"Audio · {int(abr)} kbps" if abr else "Audio"
            ) + f" · {ext} · MP3 output"
            kind = "audio"

        size_text = (
            ("" if exact else "~") + human_size(size) if size
            else "size unknown"
        )
        label += f" · {size_text}"

        if prefixed in seen:
            continue
        seen.add(prefixed)

        formats.append({
            "id": prefixed,
            "format_id": prefixed,
            "source_format_id": raw_id,
            "kind": kind,
            "label": label,
            "height": height,
            "ext": ext,
            "has_audio": has_audio,
            "has_video": has_video,
            "filesize": size,
            "size_text": size_text,
            "size_exact": exact,
            "tbr": bitrate,
        })

    formats.sort(
        key=lambda item: (
            0 if item["kind"] == "video" else 1,
            -(item["height"] or 0),
            -(item["tbr"] or 0),
        )
    )

    return {
        "ok": True,
        "version": VERSION,
        "title": info.get("title") or "Untitled video",
        "formats": formats,
        "download_dir": str(DOWNLOAD_DIR),
    }


def probe_streams(filepath):
    if not FFPROBE_EXE.is_file():
        raise RuntimeError(f"ffprobe.exe not found: {FFPROBE_EXE}")

    result = subprocess.run(
        [
            str(FFPROBE_EXE),
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "json",
            str(filepath),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode:
        raise RuntimeError(
            "ffprobe could not verify output: "
            + (result.stderr.strip() or "unknown error")
        )

    return json.loads(result.stdout).get("streams", [])


def find_output(folder, extension):
    expected = folder / (MEDIA_STEM + extension)
    if expected.is_file() and expected.stat().st_size > 0:
        return expected

    candidates = [
        p for p in folder.glob(MEDIA_STEM + ".*")
        if p.is_file()
        and p.stat().st_size > 0
        and not is_partial_name(p.name)
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def download_format(task, url, format_id, referer=None):
    url = validate_url(url)

    if not FFMPEG_EXE.is_file() or not FFPROBE_EXE.is_file():
        raise RuntimeError(
            f"FFmpeg/ffprobe missing. Check {FFMPEG_EXE.parent}"
        )

    selected_id = str(format_id or "").strip()
    if selected_id.startswith("video::"):
        kind, raw_id = "video", selected_id[len("video::"):]
    elif selected_id.startswith("audio::"):
        kind, raw_id = "audio", selected_id[len("audio::"):]
    else:
        kind, raw_id = "video", selected_id

    if not raw_id:
        raise ValueError("No format was selected")

    def find_selected(info):
        return next(
            (i for i in info.get("formats", [])
             if str(i.get("format_id")) == raw_id),
            None,
        )

    info = get_info(url, referer)
    selected = find_selected(info)
    if selected is None:
        info = get_info(url, referer, force_refresh=True)
        selected = find_selected(info)
    if selected is None:
        raise RuntimeError(
            "Selected format is no longer available. Refresh formats and try again."
        )

    title = info.get("title") or "DownloadX download"
    duration = info.get("duration")

    task.make_tmp()  # every intermediate file lives only here

    opts = base_ydl_options(task, referer)
    opts.update({
        "paths": {"home": str(task.tmp_dir), "temp": str(task.tmp_dir)},
        "outtmpl": MEDIA_STEM + ".%(ext)s",
        "overwrites": False,
        "nopart": False,
        "continuedl": True,
    })

    if kind == "audio":
        if selected.get("acodec") in (None, "none"):
            raise RuntimeError("The selected format does not contain audio")
        opts["format"] = raw_id
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
        expected_ext = ".mp3"
        task.expected_total = (
            int(duration * 192000 / 8) if duration else fmt_size(selected)
        )
    else:
        if selected.get("vcodec") in (None, "none"):
            raise RuntimeError("The selected format does not contain video")

        total = fmt_size(selected, duration) or 0
        if selected.get("acodec") not in (None, "none"):
            opts["format"] = raw_id
        else:
            audio = best_audio_format(info)
            if audio:
                opts["format"] = f"{raw_id}+{audio['format_id']}"
                total += fmt_size(audio, duration) or 0
            else:
                opts["format"] = f"{raw_id}+bestaudio"
        task.expected_total = total or None
        opts["merge_output_format"] = "mp4"
        expected_ext = ".mp4"

    task.emit(
        "progress",
        status="starting",
        percent=0,
        downloaded=0,
        total=task.expected_total,
        downloaded_text=human_size(0),
        total_text=human_size(task.expected_total),
        speed=None,
        eta=None,
        message="Preparing download…",
        title=title,
    )
    log.info(
        "Starting task=%s title=%r kind=%s format=%s",
        task.id, title, kind, raw_id,
    )
    task.check_control()

    def run_ydl(options):
        with yt_dlp.YoutubeDL(options) as ydl:
            code = ydl.download([url])
        log.info("yt-dlp download() returned code=%s", code)
        task.check_control()
        if code not in (None, 0):
            raise DownloadError(f"yt-dlp exited with status {code}")

    try:
        task.check_control()
        run_ydl(opts)
    except DownloadCancelled:
        raise
    except Exception as exc:
        log.warning("Download failed, retrying fresh: %s", clean_error(exc))
        if task.cancel_event.is_set():
            raise DownloadCancelled("Download cancelled by user")

        task.make_tmp()  # wipe stale partials before retry
        get_info(url, referer, force_refresh=True)
        retry_opts = dict(opts)
        retry_opts["continuedl"] = False
        retry_opts["http_chunk_size"] = 0
        task.check_control()
        run_ydl(retry_opts)

    task.check_control()
    task.emit(
        "progress",
        status="processing",
        percent=100,
        downloaded=None,
        total=None,
        speed=None,
        eta=None,
        message="Verifying file…",
        title=title,
    )

    produced = find_output(task.tmp_dir, expected_ext)
    if produced is None:
        raise RuntimeError(
            f"Download ended but output file was not found ({expected_ext})"
        )
    if kind == "audio" and produced.suffix.lower() != ".mp3":
        raise RuntimeError("MP3 conversion failed")

    if kind == "video":
        streams = probe_streams(produced)
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)

        if not has_video:
            raise RuntimeError("Downloaded file has no video stream")
        if not has_audio:
            raise RuntimeError(
                "Audio is missing after download. Silent output rejected; "
                "refresh formats and retry."
            )

    task.check_control()

    # The ONLY file that ever reaches the downloads folder.
    final = reserve_path(safe_title(title), produced.suffix or expected_ext)
    task.reserved = final
    shutil.move(str(produced), str(final))

    size = final.stat().st_size
    log.info("DOWNLOAD OK: %s (%s bytes)", final, size)

    return {
        "ok": True,
        "title": title,
        "path": str(final),
        "filename": final.name,
        "size": size,
        "size_text": human_size(size),
        "download_dir": str(DOWNLOAD_DIR),
        "has_audio": kind == "video",
        "message": "Download completed and verified",
    }


# --------------------------------------------------------------------------
# Direct downloads (single .part file -> renamed to final)
# --------------------------------------------------------------------------

def make_request(url, headers, byte_range=None):
    request_headers = dict(headers)
    if byte_range is not None:
        request_headers["Range"] = (
            f"bytes={byte_range[0]}-{byte_range[1]}"
        )
    return urllib.request.Request(url, headers=request_headers)


def probe_range_support(url, headers):
    """Return (size, supports_ranges, disposition_name)."""
    request = make_request(url, headers, (0, 0))

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            disp = filename_from_disposition(
                response.headers.get("Content-Disposition")
            )
            content_range = response.headers.get("Content-Range", "")
            match = re.match(r"bytes\s+0-0/(\d+)", content_range, re.I)

            if response.status == 206 and match:
                response.read(1)
                return int(match.group(1)), True, disp

            length = response.headers.get("Content-Length")
            return (
                int(length) if length and length.isdigit() else None,
                False,
                disp,
            )

    except urllib.error.HTTPError as exc:
        if exc.code in (400, 403, 405, 416):
            return None, False, None
        raise


def emit_direct_progress(
    task, downloaded, total, started,
    message="Downloading…", force=False,
):
    now = time.monotonic()
    with task.progress_lock:
        if not force and now - task.last_emit < PROGRESS_INTERVAL:
            return
        task.last_emit = now

    elapsed = max(0.001, now - started)
    speed = downloaded / elapsed

    task.emit(
        "progress",
        status="downloading",
        percent=(
            min(100.0, round(downloaded * 100 / total, 1)) if total else None
        ),
        downloaded=int(downloaded),
        total=int(total) if total else None,
        downloaded_text=human_size(downloaded),
        total_text=human_size(total),
        speed=float(speed),
        speed_text=human_size(speed) + "/s",
        eta=(
            int(max(0, total - downloaded) / speed)
            if total and speed > 0 else None
        ),
        message=message,
    )


def direct_parallel_download(task, url, headers, temp_path, total, started):
    """Disjoint byte ranges written into the single .part file."""
    if not total or total < DIRECT_PARALLEL_MIN_SIZE:
        return False

    ranges = []
    start = 0
    while start < total:
        end = min(total - 1, start + DIRECT_CHUNK_SIZE - 1)
        ranges.append((start, end))
        start = end + 1

    workers = min(DIRECT_MAX_CONNECTIONS, len(ranges))
    if workers < 2:
        return False

    progress_lock = threading.Lock()
    file_lock = threading.Lock()
    abort_event = threading.Event()
    completed = 0

    with open(temp_path, "wb") as output:
        output.truncate(total)

    def fetch_range(byte_range):
        nonlocal completed
        first, last = byte_range
        attempt = 0
        best_progress = 0

        while attempt < 3:
            attempt += 1
            attempt_progress = 0
            task.check_control()

            if abort_event.is_set():
                return

            request = make_request(url, headers, (first, last))

            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    content_range = response.headers.get("Content-Range", "")
                    match = re.match(
                        r"bytes\s+(\d+)-(\d+)/(\d+|\*)",
                        content_range,
                        re.I,
                    )

                    if (
                        response.status != 206
                        or not match
                        or int(match.group(1)) != first
                    ):
                        raise RuntimeError(
                            "Server stopped honoring HTTP Range requests"
                        )

                    position = first
                    with open(temp_path, "r+b", buffering=0) as output:
                        while position <= last:
                            task.check_control()
                            if abort_event.is_set():
                                return

                            chunk = response.read(
                                min(READ_CHUNK_SIZE, last - position + 1)
                            )
                            if not chunk:
                                raise RuntimeError(
                                    "Connection ended before this segment completed"
                                )

                            with file_lock:
                                output.seek(position)
                                output.write(chunk)

                            position += len(chunk)
                            attempt_progress += len(chunk)

                            with progress_lock:
                                delta = max(
                                    0, attempt_progress - best_progress
                                )
                                if delta:
                                    completed += delta
                                    best_progress = max(
                                        best_progress, attempt_progress
                                    )
                                current = completed

                            emit_direct_progress(
                                task, current, total, started
                            )

                    if position != last + 1:
                        raise RuntimeError(
                            "Downloaded segment length did not match requested range"
                        )
                    return

            except (DownloadError, DownloadCancelled):
                raise
            except Exception:
                if attempt >= 3:
                    raise
                time.sleep(0.35 * attempt)

    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers
        ) as pool:
            futures = [pool.submit(fetch_range, part) for part in ranges]

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception:
                    abort_event.set()
                    for other in futures:
                        other.cancel()
                    raise

        task.check_control()

        if temp_path.stat().st_size != total:
            raise RuntimeError("Parallel download output size is incorrect")

        emit_direct_progress(
            task, total, total, started,
            "Download received; verifying file…", force=True,
        )
        return True

    except Exception:
        remove_file(temp_path)
        raise


def _stream_response(task, response, temp_path, started, existing, total_hint):
    length = response.headers.get("Content-Length")
    body_length = int(length) if length and length.isdigit() else None
    total = existing + body_length if body_length is not None else total_hint
    mode = "ab" if existing else "wb"
    downloaded = existing

    with open(temp_path, mode) as output:
        while True:
            task.check_control()
            chunk = response.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            emit_direct_progress(task, downloaded, total, started)

    if downloaded == 0:
        raise RuntimeError("The server returned an empty download")

    if total and downloaded != total:
        raise RuntimeError(
            f"Download incomplete ({downloaded} of {total} bytes)"
        )

    emit_direct_progress(
        task, downloaded, total or downloaded, started,
        "Download received; verifying file…", force=True,
    )
    return downloaded


def direct_single_download(
    task, url, headers, temp_path, started,
    supports_ranges=False, total_hint=None,
):
    """Sequential download into the single .part file, resuming if safe."""
    last_error = None

    for attempt in range(1, 4):
        task.check_control()

        existing = temp_path.stat().st_size if temp_path.exists() else 0
        if existing and not supports_ranges:
            temp_path.unlink(missing_ok=True)
            existing = 0

        request = make_request(url, headers)
        if existing:
            request.add_header("Range", f"bytes={existing}-")

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if existing:
                    content_range = response.headers.get("Content-Range", "")
                    if response.status != 206 or not re.match(
                        rf"bytes\s+{existing}-", content_range, re.I
                    ):
                        temp_path.unlink(missing_ok=True)
                        raise RuntimeError("Server ignored the resume request")

                return _stream_response(
                    task, response, temp_path, started, existing, total_hint
                )

        except (DownloadCancelled, DownloadError):
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing:
                temp_path.unlink(missing_ok=True)
                last_error = exc
            else:
                raise
        except Exception as exc:
            if task.cancel_event.is_set():
                raise
            last_error = exc
            log.warning(
                "Sequential download attempt %s failed: %s",
                attempt, clean_error(exc),
            )
            time.sleep(0.5 * attempt)

    raise last_error or RuntimeError("Download failed")


def direct_download(task, url, referer=None, filename=None, title=None, cookie=None):
    url = validate_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    }
    if referer:
        headers["Referer"] = str(referer)
    if cookie:
        headers["Cookie"] = str(cookie)

    task.emit(
        "progress",
        status="starting",
        percent=0,
        downloaded=0,
        total=None,
        speed=None,
        eta=None,
        message="Checking server download support…",
        title=str(filename or title or url)[:150],
    )

    total, supports_ranges, disp_name = probe_range_support(url, headers)

    url_name = unquote(Path(urlparse(url).path).name)
    candidate = str(filename or "").strip()
    if candidate.lower().startswith(("http://", "https://")):
        candidate = ""
    title_name = str(title or "").strip()
    if title_name.lower().startswith(("http://", "https://")):
        title_name = ""

    name = safe_title(candidate or disp_name or url_name or title_name or "download")
    if not Path(name).suffix:
        for alt in (disp_name, url_name):
            if alt and Path(alt).suffix:
                name += Path(alt).suffix
                break
        else:
            name += ".bin"

    destination = reserve_path(Path(name).stem, Path(name).suffix)
    task.reserved = destination

    # The single visible temp file. It is renamed into the final file.
    temp_path = Path(str(destination) + ".part")
    remove_file(temp_path)
    task.part_path = temp_path
    register_part(temp_path)

    task.emit(
        "progress",
        status="starting",
        percent=0,
        downloaded=0,
        total=int(total) if total else None,
        downloaded_text=human_size(0),
        total_text=human_size(total),
        speed=None,
        eta=None,
        message="Starting…",
        title=destination.name,
    )

    started = time.monotonic()

    if supports_ranges and total and total >= DIRECT_PARALLEL_MIN_SIZE:
        log.info(
            "Direct download using %s parallel ranges, size=%s, url=%s",
            DIRECT_MAX_CONNECTIONS, total, url,
        )
        try:
            direct_parallel_download(
                task, url, headers, temp_path, total, started
            )
        except Exception:
            if task.cancel_event.is_set():
                raise
            log.warning(
                "Parallel Range download failed; retrying sequentially",
                exc_info=True,
            )
            remove_file(temp_path)
            direct_single_download(
                task, url, headers, temp_path, started,
                supports_ranges, total,
            )
    else:
        log.info(
            "Direct download using sequential mode; ranges=%s size=%s",
            supports_ranges, total,
        )
        direct_single_download(
            task, url, headers, temp_path, started,
            supports_ranges, total,
        )

    task.check_control()

    if not temp_path.exists() or temp_path.stat().st_size == 0:
        raise RuntimeError("The server returned an empty download")

    temp_path.replace(destination)  # the temp file BECOMES the final file

    size = destination.stat().st_size
    log.info("DIRECT DOWNLOAD OK: %s (%s bytes)", destination, size)

    return {
        "ok": True,
        "title": destination.name,
        "path": str(destination),
        "filename": destination.name,
        "size": size,
        "size_text": human_size(size),
        "download_dir": str(DOWNLOAD_DIR),
        "message": "Download completed",
    }


# --------------------------------------------------------------------------
# Task runner and request handling
# --------------------------------------------------------------------------

def run_task(task, message):
    try:
        action = message.get("action")

        if action == "download_format":
            result = download_format(
                task,
                message.get("url"),
                message.get("format_id") or message.get("formatId"),
                message.get("referer"),
            )
        else:
            result = direct_download(
                task,
                message.get("url"),
                message.get("referer"),
                message.get("filename"),
                message.get("title"),
                message.get("cookie"),
            )

        task.cleanup(failed=False)
        log.info("TASK COMPLETE id=%s", task.id)
        task.emit("complete", result=result)

    except Exception as exc:
        cancelled = (
            task.cancel_event.is_set()
            or isinstance(exc, DownloadCancelled)
        )
        log.error(
            "TASK FAILED id=%s: %s\n%s",
            task.id, clean_error(exc), traceback.format_exc(),
        )
        # Cancel OR error: delete every temp / partial / fragment file.
        task.cleanup(failed=True)
        task.emit(
            "error",
            error="Download cancelled" if cancelled else clean_error(exc),
            cancelled=cancelled,
        )

    finally:
        with TASKS_LOCK:
            TASKS.pop(task.id, None)


def handle(message):
    if not isinstance(message, dict):
        raise ValueError("Request must be a JSON object")

    action = message.get("action")
    request_id = str(message.get("request_id") or uuid.uuid4().hex)

    if action == "ping":
        return {
            "ok": True,
            "version": VERSION,
            "download_dir": str(DOWNLOAD_DIR),
            "request_id": request_id,
        }

    if action == "formats":
        result = list_formats(
            message.get("url"), message.get("referer")
        )
        result["request_id"] = request_id
        return result

    if action == "delete_file":
        target = Path(str(message.get("path") or ""))
        if not str(target) or not is_inside(DOWNLOAD_DIR, target):
            raise ValueError("Path is outside the download folder")
        deleted = False
        if target.is_file():
            deleted = remove_file(target)
        return {
            "ok": True,
            "deleted": deleted,
            "path": str(target),
            "request_id": request_id,
        }

    if action in ("download_format", "download"):
        task_id = str(message.get("task_id") or request_id)
        task = Task(task_id, request_id)

        with TASKS_LOCK:
            if task_id in TASKS:
                raise RuntimeError(
                    "A task with this ID is already running"
                )
            TASKS[task_id] = task

        threading.Thread(
            target=run_task,
            args=(task, message),
            daemon=True,
        ).start()

        return {
            "ok": True,
            "version": VERSION,
            "request_id": request_id,
            "task_id": task_id,
            "status": "started",
            "message": "Download task started",
        }

    if action in ("pause", "resume", "cancel"):
        task_id = str(message.get("task_id") or "")

        with TASKS_LOCK:
            task = TASKS.get(task_id)

        if not task:
            return {
                "ok": True,
                "request_id": request_id,
                "task_id": task_id,
                "status": "already_finished",
                "message": "Task is no longer running",
            }

        if action == "pause":
            task.paused = True
            task.resume_gate.clear()
            task.emit("state", status="paused", message="Paused")

        elif action == "resume":
            task.paused = False
            task.resume_gate.set()
            task.emit(
                "state", status="downloading", message="Resuming…"
            )

        else:
            task.cancel_event.set()
            task.resume_gate.set()
            task.emit(
                "state", status="cancelling", message="Cancelling…"
            )

        return {
            "ok": True,
            "request_id": request_id,
            "task_id": task_id,
            "status": action,
        }

    raise ValueError(f"Unsupported action: {action!r}")


def main():
    log.info(
        "HOST STARTED version=%s frozen=%s base=%s downloads=%s "
        "ffmpeg=%s ffprobe=%s connections=%s aria2=%s",
        VERSION,
        bool(getattr(sys, "frozen", False)),
        BASE_DIR,
        DOWNLOAD_DIR,
        str(FFMPEG_EXE) if FFMPEG_EXE.is_file() else "MISSING",
        str(FFPROBE_EXE) if FFPROBE_EXE.is_file() else "MISSING",
        DIRECT_MAX_CONNECTIONS,
        ARIA2_EXE.is_file(),
    )
    cleanup_orphans()

    while True:
        try:
            message = read_message()
            if message is None:
                break

            request_id = (
                str(message.get("request_id") or uuid.uuid4().hex)
                if isinstance(message, dict)
                else uuid.uuid4().hex
            )

            try:
                response = handle(message)
            except Exception as exc:
                log.error(
                    "REQUEST FAILED: %s\n%s",
                    clean_error(exc), traceback.format_exc(),
                )
                response = {
                    "ok": False,
                    "error": clean_error(exc),
                    "version": VERSION,
                    "request_id": request_id,
                }

            send_message(response)

        except (BrokenPipeError, OSError):
            log.info("Native messaging pipe closed")
            break
        except Exception:
            log.error("HOST ERROR:\n%s", traceback.format_exc())
            break


if __name__ == "__main__":
    main()