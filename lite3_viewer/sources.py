"""画面信号源：RealSense 直连 / ROS 图像话题。

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
                "无法导入 pyrealsense2（%s）。请确认感知主机已安装 librealsense 的 Python 绑定；"
                "若装不上，改用 --source ros 从 realsense-ros 话题取图。" % exc
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
    """name: auto / realsense / ros。

    auto 只是给一个建议顺序（装了 pyrealsense2 就直连，否则走 ROS 话题），
    真正打不开时由 run.py 按 realsense → ros 的顺序重试，失败就报错退出。
    """
    name = (name or "auto").lower()
    if name == "realsense":
        return RealSenseSource()
    if name == "ros":
        return RosSource(
            color_topic=kwargs.get("color_topic", "/camera/color/image_raw"),
            depth_topic=kwargs.get("depth_topic", "/camera/aligned_depth_to_color/image_raw"),
        )
    # auto
    try:
        import pyrealsense2  # noqa: F401

        return RealSenseSource()
    except Exception:
        return RosSource(
            color_topic=kwargs.get("color_topic", "/camera/color/image_raw"),
            depth_topic=kwargs.get("depth_topic", "/camera/aligned_depth_to_color/image_raw"),
        )
