# W3 离线运行报告

PCAP：9.14_caochangzuo.pcap（SHA-256 b2365c97bd22bba7…，1221 帧）
end_frame=1200，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(150.0, 300.0, -30.0, 30.0, 0.0, 80.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[-0.6689784760365333, 0.7416202620133976, -0.04967077179808134]
- point_on_line=[188.7821807861328, -16.858739852905273, 13.533750534057617]
- segment_endpoints=[[190.3640276320783, -18.61235333133218, 13.651200581434301], [150.28156892946086, 25.822504575146986, 10.675130751625598]]
- span_m=59.915916787177046
- coverage_fraction=0.4166666666666667
- largest_internal_gap_m=35.852962477641285
- nearest_distance_m=152.7679901123047
- rms/p95=0.34238685832800186/0.5671666185149686
- unique_packets=36, unique_frames=10

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
