#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发调试用：用 Edge/Chrome 无头模式 + CDP 给网页截图（只用标准库）。

Chrome 自带的 --screenshot 会在 load 事件后立刻抓图，MJPEG 长连接永远不触发
load 完成，所以这里自己驱动 CDP：导航 → 等真实时间 → captureScreenshot。

用法：
    python tools/screenshot.py http://127.0.0.1:8099/ out.png --wait 4 --size 1500x940
"""

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 Edge/Chrome 可执行文件")


class WS(object):
    """极简 WebSocket 客户端，只够和 CDP 说话。"""

    def __init__(self, url, timeout=30):
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手被拒绝: %s" % buf.split(b"\r\n")[0])
        self._buf = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("WebSocket 连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, obj):
        payload = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        data = b""
        while True:
            b1, b2 = self._read_exact(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            if b2 & 0x80:
                mask = self._read_exact(4)
                raw = self._read_exact(length)
                chunk = bytes(x ^ mask[i % 4] for i, x in enumerate(raw))
            else:
                chunk = self._read_exact(length)
            if opcode == 0x8:
                raise RuntimeError("WebSocket 收到关闭帧")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            data += chunk
            if fin:
                return data

    def call(self, method, params=None, timeout=30):
        self._id += 1
        mid = self._id
        self.send({"id": mid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.recv().decode())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("CDP %s 失败: %s" % (method, msg["error"]))
                return msg.get("result", {})
        raise RuntimeError("CDP %s 超时" % method)


def http_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def screenshot(url, out, wait=4.0, width=1500, height=940, port=9333, profile=None):
    browser = find_browser()
    profile = profile or os.path.join(tempfile.gettempdir(), "cdp-shot-profile")
    args = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check", "--mute-audio",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile,
        "--window-size=%d,%d" % (width, height),
        "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(100):
            try:
                targets = http_json("http://127.0.0.1:%d/json/list" % port)
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    page = pages[0]
                    break
            except Exception:
                pass
            time.sleep(0.15)
        if not page:
            raise RuntimeError("拿不到 CDP 页面目标")

        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
        })
        ws.call("Page.navigate", {"url": url})
        time.sleep(wait)
        res = ws.call("Page.captureScreenshot", {"format": "png"}, timeout=60)
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))
        return out
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--wait", type=float, default=4.0)
    ap.add_argument("--size", default="1500x940")
    ap.add_argument("--port", type=int, default=9333)
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    path = screenshot(a.url, a.out, a.wait, w, h, a.port)
    print("saved", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    sys.exit(main())
