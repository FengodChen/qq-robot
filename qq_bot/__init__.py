"""QQ Bot - 基于 OneBot 11 协议的 QQ 机器人框架。

此包提供一个结构化的 QQ 机器人框架，支持：
- 插件化架构
- 自然语言意图识别
- 多 LLM 后端支持
- 统一配置管理

示例:
    >>> from qq_bot import create_app
    >>> from qq_bot.core.config import BotConfig
    >>> 
    >>> config = BotConfig.from_yaml("config.yaml")
    >>> app = create_app(config)
    >>> app.run()
"""

__version__ = "2.0.0"
__author__ = "QQ Bot Team"

from qq_bot.core.config import BotConfig
from qq_bot.core.application import Application

def create_app(config: BotConfig | None = None) -> Application:
    """创建应用实例。
    
    Args:
        config: 配置对象，如果为 None 则从默认位置加载。
        
    Returns:
        应用实例。
    """
    if config is None:
        config = BotConfig.from_yaml("config.yaml")
    return Application(config)


__all__ = [
    "__version__",
    "BotConfig", 
    "Application",
    "create_app",
]
