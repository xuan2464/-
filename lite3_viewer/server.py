"""HTTP 服务：网页 + MJPEG 实时流 + 对第三方网站开放的后端 API（v1）。仅依赖标准库。

对外接口分两层：
  /api/v1/*   给别的网站/程序调用的稳定接口（带 CORS、可选 Token、有 OpenAPI 描述）
  /api/*      自带网页用的旧接口，保持兼容（内部仍在使用）
"""

import json
import mimetypes
import os
import socket
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

try:  # 指令表与步态表以 motion 模块为准，避免两处维护
    from .motion import COMMANDS as MOTION_COMMANDS, GAITS as MOTION_GAITS, \
        MotionError as MOTION_ERROR
except Exception:  # 直接运行本文件时退回同目录导入
    try:
        from motion import COMMANDS as MOTION_COMMANDS, GAITS as MOTION_GAITS, \
            MotionError as MOTION_ERROR
    except Exception:
        MOTION_COMMANDS, MOTION_GAITS = {}, {}
        MOTION_ERROR = ValueError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(ROOT, "static")

BOUNDARY = "lite3frame"
ALLOWED_SIZES = [(640, 480), (848, 480), (960, 540), (1280, 720), (1920, 1080)]
ALLOWED_FPS = [15, 30, 60]

API_VERSION = "v1"
SERVER_VERSION = "1.1.0"
MODES = ("color", "depth", "dual")

# 这些写接口不校验令牌（急停必须永远可用）
TOKEN_EXEMPT = ("/api/v1/motion/stop", "/api/motion/stop")

# 第三方网站跨域调用需要的响应头（预检与真实请求都要带）
CORS_HEADERS = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, X-API-Token"),
    ("Access-Control-Expose-Headers", "X-Frame-Id, X-Api-Version"),
    ("Access-Control-Max-Age", "600"),
)

# 指令中文说明，同时用于 /api/v1/commands 与自带网页的按钮文案
COMMAND_DOC = {
    "sit_stand": ("站立 / 坐下", "在站立与坐姿之间切换"),
    "zero": ("回零位", "关节回到零位（初始化姿态）"),
    "posture_mode": ("姿态模式", "摇杆变成调身体姿态：横滚/俯仰/偏航/高度"),
    "motion_mode": ("运动模式", "摇杆变成走路：前后/左右平移/转弯"),
    "nav_mode": ("导航模式", "响应感知主机下发的速度指令"),
    "manual_mode": ("手动模式", "响应遥控器下发的速度指令"),
    "stop": ("急停", "软件急停，立即停止运动并解除摇杆控制"),
    "heartbeat": ("心跳", "验证连接，需不低于 2Hz（服务在启用控制后自动发）"),
    "gait_slow": ("慢速步态", "移动速度较低，稳"),
    "gait_mid": ("中速步态", "默认步态"),
    "gait_fast": ("高速步态", "移动速度较高"),
    "gait_crawl": ("爬行步态", "四足交替爬行，适合狭窄或静止拍摄"),
    "gait_rug_normal": ("RUG 普通", "地毯等软地面用的普通步态"),
    "gait_rug_grip": ("RUG 抓地", "地毯等软地面用的抓地步态"),
    "gait_rug_high": ("RUG 高抬腿", "软地面 / 需要高抬腿越障"),
    "hello": ("打招呼", "坐姿状态下前腿招手（宣传动作）"),
    "twist": ("扭腰", "站立状态下的姿态展示动作"),
    "moonwalk": ("太空步", "站立状态下的姿态展示动作"),
    "keep_stepping_on": ("持续踏步 开", "原地持续踏步"),
    "keep_stepping_off": ("持续踏步 关", "停止踏步"),
}


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = traceback.format_exc()
        if "BrokenPipe" in exc or "ConnectionReset" in exc or "timed out" in exc:
            return
        print("[server] 请求处理异常 %s\n%s" % (client_address, exc))


class Handler(BaseHTTPRequestHandler):
    server_version = "Lite3Viewer/" + SERVER_VERSION
    protocol_version = "HTTP/1.0"  # MJPEG 用连接关闭语义最省事
    hub = None
    clients = None
    config = None
    motion = None
    api_token = ""       # 空 = 不做鉴权（局域网默认）

    # ------------------------------------------------------------ helpers
    def _cors(self):
        for key, value in CORS_HEADERS:
            self.send_header(key, value)
        self.send_header("X-Api-Version", API_VERSION)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, token_inject=False):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_error(404, "Not Found")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        if token_inject and self.api_token:
            body = body.replace(b"__API_TOKEN__", self.api_token.encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 静默，只在需要排错时打开
        pass

    # ---------------------------------------------------------------- 鉴权
    def _authorized(self, query):
        """开启 --api-token 后，写操作必须带 X-API-Token 头或 ?token= 参数。"""
        if not self.api_token:
            return True
        given = self.headers.get("X-API-Token") or (query.get("token") or [""])[0]
        return given == self.api_token

    def _require_auth(self, query):
        if self._authorized(query):
            return True
        self._json({"ok": False, "error": "unauthorized",
                    "detail": "缺少或错误的 X-API-Token"}, 401)
        return False

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # ---------------- v1：给第三方网站用的接口
        if path in ("/api/v1", "/api/v1/"):
            self._api_index()
            return
        if path == "/api/v1/openapi.json":
            self._json(openapi_spec(bool(self.api_token)))
            return
        if path == "/api/v1/ping":
            self._json({"ok": True, "pong": True})
            return
        if path == "/api/v1/info":
            self._json(self._info_doc())
            return
        if path == "/api/v1/status":
            self._json(self._status_doc(query))
            return
        if path == "/api/v1/config":
            self._json({"ok": True, "config": self.config,
                        "sizes": ALLOWED_SIZES, "fps": ALLOWED_FPS})
            return
        if path == "/api/v1/commands":
            self._json(self._commands_doc())
            return
        if path == "/api/v1/motion":
            self._json({"ok": True, "motion": self._motion_status()})
            return
        if path in ("/api/v1/stream", "/api/v1/stream.mjpg"):
            self._stream(query)
            return
        if path in ("/api/v1/snapshot.jpg", "/api/v1/snapshot"):
            self._snapshot(query)
            return
        if path in ("/api-demo", "/api-demo.html"):
            self._file(os.path.join(STATIC_DIR, "api-demo.html"), token_inject=True)
            return

        # ---------------- 自带网页（旧接口，保持兼容）
        if path in ("/", "/index.html"):
            self._file(os.path.join(STATIC_DIR, "index.html"), token_inject=True)
            return
        if path == "/stream":
            self._stream(query)
            return
        if path in ("/snapshot.jpg", "/snapshot"):
            self._snapshot(query)
            return
        if path == "/api/status":
            self._json(self._status_doc(query))
            return
        if path == "/api/config":
            self._json({"ok": True, "config": self.config, "sizes": ALLOWED_SIZES,
                        "fps": ALLOWED_FPS})
            return
        if path == "/api/health":
            self._json({"ok": True, "version": SERVER_VERSION})
            return
        if path == "/api/motion":
            self._json({"ok": True, "motion": self._motion_status()})
            return

        # 静态文件（限制在 static 目录内）
        rel = path.lstrip("/")
        target = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if target.startswith(STATIC_DIR) and os.path.isfile(target):
            self._file(target, token_inject=target.endswith("index.html"))
            return
        self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        routes = {
            "/api/v1/config": self._post_config,
            "/api/v1/motion/arm": self._post_motion_arm,
            "/api/v1/motion/command": self._post_motion_cmd,
            "/api/v1/motion/cmd": self._post_motion_cmd,
            "/api/v1/motion/axes": self._post_motion_axis,
            "/api/v1/motion/axis": self._post_motion_axis,
            "/api/v1/motion/stop": self._post_motion_stop,
            "/api/config": self._post_config,
            "/api/motion/arm": self._post_motion_arm,
            "/api/motion/cmd": self._post_motion_cmd,
            "/api/motion/command": self._post_motion_cmd,
            "/api/motion/axis": self._post_motion_axis,
            "/api/motion/axes": self._post_motion_axis,
            "/api/motion/stop": self._post_motion_stop,
        }
        handler = routes.get(path)
        if handler is None:
            self._json({"ok": False, "error": "unknown endpoint",
                        "detail": "可用接口见 GET /api/v1"}, 404)
            return
        # 急停永远不校验令牌：紧急情况下任何接入方都应该能停下来
        if path not in TOKEN_EXEMPT and not self._require_auth(query):
            return
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
            return
        handler(payload)

    # -------------------------------------------------------------- 文档类
    def _motion_status(self):
        if self.motion:
            try:
                return self.motion.status()
            except Exception as exc:
                return {"enabled": True, "armed": False, "last_error": str(exc)}
        return {"enabled": False, "armed": False, "last_error": "运动控制未初始化"}

    def _info_doc(self):
        st = self.hub.status()
        source = st.get("source") or {}
        return {
            "ok": True,
            "api": API_VERSION,
            "server": SERVER_VERSION,
            "kind": "lite3-camera-web",
            "source": source,
            "modes": list(MODES),
            "sizes": ALLOWED_SIZES,
            "fps": ALLOWED_FPS,
            "motion": {"enabled": bool(self.motion and self.motion.enabled),
                       "axes": ["fwd", "lat", "yaw", "hgt"],
                       "modes": ["move", "pose"]},
            "token_required": bool(self.api_token),
            "endpoints": {
                "info": "/api/v1/info",
                "status": "/api/v1/status",
                "snapshot": "/api/v1/snapshot.jpg?mode=color",
                "stream": "/api/v1/stream?mode=color",
                "commands": "/api/v1/commands",
                "config": "/api/v1/config",
                "motion": "/api/v1/motion",
                "arm": "/api/v1/motion/arm",
                "command": "/api/v1/motion/command",
                "axes": "/api/v1/motion/axes",
                "stop": "/api/v1/motion/stop",
                "openapi": "/api/v1/openapi.json",
            },
        }

    def _commands_doc(self):
        items = []
        for name, code in sorted(MOTION_COMMANDS.items()):
            label, desc = COMMAND_DOC.get(name, (name, ""))
            items.append({"cmd": name, "label": label, "desc": desc,
                          "code": "0x%08X" % code})
        gaits = sorted(MOTION_GAITS.keys()) if MOTION_GAITS else []
        return {"ok": True, "commands": items, "gaits": gaits,
                "commands_hint": "POST /api/v1/motion/command {\"cmd\": \"sit_stand\"}"}

    def _api_index(self):
        self._json({
            "ok": True,
            "api": API_VERSION,
            "server": SERVER_VERSION,
            "message": "绝影 Lite3 相机 + 运动控制后端接口",
            "docs": {
                "openapi": "/api/v1/openapi.json",
                "demo_page": "/api-demo",
                "markdown": "docs/API.md（随项目分发）",
            },
            "quickstart": [
                "GET  /api/v1/info                 能力清单",
                "GET  /api/v1/status               综合状态（画面/配置/运动）",
                "GET  /api/v1/snapshot.jpg?mode=color   取一张 JPEG",
                "GET  /api/v1/stream?mode=color         MJPEG 实时流（放进 <img src>）",
                "POST /api/v1/motion/arm   {\"on\":true}  启用控制（开始发心跳）",
                "POST /api/v1/motion/command {\"cmd\":\"sit_stand\"}",
                "POST /api/v1/motion/axes {\"fwd\":20000}  摇杆（需 20Hz 持续发）",
                "POST /api/v1/motion/stop              急停",
            ],
            "cors": "Access-Control-Allow-Origin: *（任意网站可直接调用）",
            "token_required": bool(self.api_token),
        })

    # -------------------------------------------------------------- 运动控制
    def _motion_or_error(self):
        if not self.motion:
            self._json({"ok": False, "error": "运动控制未初始化"}, 503)
            return None
        if not getattr(self.motion, "enabled", True):
            self._json({"ok": False, "error": "运动控制已关闭（--no-motion）"}, 503)
            return None
        return self.motion

    def _post_motion_cmd(self, payload):
        client = self._motion_or_error()
        if not client:
            return
        name = str(payload.get("cmd") or payload.get("command") or "")
        try:
            result = client.command(name)
        except MOTION_ERROR as exc:      # 未知指令 / 参数问题 → 客户端错误
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        except Exception as exc:         # MotionError 以及套接字异常
            self._json({"ok": False, "error": str(exc)}, 503)
            return
        self._json({"ok": True, "result": result, "motion": self._motion_status()})

    def _post_motion_arm(self, payload):
        client = self._motion_or_error()
        if not client:
            return
        try:
            armed = client.set_arm(bool(payload.get("on", payload.get("armed", False))))
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 503)
            return
        self._json({"ok": True, "armed": armed, "motion": self._motion_status()})

    def _post_motion_axis(self, payload):
        client = self._motion_or_error()
        if not client:
            return
        try:
            axes = client.set_axes(payload)
        except MOTION_ERROR as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 503)
            return
        self._json({"ok": True, "axes": axes})

    def _post_motion_stop(self, payload=None):
        client = self._motion_or_error()
        if not client:
            return
        try:
            client.set_arm(False)
            result = client.command("stop")
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 503)
            return
        self._json({"ok": True, "result": result, "motion": self._motion_status()})

    # -------------------------------------------------------------- 配置
    def _post_config(self, payload):
        cfg = dict(self.config)
        for key in ("width", "height", "fps", "quality", "depth_near", "depth_far"):
            if key in payload:
                try:
                    cfg[key] = int(payload[key]) if key in ("width", "height", "fps", "quality") \
                        else float(payload[key])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "%s 取值非法" % key}, 400)
                    return

        if (cfg["width"], cfg["height"]) not in ALLOWED_SIZES:
            self._json({"ok": False, "error": "不支持的分辨率",
                        "allowed": ALLOWED_SIZES}, 400)
            return
        if cfg["fps"] not in ALLOWED_FPS:
            self._json({"ok": False, "error": "不支持的帧率", "allowed": ALLOWED_FPS}, 400)
            return
        cfg["quality"] = max(30, min(95, int(cfg["quality"])))
        if cfg["depth_far"] <= cfg["depth_near"]:
            cfg["depth_far"] = cfg["depth_near"] + 0.5

        self.config.update(cfg)
        changed = self.hub.reconfigure(
            width=cfg["width"], height=cfg["height"], fps=cfg["fps"],
            quality=cfg["quality"], depth_near=cfg["depth_near"],
            depth_far=cfg["depth_far"],
        )
        self._json({"ok": True, "changed": changed, "config": self.config})

    # -------------------------------------------------------------- stream
    def _stream(self, query):
        mode = (query.get("mode") or ["color"])[0]
        if mode not in MODES:
            mode = "color"
        cid = (query.get("cid") or [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + BOUNDARY)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self._cors()
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        self.clients.touch(cid, mode)
        last_id = 0
        try:
            while True:
                frame = self.hub.wait_next(last_id, timeout=8.0)
                if frame is None or frame.id == last_id:
                    break  # 长时间没有新帧：主动断开，前端会自动重连
                last_id = frame.id
                data, mime = self.hub.render(mode)
                if not data:
                    continue
                head = (
                    "--%s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                    "X-Frame-Id: %d\r\n\r\n" % (BOUNDARY, mime, len(data), last_id)
                ).encode("ascii")
                self.wfile.write(head)
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.clients.frame(cid, len(head) + len(data))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.clients.drop(cid)

    # ------------------------------------------------------------ snapshot
    def _snapshot(self, query):
        mode = (query.get("mode") or ["color"])[0]
        if mode not in MODES:
            mode = "color"
        data, _mime = self.hub.render(mode)
        if not data:
            self._json({"ok": False, "error": "尚无画面"}, 503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", 'inline; filename="lite3-%s.jpg"' % mode)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------- status
    def _status_doc(self, query):
        cid = (query.get("cid") or [""])[0]
        st = self.hub.status()
        st["ok"] = True
        st["api"] = API_VERSION
        st["server"] = SERVER_VERSION
        st["config"] = self.config
        st["motion"] = self._motion_status()
        st["token_required"] = bool(self.api_token)
        if cid:
            st["client"] = self.clients.snapshot(cid)
        st["viewers"] = self.clients.count()
        return st


def openapi_spec(token_required=False):
    """返回 OpenAPI 3.0 描述，可直接导入 Postman / Apifox / 代码生成器。"""
    sec = [{"ApiToken": []}] if token_required else []

    def op(summary, description="", body=None, tags=("control",), security=True,
           params=None):
        item = {"summary": summary, "description": description, "tags": list(tags),
                "responses": {"200": {"description": "OK",
                                      "content": {"application/json": {
                                          "schema": {"type": "object"}}}}}}
        if body is not None:
            item["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": body}},
            }
        if params:
            item["parameters"] = params
        if security and sec:
            item["security"] = sec
        return item

    def q(name, desc, example):
        return {"name": name, "in": "query", "required": False,
                "description": desc, "schema": {"type": "string", "example": example}}

    axes_schema = {
        "type": "object",
        "properties": {
            "fwd": {"type": "integer", "minimum": -32767, "maximum": 32767,
                    "description": "前后 / 俯仰，正=前进"},
            "lat": {"type": "integer", "minimum": -32767, "maximum": 32767,
                    "description": "左右平移 / 横滚，正=右"},
            "yaw": {"type": "integer", "minimum": -32767, "maximum": 32767,
                    "description": "转弯 / 偏航，正=右转"},
            "hgt": {"type": "integer", "minimum": -32767, "maximum": 32767,
                    "description": "身体高度，正=抬高"},
            "mode": {"type": "string", "enum": ["move", "pose"],
                     "description": "move=运动模式语义，pose=姿态模式语义"},
            "speed": {"type": "number", "minimum": 0, "maximum": 1,
                      "description": "可选缩放系数，服务端按该系数缩放各轴"},
        },
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "绝影 Lite3 相机与运动控制 API",
            "version": SERVER_VERSION,
            "description": "把机器狗的相机画面与运动控制开放给第三方网站/程序调用。"
                           "所有写操作在开启令牌时需要 X-API-Token。",
        },
        "servers": [{"url": "/", "description": "当前服务"}],
        "components": {
            "securitySchemes": {
                "ApiToken": {"type": "apiKey", "in": "header", "name": "X-API-Token"},
            },
            "schemas": {
                "Axes": axes_schema,
                "Arm": {"type": "object", "properties": {"on": {"type": "boolean"}},
                        "required": ["on"]},
                "Command": {"type": "object",
                            "properties": {"cmd": {"type": "string",
                                                   "example": "sit_stand"}},
                            "required": ["cmd"]},
                "Config": {"type": "object",
                           "properties": {
                               "width": {"type": "integer"}, "height": {"type": "integer"},
                               "fps": {"type": "integer"}, "quality": {"type": "integer"},
                               "depth_near": {"type": "number"},
                               "depth_far": {"type": "number"}}},
            },
        },
        "paths": {
            "/api/v1/info": {"get": op("能力清单", "信号源、支持的分辨率/帧率、是否启用运动控制、"
                                                   "是否需要令牌，以及全部接口地址。",
                                       tags=("meta",), security=False)},
            "/api/v1/status": {"get": op("综合状态", "画面帧号/帧率/帧龄、编码统计、配置、"
                                                     "运动控制状态、当前观看人数。",
                                         tags=("meta",), security=False,
                                         params=[q("cid", "可选，查询某个观看连接的带宽", "web1")])},
            "/api/v1/commands": {"get": op("指令清单", "所有可用动作指令的名称、中文说明与指令码。",
                                           tags=("meta",), security=False)},
            "/api/v1/config": {
                "get": op("读取配置", "", tags=("media",), security=False),
                "post": op("修改配置", "热更新分辨率/帧率/画质/深度范围，会重连一次信号源。",
                           body={"$ref": "#/components/schemas/Config"}, tags=("media",)),
            },
            "/api/v1/snapshot.jpg": {"get": op(
                "抓取一张 JPEG",
                "适合做静态预览或定时截图；Content-Type: image/jpeg。",
                tags=("media",), security=False,
                params=[q("mode", "color / depth / dual", "color")])},
            "/api/v1/stream": {"get": op(
                "MJPEG 实时流",
                "multipart/x-mixed-replace 长连接，直接放进 <img src=\"...\"> 即可播放；"
                "8 秒没有新帧会主动断开，客户端需自己重连。",
                tags=("media",), security=False,
                params=[q("mode", "color / depth / dual", "color"),
                        q("cid", "可选，观看者标识（统计用）", "web1"),
                        q("token", "开启令牌时用查询参数传令牌（<img> 无法带请求头）", "")])},
            "/api/v1/motion": {"get": op("运动控制状态", "是否启用、是否已启用控制、当前模式/步态、"
                                                       "摇杆值、已发送包数。",
                                         tags=("meta",), security=False)},
            "/api/v1/motion/arm": {"post": op(
                "启用 / 解除控制",
                "启用后服务端以 2Hz 发心跳，并允许下发摇杆指令；解除后摇杆自动归零。",
                body={"$ref": "#/components/schemas/Arm"})},
            "/api/v1/motion/command": {"post": op(
                "下发动作指令",
                "站立/坐下、回零、步态、姿态模式等；指令名见 /api/v1/commands。"
                "急停请用 /api/v1/motion/stop。",
                body={"$ref": "#/components/schemas/Command"})},
            "/api/v1/motion/axes": {"post": op(
                "下发摇杆量",
                "值域 ±32767，需以 ≥20Hz 持续下发；400ms 没有新值服务端自动归零。",
                body={"$ref": "#/components/schemas/Axes"})},
            "/api/v1/motion/stop": {"post": op(
                "急停",
                "立即解除控制并发送软件急停；任何时刻都可调用。",
                body={"type": "object", "properties": {}})},
        },
    }


def serve(hub, clients, config, host="0.0.0.0", port=8080, motion=None, api_token=""):
    Handler.hub = hub
    Handler.clients = clients
    Handler.config = config
    Handler.motion = motion
    Handler.api_token = api_token or ""
    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    return httpd


def lan_addresses():
    """尽量列出本机对外的 IPv4，方便直接告诉用户该访问哪个地址。"""
    found = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    if not found:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            found.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return found
