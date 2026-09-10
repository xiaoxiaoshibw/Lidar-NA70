#!/usr/bin/env bash
# Jetson 构建与验证脚本
#
# 用途：把 Windows 源码同步到 Jetson 并构建验证，固化
#       ADHOC-20260907-JetsonBuild 的命令序列。
# 首次验证：2026-09-07，Claude Code，colcon build EXIT=0（1min 25s）
#
# 在 Windows 的 bash（Git Bash）中执行：
#   bash tools/jetson_build.sh            # 同步 + 构建 + 验证
#   bash tools/jetson_build.sh --no-sync  # 跳过同步，仅重新构建
#   bash tools/jetson_build.sh --clean    # 先删 build/install 再全量构建
#
# 本脚本只做构建验证，不做实机数据验收（需雷达接入）。

set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson@192.168.4.65}"
JETSON_WS="${JETSON_WS:-/home/jetson/radar_ws}"
PKG="double_wedge_driver"

# 定位仓库根目录：本脚本位于 <root>/tools/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_PKG="${REPO_ROOT}/ROS2/src/${PKG}"

DO_SYNC=1
DO_CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --no-sync) DO_SYNC=0 ;;
    --clean)   DO_CLEAN=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

if [[ ! -d "${LOCAL_PKG}" ]]; then
  echo "找不到源码包：${LOCAL_PKG}" >&2
  exit 1
fi

# Jetson 侧统一的环境前缀。
# --no-daemon 的原因：该机 ros2 daemon 曾处于坏状态（!rclpy.ok()）。
ROS_ENV="source /opt/ros/humble/setup.bash
         export ROS_DOMAIN_ID=43
         export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"

echo "=== 目标 ${JETSON_HOST}:${JETSON_WS} ==="

if [[ ${DO_SYNC} -eq 1 ]]; then
  echo "--- 同步源码（Windows 为唯一真源，不在 Jetson 上直接编辑）---"
  ssh "${JETSON_HOST}" "mkdir -p ${JETSON_WS}/src"
  scp -r "${LOCAL_PKG}" "${JETSON_HOST}:${JETSON_WS}/src/"
fi

if [[ ${DO_CLEAN} -eq 1 ]]; then
  echo "--- 清理 build/install ---"
  ssh "${JETSON_HOST}" "rm -rf ${JETSON_WS}/build ${JETSON_WS}/install"
fi

echo "--- 构建 ---"
# --parallel-workers 1 + MAKEFLAGS=-j2：4GB 内存机型限制并发，避免 OOM。
# rosdep 在该机未初始化，依赖靠 dpkg 预先核对，此处不调用 rosdep。
ssh "${JETSON_HOST}" "
  ${ROS_ENV}
  cd ${JETSON_WS}
  export MAKEFLAGS='-j2'
  colcon build --symlink-install --packages-select ${PKG} \
    --parallel-workers 1 --event-handlers console_direct+ > /tmp/build_${PKG}.log 2>&1
  code=\$?
  echo \"colcon build EXIT=\${code}\"
  if [[ \${code} -ne 0 ]]; then
    echo '--- 错误 ---'
    grep -n 'error:' /tmp/build_${PKG}.log | head -30
    exit \${code}
  fi
  tail -3 /tmp/build_${PKG}.log
"

echo "--- 验证产物 ---"
ssh "${JETSON_HOST}" "
  ${ROS_ENV}
  source ${JETSON_WS}/install/setup.bash
  echo '可执行文件：'
  ros2 pkg executables ${PKG} 2>/dev/null
  echo '消息接口：'
  ros2 interface list 2>/dev/null | grep ${PKG}
"

echo "--- 验证节点启动（无硬件，仅检查初始化与退出）---"
ssh "${JETSON_HOST}" "
  ${ROS_ENV}
  source ${JETSON_WS}/install/setup.bash
  nohup ${JETSON_WS}/install/${PKG}/lib/${PKG}/${PKG}_lidar > /tmp/rt_lidar.log 2>&1 &
  sleep 6
  echo '节点：'; timeout 15 ros2 node list --no-daemon 2>/dev/null
  echo '话题：'; timeout 15 ros2 topic list --no-daemon 2>/dev/null
  echo '状态帧：'
  timeout 12 ros2 topic echo /livox_status --no-daemon --once 2>/dev/null | head -6
  pkill -f ${PKG} || true
  sleep 1
  echo '退出日志：'; tail -2 /tmp/rt_lidar.log
"

echo ""
echo "=== 构建验证完成 ==="
echo "注意：以上不含实机数据验收。connect_state: true 表示雷达断连，属预期。"
echo "构建日志在 Jetson：/tmp/build_${PKG}.log（重启即清，需留存请 scp 取回）"
