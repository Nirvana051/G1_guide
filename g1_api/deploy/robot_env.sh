# 真机 AbotClaw 容器内的 ROS 环境前置块。用 source 执行：
#   source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh
# 每个要跑 ROS 相关进程的窗格（roscore/速度桥/g1_api/手动调试）都先来这一句。
#
# ROBOT_IP = PC2 的【对外局域网】IP（笔记本 ssh/RViz/浏览器访问用的那个）。
# 注意与它无关的另一张网：eth0 上的 192.168.123.x 是机器人内部网
# （PC2↔运动控制器↔雷达），DDS 网卡配置和 MID360_config.json 保持 123 不动。

ROBOT_IP="${ROBOT_IP:-192.168.220.8}"

source /opt/ros/noetic/setup.bash
source /home/unitree/abotclaw_nv/navigate/WK/G1Nav2D/devel/setup.bash
export ROS_MASTER_URI=http://${ROBOT_IP}:11311
export ROS_IP=${ROBOT_IP}     # 笔记本 RViz 能订阅话题靠它
unset ROS_HOSTNAME
echo "[robot_env] ROS 环境就绪 (master=$ROS_MASTER_URI, ip=$ROS_IP)"
