"""图像识别 Provider 适配器。"""

from agent.vision.adapters.baidu import BaiduVisionAdapter
from agent.vision.adapters.ollama import OllamaVisionAdapter

__all__ = ["BaiduVisionAdapter", "OllamaVisionAdapter"]
