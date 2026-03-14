"""聊天插件主模块。

实现 AI 聊天功能，支持人设定制、好感度系统和对话上下文管理。
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from qq_bot.core.plugin import Plugin, PluginInfo
from qq_bot.core.context import Context
from qq_bot.core.events import MessageEvent, ResponseEvent
from qq_bot.services.llm.base import LLMService, ChatMessage
from qq_bot.services.storage.message import MessageStore, get_message_store
from qq_bot.utils.debug_logger import log_llm_context, log_compact_debug

from qq_bot.plugins.chat.conversation import ConversationManager
from qq_bot.plugins.chat.persona import PersonaManager, PersonaConfig
from qq_bot.plugins.chat.affection import AffectionManager


class ChatPlugin(Plugin):
    """聊天插件。
    
    提供 AI 聊天功能，支持：
    - 群聊和私聊消息处理
    - 人设定制（/setprompt, /getprompt, /reset）
    - 好感度系统（/affection）
    - 对话上下文管理（/history, /clean）
    - 帮助命令（/help, /ping）
    
    Attributes:
        conversation: 对话上下文管理器。
        persona: 人设管理器。
        affection: 好感度管理器。
        commands: 命令处理器映射。
    
    Example:
        >>> plugin = ChatPlugin(ctx)
        >>> response = await plugin.on_message(ctx, event)
    """
    
    def __init__(self, ctx: Context):
        """初始化聊天插件。
        
        Args:
            ctx: 应用上下文。
        """
        super().__init__(ctx)
        
        # 获取服务
        self.llm: Optional[LLMService] = ctx.services.llm
        self.message_store: Optional[MessageStore] = ctx.services.message_store
        
        # 获取配置
        # 优先从嵌套的 chat 配置读取，兼容旧版直接读取
        chat_config = getattr(ctx.config, "chat", None)
        if chat_config:
            self.max_context = getattr(chat_config, "max_context", 5)
            self.max_output_tokens = getattr(chat_config, "max_output_tokens", 300)
            self.max_input_tokens = getattr(chat_config, "max_input_tokens", 100)
            self.group_context_messages = getattr(chat_config, "group_context_messages", 10)
            self.system_prompt = getattr(chat_config, "system_prompt", "")
        else:
            # 兼容旧版配置
            self.max_context = getattr(ctx.config, "max_context", 5)
            self.max_output_tokens = getattr(ctx.config, "max_output_tokens", 300)
            self.max_input_tokens = getattr(ctx.config, "max_input_tokens", 100)
            self.group_context_messages = getattr(ctx.config, "group_context_messages", 10)
            self.system_prompt = getattr(ctx.config, "system_prompt", "")
        
        self.debug_mode = getattr(ctx.config, "debug", None)
        if self.debug_mode:
            self.debug_mode = getattr(self.debug_mode, "enabled", False)
        else:
            self.debug_mode = getattr(ctx.config, "debug_mode", False)
        
        # 初始化组件
        self.conversation = ConversationManager(max_context=self.max_context)
        self.persona = PersonaManager(default_prompt=self.system_prompt)
        self.affection = AffectionManager(llm_service=self.llm)
        
        # 命令处理器
        self.commands: Dict[str, Tuple[str, callable]] = {
            "/help": ("显示帮助菜单", self._cmd_help),
            "/ping": ("测试连通性", self._cmd_ping),
            "/clean": ("清除对话历史", self._cmd_clean),
            "/history": ("显示对话历史", self._cmd_history),
            "/setprompt": ("更改人设", self._cmd_setprompt),
            "/getprompt": ("查看当前人设", self._cmd_getprompt),
            "/reset": ("恢复默认人设", self._cmd_reset),
            "/affection": ("查看好感度", self._cmd_affection),
        }
        
        # 等待人设设置的状态
        self._pending_prompts: Dict[Tuple[int, int], bool] = {}
    
    @property
    def info(self) -> PluginInfo:
        """获取插件信息。"""
        return PluginInfo(
            name="chat",
            description="AI聊天模式，支持人设定制和上下文记忆",
            version="1.0.0",
            author="NapCat Bot",
            dependencies=[]
        )
    
    async def initialize(self) -> None:
        """初始化插件。"""
        await super().initialize()
        print(f"[*] ChatPlugin 初始化完成")
        print(f"[*] 配置: max_context={self.max_context}, max_output_tokens={self.max_output_tokens}")
    
    async def shutdown(self) -> None:
        """关闭插件。"""
        # 清理资源
        self._pending_prompts.clear()
    
    async def on_message(self, ctx: Context, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理消息事件。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
        
        Returns:
            响应事件，如果不需要响应则返回 None。
        """
        content = event.content.strip()
        
        # 处理命令
        if content.startswith("/"):
            return await self._handle_command(content, event)
        
        # 检查是否在等待人设设置
        key = (event.group_id, event.user_id)
        if key in self._pending_prompts:
            return await self._handle_set_persona(event)
        
        # 处理普通消息
        return await self._handle_chat(event)
    
    async def on_group_message(self, ctx: Context, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理群消息。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        # 清理 @ 标记（application 层已检查是否 @ 机器人）
        content = re.sub(r"\[CQ:at,qq=\d+\]", "", event.content).strip()
        if not content:
            return None
        
        # 创建新的事件对象
        event = MessageEvent(
            message_type="group",
            user_id=event.user_id,
            group_id=event.group_id,
            content=content,
            raw_message=event.raw_message,
            sender=event.sender,
            message_id=event.message_id,
            timestamp=event.timestamp
        )
        
        return await self.on_message(ctx, event)
    
    async def on_private_message(self, ctx: Context, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理私聊消息。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        return await self.on_message(ctx, event)
    
    async def handle_intent(
        self, 
        ctx: Context, 
        event: MessageEvent, 
        intent: "IntentType",
        parameters: dict = None
    ) -> Optional[ResponseEvent]:
        """基于意图处理消息。
        
        这是 Agent 意图分类后的入口点，根据意图类型执行相应操作。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
            intent: 意图类型。
            parameters: 意图参数。
        
        Returns:
            响应事件。
        """
        from qq_bot.agent.intents import IntentType
        
        parameters = parameters or {}
        
        # 根据意图类型分发处理
        if intent == IntentType.CHAT:
            return await self._handle_chat(event)
        
        elif intent == IntentType.SET_PERSONA:
            return await self._handle_set_persona_intent(event)
        
        elif intent == IntentType.GET_PERSONA:
            return await self._cmd_getprompt(event, "")
        
        elif intent == IntentType.RESET_PERSONA:
            return await self._cmd_reset(event, "")
        
        elif intent == IntentType.CLEAR_HISTORY:
            return await self._cmd_clean(event, "")
        
        elif intent == IntentType.VIEW_HISTORY:
            return await self._cmd_history(event, "")
        
        elif intent == IntentType.VIEW_AFFECTION:
            return await self._cmd_affection(event, "")
        
        elif intent == IntentType.HELP:
            return await self._cmd_help(event, "")
        
        # 默认作为普通聊天处理
        return await self._handle_chat(event)
    
    async def _handle_command(self, content: str, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理命令。
        
        Args:
            content: 消息内容。
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        # 提取命令和参数
        parts = content.split(maxsplit=1)
        cmd = parts[0].lower()
        
        if cmd in self.commands:
            _, handler = self.commands[cmd]
            return await handler(event, parts[1] if len(parts) > 1 else "")
        
        # 处理自然语言命令
        return await self._handle_natural_command(content, event)
    
    async def _handle_natural_command(self, content: str, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理自然语言命令。
        
        Args:
            content: 消息内容。
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        content_lower = content.lower()
        
        # 清除历史
        if any(kw in content_lower for kw in ["清除历史", "清理历史", "清空记录"]):
            return await self._cmd_clean(event, "")
        
        # 查看历史
        if any(kw in content_lower for kw in ["查看历史", "历史记录", "对话历史"]):
            return await self._cmd_history(event, "")
        
        # 更改人设
        if any(kw in content_lower for kw in ["更改人设", "设置人设", "新人设"]):
            return await self._cmd_setprompt(event, "")
        
        # 查看人设
        if any(kw in content_lower for kw in ["查看人设", "当前人设", "我的人设"]):
            return await self._cmd_getprompt(event, "")
        
        # 重置人设
        if any(kw in content_lower for kw in ["恢复默认", "重置人设", "恢复人设"]):
            return await self._cmd_reset(event, "")
        
        # 查看好感度
        if any(kw in content_lower for kw in ["好感度", "亲密度", "喜欢程度"]):
            return await self._cmd_affection(event, "")
        
        return None
    
    async def _handle_set_persona_intent(self, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理设置人设意图（Agent 意图分类后调用）。
        
        从自然语言消息中提取人设内容并设置。
        
        Args:
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        content = event.content.strip()
        
        # 安全检查：排除自我介绍句式
        if content.startswith('我是') or content.startswith('我叫') or \
           content.startswith('他是') or content.startswith('她是'):
            # 这不是设置人设，而是普通聊天
            return await self._handle_chat(event)
        
        # 使用 LLM 提取并修正人设内容
        # 例如："你现在的人设是一个可爱的JK少女" -> "你是一个可爱的JK少女"
        persona_text = await self._extract_persona_with_llm(content)
        
        if not persona_text or len(persona_text) < 3:
            return ResponseEvent(
                content="请告诉我要变成什么人设，比如:\"更改人设成温柔的大姐姐\"",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 安全检查：提取的内容不应该只是人称代词或简单的身份词
        simple_identities = ['姐姐', '哥哥', '妹妹', '弟弟', '妈妈', '爸爸', '老师', '医生', '我', '你', '他', '她']
        if persona_text in simple_identities:
            # 可能是误判，作为普通聊天处理
            return await self._handle_chat(event)
        
        # 验证人设
        valid, error = self.persona.validate_prompt(persona_text)
        if not valid:
            return ResponseEvent(
                content=f"❌ 人设设置失败: {error}",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 清除历史记录和好感度
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        # 设置新人设
        self.conversation.set_custom_prompt(event.group_id, event.user_id, persona_text)
        
        preview = persona_text[:50] + "..." if len(persona_text) > 50 else persona_text
        
        msg = (
            "✨ 【人设已更新】✨\n"
            "━━━━━━━━━━━━━━\n"
            f"📝 {preview}\n"
            "━━━━━━━━━━━━━━\n"
            "🗑️ 对话历史已清除\n"
            "💕 好感度已重置"
        )
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    def _extract_persona_text(self, message: str) -> str:
        """从消息中提取人设内容（规则版本，作为备用）。
        
        使用简单的规则提取人设描述，去除指令性词汇。
        
        Args:
            message: 用户消息。
        
        Returns:
            提取到的人设描述。
        """
        # 定义需要去除的指令性前缀
        prefixes = [
            '更改人设', '修改人设', '人设改成', '人设改为', '设定人设',
            '设定为', '设定成', '变成', '扮演', '设置为', '设置成',
            '改为', '改成', '设为', '换成人设', '换人设', '新人设'
        ]
        
        text = message.strip()
        
        # 去除前缀
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        
        # 去除常见的连接词和标点
        connectors = ['成', '为', '是', '：', ':', '，', ',']
        for conn in connectors:
            if text.startswith(conn):
                text = text[len(conn):].strip()
        
        return text
    
    async def _extract_persona_with_llm(self, message: str) -> str:
        """使用 LLM 从消息中提取并修正人设内容。
        
        将用户的自然语言指令转换为纯粹的人设描述。
        例如：
        - "你现在的人设是一个可爱的JK少女" -> "你是一个可爱的JK少女"
        - "更改人设成温柔的大姐姐" -> "你是一个温柔的大姐姐"
        
        Args:
            message: 用户消息。
        
        Returns:
            提取并修正后的人设描述，失败则返回规则提取的结果。
        """
        if not self.llm:
            # LLM 不可用，回退到规则提取
            return self._extract_persona_text(message)
        
        system_prompt = """你是一个人设提取助手。你的任务是从用户的指令中提取纯粹的人设描述，并将其转换为以"你是"开头的角色设定语句。

【任务说明】
1. 从用户消息中提取人设的核心描述
2. 将描述转换为以"你是"开头的角色设定语句
3. 去除所有指令性词汇，保留纯粹的人设内容

【示例】

输入: "你现在的人设是一个可爱的JK少女"
输出: {"persona_text": "你是一个可爱的JK少女", "success": true}

输入: "更改人设成温柔的大姐姐"
输出: {"persona_text": "你是一个温柔的大姐姐，说话温柔体贴，会照顾人", "success": true}

输入: "扮演一只傲娇的猫娘"
输出: {"persona_text": "你是一只傲娇的猫娘，有着猫耳和尾巴，说话带着喵~的口癖", "success": true}

输入: "设定为知识渊博的教授"
输出: {"persona_text": "你是一位知识渊博的教授，说话严谨专业，喜欢引用经典", "success": true}

输入: "你现在是狂热的电竞观众精通LOL比赛"
输出: {"persona_text": "你是一位狂热的电竞观众，精通LOL比赛，对赛事和选手如数家珍", "success": true}

【规则】
1. 必须以"你是"开头
2. 补充适当的性格/行为描述，使人设更完整（2-3句话）
3. 去除所有指令性词汇：更改人设、修改人设、设定为、变成、扮演等
4. 如果提取失败或没有有效内容，返回success: false

只返回JSON格式，不要有任何其他说明。"""
        
        try:
            from qq_bot.services.llm.base import ChatMessage
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=message)
            ]
            
            if self.debug_mode:
                log_compact_debug("人设提取", request=message[:50])
            
            response = await self.llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=200
            )
            
            # 解析 JSON 响应
            import json
            import re
            content = response.content
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                if result.get('success') and result.get('persona_text'):
                    extracted = result.get('persona_text', '').strip()
                    if self.debug_mode:
                        log_compact_debug("人设提取结果", result=extracted[:50])
                    return extracted
                    
        except Exception as e:
            if self.debug_mode:
                print(f"[!] LLM 人设提取失败: {e}")
        
        # 如果 LLM 提取失败，回退到规则提取
        return self._extract_persona_text(message)
    
    async def _handle_set_persona(self, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理人设设置。
        
        Args:
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        key = (event.group_id, event.user_id)
        self._pending_prompts.pop(key, None)
        
        persona_text = event.content.strip()
        
        # 验证人设
        valid, error = self.persona.validate_prompt(persona_text)
        if not valid:
            return ResponseEvent(
                content=f"❌ 人设设置失败: {error}",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 清除历史记录
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        # 设置新人设
        self.conversation.set_custom_prompt(event.group_id, event.user_id, persona_text)
        
        preview = persona_text[:50] + "..." if len(persona_text) > 50 else persona_text
        
        msg = (
            "✨ 【人设已更新】✨\n"
            "━━━━━━━━━━━━━━\n"
            f"📝 {preview}\n"
            "━━━━━━━━━━━━━━\n"
            "🗑️ 对话历史已清除\n"
            "💕 好感度已重置"
        )
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _handle_chat(self, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理聊天消息。
        
        流程：
        1. 评估用户消息对好感度的影响（变化值和原因）
        2. 将好感度变化信息加入prompt
        3. 调用LLM生成回复
        4. 更新好感度和对话历史
        
        Args:
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        # 检查消息长度
        if not self._check_message_length(event.content):
            return ResponseEvent(
                content=f"消息太长了，请控制在{self.max_input_tokens}字以内~",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 检查 LLM 服务
        if not self.llm:
            return ResponseEvent(
                content="抱歉，AI 服务暂时不可用。",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        try:
            # 1. 先评估好感度变化（在生成回复之前）
            change, change_reason = await self._evaluate_affection_before_reply(event)
            
            # 2. 构建消息列表（包含好感度变化信息）
            messages = await self._build_messages(event, change, change_reason)
            
            # Debug 输出完整上下文
            if self.debug_mode:
                log_llm_context("聊天插件上下文", messages)
            
            # 3. 调用 LLM 生成回复
            response = await self.llm.chat(
                messages=messages,
                max_tokens=self.max_output_tokens,
                temperature=0.7
            )
            
            reply = response.content.strip()
            
            # 4. 更新对话历史
            self.conversation.add_message(
                event.group_id, event.user_id, "user", event.content, event.display_name
            )
            self.conversation.add_message(
                event.group_id, event.user_id, "assistant", reply, "音理"
            )
            
            # 5. 应用好感度变化并生成最终回复（包含好感度显示）
            final_reply = await self._apply_affection(event, reply, change, change_reason)
            
            # 6. 存储机器人消息
            await self._store_bot_message(event, reply)
            
            return ResponseEvent(
                content=final_reply,
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None,
                at_user=event.is_group
            )
            
        except Exception as e:
            print(f"[!] 聊天处理失败: {e}")
            return ResponseEvent(
                content="抱歉，处理出错了，请稍后再试。",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
    
    async def _evaluate_affection_before_reply(
        self, 
        event: MessageEvent
    ) -> Tuple[int, str]:
        """在生成回复前评估好感度变化。
        
        Args:
            event: 消息事件。
        
        Returns:
            (变化值, 变化原因)
        """
        # 获取人设文本
        custom_prompt = self.conversation.get_custom_prompt(event.group_id, event.user_id)
        persona_text = custom_prompt if custom_prompt else self.system_prompt
        
        # 获取当前好感度值
        current_value = self.affection.get_affection_value(event.group_id, event.user_id)
        
        # 检查是否已有人设喜好配置，如果没有则生成
        preferences = self.affection.get_persona_preferences(persona_text)
        if preferences is None:
            print(f"[*] 首次使用此人设，正在生成喜好/雷点配置...")
            preferences = await self.affection.generate_persona_preferences(persona_text)
        
        # 获取对话历史（最近5条）
        conversation_history = self.conversation.get_context(event.group_id, event.user_id)
        
        # 使用 LLM 评估好感度变化（结合对话上下文）
        change, reason = await self._evaluate_affection_with_llm_for_user_message(
            user_message=event.content,
            persona_text=persona_text,
            current_affection=current_value,
            conversation_history=conversation_history
        )
        
        if self.debug_mode:
            print(f"[DEBUG] 预评估好感度变化: user_msg={event.content[:30]}..., "
                  f"change={change}, reason={reason}")
        
        return change, reason
    
    async def _evaluate_affection_with_llm_for_user_message(
        self,
        user_message: str,
        persona_text: str,
        current_affection: int,
        conversation_history: List[Dict] = None
    ) -> Tuple[int, str]:
        """使用 LLM 评估用户消息对好感度的影响。
        
        Args:
            user_message: 用户消息。
            persona_text: 当前人设文本。
            current_affection: 当前好感度值。
            conversation_history: 对话历史记录。
        
        Returns:
            (变化值, 原因)。变化值范围为 -5 到 +5。
        """
        # 获取人设喜好配置
        preferences = self.affection.get_persona_preferences(persona_text)
        if preferences is None:
            # 如果没有配置，使用规则评估
            return self.affection.evaluate_affection_change(user_message, "", None)
        
        # 如果没有 LLM 服务，回退到规则评估
        if not self.llm:
            return self.affection.evaluate_affection_change(
                user_message, "",
                {
                    "interests": preferences.interests,
                    "favorite_things": preferences.favorite_things,
                    "dislikes": preferences.dislikes
                }
            )
        
        # 获取关系等级描述
        level = self.affection.get_affection_level(current_affection)
        level_descriptions = {
            "死敌": "你们是死敌关系，彼此憎恨",
            "憎恨": "你憎恨这个用户",
            "厌恶": "你厌恶这个用户",
            "反感": "你对这个用户有反感",
            "疏离": "你们关系疏离，有距离感",
            "陌生": "你们刚刚认识，彼此还不太了解",
            "初识": "有过几次简单交流，正在互相了解",
            "熟悉": "比较了解彼此，关系比较自然",
            "友好": "关系不错的朋友，相处融洽",
            "亲密": "很亲近的关系，彼此信任",
            "至交": "非常重要的关系，如同至交好友",
            "灵魂伴侣": "灵魂交融的关系，彼此是唯一的存在"
        }
        level_desc = level_descriptions.get(level, "关系状态未知")
        
        # 格式化对话历史（取最近5条，不包括当前消息）
        history_text = ""
        if conversation_history:
            recent_history = conversation_history[-5:]  # 最近5条
            history_lines = []
            for msg in recent_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    history_lines.append(f"用户: {content}")
                elif role == "assistant":
                    history_lines.append(f"你: {content}")
            if history_lines:
                history_text = "\n".join(history_lines)
        
        if not history_text:
            history_text = "（暂无对话历史）"
        
        try:
            system_prompt = f"""你是一个好感度评估助手。请根据对话上下文评估用户消息对好感度的影响。

【当前人设】
{persona_text}

【人设喜好/雷点】
- 兴趣爱好: {', '.join(preferences.interests)}
- 特别喜欢: {', '.join(preferences.favorite_things)}
- 雷点/讨厌: {', '.join(preferences.dislikes)}

【当前关系状态】
- 关系等级: {level}（{current_affection}/100）
- 状态描述: {level_desc}

【评估规则】
1. 好感度变化范围: -5 到 +5
2. 评估标准:
   +5: 极度感动/被深深打动
   +3~+4: 非常愉快/被关心
   +1~+2: 比较愉快/友好
   0: 中性（普通对话）
   -1~-2: 轻微不悦
   -3~-4: 明显不悦
   -5: 极度愤怒/伤心

【输出格式】
只返回 JSON 格式：
{{
  "change": 变化值(-5到5),
  "reason": "变化原因（10字以内）"
}}"""
            
            user_prompt = f"""【对话历史】
{history_text}

【用户最新消息】
{user_message}

请结合以上对话历史和当前关系状态，评估这条最新消息对好感度的影响。"""
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt)
            ]
            
            response = await self.llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=200
            )
            
            # 解析 JSON 响应
            content = response.content.strip()
            
            if self.debug_mode:
                log_compact_debug("好感度评估响应", content=content[:200])
            
            # 尝试多种方式解析 JSON
            result = None
            
            # 方式1: 直接解析（如果返回的是纯 JSON）
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                pass
            
            # 方式2: 使用正则表达式提取 JSON 对象
            if result is None:
                json_match = re.search(r'\{[\s\S]*?\}', content)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass
            
            # 方式3: 尝试提取 markdown 代码块中的 JSON
            if result is None:
                code_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
                if code_match:
                    try:
                        result = json.loads(code_match.group(1))
                    except json.JSONDecodeError:
                        pass
            
            # 如果成功解析
            if result is not None:
                change = max(-5, min(5, result.get("change", 0)))
                reason = result.get("reason", "评估完成")
                if not reason or reason == "评估完成":
                    reason = result.get("reasoning", result.get("cause", result.get("explanation", "评估完成")))
                return change, reason
            
            # 解析失败，打印原始响应以便调试
            print(f"[!] 无法解析 LLM 响应，原始内容: {content[:200]}")
            raise ValueError(f"无法从 LLM 响应中解析 JSON，响应内容: {content[:100]}...")
                
        except Exception as e:
            print(f"[!] LLM 好感度预评估失败: {e}")
            # 回退到规则评估
            return self.affection.evaluate_affection_change(
                user_message, "",
                {
                    "interests": preferences.interests,
                    "favorite_things": preferences.favorite_things,
                    "dislikes": preferences.dislikes
                }
            )
    
    async def _build_messages(
        self, 
        event: MessageEvent, 
        pending_change: int = 0, 
        change_reason: str = ""
    ) -> List[ChatMessage]:
        """构建 LLM 消息列表。
        
        Args:
            event: 消息事件。
            pending_change: 预计的好感度变化值。
            change_reason: 好感度变化原因。
        
        Returns:
            消息列表。
        """
        messages: List[ChatMessage] = []
        
        # 1. 系统提示词 - 基础人设
        custom_prompt = self.conversation.get_custom_prompt(event.group_id, event.user_id)
        system_prompt = custom_prompt if custom_prompt else self.system_prompt
        messages.append(ChatMessage(role="system", content=system_prompt))
        
        # 2. 系统提示词 - 好感度状态（包含预计变化）
        affection_prompt = self._build_affection_prompt_with_change(
            event.group_id, event.user_id, pending_change, change_reason
        )
        messages.append(ChatMessage(role="system", content=affection_prompt))
        
        # 3. 系统提示词 - 聊天要求
        chat_requirements = """称呼规则：
- 请根据系统提供的"当前对话者信息"中的名字和性别来决定如何称呼对方

聊天：
- 你可视聊天氛围，主动并自然地和对方聊及今天的新闻内容
- 请你依据你对对方的好感度变更语气
- 当前内容与你之前的聊天内容保持非重复性
- 你可以获取到群聊相关记录，其中"与<昵称>对话的分身"代表着是你的分身和<昵称>的聊天记录

输出格式：
- 你在QQ中对话，因此不要使用MD格式，而是使用适合QQ聊天的格式
- 请避免长篇大论，控制字数在100字以内
"""
        messages.append(ChatMessage(role="system", content=chat_requirements))
        
        # 4. 群聊上下文
        if event.is_group and self.message_store:
            group_context = await self._get_group_context(event)
            if group_context:
                messages.append(ChatMessage(role="system", content=group_context))
        
        # 5. 当前对话者信息
        user_info_parts = []
        display_name = event.display_name
        if display_name:
            user_info_parts.append(f"名字：{display_name}")
        if event.sex and event.sex != "unknown":
            sex_str = "男" if event.sex == "male" else "女" if event.sex == "female" else "未知"
            user_info_parts.append(f"性别：{sex_str}")
        
        if user_info_parts:
            user_info = "，".join(user_info_parts)
            messages.append(ChatMessage(role="system", content=f"当前对话者信息：{user_info}"))
        
        # 6. 当前时间
        current_time = datetime.now().strftime("%Y年%m月%d日 %H时%M分%S秒")
        messages.append(ChatMessage(role="system", content=f"当前时间：{current_time}"))
        
        # 7. 对话历史
        context = self.conversation.get_context(event.group_id, event.user_id)
        for msg in context:
            messages.append(ChatMessage(
                role=msg["role"],
                content=msg["content"]
            ))
        
        # 8. 当前用户消息
        messages.append(ChatMessage(role="user", content=event.content))
        
        return messages
    
    async def _get_group_context(self, event: MessageEvent) -> str:
        """获取群聊上下文。
        
        Args:
            event: 消息事件。
        
        Returns:
            群聊上下文文本。
        """
        if not self.message_store:
            return ""
        
        try:
            # 获取最近群消息
            recent_messages = self.message_store.get_messages_since(
                since=event.timestamp - 3600,  # 最近1小时
                group_id=event.group_id,
                limit=self.group_context_messages
            )
            
            if not recent_messages:
                return ""
            
            lines = [f"【群聊上下文（最近{len(recent_messages)}条）】"]
            
            # 缓存用户昵称
            user_nickname_cache = {}
            for msg in recent_messages:
                if msg.nickname:
                    user_nickname_cache[msg.user_id] = msg.nickname
            
            # 处理消息
            for msg in reversed(recent_messages):
                if msg.user_id == self.ctx.config.self_id if hasattr(self.ctx.config, "self_id") else 0:
                    sender_name = "音理"
                    if msg.target_user_id:
                        target_name = user_nickname_cache.get(msg.target_user_id, f"用户{msg.target_user_id}")
                        sender_name = f"与{target_name}对话的分身"
                else:
                    sender_name = msg.nickname if msg.nickname else f"用户{msg.user_id}"
                
                content = msg.content
                
                # 处理引用消息
                if msg.reply_to:
                    replied_msg = self.message_store.get_message_by_id(msg.reply_to)
                    if replied_msg:
                        replied_name = replied_msg.nickname if replied_msg.nickname else f"用户{replied_msg.user_id}"
                        content = f"[引用{replied_name}:{replied_msg.content[:30]}...]{content}"
                
                lines.append(f"{sender_name}: {content}")
            
            lines.append("【以上是群聊历史，供你参考当前聊天氛围】")
            return "\n".join(lines)
            
        except Exception as e:
            print(f"[!] 获取群聊上下文失败: {e}")
            return ""
    
    def _build_affection_prompt_with_change(
        self,
        group_id: int,
        user_id: int,
        pending_change: int,
        change_reason: str
    ) -> str:
        """构建包含预计好感度变化的prompt。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            pending_change: 预计的好感度变化值。
            change_reason: 变化原因。
        
        Returns:
            好感度相关的系统提示词。
        """
        value = self.affection.get_affection_value(group_id, user_id)
        level = self.affection.get_affection_level(value)
        
        # 根据好感度等级生成语气描述
        tone_descriptions = {
            "死敌": "你对用户怀有极致的憎恨，语气冰冷刺骨，每一个字都像淬了毒的刀刃。",
            "憎恨": "你对用户怀有强烈的负面情绪，语气极其冷漠、尖锐，充满敌意。",
            "厌恶": "你对用户有明显的反感，态度冷淡疏离，不愿多交流。",
            "反感": "你对用户有负面印象，态度冷淡客气但保持距离。",
            "疏离": "你对用户保持明显的距离感，态度冷淡客气但疏离。",
            "陌生": "你对用户完全是陌生人的态度，回答礼貌但极其疏远正式。",
            "初识": "你对用户保持基本的礼貌友好，但仍然有明显的距离感。",
            "熟悉": "你对用户比较放松，会偶尔主动关心，语气较为亲切自然。",
            "友好": "你对用户很友善，会使用轻松活泼的语气，经常会开玩笑。",
            "亲密": "你对用户非常亲近，语气温柔宠溺，充满关心和依赖。",
            "至交": "你对用户毫无保留，语气极其亲密宠溺甚至带点任性。",
            "灵魂伴侣": "你对用户的爱意已经超越了世俗的理解，达到了灵魂交融的境界。"
        }
        
        tone = tone_descriptions.get(level, "你对用户保持中立态度。")
        
        # 构建好感度变化描述
        if pending_change > 0:
            change_desc = f"用户的这句话让你感到开心，好感度预计增加{pending_change}点（{change_reason}）。"
        elif pending_change < 0:
            change_desc = f"用户的这句话让你感到不悦，好感度预计减少{abs(pending_change)}点（{change_reason}）。"
        else:
            change_desc = "用户的这句话没有引起你好感度的明显变化。"
        
        prompt = f"""【好感度状态】
当前等级: {level}（{value}/100）
语气设定: {tone}

【本次对话好感度变化预期】
{change_desc}

注意: 
1. 你的回应必须严格符合上述语气设定，通过用词、语气、态度自然体现关系状态
2. 根据好感度变化预期调整你的情绪反应，但不要直接提及"好感度"这个概念
3. 负好感度时要体现冷淡、疏离或不耐烦；陌生时要体现距离感；高好感度时要体现亲密和依赖"""
        
        return prompt
    
    async def _apply_affection(
        self, 
        event: MessageEvent, 
        reply: str,
        pre_evaluated_change: int = 0,
        change_reason: str = ""
    ) -> str:
        """应用好感度系统并生成带有好感度信息的回复。
        
        Args:
            event: 消息事件。
            reply: 原始回复。
            pre_evaluated_change: 预评估的好感度变化值。
            change_reason: 变化原因。
        
        Returns:
            添加好感度信息后的回复。
        """
        # 使用预评估的变化值更新好感度
        change = pre_evaluated_change
        reason = change_reason if change_reason else "用户互动"
        
        if change != 0:
            new_val, actual_change, _ = self.affection.update_affection(
                event.group_id, event.user_id, change, reason, event.content, reply
            )
            
            if actual_change != 0:
                print(f"[*] 好感度变化: {event.user_id} -> {new_val} ({actual_change:+d}, {reason})")
        
        # 获取更新后的好感度状态
        current_value = self.affection.get_affection_value(event.group_id, event.user_id)
        level = self.affection.get_affection_level(current_value)
        
        # 构建好感度显示信息（方案C：引导线连接）
        # 格式：💕 好感度 <关系> （分数/100） （增加/不变/下降emoji）<好感度变化>
        #       ╰─ 原因：<好感度变化简短原因>
        
        if change > 0:
            change_emoji = "📈"
            change_text = f"+{change}"
        elif change < 0:
            change_emoji = "📉"
            change_text = f"{change}"
        else:
            change_emoji = "➡️"
            change_text = "0"
        
        # 格式化原因（限制长度）
        display_reason = reason[:15] + "..." if len(reason) > 15 else reason
        
        affection_line = f"\n\n────────────\n💕 {level}（{current_value}/100） {change_emoji}{change_text}"
        if change != 0:
            affection_line += f"\n╰─ {display_reason}"
        
        return reply + affection_line
    
    async def _store_bot_message(self, event: MessageEvent, reply: str) -> None:
        """存储机器人消息。
        
        Args:
            event: 消息事件。
            reply: 回复内容。
        """
        if not self.message_store:
            return
        
        try:
            self_id = getattr(self.ctx.config, "self_id", 0)
            self.message_store.add_message(
                msg_type="group" if event.is_group else "private",
                user_id=self_id,
                group_id=event.group_id if event.is_group else 0,
                nickname="音理",
                content=reply,
                raw_message=reply,
                msg_id=int(time.time() * 1000),
                timestamp=time.time(),
                reply_to=None,
                target_user_id=event.user_id
            )
        except Exception as e:
            print(f"[!] 存储机器人消息失败: {e}")
    
    def _check_message_length(self, content: str) -> bool:
        """检查消息长度。
        
        Args:
            content: 消息内容。
        
        Returns:
            是否在限制范围内。
        """
        # 简单估算：中文1字≈1token，英文1词≈1token
        length = len(content)
        return length <= self.max_input_tokens * 3  # 粗略估算
    

    
    # ========== 命令处理器 ==========
    
    async def _cmd_help(self, event: MessageEvent, args: str) -> ResponseEvent:
        """帮助命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        help_text = "【小音理的帮助菜单】\n"
        help_text += "-" * 15 + "\n"
        for cmd, (desc, _) in self.commands.items():
            help_text += f"{cmd} - {desc}\n"
        help_text += "-" * 15 + "\n"
        help_text += "也可以直接说:\n"
        help_text += "更改人设/清除历史/查看历史"
        
        return ResponseEvent(
            content=help_text,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_ping(self, event: MessageEvent, args: str) -> ResponseEvent:
        """Ping 命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        return ResponseEvent(
            content="pong! 🏓",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_clean(self, event: MessageEvent, args: str) -> ResponseEvent:
        """清除历史命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        return ResponseEvent(
            content="已清除对话历史，好感度已重置！",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_history(self, event: MessageEvent, args: str) -> ResponseEvent:
        """历史记录命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        history_text = self.conversation.get_formatted_history(
            event.group_id, event.user_id, max_messages=15
        )
        
        return ResponseEvent(
            content=history_text,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_setprompt(self, event: MessageEvent, args: str) -> ResponseEvent:
        """设置人设命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        key = (event.group_id, event.user_id)
        self._pending_prompts[key] = True
        
        msg = (
            "请直接发送新人设内容，我会直接生效（无需确认）。\n"
            "注意：更改人设将清空对话历史！"
        )
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_getprompt(self, event: MessageEvent, args: str) -> ResponseEvent:
        """获取人设命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        custom_prompt = self.conversation.get_custom_prompt(event.group_id, event.user_id)
        
        if custom_prompt:
            preview = custom_prompt[:100] + "..." if len(custom_prompt) > 100 else custom_prompt
            msg = f"【当前人设】(自定义)\n{preview}\n\n使用 /reset 恢复默认人设"
        else:
            default = self.system_prompt[:100] + "..." if len(self.system_prompt) > 100 else self.system_prompt
            msg = f"【当前人设】(默认)\n{default}\n\n使用 /setprompt 更改人设"
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_reset(self, event: MessageEvent, args: str) -> ResponseEvent:
        """重置人设命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        self.conversation.clear_custom_prompt(event.group_id, event.user_id)
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        reset_msg = (
            "🔄 【已恢复默认人设】🔄\n"
            "━━━━━━━━━━━━━━\n"
            "✅ 人设已恢复为默认值\n"
            "🗑️ 对话历史已清除\n"
            "💕 好感度已重置\n"
            "━━━━━━━━━━━━━━\n"
            "🌟 让我们重新开始吧~"
        )
        
        return ResponseEvent(
            content=reset_msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_affection(self, event: MessageEvent, args: str) -> ResponseEvent:
        """好感度命令。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        info = self.affection.format_affection_info(event.group_id, event.user_id)
        hint = self.affection.get_personality_hint()
        
        return ResponseEvent(
            content=f"{info}\n\n{hint}",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
