# Repository Guidelines

## Project Structure & Module Organization

- `ROS2/src/double_wedge_driver/`: C++17 NA70 driver, messages, launch files, and RViz configuration.
- `ROS2/src/na70_motion_detector/`: Python algorithm core, ROS2 wrappers, YAML configuration, and `test/` unit tests.
- `web/backend/`: Python PCAP decoder and API; `web/frontend/`: React, TypeScript, Vite, Three.js,. Tests reside in `web/tests/` and `web/frontend/tests/`.
- `tools/`: exploratory analysis and replay utilities. `radar_capture_data/` holds recordings; PCAP files are excluded from Git.
- `Plan/`: architecture, implementation stages, and acceptance criteria. Read `Plan/README.md` and `Plan/05_开发执行规范.md` before changes; preserve unrelated working-tree edits.

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

Keep algorithms independent of ROS2 wrappers and detection thresholds in YAML. Preserve timestamps, coordinate frames, intensity, and point correspondence. Use the ROS2 driver (`ROS2/src/double_wedge_driver/`) as the sole decoding reference.

## Testing Guidelines

Use pytest for Web API/decoder tests, unittest for motion-core tests, and Playwright for UI tests. Name Python tests `test_*.py` and browser tests `*.spec.ts`. From the motion-detector package directory, run `python -m unittest discover -s test` with NumPy installed.

No coverage threshold is configured. Cover changed behavior and malformed inputs. Browser tests require captures, including `1.pcap` and `2.pcap`. Label synthetic evidence `SYNTHETIC`; final performance acceptance requires Jetson Orin Nano/Humble. WSL2 Ubuntu 22.04/Humble validates integration only.

## Commit & Pull Request Guidelines

Use concise Chinese action summaries, matching existing commits. PRs should describe behavior changes, related tasks, validation commands/environments, and UI screenshots when relevant. Update affected planning and validation documents. Keep credentials, PCAPs, caches, and build outputs out of commits; retain dependency lockfiles. Keep the unauthenticated Web service bound to localhost.
