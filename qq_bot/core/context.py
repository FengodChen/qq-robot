"""请求上下文。

提供统一的方式来访问请求相关的信息和依赖服务。
"""

from typing import Any, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from qq_bot.core.config import BotConfig
    from qq_bot.services.llm.base import LLMService
    from qq_bot.services.storage.message import MessageStore
    from qq_bot.services.storage.conversation import ConversationManager
    from qq_bot.services.summary_service import SummaryService


@dataclass
class Context:
    """请求上下文。
    
    封装一次请求处理过程中需要访问的所有共享资源。
    
    Example:
        >>> async def handle_message(ctx: Context, event: MessageEvent):
        ...     config = ctx.config
        ...     llm = ctx.services.llm
        ...     await llm.chat(event.content)
    """
    
    # 配置
    config: "BotConfig"
    
    # 服务集合
    services: "ServiceContainer" = field(default_factory=lambda: ServiceContainer())
    
    # 请求级别的临时数据
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取上下文数据。"""
        return self.metadata.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置上下文数据。"""
        self.metadata[key] = value


@dataclass
class ServiceContainer:
    """服务容器。
    
    统一管理所有可复用的服务实例。
    """
    
    llm: "LLMService | None" = None
    message_store: "MessageStore | None" = None
    conversation: "ConversationManager | None" = None
    summary: "SummaryService | None" = None
    
    def register(self, name: str, service: Any) -> None:
        """注册服务。"""
        if hasattr(self, name):
            setattr(self, name, service)
        else:
            # 动态添加到 metadata
            if not hasattr(self, "_extra_services"):
                object.__setattr__(self, "_extra_services", {})
            self._extra_services[name] = service
    
    def get(self, name: str) -> Any:
        """获取服务。"""
        if hasattr(self, name):
            return getattr(self, name)
        if hasattr(self, "_extra_services"):
            return self._extra_services.get(name)
        return None
