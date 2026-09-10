# inno_double_wedge_driver ROS2 迁移计划（入口）

本文件不再维护正文。迁移计划正文已统一到迁移工作区内：

- **迁移计划（唯一维护版本）**：[`ROS2/ROS2迁移计划.md`](ROS2/ROS2迁移计划.md)
- **当前落地进度与验证状态**：[`ROS2/迁移进度.md`](ROS2/迁移进度.md)
- **编译与运行说明**：[`ROS2/ReadMe.md`](ROS2/ReadMe.md)

## 为什么合并

此前根目录与 `ROS2/` 下各有一份《ROS2迁移计划.md》。两份正文经逐行比对后确认，
根目录版本相对工作区版本只少了一段适用范围说明，且 3.1 节仍写着 ROS1 时期的
`start_lidar.launch`、`start_radar.launch`、`test_lidar.launch` 文件名——这些文件
在迁移后已改为 `.launch.py`。根目录版本没有工作区版本所不具备的有效内容，
因此不再保留第二份正文，避免两套文档继续分叉。
