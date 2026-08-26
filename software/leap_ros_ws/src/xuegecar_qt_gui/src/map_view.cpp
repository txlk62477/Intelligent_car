#include "xuegecar_qt_gui/map_view.hpp"

#include <QMouseEvent>
#include <QPainter>
#include <QWheelEvent>

#include <algorithm>
#include <cmath>

namespace xuegecar_qt_gui
{

MapView::MapView(QWidget * parent)
: QWidget(parent)
{
  setMouseTracking(true);
  setMinimumSize(520, 360);
  setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
}

void MapView::setState(const AppState & state)
{
  const auto map_changed = state.has_map && state.map.image.cacheKey() != state_.map.image.cacheKey();
  state_ = state;
  if (map_changed && !user_view_) {
    fitToMap();
  }
  update();
}

void MapView::fitToMap()
{
  if (!state_.has_map || state_.map.image.isNull()) {
    return;
  }
  const double margin = 18.0;
  const double sx = (width() - 2.0 * margin) / std::max(1, state_.map.image.width());
  const double sy = (height() - 2.0 * margin) / std::max(1, state_.map.image.height());
  scale_ = std::max(0.05, std::min(sx, sy));
  offset_ = QPointF(
    (width() - state_.map.image.width() * scale_) * 0.5,
    (height() - state_.map.image.height() * scale_) * 0.5);
  user_view_ = false;
}

void MapView::setInitialPoseMode(bool enabled)
{
  initial_pose_mode_ = enabled;
  update();
}

void MapView::paintEvent(QPaintEvent *)
{
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing, true);
  painter.fillRect(rect(), QColor(255, 255, 255));

  if (!state_.has_map || state_.map.image.isNull()) {
    painter.setPen(QColor(31, 111, 232));
    painter.setFont(QFont("Sans Serif", 44, QFont::Bold));
    painter.drawText(rect().adjusted(0, -70, 0, 0), Qt::AlignCenter, "⌖");
    painter.setPen(QColor(55, 65, 81));
    painter.setFont(QFont("Sans Serif", 15));
    painter.drawText(rect().adjusted(0, 45, 0, 0), Qt::AlignCenter, "等待 /map 地图数据");
    return;
  }

  const QRectF target(offset_.x(), offset_.y(), state_.map.image.width() * scale_, state_.map.image.height() * scale_);
  painter.drawImage(target, state_.map.image);
  painter.setPen(QPen(QColor(80, 86, 92), 1));
  painter.drawRect(target);

  drawCostmaps(painter);
  drawScan(painter);
  drawPaths(painter);
  drawRobot(painter);

  if (!pose_start_.isNull() || !pose_current_.isNull()) {
    painter.setPen(QPen(initial_pose_mode_ ? QColor(255, 215, 70) : QColor(75, 190, 100), 3));
    painter.drawLine(imageToScreen(pose_start_), imageToScreen(pose_current_));
  }

  if (initial_pose_mode_) {
    painter.setPen(QColor(31, 111, 232));
    painter.setFont(QFont("Sans Serif", 10, QFont::Bold));
    painter.drawText(12, 22, "初始位姿模式");
  }
}

void MapView::resizeEvent(QResizeEvent * event)
{
  if (!user_view_) {
    fitToMap();
  }
  QWidget::resizeEvent(event);
}

void MapView::wheelEvent(QWheelEvent * event)
{
  if (!state_.has_map) {
    return;
  }
  const auto old_image = screenToImage(event->position());
  const double factor = event->angleDelta().y() > 0 ? 1.15 : 1.0 / 1.15;
  scale_ = std::clamp(scale_ * factor, 0.02, 90.0);
  offset_ = QPointF(event->position().x() - old_image.x() * scale_, event->position().y() - old_image.y() * scale_);
  user_view_ = true;
  update();
}

void MapView::mousePressEvent(QMouseEvent * event)
{
  if (!state_.has_map) {
    return;
  }
  if (event->button() == Qt::LeftButton) {
    drag_mode_ = "pose";
    pose_start_ = screenToImage(event->pos());
    pose_current_ = pose_start_;
  } else if (event->button() == Qt::RightButton || event->button() == Qt::MiddleButton) {
    drag_mode_ = "pan";
    drag_start_ = event->pos();
    drag_offset_ = offset_;
    setCursor(Qt::ClosedHandCursor);
  }
}

void MapView::mouseMoveEvent(QMouseEvent * event)
{
  if (drag_mode_ == "pan") {
    offset_ = drag_offset_ + (event->pos() - drag_start_);
    user_view_ = true;
    update();
  } else if (drag_mode_ == "pose") {
    pose_current_ = screenToImage(event->pos());
    update();
  }
}

void MapView::mouseReleaseEvent(QMouseEvent * event)
{
  if (drag_mode_ == "pose" && event->button() == Qt::LeftButton) {
    pose_current_ = screenToImage(event->pos());
    const auto start = imageToWorld(pose_start_);
    const auto end = imageToWorld(pose_current_);
    const double yaw = std::atan2(end.y() - start.y(), end.x() - start.x());
    if (initial_pose_mode_) {
      emit initialPoseSelected(start.x(), start.y(), yaw);
      initial_pose_mode_ = false;
    } else {
      emit goalSelected(start.x(), start.y(), yaw);
    }
    pose_start_ = QPointF();
    pose_current_ = QPointF();
    drag_mode_.clear();
    update();
  } else if (drag_mode_ == "pan") {
    drag_mode_.clear();
    unsetCursor();
  }
}

QPointF MapView::worldToImage(double x, double y, const MapLayer & layer) const
{
  const double dx = x - layer.origin_x;
  const double dy = y - layer.origin_y;
  const double c = std::cos(-layer.origin_yaw);
  const double s = std::sin(-layer.origin_yaw);
  const double local_x = c * dx - s * dy;
  const double local_y = s * dx + c * dy;
  return QPointF(local_x / layer.resolution, layer.height - local_y / layer.resolution);
}

QPointF MapView::imageToWorld(const QPointF & point) const
{
  const auto & layer = state_.map;
  const double local_x = point.x() * layer.resolution;
  const double local_y = (layer.height - point.y()) * layer.resolution;
  const double c = std::cos(layer.origin_yaw);
  const double s = std::sin(layer.origin_yaw);
  return QPointF(layer.origin_x + c * local_x - s * local_y, layer.origin_y + s * local_x + c * local_y);
}

QPointF MapView::worldToScreen(double x, double y) const
{
  return imageToScreen(worldToImage(x, y, state_.map));
}

QPointF MapView::imageToScreen(const QPointF & point) const
{
  return QPointF(offset_.x() + point.x() * scale_, offset_.y() + point.y() * scale_);
}

QPointF MapView::screenToImage(const QPointF & point) const
{
  return QPointF((point.x() - offset_.x()) / scale_, (point.y() - offset_.y()) / scale_);
}

void MapView::drawCostmaps(QPainter & painter)
{
  const QStringList order = {"全局代价层", "全局膨胀层", "局部代价层", "局部膨胀层"};
  for (const auto & label : order) {
    const auto it = state_.costmaps.find(label.toStdString());
    if (it == state_.costmaps.end() || it->second.image.isNull()) {
      continue;
    }
    const auto & layer = it->second;
    if (layer.frame_id != state_.map.frame_id) {
      continue;
    }
    const double c = std::cos(layer.origin_yaw);
    const double s = std::sin(layer.origin_yaw);
    QVector<QPointF> corners;
    for (const auto & p : {QPointF(0, 0), QPointF(layer.width * layer.resolution, 0),
        QPointF(0, layer.height * layer.resolution), QPointF(layer.width * layer.resolution, layer.height * layer.resolution)}) {
      const double wx = layer.origin_x + c * p.x() - s * p.y();
      const double wy = layer.origin_y + s * p.x() + c * p.y();
      corners.push_back(worldToScreen(wx, wy));
    }
    double min_x = corners[0].x(), max_x = corners[0].x(), min_y = corners[0].y(), max_y = corners[0].y();
    for (const auto & corner : corners) {
      min_x = std::min(min_x, corner.x());
      max_x = std::max(max_x, corner.x());
      min_y = std::min(min_y, corner.y());
      max_y = std::max(max_y, corner.y());
    }
    painter.drawImage(QRectF(min_x, min_y, max_x - min_x, max_y - min_y), layer.image);
  }
}

void MapView::drawScan(QPainter & painter)
{
  if (!state_.robot_pose.valid || state_.scan_points.empty()) {
    return;
  }
  QPolygonF points;
  points.reserve(static_cast<int>(state_.scan_points.size()));
  const double c = std::cos(state_.robot_pose.yaw);
  const double s = std::sin(state_.robot_pose.yaw);
  for (const auto & p : state_.scan_points) {
    const double wx = state_.robot_pose.x + c * p.x() - s * p.y();
    const double wy = state_.robot_pose.y + s * p.x() + c * p.y();
    points << worldToScreen(wx, wy);
  }
  painter.setPen(QPen(QColor(0, 190, 225, 185), 2));
  painter.drawPoints(points);
}

void MapView::drawPaths(QPainter & painter)
{
  const std::map<std::string, QColor> colors = {
    {"全局路径", QColor(70, 145, 255)},
    {"局部路径", QColor(255, 215, 70)},
    {"控制路径", QColor(190, 115, 255)},
  };
  for (const auto & [label, path] : state_.paths) {
    if (path.points.size() < 2 || path.frame_id != state_.map.frame_id) {
      continue;
    }
    QPolygonF line;
    for (const auto & p : path.points) {
      line << worldToScreen(p.x(), p.y());
    }
    const auto color = colors.count(label) ? colors.at(label) : QColor(80, 160, 255);
    painter.setPen(QPen(QColor(0, 0, 0, 160), 6, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    painter.drawPolyline(line);
    painter.setPen(QPen(color, 3, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    painter.drawPolyline(line);
  }
}

void MapView::drawRobot(QPainter & painter)
{
  if (!state_.robot_pose.valid) {
    return;
  }
  drawPoseArrow(painter, state_.robot_pose, QColor(230, 76, 70), "机器人");
}

void MapView::drawPoseArrow(QPainter & painter, const Pose2D & pose, const QColor & color, const QString & label)
{
  const auto start = worldToScreen(pose.x, pose.y);
  const auto end = worldToScreen(pose.x + 0.48 * std::cos(pose.yaw), pose.y + 0.48 * std::sin(pose.yaw));
  painter.setPen(QPen(QColor(0, 0, 0, 180), 6, Qt::SolidLine, Qt::RoundCap));
  painter.drawLine(start, end);
  painter.setPen(QPen(color, 3, Qt::SolidLine, Qt::RoundCap));
  painter.setBrush(color);
  painter.drawLine(start, end);
  painter.drawEllipse(start, 6, 6);
  painter.drawText(start + QPointF(8, -8), label);
}

}  // namespace xuegecar_qt_gui
