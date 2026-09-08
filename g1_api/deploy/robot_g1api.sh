# g1_api 真机启动脚本（日常 SOP 开关组合）。在容器内的交互 shell 里用 source 执行：
#   docker exec -it AbotClaw bash
#   source /home/unitree/abotclaw_nv/g1_api/deploy/robot_g1api.sh
# 前提：roscore 已在另一窗格长驻（source robot_env.sh && roscore）。
# 注意：必须 source（不要 bash 执行），conda activate 依赖交互 shell 的初始化。

source /home/unitree/abotclaw_nv/g1_api/deploy/robot_env.sh
conda activate g1_agent || echo "[robot_g1api] conda activate 失败——确认在交互 shell 里 source 本脚本"

cd /home/unitree/abotclaw_nv/g1_api

export G1_API_MODE=real
export G1_API_MODE_NETWORK_INTERFACE=eth0   # DDS 走机器人内部网(123网段)，与对外 IP 无关，别改
export G1_API_SERVER_HOST=0.0.0.0

# planner 选择：aero=源码编译的 AeroMazePlanner-ST-nav overlay（默认），
#   分层到点：位置由 /move_base/result 判，朝向由 g1_api 旋转阶段完成。
#   要临时回课程栈：启动前 export G1_API_PLANNER=course 再 source 本脚本。
# aero 前提：overlay 已编译（容器内跑过一次
#   bash /home/unitree/abotclaw_nv/g1_api/deploy/robot_build_aero.sh）。
G1_API_PLANNER="${G1_API_PLANNER:-aero}"
if [ "$G1_API_PLANNER" = "aero" ]; then
  export G1_API_MODE_LAUNCH_FILE=/home/unitree/abotclaw_nv/g1_api/deploy/g1_api_navigation_aero.launch
  export G1_API_MODE_NAV_FEEDBACK=planner
  export G1_API_MODE_NAV_LAUNCH_SETUP=/home/unitree/abotclaw_nv/navigate/AeroMaze_ws/devel/setup.bash
else
  export G1_API_MODE_LAUNCH_FILE=/home/unitree/abotclaw_nv/g1_api/deploy/g1_api_navigation.launch
  unset G1_API_MODE_NAV_FEEDBACK G1_API_MODE_NAV_LAUNCH_SETUP
fi
export G1_API_MODE_MAPPING_LAUNCH_FILE=/home/unitree/abotclaw_nv/g1_api/deploy/g1_api_mapping.launch
export G1_API_HAND_ROBOT_IP=127.0.0.1      # 手部 server 与 g1_api 同机(host网络)，回环最稳、换网段免改
export G1_API_MAP_MAP_STORAGE_DIR=/home/unitree/abotclaw_nv/g1_api_maps   # 地图库持久化到挂载卷

# 外部动作（回放）：库落挂载卷持久化；执行器 = g1_replay_min/action_server.py
# （另开窗格 source robot_extexec.sh 启动；没启动时执行接口会 409 提示）。
export G1_API_TOUR_EXTERNAL_ACTIONS_DIR=/home/unitree/abotclaw_nv/g1_api_external_actions
export G1_API_TOUR_EXTERNAL_EXECUTOR_URL=http://127.0.0.1:9100/execute

# TTS 默认英文（0=中文 1=English；单次请求可用 speaker_id 覆盖）
export G1_API_VOICE_DEFAULT_SPEAKER_ID=1

# 日常开关组合：除 ALLOW_MOTION（不避障遥控）外全开。
# 首次部署请注释掉这块，按手册第 04 节分层逐层打开。
export G1_API_SAFETY_ALLOW_NAVIGATION=true
export G1_API_SAFETY_ALLOW_MAP_WRITE=true
export G1_API_SAFETY_ALLOW_ARM=true
export G1_API_SAFETY_ALLOW_HAND=true
export G1_API_SAFETY_ALLOW_VOICE=true
export G1_API_SAFETY_ALLOW_TOUR=true
# export G1_API_SAFETY_ALLOW_MOTION=true   # 仅有人持急停在场时临时打开

echo "[robot_g1api] planner=${G1_API_PLANNER} 启动 g1_api → http://${ROBOT_IP:-192.168.220.8}:1448 （/sdk 调试台, /sdk/map-editor 地图编辑器）"
python3 -m g1_api
