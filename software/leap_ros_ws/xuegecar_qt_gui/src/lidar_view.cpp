#include "xuegecar_qt_gui/lidar_view.hpp"

#include <QPainter>

#include <algorithm>

namespace xuegecar_qt_gui
{

LidarView::LidarView(QWidget * parent)
: QWidget(parent)
{
  setMinimumSize(240, 150);
  setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
}

void LidarView::setState(const AppState & state)
{
  state_ = state;
  update();
}

void LidarView::paintEvent(QPaintEvent *)
{
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing, true);
  painter.fillRect(rect(), QColor(255, 255, 255));

  const QPointF center(width() * 0.5, height() * 0.55);
  const double radius = std::min(width(), height()) * 0.42;
  painter.setPen(QPen(QColor(229, 235, 245), 1));
  for (double ratio : {0.25, 0.5, 0.75, 1.0}) {
    painter.drawEllipse(center, radius * ratio, radius * ratio);
  }
  painter.drawLine(QPointF(center.x(), center.y() - radius), QPointF(center.x(), center.y() + radius));
  painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()));

  if (!state_.scan_points.empty()) {
    QPolygonF points;
    const double scale = radius / std::max(1.0, state_.scan_range_max);
    for (const auto & p : state_.scan_points) {
      points << QPointF(center.x() + p.y() * scale, center.y() - p.x() * scale);
    }
    painter.setPen(QPen(QColor(64, 220, 135), 2));
    painter.drawPoints(points);
  }

  painter.setBrush(QColor(47, 128, 237));
  painter.setPen(QPen(QColor(255, 255, 255), 1));
  QPolygonF robot;
  robot << QPointF(center.x(), center.y() - 12) << QPointF(center.x() - 8, center.y() + 9)
        << QPointF(center.x() + 8, center.y() + 9);
  painter.drawPolygon(robot);

  painter.setPen(QColor(55, 65, 81));
  painter.drawText(10, 22, QString("雷达 /scan  range %1 m").arg(state_.scan_range_max, 0, 'f', 1));
}

}  // namespace xuegecar_qt_gui
