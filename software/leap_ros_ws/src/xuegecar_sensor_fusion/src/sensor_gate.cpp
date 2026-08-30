#include "xuegecar_sensor_fusion/sensor_gate.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

namespace xuegecar_sensor_fusion
{

TimeMapper::TimeMapper(const TimeMapperConfig & config)
: config_(config)
{
  if (!std::isfinite(config_.max_mapping_error_seconds) ||
    config_.max_mapping_error_seconds <= 0.0)
  {
    throw std::invalid_argument("max_mapping_error_seconds must be finite and positive");
  }
  if (!std::isfinite(config_.reset_after_gap_seconds) ||
    config_.reset_after_gap_seconds <= 0.0)
  {
    throw std::invalid_argument("reset_after_gap_seconds must be finite and positive");
  }
}

TimeMapOutput TimeMapper::map(
  const std::int64_t source_nanoseconds,
  const std::int64_t ros_now_nanoseconds,
  const double raw_receipt_seconds)
{
  if (source_nanoseconds <= 0 || ros_now_nanoseconds <= 0 ||
    !std::isfinite(raw_receipt_seconds))
  {
    return {TimeMapResult::kInvalid, 0};
  }

  if (!has_previous_) {
    has_previous_ = true;
    previous_source_nanoseconds_ = source_nanoseconds;
    previous_mapped_nanoseconds_ = ros_now_nanoseconds;
    previous_accepted_receipt_seconds_ = raw_receipt_seconds;
    return {TimeMapResult::kMapped, ros_now_nanoseconds};
  }

  const double receipt_gap =
    raw_receipt_seconds - previous_accepted_receipt_seconds_;
  if (receipt_gap <= 0.0) {
    return {TimeMapResult::kInvalid, 0};
  }
  if (receipt_gap > config_.reset_after_gap_seconds) {
    return reinitialize(source_nanoseconds, ros_now_nanoseconds, raw_receipt_seconds);
  }
  if (source_nanoseconds <= previous_source_nanoseconds_) {
    return {TimeMapResult::kNonMonotonic, 0};
  }

  const std::int64_t source_delta = source_nanoseconds - previous_source_nanoseconds_;
  if (source_delta >
    std::numeric_limits<std::int64_t>::max() - previous_mapped_nanoseconds_)
  {
    return {TimeMapResult::kInvalid, 0};
  }
  const std::int64_t projected = previous_mapped_nanoseconds_ + source_delta;
  const auto max_mapping_error_nanoseconds = static_cast<std::int64_t>(
    config_.max_mapping_error_seconds * 1.0e9);
  const bool mapping_error_exceeded =
    std::abs(ros_now_nanoseconds - projected) > max_mapping_error_nanoseconds;

  previous_source_nanoseconds_ = source_nanoseconds;
  previous_mapped_nanoseconds_ = projected;
  previous_accepted_receipt_seconds_ = raw_receipt_seconds;
  return {TimeMapResult::kMapped, projected, mapping_error_exceeded};
}

TimeMapOutput TimeMapper::reinitialize(
  const std::int64_t source_nanoseconds,
  const std::int64_t ros_now_nanoseconds,
  const double raw_receipt_seconds)
{
  has_previous_ = true;
  previous_source_nanoseconds_ = source_nanoseconds;
  previous_mapped_nanoseconds_ = ros_now_nanoseconds;
  previous_accepted_receipt_seconds_ = raw_receipt_seconds;
  return {TimeMapResult::kReinitialized, ros_now_nanoseconds};
}

void TimeMapper::reset()
{
  has_previous_ = false;
  previous_source_nanoseconds_ = 0;
  previous_mapped_nanoseconds_ = 0;
  previous_accepted_receipt_seconds_ = 0.0;
}

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

MotionStateMachine::MotionStateMachine(const MotionStateMachineConfig & config)
: config_(config)
{
  if (!std::isfinite(config_.stationary_max_abs_linear_velocity) ||
    config_.stationary_max_abs_linear_velocity <= 0.0)
  {
    throw std::invalid_argument(
            "stationary_max_abs_linear_velocity must be finite and positive");
  }
  if (!std::isfinite(config_.stationary_max_abs_angular_velocity) ||
    config_.stationary_max_abs_angular_velocity <= 0.0)
  {
    throw std::invalid_argument(
            "stationary_max_abs_angular_velocity must be finite and positive");
  }
  if (!std::isfinite(config_.stationary_hold_duration) ||
    config_.stationary_hold_duration < 0.0)
  {
    throw std::invalid_argument("stationary_hold_duration must be finite and non-negative");
  }
  if (!std::isfinite(config_.turn_enter_angular_velocity_threshold) ||
    config_.turn_enter_angular_velocity_threshold <= 0.0)
  {
    throw std::invalid_argument(
            "turn_enter_angular_velocity_threshold must be finite and positive");
  }
  if (!std::isfinite(config_.turn_exit_angular_velocity_threshold) ||
    config_.turn_exit_angular_velocity_threshold < 0.0 ||
    config_.turn_exit_angular_velocity_threshold > config_.turn_enter_angular_velocity_threshold)
  {
    throw std::invalid_argument(
            "turn_exit_angular_velocity_threshold must be finite, non-negative "
            "and not exceed the enter threshold");
  }
  if (!std::isfinite(config_.enter_hold_duration) || config_.enter_hold_duration < 0.0) {
    throw std::invalid_argument("enter_hold_duration must be finite and non-negative");
  }
  if (!std::isfinite(config_.exit_hold_duration) || config_.exit_hold_duration < 0.0) {
    throw std::invalid_argument("exit_hold_duration must be finite and non-negative");
  }
  if (!std::isfinite(config_.arc_min_abs_linear_velocity) ||
    config_.arc_min_abs_linear_velocity <= 0.0)
  {
    throw std::invalid_argument("arc_min_abs_linear_velocity must be finite and positive");
  }
  if (!std::isfinite(config_.arc_exit_abs_linear_velocity) ||
    config_.arc_exit_abs_linear_velocity < 0.0 ||
    config_.arc_exit_abs_linear_velocity > config_.arc_min_abs_linear_velocity)
  {
    throw std::invalid_argument(
            "arc_exit_abs_linear_velocity must be finite, non-negative "
            "and not exceed arc_min_abs_linear_velocity");
  }
}

MotionPhase MotionStateMachine::phase() const
{
  return phase_;
}

void MotionStateMachine::reset()
{
  candidate_ = false;
  phase_ = MotionPhase::kStraight;
  candidate_start_time_ = 0.0;
}

MotionPhase MotionStateMachine::update(
  const double linear_velocity,
  const double angular_velocity,
  const double timestamp_seconds)
{
  if (!std::isfinite(linear_velocity) || !std::isfinite(angular_velocity) ||
    !std::isfinite(timestamp_seconds) ||
    (candidate_ && timestamp_seconds <= candidate_start_time_))
  {
    reset();
    return phase_;
  }

  const MotionPhase target = classify(linear_velocity, angular_velocity);
  if (target == phase_) {
    candidate_ = false;
    return phase_;
  }

  // 离开静止是即时动作：运动一出现立即解除零化约束，避免吃掉起步角速度。
  if (phase_ == MotionPhase::kStationary) {
    phase_ = target;
    candidate_ = false;
    return phase_;
  }

  if (!candidate_ || candidate_target_ != target) {
    candidate_ = true;
    candidate_target_ = target;
    candidate_start_time_ = timestamp_seconds;
    return phase_;
  }
  if (timestamp_seconds - candidate_start_time_ >=
    hold_duration_for(phase_, target))
  {
    phase_ = target;
    candidate_ = false;
  }
  return phase_;
}

MotionPhase MotionStateMachine::classify(
  const double linear_velocity,
  const double angular_velocity) const
{
  const double abs_vx = std::abs(linear_velocity);
  const double abs_wz = std::abs(angular_velocity);

  if (abs_vx <= config_.stationary_max_abs_linear_velocity &&
    abs_wz <= config_.stationary_max_abs_angular_velocity)
  {
    return MotionPhase::kStationary;
  }

  const bool in_turn_band =
    phase_ == MotionPhase::kTurning || phase_ == MotionPhase::kArc;
  const double wz_threshold = in_turn_band ?
    config_.turn_exit_angular_velocity_threshold :
    config_.turn_enter_angular_velocity_threshold;

  if (abs_wz >= wz_threshold) {
    const bool in_arc = phase_ == MotionPhase::kArc;
    const double vx_threshold = in_arc ?
      config_.arc_exit_abs_linear_velocity :
      config_.arc_min_abs_linear_velocity;
    return abs_vx >= vx_threshold ? MotionPhase::kArc : MotionPhase::kTurning;
  }
  return MotionPhase::kStraight;
}

double MotionStateMachine::hold_duration_for(
  const MotionPhase from,
  const MotionPhase to) const
{
  (void)from;
  if (to == MotionPhase::kStationary) {
    return config_.stationary_hold_duration;
  }
  if (to == MotionPhase::kStraight) {
    return config_.exit_hold_duration;
  }
  return config_.enter_hold_duration;
}

}  // namespace xuegecar_sensor_fusion
