"""图像编码：RGB -> JPEG，深度 -> 伪彩色。

优先使用 Pillow，其次 OpenCV。两者都没有时给出明确提示。
机器人（Jetson）上一般已带 python3-opencv / python3-pil。
"""

import io

import numpy as np

try:  # Pillow
    from PIL import Image

    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False

try:  # OpenCV
    import cv2

    _HAS_CV2 = True
except Exception:  # pragma: no cover
    _HAS_CV2 = False


# 近红 -> 黄 -> 绿 -> 蓝，接近 RealSense Viewer 的 JET 观感
_STOPS = [
    (0.00, (255, 46, 26)),
    (0.22, (255, 140, 0)),
    (0.45, (255, 226, 0)),
    (0.66, (54, 222, 128)),
    (0.84, (36, 152, 242)),
    (1.00, (126, 92, 255)),
]


def _build_lut(n=256):
    lut = np.zeros((n, 3), dtype=np.float32)
    xs = np.linspace(0.0, 1.0, n, dtype=np.float32)
    pos = np.array([s[0] for s in _STOPS], dtype=np.float32)
    colors = np.array([s[1] for s in _STOPS], dtype=np.float32)
    for c in range(3):
        lut[:, c] = np.interp(xs, pos, colors[:, c])
    return lut.astype(np.uint8)


_LUT = _build_lut()


def encoder_name():
    if _HAS_PIL:
        return "pillow"
    if _HAS_CV2:
        return "opencv"
    return "none"


def encode_jpeg(rgb, quality=80):
    """rgb: uint8 (H, W, 3) -> JPEG bytes。"""
    if rgb is None:
        raise ValueError("no frame")
    if rgb.ndim == 2:
        rgb = np.dstack([rgb, rgb, rgb])
    if not rgb.flags["C_CONTIGUOUS"]:
        rgb = np.ascontiguousarray(rgb)

    if _HAS_PIL:
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, format="JPEG", quality=int(quality), subsampling=1)
        return buf.getvalue()

    if _HAS_CV2:
        ok, buf = cv2.imencode(
            ".jpg", rgb[:, :, ::-1], [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        if not ok:
            raise RuntimeError("OpenCV JPEG 编码失败")
        return buf.tobytes()

    raise RuntimeError(
        "缺少图像编码库：请安装 Pillow (`python3 -m pip install pillow`) 或 OpenCV"
    )


def depth_to_rgb(depth_m, near=0.2, far=2.5, invert=False):
    """米制深度图 -> 伪彩色 uint8 (H, W, 3)。0 / NaN 视为无效像素。"""
    if depth_m is None:
        return None
    d = np.asarray(depth_m, dtype=np.float32)
    if d.ndim != 2:
        d = d[..., 0]
    span = max(float(far) - float(near), 1e-6)
    norm = (d - float(near)) / span
    np.clip(norm, 0.0, 1.0, out=norm)
    if invert:
        norm = 1.0 - norm
    idx = (norm * 255.0).astype(np.uint8)
    rgb = _LUT[idx]
    invalid = ~np.isfinite(d) | (d <= 0.0)
    if invalid.any():
        rgb[invalid] = (12, 14, 16)
    return np.ascontiguousarray(rgb)


def side_by_side(left, right, gap=2, gap_color=(18, 22, 24)):
    """把两幅同高画面拼成左右双画面。"""
    if left is None:
        return right
    if right is None:
        return left
    h = min(left.shape[0], right.shape[0])
    left = left[:h]
    right = right[:h]
    bar = np.empty((h, gap, 3), dtype=np.uint8)
    bar[...] = gap_color
    return np.ascontiguousarray(np.hstack([left, bar, right]))
