#include "xuegecar_qt_gui/main_window.hpp"
#include "xuegecar_qt_gui/ros_interface.hpp"

#include <QApplication>

#include <rclcpp/rclcpp.hpp>

int main(int argc, char ** argv)
{
  setenv("QT_QPA_PLATFORM", "xcb", 0);
  rclcpp::init(argc, argv);
  QApplication app(argc, argv);

  auto ros = std::make_shared<xuegecar_qt_gui::RosInterface>();
  ros->start();

  xuegecar_qt_gui::MainWindow window(ros);
  window.show();

  const int result = app.exec();
  ros->stop();
  rclcpp::shutdown();
  return result;
}
