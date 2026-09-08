#!/usr/bin/env bash
# =============================================================================
# 在新电脑上建立 g1_replay_min 的运行环境
#
#   bash setup_env.sh              # 默认：venv + pip，约 501 MB
#   NO_PIN=1 bash setup_env.sh     # 不装 pinocchio，约 118 MB（无重力补偿）
#   VENV=/opt/g1venv bash setup_env.sh
#
# 前提：
#   - Linux x86_64
#   - Python 3.10（硬性要求，见下）
#   - 能访问 PyPI（约 200 MB 下载）
#
# 为什么必须 Python 3.10：
#   cyclonedds 0.10.2 的 linux x86_64 wheel 只覆盖 cp37/38/39/310
#   pin        4.1.0  的 linux x86_64 wheel 只覆盖 cp310~cp314
#   交集只有 cp310。其它版本会退化成源码编译并失败
#   （报错形如 "Could not locate cyclonedds. Try to set CYCLONEDDS_HOME"）
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV:-$HERE/.venv}"
SDK="${SDK:-$HERE/vendor/unitree_sdk2_python}"
NO_PIN="${NO_PIN:-0}"

info() { echo -e "\033[1;32m[SETUP]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN ]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; exit 1; }

# ---------- 1. 找一个 3.10 的解释器 ----------
PY=""
for c in "${PYTHON:-}" python3.10 python3 python; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
    if [ "$v" = "3.10" ]; then PY="$c"; break; fi
    [ -n "$PY" ] || warn "$c 是 Python $v，不是 3.10，继续找"
done
[ -n "$PY" ] || die "找不到 Python 3.10。装一个再来：
    sudo apt install python3.10 python3.10-venv
  或者用 conda：
    conda create -n g1_replay_min -c conda-forge python=3.10 numpy pinocchio -y"
info "使用解释器: $PY ($($PY -V 2>&1))"

# ---------- 2. 建 venv ----------
if [ -d "$VENV" ]; then
    warn "$VENV 已存在，将复用（要重建就先删掉它）"
else
    info "创建虚拟环境 $VENV ..."
    "$PY" -m venv "$VENV" || die "venv 创建失败，可能缺 python3.10-venv 包"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install -q --upgrade pip setuptools wheel

# ---------- 3. 依赖 ----------
info "安装 numpy + cyclonedds（必需）..."
pip install -q numpy "cyclonedds==0.10.2"

if [ "$NO_PIN" = "1" ]; then
    warn "NO_PIN=1，跳过 pinocchio —— 前馈力矩恒为零，手臂会下垂约 1.8 度"
else
    info "安装 pinocchio（重力补偿，约 130 MB）..."
    pip install -q "pin==4.1.0"
fi

# ---------- 4. SDK ----------
[ -d "$SDK" ] || die "找不到 SDK 源码: $SDK
  用 SDK=<路径> 指定，或联网克隆：
    git clone https://github.com/unitreerobotics/unitree_sdk2_python $HERE/vendor/unitree_sdk2_python"
info "安装 unitree_sdk2py（--no-deps 以避开它声明的 opencv-python，回放用不到）..."
pip install -q -e "$SDK" --no-deps --no-build-isolation

# ---------- 5. 自检 ----------
info "自检..."
python "$HERE/replay_min.py" --dry-run || die "dry-run 未通过"

echo
info "===================== 完成 ====================="
info "环境大小: $(du -sh "$VENV" | cut -f1)"
info "以后每次使用："
info "    source $VENV/bin/activate"
info "    cd $HERE && python replay_min.py --dry-run"
info "接机器人后还需要（见 README 第 4 节）："
info "    sudo ip addr add 192.168.123.222/24 dev <网卡名>"
info "    export UNITREE_NIC=<网卡名>      # 多网卡时"
info "================================================"
