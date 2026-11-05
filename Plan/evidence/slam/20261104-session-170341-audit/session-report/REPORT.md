# NA70 会话诊断报告

- 结论：**REJECTED_NOT_STATIC**
- 时间等级：`C_HOST_RECEIVE_ONLY`，设备时间字段：False
- 饱和可疑：False，静置基线可用：False

- 帧数 20052，批次 164，每批中位 122.0，批次最大间隔 0.625 s
- 静置窗口：[(0, 'REJECTED'), (1, 'REJECTED'), (2, 'REJECTED'), (3, 'REJECTED'), (4, 'REJECTED'), (5, 'REJECTED'), (6, 'REJECTED'), (7, 'REJECTED'), (8, 'REJECTED'), (9, 'REJECTED')]
- 饱和事件：0
- 陀螺单位一致性：INSUFFICIENT_DATA

## 主机端点重叠
- PCAP 83351 包 / IMU 164 块，端点重叠 99.639859 s
- 最大雷达包间隔 0.027931 s，最大 IMU 块间隔 0.615829 s
- 主机端点重叠不证明设备采样同步或全程连续有效

## 用途判定
- 静态基线：False
- 动态标定：False；原因：无设备采样时间；主机批量到达限制对齐精度

本报告只汇总诊断；主机时间不是设备时间，窗口通过不等于物理静止。
