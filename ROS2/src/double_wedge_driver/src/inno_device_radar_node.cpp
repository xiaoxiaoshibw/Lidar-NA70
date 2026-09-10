#include <rclcpp/rclcpp.hpp>

#include <builtin_interfaces/msg/time.hpp>

#include <double_wedge_driver/msg/radar_msg.hpp>
#include <double_wedge_driver/msg/radar_status.hpp>

#include <sensor_msgs/msg/point_cloud2.hpp>

#include <pcl/common/common.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <chrono>
#include <deque>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <termios.h>
#include <vector>

#include <sys/types.h>

namespace
{

using RadarMsg = double_wedge_driver::msg::RadarMsg;
using RadarStatus = double_wedge_driver::msg::RadarStatus;

// 帧头高字节
constexpr std::uint8_t kFrameHeaderHigh = 0xAA;
// 帧头低字节
constexpr std::uint8_t kFrameHeaderLow = 0x55;
// 最小有效载荷长度
constexpr std::uint16_t kMinimumPayloadLength = 6;
// 最大有效载荷长度
constexpr std::uint16_t kMaximumPayloadLength = 2044;

std::shared_ptr<rclcpp::Node> g_node;
// 雷达点云发布者
rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr g_radar_pub;
// 雷达消息发布者
rclcpp::Publisher<RadarMsg>::SharedPtr g_radar_msg_pub;
// 雷达状态发布者
rclcpp::Publisher<RadarStatus>::SharedPtr g_radar_status_pub;
// 状态发布定时器
rclcpp::TimerBase::SharedPtr g_status_timer;
// 最近一次收到数据的时间点
std::chrono::steady_clock::time_point g_last_data_time;
// 雷达坐标系名称
std::string g_radar_frame_id = "map";
// 雷达状态消息缓存
RadarStatus g_radar_status_message;

// 串口操作类
class SerialPort
{
public:
  SerialPort() = default;
  SerialPort(const SerialPort &) = delete;
  SerialPort &operator=(const SerialPort &) = delete;

  ~SerialPort()
  {
    close();
  }

  // 打开串口设备并配置波特率
  void open(const std::string &device, int baud_rate)
  {
    close();
    fd_ = ::open(device.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd_ < 0) {
      throw std::runtime_error("打开 " + device + " 失败: " + std::strerror(errno));
    }

    termios options{};
    if (tcgetattr(fd_, &options) != 0) {
      const std::string error = std::strerror(errno);
      close();
      throw std::runtime_error("获取串口属性失败: " + error);
    }
    cfmakeraw(&options);
    const speed_t speed = baud_to_termios(baud_rate);
    cfsetispeed(&options, speed);
    cfsetospeed(&options, speed);
    options.c_cflag |= CLOCAL | CREAD;
    options.c_cflag &= ~CSTOPB;
    options.c_cflag &= ~CRTSCTS;
    options.c_cflag &= ~PARENB;
    options.c_cflag &= ~CSIZE;
    options.c_cflag |= CS8;
    options.c_cc[VMIN] = 0;
    options.c_cc[VTIME] = 1;
    if (tcsetattr(fd_, TCSANOW, &options) != 0) {
      const std::string error = std::strerror(errno);
      close();
      throw std::runtime_error("设置串口属性失败: " + error);
    }
    tcflush(fd_, TCIOFLUSH);
  }

  // 串口是否已打开
  bool is_open() const
  {
    return fd_ >= 0;
  }

  // 从串口读取数据
  ssize_t read(std::uint8_t *buffer, std::size_t size)
  {
    if (!is_open()) {
      return 0;
    }
    const ssize_t count = ::read(fd_, buffer, size);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
      return 0;
    }
    if (count < 0) {
      throw std::runtime_error("串口读取失败: " + std::string(std::strerror(errno)));
    }
    return count;
  }

  // 向串口写入数据
  void write(const std::uint8_t *data, std::size_t size)
  {
    if (!is_open()) {
      throw std::runtime_error("串口未打开");
    }
    std::size_t written = 0;
    while (written < size) {
      const ssize_t count = ::write(fd_, data + written, size - written);
      if (count > 0) {
        written += static_cast<std::size_t>(count);
      } else if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) {
        std::this_thread::yield();
      } else {
        throw std::runtime_error("串口写入失败: " + std::string(std::strerror(errno)));
      }
    }
  }

  // 关闭串口
  void close()
  {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }

private:
  // 波特率转换为termios常量
  static speed_t baud_to_termios(int baud_rate)
  {
    switch (baud_rate) {
      case 9600: return B9600;
      case 19200: return B19200;
      case 38400: return B38400;
      case 57600: return B57600;
      case 115200: return B115200;
      case 230400: return B230400;
      default: throw std::invalid_argument("不支持的波特率: " + std::to_string(baud_rate));
    }
  }

  int fd_ = -1;
};

// 解析后的帧结构
struct ParsedFrame
{
  std::uint8_t frame_type = 0;       // 帧类型
  std::uint16_t payload_length = 0;  // 有效载荷长度
  std::vector<std::uint8_t> bytes;   // 帧原始字节
};

// 帧解析器：逐字节解析串口数据流，提取完整帧
class FrameParser
{
public:
  // 校验和错误计数
  std::size_t invalid_checksum_count() const
  {
    return invalid_checksum_count_;
  }

  // 逐字节输入，返回true表示解析出一帧完整数据
  bool parse(std::uint8_t byte, ParsedFrame &output)
  {
    switch (state_) {
      case State::HeaderHigh:
        if (byte == kFrameHeaderHigh) {
          bytes_.clear();
          bytes_.push_back(byte);
          state_ = State::HeaderLow;
        }
        break;
      case State::HeaderLow:
        if (byte == kFrameHeaderLow) {
          bytes_.push_back(byte);
          state_ = State::FrameType;
        } else if (byte == kFrameHeaderHigh) {
          bytes_.assign(1, byte);
        } else {
          reset();
        }
        break;
      case State::FrameType:
        bytes_.push_back(byte);
        state_ = State::LengthHigh;
        break;
      case State::LengthHigh:
        bytes_.push_back(byte);
        state_ = State::LengthLow;
        break;
      case State::LengthLow:
        bytes_.push_back(byte);
        payload_length = static_cast<std::uint16_t>(bytes_[3] << 8) | bytes_[4];
        if (payload_length < kMinimumPayloadLength || payload_length > kMaximumPayloadLength) {
          reset();
        } else {
          state_ = State::Payload;
        }
        break;
      case State::Payload:
        bytes_.push_back(byte);
        if (bytes_.size() == static_cast<std::size_t>(payload_length) + 4U) {
          const std::uint8_t calculated = checksum(bytes_, payload_length);
          const bool valid = calculated == bytes_.back();
          if (valid) {
            output.frame_type = bytes_[2];
            output.payload_length = payload_length;
            output.bytes = bytes_;
          } else {
            ++invalid_checksum_count_;
          }
          reset();
          return valid;
        }
        if (bytes_.size() > static_cast<std::size_t>(payload_length) + 4U) {
          reset();
        }
        break;
    }
    return false;
  }

private:
  // 解析状态机状态
  enum class State { HeaderHigh, HeaderLow, FrameType, LengthHigh, LengthLow, Payload };

  // 计算校验和（对有效载荷字节求和）
  static std::uint8_t checksum(const std::vector<std::uint8_t> &bytes, std::uint16_t length)
  {
    std::uint8_t sum = 0;
    for (std::size_t i = 3; i < static_cast<std::size_t>(length) + 3U; ++i) {
      sum = static_cast<std::uint8_t>(sum + bytes[i]);
    }
    return sum;
  }

  // 重置解析器状态
  void reset()
  {
    state_ = State::HeaderHigh;
    payload_length = 0;
    bytes_.clear();
  }

  State state_ = State::HeaderHigh;
  std::uint16_t payload_length = 0;
  std::vector<std::uint8_t> bytes_;
  std::size_t invalid_checksum_count_ = 0;
};

// 字节流读取工具：大端序读取多字节字段
class ByteStream
{
public:
  explicit ByteStream(const std::vector<std::uint8_t> &data)
  : data_(data) {}

  // 从指定偏移量以大端序读取T类型值
  template<typename T>
  T read_be(std::size_t offset) const
  {
    if (offset + sizeof(T) > data_.size()) {
      throw std::out_of_range("雷达帧字段超出帧长度范围");
    }
    T value = 0;
    for (std::size_t i = 0; i < sizeof(T); ++i) {
      value = static_cast<T>((value << 8) | data_[offset + i]);
    }
    return value;
  }

private:
  const std::vector<std::uint8_t> &data_;
};

// 处理解析后的雷达帧
void frame_handler(const ParsedFrame &parsed)
{
  // 只处理类型1的数据帧
  if (parsed.frame_type != 1) {
    if (parsed.frame_type == 2) {
      RCLCPP_DEBUG(g_node->get_logger(), "雷达命令回复帧已解析");
    } else {
      RCLCPP_WARN(g_node->get_logger(), "未知雷达帧类型: 0x%02x", parsed.frame_type);
    }
    return;
  }

  try {
    const ByteStream stream(parsed.bytes);
    // 检查帧头完整性
    if (parsed.bytes.size() < 10U || parsed.payload_length + 3U > parsed.bytes.size() - 1U) {
      RCLCPP_WARN(g_node->get_logger(), "雷达帧头不完整");
      return;
    }

    // 解析帧头字段
    const std::uint8_t firmware_version = stream.read_be<std::uint8_t>(5);   // 固件版本
    const std::uint16_t radar_status = stream.read_be<std::uint16_t>(6);      // 雷达状态
    const std::uint16_t target_count = stream.read_be<std::uint16_t>(8);       // 目标数量
    const std::size_t payload_end = static_cast<std::size_t>(parsed.payload_length) + 3U;
    const std::size_t target_bytes = payload_end > 10U ? payload_end - 10U : 0U;
    const std::size_t max_target_count = target_bytes / 5U;  // 每个目标占5字节
    const std::size_t valid_target_count = std::min<std::size_t>(target_count, max_target_count);
    if (valid_target_count != target_count) {
      RCLCPP_WARN(g_node->get_logger(),
                  "雷达目标数量 %u 超出有效载荷容量；仅解码 %zu 个目标",
                  static_cast<unsigned int>(target_count), valid_target_count);
    }

    // 解析每个目标，构建点云
    pcl::PointCloud<pcl::PointXYZI> radar_cloud;
    float nearest_distance = 0.0F;       // 最近目标距离
    std::uint8_t nearest_intensity = 0;  // 最近目标强度
    bool has_target = false;
    std::size_t offset = 10U;
    for (std::size_t i = 0; i < valid_target_count; ++i) {
      const std::uint32_t distance_mm = stream.read_be<std::uint32_t>(offset);  // 距离(毫米)
      const std::uint8_t intensity = stream.read_be<std::uint8_t>(offset + 4U);  // 强度
      offset += 5U;

      pcl::PointXYZI point;
      point.x = static_cast<float>(distance_mm) * 0.001F;  // 毫米转米
      point.y = 0.0F;
      point.z = 0.0F;
      point.intensity = intensity;
      radar_cloud.points.push_back(point);
      // 记录最近目标
      if (!has_target || point.x < nearest_distance) {
        nearest_distance = point.x;
        nearest_intensity = intensity;
        has_target = true;
      }
    }

    // 设置点云属性
    radar_cloud.width = static_cast<std::uint32_t>(radar_cloud.points.size());
    radar_cloud.height = 1;
    radar_cloud.is_dense = true;
    const builtin_interfaces::msg::Time timestamp = g_node->now();

    // 发布雷达消息（最近目标信息）
    RadarMsg radar_message;
    radar_message.header.stamp = timestamp;
    radar_message.header.frame_id = g_radar_frame_id;
    radar_message.distance = nearest_distance;
    radar_message.software_version = firmware_version;
    radar_message.status_code = radar_status == 0x0808 ? 0 : radar_status;
    radar_message.intensity = nearest_intensity;
    g_radar_msg_pub->publish(radar_message);

    // 发布点云数据
    sensor_msgs::msg::PointCloud2 cloud_message;
    pcl::toROSMsg(radar_cloud, cloud_message);
    cloud_message.header.stamp = timestamp;
    cloud_message.header.frame_id = g_radar_frame_id;
    g_radar_pub->publish(cloud_message);

    // 更新并发布雷达状态
    g_radar_status_message.header = radar_message.header;
    g_radar_status_message.is_disconnect = false;
    g_radar_status_message.software_version = firmware_version;
    g_radar_status_message.status_code = static_cast<std::uint8_t>(radar_message.status_code & 0xFFU);
    g_radar_status_pub->publish(g_radar_status_message);
    g_last_data_time = std::chrono::steady_clock::now();
  } catch (const std::exception &error) {
    RCLCPP_WARN(g_node->get_logger(), "丢弃格式错误的雷达帧: %s", error.what());
  }
}

// 定时发布雷达状态（监控断连）
void publish_status()
{
  const double elapsed = std::chrono::duration<double>(
    std::chrono::steady_clock::now() - g_last_data_time).count();
  static bool was_disconnected = false;
  const bool disconnected = elapsed > 1.0;  // 超过1秒无数据视为断连
  if (disconnected && !was_disconnected) {
    RCLCPP_WARN(g_node->get_logger(), "雷达数据流已停止");
  } else if (!disconnected && was_disconnected) {
    RCLCPP_INFO(g_node->get_logger(), "雷达数据流已恢复");
  }
  was_disconnected = disconnected;

  g_radar_status_message.header.stamp = g_node->now();
  g_radar_status_message.header.frame_id = g_radar_frame_id;
  g_radar_status_message.is_disconnect = disconnected;
  g_radar_status_pub->publish(g_radar_status_message);
}

}  // namespace

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  g_node = std::make_shared<rclcpp::Node>("double_wedge_driver_radar");

  // 声明并读取参数
  const std::string serial_device = g_node->declare_parameter<std::string>(
    "radar_serial_port", "/dev/ttyUSB0");
  const int baud_rate = g_node->declare_parameter<int>("radar_baud_rate", 57600);
  const double blind_area = g_node->declare_parameter<double>("blind_area", 0.5);
  const bool hight_air_mode = g_node->declare_parameter<bool>("hight_air_mode", false);
  g_radar_frame_id = g_node->declare_parameter<std::string>("frame_id", "map");
  const std::string radar_topic = g_node->declare_parameter<std::string>(
    "radar_topic", "/radar_data");
  const std::string radar_message_topic = g_node->declare_parameter<std::string>(
    "radar_message_topic", "/radar_msg");
  const std::string radar_status_topic = g_node->declare_parameter<std::string>(
    "radar_status_topic", "/radar_status");

  RCLCPP_INFO(g_node->get_logger(), "雷达串口: %s, 波特率: %d",
              serial_device.c_str(), baud_rate);
  RCLCPP_INFO(g_node->get_logger(), "雷达盲区=%.3f, 高空模式=%s",
              blind_area, hight_air_mode ? "true" : "false");

  // 创建发布者
  // WSL2 mirrored 模式下 lo 的 UDP 大包(>1KB)被静默丢弃，BEST_EFFORT
  // 分片无法重组。PointCloud2 必须用 RELIABLE 才能跨进程送达。
  rclcpp::QoS radar_cloud_qos(10);
  radar_cloud_qos.reliable();
  g_radar_pub = g_node->create_publisher<sensor_msgs::msg::PointCloud2>(radar_topic, radar_cloud_qos);
  g_radar_msg_pub = g_node->create_publisher<RadarMsg>(radar_message_topic, rclcpp::QoS(5));
  g_radar_status_pub = g_node->create_publisher<RadarStatus>(radar_status_topic, rclcpp::QoS(5));
  // 每500ms发布一次状态
  g_status_timer = g_node->create_wall_timer(std::chrono::milliseconds(500), publish_status);
  g_last_data_time = std::chrono::steady_clock::now();

  // 打开串口并初始化雷达
  SerialPort radar_serial;
  try {
    radar_serial.open(serial_device, baud_rate);
    // 发送雷达回波模式配置命令
    const std::array<std::uint8_t, 11> radar_echo_mode = {
      0xAA, 0x55, 0x03, 0x00, 0x07, 0x04, 0x00, 0x00, 0x00, 0x01, 0x0C};
    radar_serial.write(radar_echo_mode.data(), radar_echo_mode.size());
    rclcpp::sleep_for(std::chrono::seconds(1));
  } catch (const std::exception &error) {
    RCLCPP_FATAL(g_node->get_logger(), "无法初始化雷达串口: %s", error.what());
    g_node.reset();
    rclcpp::shutdown();
    return 1;
  }

  RCLCPP_INFO(g_node->get_logger(), "雷达串口初始化完成");
  FrameParser parser;
  std::size_t reported_checksum_errors = 0;
  std::deque<std::uint8_t> circular_buffer;
  std::array<std::uint8_t, 512> read_buffer{};
  rclcpp::Rate loop_rate(30.0);

  // 主循环：读取串口数据并解析
  while (rclcpp::ok()) {
    const ssize_t count = radar_serial.read(read_buffer.data(), read_buffer.size());
    if (count > 0) {
      circular_buffer.insert(circular_buffer.end(), read_buffer.begin(), read_buffer.begin() + count);
      // 限制缓冲区大小，防止内存溢出
      while (circular_buffer.size() > 2048U) {
        circular_buffer.pop_front();
      }
      // 逐字节解析
      while (!circular_buffer.empty()) {
        ParsedFrame parsed;
        const std::uint8_t byte = circular_buffer.front();
        circular_buffer.pop_front();
        if (parser.parse(byte, parsed)) {
          frame_handler(parsed);
        }
        // 报告校验和错误
        const std::size_t checksum_errors = parser.invalid_checksum_count();
        if (checksum_errors != reported_checksum_errors) {
          reported_checksum_errors = checksum_errors;
          if (checksum_errors == 1U || checksum_errors % 200U == 0U) {
            RCLCPP_WARN(g_node->get_logger(),
                        "雷达帧校验和错误次数: %zu", checksum_errors);
          }
        }
      }
    }

    rclcpp::spin_some(g_node);
    loop_rate.sleep();
  }

  // 清理资源
  radar_serial.close();
  g_status_timer.reset();
  g_node.reset();
  rclcpp::shutdown();
  return 0;
}
