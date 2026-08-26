#include "xuegecar_qt_gui/main_window.hpp"

#include <QCloseEvent>
#include <QDateTime>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QKeyEvent>
#include <QMessageBox>
#include <QProcessEnvironment>
#include <QScrollArea>
#include <QTime>
#include <QVBoxLayout>

#include <cmath>
#include <signal.h>
#include <sys/types.h>
#include <unistd.h>

namespace xuegecar_qt_gui
{

MainWindow::MainWindow(std::shared_ptr<RosInterface> ros, QWidget * parent)
: QMainWindow(parent), ros_(std::move(ros))
{
  setWindowTitle("XuegeCar Qt 上位机");
  resize(1450, 840);
  setMinimumSize(1180, 720);
  setFocusPolicy(Qt::StrongFocus);

  map_view_ = new MapView(this);
  camera_view_ = new CameraView(this);
  lidar_view_ = new LidarView(this);

  connect(map_view_, &MapView::goalSelected, this, [this](double x, double y, double yaw) {
    ros_->publishGoal(x, y, yaw);
    appendLog(QString("发布导航点 x=%1 y=%2 yaw=%3 deg").arg(x, 0, 'f', 2).arg(y, 0, 'f', 2).arg(yaw * 180.0 / M_PI, 0, 'f', 1));
  });
  connect(map_view_, &MapView::initialPoseSelected, this, [this](double x, double y, double yaw) {
    ros_->publishInitialPose(x, y, yaw);
    appendLog(QString("设置初始位姿 x=%1 y=%2 yaw=%3 deg").arg(x, 0, 'f', 2).arg(y, 0, 'f', 2).arg(yaw * 180.0 / M_PI, 0, 'f', 1));
  });

  auto * root = new QWidget(this);
  auto * root_layout = new QVBoxLayout(root);
  root_layout->setContentsMargins(14, 10, 14, 14);
  root_layout->setSpacing(12);
  root_layout->addWidget(createHeader());

  auto * layout = new QGridLayout;
  layout->setSpacing(12);

  auto * left_column = new QGridLayout;
  left_column->setSpacing(12);
  left_column->addWidget(
    createCard("地图 /map      导航目标模式  |  左键拖拽设置方向  |  右键平移  |  滚轮缩放", map_view_, "▣"),
    0, 0);
  left_column->addWidget(createMapPanel(), 1, 0);
  left_column->setRowStretch(0, 76);
  left_column->setRowStretch(1, 24);

  auto * right_panel = new QWidget;
  auto * right_column = new QGridLayout(right_panel);
  right_column->setSpacing(12);
  auto * camera_card = createCard("摄像头画面", camera_view_, "▣");
  camera_card->setFixedHeight(532);
  camera_card->setMinimumWidth(676);
  right_column->addWidget(camera_card, 0, 0, 1, 2);
  right_column->addWidget(createCard("雷达 /scan", lidar_view_, "⌁"), 1, 0);
  right_column->addWidget(createVelocityPanel(), 1, 1);
  right_column->addWidget(createRosDataPanel(), 2, 0, 1, 2);
  right_column->addWidget(createStatusPanel(), 3, 0);
  right_column->addWidget(createLaunchPanel(), 3, 1);
  right_column->setColumnStretch(0, 1);
  right_column->setColumnStretch(1, 1);
  right_column->setRowStretch(0, 46);
  right_column->setRowStretch(1, 22);
  right_column->setRowStretch(2, 17);
  right_column->setRowStretch(3, 15);

  auto * right_scroll = new QScrollArea;
  right_scroll->setWidgetResizable(true);
  right_scroll->setFrameShape(QFrame::NoFrame);
  right_scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  right_scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  right_scroll->setWidget(right_panel);

  layout->addLayout(left_column, 0, 0);
  layout->addWidget(right_scroll, 0, 1);
  layout->setColumnStretch(0, 48);
  layout->setColumnStretch(1, 52);
  root_layout->addLayout(layout, 1);
  setCentralWidget(root);

  setStyleSheet(
    "QMainWindow,QWidget{background:#f6f8fb;color:#1f2937;font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif;}"
    "QFrame#card{background:#ffffff;border:1px solid #e5eaf2;border-radius:10px;}"
    "QLabel#cardTitle{background:transparent;color:#1f2937;font-weight:700;font-size:14px;}"
    "QLabel#iconLabel{background:transparent;color:#2f80ed;font-weight:700;font-size:16px;}"
    "QLabel#subTitle{background:transparent;color:#6b7280;font-size:11px;}"
    "QLabel#pill{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:5px 10px;font-weight:600;}"
    "QGroupBox{background:#ffffff;border:1px solid #e5eaf2;border-radius:10px;margin-top:10px;padding:8px;font-weight:700;color:#1f2937;}"
    "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;color:#1f2937;}"
    "QPushButton{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:6px 12px;min-height:28px;color:#374151;font-weight:600;}"
    "QPushButton:hover{background:#f3f7ff;border-color:#b9d5ff;color:#1d6fe8;}"
    "QPushButton:pressed{background:#e6f0ff;}"
    "QPushButton#primaryButton{background:#2f80ed;color:white;border-color:#2f80ed;}"
    "QPushButton#primaryButton:hover{background:#1d6fe8;}"
    "QPushButton#stopButton{background:#ef4444;color:white;border-color:#ef4444;}"
    "QPushButton#stopButton:hover{background:#dc2626;}"
    "QPushButton#ghostButton{background:#f8fafc;color:#2f80ed;border-color:#e5eaf2;}"
    "QDoubleSpinBox,QLineEdit,QPlainTextEdit{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:5px;color:#1f2937;}"
    "QLabel{background:transparent;color:#374151;}");

  connect(&refresh_timer_, &QTimer::timeout, this, &MainWindow::refresh);
  refresh_timer_.start(20);
}

MainWindow::~MainWindow()
{
  stopRobot();
}

QWidget * MainWindow::createHeader()
{
  auto * header = new QWidget;
  header->setFixedHeight(42);
  auto * layout = new QHBoxLayout(header);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(12);

  auto * logo = new QLabel("◎");
  logo->setStyleSheet("color:#2f80ed;font-size:30px;font-weight:800;");
  auto * titles = new QWidget;
  auto * title_layout = new QVBoxLayout(titles);
  title_layout->setContentsMargins(0, 0, 0, 0);
  title_layout->setSpacing(0);
  auto * title = new QLabel("机器人控制中心");
  title->setStyleSheet("font-size:19px;font-weight:800;color:#111827;");
  auto * sub = new QLabel("ROBOT CONTROL CENTER");
  sub->setObjectName("subTitle");
  title_layout->addWidget(title);
  title_layout->addWidget(sub);

  header_battery_label_ = new QLabel("▰ 电池: --");
  header_robot_label_ = new QLabel("♟ 机器人: 未连接");
  header_time_label_ = new QLabel("--:--:--");
  for (auto * label : {header_battery_label_, header_robot_label_, header_time_label_}) {
    label->setObjectName("pill");
  }
  header_robot_label_->setStyleSheet("QLabel#pill{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:5px 10px;font-weight:700;color:#16a34a;}");

  layout->addWidget(logo);
  layout->addWidget(titles);
  layout->addStretch(1);
  layout->addWidget(header_battery_label_);
  layout->addWidget(header_robot_label_);
  layout->addWidget(header_time_label_);
  return header;
}

QWidget * MainWindow::createCard(const QString & title, QWidget * content, const QString & icon)
{
  auto * card = new QFrame;
  card->setObjectName("card");
  auto * layout = new QVBoxLayout(card);
  layout->setContentsMargins(12, 10, 12, 12);
  layout->setSpacing(8);
  if (!title.isEmpty()) {
    auto * title_row = new QWidget;
    auto * title_layout = new QHBoxLayout(title_row);
    title_layout->setContentsMargins(0, 0, 0, 0);
    title_layout->setSpacing(6);
    if (!icon.isEmpty()) {
      auto * icon_label = new QLabel(icon);
      icon_label->setObjectName("iconLabel");
      title_layout->addWidget(icon_label);
    }
    auto * title_label = new QLabel(title);
    title_label->setObjectName("cardTitle");
    title_layout->addWidget(title_label);
    title_layout->addStretch(1);
    layout->addWidget(title_row);
  }
  layout->addWidget(content, 1);
  return card;
}

QWidget * MainWindow::createVelocityPanel()
{
  auto * group = new QGroupBox("速度控制");
  auto * layout = new QGridLayout(group);
  layout->setContentsMargins(8, 8, 8, 8);
  layout->setSpacing(8);
  max_linear_ = new QDoubleSpinBox;
  max_linear_->setRange(0.0, 2.0);
  max_linear_->setSingleStep(0.05);
  max_linear_->setSuffix(" m/s");
  max_linear_->setValue(0.30);
  max_angular_ = new QDoubleSpinBox;
  max_angular_->setRange(0.0, 5.0);
  max_angular_->setSingleStep(0.05);
  max_angular_->setSuffix(" rad/s");
  max_angular_->setValue(1.00);

  layout->addWidget(new QLabel("最大线速度"), 0, 0);
  layout->addWidget(max_linear_, 0, 1, 1, 3);
  layout->addWidget(new QLabel("最大角速度"), 1, 0);
  layout->addWidget(max_angular_, 1, 1, 1, 3);

  auto * w = makeButton("W");
  auto * a = makeButton("A");
  auto * s = makeButton("S");
  auto * d = makeButton("D");
  auto * stop = makeButton("停止");
  stop->setObjectName("stopButton");
  connect(w, &QPushButton::pressed, this, [this]() { setVirtualKey("w", true); });
  connect(w, &QPushButton::released, this, [this]() { setVirtualKey("w", false); });
  connect(a, &QPushButton::pressed, this, [this]() { setVirtualKey("a", true); });
  connect(a, &QPushButton::released, this, [this]() { setVirtualKey("a", false); });
  connect(s, &QPushButton::pressed, this, [this]() { setVirtualKey("s", true); });
  connect(s, &QPushButton::released, this, [this]() { setVirtualKey("s", false); });
  connect(d, &QPushButton::pressed, this, [this]() { setVirtualKey("d", true); });
  connect(d, &QPushButton::released, this, [this]() { setVirtualKey("d", false); });
  connect(stop, &QPushButton::clicked, this, &MainWindow::stopRobot);
  layout->addWidget(w, 2, 1);
  layout->addWidget(a, 3, 0);
  layout->addWidget(s, 3, 1);
  layout->addWidget(d, 3, 2);
  layout->addWidget(stop, 3, 3);
  return group;
}

QWidget * MainWindow::createStatusPanel()
{
  auto * group = new QGroupBox("状态");
  auto * layout = new QGridLayout(group);
  cmd_label_ = new QLabel;
  odom_label_ = new QLabel;
  pose_label_ = new QLabel;
  battery_label_ = new QLabel;
  topic_label_ = new QLabel;
  topic_label_->setWordWrap(true);
  layout->addWidget(cmd_label_, 0, 0);
  layout->addWidget(odom_label_, 1, 0);
  layout->addWidget(pose_label_, 2, 0);
  layout->addWidget(battery_label_, 3, 0);
  layout->addWidget(topic_label_, 4, 0);
  return group;
}

QWidget * MainWindow::createMapPanel()
{
  auto * group = new QGroupBox("地图与定位");
  group->setMaximumHeight(190);
  auto * layout = new QGridLayout(group);
  auto * init_pose = makeButton("设置位姿");
  auto * fit = makeButton("地图复位");
  for (auto * button : {init_pose, fit}) {
    button->setObjectName("ghostButton");
  }
  connect(init_pose, &QPushButton::clicked, this, &MainWindow::enableInitialPoseMode);
  connect(fit, &QPushButton::clicked, map_view_, &MapView::fitToMap);
  layout->addWidget(init_pose, 0, 0);
  layout->addWidget(fit, 0, 1);
  layout->setColumnStretch(2, 1);
  return group;
}

QWidget * MainWindow::createLaunchPanel()
{
  auto * group = new QGroupBox("启动命令");
  auto * layout = new QGridLayout(group);
  const std::vector<std::pair<QString, QString>> commands = {
    {"Micro-ROS", "ros2 launch micro_ros_agent micro_ros_agent_launch.py"},
    {"底层驱动", "ros2 launch xuegecar_bringup xuegecar_bringup.launch.py"},
    {"摄像头", "ros2 launch xuegecar_camera http_video_publisher.launch.py"},
    {"SLAM+Nav2", "ros2 launch xuegecar_navigation2 slam_nav2.launch.py"},
  };
  int row = 0;
  for (const auto & [name, command] : commands) {
    auto item = std::make_unique<LaunchItem>();
    item->name = name;
    item->command = command;
    item->start_button = makeButton("启动");
    item->stop_button = makeButton("停止");
    item->status_label = new QLabel("未运行");
    item->stop_button->setEnabled(false);
    item->start_button->setObjectName("primaryButton");
    auto * raw = item.get();
    connect(item->start_button, &QPushButton::clicked, this, [this, raw]() { startLaunch(raw); });
    connect(item->stop_button, &QPushButton::clicked, this, [this, raw]() { stopLaunch(raw); });
    layout->addWidget(new QLabel(name), row, 0);
    layout->addWidget(item->start_button, row, 1);
    layout->addWidget(item->stop_button, row, 2);
    layout->addWidget(item->status_label, row, 3);
    launch_items_.push_back(std::move(item));
    ++row;
  }
  log_view_ = new QPlainTextEdit;
  log_view_->setReadOnly(true);
  log_view_->setMaximumHeight(70);
  log_view_->hide();
  layout->addWidget(log_view_, row, 0, 1, 4);
  return group;
}

QWidget * MainWindow::createRosDataPanel()
{
  auto * group = new QGroupBox("ROS数据");
  auto * layout = new QGridLayout(group);
  layout->setContentsMargins(8, 8, 8, 8);
  layout->setSpacing(6);

  ros_topic_combo_ = new QComboBox;
  ros_topic_combo_->setEditable(false);
  auto * refresh = makeButton("刷新话题");
  auto * info = makeButton("Info");
  auto * echo = makeButton("Echo一次");
  refresh->setObjectName("ghostButton");
  info->setObjectName("ghostButton");
  echo->setObjectName("ghostButton");
  ros_data_view_ = new QPlainTextEdit;
  ros_data_view_->setReadOnly(true);
  ros_data_view_->setMaximumBlockCount(300);
  ros_data_view_->setMinimumHeight(54);
  ros_data_view_->setMaximumHeight(76);

  connect(refresh, &QPushButton::clicked, this, &MainWindow::refreshRosTopics);
  connect(info, &QPushButton::clicked, this, &MainWindow::showRosTopicInfo);
  connect(echo, &QPushButton::clicked, this, &MainWindow::echoRosTopic);

  layout->addWidget(new QLabel("话题"), 0, 0);
  layout->addWidget(ros_topic_combo_, 0, 1, 1, 3);
  layout->addWidget(refresh, 0, 4);
  layout->addWidget(info, 0, 5);
  layout->addWidget(echo, 0, 6);
  layout->addWidget(ros_data_view_, 1, 0, 1, 7);
  QTimer::singleShot(800, this, &MainWindow::refreshRosTopics);
  return group;
}

QPushButton * MainWindow::makeButton(const QString & text)
{
  auto * button = new QPushButton(text);
  button->setFocusPolicy(Qt::NoFocus);
  return button;
}

QString MainWindow::rosShellPrefix() const
{
  return
    "source /opt/ros/${ROS_DISTRO:-humble}/setup.bash && "
    "if [ -f /home/xuegeros/microros_ws/install/setup.bash ]; then source /home/xuegeros/microros_ws/install/setup.bash; fi && "
    "if [ -f /home/xuegeros/xuegeros_ws/install/setup.bash ]; then source /home/xuegeros/xuegeros_ws/install/setup.bash; fi && ";
}

void MainWindow::refreshRosTopics()
{
  QProcess process;
  process.start("bash", {"-lc", rosShellPrefix() + "ros2 topic list -t"});
  process.waitForFinished(2500);
  const auto output = QString::fromLocal8Bit(process.readAllStandardOutput());
  const auto error = QString::fromLocal8Bit(process.readAllStandardError());

  const auto current = ros_topic_combo_->currentText();
  ros_topic_combo_->clear();
  for (const auto & line : output.split('\n', Qt::SkipEmptyParts)) {
    ros_topic_combo_->addItem(line.trimmed());
  }
  const auto index = ros_topic_combo_->findText(current);
  if (index >= 0) {
    ros_topic_combo_->setCurrentIndex(index);
  }
  ros_data_view_->setPlainText(error.isEmpty() ? output : output + "\n" + error);
}

void MainWindow::showRosTopicInfo()
{
  const auto selected = ros_topic_combo_->currentText();
  const auto topic = selected.section(' ', 0, 0);
  if (topic.isEmpty()) {
    return;
  }
  QProcess process;
  process.start("bash", {"-lc", rosShellPrefix() + "ros2 topic info -v " + topic});
  process.waitForFinished(2500);
  ros_data_view_->setPlainText(
    QString::fromLocal8Bit(process.readAllStandardOutput()) +
    QString::fromLocal8Bit(process.readAllStandardError()));
}

void MainWindow::echoRosTopic()
{
  const auto selected = ros_topic_combo_->currentText();
  const auto topic = selected.section(' ', 0, 0);
  if (topic.isEmpty()) {
    return;
  }
  QProcess process;
  process.start("bash", {"-lc", rosShellPrefix() + "timeout 2 ros2 topic echo --once " + topic});
  process.waitForFinished(3500);
  ros_data_view_->setPlainText(
    QString::fromLocal8Bit(process.readAllStandardOutput()) +
    QString::fromLocal8Bit(process.readAllStandardError()));
}

void MainWindow::refresh()
{
  const auto state = ros_->stateSnapshot();
  map_view_->setState(state);
  camera_view_->setImage(state.camera_image);
  lidar_view_->setState(state);
  updateStatus(state);
}

void MainWindow::publishKeyboardCommand()
{
  double linear = 0.0;
  double angular = 0.0;
  if (keys_.count("w")) {
    linear += max_linear_->value();
  }
  if (keys_.count("s")) {
    linear -= max_linear_->value();
  }
  if (keys_.count("a")) {
    angular += max_angular_->value();
  }
  if (keys_.count("d")) {
    angular -= max_angular_->value();
  }
  ros_->publishVelocity(linear, angular);
}

void MainWindow::stopRobot()
{
  keys_.clear();
  ros_->publishVelocity(0.0, 0.0);
}

void MainWindow::setVirtualKey(const QString & key, bool pressed)
{
  if (pressed) {
    keys_.insert(key);
  } else {
    keys_.erase(key);
  }
  publishKeyboardCommand();
}

void MainWindow::updateStatus(const AppState & state)
{
  cmd_label_->setText(QString("控制速度: 线速度 %1 m/s 角速度 %2 rad/s")
    .arg(state.cmd_linear, 0, 'f', 2).arg(state.cmd_angular, 0, 'f', 2));
  odom_label_->setText(QString("反馈速度: 线速度 %1 m/s 角速度 %2 rad/s")
    .arg(state.odom_linear, 0, 'f', 2).arg(state.odom_angular, 0, 'f', 2));
  if (state.robot_pose.valid) {
    pose_label_->setText(QString("机器人位置: %1 x=%2 y=%3 方向=%4 deg")
      .arg(QString::fromStdString(state.robot_pose.frame_id))
      .arg(state.robot_pose.x, 0, 'f', 2)
      .arg(state.robot_pose.y, 0, 'f', 2)
      .arg(state.robot_pose.yaw * 180.0 / M_PI, 0, 'f', 1));
  } else {
    pose_label_->setText("机器人位置: 等待 TF 或 /odom");
  }
  const auto voltage = state.battery.has_voltage ? QString("%1 V").arg(state.battery.voltage, 0, 'f', 2) : "--";
  const auto percent = state.battery.has_percent ? QString("%1 %").arg(state.battery.percent, 0, 'f', 0) : "--";
  battery_label_->setText(QString("电池: 电压 %1 电量 %2").arg(voltage, percent));
  header_battery_label_->setText(QString("▰ 电池: %1").arg(state.battery.has_percent ? percent : voltage));
  const bool connected = state.ages.odom >= 0.0 && state.ages.odom < 2.0;
  header_robot_label_->setText(connected ? "♟ 机器人: 已连接" : "♟ 机器人: 未连接");
  header_robot_label_->setStyleSheet(connected
    ? "QLabel#pill{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:5px 10px;font-weight:700;color:#16a34a;}"
    : "QLabel#pill{background:#ffffff;border:1px solid #e5eaf2;border-radius:6px;padding:5px 10px;font-weight:700;color:#ef4444;}");
  header_time_label_->setText("◷ " + QTime::currentTime().toString("HH:mm:ss"));
  auto age = [](double value) { return value < 0.0 ? QString("--") : QString("%1s").arg(value, 0, 'f', 1); };
  topic_label_->setText(QString("刷新: map %1 | scan %2 | camera %3 | odom %4 | tf %5 | battery %6")
    .arg(age(state.ages.map), age(state.ages.scan), age(state.ages.camera),
      age(state.ages.odom), age(state.ages.tf), age(state.ages.battery)));
}

void MainWindow::enableInitialPoseMode()
{
  map_view_->setInitialPoseMode(true);
  appendLog("初始位姿模式: 在地图左键拖拽设置位置和方向");
}

void MainWindow::startLaunch(LaunchItem * item)
{
  if (item->process && item->process->state() != QProcess::NotRunning) {
    return;
  }
  item->process = new QProcess(this);
  item->process->setProcessChannelMode(QProcess::MergedChannels);
  connect(item->process, &QProcess::readyReadStandardOutput, this, [this, item]() {
    const auto text = QString::fromLocal8Bit(item->process->readAllStandardOutput());
    for (const auto & line : text.split('\n', Qt::SkipEmptyParts)) {
      appendLog(QString("[%1] %2").arg(item->name, line));
    }
  });
  connect(item->process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [item](int code) {
    item->start_button->setEnabled(true);
    item->stop_button->setEnabled(false);
    item->status_label->setText(QString("已退出(%1)").arg(code));
    item->pid = -1;
  });
  const auto command = QString("%1exec %2").arg(rosShellPrefix(), item->command);
  item->process->start("setsid", {"bash", "-lc", command});
  if (item->process->waitForStarted(1500)) {
    item->pid = item->process->processId();
  }
  item->start_button->setEnabled(false);
  item->stop_button->setEnabled(true);
  item->status_label->setText("运行中");
  appendLog(QString("[%1] 启动").arg(item->name));
}

void MainWindow::stopLaunch(LaunchItem * item)
{
  if (!item->process || item->process->state() == QProcess::NotRunning) {
    return;
  }
  cleanupLaunchProcess(item, false);
  item->status_label->setText("停止中");
  QTimer::singleShot(1800, this, [this, item]() { cleanupLaunchProcess(item, true); });
}

void MainWindow::cleanupLaunchProcess(LaunchItem * item, bool force)
{
  if (!item || !item->process) {
    return;
  }
  const int signal = force ? SIGKILL : SIGTERM;
  if (item->pid > 0) {
    kill(-static_cast<pid_t>(item->pid), signal);
    QProcess::execute("pkill", {force ? "-KILL" : "-TERM", "-s", QString::number(item->pid)});
    QProcess::execute("pkill", {force ? "-KILL" : "-TERM", "-P", QString::number(item->pid)});
  }
  if (item->process->state() != QProcess::NotRunning) {
    if (force) {
      item->process->kill();
    } else {
      item->process->terminate();
    }
  }
}

void MainWindow::appendLog(const QString & text)
{
  log_view_->appendPlainText(QDateTime::currentDateTime().toString("HH:mm:ss ") + text);
}

void MainWindow::keyPressEvent(QKeyEvent * event)
{
  if (event->isAutoRepeat()) {
    return;
  }
  if (event->key() == Qt::Key_W) setVirtualKey("w", true);
  else if (event->key() == Qt::Key_A) setVirtualKey("a", true);
  else if (event->key() == Qt::Key_S) setVirtualKey("s", true);
  else if (event->key() == Qt::Key_D) setVirtualKey("d", true);
  else if (event->key() == Qt::Key_Space) stopRobot();
  else QMainWindow::keyPressEvent(event);
}

void MainWindow::keyReleaseEvent(QKeyEvent * event)
{
  if (event->isAutoRepeat()) {
    return;
  }
  if (event->key() == Qt::Key_W) setVirtualKey("w", false);
  else if (event->key() == Qt::Key_A) setVirtualKey("a", false);
  else if (event->key() == Qt::Key_S) setVirtualKey("s", false);
  else if (event->key() == Qt::Key_D) setVirtualKey("d", false);
  else QMainWindow::keyReleaseEvent(event);
}

void MainWindow::closeEvent(QCloseEvent * event)
{
  stopRobot();
  for (auto & item : launch_items_) {
    if (item->process && item->process->state() != QProcess::NotRunning) {
      cleanupLaunchProcess(item.get(), false);
      item->process->waitForFinished(1200);
      if (item->process->state() != QProcess::NotRunning) {
        cleanupLaunchProcess(item.get(), true);
        item->process->waitForFinished(500);
      }
    }
  }
  QMainWindow::closeEvent(event);
}

}  // namespace xuegecar_qt_gui
