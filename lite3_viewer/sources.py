"""画面信号源：合成演示 / RealSense 直连 / ROS 图像话题。

每个信号源都实现 open(width, height, fps) / read() / close()，
read() 返回 (color_rgb, depth_m)：
    color_rgb  uint8  (H, W, 3)
    depth_m    float32 (H, W)，单位米；没有深度时为 None
读取超时返回 None，由上层决定是否重试。
"""

import threading
import time

import numpy as np


class SourceError(RuntimeError):
    """信号源无法打开或运行。"""


class BaseSource(object):
    kind = "base"
    label = "未知信号源"
    has_depth = False

    def open(self, width, height, fps):
        raise NotImplementedError

    def read(self):
        raise NotImplementedError

    def close(self):
        pass


# --------------------------------------------------------------------------
# 合成演示源：没有机器狗也能验证整条链路（服务端编码 / MJPEG / 前端）
# 所有与时间无关的几何量都在 open() 里预计算成 float32 缓冲，逐帧只做少量运算，
# 这样 640x480 也能轻松跑满 30fps。
# --------------------------------------------------------------------------
class DemoSource(BaseSource):
    kind = "demo"
    label = "合成演示信号源"
    has_depth = True

    def open(self, width, height, fps):
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1, int(fps))
        self._t0 = time.time()
        self._n = 0
        self._last = 0.0
        self._next = time.time()

        h, w = self.height, self.width
        yy, xx = np.mgrid[0:h, 0:w]
        yy = yy.astype(np.float32)
        xx = xx.astype(np.float32)
        self._yy, self._xx = yy, xx
        self._rows = np.arange(h, dtype=np.float32)

        horizon = h * 0.40
        below = yy > horizon
        dy = np.maximum(yy - horizon, 1.0)
        z = np.clip((0.55 * h) / dy, 0.3, 24.0).astype(np.float32)
        z[~below] = 24.0

        # 世界坐标（相机朝前）
        xw = ((xx - w * 0.5) * z / (0.9 * w)).astype(np.float32)

        # 静态底色：天空渐变 + 地面 + 地平线辉光 + 暗角
        sky_t = np.clip(yy / horizon, 0.0, 1.0)[..., None].astype(np.float32)
        sky = (np.array([7, 12, 18], np.float32) * (1.0 - sky_t)
               + np.array([20, 44, 52], np.float32) * sky_t)
        ground = (np.array([17, 25, 27], np.float32) + 11.0 * np.exp(-z / 6.0)[..., None])
        base = np.where(below[..., None], ground, sky).astype(np.float32)

        glow = np.exp(-((yy - horizon) ** 2) / (2 * (h * 0.013) ** 2))[..., None]
        base += glow * np.array([26, 110, 126], np.float32) * 0.55

        # 天空里的稀疏星点
        rng = np.random.default_rng(7)
        stars = rng.random((h, w)).astype(np.float32)
        star_mask = (stars > 0.99955) & (~below)
        base[star_mask] += 90.0

        halo = np.clip(
            1.0 - 0.30 * (((xx - w * 0.5) / (w * 0.5)) ** 2
                          + ((yy - h * 0.5) / (h * 0.5)) ** 2) / 2.0, 0.6, 1.0
        )[..., None]
        base *= halo
        self._base = base

        # 网格：X 方向相位固定；Z 方向相位随前进滚动
        self._fx = np.abs(((xw / 0.5) % 1.0) - 0.5).astype(np.float32)
        self._zmod = ((z / 0.5) % 1.0).astype(np.float32)
        self._fade = (np.exp(-z / 4.5) * below).astype(np.float32)
        self._grid_col = np.array([54, 232, 186], np.float32) * 0.85

        # 深度底图（与时间无关）
        self._depth_base = np.where(below, z, 12.0).astype(np.float32)

        # 噪声贴图，逐帧平移复用，避免每帧生成百万随机数
        self._noise = rng.normal(0.0, 1.8, (96, 96, 3)).astype(np.float32)

    # ------------------------------------------------------------------
    def _blob(self, img, cx, cy, r, col, gain=0.9):
        h, w = img.shape[0], img.shape[1]
        pad = int(r * 2.6) + 2
        x0, x1 = max(0, int(cx) - pad), min(w, int(cx) + pad + 1)
        y0, y1 = max(0, int(cy) - pad), min(h, int(cy) + pad + 1)
        if x1 <= x0 or y1 <= y0:
            return
        xs = self._xx[y0:y1, x0:x1]
        ys = self._yy[y0:y1, x0:x1]
        g = np.exp(-(((xs - cx) ** 2 + (ys - cy) ** 2) / (2.0 * r * r)))
        img[y0:y1, x0:x1] += g[..., None] * np.array(col, np.float32) * gain

    def _disc(self, arr, cx, cy, r, value):
        h, w = arr.shape
        pad = int(r) + 2
        x0, x1 = max(0, int(cx) - pad), min(w, int(cx) + pad + 1)
        y0, y1 = max(0, int(cy) - pad), min(h, int(cy) + pad + 1)
        if x1 <= x0 or y1 <= y0:
            return
        xs = self._xx[y0:y1, x0:x1]
        ys = self._yy[y0:y1, x0:x1]
        arr[y0:y1, x0:x1][((xs - cx) ** 2 + (ys - cy) ** 2) < r * r] = value

    # ------------------------------------------------------------------
    def read(self):
        # 绝对时基节流：按计划时刻出帧，渲染耗时不会被叠加成掉帧
        interval = 1.0 / float(self.fps)
        now = time.time()
        if self._next > now:
            time.sleep(self._next - now)
            now = time.time()
        self._next += interval
        if self._next < now:  # 落后太多就直接对齐，不补帧
            self._next = now
        self._last = now

        t = self._last - self._t0
        h, w = self.height, self.width

        # 网格线（Z 相位滚动）
        fz = np.abs(((self._zmod + np.float32(t * 3.2)) % 1.0) - 0.5)
        dist = np.minimum(self._fx, fz)
        line = np.clip(1.0 - dist * 28.0, 0.0, 1.0) * self._fade
        img = self._base + line[..., None] * self._grid_col

        # 两个漂浮光球：直观显示画面在实时刷新
        bob = 0.5 + 0.26 * np.sin(t * 0.7)
        bocy = 0.60 + 0.05 * np.sin(t * 1.4)
        self._blob(img, bob * w, bocy * h, 0.055 * h, (40, 205, 255), 0.95)
        b2x = 0.30 + 0.14 * np.sin(-t * 0.45 + 1.3)
        b2y = 0.72 + 0.04 * np.cos(t * 0.9)
        self._blob(img, b2x * w, b2y * h, 0.033 * h, (255, 172, 60), 0.9)

        # 向下扫过的高亮带
        scan_y = (t * 90.0) % (h + 120) - 60
        band = np.exp(-((self._rows - scan_y) ** 2) / (2 * (h * 0.0055) ** 2))
        img += band[:, None, None] * np.array([20, 76, 70], np.float32)

        # 轻微噪声（贴图平移复用）
        off = int(t * 613) % 96
        nz = np.roll(self._noise, (off, off // 3), axis=(0, 1))
        img += np.tile(nz, (h // 96 + 2, w // 96 + 2, 1))[:h, :w]

        rgb = np.clip(img, 0.0, 255.0).astype(np.uint8)

        # 深度图：地面按距离，光球处偏近
        depth = self._depth_base.copy()
        self._disc(depth, bob * w, bocy * h, 0.055 * h, 0.9)

        self._n += 1
        return rgb, depth


# --------------------------------------------------------------------------
# RealSense D435i 直连（librealsense / pyrealsense2）
# --------------------------------------------------------------------------
class RealSenseSource(BaseSource):
    kind = "realsense"
    label = "Intel RealSense D435i"
    has_depth = True

    def open(self, width, height, fps):
        try:
            import pyrealsense2 as rs
        except Exception as exc:
            raise SourceError(
                "无法导入 pyrealsense2（%s）。请确认感知主机已安装 librealsense 的 Python 绑定，"
                "或用 --source demo 先查看网页效果。" % exc
            )

        self._rs = rs
        self._pipeline = rs.pipeline()
        self._align = rs.align(rs.stream.color)
        cfg = rs.config()
        cfg.enable_stream(rs.stream.depth, int(width), int(height), rs.format.z16, int(fps))
        cfg.enable_stream(rs.stream.color, int(width), int(height), rs.format.bgr8, int(fps))
        try:
            self._pipeline.start(cfg)
        except Exception:
            # 常见原因：分辨率/帧率组合不支持，或 realsense 服务正占用设备
            cfg = rs.config()
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            try:
                self._pipeline.start(cfg)
            except Exception as exc:
                raise SourceError(
                    "打开 RealSense 失败：%s\n"
                    "排查：1) realsense-viewer 能否看到画面；"
                    "2) 若 realsense 服务在跑（sudo systemctl status realsense），先 "
                    "sudo systemctl stop realsense 释放设备；3) USB 线是否插紧。" % exc
                )
        for _ in range(8):  # 预热，丢弃自动曝光未稳定的前几帧
            try:
                self._pipeline.wait_for_frames(1000)
            except Exception:
                break

    def read(self):
        try:
            frames = self._pipeline.wait_for_frames(2000)
        except Exception:
            return None
        aligned = self._align.process(frames)
        cf = aligned.get_color_frame()
        if not cf:
            return None
        color = np.asanyarray(cf.get_data())[:, :, ::-1].copy()
        depth = None
        df = aligned.get_depth_frame()
        if df:
            depth = np.asanyarray(df.get_data()).astype(np.float32) * 0.001
        return color, depth

    def close(self):
        try:
            self._pipeline.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------
# ROS1 图像话题（realsense-ros 已经在跑的时候用这个，不抢设备）
# --------------------------------------------------------------------------
class RosSource(BaseSource):
    kind = "ros"
    label = "ROS 图像话题"
    has_depth = True

    def __init__(self, color_topic="/camera/color/image_raw",
                 depth_topic="/camera/aligned_depth_to_color/image_raw"):
        self.color_topic = color_topic
        self.depth_topic = depth_topic
        # 用 Condition + 序号：read() 阻塞到"新的一帧"，避免空转重复编码同一帧
        self._cond = threading.Condition()
        self._color = None
        self._depth = None
        self._color_ts = 0.0
        self._depth_ts = 0.0
        self._seq = 0
        self._seen = 0

    def open(self, width, height, fps):
        try:
            import rospy
            from sensor_msgs.msg import Image
        except Exception as exc:
            raise SourceError(
                "无法导入 rospy（%s）。ROS1 用法需要在已 source 的 ROS 环境里启动，"
                "或改用 --source realsense。" % exc
            )
        self._rospy = rospy
        rospy.init_node("lite3_web_viewer", anonymous=True, disable_signals=True)
        self._sub_c = rospy.Subscriber(self.color_topic, Image, self._on_color, queue_size=1,
                                       buff_size=2 ** 24)
        self._sub_d = None
        if self.depth_topic:
            self._sub_d = rospy.Subscriber(self.depth_topic, Image, self._on_depth, queue_size=1,
                                           buff_size=2 ** 24)

    def _on_color(self, msg):
        arr = _ros_image_to_numpy(msg)
        if arr is None:
            return
        with self._cond:
            self._color = arr
            self._color_ts = time.time()
            self._seq += 1
            self._cond.notify_all()

    def _on_depth(self, msg):
        arr = _ros_image_to_numpy(msg)
        if arr is None:
            return
        if arr.dtype == np.uint16:
            arr = arr.astype(np.float32) * 0.001
        with self._cond:
            self._depth = arr
            self._depth_ts = time.time()
            self._cond.notify_all()

    def read(self):
        with self._cond:
            if self._seq == self._seen:
                self._cond.wait(1.0)
            if self._seq == self._seen:
                return None
            self._seen = self._seq
            color = None if self._color is None else self._color.copy()
            depth = None if self._depth is None else self._depth.copy()
        if color is None:
            return None
        return color, depth


def _ros_image_to_numpy(msg):
    try:
        h, w = int(msg.height), int(msg.width)
        enc = (msg.encoding or "").lower()
        channels = 3 if enc in ("rgb8", "bgr8") else 1
        if enc in ("16uc1", "mono16"):
            dtype = np.uint16
            channels = 1
        else:
            dtype = np.uint8
        buf = np.frombuffer(msg.data, dtype=dtype)
        usable = (msg.step // (dtype().itemsize * channels)) or w
        arr = buf.reshape(h, usable, channels)[:, :w]
        if channels == 1:
            return arr[:, :, 0].copy()
        arr = arr[:, :, ::-1].copy() if enc == "bgr8" else arr.copy()
        return arr
    except Exception:
        return None


# --------------------------------------------------------------------------
def create_source(name, **kwargs):
    name = (name or "auto").lower()
    if name == "demo":
        return DemoSource()
    if name == "realsense":
        return RealSenseSource()
    if name == "ros":
        return RosSource(
            color_topic=kwargs.get("color_topic", "/camera/color/image_raw"),
            depth_topic=kwargs.get("depth_topic", "/camera/aligned_depth_to_color/image_raw"),
        )
    # auto：优先真实相机，失败由 run.py 兜底到 demo
    try:
        import pyrealsense2  # noqa: F401

        return RealSenseSource()
    except Exception:
        return DemoSource()
