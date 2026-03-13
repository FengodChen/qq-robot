"""服务层。

提供可复用的业务服务：
- llm: LLM 服务
- storage: 存储服务
- news: 新闻服务
"""

from qq_bot.services.llm.base import LLMService
from qq_bot.services.storage.base import StorageService

__all__ = [
    "LLMService",
    "StorageService",
]
