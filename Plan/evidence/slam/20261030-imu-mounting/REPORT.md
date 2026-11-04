# NA70＋YESENSE 本机接入与安装布局记录

日期：2026-10-30（Asia/Shanghai）。环境：Windows 本机、Python 3.12、COM12/460800/8N1。状态：OBSERVED_ONLY / NOT_CALIBRATED。

用户确认：外壳宽×深×高 90×130×110 mm；IMU 位于尾部顶面中线，并向 -X 后移；+Y LEFT 侧的大接口从 +Y 看向机身时位于画面右下角。

模型 I*=[-43,0,60] mm 是板卡可视中心，不是测量原点或实测外参。X/Z 数值仍为粗估；8 mm 后移仅为调图量。导出保持 T_IL=null、time_offset_s=null、usable_for_sensor_fusion=false。

`static-checks/` 保留三轮原始 JSONL 和报告：串口失败/0样本；2007帧静置一致性符合；2008帧运动干扰拒绝。静置结果仅代表短时一致性及有条件的零偏候选，未应用。最后一次拒绝没有覆盖此前成功证据。设备采样时间戳未输出，完整同步、雷达内参/外参、NA70移动LIO和Jetson均未验收。

`monitor-source/` 为本地独立页面源码快照；原参考照片未复制，页面照片路由对应用户 Downloads 内原文件，运行时需自行补齐 photos/top.png、front.png、side.png。原运行服务与完整照片仍位于 manifest 中记录的 monitor_source_path，本快照不是主 WebUI 生产集成。

`manifest.json` 保存源码、模型、原始数据和报告 SHA-256；已逐一核验静置报告对原始 JSONL 的哈希。源码解析曾通过真实分片帧、校验损坏恢复、载荷越界和 SYNTHETIC 静置/运动/不足样本/错误输入检查；本次记录阶段未重新运行算法测试。模型调整后已通过 JS 语法和浏览器位置检查。

测试顺序、所需基准、采集动作、外参方向和验收限制见 [标定测试指南](../../../../docs/NA70_YESENSE_CALIBRATION_GUIDE.md)。
