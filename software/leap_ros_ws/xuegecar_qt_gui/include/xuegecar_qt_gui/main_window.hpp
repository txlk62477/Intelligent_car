#pragma once

#include "xuegecar_qt_gui/camera_view.hpp"
#include "xuegecar_qt_gui/lidar_view.hpp"
#include "xuegecar_qt_gui/map_view.hpp"
#include "xuegecar_qt_gui/ros_interface.hpp"

#include <QDoubleSpinBox>
#include <QComboBox>
#include <QLabel>
#include <QLineEdit>
#include <QMainWindow>
#include <QPlainTextEdit>
#include <QProcess>
#include <QPushButton>
#include <QTimer>

#include <map>
#include <memory>
#include <set>

namespace xuegecar_qt_gui
{

struct LaunchItem
{
  QString name;
  QString command;
  QPushButton * start_button{nullptr};
  QPushButton * stop_button{nullptr};
  QLabel * status_label{nullptr};
  QProcess * process{nullptr};
  qint64 pid{-1};
};

class MainWindow : public QMainWindow
{
  Q_OBJECT

public:
  explicit MainWindow(std::shared_ptr<RosInterface> ros, QWidget * parent = nullptr);
  ~MainWindow() override;

protected:
  void keyPressEvent(QKeyEvent * event) override;
  void keyReleaseEvent(QKeyEvent * event) override;
  void closeEvent(QCloseEvent * event) override;

private:
  QWidget * createHeader();
  QWidget * createCard(const QString & title, QWidget * content, const QString & icon = QString());
  QWidget * createVelocityPanel();
  QWidget * createStatusPanel();
  QWidget * createMapPanel();
  QWidget * createLaunchPanel();
  QWidget * createRosDataPanel();
  QPushButton * makeButton(const QString & text);

  void refresh();
  void publishKeyboardCommand();
  void stopRobot();
  void setVirtualKey(const QString & key, bool pressed);
  void startLaunch(LaunchItem * item);
  void stopLaunch(LaunchItem * item);
  void cleanupLaunchProcess(LaunchItem * item, bool force);
  QString rosShellPrefix() const;
  void refreshRosTopics();
  void showRosTopicInfo();
  void echoRosTopic();
  void appendLog(const QString & text);
  void updateStatus(const AppState & state);
  void enableInitialPoseMode();

  std::shared_ptr<RosInterface> ros_;
  MapView * map_view_{nullptr};
  CameraView * camera_view_{nullptr};
  LidarView * lidar_view_{nullptr};
  QDoubleSpinBox * max_linear_{nullptr};
  QDoubleSpinBox * max_angular_{nullptr};
  QLabel * cmd_label_{nullptr};
  QLabel * odom_label_{nullptr};
  QLabel * pose_label_{nullptr};
  QLabel * battery_label_{nullptr};
  QLabel * topic_label_{nullptr};
  QLabel * header_battery_label_{nullptr};
  QLabel * header_robot_label_{nullptr};
  QLabel * header_time_label_{nullptr};
  QPlainTextEdit * log_view_{nullptr};
  QComboBox * ros_topic_combo_{nullptr};
  QPlainTextEdit * ros_data_view_{nullptr};
  QTimer refresh_timer_;
  std::set<QString> keys_;
  std::vector<std::unique_ptr<LaunchItem>> launch_items_;
};

}  // namespace xuegecar_qt_gui
