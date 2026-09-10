#!/usr/bin/env bash
# 在 WSL2 中回放 NA70 pcap，并用 RViz2 显示合并后的 PointCloud2。
# 用法：bash tools/wsl2_pcap_rviz.sh [pcap] [rate]

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ros_ws="${repo_dir}/ROS2"
pcap="${1:-${repo_dir}/radar_capture_data/2.pcap}"
rate="${2:-1000}"
log_dir="/tmp/na70_wsl2_rviz"

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "错误：没有找到 /opt/ros/humble/setup.bash" >&2
  exit 1
fi
if [[ ! -f "${pcap}" ]]; then
  echo "错误：pcap 不存在：${pcap}" >&2
  exit 2
fi
if [[ ! -f "${ros_ws}/install/setup.bash" ]]; then
  echo "错误：驱动尚未编译。请先在 ${ros_ws} 执行 colcon build。" >&2
  exit 1
fi
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "错误：WSLg 显示变量不存在，当前会话无法打开 RViz2。" >&2
  exit 1
fi

# 一组 LidarScanBundle 约 320 KiB，WSL 默认 208 KiB socket 缓冲会丢分片。
# 这里只修改当前 WSL 内核实例；WSL 关闭后会自动恢复默认值。
if (( $(sysctl -n net.core.rmem_max) < 16777216 )); then
  if ! sudo -n sysctl -q -w net.core.rmem_max=16777216 net.core.wmem_max=16777216; then
    echo "错误：需要把 WSL socket 缓冲提高到 16 MiB。" >&2
    echo "请从 Windows PowerShell 运行 tools/wsl2_pcap_rviz.ps1。" >&2
    exit 1
  fi
fi

mkdir -p "${log_dir}"
rm -f "${log_dir}"/*.log

# ROS 的 setup 脚本会读取未定义变量，source 时暂时关闭 nounset。
set +u
source /opt/ros/humble/setup.bash
source "${ros_ws}/install/setup.bash"
set -u

export ROS_DOMAIN_ID=43
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://${ros_ws}/config/cyclonedds_wsl.xml"

lidar_pid=""
merge_pid=""
replay_pid=""
rviz_pid=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "${replay_pid}" "${rviz_pid}" "${merge_pid}" "${lidar_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[1/4] 启动 UDP 驱动（127.0.0.1:2368 -> /lidar_data）"
ros2 launch double_wedge_driver start_lidar.launch.py \
  >"${log_dir}/lidar.log" 2>&1 &
lidar_pid=$!

echo "[2/4] 启动点云合并节点（/lidar_data -> /inno_lidar_cloud）"
ros2 launch double_wedge_driver test_lidar.launch.py use_rviz:=false \
  >"${log_dir}/merge.log" 2>&1 &
merge_pid=$!
sleep 3

echo "[3/4] 循环回放 $(basename "${pcap}")，速率 ${rate} 包/秒"
(
  while true; do
    python3 "${repo_dir}/tools/replay_pcap.py" "${pcap}" --rate "${rate}"
  done
) >"${log_dir}/replay.log" 2>&1 &
replay_pid=$!
sleep 3

echo "[4/4] 启动 RViz2；关闭 RViz 窗口即可停止全部进程"
echo "日志目录：${log_dir}"
rviz2 -d "${ros_ws}/src/double_wedge_driver/config/inno_lidar_rviz.rviz" \
  >"${log_dir}/rviz.log" 2>&1 &
rviz_pid=$!

wait "${rviz_pid}"
