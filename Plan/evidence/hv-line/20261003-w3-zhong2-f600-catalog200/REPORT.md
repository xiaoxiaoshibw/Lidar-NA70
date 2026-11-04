# W3 离线运行报告

PCAP：9.14_caochangzhong2.pcap（SHA-256 2caa68d6491cb2ba…，1298 帧）
end_frame=600，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(170.0, 320.0, -60.0, 60.0, 5.0, 30.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[-0.05704865127574542, -0.9983261044675269, 0.009510022413813334]
- point_on_line=[183.04879760742188, 45.836246490478516, 10.49543285369873]
- segment_endpoints=[[183.06232968632716, 46.073051871793304, 10.493177053240405], [178.94796350322542, -25.926532203828486, 11.1790427791593]]
- span_m=72.1203059335245
- coverage_fraction=0.4657534246575342
- largest_internal_gap_m=21.37006287658115
- nearest_distance_m=180.72000122070312
- rms/p95=0.34422343078800693/0.5598662350928878
- unique_packets=63, unique_frames=10

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
