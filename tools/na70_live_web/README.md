# NA70 Jetson 实时点云

此工具独占 UDP 2368，按连续 100 个 1410 字节数据包生成一帧，使用 `na70_core.geometry` 和厂家状态字偏移转换坐标。零距离回波不显示；帧边界只是接收端分组，不代表严格的设备扫描边界。未进行运动补偿、时间同步或目标识别，丢包数量未知。

Jetson 需要 Python 3、NumPy。将 `server.py`、`index.html` 与项目 `packages/na70_core/na70_core` 包部署在同一目录，并将 `web/frontend/node_modules/three/build/` 的 `three.module.js`、`three.core.js` 和 `web/frontend/node_modules/three/examples/jsm/controls/OrbitControls.js` 放在该目录。启动：

```sh
cd ~/na70-live-web
python3 server.py
```

服务只监听 Jetson 的 `127.0.0.1:8795`。在本机运行 `ssh -N -L 127.0.0.1:8795:127.0.0.1:8795 jetson@192.168.31.168`，保持该终端打开，再访问 `http://127.0.0.1:8795/`。在 Jetson 本机浏览器也可直接访问该地址。页面可选择叠加最近 1、5、10、20 或 30 帧，默认 10 帧；接收中断超过 1 秒后重新收包时会清空旧帧。叠加仅适合观察静止场景；未进行运动补偿，雷达或物体运动时会产生重影。鼠标可旋转、平移和缩放。服务没有设置自动启动。

2026-11-11 在 Jetson 上录制的真实数据位于 `~/na70-live-recordings/na70-20261111-153914.pcap`。tcpdump 报告写入 29,913 包、过滤器收到 29,964 包、内核丢包 0；停止时两项计数相差 51，不能据此宣称全链路零丢包。文件 SHA-256：`72957ca999bedfcbe28ef2b4b39af1f2148d5590c0664d38e6cea67332ee0b05`。逐包检查该文件 5,982,600 条射线，仅 9 条距离码非零；这段文件不包含后续出现的可见点云。后续现场实时数据与浏览器叠加检查见[现场记录](../../Plan/evidence/slam/20261111-jetson-live-view/REPORT.md)。
