#ifndef XUEGECAR_SENSOR_FUSION__RAW_MONOTONIC_TIME_HPP_
#define XUEGECAR_SENSOR_FUSION__RAW_MONOTONIC_TIME_HPP_

#include <cerrno>
#include <cstdint>
#include <ctime>
#include <system_error>

namespace xuegecar_sensor_fusion
{

inline std::int64_t raw_monotonic_now_nanoseconds()
{
  timespec value{};
  if (clock_gettime(CLOCK_MONOTONIC_RAW, &value) != 0) {
    throw std::system_error(
            errno, std::generic_category(), "clock_gettime(CLOCK_MONOTONIC_RAW)");
  }
  return static_cast<std::int64_t>(value.tv_sec) * 1000000000LL +
         static_cast<std::int64_t>(value.tv_nsec);
}

inline double raw_monotonic_now_seconds()
{
  return static_cast<double>(raw_monotonic_now_nanoseconds()) * 1.0e-9;
}

}  // namespace xuegecar_sensor_fusion

#endif  // XUEGECAR_SENSOR_FUSION__RAW_MONOTONIC_TIME_HPP_
