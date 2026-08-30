# 一帧图像的 YOLO 检测结果
std_msgs/Header header
float32 inference_ms   # 纯推理耗时
float32 total_ms       # 端到端耗时（解码+预处理+推理+后处理）
Detection[] detections # 本帧检测到的目标
