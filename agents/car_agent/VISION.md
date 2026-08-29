# 图像识别工具

Agent 暴露一个统一工具：

```text
recognize_image(image_path, question?)
```

底层 Provider 由 `VISION_PROVIDER` 选择，默认是本机 Ollama：

```bash
cd /home/lk/car/agents/car_agent
source .venv/bin/activate
export PYTHONPATH=src
export VISION_PROVIDER=ollama
export VISION_ALLOWED_IMAGE_DIRS=/home/lk/car/test/fixtures:/home/lk/car/data/images
python scripts/test_vision.py \
  --image /home/lk/car/test/fixtures/esp_vga_q20.jpg \
  --question '这个是什么？'
```

Ollama 默认使用 `http://127.0.0.1:11434` 和
`qwen3-vl:4b-instruct`。可通过 `OLLAMA_VISION_URL`、
`OLLAMA_VISION_MODEL` 等环境变量调整。

测试百度云适配器时，不要把密钥写进命令行或代码；从已经配置好的环境变量加载：

```bash
source ~/.bashrc
export PYTHONPATH=src
export VISION_PROVIDER=baidu
python scripts/test_vision.py \
  --image /home/lk/car/test/fixtures/esp_vga_q20.jpg \
  --question '这个是什么？'
```

也可以用 `--provider ollama` 或 `--provider baidu` 临时覆盖环境变量。脚本输出统一的
`status`、`answer`、`provider`、`model` 和 `latency_ms`，失败时输出错误码和是否可重试，
不会输出 Access Token、API Key、Secret Key 或图片 Base64。

允许目录、图片大小和问题长度会在识别器入口统一校验。ESP32 测试图片
`esp_vga_q20.jpg` 是 640×480、约 29 KB 的 JPEG，适合复用做本地和云端对比。
