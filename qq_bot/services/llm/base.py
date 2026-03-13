"""LLM 服务基类。

定义 LLM 服务的通用接口和数据结构。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Literal, Optional


@dataclass
class ChatMessage:
    """聊天消息。
    
    Attributes:
        role: 消息角色。
        content: 消息内容。
        name: 可选的发送者名称。
    """
    role: Literal["system", "user", "assistant"]
    content: str
    name: Optional[str] = None


@dataclass
class ChatResponse:
    """聊天响应。
    
    Attributes:
        content: 响应内容。
        usage: Token 使用情况。
        finish_reason: 结束原因。
        raw_response: 原始响应。
    """
    content: str
    usage: Dict[str, int]
    finish_reason: str = ""
    raw_response: Optional[Any] = None


class LLMService(ABC):
    """LLM 服务基类。
    
    所有 LLM 实现必须继承此类。
    
    Example:
        >>> class MyLLM(LLMService):
        ...     async def chat(self, messages, **kwargs):
        ...         # 实现聊天逻辑
        ...         return ChatResponse(content="Hello", usage={})
    """
    
    def __init__(self, api_key: str, model: str, **kwargs):
        """初始化 LLM 服务。
        
        Args:
            api_key: API 密钥。
            model: 模型名称。
            **kwargs: 其他配置参数。
        """
        self.api_key = api_key
        self.model = model
        self.config = kwargs
    
    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> ChatResponse:
        """非流式聊天。
        
        Args:
            messages: 消息列表。
            temperature: 温度参数。
            max_tokens: 最大生成 token 数。
            **kwargs: 其他参数。
            
        Returns:
            聊天响应。
        """
        raise NotImplementedError
    
    @abstractmethod
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式聊天。
        
        Args:
            messages: 消息列表。
            temperature: 温度参数。
            max_tokens: 最大生成 token 数。
            **kwargs: 其他参数。
            
        Yields:
            响应内容片段。
        """
        raise NotImplementedError
    
    async def health_check(self) -> bool:
        """健康检查。
        
        Returns:
            服务是否正常。
        """
        try:
            response = await self.chat(
                [ChatMessage(role="user", content="Hi")],
                max_tokens=5
            )
            return bool(response.content)
        except Exception:
            return False
