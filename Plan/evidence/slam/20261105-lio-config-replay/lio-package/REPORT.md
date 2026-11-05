# NA70 LIO 扫描包

- 状态：**EXPORTED_BLOCKED**，`lio_ready=false`
- 帧：100，点：2000000
- 时间源：`host_receive_only`；时间质量：`PACKET_APPROXIMATION`（point_time_mode=`packet`）
- 阻塞项：calibration_missing, motion_envelope_unverified, time_level_C

逐点时间由包时间复制，不是实测逐点时间；本包不执行去畸变、坐标变换或 SLAM。
