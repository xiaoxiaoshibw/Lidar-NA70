"""Apply the NA70 PointCloud2 handler to a fixed FAST-LIO ROS2 checkout.

The upstream FAST-LIO tree is not vendored here.  This script edits a checkout
at ``<workspace>/src/fast_lio`` idempotently and copies the NA70 config.  It is
a prototype patch for plumbing validation; it does not claim NA70 LIO accuracy.

Usage (inside WSL):
    python3 tools/lio/apply_fast_lio_na70_patch.py --workspace /home/radar/na70_lio_ws
"""
from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "na70_fast_lio.yaml"


def _replace_once(text, old, new, name):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{name}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_header(path):
    text = path.read_text(encoding="utf-8")
    if "NA70" in text:
        return False
    text = _replace_once(text, "  OUST64,\n  MID360\n};  //{1, 2, 3}",
                         "  OUST64,\n  MID360,\n  NA70\n};  //{1, 2, 3, 4, 5}", "LID_TYPE")
    point_struct = '''namespace na70_ros
{
struct EIGEN_ALIGN16 Point
{
  PCL_ADD_POINT4D;
  float intensity;
  float time;  // seconds, relative to scan start
  EIGEN_MAKE_ALIGNED_OPERATOR_NEW
};
}
POINT_CLOUD_REGISTER_POINT_STRUCT(na70_ros::Point,
                                  (float, x, x)(float, y, y)(float, z, z)
                                  (float, intensity, intensity)(float, time, time))

'''
    text = _replace_once(text, "namespace ouster_ros\n{", point_struct + "namespace ouster_ros\n{",
                         "na70 point struct")
    text = _replace_once(text, "  void mid360_handler(const sensor_msgs::msg::PointCloud2::UniquePtr &msg);",
                         "  void mid360_handler(const sensor_msgs::msg::PointCloud2::UniquePtr &msg);\n"
                         "  void na70_handler(const sensor_msgs::msg::PointCloud2::UniquePtr &msg);",
                         "na70 declaration")
    path.write_text(text, encoding="utf-8")
    return True


def patch_source(path):
    text = path.read_text(encoding="utf-8")
    if "na70_handler" in text:
        return False
    text = _replace_once(text,
                         "    case MID360:\n      mid360_handler(msg);\n      break;\n",
                         "    case MID360:\n      mid360_handler(msg);\n      break;\n\n"
                         "    case NA70:\n      na70_handler(msg);\n      break;\n",
                         "switch case")
    handler = '''void Preprocess::na70_handler(const sensor_msgs::msg::PointCloud2::UniquePtr &msg)
{
  pl_surf.clear();
  pl_corn.clear();
  pl_full.clear();

  pcl::PointCloud<na70_ros::Point> pl_orig;
  pcl::fromROSMsg(*msg, pl_orig);
  int plsize = pl_orig.points.size();
  if (plsize == 0)
    return;
  pl_surf.reserve(plsize);

  for (int i = 0; i < plsize; i++)
  {
    if (i % point_filter_num != 0)
      continue;
    const auto &p = pl_orig.points[i];
    if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z))
      continue;
    double range2 = p.x * p.x + p.y * p.y + p.z * p.z;
    if (range2 < blind * blind)
      continue;
    PointType added_pt;
    added_pt.normal_x = 0;
    added_pt.normal_y = 0;
    added_pt.normal_z = 0;
    added_pt.x = p.x;
    added_pt.y = p.y;
    added_pt.z = p.z;
    added_pt.intensity = p.intensity;
    // NA70 package stores per-point time in seconds; FAST-LIO curvature is ms.
    added_pt.curvature = p.time * time_unit_scale;
    pl_surf.points.push_back(added_pt);
  }
}

'''
    text = _replace_once(text, "void Preprocess::default_handler(", handler + "void Preprocess::default_handler(",
                         "handler insert")
    path.write_text(text, encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args(argv)
    fast_lio = Path(args.workspace) / "src" / "fast_lio"
    header = fast_lio / "src/preprocess.h"
    source = fast_lio / "src/preprocess.cpp"
    for path in (header, source):
        if not path.is_file():
            raise SystemExit(f"missing {path}")
    changed_header = patch_header(header)
    changed_source = patch_source(source)
    config_target = fast_lio / "config/na70.yaml"
    config_target.write_text(Path(args.config).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"header_changed={changed_header} source_changed={changed_source} config={config_target}")


if __name__ == "__main__":
    main()