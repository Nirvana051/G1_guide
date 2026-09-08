#!/bin/bash
# 在机器人 AbotClaw 容器内把 AeroMazePlanner-ST-nav 编成 overlay 工作区。
# 前置（本机执行，把 planner 源码传上去）：
#   sshpass -p 123 ssh unitree@192.168.220.8 "mkdir -p /home/unitree/abotclaw_nv/navigate/AeroMaze_ws/src"
#   sshpass -p 123 scp -r /home/g1/workspace/project/concierge/G1/st_ws/navigate/AeroMaze_ws/src/AeroMazePlanner-ST-nav unitree@192.168.220.8:/home/unitree/abotclaw_nv/navigate/AeroMaze_ws/src/
# 然后在容器里跑本脚本：
#   docker exec -it AbotClaw bash
#   bash /home/unitree/abotclaw_nv/g1_api/deploy/robot_build_aero.sh
# 编译产物在 AeroMaze_ws/{build,devel}，挂载卷里持久化，只需编一次。
set -e

WS=/home/unitree/abotclaw_nv/navigate/AeroMaze_ws
COURSE_DEVEL=/home/unitree/abotclaw_nv/navigate/WK/G1Nav2D/devel/setup.bash

# 只编 aero 导航链路需要的包；amcl/dwa/mbf-nav 等一概跳过。
# map_server 必须在列：overlay 的 ROS_PACKAGE_PATH 会让 fork 源码树遮蔽 apt 包，
# 没编译的遮蔽包=可执行文件缺失，gridmap_load 里的 map_server 会静默起不来。
WHITELIST="robot_settings;costmap_2d;voxel_grid;nav_core;base_local_planner;navfn;global_planner;clear_costmap_recovery;rotate_recovery;move_base;map_server;teb_local_planner;costmap_converter;mbf_abstract_core;mbf_costmap_core;mbf_msgs;mbf_utility"

[ -d "$WS/src/AeroMazePlanner-ST-nav" ] || {
  echo "缺源码：先按脚本头部的 scp 命令把 AeroMazePlanner-ST-nav 传到 $WS/src/"; exit 1; }

# teb 从源码编需要 g2o 头文件与 SuiteSparse；map_server 需要 SDL 头文件。
if ! dpkg -s ros-noetic-libg2o >/dev/null 2>&1 || ! dpkg -s libsuitesparse-dev >/dev/null 2>&1 \
   || ! dpkg -s libsdl-image1.2-dev >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y -qq ros-noetic-libg2o libsuitesparse-dev ros-noetic-cmake-modules \
      libsdl1.2-dev libsdl-image1.2-dev libyaml-cpp-dev \
    || { echo "apt 装依赖失败（容器没网？）——需要 ros-noetic-libg2o libsuitesparse-dev ros-noetic-cmake-modules libsdl1.2-dev libsdl-image1.2-dev libyaml-cpp-dev"; exit 1; }
fi

source /opt/ros/noetic/setup.bash
source "$COURSE_DEVEL"
cd "$WS"

# 容器里 miniconda 的 python 排在 PATH 前面，catkin 会误抓它（conda python 没有
# empy/catkin_pkg，报 "Unable to find ... 'em'"）。强制系统 python，并清掉
# 已被 conda python 污染的 CMake 缓存。
if grep -qs miniconda build/CMakeCache.txt 2>/dev/null; then
  echo "检测到 conda python 污染的 CMake 缓存，清理 build/ 重新配置 ..."
  rm -rf "$WS/build"
fi
catkin_make -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3 \
  -DCATKIN_WHITELIST_PACKAGES="$WHITELIST"

echo
echo "AeroMaze overlay 编译完成: $WS/devel"
echo "启用：启动 g1_api 前 export G1_API_PLANNER=aero，再 source robot_g1api.sh"
