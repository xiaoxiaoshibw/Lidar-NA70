# Repository Guidelines

## Project Structure & Module Organization

- `packages/na70_core/`: pure Python source-preserving observation contract, shared NA70 angular transform, bounded causal packet windows (`windows.py`), the wire manual-ROI pipeline (`wire/`), the NA70+IMU calibration math (`calibration/`), the pose-driven scene assembler (`scene.py`), the packet-time LIO scan packager (`lio_scans.py`) and map quality checks (`map_quality.py`); `web/backend/decoder.py` remains the sole classic PCAP parser. Wire default config lives in `na70_core/wire_default.yaml`.
- `ROS2/src/double_wedge_driver/`: C++17 NA70 driver, messages, launch files, and RViz configuration.
- `ROS2/src/na70_motion_detector/`: Python algorithm core, ROS2 wrappers, YAML configuration, and `test/` unit tests.
- `web/backend/`: Python PCAP decoder and FastAPI backend; `web/frontend/`: React + TypeScript + Vite + Three.js multi-entry pages (no client router, HTTP polling only, no WebSocket). Tests reside in `web/tests/` and `web/frontend/tests/`.
- `web/backend/wire_api.py`: persistent W1 raw-return diagnostic jobs under `/api/wire`; `web/frontend/src/wire/`: the `/wire/` workbench. W3 is available via the offline CLI `tools/run_wire_w3_offline.py`; W4/W5/third-party proposals remain placeholders, not detection results. Real 200 m HV-line attribution stays `NOT_EVALUATED` until independent reviewed ground truth exists.
- `web/backend/goal_detection/`: goal (球门) detection/tracking pipeline package (`bev.py`, `detector.py`, `tracker.py`, `accumulation.py`, `observability.py`, …), configured by `goal_detection.yaml`; `demo_api.py` exposes the Inspector/debug API. `turntable.py` implements the fixed-point turntable stitching route; `mapping.py` is the historical odometry + voxel-map chain (kept, no longer the mainline).
- `docs/`: goal-tracking current architecture, implementation plan, and debug contract (`GOAL_TRACKING_*.md`, `GOAL_DEBUG_CONTRACT.md`).
- `tools/`: exploratory analysis and replay utilities. `radar_capture_data/` holds recordings; PCAP files are excluded from Git.
- `Plan/`: plans and acceptance gates. Read `Plan/README.md` first (it records route decisions). The current execution entry is `足球门识别主线开发计划.md`; `GOAL_P3_真实球门检测修复执行计划.md` and `球门跟踪下一阶段完整开发计划.md` remain detailed/history references. `固定点转台操场建图计划.md` is the separate turntable route. `MASTER_开发与验收总计划.md` is historical failure evidence. `05_开发执行规范.md` applies to all work; preserve unrelated working-tree edits.

## Current Mainline & Environment

- 2026-10-30 NA70+IMU calibration toolchain (entry: `Plan/NA70_YESENSE_标定AI执行计划.md`): `na70_core/calibration/`, `scene.py`, `lio_scans.py`, `map_quality.py` plus CLIs are ready with SYNTHETIC evidence in `Plan/evidence/slam/20261030-*` (usage: `docs/NA70_CALIBRATION_WORKFLOW.md`). One-click capture entries `开始外参采集.bat` (single 80 s wall rotation) and `开始全量标定采集.bat` (STATIC 120 s + CAL-ROT 80 s + six faces 25 s each, `na70-calibration-batch-v1` manifest) feed local batch processing via `tools/report_na70_calibration_batch.py` and `tools/solve_na70_six_face_sessions.py`; capture scripts fail fast on the capture service's `failed` terminal state. SYNTHETIC tests cover the batch pipeline and the plane-normal `calibrate()` end to end. Near-range audit (four PCAP sources, identical): **the closest valid return is 7.488 m (distance code 468); codes 1–467 never appear** — a device/data floor, not a decoder or tool filter — so the extrinsic ROT wall must be **at least 8 m away (9–15 m preferred)**, not 2–5 m (`Plan/evidence/slam/20261030-near-range-floor/REPORT.md`). Still outstanding: B1–B5 field data, real time sync, real extrinsics, real playground reconstruction, Jetson acceptance; no `calibration_frozen.yaml`.
- 2026-09-21 latest route decision: the near-term mainline is `Plan/足球门识别主线开发计划.md`. The user wants the real football goal recognized first to validate the full perception chain. Execute in order: G0 reviewed real truth, G1 ground-relative height, G2 net-aware geometry inside a manual ROI, G3 full-FOV proposals, G4 tracking, G5 frozen acceptance. Start with G0-G1 only. The thin-wire W1 tool and evidence remain preserved but that route is paused. Read `Plan/README.md` before older handoffs.
- 2026-09-26 WebUI refactor adds a wire W1 raw-diagnostic workbench and persistent run API. This does not upgrade wire-return attribution (`NOT_EVALUATED`) or change the route decision; consult `Plan/README.md` for current priorities.

- Active development is Windows-local offline PCAP analysis + WebUI acceptance. WSL2/ROS2 is integration validation only; Jetson Orin Nano/Humble is the final performance-acceptance target. Real-time device ingestion is a later exit, not current scope.
- Moving-object detection has exited the main acceptance scope (backend code stays; its entry was removed). Pure no-IMU point-cloud odometry is a historical route kept as failure evidence — do not resume it as the mainline without a new plan decision in `Plan/`.
- The shell here is Git Bash on Windows, but project scripts (`start.ps1`, `启动WebUI.bat`) follow PowerShell conventions — run them from PowerShell.

## Build, Test, and Development Commands

Requires Python 3.12 and Node.js 22.12+.

From `web/` in PowerShell:

- `.\start.ps1`: create the virtual environment, install locked dependencies, build, and launch the application.
- `.\.venv\Scripts\python.exe -m pytest tests -q`: run decoder and API tests.
- `.\.venv\Scripts\python.exe tests\benchmark.py`: benchmark playback processing.

From `web/frontend/`:

- `npm run dev`: start Vite with the API proxy targeting port 8765.
- `npm run build`: type-check TypeScript and build production assets.
- `npx playwright install chromium`, then `npm run test:e2e`: run browser tests; backend must run on port 8765.

From `ROS2/` in a Humble shell, run `source /opt/ros/humble/setup.bash`, `colcon build --symlink-install`, then `source install/setup.bash`. Launch detection with `ros2 launch na70_motion_detector motion_detector.launch.py`.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions/modules, and PascalCase classes. TypeScript uses two spaces, camelCase functions, and PascalCase components. Match surrounding C++ style. No dedicated formatter or linter configuration is present.

Keep algorithms independent of ROS2 wrappers and detection thresholds in YAML. Preserve timestamps, coordinate frames, intensity, and point correspondence. Use the ROS2 driver (`ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` and `test_lidar_driver.cpp`) as the sole decoding reference: classic PCAP payload is 1410 bytes = 200 points + 10-byte tail; 100 valid packets form one frame (~10 Hz, 20000 rays/frame); distance code ×16 mm, pitch code ×0.0258°, azimuth code ×0.0432°.

## Testing Guidelines

Use pytest for Web API/decoder tests, unittest for motion-core tests, and Playwright for UI tests. Name Python tests `test_*.py` and browser tests `*.spec.ts`. From the motion-detector package directory, run `python -m unittest discover -s test` with NumPy installed.

No coverage threshold is configured. Cover changed behavior and malformed inputs. Browser tests require captures, including `1.pcap` and `2.pcap`. Label synthetic evidence `SYNTHETIC`; final performance acceptance requires Jetson Orin Nano/Humble. WSL2 Ubuntu 22.04/Humble validates integration only.

## Commit & Pull Request Guidelines

Use concise Chinese action summaries, matching existing commits. PRs should describe behavior changes, related tasks, validation commands/environments, and UI screenshots when relevant. Update affected planning and validation documents. Keep credentials, PCAPs, caches, and build outputs out of commits; retain dependency lockfiles. Keep the unauthenticated Web service bound to localhost.
