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

enum class MotionPhase : std::uint8_t
{
  kStationary,
  kStraight,
  kTurning,
  kArc,
};

struct MotionStateMachineConfig
{
  // 静止判定：两个速度都低于阈值并持续 hold 时长才进入静止。
  double stationary_max_abs_linear_velocity{0.0};
  double stationary_max_abs_angular_velocity{0.0};
  double stationary_hold_duration{0.0};
  // 转弯带判定：|wz| 进入/退出阈值不同（迟滞），各自需要持续时长。
  double turn_enter_angular_velocity_threshold{0.0};
  double turn_exit_angular_velocity_threshold{0.0};
  double enter_hold_duration{0.0};
  double exit_hold_duration{0.0};
  // 弧线与原地转弯的区别：|vx| 是否超过阈值（同样带迟滞）。
  double arc_min_abs_linear_velocity{0.0};
  double arc_exit_abs_linear_velocity{0.0};
};

class MotionStateMachine
{
public:
  explicit MotionStateMachine(const MotionStateMachineConfig & config);

  MotionPhase update(
    double linear_velocity, double angular_velocity, double timestamp_seconds);
  MotionPhase phase() const;
  void reset();

private:
  MotionPhase classify(double linear_velocity, double angular_velocity) const;
  double hold_duration_for(MotionPhase from, MotionPhase to) const;

  MotionStateMachineConfig config_;
  MotionPhase phase_{MotionPhase::kStraight};
  bool candidate_{false};
  MotionPhase candidate_target_{MotionPhase::kStraight};
  double candidate_start_time_{0.0};
};

}  // namespace xuegecar_sensor_fusion

#endif  // XUEGECAR_SENSOR_FUSION__SENSOR_GATE_HPP_
