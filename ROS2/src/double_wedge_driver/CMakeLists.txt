cmake_minimum_required(VERSION 3.8)
project(double_wedge_driver)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

# 供 .vscode/c_cpp_properties.json 使用，产物位于 build/double_wedge_driver/
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(pcl_conversions REQUIRED)
find_package(PCL REQUIRED COMPONENTS common filters)
find_package(Boost REQUIRED COMPONENTS system)
find_package(Eigen3 REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/LidarScanBundle.msg"
  "msg/CustomPoint.msg"
  "msg/CustomMsg.msg"
  "msg/LidarStatus.msg"
  "msg/RadarStatus.msg"
  "msg/RadarMsg.msg"
  DEPENDENCIES
    std_msgs
    sensor_msgs
)

include_directories(
  ${PCL_INCLUDE_DIRS}
  ${Boost_INCLUDE_DIRS}
)
add_definitions(${PCL_DEFINITIONS})

function(configure_driver_target target)
  ament_target_dependencies(${target}
    rclcpp
    builtin_interfaces
    std_msgs
    sensor_msgs
    pcl_conversions
  )
  target_link_libraries(${target}
    ${PCL_LIBRARIES}
    ${Boost_LIBRARIES}
    Eigen3::Eigen
    pthread
  )
  rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} "rosidl_typesupport_cpp")
  target_link_libraries(${target} "${cpp_typesupport_target}")
endfunction()

add_executable(${PROJECT_NAME}_lidar src/inno_device_lidar_node.cpp)
configure_driver_target(${PROJECT_NAME}_lidar)

add_executable(${PROJECT_NAME}_radar src/inno_device_radar_node.cpp)
configure_driver_target(${PROJECT_NAME}_radar)

add_executable(${PROJECT_NAME}_test_lidar src/test_lidar_driver.cpp)
configure_driver_target(${PROJECT_NAME}_test_lidar)

install(TARGETS
  ${PROJECT_NAME}_lidar
  ${PROJECT_NAME}_radar
  ${PROJECT_NAME}_test_lidar
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch config msg doc
  DESTINATION share/${PROJECT_NAME}
)

install(PROGRAMS script/run.sh
  DESTINATION share/${PROJECT_NAME}/script
)

ament_export_dependencies(
  rclcpp
  builtin_interfaces
  std_msgs
  sensor_msgs
  pcl_conversions
  rosidl_default_runtime
)

ament_package()
