#!/usr/bin/env python3
"""临时探测：在感知主机上监听运动主机上报的 UDP 数据。

运动主机默认向感知主机上报（目标端口通常 43897）。若 Message Transformer
没在跑，这个端口是空闲的，我们就能直接看到 0x0901 状态结构体。
"""
import socket
import struct
import sys
import time

PORTS = [int(p) for p in (sys.argv[1:] or ["43897", "43899", "43901"])]
DURATION = 5.0

socks = []
for port in PORTS:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
    except OSError as exc:
        print("bind %d 失败: %s" % (port, exc))
        continue
    s.settimeout(0.2)
    socks.append((port, s))

if not socks:
    raise SystemExit("没有任何端口绑定成功")

print("监听端口 %s，时长 %.0fs ..." % (PORTS, DURATION))
counts = {}
samples = {}
deadline = time.time() + DURATION
while time.time() < deadline:
    for port, s in socks:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        if len(data) < 12:
            continue
        code, param, ctype = struct.unpack("<III", data[:12])
        key = (port, addr[0], code, ctype, param, len(data))
        counts[key] = counts.get(key, 0) + 1
        samples.setdefault(key, data[12:12 + 48].hex())

print("--- 收到的报文种类 ---")
if not counts:
    print("(什么都没收到)")
for key in sorted(counts, key=lambda k: -counts[k]):
    port, ip, code, ctype, param, size = key
    print("port=%d from=%s code=0x%08X type=%d param=%d len=%d  count=%d"
          % (port, ip, code, ctype, param, size, counts[key]))
    print("        payload[:48]=%s" % samples[key])
