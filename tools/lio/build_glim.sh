#!/usr/bin/env bash
set -euo pipefail
workspace="${1:?usage: bash build_glim.sh /absolute/linux/workspace}"
case "$workspace" in /*) ;; *) echo 'workspace must be absolute' >&2; exit 2;; esac
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$workspace/src" "$workspace/logs"
exec > >(tee -a "$workspace/logs/glim-build.log") 2>&1
date -u
vcs import --skip-existing "$workspace/src" < "$script_dir/upstreams.repos"
python3 - "$script_dir/upstreams.repos" "$workspace/src" <<'PY'
import pathlib, subprocess, sys, yaml
lock = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())['repositories']
for name in ('glim', 'glim_ros2'):
    root = pathlib.Path(sys.argv[2]) / name
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    if (git('rev-parse', 'HEAD') != lock[name]['version'] or git('status', '--porcelain')
            or git('remote', 'get-url', 'origin') != lock[name]['url']):
        raise SystemExit(f'{root}: revision/remote mismatch or dirty checkout')
PY
set +u
source /opt/ros/humble/setup.bash
set -u
export CMAKE_BUILD_PARALLEL_LEVEL=2
export MAKEFLAGS=-j2
cd "$workspace"
colcon build --symlink-install --packages-select glim glim_ros \
  --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_WITH_CUDA=OFF -DBUILD_WITH_VIEWER=OFF -DBUILD_WITH_MARCH_NATIVE=OFF
