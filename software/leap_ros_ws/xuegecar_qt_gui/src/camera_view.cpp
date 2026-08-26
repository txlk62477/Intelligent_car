#include "xuegecar_qt_gui/camera_view.hpp"

#include <QPainter>

#include <algorithm>

namespace xuegecar_qt_gui
{

namespace
{

constexpr int kCameraDisplayWidth = 640;
constexpr int kCameraDisplayHeight = 480;

}  // namespace

CameraView::CameraView(QWidget * parent)
: QWidget(parent)
{
  setFixedSize(kCameraDisplayWidth, kCameraDisplayHeight);
  setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
}

void CameraView::setImage(const QImage & image)
{
  image_ = image;
  update();
}

void CameraView::paintEvent(QPaintEvent *)
{
  QPainter painter(this);
  painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
  painter.fillRect(rect(), QColor(255, 255, 255));

  if (image_.isNull()) {
    const int center_y = rect().center().y();
    painter.setPen(QColor(156, 163, 175));
    painter.setFont(QFont("Sans Serif", 40, QFont::Bold));
    painter.drawText(QRect(0, center_y - 70, width(), 58), Qt::AlignCenter, "◉");
    painter.setPen(QColor(75, 85, 99));
    painter.setFont(QFont("Sans Serif", 13));
    painter.drawText(QRect(0, center_y + 2, width(), 34), Qt::AlignCenter, "等待 /camera/image_raw/compressed");
    return;
  }

  const QRect available = rect().adjusted(6, 6, -6, -6);
  const double display_ratio = 4.0 / 3.0;
  double target_w = available.width();
  double target_h = target_w / display_ratio;
  if (target_h > available.height()) {
    target_h = available.height();
    target_w = target_h * display_ratio;
  }
  const QRectF target(
    available.x() + (available.width() - target_w) * 0.5,
    available.y() + (available.height() - target_h) * 0.5,
    target_w,
    target_h);
  painter.drawImage(target, image_);
  painter.setPen(QPen(QColor(191, 219, 254), 2));
  painter.drawRect(target);
}

}  // namespace xuegecar_qt_gui
