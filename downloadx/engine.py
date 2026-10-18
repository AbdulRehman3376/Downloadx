"""DownloadX engine: multi-connection (HTTP Range) downloader with pause/resume,
dynamic segment splitting, retries, queue and on-disk persistence.
Standard library only."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .util import USER_AGENT, filename_from, unique_path

CHUNK = 64 * 1024
MIN_SPLIT = 512 * 1024        # don't split segments smaller than this
CRASH_REWIND = 256 * 1024     # after a crash, re-download this much per segment
MAX_RETRIES = 8

QUEUED, DOWNLOADING, PAUSED, COMPLETED, ERROR = (
    "queued", "downloading", "paused", "completed", "error")


class Segment:
    def __init__(self, start, end, pos=None):
        self.start, self.end = start, end
        self.pos = start if pos is None else pos

    def to_dict(self):
        return {"start": self.start, "end": self.end, "pos": self.pos}


class Task:
    def __init__(self, url, dest_dir, referer="", cookie="", connections=8, filename=None):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.referer = referer
        self.cookie = cookie          # kept in memory only, never written to disk
        self.dest_dir = str(dest_dir)
        self.connections = connections
        self.filename = filename
        self.path = None              # final path
        self.size = None
        self.resumable = False
        self.segments = []
        self.status = QUEUED
        self.error = ""
        self.added = time.time()
        self.downloaded = 0
        self.speed = 0.0
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_bytes, self._last_t = 0, time.time()
        self.active_workers = 0

    # ---- helpers
    @property
    def part_path(self):
        return Path(str(self.path) + ".dxpart") if self.path else None

    def percent(self):
        if self.status == COMPLETED:
            return 100.0
        if not self.size:
            return 0.0
        return min(100.0, self.downloaded * 100.0 / self.size)

    def eta(self):
        if self.status != DOWNLOADING or not self.size or self.speed < 1:
            return None
        return (self.size - self.downloaded) / self.speed

    def to_dict(self):
        return {"id": self.id, "url": self.url, "referer": self.referer,
                "dest_dir": self.dest_dir, "connections": self.connections,
                "filename": self.filename, "path": str(self.path) if self.path else None,
                "size": self.size, "resumable": self.resumable, "status": self.status,
                "error": self.error, "added": self.added, "downloaded": self.downloaded,
                "segments": [s.to_dict() for s in self.segments]}

    @classmethod
    def from_dict(cls, d):
        t = cls(d["url"], d["dest_dir"], d.get("referer", ""), "", d.get("connections", 8),
                d.get("filename"))
        t.id = d["id"]
        t.path = Path(d["path"]) if d.get("path") else None
        t.size, t.resumable = d.get("size"), d.get("resumable", False)
        t.status, t.error, t.added = d.get("status", PAUSED), d.get("error", ""), d.get("added", 0)
        t.segments = [Segment(s["start"], s["end"], s["pos"]) for s in d.get("segments", [])]
        if t.status in (QUEUED, DOWNLOADING):
            t.status = PAUSED
        if t.status != COMPLETED:
            for s in t.segments:  # safety margin in case of a crash mid-write
                s.pos = max(s.start, s.pos - CRASH_REWIND)
        t.downloaded = d.get("downloaded", 0) if t.status == COMPLETED else t._sum_done()
        return t

    def _sum_done(self):
        return sum(max(0, s.pos - s.start) for s in self.segments)


def _open(task, start=None, end=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "identity"}
    if task.referer.startswith(("http://", "https://")):
        headers["Referer"] = task.referer
    if task.cookie:
        headers["Cookie"] = task.cookie
    if start is not None:
        headers["Range"] = f"bytes={start}-{'' if end is None else end}"
    return urllib.request.urlopen(urllib.request.Request(task.url, headers=headers), timeout=30)


def _probe(task):
    """Find size, filename and Range support using a 1-byte ranged GET."""
    with _open(task, 0, 0) as resp:
        code = resp.getcode()
        cd = resp.headers.get("Content-Disposition")
        final_url = resp.geturl()
        if code == 206:
            cr = resp.headers.get("Content-Range", "")
            total = cr.rsplit("/", 1)[-1]
            task.size = int(total) if total.isdigit() else None
            task.resumable = task.size is not None
        else:
            cl = resp.headers.get("Content-Length")
            task.size = int(cl) if cl and cl.isdigit() else None
            task.resumable = False
    if not task.path:
        name = task.filename or filename_from(final_url, cd)
        task.path = unique_path(task.dest_dir, name)


class Manager:
    def __init__(self, data_dir, default_dir, connections=8, max_active=3):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "queue.json"
        self.settings_file = self.data_dir / "settings.json"
        self.settings = {"dest_dir": str(default_dir), "connections": connections,
                         "max_active": max_active}
        self.tasks = {}
        self.lock = threading.RLock()
        self._running = True
        self._load()
        threading.Thread(target=self._loop, daemon=True, name="dx-sched").start()

    # ---- persistence
    def _load(self):
        try:
            self.settings.update(json.loads(self.settings_file.read_text("utf-8")))
        except Exception:
            pass
        try:
            for d in json.loads(self.state_file.read_text("utf-8")):
                t = Task.from_dict(d)
                self.tasks[t.id] = t
        except Exception:
            pass

    def save(self):
        with self.lock:
            data = [t.to_dict() for t in self.tasks.values()]
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        os.replace(tmp, self.state_file)

    def save_settings(self):
        self.settings_file.write_text(json.dumps(self.settings, indent=1), "utf-8")

    # ---- public API
    def add(self, url, dest_dir=None, referer="", cookie="", connections=None,
            filename=None, start=True):
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Only http/https URLs are supported")
        t = Task(url, dest_dir or self.settings["dest_dir"], referer, cookie,
                 connections or self.settings["connections"], filename)
        Path(t.dest_dir).mkdir(parents=True, exist_ok=True)
        t.status = QUEUED if start else PAUSED
        with self.lock:
            self.tasks[t.id] = t
        self.save()
        return t

    def pause(self, tid):
        t = self.tasks.get(tid)
        if t and t.status in (QUEUED, DOWNLOADING):
            t._stop.set()
            if t.status == QUEUED:
                t.status = PAUSED
            self.save()

    def resume(self, tid):
        t = self.tasks.get(tid)
        if t and t.status in (PAUSED, ERROR):
            t.error = ""
            t.status = QUEUED
            self.save()

    def remove(self, tid, delete_files=False):
        t = self.tasks.get(tid)
        if not t:
            return
        t._stop.set()
        if t._thread:
            t._thread.join(timeout=5)
        with self.lock:
            self.tasks.pop(tid, None)
        if t.part_path and t.part_path.exists():
            _try_unlink(t.part_path)
        if delete_files and t.path and t.status == COMPLETED:
            _try_unlink(t.path)
        self.save()

    def pause_all(self):
        for tid in list(self.tasks):
            self.pause(tid)

    def resume_all(self):
        for tid in list(self.tasks):
            self.resume(tid)

    def shutdown(self):
        self._running = False
        for t in list(self.tasks.values()):
            t._stop.set()
        for t in list(self.tasks.values()):
            if t._thread:
                t._thread.join(timeout=5)
        self.save()
        self.save_settings()

    def total_speed(self):
        return sum(t.speed for t in self.tasks.values() if t.status == DOWNLOADING)

    # ---- scheduler
    def _loop(self):
        last_save = 0
        while self._running:
            with self.lock:
                tasks = list(self.tasks.values())
            active = [t for t in tasks if t.status == DOWNLOADING]
            for t in tasks:
                if t.status == DOWNLOADING:
                    now = time.time()
                    dt = now - t._last_t
                    if dt >= 0.5:
                        inst = (t.downloaded - t._last_bytes) / dt
                        t.speed = inst if t.speed == 0 else 0.6 * t.speed + 0.4 * inst
                        t._last_bytes, t._last_t = t.downloaded, now
                else:
                    t.speed = 0.0
            free = self.settings["max_active"] - len(active)
            for t in sorted(tasks, key=lambda x: x.added):
                if free <= 0:
                    break
                if t.status == QUEUED and not (t._thread and t._thread.is_alive()):
                    t._stop.clear()
                    t.status = DOWNLOADING
                    t._last_bytes, t._last_t, t.speed = t.downloaded, time.time(), 0.0
                    t._thread = threading.Thread(target=self._run_task, args=(t,),
                                                 daemon=True, name=f"dx-{t.id}")
                    t._thread.start()
                    free -= 1
            if active and time.time() - last_save > 2:
                try:
                    self.save()
                except Exception:
                    pass
                last_save = time.time()
            time.sleep(0.25)

    # ---- task runner
    def _run_task(self, t):
        try:
            if not t.segments:
                _probe(t)
                self._prepare(t)
            if t.path and not t.part_path.exists():
                self._prepare(t)  # part file vanished -> start over
            workers = []
            t.active_workers = 0
            for seg in list(t.segments):
                if seg.pos <= seg.end or not t.resumable:
                    th = threading.Thread(target=self._worker, args=(t, seg), daemon=True)
                    workers.append(th)
                    th.start()
            for th in workers:
                th.join()
            if t.error:
                t.status = ERROR
            elif t._stop.is_set():
                if not t.resumable:           # can't resume without Range: restart later
                    t.segments.clear()
                    t.downloaded = 0
                t.status = PAUSED
            else:
                self._finish(t)
        except Exception as e:  # noqa
            t.error = _msg(e)
            t.status = ERROR
        finally:
            t.speed = 0.0
            self.save()

    def _prepare(self, t):
        t.part_path.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        if t.resumable and t.size:
            n = max(1, min(t.connections, t.size // MIN_SPLIT or 1))
        with open(t.part_path, "wb") as f:
            if t.size:
                f.truncate(t.size)
        t.segments = []
        if t.size:
            step = t.size // n
            for i in range(n):
                s = i * step
                e = t.size - 1 if i == n - 1 else (i + 1) * step - 1
                t.segments.append(Segment(s, e))
        else:
            t.segments.append(Segment(0, 2 ** 62))
        t.downloaded = 0

    def _finish(self, t):
        if t.size and t.downloaded < t.size:
            raise IOError(f"Incomplete download ({t.downloaded}/{t.size} bytes)")
        if not t.size:
            t.size = t.downloaded
        else:
            with open(t.part_path, "r+b") as f:
                f.truncate(t.size)
        os.replace(t.part_path, t.path)
        t.status = COMPLETED
        t.downloaded = t.size

    # ---- worker
    def _steal(self, t):
        with t.lock:
            best, rem = None, 0
            for s in t.segments:
                r = s.end - s.pos + 1
                if r > rem:
                    best, rem = s, r
            if not best or rem < 2 * MIN_SPLIT or not t.resumable:
                return None
            mid = best.pos + rem // 2
            new = Segment(mid, best.end)
            best.end = mid - 1
            t.segments.append(new)
            return new

    def _worker(self, t, seg):
        fh = None
        try:
            fh = open(t.part_path, "r+b")
            while not t._stop.is_set() and not t.error:
                self._download_segment(t, seg, fh)
                if t._stop.is_set() or t.error:
                    break
                nxt = self._steal(t)
                if not nxt:
                    break
                seg = nxt
        except Exception as e:  # noqa
            if not t.error:
                t.error = _msg(e)
            t._stop.set()   # tell the other workers to stop too
        finally:
            if fh:
                fh.close()

    def _download_segment(self, t, seg, fh):
        retries = 0
        while not t._stop.is_set():
            with t.lock:
                if seg.pos > seg.end:
                    return
                start, end = seg.pos, seg.end
            try:
                open_end = None if end >= 2 ** 62 else end
                resp = _open(t, start, open_end) if (t.resumable or start > 0) else _open(t)
                with resp:
                    if t.resumable and resp.getcode() != 206:
                        raise IOError("Server stopped honouring Range requests")
                    while not t._stop.is_set():
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        with t.lock:
                            room = seg.end - seg.pos + 1
                            if room <= 0:
                                return          # another worker took the rest
                            n = min(len(chunk), room)
                            at = seg.pos
                            seg.pos += n
                            t.downloaded += n
                        fh.seek(at)
                        fh.write(chunk[:n] if n < len(chunk) else chunk)
                        retries = 0
                        if n < len(chunk):
                            return
                with t.lock:
                    if seg.pos <= seg.end and not t._stop.is_set():
                        if seg.end >= 2 ** 62:   # unknown size: EOF means done
                            seg.end = seg.pos - 1
                        else:
                            raise IOError("Connection closed early")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404, 410, 416):
                    raise IOError(f"HTTP {e.code} {e.reason}")
                retries += 1
                if retries > MAX_RETRIES:
                    raise
                time.sleep(min(2 ** retries, 15))
            except Exception:
                if t._stop.is_set():
                    return
                retries += 1
                if retries > MAX_RETRIES:
                    raise
                time.sleep(min(2 ** retries, 15))


def _msg(e):
    return f"{type(e).__name__}: {e}"[:200]


def _try_unlink(p):
    try:
        Path(p).unlink()
    except OSError:
        pass
