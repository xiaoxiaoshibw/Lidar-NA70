"""NA70 goal-demo core: sliding-window accumulation and football-goal detection.

Scope: LiDAR-only / no IMU.  The package is deliberately free of FastAPI and
ROS2 imports so every numerical decision can be exercised offline against a
PCAP replay.  HTTP surface lives in ``backend.demo_api``; a future ROS2 node
would only wrap these modules (same split as ``na70_motion_detector``).
"""
