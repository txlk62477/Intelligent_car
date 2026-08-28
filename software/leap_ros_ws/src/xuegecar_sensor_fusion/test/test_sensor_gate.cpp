#include "xuegecar_sensor_fusion/sensor_gate.hpp"

#include <gtest/gtest.h>

#include <limits>
#include <stdexcept>

namespace xuegecar_sensor_fusion
{
namespace
{

ScalarGate make_gate()
{
  return ScalarGate(ScalarGateConfig{
    1.0,   // max_abs_value
    10.0,  // max_abs_rate
    0.01,  // min_rate_dt
    0.5}); // reset_rate_after_gap
}

MotionStateMachineConfig make_motion_config()
{
  return MotionStateMachineConfig{
    0.005,  // stationary vx
    0.01,   // stationary wz
    0.2,    // stationary hold
    0.15,   // turn enter wz
    0.10,   // turn exit wz
    0.1,    // enter hold
    0.2,    // exit hold
    0.05,   // arc min vx
    0.03,   // arc exit vx
  };
}

TEST(ScalarGateTest, AcceptsFiniteValuesWithinMagnitude)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kAccepted);
  EXPECT_EQ(gate.evaluate(0.1, 1.02), GateResult::kAccepted);
}

TEST(ScalarGateTest, RejectsNonFiniteValue)
{
  auto gate = make_gate();
  EXPECT_EQ(
    gate.evaluate(std::numeric_limits<double>::quiet_NaN(), 1.0),
    GateResult::kNonFinite);
}

TEST(ScalarGateTest, RejectsMagnitudeOutlier)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(1.01, 1.0), GateResult::kMagnitude);
}

TEST(ScalarGateTest, RejectsRateOutlierWithoutPoisoningBaseline)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kAccepted);
  EXPECT_EQ(gate.evaluate(0.5, 1.02), GateResult::kRate);
  EXPECT_EQ(gate.evaluate(0.1, 1.04), GateResult::kAccepted);
}

TEST(ScalarGateTest, DoesNotEstimateRateAcrossTinyNetworkBurstInterval)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kAccepted);
  EXPECT_EQ(gate.evaluate(0.5, 1.005), GateResult::kAccepted);
}

TEST(ScalarGateTest, ResetsRateCheckAfterLongGap)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kAccepted);
  EXPECT_EQ(gate.evaluate(0.8, 2.0), GateResult::kAccepted);
}

TEST(ScalarGateTest, RejectsNonMonotonicEvaluationTime)
{
  auto gate = make_gate();
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kAccepted);
  EXPECT_EQ(gate.evaluate(0.0, 1.0), GateResult::kNonMonotonicTime);
}

TEST(ScalarGateTest, RejectsInvalidConfiguration)
{
  EXPECT_THROW(
    ScalarGate(ScalarGateConfig{0.0, 10.0, 0.01, 0.5}),
    std::invalid_argument);
  EXPECT_THROW(
    ScalarGate(ScalarGateConfig{1.0, 10.0, 0.5, 0.1}),
    std::invalid_argument);
}

TEST(MotionStateMachineTest, StartsInStraightPhase)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.phase(), MotionPhase::kStraight);
}

TEST(MotionStateMachineTest, BecomesStationaryAfterHoldLongEnough)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.0, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.19), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.21), MotionPhase::kStationary);
  EXPECT_EQ(machine.update(-0.004, -0.009, 1.23), MotionPhase::kStationary);
}

TEST(MotionStateMachineTest, MotionImmediatelyLeavesStationary)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.0, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.21), MotionPhase::kStationary);
  // 离开静止是即时动作：不等 enter_hold。
  EXPECT_EQ(machine.update(0.006, 0.0, 1.22), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.23), MotionPhase::kStraight);
}

TEST(MotionStateMachineTest, StrongRotationImmediatelyLeavesStationaryToTurning)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.0, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.21), MotionPhase::kStationary);
  EXPECT_EQ(machine.update(0.0, 0.20, 1.22), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, AngularMotionPreventsStationaryState)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.02, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.1), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.29), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.0, 1.31), MotionPhase::kStationary);
}

TEST(MotionStateMachineTest, EntersTurningAfterEnterHold)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.09), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.11), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, TurningHysteresisKeepsPhaseAboveExitThreshold)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.11), MotionPhase::kTurning);
  // 0.11 仍高于退出阈值 0.10，保持转弯。
  EXPECT_EQ(machine.update(0.0, 0.11, 1.2), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, LeavesTurningAfterExitHold)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.11), MotionPhase::kTurning);
  EXPECT_EQ(machine.update(0.0, 0.09, 1.3), MotionPhase::kTurning);
  EXPECT_EQ(machine.update(0.0, 0.09, 1.49), MotionPhase::kTurning);
  EXPECT_EQ(machine.update(0.0, 0.09, 1.51), MotionPhase::kStraight);
}

TEST(MotionStateMachineTest, ArcRequiresLinearVelocity)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.06, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.06, 0.16, 1.11), MotionPhase::kArc);
}

TEST(MotionStateMachineTest, WithoutLinearVelocitySameRotationIsTurning)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.11), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, ArcToTurningUsesVxHysteresis)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.06, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.06, 0.16, 1.11), MotionPhase::kArc);
  // 0.04 仍高于弧线退出阈值 0.03，保持弧线。
  EXPECT_EQ(machine.update(0.04, 0.16, 1.2), MotionPhase::kArc);
  // 0.02 低于退出阈值，持续 enter_hold 后回到原地转弯。
  EXPECT_EQ(machine.update(0.02, 0.16, 1.3), MotionPhase::kArc);
  EXPECT_EQ(machine.update(0.02, 0.16, 1.41), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, TurningToArcRequiresVxThresholdAndHold)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 1.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.11), MotionPhase::kTurning);
  EXPECT_EQ(machine.update(0.06, 0.16, 1.2), MotionPhase::kTurning);
  EXPECT_EQ(machine.update(0.06, 0.16, 1.31), MotionPhase::kArc);
}

TEST(MotionStateMachineTest, NonMonotonicTimeResetsCandidate)
{
  MotionStateMachine machine(make_motion_config());
  EXPECT_EQ(machine.update(0.0, 0.16, 2.0), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 2.05), MotionPhase::kStraight);
  // 时间回退：候选清零，不允许用陈旧候选直接进入转弯。
  EXPECT_EQ(machine.update(0.0, 0.16, 1.5), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.6), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.65), MotionPhase::kStraight);
  EXPECT_EQ(machine.update(0.0, 0.16, 1.71), MotionPhase::kTurning);
}

TEST(MotionStateMachineTest, RejectsInvalidConfiguration)
{
  const auto valid = make_motion_config();
  {
    auto bad = valid;
    bad.stationary_max_abs_linear_velocity = 0.0;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.stationary_max_abs_angular_velocity = 0.0;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.stationary_hold_duration = -0.1;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.turn_enter_angular_velocity_threshold = 0.0;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.turn_exit_angular_velocity_threshold = -0.1;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.turn_exit_angular_velocity_threshold = 0.16;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.enter_hold_duration = -0.1;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.exit_hold_duration = -0.1;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.arc_min_abs_linear_velocity = 0.0;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.arc_exit_abs_linear_velocity = -0.1;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
  {
    auto bad = valid;
    bad.arc_exit_abs_linear_velocity = 0.06;
    EXPECT_THROW(MotionStateMachine{bad}, std::invalid_argument);
  }
}

}  // namespace
}  // namespace xuegecar_sensor_fusion
