"""意图类型定义。

定义所有支持的用户意图类型和数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentType(Enum):
    """用户意图类型。
    
    Attributes:
        CHAT: 普通聊天
        SUMMARIZE: 总结聊天记录
        SET_PERSONA: 更改人设
        GET_PERSONA: 查看当前人设
        RESET_PERSONA: 恢复默认人设
        CLEAR_HISTORY: 清除历史
        VIEW_HISTORY: 查看历史
        VIEW_AFFECTION: 查看好感度
        CONFIRM: 确认操作
        CANCEL: 取消操作
        HELP: 帮助
        UNKNOWN: 未知意图
    """
    
    CHAT = "chat"                    # 普通聊天
    SUMMARIZE = "summarize"          # 总结聊天记录
    SET_PERSONA = "set_persona"      # 更改人设
    GET_PERSONA = "get_persona"      # 查看当前人设
    RESET_PERSONA = "reset_persona"  # 恢复默认人设
    CLEAR_HISTORY = "clear_history"  # 清除历史
    VIEW_HISTORY = "view_history"    # 查看历史
    VIEW_AFFECTION = "view_affection"  # 查看好感度
    CONFIRM = "confirm"              # 确认操作
    CANCEL = "cancel"                # 取消操作
    HELP = "help"                    # 帮助
    UNKNOWN = "unknown"              # 未知


@dataclass
class IntentResult:
    """意图识别结果。
    
    Attributes:
        intent: 识别到的意图类型
        confidence: 置信度 (0.0-1.0)
        parameters: 额外参数
        reason: 判断理由
    """
    
    intent: IntentType
    confidence: float
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    
    def __post_init__(self):
        """验证置信度范围。"""
        self.confidence = max(0.0, min(1.0, self.confidence))
    
    def is_confident(self, threshold: float = 0.7) -> bool:
        """检查置信度是否达到阈值。
        
        Args:
            threshold: 置信度阈值
            
        Returns:
            是否达到阈值
        """
        return self.confidence >= threshold
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。
        
        Returns:
            字典表示
        """
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "parameters": self.parameters,
            "reason": self.reason
        }
