# W3 离线运行报告

PCAP：9.14_caochangzhong2.pcap（SHA-256 2caa68d6491cb2ba…，1298 帧）
end_frame=600，请求时长=1.0s；实际 window=1000 packets，quality=ok。
ROI=(5.0, 30.0, -15.0, 15.0, 0.0, 30.0)（sensor frame，单位米）
状态：**UNKNOWN**（geometry=None）
首个阻断阶段：fit，拒绝原因：INSUFFICIENT_SUPPORT

## 阶段执行记录（顺序与 counts 见 stages.jsonl/candidate.json）
- decode: completed
- time_quality: completed
- window_quality: completed
- valid_return: completed
- roi_or_proposal: completed
- fit: rejected

本次运行只反映算法在给定 ROI 与时长下的行为，**不能解释为已识别高压线**。真实身份仍需 independent reviewed truth。
