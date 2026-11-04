#!/usr/bin/env bash
# Run in Ubuntu 22.04/Humble; sources/builds stay on the Linux filesystem.
set -euo pipefail
workspace="${1:?usage: bash build_fast_lio.sh /absolute/linux/workspace}"
case "$workspace" in /*) ;; *) echo 'workspace must be absolute' >&2; exit 2;; esac
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$workspace/src" "$workspace/logs"
exec > >(tee -a "$workspace/logs/build.log") 2>&1
date -u
vcs import --skip-existing "$workspace/src" < "$script_dir/upstreams.repos"
# Verify exact revisions and refuse to build a dirty or mismatched checkout.
python3 - "$script_dir/upstreams.repos" "$workspace/src" <<'PY'
import pathlib, subprocess, sys, yaml
lock = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())['repositories']
for name, entry in lock.items():
    root = pathlib.Path(sys.argv[2]) / name
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    dirty = git('status', '--porcelain')
    if name == 'livox_ros_driver2' and dirty == '?? package.xml':
        if (root / 'package.xml').read_bytes() == (root / 'package_ROS2.xml').read_bytes():
            dirty = ''
    if git('rev-parse', 'HEAD') != entry['version'] or dirty:
        raise SystemExit(f'{root}: revision mismatch or dirty checkout; inspect manually')
    if git('remote', 'get-url', 'origin') != entry['url']:
        raise SystemExit(f'{root}: remote mismatch')
PY
cp "$workspace/src/livox_ros_driver2/package_ROS2.xml" "$workspace/src/livox_ros_driver2/package.xml"
git -C "$workspace/src/fast_lio" submodule update --init --recursive
cmake -S "$workspace/src/Livox-SDK2" -B "$workspace/sdk-build" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$workspace/sdk-install"
cmake --build "$workspace/sdk-build" -j2
cmake --install "$workspace/sdk-build"
# Upstream SDK lookup uses standard include/library search paths.
export CMAKE_PREFIX_PATH="$workspace/sdk-install:${CMAKE_PREFIX_PATH:-}"
export CMAKE_INCLUDE_PATH="$workspace/sdk-install/include:${CMAKE_INCLUDE_PATH:-}"
export CMAKE_LIBRARY_PATH="$workspace/sdk-install/lib:${CMAKE_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$workspace/sdk-install/lib:${LD_LIBRARY_PATH:-}"
export CMAKE_BUILD_PARALLEL_LEVEL=2
export MAKEFLAGS=-j2
set +u
source /opt/ros/humble/setup.bash
set -u
cd "$workspace"
colcon build --symlink-install --packages-select livox_ros_driver2 fast_lio \
  --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release \
  -DROS_EDITION=ROS2 -DDISTRO_ROS=humble
