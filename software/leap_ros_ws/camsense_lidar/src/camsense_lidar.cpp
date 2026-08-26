#include "camsense_lidar/camsense_lidar.hpp"

#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#include <cerrno>
#include <stdexcept>
#include <string>
#include <utility>

namespace camsense_lidar
{
namespace
{

speed_t baudToConstant(const int baud_rate)
{
  switch (baud_rate) {
    case 9600:
      return B9600;
    case 19200:
      return B19200;
    case 38400:
      return B38400;
    case 57600:
      return B57600;
    case 115200:
      return B115200;
    case 230400:
      return B230400;
    case 460800:
      return B460800;
    case 921600:
      return B921600;
    default:
      throw std::runtime_error("unsupported baud rate: " + std::to_string(baud_rate));
  }
}

}  // namespace

CamsenseLidar::CamsenseLidar(
  std::string port, const int baud_rate,
  const float center_base_angle_deg)
: port_(std::move(port)),
  baud_rate_(baud_rate),
  center_base_angle_deg_(center_base_angle_deg)
{
  current_scan_.reserve(kMaxLidarPointsPerScan);
}

CamsenseLidar::~CamsenseLidar()
{
  close();
}

bool CamsenseLidar::open()
{
  if (isOpen()) {
    return true;
  }

  fd_ = ::open(port_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
  if (fd_ < 0) {
    return false;
  }

  try {
    if (!configureSerial()) {
      close();
      return false;
    }
  } catch (const std::runtime_error &) {
    close();
    return false;
  }

  state_ = 0;
  payload_idx_ = 0;
  expected_payload_len_ = 0;
  current_scan_.clear();
  return true;
}

void CamsenseLidar::close()
{
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
}

bool CamsenseLidar::isOpen() const
{
  return fd_ >= 0;
}

bool CamsenseLidar::configureSerial()
{
  termios tty {};
  if (tcgetattr(fd_, &tty) != 0) {
    return false;
  }

  cfmakeraw(&tty);
  const speed_t baud = baudToConstant(baud_rate_);
  cfsetispeed(&tty, baud);
  cfsetospeed(&tty, baud);

  tty.c_cflag |= (CLOCAL | CREAD);
  tty.c_cflag &= ~CSIZE;
  tty.c_cflag |= CS8;
  tty.c_cflag &= ~PARENB;
  tty.c_cflag &= ~CSTOPB;
  tty.c_cflag &= ~CRTSCTS;
  tty.c_cc[VMIN] = 0;
  tty.c_cc[VTIME] = 0;

  if (tcsetattr(fd_, TCSANOW, &tty) != 0) {
    return false;
  }

  tcflush(fd_, TCIOFLUSH);
  return true;
}

bool CamsenseLidar::poll(std::vector<LidarPoint> & out_scan)
{
  out_scan.clear();
  if (!isOpen()) {
    return false;
  }

  uint8_t rx_buf[512];
  const ssize_t read_len = ::read(fd_, rx_buf, sizeof(rx_buf));
  if (read_len < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      return false;
    }
    close();
    return false;
  }
  if (read_len == 0) {
    return false;
  }
  stats_.bytes_read += static_cast<uint64_t>(read_len);

  bool circle_completed = false;
  for (ssize_t i = 0; i < read_len; ++i) {
    if (processByte(rx_buf[i], out_scan)) {
      circle_completed = true;
    }
  }

  return circle_completed;
}

DriverStats CamsenseLidar::getStats() const
{
  DriverStats stats = stats_;
  stats.current_scan_points = current_scan_.size();
  return stats;
}

bool CamsenseLidar::processByte(const uint8_t rx_byte, std::vector<LidarPoint> & out_scan)
{
  bool circle_completed = false;

  if (state_ == 0) {
    if (rx_byte == 0x55) {
      state_ = 1;
    }
  } else if (state_ == 1) {
    if (rx_byte == 0xAA) {
      state_ = 2;
    } else if (rx_byte == 0x55) {
      state_ = 1;
    } else {
      state_ = 0;
    }
  } else if (state_ == 2) {
    c_info_ = rx_byte;
    state_ = 3;
  } else if (state_ == 3) {
    packet_point_count_ = rx_byte;
    expected_payload_len_ = static_cast<std::size_t>(packet_point_count_) * c_info_ + 8U;

    if (expected_payload_len_ > kMaxLidarPacketSize || c_info_ < 2 || c_info_ > 3 ||
      packet_point_count_ == 0 || packet_point_count_ > 60)
    {
      ++stats_.invalid_headers;
      state_ = 0;
    } else {
      payload_idx_ = 0;
      state_ = 4;
    }
  } else if (state_ == 4) {
    payload_buffer_[payload_idx_++] = rx_byte;

    if (payload_idx_ >= expected_payload_len_) {
      const std::vector<LidarPoint> packet_points =
        parsePacket(payload_buffer_, c_info_, packet_point_count_);
      ++stats_.packets;
      stats_.valid_points += packet_points.size();
      stats_.invalid_points += packet_point_count_ - packet_points.size();

      for (const auto & point : packet_points) {
        if (point.angle_deg < last_angle_deg_ - 100.0F && current_scan_.size() > 50) {
          out_scan = current_scan_;
          current_scan_.clear();
          ++stats_.completed_scans;
          circle_completed = true;
        }

        if (current_scan_.size() < kMaxLidarPointsPerScan) {
          current_scan_.push_back(point);
        }

        last_angle_deg_ = point.angle_deg;
      }
      state_ = 0;
    }
  }

  return circle_completed;
}

std::vector<LidarPoint> CamsenseLidar::parsePacket(
  const uint8_t * buffer, const uint8_t c_info, const uint8_t point_count)
{
  std::vector<LidarPoint> packet_points;
  packet_points.reserve(point_count);

  float first_angle = (static_cast<float>(buffer[3]) - 160.0F +
    static_cast<float>(buffer[2]) / 256.0F) * 4.0F;
  const int last_angle_start = 4 + point_count * c_info;
  float last_angle = (static_cast<float>(buffer[last_angle_start + 1]) - 160.0F +
    static_cast<float>(buffer[last_angle_start]) / 256.0F) * 4.0F;

  if (last_angle < first_angle) {
    last_angle += 360.0F;
  }
  const float angle_step = point_count > 1 ?
    (last_angle - first_angle) / static_cast<float>(point_count - 1) : 0.0F;

  for (uint8_t i = 0; i < point_count; ++i) {
    const int idx = 4 + i * c_info;
    const uint16_t distance =
      static_cast<uint16_t>(((buffer[idx + 1] & 0x3F) << 8) | buffer[idx]);
    uint8_t invalid_flag = (buffer[idx + 1] >> 7) & 0x01;

    if (distance == 0) {
      invalid_flag = 1;
    }
    if (invalid_flag != 0) {
      continue;
    }

    float angle = first_angle + angle_step * static_cast<float>(i) + center_base_angle_deg_;
    while (angle < 0.0F) {
      angle += 360.0F;
    }
    while (angle >= 360.0F) {
      angle -= 360.0F;
    }

    packet_points.push_back({angle, static_cast<float>(distance)});
  }

  return packet_points;
}

}  // namespace camsense_lidar
