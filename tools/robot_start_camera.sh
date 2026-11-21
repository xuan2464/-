#!/usr/bin/env bash
# 在机器狗感知主机上启动 ROS1 + Intel RealSense 相机节点（幂等、不需要 sudo）
#
# 机器狗自带的 realsense 系统服务默认是 disabled 的，手工拉起 roscore + realsense2_camera
# 效果等价于 `sudo systemctl start realsense`，但不用输密码，也不会占用系统服务。
#
# 用法：
#   bash tools/robot_start_camera.sh             # 没跑就启动，已经在跑就复用
#   bash tools/robot_start_camera.sh --restart   # 强制重启用

RESTART=0
[ "${1:-}" = "--restart" ] && RESTART=1

WS=/home/ysc/lite_cog/drivers/realsense_ws
for f in /opt/ros/noetic/setup.bash "$WS/devel/setup.bash"; do
  [ -f "$f" ] && . "$f" >/dev/null 2>&1
done
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"

has() { command -v "$1" >/dev/null 2>&1; }
say() { printf '  %s\n' "$*"; }

has roscore || { echo "  [错误] 找不到 roscore（ROS1 没装好）"; exit 1; }

# 1) ROS 主节点
if [ "$RESTART" = 1 ] || ! pgrep -f 'rosmaster --core' >/dev/null 2>&1; then
  pkill -f 'rosmaster --core' 2>/dev/null
  sleep 1
  nohup roscore >/tmp/roscore.log 2>&1 &
  say "已启动 roscore（日志 /tmp/roscore.log）"
  sleep 4
fi

# 2) 相机节点：640x480@30，深度对齐到彩色（对齐了才能在网页上做彩色+深度叠加）
if [ "$RESTART" = 1 ] || ! pgrep -f 'realsense2_camera' >/dev/null 2>&1; then
  pkill -f 'roslaunch realsense2_camera' 2>/dev/null
  pkill -f 'nodelet load realsense2_camera' 2>/dev/null
  sleep 2
  nohup roslaunch realsense2_camera rs_camera.launch \
    enable_color:=true enable_depth:=true align_depth:=true \
    color_width:=640 color_height:=480 color_fps:=30 \
    depth_width:=640 depth_height:=480 depth_fps:=30 \
    filters:= >/tmp/realsense_launch.log 2>&1 &
  say "已启动 realsense2_camera（日志 /tmp/realsense_launch.log）"
fi

# 3) 等第一帧，确认真的出图
python3 - <<'PY'
import sys
try:
    import rospy
    from sensor_msgs.msg import Image
    rospy.init_node('lite3_cam_wait', anonymous=True, disable_signals=True)
    msg = rospy.wait_for_message('/camera/color/image_raw', Image, timeout=25)
    print('  [ok] 相机出图 %dx%d %s' % (msg.width, msg.height, msg.encoding))
except Exception as exc:
    print('  [错误] 等不到相机画面：%s' % exc)
    print('      排查：tail -n 40 /tmp/realsense_launch.log')
    print('      若提示设备被占用，先在终端跑：sudo systemctl stop realsense')
    sys.exit(1)
PY
