#pragma once

#include "xuegecar_qt_gui/types.hpp"

#include <QWidget>

namespace xuegecar_qt_gui
{

class MapView : public QWidget
{
  Q_OBJECT

public:
  explicit MapView(QWidget * parent = nullptr);

  void setState(const AppState & state);
  void fitToMap();
  void setInitialPoseMode(bool enabled);

signals:
  void goalSelected(double x, double y, double yaw);
  void initialPoseSelected(double x, double y, double yaw);

protected:
  void paintEvent(QPaintEvent * event) override;
  void resizeEvent(QResizeEvent * event) override;
  void wheelEvent(QWheelEvent * event) override;
  void mousePressEvent(QMouseEvent * event) override;
  void mouseMoveEvent(QMouseEvent * event) override;
  void mouseReleaseEvent(QMouseEvent * event) override;

private:
  QPointF worldToImage(double x, double y, const MapLayer & layer) const;
  QPointF imageToWorld(const QPointF & point) const;
  QPointF worldToScreen(double x, double y) const;
  QPointF imageToScreen(const QPointF & point) const;
  QPointF screenToImage(const QPointF & point) const;

  void drawCostmaps(QPainter & painter);
  void drawScan(QPainter & painter);
  void drawPaths(QPainter & painter);
  void drawRobot(QPainter & painter);
  void drawPoseArrow(QPainter & painter, const Pose2D & pose, const QColor & color, const QString & label);

  AppState state_;
  double scale_{1.0};
  QPointF offset_{0.0, 0.0};
  bool user_view_{false};
  bool initial_pose_mode_{false};
  QString drag_mode_;
  QPointF drag_start_;
  QPointF drag_offset_;
  QPointF pose_start_;
  QPointF pose_current_;
};

}  // namespace xuegecar_qt_gui
