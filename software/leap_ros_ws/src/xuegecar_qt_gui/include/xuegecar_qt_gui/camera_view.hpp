#pragma once

#include <QImage>
#include <QWidget>

namespace xuegecar_qt_gui
{

class CameraView : public QWidget
{
  Q_OBJECT

public:
  explicit CameraView(QWidget * parent = nullptr);
  void setImage(const QImage & image);

protected:
  void paintEvent(QPaintEvent * event) override;

private:
  QImage image_;
};

}  // namespace xuegecar_qt_gui
