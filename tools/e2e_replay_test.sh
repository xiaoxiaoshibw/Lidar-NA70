#!/usr/bin/env bash
# PCAP 回放端到端测试 —— 在 Jetson 上本地执行
#
# 为什么是脚本而不是一串 ssh 命令：回放 + 话题采样要跑 60s 以上，
# 通过 SSH 直接跑会因连接超时把整个进程组带走（实测踩到两次）。
# 脚本内部自洽，配合 tmux/nohup 脱离 SSH 生命周期。
#
# 用法（在 Jetson 上）：
#   bash e2e_replay_test.sh <pcap路径> [包数]
#
# 产物：/tmp/e2e_result/ 下的日志与采样结果

set -uo pipefail

PCAP="${1:?用法: $0 <pcap路径> [包数]}"
COUNT="${2:-40000}"
WS=/home/jetson/radar_ws
OUT=/tmp/e2e_result

rm -rf "${OUT}"; mkdir -p "${OUT}"

# ROS2 的 setup.bash 会引用未定义的 AMENT_TRACE_SETUP_FILES 等变量，
# 与 set -u 冲突。source 期间临时关掉，之后恢复。
set +u
source /opt/ros/humble/setup.bash
source "${WS}/install/setup.bash"
set -u

export ROS_DOMAIN_ID=43
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

BIN="${WS}/install/double_wedge_driver/lib/double_wedge_driver"

cleanup() { pkill -f double_wedge_driver 2>/dev/null; pkill -f replay_pcap 2>/dev/null; }
trap cleanup EXIT
cleanup; sleep 1

echo "=== 启动驱动节点 ===" | tee "${OUT}/summary.txt"
nohup "${BIN}/double_wedge_driver_lidar"      > "${OUT}/lidar_node.log" 2>&1 &
# test 节点的点数统计走 RCLCPP_DEBUG，默认 INFO 级别看不到。
# 开 debug 才能拿到「合并后多少点」这个关键证据。
nohup "${BIN}/double_wedge_driver_test_lidar" --ros-args --log-level debug \
  > "${OUT}/test_node.log" 2>&1 &
sleep 5

echo "=== 启动回放（${COUNT} 包 @1000pps）===" | tee -a "${OUT}/summary.txt"
nohup python3 "${WS}/replay_pcap.py" "${PCAP}" --count "${COUNT}" --rate 1000 \
  > "${OUT}/replay.log" 2>&1 &
REPLAY_PID=$!
sleep 15   # 等数据流稳定，再采样话题。0.1s 一组 bundle，15s = 150 组，足够 hz 采样

{
  echo "=== 回放中的话题列表 ==="
  timeout 15 ros2 topic list 2>/dev/null

  echo ""
  echo "=== /lidar_data 频率 ==="
  timeout 20 ros2 topic hz /lidar_data 2>&1 | head -5

  echo ""
  echo "=== /inno_lidar_cloud 频率 ==="
  timeout 20 ros2 topic hz /inno_lidar_cloud 2>&1 | head -5

  echo ""
  echo "=== /inno_lidar_cloud 话题信息 ==="
  timeout 15 ros2 topic info /inno_lidar_cloud 2>&1

  echo ""
  echo "=== PointCloud2 字段与头信息（关键证据：是否有数据、frame_id、字段名）==="
  timeout 20 ros2 topic echo /inno_lidar_cloud --once 2>/dev/null \
    | head -80

  echo ""
  echo "=== /livox_data 头信息 ==="
  timeout 15 ros2 topic echo /livox_data --once 2>/dev/null | head -30
} >> "${OUT}/summary.txt" 2>&1

wait "${REPLAY_PID}" 2>/dev/null
sleep 2

{
  echo ""
  echo "=== 回放输出 ==="
  tail -12 "${OUT}/replay.log"
  echo ""
  echo "=== lidar 节点日志 ==="
  tail -20 "${OUT}/lidar_node.log"
  echo ""
  echo "=== test 节点日志 ==="
  tail -20 "${OUT}/test_node.log"
} >> "${OUT}/summary.txt" 2>&1

cleanup
echo "=== 完成，结果在 ${OUT}/summary.txt ==="
