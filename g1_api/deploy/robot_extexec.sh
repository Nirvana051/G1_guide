# 外部动作执行器（g1_replay_min 的 HTTP 包装）。容器内交互 shell 里 source 执行：
#   docker exec -it AbotClaw bash
#   source /home/unitree/abotclaw_nv/g1_api/deploy/robot_extexec.sh
#
# ⚠️ 它执行的回放会直接驱动双臂（rt/arm_sdk，与运控共存但手臂真动）——
#    机器人须已站立、周围无人、急停在手。
# 前置：g1_replay_min 已 scp 到 /home/unitree/abotclaw_nv/g1_replay_min
# g1_api 侧对应配置在 robot_g1api.sh（G1_API_TOUR_EXTERNAL_EXECUTOR_URL 指到这里）。

source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh
conda activate g1_agent || echo "[extexec] conda activate 失败——确认在交互 shell 里 source 本脚本"
export CYCLONEDDS_HOME=/home/unitree/abotclaw_nv/unitree_sdk2_python/cyclonedds/install
export LD_LIBRARY_PATH=$CYCLONEDDS_HOME/lib:$LD_LIBRARY_PATH
export UNITREE_NIC=eth0    # DDS 内网网卡，别改
echo "[extexec] 启动外部动作执行器 :9100（Ctrl-C 停止）"
python3 /home/unitree/abotclaw_nv/g1_replay_min/action_server.py
