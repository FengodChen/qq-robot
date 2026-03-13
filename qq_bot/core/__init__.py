"""核心框架层。

提供基础设施组件：
- config: 配置管理
- events: 事件系统  
- plugin: 插件基类
- context: 请求上下文
- router: 消息路由
- exceptions: 异常体系
"""

from qq_bot.core.config import BotConfig
from qq_bot.core.events import MessageEvent, IntentEvent
from qq_bot.core.context import Context
from qq_bot.core.plugin import Plugin, PluginManager
from qq_bot.core.router import Router
from qq_bot.core.exceptions import (
    BotError,
    ConfigError,
    PluginError,
    StorageError,
    AdapterError,
)

__all__ = [
    "BotConfig",
    "MessageEvent",
    "IntentEvent", 
    "Context",
    "Plugin",
    "PluginManager",
    "Router",
    "BotError",
    "ConfigError",
    "PluginError",
    "StorageError",
    "AdapterError",
]
