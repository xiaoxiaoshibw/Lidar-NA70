# W3 离线运行报告

PCAP：9.14_caochangzhong2.pcap（SHA-256 2caa68d6491cb2ba…，1298 帧）
end_frame=600，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(190.0, 240.0, -15.0, 15.0, 0.0, 50.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[-0.015571245565513378, -0.4352492888006077, -0.9001753123198302]
- point_on_line=[226.80506896972656, -3.8144524097442627, 19.57279396057129]
- segment_endpoints=[[226.81228641865647, -3.6127094277532055, 19.99003542769136], [226.74621460272587, -5.459556603373841, 16.1704169334199]]
- span_m=4.243194011206519
- coverage_fraction=1.0
- largest_internal_gap_m=1.4745301495459695
- nearest_distance_m=227.0399932861328
- rms/p95=0.36902873849779344/0.5856194656183984
- unique_packets=8, unique_frames=7

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
