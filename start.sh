#!/usr/bin/env bash
# 绝影 Lite3 相机网页画面 —— 一键启动（Linux / 机器狗感知主机）
#   ./start.sh                      自动选信号源
#   ./start.sh --source ros         从 realsense-ros 话题取图
#   ./start.sh --source demo --open 无相机时看界面
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

exec "$PY" run.py "$@"
