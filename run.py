#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绝影 Lite3 相机网页实时画面 —— 启动脚本。

用法示例：
    python3 run.py                        # 自动选择信号源（RealSense 优先，失败退回演示）
    python3 run.py --source demo          # 无机器狗，先看网页效果
    python3 run.py --source realsense     # 直连 D435i
    python3 run.py --source ros           # 订阅 realsense-ros 已发布的图像话题
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# 本目录下的 .deps 是免安装的依赖兜底（例如没有管理员权限时 pip --target 安装 Pillow）；
# 也支持放在上一级目录，方便把代码单独放到子目录里运行
for _cand in (os.path.join(_HERE, ".deps"),
              os.path.join(os.path.dirname(_HERE), ".deps")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.append(_cand)

from lite3_viewer import encoder, hub, motion, server, sources  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="绝影 Lite3 相机网页实时画面（MJPEG over HTTP）"
    )
    p.add_argument("--source", default="auto",
                   choices=["auto", "realsense", "ros", "demo"],
                   help="信号源：auto / realsense / ros / demo（默认 auto）")
    p.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    p.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    p.add_argument("--width", type=int, default=640, help="画面宽度（默认 640）")
    p.add_argument("--height", type=int, default=480, help="画面高度（默认 480）")
    p.add_argument("--fps", type=int, default=30, help="目标帧率（默认 30）")
    p.add_argument("--quality", type=int, default=80, help="JPEG 质量 30-95（默认 80）")
    p.add_argument("--depth-near", type=float, default=0.2, help="深度伪彩近端，单位米")
    p.add_argument("--depth-far", type=float, default=2.5, help="深度伪彩远端，单位米")
    p.add_argument("--ros-color", default="/camera/color/image_raw",
                   help="ROS 彩色图话题")
    p.add_argument("--ros-depth", default="/camera/aligned_depth_to_color/image_raw",
                   help="ROS 对齐深度图话题（留空则不订阅深度）")
    p.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p.add_argument("--motion-ip", default="192.168.1.120",
                   help="运动主机 IP（默认 192.168.1.120）")
    p.add_argument("--motion-port", type=int, default=43893,
                   help="运动主机指令端口（默认 43893）")
    p.add_argument("--no-motion", action="store_true",
                   help="关闭运动控制，只做画面监视")
    p.add_argument("--api-token", default="",
                   help="给第三方网站调用 API 的令牌（留空=不校验；"
                        "开启后写操作需带 X-API-Token 头或 ?token= 参数）")
    return p.parse_args(argv)


def banner(httpd, cfg, source_label, motion_client=None, api_token=""):
    host, port = httpd.server_address[0], httpd.server_address[1]
    lines = []
    lines.append("")
    lines.append("  绝影 Lite3 · 相机实时画面服务")
    lines.append("  " + "-" * 46)
    lines.append("  信号源   %s" % source_label)
    lines.append("  分辨率   %dx%d @ %d fps  · JPEG q%d · %s"
                 % (cfg["width"], cfg["height"], cfg["fps"], cfg["quality"],
                    encoder.encoder_name()))
    if motion_client is not None and motion_client.enabled:
        lines.append("  运动控制 已启用 → %s（网页「控制」页）"
                     % (motion_client.resolved_target or motion_client.host))
    else:
        lines.append("  运动控制 未启用（只做画面监视）")
    lines.append("  本机访问 http://127.0.0.1:%d" % port)
    for ip in server.lan_addresses():
        lines.append("  局域网   http://%s:%d" % (ip, port))
    lines.append("  接口文档 http://127.0.0.1:%d/api/v1%s"
                 % (port, "（需 X-API-Token）" if api_token else ""))
    lines.append("  " + "-" * 46)
    lines.append("  Ctrl+C 退出")
    lines.append("")
    print("\n".join(lines), flush=True)


def main(argv=None):
    args = parse_args(argv)

    if encoder.encoder_name() == "none":
        sys.exit(
            "缺少图像编码库。请先安装其一：\n"
            "  python3 -m pip install pillow\n"
            "  sudo apt install python3-opencv"
        )

    cfg = {
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "quality": max(30, min(95, args.quality)),
        "depth_near": args.depth_near,
        "depth_far": args.depth_far,
    }

    attempts = [0]

    def factory():
        attempts[0] += 1
        if args.source == "auto":
            if attempts[0] > 2:
                print("  [warn] 真实相机打不开，已切换到合成演示信号源；"
                      "用 --source realsense 可单独查看报错原因", flush=True)
                return sources.DemoSource()
            return sources.create_source("realsense")
        return sources.create_source(
            args.source,
            color_topic=args.ros_color,
            depth_topic=args.ros_depth,
        )

    h = hub.Hub(factory, **cfg)
    clients = hub.Clients()
    h.start()

    motion_client = motion.MotionClient(
        host=args.motion_ip, port=args.motion_port,
        enabled=not args.no_motion,
        log=lambda level, msg: h.log(level, msg) if hasattr(h, "log") else None,
    )
    motion_client.start()

    httpd = server.serve(h, clients, cfg, host=args.host, port=args.port,
                         motion=motion_client, api_token=args.api_token)
    banner(httpd, cfg, sources.create_source(args.source).label if args.source != "auto"
           else "自动选择（RealSense 优先）", motion_client, args.api_token)

    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d" % args.port)).start()

    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n  正在退出…")
    finally:
        h.stop()
        motion_client.close()
        httpd.server_close()


if __name__ == "__main__":
    main()
