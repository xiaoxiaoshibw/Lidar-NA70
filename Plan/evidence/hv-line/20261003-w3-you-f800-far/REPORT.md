# W3 离线运行报告

PCAP：9.14_caochangyou.pcap（SHA-256 ceca9fc08758f3c3…，1211 帧）
end_frame=800，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(180.0, 300.0, -30.0, 30.0, 0.0, 80.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[-0.6932580764588766, -0.7168015524899903, -0.07475810171799398]
- point_on_line=[240.56033325195312, -0.4192594587802887, 21.86159896850586]
- segment_endpoints=[[240.5864232946485, -0.3922833819863264, 21.864412411481688], [221.98477309104396, -19.625657211437034, 19.858486905143785]]
- span_m=26.832215642723913
- coverage_fraction=0.8888888888888888
- largest_internal_gap_m=2.012656551058715
- nearest_distance_m=223.6639862060547
- rms/p95=0.38814622179194497/0.5723052650154214
- unique_packets=31, unique_frames=10

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
