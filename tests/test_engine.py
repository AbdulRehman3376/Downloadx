"""Run:  python tests/test_engine.py   (spins up a local Range-capable server)"""
import hashlib, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from downloadx.engine import Manager, COMPLETED, PAUSED, ERROR

DATA = os.urandom(6 * 1024 * 1024 + 123)
SLOW = {"on": False}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        body, code, hdr = DATA, 200, {}
        rng = self.headers.get("Range")
        if rng and not self.path.startswith("/norange"):
            a, b = rng.replace("bytes=", "").split("-")
            a = int(a); b = int(b) if b else len(DATA) - 1
            body, code = DATA[a:b + 1], 206
            hdr["Content-Range"] = f"bytes {a}-{b}/{len(DATA)}"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="test file.bin"')
        for k, v in hdr.items(): self.send_header(k, v)
        self.end_headers()
        try:
            for i in range(0, len(body), 32768):
                self.wfile.write(body[i:i + 32768])
                if SLOW["on"]: time.sleep(0.1)
        except Exception:
            pass


def wait(t, states, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if t.status in states: return
        time.sleep(0.1)
    raise AssertionError(f"timeout, status={t.status} err={t.error}")


def md5(p): return hashlib.md5(Path(p).read_bytes()).hexdigest()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    want = hashlib.md5(DATA).hexdigest()
    tmp = Path(tempfile.mkdtemp())
    m = Manager(tmp / "data", tmp / "dl")

    t = m.add(base + "/file"); wait(t, {COMPLETED})
    assert md5(t.path) == want and t.path.name == "test file.bin", "multi-conn download"
    print("PASS multi-connection download, segments:", len(t.segments))

    SLOW["on"] = True
    t = m.add(base + "/file"); time.sleep(0.6)
    m.pause(t.id); wait(t, {PAUSED}); got = t.downloaded
    assert 0 < got < len(DATA), got
    time.sleep(0.5); assert t.downloaded == got, "must stay paused"
    m.resume(t.id); wait(t, {COMPLETED})
    assert md5(t.path) == want, "resume integrity"
    print("PASS pause/resume integrity (paused at", got, "bytes)")

    t = m.add(base + "/file"); time.sleep(0.6); m.pause(t.id); wait(t, {PAUSED}); m.save()
    m.shutdown()
    m2 = Manager(tmp / "data", tmp / "dl")
    t2 = m2.tasks[t.id]; assert t2.status == PAUSED
    m2.resume(t2.id); wait(t2, {COMPLETED})
    assert md5(t2.path) == want, "restart resume integrity"
    print("PASS resume after app restart")
    SLOW["on"] = False

    t = m2.add(base + "/norange"); wait(t, {COMPLETED})
    assert md5(t.path) == want and not t.resumable
    print("PASS server without Range support (single stream)")

    t = m2.add(base + "/missing"); wait(t, {ERROR}, 20)
    print("PASS 404 reported as error:", t.error)

    SLOW["on"] = True
    ts = [m2.add(base + "/file") for _ in range(5)]
    time.sleep(0.8)
    assert sum(1 for x in ts if x.status == "downloading") <= 3, "queue limit"
    for x in ts: wait(x, {COMPLETED}, 120)
    assert all(md5(x.path) == want for x in ts)
    print("PASS queue (max 3 simultaneous), 5 files intact")
    m2.shutdown(); print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
