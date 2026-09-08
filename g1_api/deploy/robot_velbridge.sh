# 速度桥（/cmd_vel → LocoClient）。容器内交互 shell 里 source 执行：
#   docker exec -it AbotClaw bash
#   source /home/unitree/abotclaw_nv/g1_api/deploy/robot_velbridge.sh
#
# ⚠️ 它一起来，/cmd_vel 上的任何速度都会让机器人真的走——
#    启动前确认：机器人已进走跑模式、周围场地清空、急停在手。
# 用 g1_api 管理版桥（课程原件不动）：限幅 0.8/0.2/0.8，死区 0.1/0.1/0.15，
# 连续5帧零速锁存。想临时改限幅/死区：启动前 export G1_VEL_MAX_VX=0.5 等。
# eth0 是 DDS 内网网卡，别改。

source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh
export CYCLONEDDS_HOME=/home/unitree/abotclaw_nv/unitree_sdk2_python/cyclonedds/install
export LD_LIBRARY_PATH=$CYCLONEDDS_HOME/lib:$LD_LIBRARY_PATH
# 抗蛇形：wz 死区 0.15 会把 TEB 边走边发的小航向修正(0.05~0.1)清零，
# 误差攒够才猛拐——表现为一节一节地扭。降到 0.08 让小修正连续通过。
export G1_VEL_DEADBAND_WZ="${G1_VEL_DEADBAND_WZ:-0.08}"

# 实走录制（分析"扭/歪"要用真实运动数据）：G1_VEL_RECORD=1 source 本脚本，
# 边走边录 /cmd_vel /slam_odom /tf /scan，Ctrl-C 停桥时 bag 自动收尾。
_BAG_PID=""
if [ -n "$G1_VEL_RECORD" ]; then
  _BAG_DIR=/home/unitree/abotclaw_nv/bags
  mkdir -p "$_BAG_DIR"
  _BAG="$_BAG_DIR/walk_$(date +%m%d_%H%M%S)"
  rosbag record -O "$_BAG" /cmd_vel /slam_odom /tf /tf_static /scan \
      > /tmp/walk_bag_record.log 2>&1 &
  _BAG_PID=$!
  echo "[velbridge] 实走录制中 → ${_BAG}.bag"
fi

echo "[velbridge] 启动 /cmd_vel 桥（Ctrl-C 停止；停止后 velocity_bridge_down 互锁会亮）"
python3 /home/unitree/abotclaw_nv/g1_api/deploy/g1_control_vel.py eth0

if [ -n "$_BAG_PID" ]; then
  kill -INT "$_BAG_PID" 2>/dev/null
  wait "$_BAG_PID" 2>/dev/null
  echo "[velbridge] bag 已保存：${_BAG}.bag"
  echo "  sshpass -p 123 scp unitree@192.168.220.8:${_BAG}.bag /home/g1/workspace/project/concierge/G1/st_ws/from_robot/"
fi
