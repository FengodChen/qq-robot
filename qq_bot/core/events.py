"""事件系统。

定义机器人在运行过程中产生的各种事件类型。
"""

from dataclasses import dataclass, field
from typing import Any, Literal
from datetime import datetime

from qq_bot.agent import IntentType


@dataclass(frozen=True)
class MessageEvent:
    """消息事件。
    
    当收到用户消息时触发。
    
    Attributes:
        message_type: 消息类型，"private" 或 "group"。
        user_id: 发送者 QQ 号。
        group_id: 群号（私聊为 0）。
        content: 消息纯文本内容。
        raw_message: 原始消息内容（包含 CQ 码等）。
        sender: 发送者详细信息。
        message_id: 消息 ID。
        timestamp: 消息时间戳。
    """
    message_type: Literal["private", "group"]
    user_id: int
    group_id: int
    content: str
    raw_message: str
    sender: dict[str, Any]
    message_id: int = 0
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    @property
    def is_group(self) -> bool:
        """是否为群消息。"""
        return self.message_type == "group"
    
    @property
    def is_private(self) -> bool:
        """是否为私聊消息。"""
        return self.message_type == "private"
    
    @property
    def display_name(self) -> str:
        """获取显示名称（优先使用群名片）。"""
        if self.is_group:
            return self.sender.get("card") or self.sender.get("nickname", "未知")
        return self.sender.get("nickname", "未知")
    
    @property
    def sex(self) -> str:
        """获取发送者性别。"""
        return self.sender.get("sex", "unknown")


@dataclass(frozen=True)
class IntentEvent:
    """意图识别事件。
    
    当 Agent 识别出用户意图时触发。
    
    Attributes:
        intent: 意图类型。
        confidence: 置信度（0-1）。
        parameters: 意图参数。
        original_message: 原始消息事件。
        reason: 识别原因说明。
    """
    intent: IntentType
    confidence: float
    parameters: dict[str, Any]
    original_message: MessageEvent
    reason: str = ""
    
    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """是否为高置信度识别结果。"""
        return self.confidence >= threshold


@dataclass(frozen=True)
class ResponseEvent:
    """响应事件。
    
    当机器人需要发送回复时触发。
    
    Attributes:
        content: 回复内容。
        target_user_id: 目标用户 ID。
        target_group_id: 目标群 ID（私聊为 0）。
        reply_to_message_id: 回复的消息 ID（可选）。
        at_user: 是否 @ 用户（群聊有效）。
    """
    content: str
    target_user_id: int
    target_group_id: int = 0
    reply_to_message_id: int | None = None
    at_user: bool = True


@dataclass(frozen=True)
class PluginLoadedEvent:
    """插件加载事件。
    
    当插件加载完成时触发。
    
    Attributes:
        plugin_name: 插件名称。
        plugin_instance: 插件实例。
    """
    plugin_name: str
    plugin_instance: Any


@dataclass(frozen=True)
class LifecycleEvent:
    """生命周期事件。
    
    机器人启动、停止等生命周期事件。
    
    Attributes:
        event_type: 生命周期类型。
        data: 附加数据。
    """
    event_type: Literal["startup", "shutdown", "connect", "disconnect"]
    data: dict[str, Any] = field(default_factory=dict)


# 事件类型联合类型
Event = MessageEvent | IntentEvent | ResponseEvent | PluginLoadedEvent | LifecycleEvent
