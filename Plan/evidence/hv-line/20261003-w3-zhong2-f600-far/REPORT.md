# W3 离线运行报告

PCAP：9.14_caochangzhong2.pcap（SHA-256 2caa68d6491cb2ba…，1298 帧）
end_frame=600，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(180.0, 300.0, -30.0, 30.0, 0.0, 80.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[-0.04953130180771385, -0.9987683582032408, 0.002901171011146775]
- point_on_line=[181.2975311279297, 16.6109561920166, 10.647430419921875]
- segment_endpoints=[[181.67046834960905, 24.131006806789156, 10.625586563269085], [180.35899343069437, -2.314081526931396, 10.702402897137556]]
- span_m=26.477699374952767
- coverage_fraction=0.9259259259259259
- largest_internal_gap_m=2.7860969537598343
- nearest_distance_m=180.72000122070312
- rms/p95=0.34671091946430627/0.5761407022420983
- unique_packets=55, unique_frames=10

## 阶段执行记录（顺序与 counts 见 stages.jsonl/candidate.json）
- decode: completed
- time_quality: completed
- window_quality: completed
- valid_return: completed
- roi_or_proposal: completed
- fit: completed
- finite_support: completed
- background_check: skipped
- freshness: completed
- association: skipped
- output: completed

本次运行只反映算法在给定 ROI 与时长下的行为，**不能解释为已识别高压线**。真实身份仍需 independent reviewed truth。
