#ifndef CAMSENSE_LIDAR__CAMSENSE_LIDAR_HPP_
#define CAMSENSE_LIDAR__CAMSENSE_LIDAR_HPP_

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace camsense_lidar
{

constexpr std::size_t kMaxLidarPointsPerScan = 720;
constexpr std::size_t kMaxLidarPacketSize = 256;

struct LidarPoint
{
  float angle_deg = 0.0F;
  float distance_mm = 0.0F;
};

struct DriverStats
{
  uint64_t bytes_read = 0;
  uint64_t packets = 0;
  uint64_t invalid_headers = 0;
  uint64_t valid_points = 0;
  uint64_t invalid_points = 0;
  uint64_t completed_scans = 0;
  std::size_t current_scan_points = 0;
};

class CamsenseLidar
{
public:
  CamsenseLidar(std::string port, int baud_rate, float center_base_angle_deg);
  ~CamsenseLidar();

  CamsenseLidar(const CamsenseLidar &) = delete;
  CamsenseLidar & operator=(const CamsenseLidar &) = delete;

  bool open();
  void close();
  bool isOpen() const;
  bool poll(std::vector<LidarPoint> & out_scan);
  DriverStats getStats() const;

private:
  bool processByte(uint8_t rx_byte, std::vector<LidarPoint> & out_scan);
  std::vector<LidarPoint> parsePacket(const uint8_t * buffer, uint8_t c_info, uint8_t point_count);
  bool configureSerial();

  std::string port_;
  int baud_rate_;
  float center_base_angle_deg_;
  int fd_ = -1;

  int state_ = 0;
  uint8_t c_info_ = 0;
  uint8_t packet_point_count_ = 0;
  float last_angle_deg_ = 0.0F;
  std::vector<LidarPoint> current_scan_;

  uint8_t payload_buffer_[kMaxLidarPacketSize] = {};
  std::size_t payload_idx_ = 0;
  std::size_t expected_payload_len_ = 0;
  DriverStats stats_;
};

}  // namespace camsense_lidar

#endif  // CAMSENSE_LIDAR__CAMSENSE_LIDAR_HPP_
