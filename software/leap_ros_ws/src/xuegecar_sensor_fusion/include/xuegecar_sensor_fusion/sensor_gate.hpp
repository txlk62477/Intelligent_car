#ifndef XUEGECAR_SENSOR_FUSION__SENSOR_GATE_HPP_
#define XUEGECAR_SENSOR_FUSION__SENSOR_GATE_HPP_

#include <cstdint>

namespace xuegecar_sensor_fusion
{

enum class GateResult : std::uint8_t
{
  kAccepted,
  kNonFinite,
  kMagnitude,
  kNonMonotonicTime,
  kRate,
};

struct ScalarGateConfig
{
  double max_abs_value{0.0};
  double max_abs_rate{0.0};
  double min_rate_dt{0.0};
  double reset_rate_after_gap{0.0};
};

class ScalarGate
{
public:
  explicit ScalarGate(const ScalarGateConfig & config);

  GateResult evaluate(double value, double timestamp_seconds);
  void reset();

private:
  ScalarGateConfig config_;
  bool has_previous_{false};
  double previous_value_{0.0};
  double previous_time_{0.0};
};

struct StationaryDetectorConfig
{
  double max_abs_linear_velocity{0.0};
  double max_abs_angular_velocity{0.0};
  double hold_duration{0.0};
};

class StationaryDetector
{
public:
  explicit StationaryDetector(const StationaryDetectorConfig & config);

  bool update(double linear_velocity, double angular_velocity, double timestamp_seconds);
  void reset();

private:
  StationaryDetectorConfig config_;
  bool candidate_{false};
  bool stationary_{false};
  double candidate_start_time_{0.0};
  double previous_time_{0.0};
};

}  // namespace xuegecar_sensor_fusion

#endif  // XUEGECAR_SENSOR_FUSION__SENSOR_GATE_HPP_
