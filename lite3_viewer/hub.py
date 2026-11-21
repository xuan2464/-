"""取帧线程 + 帧分发 + 各显示模式的 JPEG 缓存。

架构：一个后台线程持续从信号源取最新帧写入 self._frame，
所有 HTTP 客户端（/stream）都读取同一帧并按模式编码，编码结果按帧号缓存，
因此多开几个网页也不会成倍消耗 CPU。
"""

import threading
import time
from collections import deque

from . import encoder

MODES = ("color", "depth", "dual")


class Frame(object):
    __slots__ = ("id", "ts", "color", "depth")

    def __init__(self, fid, ts, color, depth):
        self.id = fid
        self.ts = ts
        self.color = color
        self.depth = depth


class Hub(object):
    def __init__(self, factory, width=640, height=480, fps=30, quality=80,
                 depth_near=0.2, depth_far=2.5, max_log=60):
        self._factory = factory
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.quality = int(quality)
        self.depth_near = float(depth_near)
        self.depth_far = float(depth_far)

        self._cond = threading.Condition()
        self._frame = None
        self._source = None
        self._source_name = "未连接"
        self._source_kind = "-"
        self._has_depth = False
        self._stop = threading.Event()
        self._thread = None
        self._reopen = False

        self._enc_lock = threading.Lock()
        self._enc_cache = {}

        self._t0 = time.time()
        self.started_at = time.time()
        self.total_frames = 0
        self.dropped = 0
        self._stamps = deque(maxlen=40)
        self.last_error = ""
        self.logs = deque(maxlen=max_log)

        self.clients = {}  # cid -> dict，由 server 维护
        self._clients_lock = threading.Lock()

        self.log("info", "服务启动，等待信号源")

    # ------------------------------------------------------------------ log
    def log(self, level, msg):
        self.logs.append({"t": time.time(), "level": level, "msg": msg})

    # --------------------------------------------------------------- thread
    def start(self):
        self._thread = threading.Thread(target=self._run, name="frame-hub")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            if self._source is None or self._reopen:
                self._reopen = False
                if self._source is not None:
                    try:
                        self._source.close()
                    except Exception:
                        pass
                    self._source = None
                try:
                    src = self._factory()
                    src.open(self.width, self.height, self.fps)
                    with self._cond:
                        self._source = src
                        self._source_name = src.label
                        self._source_kind = src.kind
                        self._has_depth = bool(src.has_depth)
                    self.log("ok", "信号源已连接：%s（%dx%d @%d）"
                             % (src.label, self.width, self.height, self.fps))
                    backoff = 0.5
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log("error", str(exc))
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                    continue

            try:
                pair = self._source.read()
            except Exception as exc:
                self.last_error = str(exc)
                self.log("error", "读取帧失败：%s" % exc)
                self._reopen = True
                time.sleep(0.5)
                continue

            if not pair or pair[0] is None:
                self.dropped += 1
                time.sleep(0.01)
                continue

            color, depth = pair
            now = time.time()
            with self._cond:
                self._frame = Frame(
                    self.total_frames + 1, now, color, depth if self._has_depth else None
                )
                self.total_frames += 1
                self._stamps.append(now)
                self._cond.notify_all()
            backoff = 0.5

    # ---------------------------------------------------------------- frame
    def latest(self):
        with self._cond:
            return self._frame

    def wait_next(self, after_id, timeout=10.0):
        with self._cond:
            self._cond.wait_for(
                lambda: self._stop.is_set()
                or (self._frame is not None and self._frame.id != after_id),
                timeout,
            )
            return self._frame

    def capture_fps(self):
        s = self._stamps
        if len(s) < 2:
            return 0.0
        span = s[-1] - s[0]
        return (len(s) - 1) / span if span > 0 else 0.0

    def frame_age_ms(self):
        f = self._frame
        return 0.0 if f is None else (time.time() - f.ts) * 1000.0

    # --------------------------------------------------------------- encode
    def render(self, mode):
        """返回 (jpeg_bytes, mime) 或 (None, None)。"""
        mode = mode if mode in MODES else "color"
        frame = self.latest()
        if frame is None:
            return None, None
        with self._enc_lock:
            hit = self._enc_cache.get(mode)
            if hit and hit[0] == frame.id:
                return hit[1], "image/jpeg"

        if mode == "depth":
            if frame.depth is None:
                img = frame.color
                mode = "color"
            else:
                img = encoder.depth_to_rgb(
                    frame.depth, self.depth_near, self.depth_far
                )
        elif mode == "dual" and frame.depth is not None:
            img = encoder.side_by_side(
                frame.color,
                encoder.depth_to_rgb(frame.depth, self.depth_near, self.depth_far),
            )
        else:
            img = frame.color

        data = encoder.encode_jpeg(img, self.quality)
        with self._enc_lock:
            self._enc_cache[mode] = (frame.id, data)
        return data, "image/jpeg"

    # ----------------------------------------------------------- configure
    def reconfigure(self, width=None, height=None, fps=None, quality=None,
                    depth_near=None, depth_far=None):
        changed = False
        with self._cond:
            if width is not None and int(width) != self.width:
                self.width = int(width)
                changed = True
            if height is not None and int(height) != self.height:
                self.height = int(height)
                changed = True
            if fps is not None and int(fps) != self.fps:
                self.fps = int(fps)
                changed = True
            if depth_near is not None and float(depth_near) != self.depth_near:
                self.depth_near = float(depth_near)
                changed = True
            if depth_far is not None and float(depth_far) != self.depth_far:
                self.depth_far = float(depth_far)
                changed = True
            if quality is not None:
                self.quality = int(quality)
            if changed:
                self._reopen = True
                if self._source is not None:
                    try:
                        self._source.close()
                    except Exception:
                        pass
                    self._source = None
                self._cond.notify_all()
        if changed:
            self.log("info", "参数变更，正在重连信号源（%dx%d@%d）"
                     % (self.width, self.height, self.fps))
        return changed

    # -------------------------------------------------------------- status
    def status(self, cid=None):
        with self._cond:
            has_frame = self._frame is not None
            fid = self._frame.id if has_frame else 0
            src_name = self._source_name
            src_kind = self._source_kind
            has_depth = self._has_depth
        client = None
        if cid:
            with self._clients_lock:
                c = self.clients.get(cid)
                if c:
                    elapsed = max(time.time() - c["t0"], 1e-6)
                    client = {
                        "id": cid,
                        "connected": True,
                        "frames": c["frames"],
                        "fps": c["frames"] / elapsed,
                        "bytes": c["bytes"],
                        "kbps": c["bytes"] * 8.0 / 1000.0 / elapsed,
                    }
        return {
            "ok": has_frame,
            "source": {
                "kind": src_kind,
                "name": src_name,
                "has_depth": has_depth,
                "width": self.width,
                "height": self.height,
                "target_fps": self.fps,
            },
            "frame": {
                "id": fid,
                "age_ms": self.frame_age_ms(),
                "capture_fps": self.capture_fps(),
                "total": self.total_frames,
                "dropped": self.dropped,
            },
            "stream": {
                "quality": self.quality,
                "depth_near": self.depth_near,
                "depth_far": self.depth_far,
                "encoder": encoder.encoder_name(),
            },
            "uptime_s": time.time() - self.started_at,
            "last_error": self.last_error,
            "client": client,
            "log": list(self.logs),
        }


# ----------------------------------------------------------------- clients
class Clients(object):
    """记录每个浏览器连接的成绩（帧数 / 字节数），用于状态面板。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def touch(self, cid, mode="color"):
        if not cid:
            return
        with self._lock:
            c = self._data.get(cid)
            if not c:
                self._data[cid] = {
                    "t0": time.time(), "frames": 0, "bytes": 0, "mode": mode,
                    "last": time.time(),
                }
            else:
                c["last"] = time.time()
                c["mode"] = mode

    def frame(self, cid, nbytes):
        if not cid:
            return
        with self._lock:
            c = self._data.get(cid)
            if c:
                c["frames"] += 1
                c["bytes"] += nbytes

    def drop(self, cid):
        with self._lock:
            self._data.pop(cid, None)

    def snapshot(self, cid):
        with self._lock:
            c = self._data.get(cid)
            if not c:
                return None
            elapsed = max(time.time() - c["t0"], 1e-6)
            return {
                "id": cid,
                "connected": (time.time() - c["last"]) < 5.0,
                "mode": c["mode"],
                "frames": c["frames"],
                "fps": c["frames"] / elapsed,
                "kbps": c["bytes"] * 8.0 / 1000.0 / elapsed,
            }

    def count(self):
        with self._lock:
            return len(self._data)
