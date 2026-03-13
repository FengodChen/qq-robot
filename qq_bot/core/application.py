"""应用核心类。

整合配置管理、适配器、服务层、插件系统和 Agent，提供统一的入口点。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

from qq_bot.core.config import BotConfig
from qq_bot.core.context import Context, ServiceContainer
from qq_bot.core.events import (
    IntentEvent,
    LifecycleEvent,
    MessageEvent,
    ResponseEvent,
)
from qq_bot.core.exceptions import AdapterError, ConfigError, PluginError
from qq_bot.core.plugin import PluginManager
from qq_bot.plugins.chat import ChatPlugin
from qq_bot.plugins.summary import SummaryPlugin
from qq_bot.agent import IntentClassifier
from qq_bot.services.llm.base import LLMService
from qq_bot.services.llm.deepseek import DeepSeekService
from qq_bot.services.storage.message import MessageStore
from qq_bot.services.daily_summary import DailySummaryScheduler, DailySummaryConfig


class Application:
    """应用核心类。
    
    整合配置管理、适配器、服务层、插件系统和 Agent，
    提供统一的机器人应用生命周期管理。
    
    Example:
        >>> config = BotConfig.from_yaml("config.yaml")
        >>> app = Application(config)
        >>> await app.setup()
        >>> await app.run()
    
    Attributes:
        config: 机器人配置。
        ctx: 应用上下文。
        adapter: 协议适配器。
        plugin_manager: 插件管理器。
        intent_classifier: 意图分类器。
        running: 应用是否正在运行。
    """
    
    def __init__(self, config: BotConfig | None = None):
        """初始化应用。
        
        Args:
            config: 机器人配置，为 None 则从默认位置加载。
            
        Raises:
            ConfigError: 配置加载失败时。
        """
        if config is None:
            try:
                config = BotConfig.from_yaml("config.yaml")
            except Exception as e:
                raise ConfigError(f"加载默认配置失败: {e}")
        
        self.config = config
        self.ctx = Context(config=config)
        
        # 核心组件（延迟初始化）
        self._adapter: Any = None
        self._plugin_manager: PluginManager | None = None
        self._intent_classifier: IntentClassifier | None = None
        self._message_store: MessageStore | None = None
        self._llm_service: LLMService | None = None
        self._daily_summary_scheduler: DailySummaryScheduler | None = None
        
        # 用户信息缓存: {(group_id, user_id): {'sex': '...', 'nickname': '...', 'timestamp': 1234567890}}
        self._user_info_cache: dict[tuple[int, int], dict] = {}
        self._user_info_cache_ttl = 3600  # 缓存有效期1小时
        
        # 状态
        self._running = False
        self._initialized = False
        self._shutdown_event = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
    
    @property
    def running(self) -> bool:
        """应用是否正在运行。"""
        return self._running
    
    @property
    def adapter(self) -> Any:
        """获取适配器实例。"""
        return self._adapter
    
    @property
    def plugin_manager(self) -> PluginManager | None:
        """获取插件管理器实例。"""
        return self._plugin_manager
    
    @property
    def message_store(self) -> MessageStore | None:
        """获取消息存储实例。"""
        return self._message_store
    
    async def setup(self) -> None:
        """异步设置应用。
        
        初始化所有服务和组件，包括：
        1. 消息存储
        2. LLM 服务
        3. 更新上下文服务容器
        4. 意图分类器
        5. 插件系统
        6. 协议适配器
        
        Raises:
            ConfigError: 配置错误时。
            PluginError: 插件加载失败时。
        """
        print("[*] 正在初始化应用...")
        
        # 1. 初始化消息存储
        await self._setup_message_store()
        
        # 2. 初始化 LLM 服务
        await self._setup_llm_service()
        
        # 3. 更新上下文（必须在插件初始化之前）
        self.ctx.services = ServiceContainer(
            llm=self._llm_service,
            message_store=self._message_store,
        )
        
        # 4. 初始化意图分类器
        await self._setup_intent_classifier()
        
        # 5. 初始化插件系统
        await self._setup_plugins()
        
        # 6. 初始化适配器
        await self._setup_adapter()
        
        # 7. 初始化每日总结服务
        await self._setup_daily_summary()
        
        self._initialized = True
        print("[*] 应用初始化完成")
    
    async def _setup_message_store(self) -> None:
        """设置消息存储。"""
        try:
            data_dir = Path(self.config.storage.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            
            db_path = data_dir / "messages.db"
            self._message_store = MessageStore(
                db_path=db_path,
                retention_days=self.config.storage.message_retention_days
            )
            print(f"[*] 消息存储已初始化: {db_path}")
        except Exception as e:
            print(f"[!] 消息存储初始化失败: {e}")
            # 非关键组件，允许失败
    
    async def _setup_llm_service(self) -> None:
        """设置 LLM 服务。"""
        try:
            if self.config.llm.provider == "deepseek":
                self._llm_service = DeepSeekService(
                    api_key=self.config.llm.api_key,
                    model=self.config.llm.model,
                    base_url=self.config.llm.base_url,
                    timeout=self.config.llm.timeout,
                    max_retries=self.config.llm.max_retries,
                )
                print(f"[*] LLM 服务已初始化: {self.config.llm.provider}")
            else:
                print(f"[!] 不支持的 LLM 提供商: {self.config.llm.provider}")
        except Exception as e:
            print(f"[!] LLM 服务初始化失败: {e}")
            # 非关键组件，允许失败
    
    async def _setup_intent_classifier(self) -> None:
        """设置意图分类器。"""
        self._intent_classifier = IntentClassifier(
            llm_service=self._llm_service,
            debug_mode=self.config.debug.enabled
        )
        print("[*] 意图分类器已初始化")
    
    async def _setup_plugins(self) -> None:
        """设置插件系统。"""
        self._plugin_manager = PluginManager()
        
        # 注册插件
        self._plugin_manager.register("chat", ChatPlugin, description="AI聊天模式，支持人设定制和上下文记忆")
        self._plugin_manager.register("summary", SummaryPlugin, description="聊天记录总结模式，支持时间窗口选择")
        
        # 加载配置的插件
        for plugin_name in self.config.plugins:
            try:
                await self._plugin_manager.load(self.ctx, plugin_name)
                print(f"[*] 插件已加载: {plugin_name}")
            except Exception as e:
                print(f"[!] 加载插件 {plugin_name} 失败: {e}")
        
        print(f"[*] 插件系统已初始化，已加载 {len(self._plugin_manager._instances)} 个插件")
    
    async def _setup_adapter(self) -> None:
        """设置协议适配器。"""
        try:
            # 尝试导入 OneBot 适配器
            from qq_bot.adapters.onebot11 import OneBot11Adapter
            
            self._adapter = OneBot11Adapter(self.config)
            self._adapter.on_message(self._handle_message)
            print("[*] OneBot 适配器已初始化")
        except ImportError:
            print("[!] OneBot 适配器不可用，请安装 qq_bot.adapters.onebot")
            raise ConfigError("适配器初始化失败: OneBot 适配器不可用")
        except Exception as e:
            print(f"[!] 适配器初始化失败: {e}")
            raise AdapterError(f"适配器初始化失败: {e}")
    
    async def _setup_daily_summary(self) -> None:
        """设置每日总结服务。"""
        try:
            if not self.config.daily_summary.enabled:
                print("[*] 每日总结服务已禁用")
                return
            
            ds_config = DailySummaryConfig(
                enabled=self.config.daily_summary.enabled,
                group_id=self.config.daily_summary.group_id,
                max_tokens=self.config.daily_summary.max_tokens,
                hour=self.config.daily_summary.hour,
                minute=self.config.daily_summary.minute
            )
            
            self._daily_summary_scheduler = DailySummaryScheduler(
                config=ds_config,
                adapter=self._adapter,
                llm_service=self._llm_service,
                message_store=self._message_store
            )
            self._daily_summary_scheduler.start()
            print("[*] 每日总结服务已启动")
        except Exception as e:
            print(f"[!] 每日总结服务初始化失败: {e}")
    
    async def _get_user_info(self, group_id: int, user_id: int) -> dict:
        """获取用户信息（带缓存）。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            
        Returns:
            用户信息字典，包含 sex, nickname 等字段。
        """
        key = (group_id, user_id)
        now = time.time()
        
        # 检查缓存
        if key in self._user_info_cache:
            cached = self._user_info_cache[key]
            if now - cached.get('timestamp', 0) < self._user_info_cache_ttl:
                return cached
        
        # 缓存未命中，从 API 获取
        try:
            if group_id == 0:
                # 私聊用户
                info = await self._adapter.get_stranger_info(user_id)
            else:
                # 群成员
                info = await self._adapter.get_group_member_info(group_id, user_id)
            
            user_info = {
                'sex': info.get('sex', 'unknown'),
                'nickname': info.get('nickname', '未知'),
                'card': info.get('card', ''),
                'timestamp': now
            }
            
            self._user_info_cache[key] = user_info
            return user_info
            
        except Exception as e:
            if self.config.debug.enabled:
                print(f"[DEBUG] 获取用户信息失败: {e}")
            return {'sex': 'unknown', 'nickname': '未知', 'card': '', 'timestamp': now}
    
    async def run(self) -> None:
        """启动应用。
        
        自动初始化并启动适配器，开始接收和处理消息。
        此方法会阻塞直到应用关闭。
        
        Raises:
            AdapterError: 适配器启动失败时。
        """
        # 自动初始化（如果还未初始化）
        if not self._initialized:
            await self.setup()
        
        print("=" * 60)
        print("QQ 机器人应用启动")
        print("=" * 60)
        
        self._running = True
        
        # 启动后台任务
        cleanup_task = asyncio.create_task(self._periodic_cleanup())
        self._tasks.add(cleanup_task)
        cleanup_task.add_done_callback(self._tasks.discard)
        
        try:
            # 启动适配器
            print("[*] 启动协议适配器...")
            await self._adapter.start()
            
            # 等待关闭信号
            await self._shutdown_event.wait()
            
        except asyncio.CancelledError:
            print("[*] 运行被取消")
        except Exception as e:
            print(f"[!] 运行异常: {e}")
            raise
        finally:
            self._running = False
            await self.shutdown()
    
    async def shutdown(self) -> None:
        """优雅关闭应用。
        
        按正确顺序关闭所有组件：
        1. 停止适配器
        2. 卸载插件
        3. 关闭服务
        4. 清理资源
        """
        print("[*] 正在关闭应用...")
        
        # 停止接收新消息
        self._running = False
        self._shutdown_event.set()
        
        # 1. 停止每日总结服务
        if self._daily_summary_scheduler:
            try:
                self._daily_summary_scheduler.stop()
                print("[*] 每日总结服务已停止")
            except Exception as e:
                print(f"[!] 停止每日总结服务失败: {e}")
        
        # 2. 停止适配器
        if self._adapter:
            try:
                await self._adapter.stop()
                print("[*] 适配器已停止")
            except Exception as e:
                print(f"[!] 停止适配器失败: {e}")
        
        # 3. 卸载插件
        if self._plugin_manager:
            try:
                await self._plugin_manager.unload_all()
                print("[*] 插件已卸载")
            except Exception as e:
                print(f"[!] 卸载插件失败: {e}")
        
        # 4. 取消后台任务
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # 5. 清理消息存储
        if self._message_store:
            try:
                self._message_store.cleanup_old_messages()
                print("[*] 消息存储已清理")
            except Exception as e:
                print(f"[!] 清理消息存储失败: {e}")
        
        print("[*] 应用已关闭")
    
    def _is_at_me(self, raw_message: str) -> bool:
        """检查消息是否 @ 了机器人。
        
        Args:
            raw_message: 原始消息内容。
            
        Returns:
            是否被 @。
        """
        if not self._adapter or not self._adapter.state.self_id:
            return False
        self_id = self._adapter.state.self_id
        return f"[CQ:at,qq={self_id}]" in raw_message
    
    async def _handle_message(self, event: MessageEvent) -> None:
        """消息处理主流程。
        
        处理流程：
        1. 存储消息
        2. 检查是否需要响应（群聊需要 @）
        3. 意图识别
        4. 路由到对应插件或 Agent 处理
        5. 发送响应
        
        Args:
            event: 消息事件。
        """
        try:
            # 1. 存储消息（所有消息都存储）
            if self._message_store:
                self._store_message(event)
            
            # 2. 群聊消息检查是否 @ 机器人
            if event.is_group and not self._is_at_me(event.raw_message):
                # 群聊没有被 @，不处理
                return
            
            # 3. 意图识别
            intent_result = await self._intent_classifier.classify_intent(
                event.content,
                context={
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "message_id": event.message_id,
                }
            )
            
            if self.config.debug.enabled:
                print(f"[DEBUG] 意图识别: {intent_result.intent.value} (置信度: {intent_result.confidence:.2f})")
            
            # 转换为 IntentEvent
            intent_event = IntentEvent(
                intent=intent_result.intent,
                confidence=intent_result.confidence,
                parameters=intent_result.parameters,
                original_message=event,
                reason=intent_result.reason,
            )
            
            # 4. 路由处理
            response = await self._route_event(event, intent_event)
            
            # 4. 发送响应
            if response:
                await self._send_response(response)
                
        except Exception as e:
            print(f"[!] 处理消息失败: {e}")
            if self.config.debug.enabled:
                import traceback
                traceback.print_exc()
    
    def _store_message(self, event: MessageEvent) -> None:
        """存储消息到数据库。"""
        if not self._message_store:
            return
        
        try:
            self._message_store.add_message(
                msg_type=event.message_type,
                user_id=event.user_id,
                group_id=event.group_id,
                nickname=event.display_name,
                content=event.content,
                raw_message=event.raw_message,
                msg_id=event.message_id,
                timestamp=event.timestamp,
            )
        except Exception as e:
            if self.config.debug.enabled:
                print(f"[DEBUG] 存储消息失败: {e}")
    
    async def _route_event(
        self, 
        event: MessageEvent, 
        intent_event: IntentEvent
    ) -> ResponseEvent | None:
        """路由事件到对应处理器。
        
        根据意图分类结果，路由到对应的插件或处理器。
        流程：Agent判断意图 -> 根据意图路由 -> 执行对应功能
        
        Args:
            event: 原始消息事件。
            intent_event: 意图识别事件。
            
        Returns:
            响应事件，如果不需要响应则返回 None。
        """
        from qq_bot.agent import IntentType
        
        intent = intent_event.intent
        
        # 根据意图路由到对应插件
        if intent == IntentType.SUMMARIZE:
            # 路由到 summary 插件
            summary_plugin = self._plugin_manager.get("summary")
            if summary_plugin:
                try:
                    if event.is_group:
                        return await summary_plugin.on_group_message(self.ctx, event)
                    else:
                        return await summary_plugin.on_private_message(self.ctx, event)
                except Exception as e:
                    print(f"[!] Summary 插件处理失败: {e}")
            # 如果插件不可用，使用内置处理器
            return await self._handle_summarize(event, intent_event)
        
        # 其他意图（聊天相关）由 chat 插件处理
        chat_plugin = self._plugin_manager.get("chat")
        if chat_plugin:
            try:
                # 使用 handle_intent 方法，传入意图信息
                if hasattr(chat_plugin, 'handle_intent'):
                    response = await chat_plugin.handle_intent(
                        self.ctx, event, intent, intent_event.parameters
                    )
                    if response:
                        return response
                else:
                    # 兼容旧方式：直接调用消息处理方法
                    if event.is_group:
                        response = await chat_plugin.on_group_message(self.ctx, event)
                    else:
                        response = await chat_plugin.on_private_message(self.ctx, event)
                    
                    if response:
                        return response
            except Exception as e:
                print(f"[!] Chat 插件处理失败: {e}")
                if self.config.debug.enabled:
                    import traceback
                    traceback.print_exc()
        
        # Chat 插件未处理或不可用，使用内置处理器作为兜底
        intent_handlers = {
            IntentType.HELP: self._handle_help,
            IntentType.VIEW_AFFECTION: self._handle_view_affection,
            IntentType.VIEW_HISTORY: self._handle_view_history,
            IntentType.CLEAR_HISTORY: self._handle_clear_history,
            IntentType.GET_PERSONA: self._handle_get_persona,
            IntentType.SET_PERSONA: self._handle_set_persona,
            IntentType.RESET_PERSONA: self._handle_reset_persona,
        }
        
        handler = intent_handlers.get(intent)
        if handler:
            return await handler(event, intent_event)
        
        # 默认聊天意图
        return await self._handle_chat(event, intent_event)
    
    async def _send_response(self, response: ResponseEvent) -> None:
        """发送响应消息。
        
        Args:
            response: 响应事件。
        """
        if not self._adapter:
            return
        
        try:
            if response.target_group_id:
                await self._adapter.send_group_message(
                    group_id=response.target_group_id,
                    content=response.content,
                    reply_to=response.reply_to_message_id,
                    at_user=response.target_user_id if response.at_user else None
                )
            else:
                await self._adapter.send_private_message(
                    user_id=response.target_user_id,
                    content=response.content
                )
        except Exception as e:
            print(f"[!] 发送响应失败: {e}")
    
    # 意图处理器
    
    async def _handle_help(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理帮助请求。"""
        help_text = """我可以帮你做这些事哦：

【聊天功能】
· 直接和我对话聊天
· "更改人设成xxx" - 修改人设
· "查看人设" - 看当前人设
· "清除历史" - 清除对话记录
· "查看历史" - 看最近对话
· "好感度" - 查看我们的关系值

【总结功能】
· "总结一下" - 总结最近1小时的聊天
· "总结过去30分钟的聊天" - 支持任意时间范围

需要帮助就 @我 说出来吧~"""
        
        return ResponseEvent(
            content=help_text,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_view_affection(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理查看好感度请求。"""
        return ResponseEvent(
            content="好感度功能正在开发中~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_view_history(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理查看历史请求。"""
        return ResponseEvent(
            content="历史记录功能正在开发中~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_clear_history(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理清除历史请求。"""
        return ResponseEvent(
            content="已经清除我们的对话历史啦~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_get_persona(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理获取人设请求。"""
        return ResponseEvent(
            content=f"当前人设: {self.config.chat.system_prompt[:100]}...",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_set_persona(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理设置人设请求。"""
        return ResponseEvent(
            content="人设设置功能正在开发中~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_reset_persona(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理重置人设请求。"""
        return ResponseEvent(
            content="人设已恢复默认设置~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_summarize(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理总结请求。"""
        return ResponseEvent(
            content="总结功能正在开发中~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None,
            at_user=True
        )
    
    async def _handle_chat(
        self, 
        event: MessageEvent, 
        intent: IntentEvent
    ) -> ResponseEvent:
        """处理普通聊天请求。
        
        调用 LLM 服务进行 AI 对话。
        """
        # 检查 LLM 服务是否可用
        if not self._llm_service:
            return ResponseEvent(
                content="AI 服务暂时不可用，请检查配置。",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None,
                at_user=True
            )
        
        try:
            # 构建消息列表
            from qq_bot.services.llm.base import ChatMessage
            
            messages = []
            
            # 添加系统提示词（人设）
            system_prompt = self.config.chat.system_prompt
            if system_prompt:
                messages.append(ChatMessage(role="system", content=system_prompt))
            
            # 添加聊天要求
            chat_requirements = """称呼规则：
- 请根据系统提供的"当前对话者信息"中的名字和性别来决定如何称呼对方

聊天：
- 请你依据你对对方的好感度变更语气
- 当前内容与你之前的聊天内容保持非重复性

输出格式：
- 你在QQ中对话，因此不要使用MD格式，而是使用适合QQ聊天的格式
- 请避免长篇大论，控制字数在100字以内
"""
            messages.append(ChatMessage(role="system", content=chat_requirements))
            
            # 添加用户消息
            messages.append(ChatMessage(role="user", content=event.content))
            
            # 调用 LLM 服务
            response = await self._llm_service.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=self.config.chat.max_output_tokens or 300
            )
            
            # 返回 AI 回复
            return ResponseEvent(
                content=response.content,
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None,
                at_user=True
            )
            
        except Exception as e:
            print(f"[!] AI 对话失败: {e}")
            return ResponseEvent(
                content="抱歉，我暂时无法回答，请稍后再试。",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None,
                at_user=True
            )
    
    async def _periodic_cleanup(self) -> None:
        """定期清理任务。
        
        每 6 小时执行一次清理。
        """
        while self._running:
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), 
                    timeout=6 * 3600  # 6小时
                )
                break  # 收到关闭信号
            except asyncio.TimeoutError:
                # 执行清理
                if self._message_store:
                    try:
                        deleted = self._message_store.cleanup_old_messages()
                        if deleted > 0:
                            print(f"[*] 清理了 {deleted} 条过期消息")
                    except Exception as e:
                        print(f"[!] 清理消息失败: {e}")


def create_app(config_path: str | Path | None = None) -> Application:
    """创建应用实例的工厂函数。
    
    Args:
        config_path: 配置文件路径，为 None 则使用默认配置。
        
    Returns:
        应用实例。
        
    Example:
        >>> app = create_app("config.yaml")
        >>> await app.setup()
        >>> await app.run()
    """
    if config_path:
        config = BotConfig.from_yaml(config_path)
    else:
        config = None
    
    return Application(config)
