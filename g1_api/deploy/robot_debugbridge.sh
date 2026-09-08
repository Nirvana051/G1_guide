# 调试速度桥（终端② 的调试版）＝ 真桥驱动 + 逐秒诊断打印 + 自动录制 bag。
# ⚠️ 机器人会真的走——与 robot_velbridge.sh 完全同源的驱动管线，只是多了：
#   · 每秒一行「指令(raw) → 实发(过滤限幅后)」+ 统计：
#       vy≠0（"歪着走"的指令性来源）· wz 被死区清零（"一节节扭"的量化来源）· 锁定
#   · 后台 rosbag 录制 /cmd_vel /slam_odom /tf /scan，Ctrl-C 停桥时自动收尾，
#     结束打印拉取命令——把 bag 交给开发机做定量分析
#
# 用法：docker exec -it AbotClaw bash
#       source /home/unitree/abotclaw_nv/g1_api/deploy/robot_debugbridge.sh
# 关闭录制：G1_DEBUG_NO_BAG=1 source 本脚本。日常不调试就用 robot_velbridge.sh（安静版）。
# 纯观察不驱动（机器人不动）另有 g1_debug_vel.py，一般用不到。

source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh
export CYCLONEDDS_HOME=/home/unitree/abotclaw_nv/unitree_sdk2_python/cyclonedds/install
export LD_LIBRARY_PATH=$CYCLONEDDS_HOME/lib:$LD_LIBRARY_PATH
export G1_VEL_DEADBAND_WZ="${G1_VEL_DEADBAND_WZ:-0.08}"
export G1_VEL_DEBUG=1

_BAG_PID=""
if [ -z "$G1_DEBUG_NO_BAG" ]; then
  _BAG_DIR=/home/unitree/abotclaw_nv/bags
  mkdir -p "$_BAG_DIR"
  _BAG="$_BAG_DIR/debug_$(date +%m%d_%H%M%S)"
  rosbag record -O "$_BAG" /cmd_vel /slam_odom /tf /tf_static /scan \
      > /tmp/debug_bag_record.log 2>&1 &
  _BAG_PID=$!
  echo "[debug-bridge] 录制中 → ${_BAG}.bag （Ctrl-C 停桥时自动收尾）"
fi

echo "[debug-bridge] ⚠️ 调试桥会真的驱动机器人——确认场地清空、急停在手"
python3 /home/unitree/abotclaw_nv/g1_api/deploy/g1_control_vel.py eth0

if [ -n "$_BAG_PID" ]; then
  kill -INT "$_BAG_PID" 2>/dev/null
  wait "$_BAG_PID" 2>/dev/null
  echo "[debug-bridge] bag 已保存：${_BAG}.bag"
  echo "[debug-bridge] 开发机拉取："
  echo "  sshpass -p 123 scp unitree@192.168.220.8:${_BAG}.bag /home/g1/workspace/project/concierge/G1/st_ws/from_robot/"
fi
