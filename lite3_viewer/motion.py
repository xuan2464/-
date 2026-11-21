#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运动控制：向运动主机发送 Lite3 UDP 指令。

协议来自官方文档「通信架构」：
    简单指令  [code:uint32][value:uint32][type=0:uint32]   共 12 字节，小端
    复杂指令  [code][len][type=1] + data
指令下发目标为运动主机（默认 192.168.1.120）的 43893 端口。

安全约束（照文档实现）：
- 轴指令下发频率不低于 20 Hz，机器人 250 ms 收不到就自己停 → 这里固定 25 Hz。
- 轴指令有死区，落在死区内的值视作 0。
- 只有「已启用」状态才发轴指令；点 STOP 会立即清零并解除启用。
"""

import socket
import struct
import threading
import time

HEARTBEAT = 0x21040001
SEND_HZ = 25.0
AXIS_TIMEOUT = 0.40      # 前端超过这个时间没更新，就把轴指令归零
HEARTBEAT_HZ = 2.0

# 名称 -> 指令码（0x2******* 简单指令，参数无意义）
COMMANDS = {
    "sit_stand":        0x21010202,   # 站立 / 坐下 切换
    "stop":             0x21020C0E,   # 软件急停
    "zero":             0x21010C05,   # 回零位（初始化关节）
    "posture_mode":     0x21010D05,   # 姿态模式
    "motion_mode":      0x21010D06,   # 运动模式
    "nav_mode":         0x21010C03,   # 导航模式（响应速度指令）
    "manual_mode":      0x21010C02,   # 手动模式（响应遥控器）
    "gait_slow":        0x21010300,   # 慢速平地
    "gait_mid":         0x21010307,   # 中速平地
    "gait_fast":        0x21010303,   # 高速平地
    "gait_crawl":       0x21010406,   # 爬行挡
    "gait_rug_grip":    0x21010402,   # 抓地挡 RUG
    "gait_rug_normal":  0x21010401,   # 普通挡 RUG
    "gait_rug_high":    0x21010407,   # 高抬腿挡 RUG
    "hello":            0x21010507,   # 打招呼
    "twist":            0x21010204,   # 扭腰
    "moonwalk":         0x2101030C,   # 太空步
    "keep_stepping_on": 0x21010C06,   # 持续踏步 开（指令值 -1）
    "keep_stepping_off": 0x21010C06,  # 持续踏步 关（指令值 2）
    "heartbeat":        HEARTBEAT,
}

# 轴指令码：移动模式与姿态模式复用同一批码，靠模式解释
AXIS_CODES = {
    "lat":  0x21010131,   # 移动=左右平移 / 姿态=横滚
    "fwd":  0x21010130,   # 移动=前后平移 / 姿态=俯仰
    "yaw":  0x21010135,   # 移动=转弯     / 姿态=偏航
    "hgt":  0x21010102,   # 身体高度（两种模式都有效）
}
# 各轴死区，|值| 小于死区按 0 处理
AXIS_DEADZONE = {"lat": 12553, "fwd": 6553, "yaw": 9553, "hgt": 20000}
AXIS_LIMIT = 32767

GAITS = {
    "slow": "gait_slow", "mid": "gait_mid", "fast": "gait_fast",
    "crawl": "gait_crawl", "rug_grip": "gait_rug_grip",
    "rug_normal": "gait_rug_normal", "rug_high": "gait_rug_high",
}


class MotionError(Exception):
    """运动控制相关的可预期错误。"""


class MotionClient(object):
    def __init__(self, host="192.168.1.120", port=43893, enabled=True, log=None):
        self.host = host
        self.port = int(port)
        self.enabled = bool(enabled)
        self._log = log or (lambda level, msg: None)
        self._target = None
        self._sock = None
        self._lock = threading.Lock()
        self._axes = {name: 0 for name in AXIS_CODES}
        self._sent_axes = {name: 0 for name in AXIS_CODES}
        self._axes_ts = 0.0
        self._armed = False
        self._mode = "move"          # move | pose
        self._gait = ""
        self._thread = None
        self._closing = threading.Event()
        self.sent = 0
        self.last_error = ""
        self.last_send_ts = 0.0
        self.last_command = ""
        self.resolved_target = ""

    # ------------------------------------------------------------ lifecycle
    def start(self):
        if not self.enabled:
            self.last_error = "运动控制已禁用（--no-motion）"
            return
        try:
            info = socket.getaddrinfo(self.host, self.port, socket.AF_INET,
                                      socket.SOCK_DGRAM)[0]
            self._target = info[4]
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.resolved_target = "%s:%d" % (self.host, self.port)
        except OSError as exc:
            self.last_error = "解析运动主机地址失败：%s" % exc
            self._log("error", self.last_error)
            return
        self._thread = threading.Thread(target=self._loop, name="motion", daemon=True)
        self._thread.start()
        self._log("ok", "运动控制就绪，目标 %s" % self.resolved_target)

    def close(self):
        self._closing.set()
        self._armed = False
        try:
            self._zero_axes()
        except MotionError:
            pass
        if self._sock:
            self._sock.close()
            self._sock = None

    # ---------------------------------------------------------------- 发送
    def _sendto(self, code, value=0, ctype=0):
        if not self._sock or not self._target:
            raise MotionError(self.last_error or "运动控制未就绪")
        packet = struct.pack("<Iii", code & 0xFFFFFFFF, int(value), int(ctype))
        self._sock.sendto(packet, self._target)
        self.sent += 1
        self.last_send_ts = time.time()

    def _loop(self):
        axis_interval = 1.0 / SEND_HZ
        beat_interval = 1.0 / HEARTBEAT_HZ
        next_beat = 0.0
        next_axis = 0.0
        while not self._closing.is_set():
            now = time.time()
            try:
                if self._armed and now >= next_beat:
                    self._sendto(HEARTBEAT)
                    next_beat = now + beat_interval
                if now >= next_axis:
                    self._tick_axes(now)
                    next_axis = now + axis_interval
            except (OSError, MotionError) as exc:
                self.last_error = str(exc)
                time.sleep(0.2)
                next_beat = next_axis = 0.0
            self._closing.wait(axis_interval / 2.0)

    def _tick_axes(self, now):
        fresh = (now - self._axes_ts) < AXIS_TIMEOUT
        with self._lock:
            values = dict(self._axes)
        if not (self._armed and fresh):
            values = {name: 0 for name in AXIS_CODES}
        for name, code in AXIS_CODES.items():
            value = values[name]
            if value:
                self._sendto(code, value)
                self._sent_axes[name] = value
            elif self._sent_axes[name] != 0:
                self._sendto(code, 0)      # 松开后补一帧 0，让机器人停下
                self._sent_axes[name] = 0

    def _zero_axes(self):
        with self._lock:
            for name in self._axes:
                self._axes[name] = 0
        if self._sock:
            for name, code in AXIS_CODES.items():
                if self._sent_axes.get(name):
                    self._sendto(code, 0)
                    self._sent_axes[name] = 0

    # ---------------------------------------------------------------- 接口
    def set_arm(self, on):
        if not self.enabled or not self._sock:
            raise MotionError(self.last_error or "运动控制不可用")
        on = bool(on)
        with self._lock:
            self._armed = on
            if not on:
                for name in self._axes:
                    self._axes[name] = 0
        if not on:
            for name, code in AXIS_CODES.items():
                if self._sent_axes.get(name):
                    self._sendto(code, 0)
                    self._sent_axes[name] = 0
        self.last_command = "启用控制" if on else "解除控制"
        self._log("ok" if on else "info", "运动控制%s" % ("已启用（会发心跳）" if on else "已解除"))
        return self._armed

    def is_armed(self):
        return self._armed

    def command(self, name):
        """下发一条简单指令（急停不受启用状态限制）。"""
        if not self.enabled or not self._sock:
            raise MotionError(self.last_error or "运动控制不可用")
        if name not in COMMANDS:
            raise MotionError("未知指令：%s" % name)
        code = COMMANDS[name]
        value = 0
        if name == "keep_stepping_on":
            value = -1
        elif name == "keep_stepping_off":
            value = 2
        if name == "posture_mode":
            self._mode = "pose"
        elif name in ("motion_mode", "nav_mode", "manual_mode"):
            self._mode = "move"
        for gait, cmd in GAITS.items():
            if cmd == name:
                self._gait = gait
        if name == "stop":
            self._armed = False
            with self._lock:
                for key in self._axes:
                    self._axes[key] = 0
        self._sendto(code, value)
        self.last_command = name
        self._log("warn" if name == "stop" else "info", "已下发指令 %s (0x%08X)" % (name, code))
        return {"command": name, "code": code, "value": value}

    def set_axes(self, payload):
        """上游（网页）推来的摇杆量，语义名字而不是指令码。"""
        mode = payload.get("mode")
        if mode in ("move", "pose"):
            self._mode = mode
        with self._lock:
            for name in AXIS_CODES:
                if name not in payload:
                    continue
                try:
                    value = int(payload[name])
                except (TypeError, ValueError):
                    value = 0
                value = max(-AXIS_LIMIT, min(AXIS_LIMIT, value))
                if abs(value) < AXIS_DEADZONE[name]:
                    value = 0
                self._axes[name] = value
            self._axes_ts = time.time()
            snapshot = dict(self._axes)
        return snapshot

    def status(self):
        with self._lock:
            axes = dict(self._axes)
            stamp = self._axes_ts
        # 摇杆量只在"已启用且前端还在刷新"时才算数，否则实际下发的是 0
        live = self._armed and (time.time() - stamp) < AXIS_TIMEOUT
        if not live:
            axes = {name: 0 for name in AXIS_CODES}
        return {
            "enabled": self.enabled,
            "armed": self._armed,
            "mode": self._mode,
            "gait": self._gait,
            "axes": axes,
            "axes_live": live,
            "target": self.resolved_target,
            "sent": self.sent,
            "last_command": self.last_command,
            "last_error": self.last_error,
            "last_send_age_ms": int((time.time() - self.last_send_ts) * 1000)
            if self.last_send_ts else None,
            "commands": sorted(COMMANDS.keys()),
            "gaits": sorted(GAITS.keys()),
        }
