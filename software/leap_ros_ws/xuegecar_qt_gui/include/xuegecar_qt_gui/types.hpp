#pragma once

#include <QImage>
#include <QPointF>

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace xuegecar_qt_gui
{

struct Pose2D
{
  double x{0.0};
  double y{0.0};
  double yaw{0.0};
  std::string frame_id;
  bool valid{false};
};

struct MapLayer
{
  QImage image;
  int width{0};
  int height{0};
  double resolution{0.05};
  double origin_x{0.0};
  double origin_y{0.0};
  double origin_yaw{0.0};
  std::string frame_id{"map"};
};

struct PathLayer
{
  std::vector<QPointF> points;
  std::string frame_id{"map"};
};

struct BatteryInfo
{
  double voltage{0.0};
  double percent{-1.0};
  bool has_voltage{false};
  bool has_percent{false};
};

struct TopicAges
{
  double map{-1.0};
  double scan{-1.0};
  double camera{-1.0};
  double odom{-1.0};
  double tf{-1.0};
  double battery{-1.0};
};

struct AppState
{
  QImage camera_image;
  MapLayer map;
  bool has_map{false};
  std::vector<QPointF> scan_points;
  double scan_range_max{1.0};
  Pose2D robot_pose;
  double odom_linear{0.0};
  double odom_angular{0.0};
  double cmd_linear{0.0};
  double cmd_angular{0.0};
  BatteryInfo battery;
  std::map<std::string, PathLayer> paths;
  std::map<std::string, MapLayer> costmaps;
  TopicAges ages;
};

}  // namespace xuegecar_qt_gui
