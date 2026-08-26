#pragma once

#include "xuegecar_qt_gui/types.hpp"

#include <QWidget>

namespace xuegecar_qt_gui
{

class LidarView : public QWidget
{
  Q_OBJECT

public:
  explicit LidarView(QWidget * parent = nullptr);
  void setState(const AppState & state);

protected:
  void paintEvent(QPaintEvent * event) override;

private:
  AppState state_;
};

}  // namespace xuegecar_qt_gui
