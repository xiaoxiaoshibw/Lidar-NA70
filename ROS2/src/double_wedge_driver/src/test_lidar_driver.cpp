#include <rclcpp/rclcpp.hpp>

#include <builtin_interfaces/msg/time.hpp>

#include <double_wedge_driver/msg/lidar_scan_bundle.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>

#include <pcl/filters/statistical_outlier_removal.h>
#include <pcl/filters/voxel_grid.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

#define USE_VOXEL_GRID_FILTER
#define USE_OUTLIER_REMOVAL
// #define USE_CUT_OFF_PC
// #define TEST

class LidarScanSubscriber final : public rclcpp::Node
{
public:
  LidarScanSubscriber()
  : Node("lidar_scan_subscriber")
  {
    const std::string input_topic = declare_parameter<std::string>("input_topic", "/lidar_data");
    output_topic_ = declare_parameter<std::string>("output_topic", "/inno_lidar_cloud");
    frame_id_ = declare_parameter<std::string>("frame_id", "lidar_link");

    const rclcpp::SensorDataQoS sensor_qos;
    const auto bundle_qos = rclcpp::QoS(rclcpp::KeepLast(5)).reliable();
    lidar_scan_sub_ = create_subscription<double_wedge_driver::msg::LidarScanBundle>(
      input_topic, bundle_qos,
      std::bind(&LidarScanSubscriber::lidar_scan_callback, this, std::placeholders::_1));
    cloud_pub_ = create_publisher<sensor_msgs::msg::PointCloud2>(output_topic_, sensor_qos);

#ifdef TEST
    voxel_filtered_cloud_sub_ = create_subscription<sensor_msgs::msg::PointCloud2>(
      output_topic_, sensor_qos,
      std::bind(&LidarScanSubscriber::voxel_filter_callback, this, std::placeholders::_1));
    voxel_filtered_cloud_pub_ = create_publisher<sensor_msgs::msg::PointCloud2>(
      "/inno_lidar_filtered_cloud", sensor_qos);
#endif
  }

private:
  void lidar_scan_callback(
    const double_wedge_driver::msg::LidarScanBundle::SharedPtr scan_msg)
  {
    pcl::PointCloud<pcl::PointXYZI> combined_cloud;
    builtin_interfaces::msg::Time acquisition_stamp{};
    bool has_acquisition_stamp = false;

    for (const auto &scan : scan_msg->clouds) {
      const std::size_t total_points = static_cast<std::size_t>(scan.width) * scan.height;
      if (total_points == 0U) {
        continue;
      }

      pcl::PointCloud<pcl::PointXYZI> cloud;
      pcl::fromROSMsg(scan, cloud);
      combined_cloud += cloud;
      acquisition_stamp = scan.header.stamp;
      has_acquisition_stamp = true;
    }

    sensor_msgs::msg::PointCloud2 cloud_message;
    pcl::toROSMsg(combined_cloud, cloud_message);
    cloud_message.header.frame_id = frame_id_;
    cloud_message.header.stamp = has_acquisition_stamp
                                     ? acquisition_stamp
                                     : static_cast<builtin_interfaces::msg::Time>(now());
    RCLCPP_DEBUG(get_logger(), "Published merged LiDAR cloud with %zu points",
                 combined_cloud.points.size());
    cloud_pub_->publish(cloud_message);
  }

#ifdef TEST
  void voxel_filter_callback(const sensor_msgs::msg::PointCloud2::SharedPtr cloud_msg)
  {
    auto cloud = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    pcl::fromROSMsg(*cloud_msg, *cloud);
    RCLCPP_DEBUG(get_logger(), "Original cloud has %zu points", cloud->points.size());

#ifdef USE_VOXEL_GRID_FILTER
    const auto voxel_start = std::chrono::steady_clock::now();
    pcl::VoxelGrid<pcl::PointXYZI> voxel_filter;
    voxel_filter.setInputCloud(cloud);
    voxel_filter.setLeafSize(0.5F, 0.5F, 0.5F);
    auto voxel_cloud = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    voxel_filter.filter(*voxel_cloud);
    const auto voxel_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - voxel_start).count();
    RCLCPP_DEBUG(get_logger(), "VoxelGrid filter took %lld ms and left %zu points",
                 static_cast<long long>(voxel_duration), voxel_cloud->points.size());
#ifdef USE_CUT_OFF_PC
    auto filtered_cloud = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    for (const auto &point : voxel_cloud->points) {
      if (point.x > 5.0F) {
        filtered_cloud->points.push_back(point);
      }
    }
#else
    auto filtered_cloud = voxel_cloud;
#endif
#else
    auto filtered_cloud = cloud;
#endif

#ifdef USE_OUTLIER_REMOVAL
    const auto outlier_start = std::chrono::steady_clock::now();
    auto cloud_cutoff = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    for (const auto &point : filtered_cloud->points) {
      if (point.x <= 30.0F) {
        cloud_cutoff->points.push_back(point);
      }
    }

    pcl::StatisticalOutlierRemoval<pcl::PointXYZI> outlier_filter;
    outlier_filter.setInputCloud(cloud_cutoff);
    outlier_filter.setMeanK(50);
    outlier_filter.setStddevMulThresh(1.0);
    auto final_cloud = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    if (!cloud_cutoff->empty()) {
      outlier_filter.filter(*final_cloud);
    }
    for (const auto &point : filtered_cloud->points) {
      if (point.x > 30.0F) {
        final_cloud->points.push_back(point);
      }
    }
    final_cloud->width = static_cast<std::uint32_t>(final_cloud->points.size());
    final_cloud->height = 1;
    final_cloud->is_dense = true;
    const auto outlier_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - outlier_start).count();
    RCLCPP_DEBUG(get_logger(), "Outlier filter took %lld ms and left %zu points",
                 static_cast<long long>(outlier_duration), final_cloud->points.size());
#else
    auto final_cloud = filtered_cloud;
#endif

    sensor_msgs::msg::PointCloud2 filtered_message;
    pcl::toROSMsg(*final_cloud, filtered_message);
    filtered_message.header = cloud_msg->header;
    voxel_filtered_cloud_pub_->publish(filtered_message);
  }
#endif

  std::string output_topic_;
  std::string frame_id_;
  rclcpp::Subscription<double_wedge_driver::msg::LidarScanBundle>::SharedPtr lidar_scan_sub_;
  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr cloud_pub_;
#ifdef TEST
  rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr voxel_filtered_cloud_sub_;
  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr voxel_filtered_cloud_pub_;
#endif
};

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<LidarScanSubscriber>();
  rclcpp::spin(node);
  node.reset();
  rclcpp::shutdown();
  return 0;
}
