"""聊天插件模块。

提供 AI 聊天功能，支持人设定制、好感度系统和对话上下文管理。
"""

from qq_bot.plugins.chat.plugin import ChatPlugin
from qq_bot.plugins.chat.conversation import ConversationManager
from qq_bot.plugins.chat.persona import PersonaManager
from qq_bot.plugins.chat.affection import AffectionManager

__all__ = [
    "ChatPlugin",
    "ConversationManager",
    "PersonaManager",
    "AffectionManager",
]
