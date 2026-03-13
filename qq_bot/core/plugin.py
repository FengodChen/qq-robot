"""插件系统。

提供插件基类和注册机制。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, TypeVar
from dataclasses import dataclass, field

from qq_bot.core.events import MessageEvent, ResponseEvent
from qq_bot.core.context import Context


T = TypeVar("T", bound=type)


@dataclass
class PluginInfo:
    """插件信息。"""
    name: str
    description: str = ""
    version: str = "1.0"
    author: str = ""
    dependencies: list[str] = field(default_factory=list)


class Plugin(ABC):
    """插件基类。
    
    所有插件必须继承此类并实现必要的方法。
    
    Example:
        >>> class MyPlugin(Plugin):
        ...     @property
        ...     def info(self) -> PluginInfo:
        ...         return PluginInfo(name="my_plugin", description="我的插件")
        ...     
        ...     async def on_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        ...         if "hello" in event.content:
        ...             return ResponseEvent("Hello!", event.user_id, event.group_id)
        ...         return None
    """
    
    def __init__(self, ctx: Context):
        """初始化插件。
        
        Args:
            ctx: 应用上下文，包含配置和服务。
        """
        self.ctx = ctx
        self._initialized = False
    
    @property
    @abstractmethod
    def info(self) -> PluginInfo:
        """获取插件信息。"""
        raise NotImplementedError
    
    async def initialize(self) -> None:
        """初始化插件。
        
        在插件加载完成后调用，用于执行异步初始化操作。
        """
        self._initialized = True
    
    async def shutdown(self) -> None:
        """关闭插件。
        
        在应用关闭时调用，用于清理资源。
        """
        pass
    
    async def on_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        """处理消息事件。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
            
        Returns:
            响应事件，如果不需要响应则返回 None。
        """
        return None
    
    async def on_group_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        """处理群消息（可选实现）。
        
        默认调用 on_message，子类可覆盖。
        """
        return await self.on_message(ctx, event)
    
    async def on_private_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        """处理私聊消息（可选实现）。
        
        默认调用 on_message，子类可覆盖。
        """
        return await self.on_message(ctx, event)


class PluginManager:
    """插件管理器。
    
    负责插件的注册、加载和生命周期管理。
    
    Example:
        >>> manager = PluginManager()
        >>> manager.register("chat", ChatPlugin)
        >>> await manager.load_all(ctx)
    """
    
    def __init__(self):
        self._registry: dict[str, type[Plugin]] = {}
        self._instances: dict[str, Plugin] = {}
        self._info: dict[str, PluginInfo] = {}
    
    def register(
        self, 
        name: str, 
        plugin_class: type[Plugin],
        description: str = "",
        version: str = "1.0"
    ) -> type[Plugin]:
        """注册插件。
        
        Args:
            name: 插件名称（唯一标识）。
            plugin_class: 插件类。
            description: 插件描述。
            version: 插件版本。
            
        Returns:
            返回插件类本身（可用作装饰器）。
        """
        self._registry[name] = plugin_class
        self._info[name] = PluginInfo(
            name=name,
            description=description,
            version=version
        )
        return plugin_class
    
    def get(self, name: str) -> Plugin | None:
        """获取已加载的插件实例。"""
        return self._instances.get(name)
    
    def list_plugins(self) -> list[PluginInfo]:
        """获取所有已注册插件的信息。"""
        return list(self._info.values())
    
    async def load(self, ctx: Context, name: str) -> Plugin | None:
        """加载指定插件。
        
        Args:
            ctx: 应用上下文。
            name: 插件名称。
            
        Returns:
            插件实例，如果加载失败返回 None。
        """
        if name in self._instances:
            return self._instances[name]
        
        plugin_class = self._registry.get(name)
        if not plugin_class:
            raise ValueError(f"未注册的插件: {name}")
        
        try:
            instance = plugin_class(ctx)
            await instance.initialize()
            self._instances[name] = instance
            return instance
        except Exception as e:
            # 记录错误但不中断
            print(f"[!] 加载插件 {name} 失败: {e}")
            return None
    
    async def load_all(self, ctx: Context, names: list[str] | None = None) -> list[Plugin]:
        """加载多个插件。
        
        Args:
            ctx: 应用上下文。
            names: 要加载的插件名称列表，为 None 则加载所有已注册插件。
            
        Returns:
            成功加载的插件实例列表。
        """
        if names is None:
            names = list(self._registry.keys())
        
        loaded = []
        for name in names:
            instance = await self.load(ctx, name)
            if instance:
                loaded.append(instance)
        
        return loaded
    
    async def unload(self, name: str) -> bool:
        """卸载插件。
        
        Args:
            name: 插件名称。
            
        Returns:
            是否成功卸载。
        """
        instance = self._instances.pop(name, None)
        if instance:
            await instance.shutdown()
            return True
        return False
    
    async def unload_all(self) -> None:
        """卸载所有插件。"""
        for name in list(self._instances.keys()):
            await self.unload(name)


# 全局插件管理器实例
_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """获取全局插件管理器实例。"""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


def register_plugin(
    name: str,
    description: str = "",
    version: str = "1.0"
) -> Callable[[T], T]:
    """插件注册装饰器。
    
    Example:
        >>> @register_plugin("chat", description="聊天插件")
        ... class ChatPlugin(Plugin):
        ...     pass
    """
    def decorator(plugin_class: T) -> T:
        manager = get_plugin_manager()
        manager.register(name, plugin_class, description, version)  # type: ignore
        return plugin_class
    return decorator
