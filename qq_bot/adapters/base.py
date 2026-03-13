"""适配器基类。

定义消息适配器的通用接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, List, Optional

from qq_bot.core.events import MessageEvent


@dataclass
class ConnectionState:
    """连接状态。"""
    connected: bool = False
    reconnect_count: int = 0
    last_ping: float = 0.0
    self_id: int = 0  # 机器人自己的 QQ 号


class Adapter(ABC):
    """适配器基类。
    
    所有协议适配器必须继承此类。
    """
    
    def __init__(self, config: Any):
        """初始化适配器。
        
        Args:
            config: 配置对象。
        """
        self.config = config
        self.state = ConnectionState()
        self._message_handler: Optional[Callable[[MessageEvent], Coroutine]] = None
    
    def on_message(self, handler: Callable[[MessageEvent], Coroutine]) -> None:
        """注册消息处理器。
        
        Args:
            handler: 异步消息处理函数。
        """
        self._message_handler = handler
    
    @abstractmethod
    async def start(self) -> None:
        """启动适配器，开始接收消息。"""
        raise NotImplementedError
    
    @abstractmethod
    async def stop(self) -> None:
        """停止适配器。"""
        raise NotImplementedError
    
    @abstractmethod
    async def send_private_message(self, user_id: int, content: str) -> bool:
        """发送私聊消息。
        
        Args:
            user_id: 用户 QQ。
            content: 消息内容。
            
        Returns:
            是否发送成功。
        """
        raise NotImplementedError
    
    @abstractmethod
    async def send_group_message(
        self,
        group_id: int,
        content: str,
        at_user: Optional[int] = None,
        reply_to: Optional[int] = None
    ) -> bool:
        """发送群消息。
        
        Args:
            group_id: 群号。
            content: 消息内容。
            at_user: @ 的用户（可选）。
            reply_to: 回复的消息 ID（可选）。
            
        Returns:
            是否发送成功。
        """
        raise NotImplementedError
    
    @abstractmethod
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        """获取群成员信息。
        
        Args:
            group_id: 群号。
            user_id: 用户 QQ。
            
        Returns:
            成员信息字典。
        """
        raise NotImplementedError
    
    @abstractmethod
    async def get_stranger_info(self, user_id: int) -> dict:
        """获取陌生人信息。
        
        Args:
            user_id: 用户 QQ。
            
        Returns:
            用户信息字典。
        """
        raise NotImplementedError
