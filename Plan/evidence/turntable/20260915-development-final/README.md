# NA70 fixed-point turntable development evidence

**DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE**

This output uses estimated angles because the existing PCAPs have no synchronized encoder log. It is not formal turntable accuracy acceptance and is not complete SLAM.

完整运行命令：`tools\build_turntable_map.py --pcap 2\9.14_caochangzhong2.pcap --pcap 2\9.14_caochangzuo.pcap --pcap 2\9.14_caochangyou.pcap --angle-log web\backend\angle_log.20260914-development.csv --calibration web\backend\turntable_calibration.20260914-assumed.json --output Plan\evidence\turntable\20260915-development-final`

输入文件（绝对路径与 SHA-256）：
- `D:\Code\radar\2\9.14_caochangzhong2.pcap` SHA-256 `2caa68d6491cb2ba6fd9bae86e04104038fbbf4c7a4e349fa6dcc211adc743ce`
- `D:\Code\radar\2\9.14_caochangzuo.pcap` SHA-256 `b2365c97bd22bba7d24ad14ceee365b59b4dba4f6f8e9721e694f3b8c6ef63c6`
- `D:\Code\radar\2\9.14_caochangyou.pcap` SHA-256 `ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`

最终采用的静止区间：center_reference=9.14_caochangzhong2.pcap 0.000-129.900s; right_initial_estimated=9.14_caochangyou.pcap 0.000-20.000s; right_final_estimated=9.14_caochangyou.pcap 82.000-120.000s; left_initial_estimated=9.14_caochangzuo.pcap 0.000-20.000s; left_final_estimated=9.14_caochangzuo.pcap 113.000-122.000s

连续转动和未采用时间段见 `validation.json` 的 `excluded_rotation_time`；质量失败子图保留在 `submap_quality.jsonl`，不进入最终地图。 `D_constrained_refinement` 若未通过留出结构门禁则拒绝微调，最终使用外部角度变换。

Future formal capture must provide measured yaw per stationary segment, encoder resolution/repeatability, zero reference, positive direction, measured lidar-to-axis translation and mounting RPY, settle/valid timestamps, and an independent repeat capture.
