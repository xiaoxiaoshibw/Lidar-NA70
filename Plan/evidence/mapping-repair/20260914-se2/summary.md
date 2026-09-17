# NA70 playground SE(2) candidate summary

Rejected candidates are diagnostics only and are never fused.

| Capture | Pairs | Method | Accepted | States | Candidate yaw p50 | Candidate translation p50 |
|---|---:|---|---:|---|---:|---:|
| 9.14_caochangyidong.pcap | 12 | point_to_plane | 0 | `{"insufficient_points": 3, "quality_failed": 9}` | 0.2964624910397468 | 0.00012358400855785526 |
| 9.14_caochangyidong.pcap | 12 | gicp | 0 | `{"insufficient_points": 7, "quality_failed": 5}` | 0.1358164821048207 | 0.00012771631448575457 |
| 9.14_caochangyidong.pcap | 12 | ndt | 0 | `{"insufficient_points": 7, "quality_failed": 5}` | 0.8439614435978612 | 0.00028402687391672023 |
| 9.14_caochangyou.pcap | 12 | point_to_plane | 1 | `{"insufficient_points": 5, "quality_failed": 5, "accepted": 1, "degenerate_geometry": 1}` | -0.0034455820797041513 | 6.056025457908016e-06 |
| 9.14_caochangyou.pcap | 12 | gicp | 1 | `{"insufficient_points": 6, "accepted": 1, "quality_failed": 4, "degenerate_geometry": 1}` | -0.0013884032771178001 | 1.6443522715173557e-06 |
| 9.14_caochangyou.pcap | 12 | ndt | 1 | `{"insufficient_points": 6, "accepted": 1, "quality_failed": 4, "degenerate_geometry": 1}` | -0.01699490984693389 | 9.403845997126409e-06 |
| 9.14_caochangzhong2.pcap | 12 | point_to_plane | 0 | `{"degenerate_geometry": 2, "insufficient_points": 3, "quality_failed": 7}` | -0.2810009838168383 | 0.00012609516222923667 |
| 9.14_caochangzhong2.pcap | 12 | gicp | 0 | `{"degenerate_geometry": 2, "insufficient_points": 10}` | None | None |
| 9.14_caochangzhong2.pcap | 12 | ndt | 0 | `{"degenerate_geometry": 2, "insufficient_points": 8, "quality_failed": 2}` | -0.4684830250498222 | 0.00016538420861623459 |
| 9.14_caochangzuo.pcap | 12 | point_to_plane | 0 | `{"quality_failed": 9, "insufficient_points": 3}` | 0.009354652981140245 | 1.9109630342387535e-05 |
| 9.14_caochangzuo.pcap | 12 | gicp | 0 | `{"insufficient_points": 7, "quality_failed": 5}` | 0.01850723100575215 | 1.8108534229985984e-05 |
| 9.14_caochangzuo.pcap | 12 | ndt | 0 | `{"quality_failed": 8, "insufficient_points": 4}` | 0.032154648272902306 | 1.3210705627433468e-05 |
