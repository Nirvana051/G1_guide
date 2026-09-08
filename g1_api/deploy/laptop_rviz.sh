#!/bin/bash
# 笔记本/开发机上的远程 RViz。用法：
#   ./laptop_rviz.sh mapping   # 建图视角（local 系，点云+投影图）
#   ./laptop_rviz.sh nav       # 导航视角（map 系，栅格+激光+路径）
# 自动探测本机 123 网段 IP 作为 ROS_IP。
set -e
MODE="${1:-nav}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROBOT_IP="${ROBOT_IP:-192.168.220.8}"
SUBNET="$(echo "$ROBOT_IP" | cut -d. -f1-3)"

source /opt/ros/noetic/setup.bash
export ROS_MASTER_URI=http://${ROBOT_IP}:11311
MY_IP=$(hostname -I | tr ' ' '\n' | grep "^${SUBNET//./\\.}\." | head -1)
if [ -z "$MY_IP" ]; then
  echo "本机没有 ${SUBNET}.x 地址——先接入机器人所在网段"; exit 1
fi
export ROS_IP="$MY_IP"
unset ROS_HOSTNAME
echo "[laptop_rviz] master=$ROS_MASTER_URI  my_ip=$ROS_IP  mode=$MODE"

case "$MODE" in
  mapping) exec rviz -d "$HERE/rviz/mapping.rviz" ;;
  nav)     exec rviz -d "$HERE/rviz/navigation.rviz" ;;
  *)       echo "用法: $0 mapping|nav"; exit 1 ;;
esac
