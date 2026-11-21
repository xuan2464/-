#!/usr/bin/env bash
# 在机器狗感知主机上执行：检查依赖 → 释放相机 → 启动网页服务 → 确认真的出画面
# 由 连接机器狗.bat / connect.ps1 自动调用，也可以自己手动跑：
#   bash ~/camera_web/tools/robot_setup.sh                 # 自动挑信号源
#   bash ~/camera_web/tools/robot_setup.sh --source ros    # 用 realsense-ros 话题
#   bash ~/camera_web/tools/robot_setup.sh --source realsense  # pyrealsense2 直连

# 注意：这里**不能**用 set -u。ROS 的 setup.bash 内部会引用未定义变量
# （例如 _CATKIN_SETUP_DIR_OLD），一旦开启 nounset，source 就会立刻杀掉整个脚本，
# 表现就是"什么错误都没打印，SSH 连接直接断开"。

SOURCE=auto
PORT=8080
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:-auto}"; shift 2 ;;
    --port)   PORT="${2:-8080}"; shift 2 ;;
    *) shift ;;
  esac
done

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR" || { echo "[错误] 进不去项目目录"; exit 1; }

line() { printf '%s\n' "------------------------------------------------------------"; }
say()  { printf '  %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
err()  { printf '  [错误] %s\n' "$*"; }
has()  { command -v "$1" >/dev/null 2>&1; }

# 载入 ROS 环境（有就载，没有返回 1）
# 只认 ROS1：/opt/ros/*/setup.bash 里带 bin/roscore 的才是（foxy 是 ROS2，跳过），
# 否则 ROS_DISTRO 会被 ROS2 的 setup.bash 覆盖成 foxy，日志和排查都会误导人。
load_ros() {
  [ -n "${ROS_DISTRO:-}" ] && has rosnode && return 0
  for f in /opt/ros/noetic/setup.bash /opt/ros/melodic/setup.bash /opt/ros/*/setup.bash; do
    [ -f "$f" ] || continue
    [ -x "$(dirname "$f")/bin/roscore" ] || continue
    . "$f" >/dev/null 2>&1 || true
    break
  done
  # 机器狗自带的 realsense ROS1 工作空间
  for f in /home/ysc/lite_cog/drivers/realsense_ws/devel/setup.bash; do
    [ -f "$f" ] && { . "$f" >/dev/null 2>&1 || true; }
  done
  [ -n "${ROS_DISTRO:-}" ] && has rosnode
}

start_service() {
  if has pgrep && pgrep -f "run.py --source" >/dev/null 2>&1; then
    pkill -f "run.py --source" 2>/dev/null
    sleep 1
  fi
  if has setsid; then
    setsid nohup python3 run.py --source "$1" --port "$PORT" \
      > "$DIR/viewer.log" 2>&1 < /dev/null &
  else
    nohup python3 run.py --source "$1" --port "$PORT" \
      > "$DIR/viewer.log" 2>&1 < /dev/null &
  fi
  echo $! > "$DIR/viewer.pid"
  sleep 3
  if has pgrep && pgrep -f "run.py --source" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

# 用项目自带的接口问一下：到现在为止收到多少帧？
frames() {
  python3 - "$PORT" <<'PY' 2>/dev/null || echo -1
import json, sys, urllib.request
try:
    url = "http://127.0.0.1:%s/api/status" % sys.argv[1]
    d = json.load(urllib.request.urlopen(url, timeout=3))
    print(int(d["frame"]["total"]))
except Exception:
    print(-1)
PY
}

wait_frames() {
  n=-1
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    n=$(frames)
    case "$n" in ''|*[!0-9-]*) n=-1 ;; esac
    [ "$n" -gt 0 ] 2>/dev/null && break
    sleep 1
  done
  echo "$n"
}

echo ""
say "绝影 Lite3 · 相机画面服务部署"
line
say "项目目录 $DIR"
say "端口     $PORT"
line

# ---------------------------------------------------------------- 1. Python
if ! has python3; then
  err "找不到 python3"
  exit 1
fi
ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"

# ---------------------------------------------------------------- 2. 依赖
if python3 -c "import numpy" >/dev/null 2>&1; then
  ok "numpy 已安装"
else
  err "缺少 numpy，请先安装：sudo apt install python3-numpy"
  exit 1
fi

if python3 -c "import PIL" >/dev/null 2>&1; then
  ok "Pillow 已安装（JPEG 编码）"
elif python3 -c "import cv2" >/dev/null 2>&1; then
  ok "OpenCV 已安装（JPEG 编码）"
else
  err "缺少图像编码库，两个装一个即可："
  say "      sudo apt install python3-opencv"
  say "      python3 -m pip install pillow"
  exit 1
fi

# ---------------------------------------------------------------- 3. 挑信号源
if [ "$SOURCE" = "auto" ]; then
  if python3 -c "import pyrealsense2" >/dev/null 2>&1; then
    SOURCE=realsense
    ok "检测到 pyrealsense2，直接读相机"
  elif load_ros; then
    SOURCE=ros
    ok "改用 realsense-ros 话题取图（ROS $ROS_DISTRO，不用停服务）"
  else
    err "既没有 pyrealsense2，也没有可用的 ROS 环境，取不到相机画面"
    say "  装 pyrealsense2 或装 ROS1(noetic) + realsense2_camera 之后再来"
    exit 1
  fi
fi
say "信号源   $SOURCE"

# ---------------------------------------------------------------- 4. ROS 环境
if [ "$SOURCE" = "ros" ] && ! load_ros; then
  err "ROS 环境没配好，先 source /opt/ros/<发行版>/setup.bash 再运行"
  exit 1
fi
[ "$SOURCE" = "ros" ] && ok "ROS 环境 ${ROS_DISTRO:-?}"

# ---------------------------------------------------------------- 4.5 相机节点
# 走 ROS 话题时，先确保 roscore 和 realsense2_camera 都在跑（不需要 sudo）
if [ "$SOURCE" = "ros" ] && [ -f "$DIR/tools/robot_start_camera.sh" ]; then
  say "启动相机节点（roscore + realsense2_camera，不需要密码）…"
  if ! bash "$DIR/tools/robot_start_camera.sh"; then
    warn "相机节点没起来，继续启动服务，出不了图会有明确报错"
  fi
fi

# ---------------------------------------------------------------- 5. 释放相机
if [ "$SOURCE" = "realsense" ]; then
  if has systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^realsense'; then
    if systemctl is-active --quiet realsense 2>/dev/null; then
      warn "realsense 服务正占用相机，需要停掉它（要输一次密码：一个英文单引号 '）"
      if sudo systemctl stop realsense; then
        ok "realsense 服务已停止"
      else
        warn "停服务失败（多半是 sudo 密码没输对），先直接用相机试一次"
      fi
    else
      ok "realsense 服务未运行，相机可用"
    fi
  else
    warn "没有 realsense 系统服务，跳过；相机若被占用请手动释放"
  fi
fi

# ---------------------------------------------------------------- 6. 启动 + 自检
line
say "启动服务（信号源 $SOURCE，端口 $PORT）…"
if ! start_service "$SOURCE"; then
  err "服务没起来，日志如下："
  tail -n 25 "$DIR/viewer.log" 2>/dev/null
  exit 1
fi
ok "服务进程已启动（日志 $DIR/viewer.log）"

N=$(wait_frames)
if [ "$N" -gt 0 ] 2>/dev/null; then
  ok "已经收到 $N 帧画面"
else
  warn "10 秒内没收到画面，日志如下"
  tail -n 12 "$DIR/viewer.log" 2>/dev/null | sed 's/^/      /'

  # 直连相机没出图 → 试 ROS 话题
  if [ "$SOURCE" = "realsense" ] && load_ros; then
    warn "直连相机没出图，自动改用 ROS 话题再试一次"
    if [ -f "$DIR/tools/robot_start_camera.sh" ]; then
      bash "$DIR/tools/robot_start_camera.sh" || warn "相机节点没起来"
    fi
    SOURCE=ros
    start_service ros && N=$(wait_frames)
    if [ "${N:-0}" -gt 0 ] 2>/dev/null; then
      ok "改用 ROS 话题后收到 $N 帧画面"
      N=0
    fi
  fi

  # 还是没图 → 直接报错，不退回任何假画面
  if [ "${N:-0}" -le 0 ] 2>/dev/null; then
    err "相机确实没出画面（信号源 $SOURCE），服务已起但流媒体为空"
    say "排查顺序："
    say "  1) tail -n 60 $DIR/viewer.log              看具体报错"
    say "  2) rs-enumerate-devices                    看相机有没有枚举到"
    say "  3) bash $DIR/tools/robot_start_camera.sh --restart"
    say "  4) sudo systemctl stop realsense           释放被系统服务占用的相机"
    exit 1
  fi
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "${IP:-}" ] && IP="192.168.1.103"

if has curl && curl -fsS --max-time 4 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  ok "接口自检通过"
fi

line
sed -n '1,12p' "$DIR/viewer.log" 2>/dev/null | sed 's/^/  /'
line
say "浏览器打开  http://$IP:$PORT"
say "看日志      tail -f $DIR/viewer.log"
say "停止服务    pkill -f 'run.py --source'"
say "换信号源    bash $DIR/tools/robot_setup.sh --source ros|realsense"
line
ok "部署脚本执行完毕"
echo ""
