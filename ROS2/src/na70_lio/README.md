# na70_lio（原型，未验收）

这是 NA70 → FAST-LIO 的最小接入原型，状态 **PROTOTYPE_CONSUMED_5_FRAMES / NO_VALID_TRAJECTORY / NOT_EVALUATED**。它不是生产包，也不代表 NA70 LIO 可用。

## 文件

- `patches/fast_lio_na70_handler.patch`：给固定 FAST-LIO `cc3c9e8` 增加 `NA70` 点类型与预处理 handler（x/y/z/intensity/time 秒，不使用/伪造 ring）。由 `tools/lio/apply_fast_lio_na70_patch.py` 应用。
- `tools/lio/na70_fast_lio.yaml`：FAST-LIO 配置（`lidar_type: 5`、`timestamp_unit: 0`、单位外参，仅诊断）。
- `tools/lio/replay_na70_package.py`：读取 `scans.npz`/`imu.npz`，发布 `/na70/points` 与 `/na70/imu`。
- `tools/lio/run_na70_fastlio_smoke.sh`：单 shell 启动 FAST-LIO 并回放 5 帧包。
- `tools/lio/record_na70_fastlio_output.py`：记录 `/Odometry` 与最新 `/cloud_registered`（尚不稳定）。

## 复现（WSL2/Humble）

```bash
python3 tools/lio/apply_fast_lio_na70_patch.py --workspace /home/radar/na70_lio_ws
source /opt/ros/humble/setup.bash
cd /home/radar/na70_lio_ws && colcon build --symlink-install --packages-select fast_lio
bash /mnt/d/Code/radar/tools/lio/run_na70_fastlio_smoke.sh \
  /mnt/d/Code/radar/Plan/evidence/slam/20261030-lio-package-smoke
```

## 已知问题

- 本 WSL 的 FastDDS 跨进程发现失败；必须 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`，脚本已设置。
- 5 帧短包投递跑次间不稳定；一次成功运行只有 1 行 `Log/pos_log.txt`，无 PCD（上游 `pcd_save` 代码块被注释）。
- 时间等级 C、外参单位矩阵；不满足 S3/S5 或精度验收。
- 见 `Plan/evidence/slam/20261030-na70-fastlio-prototype/REPORT.md`。
