# 绝影 Lite3 后端接口文档

把机器狗的**相机画面**和**运动控制**做成一个 HTTP 后端，别的网站、桌面程序、手机 App、脚本都能连上来用。

```
                 ┌──────────────── 机器狗感知主机 192.168.1.103 ─────────────────┐
  你的网站/脚本   │  这个后端（run.py，:8080）        D435i 相机                    │
  ──────────────▶│  ├─ /api/v1/stream   MJPEG  ←──  ROS 话题 / 直连 Librealsense  │
   HTTP / JSON   │  ├─ /api/v1/motion/* 控制 ──┐                                  │
                 │  └─ /api/v1/status           │                                  │
                 └─────────────────────────────┼──────────────────────────────────┘
                                               │ UDP 43893（0x2******* 指令帧）
                                               ▼
                                     运动主机 192.168.1.120
```

- 全部接口都是 **普通 HTTP + JSON**，没有 WebSocket、没有私有 SDK，浏览器 `fetch` 直接能用。
- 已经开了 **CORS**（`Access-Control-Allow-Origin: *`），任意域名的网页都能调用。
- 画面用 **MJPEG**：一个 `<img src="http://.../api/v1/stream">` 就能播放，不需要额外解码库。

---

## 1. 先把后端跑起来，并让它能被"网络"访问

### 1.1 在机器狗上启动

最简单：Windows 双击 `连接机器狗.bat`（部署 + 启动 + 打开页面），或者已经部署过时直接：

```bash
ssh ysc@192.168.1.103
cd ~/camera_web
bash tools/robot_setup.sh --source auto          # 自动选 ROS 话题取图，顺带起 roscore + 相机节点
```

看到 `[ok] 接口自检通过` 就说明后端已经在 `0.0.0.0:8080` 上监听了（**0.0.0.0 表示局域网任意主机都能连**，不只是本机）。

手动启动也可以：

```bash
cd ~/camera_web
python3 run.py --source ros --port 8080                 # 常规启动
python3 run.py --source ros --port 8080 --api-token 你自己的口令   # 开启令牌鉴权
```

### 1.2 后端地址是什么

后端地址 = `http://<机器人感知主机IP>:<端口>`，本机部署就是：

```
http://192.168.1.103:8080
```

不确定的话，在机器狗上执行 `hostname -I`，第一个就是；启动时控制台也会打印"局域网 http://…"。

### 1.3 别的电脑 / 别的网站怎么连（网络要求）

| 场景 | 是否可直连 | 需要做的事 |
| --- | --- | --- |
| 同一台电脑上的网页 | ✅ | 用 `http://127.0.0.1:8080` |
| 同一局域网的另一台电脑/手机 | ✅ | 用 `http://192.168.1.103:8080`；双方都在 `192.168.1.x` 网段 |
| 公网上的网站（如你自己买的域名） | ❌ 默认不行 | 内网 IP 不能被公网访问，需要端口映射/内网穿透（见 1.5） |
| 机器狗换了网络（WiFi） | ⚠️ | IP 会变，重新看 `hostname -I`；或用 `--port` 固定端口 + 固定 IP |

**先做三步连通性自检**（在"别的电脑"上）：

1. `ping 192.168.1.103` —— 通不通。
2. `curl http://192.168.1.103:8080/api/v1/ping` —— 应返回 `{"ok": true, "pong": true}`。
3. 浏览器打开 `http://192.168.1.103:8080/api-demo` —— 有画面和按钮就说明一切正常。

> ⚠️ 你正在用的电脑如果同时连着"机器狗网线 + 别的 WiFi"，注意网卡优先级。实测本机做法：**有线网卡设为静态 `192.168.1.50/24`**（机器狗不提供 DHCP，插网线只会拿到 169.254.x.x）。改 IP 的脚本：`tools\set_static_ip.ps1 -Enable / -Disable`。

### 1.4 防火墙

- **机器狗（Ubuntu）**：一般没开 ufw；如果连不上，执行 `sudo ufw status`，必要时 `sudo ufw allow 8080/tcp`。
- **Windows 上跑后端**：首次监听会弹"是否允许网络访问"，选**允许**；或手动 `netsh advfirewall firewall add rule name="Lite3API" dir=in action=allow protocol=TCP localport=8080`。

### 1.5 想让公网网站也能连（可选）

后端本身只监听内网。要暴露到公网，三选一：

- **内网穿透（推荐、最快）**：`cloudflared tunnel --url http://192.168.1.103:8080`，会给你一个 https 域名，其余网站直接调这个域名即可。
- **frp / nps**：把内网 8080 映射到有公网 IP 的服务器端口。
- **路由器端口映射**：把公网的 8080 转发到 `192.168.1.103:8080`（需公网 IP）。

> 暴露到公网前**务必**开启 `--api-token`，否则任何拿到地址的人都能让你的狗站起来、走路。

---

## 2. 三十秒上手

### 2.1 curl

```bash
BASE=http://192.168.1.103:8080

curl $BASE/api/v1/ping                      # 连通性
curl $BASE/api/v1/status | python3 -m json.tool   # 综合状态
curl -o shot.jpg "$BASE/api/v1/snapshot.jpg?mode=color"   # 抓一张

# 控制：启用 → 站立 → 急停
curl -X POST $BASE/api/v1/motion/arm -H 'Content-Type: application/json' -d '{"on":true}'
curl -X POST $BASE/api/v1/motion/command -H 'Content-Type: application/json' -d '{"cmd":"sit_stand"}'
curl -X POST $BASE/api/v1/motion/stop
```

### 2.2 网页（一行放画面）

```html
<img src="http://192.168.1.103:8080/api/v1/stream?mode=color" alt="Lite3">
```

### 2.3 网页（JavaScript 控制）

```js
const BASE = 'http://192.168.1.103:8080';            // 也可以是你穿透后的域名
const H = { 'Content-Type': 'application/json' };    // 开了令牌再加 'X-API-Token': '口令'

async function api(path, body) {
  const r = await fetch(BASE + path, {
    method: body ? 'POST' : 'GET',
    headers: H,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

await api('/api/v1/motion/arm', { on: true });        // 启用控制（服务端开始发心跳）
await api('/api/v1/motion/command', { cmd: 'sit_stand' });
setInterval(() => api('/api/v1/motion/axes', { fwd: 20000, mode: 'move' }), 50); // 20Hz
await api('/api/v1/motion/stop');                     // 急停
```

### 2.4 Python

```python
import requests, time
BASE = "http://192.168.1.103:8080"
S = requests.Session()
S.headers["Content-Type"] = "application/json"
# S.headers["X-API-Token"] = "口令"      # 服务端开了令牌才需要

print(S.get(f"{BASE}/api/v1/status").json()["frame"])

img = S.get(f"{BASE}/api/v1/snapshot.jpg", params={"mode": "color"})
open("shot.jpg", "wb").write(img.content)

S.post(f"{BASE}/api/v1/motion/arm", json={"on": True})
S.post(f"{BASE}/api/v1/motion/command", json={"cmd": "sit_stand"})
for _ in range(20):                                    # 前进 1 秒
    S.post(f"{BASE}/api/v1/motion/axes", json={"fwd": 15000, "mode": "move"})
    time.sleep(0.05)
S.post(f"{BASE}/api/v1/motion/stop")
```

---

## 3. 鉴权与跨域

### 3.1 令牌（可选）

启动时加 `--api-token 口令` 即开启。开启后：

| 接口类型 | 是否校验令牌 |
| --- | --- |
| 读接口（info/status/snapshot/stream/config/commands/motion） | 不校验，方便直接 `<img>` 嵌入 |
| **写接口**（config 的 POST、motion 的所有 POST） | **必须**带令牌 |

三种带法任选其一：

```
X-API-Token: 口令                 ← 推荐（请求头）
?token=口令                       ← 查询参数（<img> / <video> 无法带请求头时用）
POST /api/v1/motion/stop          ← 特例：急停任何时候都不需要令牌
```

> 急停**故意不校验令牌**：任何人在紧急情况下都应该能停下来。

### 3.2 跨域（CORS）

所有响应都带：

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-API-Token
Access-Control-Expose-Headers: X-Frame-Id, X-Api-Version
```

浏览器发 `application/json` 的 POST 时会先发 `OPTIONS` 预检，后端已实现，无需额外配置。
`<img>`、`<video>`、`canvas` 读画面属于"简单请求"，不受 CORS 限制。

---

## 4. 接口总表

| 方法 | 路径 | 说明 | 令牌 |
| --- | --- | --- | --- |
| GET | `/api/v1` | 接口索引 + 快速上手 | - |
| GET | `/api/v1/ping` | 连通性探测 | - |
| GET | `/api/v1/info` | 能力清单（信号源/模式/分辨率/是否可控制） | - |
| GET | `/api/v1/status` | 综合状态（画面 + 配置 + 运动 + 观看者） | - |
| GET | `/api/v1/commands` | 动作指令清单（名称 + 中文说明 + 指令码） | - |
| GET | `/api/v1/snapshot.jpg` | 单帧 JPEG | - |
| GET | `/api/v1/stream` | MJPEG 实时流 | - |
| GET | `/api/v1/config` | 读取画面配置 | - |
| POST | `/api/v1/config` | 修改分辨率/帧率/画质/深度范围 | ✅ |
| GET | `/api/v1/motion` | 运动控制状态 | - |
| POST | `/api/v1/motion/arm` | 启用 / 解除控制 | ✅ |
| POST | `/api/v1/motion/command` | 下发动作指令（站立/坐下/步态/模式…） | ✅ |
| POST | `/api/v1/motion/axes` | 下发摇杆量（≥20Hz） | ✅ |
| POST | `/api/v1/motion/stop` | 急停 | ❌ 永远开放 |
| GET | `/api/v1/openapi.json` | OpenAPI 3.0 描述（可导入 Postman/Apifox） | - |
| GET | `/api-demo` | 现成的第三方接入示例页 | - |

统一返回格式：成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": "说明"}`。

---

## 5. 接口详解

### 5.1 `GET /api/v1/info`

```json
{
  "ok": true, "api": "v1", "server": "1.1.0", "kind": "lite3-camera-web",
  "source": { "kind": "ros", "name": "ROS 图像话题", "width": 640, "height": 480, "target_fps": 30 },
  "modes": ["color", "depth", "dual"],
  "sizes": [[640,480],[848,480],[960,540],[1280,720],[1920,1080]],
  "fps": [15, 30, 60],
  "motion": { "enabled": true, "axes": ["fwd","lat","yaw","hgt"], "modes": ["move","pose"] },
  "token_required": false,
  "endpoints": { "stream": "/api/v1/stream?mode=color", "...": "..." }
}
```

### 5.2 `GET /api/v1/status`

```json
{
  "ok": true, "api": "v1", "server": "1.1.0",
  "source": { "kind": "ros", "name": "ROS 图像话题", "has_depth": true },
  "frame": { "id": 8821, "age_ms": 12.4, "capture_fps": 30.1, "total": 8821, "dropped": 0 },
  "stream": { "quality": 80, "depth_near": 0.2, "depth_far": 2.5, "encoder": "pillow" },
  "config": { "width": 640, "height": 480, "fps": 30, "quality": 80, "depth_near": 0.2, "depth_far": 2.5 },
  "motion": { "enabled": true, "armed": false, "mode": "move", "gait": "",
              "axes": {"fwd":0,"lat":0,"yaw":0,"hgt":0}, "axes_live": false,
              "sent": 128, "last_command": "sit_stand", "last_send_age_ms": 484 },
  "viewers": 2,
  "token_required": false
}
```

- `frame.age_ms`：画面有多旧（毫秒）。> 500 基本可以认为卡了。
- `frame.capture_fps`：服务端真实取帧速率。
- `motion.axes_live`：摇杆量此刻是否真的在生效（400ms 没刷新就变 `false`，服务端发的是 0）。
- 需要单个观看连接的带宽统计时加 `?cid=你的标识`，会多返回 `client: {fps, kbps}`。

### 5.3 `GET /api/v1/snapshot.jpg?mode=color|depth|dual`

返回 `image/jpeg`。适合做静态预览、定时截图、AI 推理前的取样。

```bash
curl -o now.jpg "http://192.168.1.103:8080/api/v1/snapshot.jpg?mode=depth"
```

无画面时返回 `503` + JSON 错误。

### 5.4 `GET /api/v1/stream?mode=color|depth|dual&cid=xxx`

`multipart/x-mixed-replace; boundary=lite3frame` 长连接，每帧一个 JPEG，帧头带 `X-Frame-Id`。

```html
<img src="http://192.168.1.103:8080/api/v1/stream?mode=dual&cid=mySite">
```

```js
// 需要逐帧处理（例如自己画到 canvas / 送 AI）时
const r = await fetch(BASE + '/api/v1/stream?mode=color');
const reader = r.body.getReader();
const dec = new TextDecoder();
let buf = '';
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  // 按 boundary + Content-Length 切出每帧 JPEG，再 createImageBitmap(new Blob([bytes]))
}
```

- 服务端 **8 秒没有新帧会主动断开**，客户端要自己重连（`<img>` 不会自动重连，见 6.3）。
- `cid` 只是统计标识，随便填，同一个人用同一个值即可。
- 同一个流可以被多个客户端同时观看，编码只做一次。

### 5.5 `GET/POST /api/v1/config`

```bash
curl -X POST http://192.168.1.103:8080/api/v1/config \
     -H 'Content-Type: application/json' \
     -d '{"width":960,"height":540,"fps":30,"quality":70,"depth_near":0.3,"depth_far":3.0}'
```

- 允许尺寸：`640x480 / 848x480 / 960x540 / 1280x720 / 1920x1080`；帧率：`15 / 30 / 60`；质量 `30-95`。
- 改分辨率/帧率会让服务端重连一次信号源，画面会断一下（约 1 秒）。
- 返回 `{"ok":true,"changed":true,"config":{...}}`，`changed=false` 表示参数刚设置过、未触发重连。

### 5.6 `GET /api/v1/motion`

返回 5.2 里的 `motion` 字段（`enabled/armed/mode/gait/axes/axes_live/sent/last_command/last_error/commands/gaits`）。

### 5.7 `POST /api/v1/motion/arm`

```json
{ "on": true }
```

启用后服务端以 **2Hz 发心跳**（`0x21040001`，官方要求 ≥2Hz），并允许下发摇杆；`{"on": false}` 解除，摇杆自动归零。

返回 `{"ok":true,"armed":true,"motion":{...}}`。

### 5.8 `POST /api/v1/motion/command`

```json
{ "cmd": "sit_stand" }
```

可用名称（`GET /api/v1/commands` 会给出完整清单与指令码）：

| cmd | 含义 | 生效前提 |
| --- | --- | --- |
| `sit_stand` | 站立 / 坐下 | 任意 |
| `zero` | 回零位 | 任意 |
| `posture_mode` | 姿态模式（摇杆变姿态控制） | 站立 |
| `motion_mode` | 运动模式（摇杆变走路） | 站立 |
| `nav_mode` / `manual_mode` | 导航 / 手动（速度指令来源） | 站立 |
| `gait_slow` / `gait_mid` / `gait_fast` | 慢 / 中 / 高速步态 | 运动模式 |
| `gait_crawl` / `gait_rug_normal` / `gait_rug_grip` / `gait_rug_high` | 爬行 / 地毯普通 / 地毯抓地 / 高抬腿 | 运动模式 |
| `hello` / `twist` / `moonwalk` | 打招呼 / 扭腰 / 太空步（展示动作） | 见备注 |
| `keep_stepping_on` / `keep_stepping_off` | 原地持续踏步 开 / 关 | 站立 |
| `heartbeat` | 手动发一次心跳 | 任意（服务端已自动发，一般不用） |

未知指令返回 `400`（附可用清单）。

### 5.9 `POST /api/v1/motion/axes`

```json
{ "mode": "move", "fwd": 20000, "lat": 0, "yaw": 0, "hgt": 0 }
```

| 字段 | 范围 | move 模式语义 | pose 模式语义 |
| --- | --- | --- | --- |
| `fwd` | -32767 ~ 32767 | 前进 / 后退 | 俯仰（低头 / 抬头） |
| `lat` | -32767 ~ 32767 | 左 / 右平移 | 横滚（左 / 右翻） |
| `yaw` | -32767 ~ 32767 | 左 / 右转弯 | 偏航 |
| `hgt` | -32767 ~ 32767 | 身体抬高 / 降低 | 同左 |
| `mode` | `move` / `pose` | 运动模式语义 | 姿态模式语义 |
| `speed` | 0 ~ 1 | 可选，服务端按比例缩放四轴 | 同左 |

**规则（照官方要求实现的，直接照做即可）：**

1. **必须 ≥20Hz 持续下发**，松手就停下不发；服务端内部以 25Hz 重发当前值。
2. **400ms 没有新值就自动归零**——网页崩了、网线拔了，狗会停。
3. 也有**官方 250ms 超时**兜底：机器人自己收不到轴指令也会停。
4. 小于死区的值会被当成 0：`lat` 12553、`fwd` 6553、`yaw` 9553、`hgt` 20000。所以想要"很小的一点移动"，得给到死区以上。
5. 值超出 ±32767 会被夹住。

返回 `{"ok":true,"axes":{...}}`（回显实际下发的值）。

### 5.10 `POST /api/v1/motion/stop`

无需参数、**无需令牌**。动作：解除控制 + 发软件急停 `0x21020C0E` + 摇杆清零。

---

## 6. 画面接入指南

### 6.1 三种画质/数据选择

| mode | 内容 | 典型码率（640x480@30） |
| --- | --- | --- |
| `color` | 彩色 RGB | ~6 Mbps |
| `depth` | 深度伪彩（近红远紫，默认 0.2–2.5 m） | ~9 Mbps |
| `dual` | 彩色 + 深度并排（1282x480） | ~10 Mbps，Jetson 上约 15 fps |

### 6.2 多观看者

一个后端可以同时给 N 个网页推流，服务端只编码一次（按帧号缓存）。实测 2 个观看者时 Jetson 负载无感变化。

### 6.3 `<img>` 断流要重连

服务端 8 秒无新帧会主动断开，`<img>` 会变成破图。生产页面建议：

```js
function watch(img) {
  img.onerror = () => setTimeout(() => {
    img.src = img.src.replace(/([?&])_t=\d+/, '$1') + '&_t=' + Date.now(); // 触发重新请求
  }, 1000);
  img.onload = () => { img.dataset.ok = '1'; };
}
```

### 6.4 需要"帧级"数据

轮询 `snapshot.jpg`（例如每 200ms 取一张做 AI 推理）比自己解析 MJPEG 简单，代价是多一次 HTTP。50ms 以内的高频需求才建议自己解析 MJPEG 分片。

---

## 7. 运动控制接入指南（**请先读完再动手**）

### 7.1 安全模型

```
启用控制(arm) ──▶ 服务端开始 2Hz 心跳 ──▶ 允许发轴指令
     │                                        │
     └──────── 急停(stop) 任何时候都能打断 ◀───┘
```

- **没有 arm 的时候，服务端不会发任何运动指令**（除了你显式调用的 action），轴指令直接返回 `axes_live=false`。
- 400ms 不刷新自动归零 + 机器人 250ms 超时 = 双重兜底。
- 断网、关页面、浏览器崩溃都不会让狗一直走。

### 7.2 推荐调用顺序

```text
1. POST /api/v1/motion/arm      {"on":true}
2. POST /api/v1/motion/command  {"cmd":"sit_stand"}      # 站起来
3. POST /api/v1/motion/command  {"cmd":"motion_mode"}    # 切运动模式
4. POST /api/v1/motion/axes     {"fwd":15000,"mode":"move"}   # 每 50ms 一次
5. 松手 → 停止发轴 → 400ms 内自动停
6. POST /api/v1/motion/stop                              # 收尾，解除控制
```

### 7.3 多客户端同时控制

官方允许**多个客户端同时**发 `0x2*******` 指令，但**后发的轴指令会覆盖先发的**，两个人一起开摇杆会互相打架。

建议：每个接入方自己维护一个"操作权"约定（例如只有拿到锁的客户端才发 axes），或让后端做单点控制（当前实现是直接转发，不仲裁）。

### 7.4 调试：指令发出去了但狗不动？

```bash
# 在机器狗上抓包，看报文是否真的到了运动主机
sudo tcpdump -i any -x udp port 43893
```

- 看得到我们发的 12 字节小端帧 → 报文到了，问题在指令语义（例如姿态模式下轴是调姿态不是走路）。
- 收不到 → 检查 `--motion-ip`（默认 `192.168.1.120`，也可能是 `192.168.2.1`）。
- 备选通道：机器狗自带的 `lite3_transfer` 会把指令转成 ROS2 的 `/simple_cmd`、`/complex_cmd` 话题，实在不行可以改走那边（本后端未实现，需要 rclpy）。

---

## 8. 错误码

| HTTP | 场景 | 返回体 |
| --- | --- | --- |
| 200 | 成功 | `{"ok": true, ...}` |
| 400 | JSON 非法 / 参数越界 / 未知指令 | `{"ok": false, "error": "不支持的帧率", "allowed": [15,30,60]}` |
| 401 | 开启令牌但没带或带错 | `{"ok": false, "error": "unauthorized"}` |
| 404 | 路径不存在 | `{"ok": false, "error": "unknown endpoint"}` |
| 503 | 运动控制未初始化 / 相机无画面 | `{"ok": false, "error": "运动控制未初始化"}` |

---

## 9. OpenAPI / Postman

- 描述文件：`GET /api/v1/openapi.json`（OpenAPI 3.0.3）。
- Postman：`Import → Link`，填 `http://192.168.1.103:8080/api/v1/openapi.json`。
- Apifox / Swagger Editor 同法导入，可直接生成 TypeScript / Python 客户端。

---

## 10. 常见问题

**Q: 网页报 `blocked by CORS policy`？**
后端已带 `Access-Control-Allow-Origin: *`，仍报错一般是：① 打的是 `https` 但后端是 `http`（混合内容被浏览器拦）；② 中间经过了反向代理把 CORS 头吃掉了。公网部署建议统一用 https（cloudflared 自带证书）。

**Q: 返回 401？**
后端开了 `--api-token`，写接口要加请求头 `X-API-Token`。

**Q: 画面很卡 / 帧率只有 15？**
① 用了 `dual` 模式（Jetson 要编码两张图）；② 分辨率开到 1280x720 以上；③ WiFi 带宽不够。降到 `640x480 + color + q70` 最稳。

**Q: 从公网连不上？**
内网 IP 天生不可达，见 1.5 内网穿透；穿透后把 BASE 换成穿透域名即可，其余接口不变。

**Q: 机器狗 IP 变了？**
`hostname -I` 查新 IP；或在路由器里给它做静态分配。

**Q: 能不能不要网页，只要后端？**
可以。`python3 run.py --source ros --port 8080` 只提供接口和默认页面，`static/` 整个删掉也不影响 `/api/v1/*`（只是没有可视化页面）。

---

## 11. 附：把所有接口当"能力清单"用

```bash
curl -s http://192.168.1.103:8080/api/v1/info | python3 -m json.tool
curl -s http://192.168.1.103:8080/api/v1/commands | python3 -m json.tool
```

这两份 JSON 已经包含"能做什么"的全部信息（模式、分辨率、指令名、中文说明、指令码），接进来的网站可以把按钮**自动生成**出来，不必写死。

---

## 12. 附录：底层 UDP 协议（后端替你做的事）

如果要在**别的语言**里直接跟运动主机说话（不经这个后端），或者想排查"指令到底发得对不对"，
这里是本后端 `lite3_viewer/motion.py` 实际使用的报文格式，与官方《UDP 通信》一致。

**目标**：运动主机 UDP `43893`（`192.168.1.120`，WiFi 直连时也可能是 `192.168.2.1`），**小端字节序**。

### 12.1 简单指令（12 字节，指令值就是"参数"）

```c
struct CommandHead{          // 共 12 字节，小端
    uint32_t code;           // 指令码，如 0x21010202
    uint32_t paramters_size; // 简单指令里它就是"指令值"（如轴值 / -1 / 2）
    uint32_t type;           // 0 = 简单指令
};
```

Python 等价写法（后端用的就是这一行）：

```python
import socket, struct
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
packet = struct.pack("<Iii", code, value, 0)   # 12 字节
sock.sendto(packet, ("192.168.1.120", 43893))
```

> 轴值是负数时，`struct.pack("<i", -20000)` 与 C 里的 `int32_t` 二进制一致，机器人按补码解读。

### 12.2 复杂指令（12 + N 字节）

```c
const uint32_t kDataSize = 256;
struct Command{
    CommandHead head;        // type = 1
    uint32_t data[kDataSize];
};
// 发送长度 = sizeof(head) + head.paramters_size
```

`head.paramters_size` 此时表示数据内容长度（字节），`type = 1`。

### 12.3 常用指令码

| 名称 | 代码 | 类型 | 备注 |
| --- | --- | --- | --- |
| 心跳 | `0x21040001` | 简单 | ≥2Hz，后端在 arm 后自动发 |
| 站立 / 坐下 | `0x21010202` | 简单 | 坐站切换 |
| 急停 | `0x21020C0E` | 简单 | 软件急停 |
| 回零位 | `0x21010C05` | 简单 | 关节初始化 |
| 姿态模式 / 运动模式 | `0x21010D05` / `0x21010D06` | 简单 | 决定轴指令语义 |
| 导航 / 手动 | `0x21010C03` / `0x21010C02` | 简单 | 速度指令来源 |
| 轴：前后(`fwd`) | `0x21010130` | 简单 | 死区 6553 |
| 轴：左右(`lat`) | `0x21010131` | 简单 | 死区 12553 |
| 轴：转弯(`yaw`) | `0x21010135` | 简单 | 死区 9553 |
| 轴：高度(`hgt`) | `0x21010102` | 简单 | 死区 20000 |
| 步态 慢/中/高 | `0x21010300` / `0x21010307` / `0x21010303` | 简单 | |
| 步态 爬行 / RUG 抓地 / RUG 普通 / 高抬腿 | `0x21010406` / `0x21010402` / `0x21010401` / `0x21010407` | 简单 | |
| 打招呼 / 扭腰 / 太空步 | `0x21010507` / `0x21010204` / `0x2101030C` | 简单 | 展示动作 |
| 持续踏步 开 / 关 | `0x21010C06`，值 `-1` / `2` | 简单 | 值在 `paramters_size` 里 |

完整清单以 `GET /api/v1/commands` 为准（后端从 `motion.py` 的 `COMMANDS` 表实时生成）。

### 12.4 状态上报（`0x0901`）

- `type = 1` 的复杂指令，50Hz，结构体 `RobotStateUpload`：
  `robot_basic_state / robot_gait_state / rpy[3] / rpy_vel[3] / xyz_acc[3] / pos_world[3] /
  vel_world[3] / vel_body[3] / … / battery_level / ultrasound[2]`。
- **运动主机默认只往感知主机上报**，目标端口 `43897`，而那个端口被机器狗自带的
  `lite3_transfer` 占着。想自己收：SSH 到运动主机改上报目标地址为主机静态 IP（官方
  MotionSDK 说明第 4、5 节），或者直接订阅 transformer 转出来的 ROS 话题。

### 12.5 感知主机上的消息转换器

`lite3_transfer`（Message Transformer）负责 **UDP ↔ ROS** 双向转换：

- 上行：把运动主机发来的 UDP 状态转成 ROS 话题发布；
- 下行：把 ROS 话题里的控制指令打包成 UDP 发给运动主机。

所以除了本后端的"直发 UDP"路线，还有一条"发 ROS 话题"的路线（`/simple_cmd`、
`/complex_cmd`）。两条路等效，本后端选了 UDP：不依赖 ROS 环境、不占用 transformer。

---

_文档版本 1.1.0 · 对应后端 `lite3_viewer/server.py`（`API_VERSION = "v1"`）_
