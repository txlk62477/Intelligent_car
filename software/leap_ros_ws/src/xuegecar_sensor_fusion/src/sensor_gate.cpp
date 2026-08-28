#include "xuegecar_sensor_fusion/sensor_gate.hpp"

#include <cmath>
#include <stdexcept>

namespace xuegecar_sensor_fusion
{

ScalarGate::ScalarGate(const ScalarGateConfig & config)
: config_(config)
{
  if (!std::isfinite(config_.max_abs_value) || config_.max_abs_value <= 0.0) {
    throw std::invalid_argument("max_abs_value must be finite and positive");
  }
  if (!std::isfinite(config_.max_abs_rate) || config_.max_abs_rate < 0.0) {
    throw std::invalid_argument("max_abs_rate must be finite and non-negative");
  }
  if (!std::isfinite(config_.min_rate_dt) || config_.min_rate_dt < 0.0) {
    throw std::invalid_argument("min_rate_dt must be finite and non-negative");
  }
  if (!std::isfinite(config_.reset_rate_after_gap) ||
    config_.reset_rate_after_gap < config_.min_rate_dt)
  {
    throw std::invalid_argument("reset_rate_after_gap must not be less than min_rate_dt");
  }
}

GateResult ScalarGate::evaluate(const double value, const double timestamp_seconds)
{
  if (!std::isfinite(value) || !std::isfinite(timestamp_seconds)) {
    return GateResult::kNonFinite;
  }
  if (std::abs(value) > config_.max_abs_value) {
    return GateResult::kMagnitude;
  }

  if (has_previous_) {
    const double dt = timestamp_seconds - previous_time_;
    if (dt <= 0.0) {
      return GateResult::kNonMonotonicTime;
    }
    if (config_.max_abs_rate > 0.0 && dt >= config_.min_rate_dt &&
      dt <= config_.reset_rate_after_gap &&
      std::abs(value - previous_value_) / dt > config_.max_abs_rate)
    {
      return GateResult::kRate;
    }
  }

  has_previous_ = true;
  previous_value_ = value;
  previous_time_ = timestamp_seconds;
  return GateResult::kAccepted;
}

void ScalarGate::reset()
{
  has_previous_ = false;
  previous_value_ = 0.0;
  previous_time_ = 0.0;
}

StationaryDetector::StationaryDetector(const StationaryDetectorConfig & config)
: config_(config)
{
  if (!std::isfinite(config_.max_abs_linear_velocity) ||
    config_.max_abs_linear_velocity <= 0.0)
  {
    throw std::invalid_argument(
            "max_abs_linear_velocity must be finite and positive");
  }
  if (!std::isfinite(config_.max_abs_angular_velocity) ||
    config_.max_abs_angular_velocity <= 0.0)
  {
    throw std::invalid_argument(
            "max_abs_angular_velocity must be finite and positive");
  }
  if (!std::isfinite(config_.hold_duration) || config_.hold_duration < 0.0) {
    throw std::invalid_argument("hold_duration must be finite and non-negative");
  }
}

bool StationaryDetector::update(
  const double linear_velocity,
  const double angular_velocity,
  const double timestamp_seconds)
{
  if (!std::isfinite(linear_velocity) || !std::isfinite(angular_velocity) ||
    !std::isfinite(timestamp_seconds) ||
    (candidate_ && timestamp_seconds <= previous_time_))
  {
    reset();
    return false;
  }

  const bool below_thresholds =
    std::abs(linear_velocity) <= config_.max_abs_linear_velocity &&
    std::abs(angular_velocity) <= config_.max_abs_angular_velocity;
  if (!below_thresholds) {
    reset();
    return false;
  }

  if (!candidate_) {
    candidate_ = true;
    candidate_start_time_ = timestamp_seconds;
  }
  previous_time_ = timestamp_seconds;
  stationary_ = timestamp_seconds - candidate_start_time_ >= config_.hold_duration;
  return stationary_;
}

void StationaryDetector::reset()
{
  candidate_ = false;
  stationary_ = false;
  candidate_start_time_ = 0.0;
  previous_time_ = 0.0;
}

}  // namespace xuegecar_sensor_fusion
