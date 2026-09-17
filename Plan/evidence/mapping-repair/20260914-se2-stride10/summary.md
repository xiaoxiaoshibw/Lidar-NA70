# NA70 playground SE(2) candidate summary

Rejected candidates are diagnostics only and are never fused.

| Capture | Pairs | Method | Accepted | States | Candidate yaw p50 | Candidate translation p50 |
|---|---:|---|---:|---|---:|---:|
| 9.14_caochangyou.pcap | 221 | point_to_plane | 3 | `{"insufficient_points": 57, "quality_failed": 160, "accepted": 3, "degenerate_geometry": 1}` | -0.0036333292455221077 | 5.111859188505276e-06 |
| 9.14_caochangyou.pcap | 221 | gicp | 3 | `{"insufficient_points": 109, "quality_failed": 108, "accepted": 3, "degenerate_geometry": 1}` | -0.00015554393509551178 | 4.0831500490503465e-06 |
| 9.14_caochangyou.pcap | 221 | ndt | 3 | `{"insufficient_points": 110, "quality_failed": 107, "accepted": 3, "degenerate_geometry": 1}` | 0.001084167643405909 | 4.7305680727384e-06 |
| 9.14_caochangzuo.pcap | 234 | point_to_plane | 0 | `{"quality_failed": 191, "insufficient_points": 36, "degenerate_geometry": 7}` | -0.0021284670139225387 | 3.5513785481026546e-05 |
| 9.14_caochangzuo.pcap | 234 | gicp | 0 | `{"insufficient_points": 142, "quality_failed": 85, "degenerate_geometry": 7}` | 0.002551725497268703 | 2.2075886785169962e-05 |
| 9.14_caochangzuo.pcap | 234 | ndt | 0 | `{"insufficient_points": 102, "quality_failed": 125, "degenerate_geometry": 7}` | 0.0025488706297364895 | 3.4736321301362585e-05 |
