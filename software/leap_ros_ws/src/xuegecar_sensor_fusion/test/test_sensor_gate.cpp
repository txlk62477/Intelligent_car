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

TEST(StationaryDetectorTest, ActivatesAfterThresholdsHoldLongEnough)
{
  StationaryDetector detector({0.005, 0.01, 0.2});
  EXPECT_FALSE(detector.update(0.004, 0.009, 1.0));
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.19));
  EXPECT_TRUE(detector.update(0.0, 0.0, 1.21));
  EXPECT_TRUE(detector.update(-0.005, -0.01, 1.23));
}

TEST(StationaryDetectorTest, MotionImmediatelyReleasesConstraint)
{
  StationaryDetector detector({0.005, 0.01, 0.2});
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.0));
  EXPECT_TRUE(detector.update(0.0, 0.0, 1.21));
  EXPECT_FALSE(detector.update(0.006, 0.0, 1.22));
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.23));
}

TEST(StationaryDetectorTest, AngularMotionPreventsStationaryState)
{
  StationaryDetector detector({0.005, 0.01, 0.2});
  EXPECT_FALSE(detector.update(0.0, 0.02, 1.0));
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.1));
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.29));
  EXPECT_TRUE(detector.update(0.0, 0.0, 1.31));
}

TEST(StationaryDetectorTest, NonMonotonicTimeResetsCandidate)
{
  StationaryDetector detector({0.005, 0.01, 0.2});
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.0));
  EXPECT_FALSE(detector.update(0.0, 0.0, 0.9));
  EXPECT_FALSE(detector.update(0.0, 0.0, 1.1));
}

TEST(StationaryDetectorTest, RejectsInvalidConfiguration)
{
  EXPECT_THROW(StationaryDetector({0.0, 0.01, 0.2}), std::invalid_argument);
  EXPECT_THROW(StationaryDetector({0.005, 0.0, 0.2}), std::invalid_argument);
  EXPECT_THROW(StationaryDetector({0.005, 0.01, -0.1}), std::invalid_argument);
}

}  // namespace
}  // namespace xuegecar_sensor_fusion
