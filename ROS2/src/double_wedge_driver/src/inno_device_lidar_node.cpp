#include <rclcpp/rclcpp.hpp>

#include <builtin_interfaces/msg/time.hpp>

#include <double_wedge_driver/msg/custom_msg.hpp>
#include <double_wedge_driver/msg/custom_point.hpp>
#include <double_wedge_driver/msg/lidar_scan_bundle.hpp>
#include <double_wedge_driver/msg/lidar_status.hpp>

#include <sensor_msgs/msg/point_cloud2.hpp>

#include <boost/asio.hpp>
#include <Eigen/Core>
#include <Eigen/Geometry>
#include <pcl/common/transforms.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <termios.h>
#include <thread>
#include <unistd.h>
#include <vector>

// 安装方向：1 为正常安装，2 为倒立安装。
#define INSTALL_DIRECTION 1
#define IS_FILTER_LOW_REFLECTIVITY 0     // 是否过滤低反射率点
#define LOW_REFLECTIVITY_THRESHOLD 130   // 低反射率阈值

// 调试模式开启时可用 w/s 调俯仰角、a/d 调航向角。
#define DEBUG_MODE 0

namespace
{

using CustomMsg = double_wedge_driver::msg::CustomMsg;
using CustomPoint = double_wedge_driver::msg::CustomPoint;
using LidarScanBundle = double_wedge_driver::msg::LidarScanBundle;
using LidarStatus = double_wedge_driver::msg::LidarStatus;

// 俯仰角分辨率（度）
constexpr float kPitchResolution = 0.0258F;
// 航向角分辨率（度）
constexpr float kYawResolution = 0.0432F;
// 距离分辨率（毫米）
constexpr float kDistanceResolution = 16.0F;
// 每个UDP包包含的点数
constexpr std::size_t kPointsPerPacket = 200;
// 每个包点数据字节数
constexpr std::size_t kPacketDataBytes = kPointsPerPacket * 7;
// 时间戳字段在包中的偏移
constexpr std::size_t kTimestampIndex = 1400;
// 出厂信息字段在包中的偏移
constexpr std::size_t kFactoryIndex = kTimestampIndex + 8;
// 最小包大小
constexpr std::size_t kMinimumPacketSize = kFactoryIndex + 2;
// 缓存的包数量（攒够后一起发布）
constexpr int kCachePacketCount = 100;
// 超时阈值（秒），超过则判定断连
constexpr double kTimeoutThresholdSeconds = 0.5;

// 俯仰角偏移（度）
float g_pitch_offset = 0.0F;
// 航向角偏移（度）
float g_yaw_offset = 0.0F;
// 调试模式下角度调节步长（度）
float g_angle_step = 0.05F;

std::shared_ptr<rclcpp::Node> g_node;
// 激光雷达扫描包发布者
rclcpp::Publisher<LidarScanBundle>::SharedPtr g_lidar_pub;
// Livox格式点云发布者
rclcpp::Publisher<CustomMsg>::SharedPtr g_livox_pub;
// 激光雷达状态发布者
rclcpp::Publisher<LidarStatus>::SharedPtr g_livox_status_pub;
// 状态检查定时器
rclcpp::TimerBase::SharedPtr g_status_timer;

// UDP通信相关
boost::asio::io_service g_io_service;
boost::asio::ip::udp::socket g_udp_socket(g_io_service);
boost::asio::ip::udp::endpoint g_sender_endpoint;
std::mutex g_state_mutex;
std::chrono::steady_clock::time_point g_last_receive_time;

// 激光雷达坐标系名称
std::string g_lidar_frame_id = "lidar_frame";
// 已接收的包计数
std::size_t g_clouds_received = 0;
// 时间同步标志（true表示使用GPS同步时间）
bool g_sync_flag = false;

// 缓存的扫描包数据
LidarScanBundle g_bundle;
// Livox格式消息缓存
CustomMsg g_livox_msg;
// 激光雷达状态消息缓存
LidarStatus g_livox_status_msg;

// 单个激光雷达点数据
struct LidarData
{
  float distance;        // 距离（毫米）
  float pitch;           // 俯仰角（度）
  float yaw;             // 航向角（度）
  std::uint8_t reflectivity;  // 反射率
};

// 系统状态结构体
struct SystemStatus
{
  bool is_disconnect = false;               // 是否断连
  bool is_sync_time_ok = false;             // 时间同步是否正常
  bool is_motor_one_ok = false;             // 电机1是否正常
  bool is_motor_two_ok = false;             // 电机2是否正常
  bool is_encoder_one_ok = false;           // 编码器1是否正常
  bool is_encoder_two_ok = false;           // 编码器2是否正常
  bool is_sys_temp_ok = false;              // 系统温度是否正常
  bool is_receiver_temp_ok = false;         // 接收器温度是否正常
  bool is_laser_temp_ok = false;            // 激光器温度是否正常
  bool is_laser_ray_ok = false;             // 激光发射是否正常
  bool is_receiver_bias_voltage_ok = false; // 接收器偏置电压是否正常
};

SystemStatus g_lidar_status;

// 检测键盘是否有按键按下（非阻塞）
bool kbhit()
{
  termios oldt{};
  termios newt{};
  if (tcgetattr(STDIN_FILENO, &oldt) != 0) {
    return false;
  }
  newt = oldt;
  newt.c_lflag &= ~(ICANON | ECHO);
  tcsetattr(STDIN_FILENO, TCSANOW, &newt);
  const int old_flags = fcntl(STDIN_FILENO, F_GETFL, 0);
  fcntl(STDIN_FILENO, F_SETFL, old_flags | O_NONBLOCK);
  const int ch = getchar();
  tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
  fcntl(STDIN_FILENO, F_SETFL, old_flags);
  if (ch != EOF) {
    ungetc(ch, stdin);
    return true;
  }
  return false;
}

// 处理调试模式下的键盘输入
void handle_keyboard_input()
{
  static auto last_press_time = std::chrono::steady_clock::time_point{};
  constexpr double key_press_interval = 0.2;  // 按键最小间隔（秒）
  if (!kbhit()) {
    return;
  }

  const auto now = std::chrono::steady_clock::now();
  if (std::chrono::duration<double>(now - last_press_time).count() < key_press_interval) {
    getchar();
    return;
  }

  const int key = getchar();
  bool changed = false;
  {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    switch (key) {
      case 'w': g_pitch_offset += g_angle_step; changed = true; break;   // 俯仰角+
      case 's': g_pitch_offset -= g_angle_step; changed = true; break;   // 俯仰角-
      case 'a': g_yaw_offset -= g_angle_step; changed = true; break;     // 航向角-
      case 'd': g_yaw_offset += g_angle_step; changed = true; break;     // 航向角+
      case 'r': g_angle_step += 0.01F; changed = true; break;            // 步长+
      case 'f':
        g_angle_step = std::max(0.01F, g_angle_step - 0.01F);            // 步长-
        changed = true;
        break;
      default: break;
    }
    if (changed) {
      std::cout << "[调试] 俯仰角偏移=" << g_pitch_offset
                << "°, 航向角偏移=" << g_yaw_offset
                << "°, 调节步长=" << g_angle_step << "°." << std::endl;
    }
  }
  if (changed) {
    last_press_time = now;
  }
}

// 将Unix时间戳转换为人类可读格式
std::string convert_timestamp_to_human_readable(double timestamp)
{
  const auto time_value = static_cast<std::time_t>(timestamp);
  const std::tm *time_info = std::localtime(&time_value);
  if (time_info == nullptr) {
    return "无效时间";
  }
  char buffer[32]{};
  std::strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", time_info);
  const int milliseconds = static_cast<int>((timestamp - std::floor(timestamp)) * 1000.0);
  std::ostringstream output;
  output << buffer << "." << std::setfill('0') << std::setw(3) << milliseconds;
  return output.str();
}

// 自定义时间转换函数（年月日时分秒转Unix时间戳）
unsigned long long my_mktimea(
  unsigned int year0, unsigned int mon0, unsigned int day,
  unsigned int hour, unsigned int min, unsigned int sec)
{
  unsigned int mon = mon0;
  unsigned int year = year0;
  if (0 >= static_cast<int>(mon -= 2)) {
    mon += 12;
    year -= 1;
  }
  return (((static_cast<unsigned long long>(
      year / 4 - year / 100 + year / 400 + 367 * mon / 12 + day) +
      year * 365 - 719499) * 24 + hour) * 60 + min) * 60 + sec;
}

// 将double时间戳转换为ROS消息时间
builtin_interfaces::msg::Time timestamp_to_message(double timestamp)
{
  if (!std::isfinite(timestamp) || timestamp < 0.0 ||
      timestamp > static_cast<double>(std::numeric_limits<std::int32_t>::max())) {
    return g_node->now();
  }
  const auto nanoseconds = static_cast<std::int64_t>(std::llround(timestamp * 1.0e9));
  return rclcpp::Time(nanoseconds, RCL_SYSTEM_TIME);
}

// 解析出厂状态字段（俯仰角/航向角偏移等）
void parse_status(std::uint16_t data)
{
  const std::uint8_t attribute = static_cast<std::uint8_t>((data >> 12) & 0x0F);
  const std::uint16_t value = data & 0x0FFF;
  switch (attribute) {
    case 1:
      if (DEBUG_MODE == 0) {
        g_pitch_offset = (static_cast<int>(value) - 2048) * 0.01F;
      }
      break;
    case 2:
      if (DEBUG_MODE == 0) {
        g_yaw_offset = (static_cast<int>(value) - 2048) * 0.01F;
      }
      break;
    default:
      // 属性 0 和 3~11 的具体状态字段未在原协议实现中展开，保持原行为。
      break;
  }
}

// 获取激光雷达旋转变换矩阵（安装方向+俯仰/航向偏移）
Eigen::Affine3f get_lidar_rotation_transform()
{
  Eigen::Affine3f transform = Eigen::Affine3f::Identity();
#if INSTALL_DIRECTION == 2
  // 倒立安装：绕X轴旋转180度
  transform.rotate(Eigen::AngleAxisf(static_cast<float>(M_PI), Eigen::Vector3f::UnitX()));
#endif
  // 绕Y轴旋转俯仰角
  transform.rotate(Eigen::AngleAxisf(g_pitch_offset * static_cast<float>(M_PI) / 180.0F,
                                     Eigen::Vector3f::UnitY()));
  // 绕Z轴旋转航向角
  transform.rotate(Eigen::AngleAxisf(g_yaw_offset * static_cast<float>(M_PI) / 180.0F,
                                     Eigen::Vector3f::UnitZ()));
  return transform;
}

// 处理单个UDP数据包
void process_udp_packet(const std::vector<std::uint8_t> &packet, std::size_t packet_size)
{
  std::lock_guard<std::mutex> lock(g_state_mutex);
  // 检查包完整性
  if (packet_size < kMinimumPacketSize || packet_size < kPacketDataBytes) {
    RCLCPP_WARN(g_node->get_logger(), "收到不完整的UDP包: %zu 字节", packet_size);
    return;
  }

  // 解析200个点的数据
  std::vector<LidarData> lidar_points;
  lidar_points.reserve(kPointsPerPacket);
  for (std::size_t i = 0; i < kPointsPerPacket; ++i) {
    const std::size_t offset = i * 7;  // 每个点占7字节
    LidarData point{};
    // 距离（2字节，大端）* 分辨率
    point.distance = static_cast<float>((static_cast<std::uint16_t>(packet[offset]) << 8) |
                                        packet[offset + 1]) * kDistanceResolution;
    // 俯仰角（2字节，大端）* 分辨率
    point.pitch = static_cast<float>((static_cast<std::uint16_t>(packet[offset + 2]) << 8) |
                                     packet[offset + 3]) * kPitchResolution;
    // 航向角（2字节，大端）* 分辨率
    point.yaw = static_cast<float>((static_cast<std::uint16_t>(packet[offset + 4]) << 8) |
                                   packet[offset + 5]) * kYawResolution;
    // 反射率（1字节）
    point.reflectivity = packet[offset + 6];
    lidar_points.push_back(point);
  }

  // 解析时间戳
  const std::size_t timestamp_index = kTimestampIndex;
  const std::size_t factory_index = kFactoryIndex;
  std::uint64_t milliseconds =
    (static_cast<std::uint16_t>(packet[timestamp_index + 6]) << 8) |
    packet[timestamp_index + 7];
  double timestamp = 0.0;

  if (packet[timestamp_index] & 0x80U) {
    // GPS同步时间格式：年月日时分秒
    std::tm time_info{};
    time_info.tm_year = static_cast<int>(packet[timestamp_index] & 0x7FU) + 100;
    time_info.tm_mon = static_cast<int>(packet[timestamp_index + 1]) - 1;
    time_info.tm_mday = packet[timestamp_index + 2];
    time_info.tm_hour = packet[timestamp_index + 3];
    time_info.tm_min = packet[timestamp_index + 4];
    time_info.tm_sec = packet[timestamp_index + 5];
    timestamp = static_cast<double>(my_mktimea(
      static_cast<unsigned int>(time_info.tm_year + 1900),
      static_cast<unsigned int>(time_info.tm_mon + 1),
      static_cast<unsigned int>(time_info.tm_mday),
      static_cast<unsigned int>(time_info.tm_hour),
      static_cast<unsigned int>(time_info.tm_min),
      static_cast<unsigned int>(time_info.tm_sec))) + milliseconds * 0.001;
    g_sync_flag = true;
  } else {
    // 设备内部时间格式：秒数（大端，6字节）+ 毫秒
    const std::uint64_t seconds =
      static_cast<std::uint64_t>(packet[timestamp_index + 5]) |
      (static_cast<std::uint64_t>(packet[timestamp_index + 4]) << 8) |
      (static_cast<std::uint64_t>(packet[timestamp_index + 3]) << 16) |
      (static_cast<std::uint64_t>(packet[timestamp_index + 2]) << 24) |
      (static_cast<std::uint64_t>(packet[timestamp_index + 1]) << 32) |
      (static_cast<std::uint64_t>(packet[timestamp_index] & 0x7FU) << 40);
    // 协议字段为毫秒，原 ROS1 代码在此处漏乘 0.001，迁移时修正。
    timestamp = static_cast<double>(seconds) + milliseconds * 0.001;
    g_sync_flag = false;
    if (timestamp < 0.0 || timestamp > static_cast<double>(std::numeric_limits<std::uint32_t>::max())) {
      timestamp = g_node->now().seconds();
    }
  }

  g_lidar_status.is_sync_time_ok = !g_sync_flag;
  // 解析出厂状态字段
  const std::uint16_t factory =
    (static_cast<std::uint16_t>(packet[factory_index + 1]) << 8) | packet[factory_index];
  parse_status(factory);

  // 极坐标转笛卡尔坐标，并构建点云
  pcl::PointCloud<pcl::PointXYZI> lidar_cloud;
  for (std::size_t i = 0; i < lidar_points.size(); ++i) {
#if IS_FILTER_LOW_REFLECTIVITY == 1
    // 过滤低反射率点
    if (lidar_points[i].reflectivity > LOW_REFLECTIVITY_THRESHOLD) {
      continue;
    }
#endif
    const float distance = lidar_points[i].distance / 1000.0F;  // 毫米转米
    const float yaw = lidar_points[i].yaw * static_cast<float>(M_PI) / 180.0F;
    const float pitch = lidar_points[i].pitch * static_cast<float>(M_PI) / 180.0F;
    const float x = distance * std::cos(yaw) * std::cos(pitch);
    const float y = distance * std::sin(yaw) * std::cos(pitch);
    const float z = distance * std::sin(pitch);

    pcl::PointXYZI pcl_point;
#if INSTALL_DIRECTION == 2
    // 倒立安装坐标变换
    pcl_point.x = z;
    pcl_point.y = x;
    pcl_point.z = y;
#else
    // 正常安装坐标变换
    pcl_point.x = z;
    pcl_point.y = -x;
    pcl_point.z = -y;
#endif
    pcl_point.intensity = lidar_points[i].reflectivity;
    lidar_cloud.points.push_back(pcl_point);

    // 同时构建Livox格式自定义点
    CustomPoint custom_point;
#if INSTALL_DIRECTION == 2
    custom_point.x = z;
    custom_point.y = x;
    custom_point.z = y;
#else
    custom_point.x = z;
    custom_point.y = -x;
    custom_point.z = -y;
#endif
    custom_point.reflectivity = lidar_points[i].reflectivity;
    custom_point.tag = 0;
    custom_point.line = 0;
    custom_point.offset_time = static_cast<std::uint32_t>(i * 5000U + g_clouds_received * 1000000U);
    g_livox_msg.points.push_back(custom_point);
  }

  const auto timestamp_message = timestamp_to_message(timestamp);
  // 第一个包时设置消息头
  if (g_clouds_received == 0) {
    g_bundle.header.stamp = timestamp_message;
    g_bundle.header.frame_id = g_lidar_frame_id;
    g_livox_msg.header.stamp = timestamp_message;
    g_livox_msg.header.frame_id = g_lidar_frame_id;
    g_livox_msg.timebase = static_cast<std::uint64_t>(timestamp * 1.0e9);
    RCLCPP_DEBUG(g_node->get_logger(), "激光雷达时间戳: 同步=%s, 时间=%s",
                 g_sync_flag ? "true" : "false",
                 convert_timestamp_to_human_readable(timestamp).c_str());
  }

  // 将点云存入缓存
  lidar_cloud.width = static_cast<std::uint32_t>(lidar_cloud.points.size());
  lidar_cloud.height = 1;
  lidar_cloud.is_dense = true;
  pcl::toROSMsg(lidar_cloud, g_bundle.clouds[g_clouds_received]);
  g_bundle.clouds[g_clouds_received].header.stamp = timestamp_message;
  g_bundle.clouds[g_clouds_received].header.frame_id = g_lidar_frame_id;
  ++g_clouds_received;

  // 攒够100个包后统一发布
  if (g_clouds_received >= static_cast<std::size_t>(kCachePacketCount)) {
    // 应用旋转变换（安装方向+角度偏移）
    const Eigen::Affine3f rotation = get_lidar_rotation_transform();
    for (auto &cloud_message : g_bundle.clouds) {
      pcl::PointCloud<pcl::PointXYZI> cloud;
      pcl::fromROSMsg(cloud_message, cloud);
      pcl::transformPointCloud(cloud, cloud, rotation);
      pcl::toROSMsg(cloud, cloud_message);
      cloud_message.header.frame_id = g_lidar_frame_id;
    }
    for (auto &point : g_livox_msg.points) {
      Eigen::Vector3f xyz(point.x, point.y, point.z);
      xyz = rotation * xyz;
      point.x = xyz.x();
      point.y = xyz.y();
      point.z = xyz.z();
    }

    // 填充附加信息并发布
    g_bundle.software_version = std::to_string(static_cast<int>(factory));
    g_bundle.lidar_temperature = 25.0F;
    g_bundle.lidar_rotation_speed = 600.0F;
    g_livox_msg.lidar_id = 1;
    g_livox_msg.point_num = static_cast<std::uint32_t>(g_livox_msg.points.size());
    g_lidar_pub->publish(g_bundle);
    g_livox_pub->publish(g_livox_msg);

    // 清空缓存
    for (auto &cloud : g_bundle.clouds) {
      cloud.data.clear();
      cloud.width = 0;
      cloud.height = 0;
      cloud.row_step = 0;
      cloud.point_step = 0;
      cloud.is_dense = false;
    }
    g_livox_msg.points.clear();
    g_clouds_received = 0;
    RCLCPP_DEBUG(g_node->get_logger(), "已发布激光雷达数据包，包含 %d 个包", kCachePacketCount);
  }

  g_last_receive_time = std::chrono::steady_clock::now();
}

// 前向声明
void start_receive(std::vector<std::uint8_t> *data);

// UDP异步接收回调
void handle_receive(
  const boost::system::error_code &error, std::size_t bytes_transferred,
  std::vector<std::uint8_t> *data)
{
  if (error == boost::asio::error::operation_aborted) {
    return;
  }
  if (error) {
    RCLCPP_ERROR(g_node->get_logger(), "接收UDP数据出错: %s", error.message().c_str());
    return;
  }

  process_udp_packet(*data, bytes_transferred);
  if (rclcpp::ok()) {
    start_receive(data);
  }
}

// 启动异步UDP接收
void start_receive(std::vector<std::uint8_t> *data)
{
  g_udp_socket.async_receive_from(
    boost::asio::buffer(*data), g_sender_endpoint,
    [data](const boost::system::error_code &error, std::size_t bytes_transferred) {
      handle_receive(error, bytes_transferred, data);
    });
}

// 超时检查回调（每100ms执行一次）
void timeout_check_callback()
{
  std::lock_guard<std::mutex> lock(g_state_mutex);
  const double elapsed = std::chrono::duration<double>(
    std::chrono::steady_clock::now() - g_last_receive_time).count();
  static bool was_disconnected = false;
  g_lidar_status.is_disconnect = elapsed > kTimeoutThresholdSeconds;
  if (g_lidar_status.is_disconnect && !was_disconnected) {
    RCLCPP_WARN(g_node->get_logger(), "激光雷达数据超时: 距上次包已 %.2f 秒", elapsed);
  } else if (!g_lidar_status.is_disconnect && was_disconnected) {
    RCLCPP_INFO(g_node->get_logger(), "激光雷达数据流已恢复");
  }
  was_disconnected = g_lidar_status.is_disconnect;

  // 发布状态消息
  g_livox_status_msg.header.stamp = g_node->now();
  g_livox_status_msg.header.frame_id = g_lidar_frame_id;
  g_livox_status_msg.connect_state = g_lidar_status.is_disconnect;
  g_livox_status_msg.sync_time_state = g_lidar_status.is_sync_time_ok;
  g_livox_status_msg.motor_one_state = g_lidar_status.is_motor_one_ok;
  g_livox_status_msg.motor_two_state = g_lidar_status.is_motor_two_ok;
  g_livox_status_msg.encoder_one_state = g_lidar_status.is_encoder_one_ok;
  g_livox_status_msg.encoder_two_state = g_lidar_status.is_encoder_two_ok;
  g_livox_status_msg.sys_temp_state = g_lidar_status.is_sys_temp_ok;
  g_livox_status_msg.rec_temp_state = g_lidar_status.is_receiver_temp_ok;
  g_livox_status_msg.laser_temp_state = g_lidar_status.is_laser_temp_ok;
  g_livox_status_msg.laser_ray_state = g_lidar_status.is_laser_ray_ok;
  g_livox_status_msg.rec_bias_vol_state = g_lidar_status.is_receiver_bias_voltage_ok;
  g_livox_status_pub->publish(g_livox_status_msg);
}

}  // namespace

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  g_node = std::make_shared<rclcpp::Node>("double_wedge_driver_lidar");

  // 声明并读取参数
  const int udp_port = g_node->declare_parameter<int>("udp_port", 2368);
  g_lidar_frame_id = g_node->declare_parameter<std::string>("frame_id", "lidar_frame");
  const std::string lidar_topic = g_node->declare_parameter<std::string>("lidar_topic", "/lidar_data");
  const std::string livox_topic = g_node->declare_parameter<std::string>("livox_topic", "/livox_data");
  const std::string status_topic = g_node->declare_parameter<std::string>("status_topic", "/livox_status");

  // 校验端口号范围
  if (udp_port < 1 || udp_port > 65535) {
    RCLCPP_FATAL(g_node->get_logger(), "udp_port 必须在 1 到 65535 之间");
    rclcpp::shutdown();
    return 1;
  }

  // 创建发布者
  const rclcpp::SensorDataQoS sensor_qos;
  // LidarScanBundle 一次携带 100 个点云包，序列化后会跨越大量 DDS 分片。
  // 私有的同机节点间链路使用 Reliable，避免任一分片丢失导致整组作废。
  const auto bundle_qos = rclcpp::QoS(rclcpp::KeepLast(5)).reliable();
  g_lidar_pub = g_node->create_publisher<LidarScanBundle>(lidar_topic, bundle_qos);
  g_livox_pub = g_node->create_publisher<CustomMsg>(livox_topic, sensor_qos);
  g_livox_status_pub = g_node->create_publisher<LidarStatus>(status_topic, rclcpp::QoS(5));
  // 每100ms检查一次超时状态
  g_status_timer = g_node->create_wall_timer(
    std::chrono::milliseconds(100), timeout_check_callback);

  // 预分配缓存
  g_bundle.clouds.resize(kCachePacketCount);
  g_livox_msg.points.reserve(kCachePacketCount * kPointsPerPacket);

  // 打开并绑定UDP端口
  try {
    g_udp_socket.open(boost::asio::ip::udp::v4());
    g_udp_socket.set_option(boost::asio::socket_base::reuse_address(true));
    g_udp_socket.bind(boost::asio::ip::udp::endpoint(
      boost::asio::ip::udp::v4(), static_cast<unsigned short>(udp_port)));
  } catch (const boost::system::system_error &error) {
    RCLCPP_FATAL(g_node->get_logger(), "无法绑定UDP端口 %d: %s", udp_port, error.what());
    g_node.reset();
    rclcpp::shutdown();
    return 1;
  }

  // 启动异步UDP接收
  std::vector<std::uint8_t> data(1500);
  g_last_receive_time = std::chrono::steady_clock::now();
  start_receive(&data);
  std::thread io_thread([]() { g_io_service.run(); });

#if DEBUG_MODE == 1
  RCLCPP_WARN(g_node->get_logger(),
              "调试模式已开启: w/s 调俯仰角, a/d 调航向角, r/f 调步长");
#else
  RCLCPP_INFO(g_node->get_logger(), "激光雷达驱动正在监听UDP端口 %d", udp_port);
#endif

  // 主循环
  rclcpp::Rate loop_rate(20.0);
  while (rclcpp::ok()) {
    rclcpp::spin_some(g_node);
#if DEBUG_MODE == 1
    handle_keyboard_input();
#endif
    loop_rate.sleep();
  }

  // 清理资源
  boost::system::error_code ignored_error;
  g_udp_socket.cancel(ignored_error);
  g_udp_socket.close(ignored_error);
  g_io_service.stop();
  if (io_thread.joinable()) {
    io_thread.join();
  }
  g_status_timer.reset();
  g_node.reset();
  rclcpp::shutdown();
  return 0;
}
