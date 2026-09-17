# NA70 playground SE(2) candidate summary

Rejected candidates are diagnostics only and are never fused.

| Capture | Pairs | Method | Accepted | States | Candidate yaw p50 | Candidate translation p50 |
|---|---:|---|---:|---|---:|---:|
| 9.14_caochangyou.pcap | 230 | point_to_plane | 3 | `{"insufficient_points": 50, "quality_failed": 176, "accepted": 3, "degenerate_geometry": 1}` | -0.004444536274915126 | 4.715411142388631e-06 |
| 9.14_caochangyou.pcap | 230 | gicp | 3 | `{"insufficient_points": 94, "quality_failed": 132, "accepted": 3, "degenerate_geometry": 1}` | -0.0018563857392259356 | 4.329935999284635e-06 |
| 9.14_caochangyou.pcap | 230 | ndt | 3 | `{"insufficient_points": 99, "quality_failed": 127, "accepted": 3, "degenerate_geometry": 1}` | -0.0007826844082580911 | 6.246438761268018e-06 |
| 9.14_caochangzuo.pcap | 243 | point_to_plane | 0 | `{"quality_failed": 212, "insufficient_points": 23, "degenerate_geometry": 8}` | 0.0011300444570806428 | 3.0340296519348854e-05 |
| 9.14_caochangzuo.pcap | 243 | gicp | 0 | `{"insufficient_points": 123, "degenerate_geometry": 8, "quality_failed": 112}` | 0.002204455470686077 | 1.7589094441773294e-05 |
| 9.14_caochangzuo.pcap | 243 | ndt | 0 | `{"quality_failed": 147, "insufficient_points": 88, "degenerate_geometry": 8}` | -0.0019654672735673327 | 2.483440226840313e-05 |
