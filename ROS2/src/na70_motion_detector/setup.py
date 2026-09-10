from glob import glob
from setuptools import find_packages, setup


package_name = "na70_motion_detector"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=("test",)),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", glob("config/*.yaml")),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "numpy"],
    tests_require=["pytest"],
    zip_safe=True,
    maintainer="ly",
    maintainer_email="ly@todo.todo",
    description="Minimal fixed-NA70 motion-presence detector based on a voxel background.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "motion_detector_node = na70_motion_detector.motion_detector_node:main",
            "motion_detector_offline = na70_motion_detector.offline_demo:main",
            "synthetic_cloud_publisher = na70_motion_detector.synthetic_cloud_publisher:main",
        ],
    },
)
