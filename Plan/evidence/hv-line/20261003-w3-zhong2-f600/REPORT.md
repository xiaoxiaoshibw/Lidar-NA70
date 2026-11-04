# W3 离线运行报告

PCAP：9.14_caochangzhong2.pcap（SHA-256 2caa68d6491cb2ba…，1298 帧）
end_frame=600，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(100.0, 250.0, -20.0, 20.0, 0.0, 60.0)（sensor frame，单位米）
状态：**MEASURED_CURRENT**（geometry=LINE_STRUCTURE）
首个阻断阶段：none

## 候选详
- direction=[0.08557397850472005, -0.9958497260546272, -0.030990600507215024]
- point_on_line=[113.31668853759766, 6.134856224060059, 2.912172794342041]
- segment_endpoints=[[112.59329407846052, 14.553211739941782, 3.1741499640843056], [115.03292320736632, -13.837474799912481, 2.2906387257291256]]
- span_m=28.509006727684632
- coverage_fraction=0.8275862068965517
- largest_internal_gap_m=3.3353710426635974
- nearest_distance_m=112.97600555419922
- rms/p95=0.43191211679117053/0.5838607937301432
- unique_packets=116, unique_frames=10

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
