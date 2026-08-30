# yolo_detection — YOLOv8 常见物体检测（测试部署）

在 `test/` 下的独立部署：YOLOv8n/s（COCO 80 类），GPU 优先、CPU 兜底。
用于验证"当前机器能否实时检测常见物体"，并为后续 ROS 接入（`/detections`）打基础。

## 机器配置（实测）

| 项 | 值 |
|---|---|
| CPU | i7-13620H（16 线程） |
| GPU | RTX 4050 Laptop 6 GB，CUDA 12.7，WSL2 直通 |
| 内存 | 7.6 GB |
| Python | 3.12.3（venv: `.venv/`） |
| 相机 | ESP32-S3 MJPEG 640×480 @10–15 fps（http://192.168.31.188:81/） |

## 快速开始

```bash
# 1. 下载模型（yolov8n.onnx 12MB / yolov8s.onnx 43MB，GitHub releases）
python3 scripts/download_models.py n      # 或 s / all

# 2. 创建 venv 并安装推理库（GPU 版）
python3 -m venv .venv
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 3. 单图检测
./.venv/bin/python run_detect.py --input samples/bus.jpg --save output/bus_out.jpg

# 4. 相机流（ESP32 MJPEG）
./.venv/bin/python run_detect.py --input http://192.168.31.188:81/ --camera

# 5. 换模型 / 换后端
./.venv/bin/python run_detect.py --input samples/bus.jpg --model models/yolov8s.onnx --device cpu
```

## 后端选择（engine.py）

- `auto`（默认）：onnxruntime 有 CUDA EP 就用 GPU（FP16 自动），否则 CPU；
- `cpu`：强制 CPU（onnxruntime CPU 或 OpenCV DNN 兜底）；
- 若 onnxruntime 未安装：自动回退 OpenCV DNN（系统 cv2 4.6.0，零依赖）。

## 实测性能

### 单帧延迟（bus.jpg，yolov8n@640，50 次统计）

| 后端 | p50 | p95 | min | max |
|---|---|---|---|---|
| **GPU**（onnxruntime CUDA EP，RTX 4050） | **10.9 ms** | 12.7 ms | 10.1 ms | 16.5 ms |
| CPU（onnxruntime CPU，16 线程） | 37.1 ms | 41.9 ms | 31.6 ms | 47.3 ms |

### 延迟分解 + 资源占用（640x480 模拟帧，15 s 持续推理，`bench_latency.py`）

| 模型 | 阶段 | 预处理 | 推理 | 后处理 | 端到端 p50 | 吞吐 | GPU util | 显存 | CPU(全核) |
|---|---|---|---|---|---|---|---|---|---|
| yolov8n | GPU | 1.15 ms | 6.72 ms | 0.57 ms | **8.53 ms** | **113.9 fps** | 46–50% | 395 MiB | ~134% |
| yolov8n | CPU | 1.89 ms | 34.20 ms | 0.71 ms | 36.84 ms | 27.0 fps | — | — | ~316% |
| yolov8s | GPU | 1.24 ms | 10.01 ms | 0.58 ms | **11.91 ms** | **83.1 fps** | 59–63% | 481 MiB | ~123% |
| yolov8s | CPU | 1.42 ms | 82.66 ms | 0.70 ms | 84.94 ms | 11.7 fps | — | — | ~311% |

- 纯推理本体（warmup）：GPU **6.4 ms**，CPU 26–30 ms；
- 结论：GPU 富余（纯处理能力 >100 fps），瓶颈在相机链路（ESP32 10–15 fps，见 docs/ESP32图传画质与帧率待办.md）；
- 相机流端到端（含采集/编码/WiFi）预算约 150–300 ms，见 docs 分析。

### GPU 环境说明

- onnxruntime 1.20.2 + `nvidia-*-cu12` 运行库（pip 安装，wheel 自带 CUDA 12/cuDNN 9）；
- **必须通过 `./run.sh` 运行**（自动设置 `LD_LIBRARY_PATH` 指向 venv 内 nvidia 库）；
- WSL2 依赖 NVIDIA Windows 驱动（本机 566.24 / CUDA 12.7）；若在受限沙箱/容器中运行，`/dev/dxg` ioctl 可能被拦截导致回落 CPU（实机正常终端无此问题）。

## 目录

```
run_detect.py          CLI 入口
yolo_detect/           包：preprocess / postprocess / engine / detector
scripts/download_models.py   模型下载
models/                ONNX 模型缓存（gitignore）
samples/               测试图
output/                标注结果（gitignore）
```

## 后续（未做）

- ROS 2 节点：订阅 `/camera/image_raw/compressed` → 发布 `/detections`
- 帧时间戳修复（ESP32 固件注入采集时刻，当前 stamp 是发布时刻）
- TensorRT 加速 / INT8 量化
