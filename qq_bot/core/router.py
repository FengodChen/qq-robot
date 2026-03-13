"""消息路由。

负责将消息分发到对应的处理器。
"""

from typing import Callable, Coroutine, Dict, List
from dataclasses import dataclass, field

from qq_bot.core.events import MessageEvent, ResponseEvent
from qq_bot.core.context import Context


HandlerType = Callable[[Context, MessageEvent], Coroutine[None, None, ResponseEvent | None]]


@dataclass
class Route:
    """路由规则。"""
    name: str
    handler: HandlerType
    priority: int = 0  # 优先级，数字越大越优先
    condition: Callable[[MessageEvent], bool] | None = None


class Router:
    """消息路由器。
    
    管理消息路由规则，按优先级分发消息。
    
    Example:
        >>> router = Router()
        >>> router.add_route("chat", chat_handler, priority=10)
        >>> response = await router.route(ctx, event)
    """
    
    def __init__(self):
        self._routes: List[Route] = []
        self._handlers: Dict[str, HandlerType] = {}
    
    def add_route(
        self,
        name: str,
        handler: HandlerType,
        priority: int = 0,
        condition: Callable[[MessageEvent], bool] | None = None
    ) -> None:
        """添加路由规则。
        
        Args:
            name: 路由名称。
            handler: 处理函数。
            priority: 优先级，数字越大越优先。
            condition: 可选的条件函数。
        """
        route = Route(name=name, handler=handler, priority=priority, condition=condition)
        self._routes.append(route)
        self._handlers[name] = handler
        # 按优先级排序
        self._routes.sort(key=lambda r: r.priority, reverse=True)
    
    def remove_route(self, name: str) -> bool:
        """移除路由规则。
        
        Args:
            name: 路由名称。
            
        Returns:
            是否成功移除。
        """
        self._routes = [r for r in self._routes if r.name != name]
        return self._handlers.pop(name, None) is not None
    
    async def route(
        self,
        ctx: Context,
        event: MessageEvent
    ) -> ResponseEvent | None:
        """路由消息。
        
        按优先级依次尝试各个处理器，直到有一个返回响应。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
            
        Returns:
            响应事件，如果没有处理器处理则返回 None。
        """
        for route in self._routes:
            # 检查条件
            if route.condition and not route.condition(event):
                continue
            
            try:
                response = await route.handler(ctx, event)
                if response is not None:
                    return response
            except Exception as e:
                # 记录错误但继续尝试其他处理器
                print(f"[!] 路由 {route.name} 处理失败: {e}")
                continue
        
        return None
