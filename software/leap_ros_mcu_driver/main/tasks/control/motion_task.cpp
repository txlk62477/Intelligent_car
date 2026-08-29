#include "system_globals.h"
#include "board.h" 
#include "msg/motion_msg.h"
#include "msg/pid_msg.h"
#include "msg/imu_msg.h"  
#include "esp_log.h"
#include "esp_timer.h"
#include <cmath>          

static const char *TAG = "MOTION_TASK";

void motion_task(void *p) {
    constexpr float kNominalDtSeconds = 0.02f;
    const TickType_t control_period_ticks = pdMS_TO_TICKS(20);
    TickType_t last_wake_tick = xTaskGetTickCount();
    int64_t last_update_us = esp_timer_get_time();
    bool first_update = true;

    MotionMsg cmd_msg = {};
    MotionMsg incoming_msg = {};
    MotionMsg status_msg = {};
    PidMsg speed_msg;
    PidMsg position_msg;
    
    ImuMsg imu_msg = {}; 

    int current_mode = 0;
    
    while (1) {
        const int64_t current_update_us = esp_timer_get_time();
        const float dt = first_update
            ? kNominalDtSeconds
            : static_cast<float>(current_update_us - last_update_us) / 1000000.0f;
        last_update_us = current_update_us;
        first_update = false;

        xQueuePeek(q_imu_state, &imu_msg, 0);
                 
        // 1. 接收底层运动指令 (指令状态机)
        if (xQueueReceive(q_motion_cmd, &incoming_msg, 0) == pdTRUE) { 
            cmd_msg = incoming_msg;
            current_mode = cmd_msg.control_mode; // 记录最新模式

            // 处理离散单次触发模式
            if (current_mode == 0) {
                robot.Drive(cmd_msg.target_vx, cmd_msg.target_vy, cmd_msg.target_wz);
            } else if (current_mode == 1) {
                robot.MoveToPosition(cmd_msg.target_x, cmd_msg.target_y, cmd_msg.target_yaw);
            } else if (current_mode == 2) {
                g_motion_busy = true;
                robot.MoveRelative(cmd_msg.target_x, cmd_msg.target_yaw);
            } else if (current_mode == 3) {
                robot.SetMotorTargetVelocity((MotorID)cmd_msg.motor_id, cmd_msg.target_motor_v);
            } else if (current_mode == 6) {
                robot.SetAllMotorTargetsVelocity(
                    cmd_msg.target_vx,
                    cmd_msg.target_vy);
            } else {
                ESP_LOGW(TAG, "Unsupported motion mode: %d", current_mode);
            }
        }

        // 2. 接收 PID 指令 (速度环)
        if (xQueueReceive(q_speedpid_cmd, &speed_msg, 0) == pdTRUE) { 
            robot.SetVelocityPidGains(speed_msg.kp, speed_msg.ki, speed_msg.kd);
            g_speed_pid_state = speed_msg;
            ESP_LOGI(TAG, "Speed PID updated: kp=%.3f, ki=%.3f, kd=%.3f",
                     speed_msg.kp, speed_msg.ki, speed_msg.kd);
        }
        
        // 3. 接收 PID 指令 (位置环)
        if (xQueueReceive(q_postionpid_cmd, &position_msg, 0) == pdTRUE) { 
            robot.SetPositionPidGains(position_msg.kp, position_msg.ki, position_msg.kd);
            g_position_pid_state = position_msg;
            ESP_LOGI(TAG, "Position PID updated: kp=%.3f, ki=%.3f, kd=%.3f",
                     position_msg.kp, position_msg.ki, position_msg.kd);
        }

        // 4. 执行物理控制循环
        if (g_emergency_stop) {
            robot.Stop();
            g_motion_busy = false;
        } else {
            float imu_yaw_rad = imu_msg.yaw * (M_PI / 180.0f); 
            robot.Update(dt, imu_yaw_rad);
            if (current_mode == 2 && !robot.IsBusy()) {
                g_motion_busy = false;
            }
        }
        
        // 5. 更新遥测状态并上报
        robot.GetVelocity(&status_msg.vx, &status_msg.vy, &status_msg.wz);
        robot.GetOdometry(&status_msg.x, &status_msg.y, &status_msg.yaw);
        robot.GetOdometryQuaternion(&status_msg.qw,&status_msg.qx,&status_msg.qy,&status_msg.qz);

        robot.GetAllMotorVelocities(&status_msg.vel_left, &status_msg.vel_right);

        // 与本轮编码器读取/里程计更新对应的 MCU 单调采样时间。
        status_msg.sample_time_us = current_update_us;

        status_msg.control_mode = cmd_msg.control_mode;
        status_msg.source = cmd_msg.source;
        status_msg.target_vx = cmd_msg.target_vx; 
        
        xQueueOverwrite(q_motion_state, &status_msg);
        
        vTaskDelayUntil(&last_wake_tick, control_period_ticks);
    }
}
