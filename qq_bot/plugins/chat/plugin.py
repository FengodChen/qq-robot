"""聊天插件主模块。

实现 AI 聊天功能，支持人设定制、好感度系统和对话上下文管理。
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from qq_bot.core.plugin import Plugin, PluginInfo
from qq_bot.core.context import Context
from qq_bot.core.events import MessageEvent, ResponseEvent
from qq_bot.services.llm.base import LLMService, ChatMessage
from qq_bot.services.storage.message import MessageStore, get_message_store
from qq_bot.utils.debug_logger import log_llm_context, log_compact_debug
from qq_bot.utils.text import convert_at_to_text

from qq_bot.plugins.chat.conversation import ConversationManager
from qq_bot.plugins.chat.persona import PersonaManager, PersonaConfig
from qq_bot.plugins.chat.affection import AffectionManager


@dataclass
class PendingConfirmation:
    """待确认的操作。
    
    Attributes:
        operation: 操作类型 ('reset_persona', 'clear_history', 'set_persona')
        expire_time: 过期时间戳
        event: 原始消息事件（用于获取用户信息和上下文）
        data: 附加数据（如人设文本等）
    """
    operation: str
    expire_time: float
    event: MessageEvent
    data: Dict[str, Any]


class ChatPlugin(Plugin):
    """聊天插件。
    
    提供 AI 聊天功能，支持：
    - 群聊和私聊消息处理
    - 人设定制（更改人设、查看人设、恢复默认）
    - 好感度系统
    - 对话上下文管理（清除历史、查看历史）
    - 帮助系统
    
    所有指令通过 LLM 进行意图识别，支持自然语言表达。
    
    Attributes:
        conversation: 对话上下文管理器。
        persona: 人设管理器。
        affection: 好感度管理器。
    
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
        
        # 获取新闻服务
        self.news_service = ctx.services.news
        
        # 获取配置
        self.config = ctx.config
        chat_config = ctx.config.chat
        self.max_output_tokens = chat_config.max_output_tokens
        self.max_input_tokens = chat_config.max_input_tokens
        self.group_context_messages = chat_config.group_context_messages
        self.system_prompt = chat_config.system_prompt
        
        # max_context 现在表示"对话轮数"，每轮包含 user + assistant 两条消息
        # 从 storage 配置读取
        storage_config = ctx.config.storage
        self.max_context = storage_config.conversation_max_context
        
        # 从 debug 配置读取调试模式
        self.debug_mode = ctx.config.debug.enabled
        
        # 从配置读取提示词
        self.prompts = ctx.config.prompts.chat
        
        # 初始化组件
        self.conversation = ConversationManager(max_context=self.max_context)
        self.persona = PersonaManager(default_prompt=self.system_prompt)
        self.affection = AffectionManager(
            llm_service=self.llm, 
            prompts=ctx.config.prompts.affection,
            tone_descriptions=self.prompts.tone_descriptions
        )
        
        # 等待人设设置的状态
        self._pending_prompts: Dict[Tuple[int, int], bool] = {}
        
        # 待确认的敏感操作
        self._pending_confirmations: Dict[Tuple[int, int], PendingConfirmation] = {}
        
        # 确认超时时间（从配置读取，默认300秒）
        self._confirmation_timeout = getattr(ctx.config.chat, 'confirmation_timeout', 300)
    
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
        
        所有消息通过 Agent 意图分类器处理，统一使用 LLM 进行意图识别。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
        
        Returns:
            响应事件，如果不需要响应则返回 None。
        """
        content = event.content.strip()
        
        # 检查是否在等待人设设置
        key = (event.group_id, event.user_id)
        if key in self._pending_prompts:
            return await self._handle_set_persona(event)
        
        # 处理普通消息（命令和自然语言指令都通过 Agent 意图分类器处理）
        return await self._handle_chat(event)
    
    async def on_group_message(self, ctx: Context, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理群消息。
        
        Args:
            ctx: 请求上下文。
            event: 消息事件。
        
        Returns:
            响应事件。
        """
        # 转换 @ 标记为可读格式，保留提及信息
        # 从群聊上下文中收集用户信息用于昵称映射
        user_map = await self._build_user_map_from_context(event.group_id)
        self_id = getattr(self.ctx.config, "self_id", None)
        content = convert_at_to_text(event.content, user_map=user_map, self_id=self_id, self_name="我").strip()
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
        
        elif intent == IntentType.CONFIRM:
            return await self._handle_confirm(event)
        
        elif intent == IntentType.CANCEL:
            return await self._handle_cancel(event)
        
        elif intent == IntentType.HELP:
            return await self._cmd_help(event, "")
        
        # 默认作为普通聊天处理
        return await self._handle_chat(event)
    

    
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
        
        # 检查是否已有待确认的相同操作
        pending = self._get_pending_confirmation(event)
        if pending and pending.operation == 'set_persona':
            return ResponseEvent(
                content='⚠️ 您已经有一个设置人设的待确认操作，请回复"确认"执行或"取消"放弃~',
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 设置待确认状态
        self._set_pending_confirmation(event, 'set_persona', {'persona_text': persona_text})
        
        # 计算超时分钟数
        timeout_minutes = self._confirmation_timeout // 60
        
        # 显示人设预览
        preview = persona_text[:80] + "..." if len(persona_text) > 80 else persona_text
        
        confirm_msg = (
            "⚠️ 【敏感操作确认】⚠️\n"
            "━━━━━━━━━━━━━━\n"
            "您即将设置新人设：\n\n"
            f"📝 {preview}\n\n"
            "此操作将：\n"
            "🗑️ 清除现有对话历史\n"
            "💕 重置好感度为初始值\n"
            "✨ 应用新的角色设定\n\n"
            '请回复"确认"继续，或回复"取消"放弃\n'
            f"（{timeout_minutes}分钟内有效）"
        )
        
        return ResponseEvent(
            content=confirm_msg,
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
        
        system_prompt = self.prompts.persona_extraction
        
        try:
            from qq_bot.services.llm.base import ChatMessage
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=message)
            ]
            
            response = await self.llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=300
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
            return 0, ""
        
        # 如果没有 LLM 服务，返回无变化
        if not self.llm:
            return 0, ""
        
        # 获取关系等级描述
        # ========== 修改：使用人设对应的等级名称和描述 ==========
        level = self.affection.get_affection_level(current_affection, persona_text)
        level_desc = self.affection.get_level_description(level, persona_text)
        # ========== 修改结束 ==========
        
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
            system_prompt = self.prompts.affection_evaluation.format(
                persona_text=persona_text,
                interests=', '.join(preferences.interests) if preferences.interests else "",
                favorite_things=', '.join(preferences.favorite_things) if preferences.favorite_things else "",
                dislikes=', '.join(preferences.dislikes) if preferences.dislikes else "",
                level=level,
                current_affection=current_affection,
                level_desc=level_desc
            )
            
            user_prompt = f"""【对话历史】
{history_text}

【用户最新消息】
{user_message}

请结合以上对话历史和当前关系状态，评估这条最新消息对好感度的影响。"""
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt)
            ]
            
            # Debug 输出请求信息
            if self.debug_mode:
                log_llm_context("好感度评估请求", messages)
            
            response = await self.llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=500
            )
            
            # 解析 JSON 响应
            content = response.content.strip()
            
            # Debug 输出原始响应
            if self.debug_mode:
                log_compact_debug("好感度评估原始响应", content=repr(content[:500]))
            
            # 尝试多种方式解析 JSON
            result = None
            
            # 方式1: 直接解析（如果返回的是纯 JSON）
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                pass
            
            # 方式2: 使用正则表达式提取 JSON 对象（非贪婪匹配）
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
            
            # Debug 输出当前解析状态
            if self.debug_mode:
                log_compact_debug("好感度解析", content_preview=repr(content[:100]))
            
            # 方式4: 尝试修复不完整的 JSON（提取 change 和 reason 字段）
            if result is None:
                change_match = re.search(r'"change"\s*[:\=]\s*([+-]?\d+)', content)
                reason_match = re.search(r'"reason"\s*[:\=]\s*"([^"]*)"', content)
                if change_match:
                    try:
                        change = int(change_match.group(1))
                        reason = reason_match.group(1) if reason_match else "评估完成"
                        result = {"change": change, "reason": reason}
                        if self.debug_mode:
                            log_compact_debug("好感度解析方式4成功", change=change)
                    except (ValueError, AttributeError):
                        pass
            
            # 方式5: 处理极端不完整的情况（如 '\n  "change"' 只有字段名）
            if result is None:
                # 检查是否有 change 字段的痕迹但无法提取数值
                if '"change"' in content or '"change":' in content or '"change" :' in content:
                    # 尝试从任何数字中提取可能的 change 值
                    all_numbers = re.findall(r'[+-]?\d+', content)
                    if all_numbers:
                        try:
                            # 取第一个数字作为 change 值
                            change = max(-5, min(5, int(all_numbers[0])))
                            result = {"change": change, "reason": "评估完成"}
                            if self.debug_mode:
                                log_compact_debug("好感度解析方式5成功(有数字)", change=change)
                        except ValueError:
                            pass
                    else:
                        # 完全无法解析，默认无变化
                        result = {"change": 0, "reason": "评估完成"}
                        if self.debug_mode:
                            log_compact_debug("好感度解析方式5成功(无数字)", change=0)
            
            # 如果成功解析
            if result is not None:
                change = max(-5, min(5, result.get("change", 0)))
                reason = result.get("reason", "评估完成")
                if not reason or reason == "评估完成":
                    reason = result.get("reasoning", result.get("cause", result.get("explanation", "评估完成")))
                return change, reason
            
            # 解析失败
            raise ValueError(f"无法从 LLM 响应中解析 JSON")
                
        except Exception as e:
            if self.debug_mode:
                print(f"[!] LLM 好感度预评估失败: {e}")
            # 返回无变化
            return 0, ""
    
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
        # ========== 修改：传递人设信息 ==========
        persona_text = custom_prompt if custom_prompt else None
        affection_prompt = self._build_affection_prompt_with_change(
            event.group_id, event.user_id, pending_change, change_reason, persona_text
        )
        # ========== 修改结束 ==========
        messages.append(ChatMessage(role="system", content=affection_prompt))
        
        # 3. 系统提示词 - 聊天要求
        messages.append(ChatMessage(role="system", content=self.prompts.chat_requirements))
        
        # 3.5 新闻内容（根据概率配置）
        if self.news_service and self.config.news.enabled:
            import random
            if random.random() < self.config.news.probability:
                try:
                    news_content = await self.news_service.fetch_news()
                    if news_content:
                        news_prompt = f"【今日新闻参考】\n{news_content}\n\n你可以根据聊天氛围，自然地提及相关新闻内容。"
                        messages.append(ChatMessage(role="system", content=news_prompt))
                except Exception as e:
                    if self.debug_mode:
                        print(f"[!] 获取新闻失败: {e}")
        
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
            # 获取最近群消息（扩大到包含当前消息的时间范围）
            recent_messages = self.message_store.get_messages_since(
                since=event.timestamp - 3600,  # 最近1小时
                group_id=event.group_id,
                limit=self.group_context_messages + 5  # 多获取几条确保包含当前消息
            )
            
            # 过滤：只保留当前消息时间戳之前的消息（包括当前消息）
            recent_messages = [
                msg for msg in recent_messages 
                if msg.timestamp <= event.timestamp + 1  # +1秒容错
            ][:self.group_context_messages]
            
            if not recent_messages:
                return ""
            
            if self.debug_mode:
                print(f"[DEBUG] 群聊上下文: 获取到 {len(recent_messages)} 条消息")
            
            lines = [f"【群聊上下文（最近{len(recent_messages)}条）】"]
            
            # 缓存用户昵称
            user_nickname_cache = {}
            for msg in recent_messages:
                if msg.nickname:
                    user_nickname_cache[msg.user_id] = msg.nickname
            
            # 处理消息（排除当前触发消息）
            for msg in reversed(recent_messages):
                # 跳过当前这条触发回复的消息
                if msg.msg_id == event.message_id:
                    continue
                
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
        change_reason: str,
        persona_text: str = None  # 新增参数
    ) -> str:
        """构建包含预计好感度变化的prompt。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            pending_change: 预计的好感度变化值。
            change_reason: 变化原因。
            persona_text: 人设文本，用于获取对应的好感度配置。
        
        Returns:
            好感度相关的系统提示词。
        """
        value = self.affection.get_affection_value(group_id, user_id)
        # ========== 修改：使用人设对应的等级名称 ==========
        level = self.affection.get_affection_level(value, persona_text)
        # ========== 修改结束 ==========
        
        # 根据人设获取语气描述
        # ========== 修改：使用人设对应的语气描述 ==========
        tone = self.affection.get_tone_description(level, persona_text)
        # ========== 修改结束 ==========
        
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
        
        # ========== 新增：记录旧值用于满好感度检查 ==========
        old_value = self.affection.get_affection_value(event.group_id, event.user_id)
        # ========== 新增结束 ==========
        
        if change != 0:
            new_val, actual_change, _ = self.affection.update_affection(
                event.group_id, event.user_id, change, reason, event.content, reply
            )
            
            if actual_change != 0:
                print(f"[*] 好感度变化: {event.user_id} -> {new_val} ({actual_change:+d}, {reason})")
        
        # 获取更新后的好感度状态
        current_value = self.affection.get_affection_value(event.group_id, event.user_id)
        
        # ========== 新增：检查满好感度奖励 ==========
        reward_msg = self.affection.check_max_affection_reward(
            event.group_id, event.user_id, old_value, current_value
        )
        # ========== 新增结束 ==========
        
        # ========== 修改：使用人设对应的等级名称 ==========
        custom_prompt = self.conversation.get_custom_prompt(event.group_id, event.user_id)
        persona_text = custom_prompt if custom_prompt else None
        level = self.affection.get_affection_level(current_value, persona_text)
        # ========== 修改结束 ==========
        
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
        
        # ========== 新增：追加奖励消息 ==========
        if reward_msg:
            affection_line += reward_msg
        # ========== 新增结束 ==========
        
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
    
    async def _build_user_map_from_context(self, group_id: int) -> dict[int, str]:
        """从群聊上下文中构建用户 ID 到昵称的映射。
        
        Args:
            group_id: 群组 ID。
            
        Returns:
            用户 ID 到昵称的映射字典。
        """
        user_map: dict[int, str] = {}
        
        if not self.message_store:
            return user_map
        
        try:
            # 获取最近群消息中的用户信息
            recent_messages = self.message_store.get_messages_since(
                since=time.time() - 3600,  # 最近1小时
                group_id=group_id,
                limit=50
            )
            
            for msg in recent_messages:
                if msg.nickname:
                    user_map[msg.user_id] = msg.nickname
            
            if self.debug_mode and user_map:
                print(f"[DEBUG] 构建用户映射: {len(user_map)} 个用户")
                
        except Exception as e:
            if self.debug_mode:
                print(f"[!] 构建用户映射失败: {e}")
        
        return user_map
    
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
        return ResponseEvent(
            content=self.prompts.help_text,
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
        
        先请求用户确认，确认后才执行清除操作。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        # 检查是否已有待确认的相同操作
        pending = self._get_pending_confirmation(event)
        if pending and pending.operation == 'clear_history':
            return ResponseEvent(
                content='⚠️ 您已经有一个清除对话历史的待确认操作，请回复"确认"执行或"取消"放弃~',
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 设置待确认状态
        self._set_pending_confirmation(event, 'clear_history')
        
        # 计算超时分钟数
        timeout_minutes = self._confirmation_timeout // 60
        
        confirm_msg = (
            "⚠️ 【敏感操作确认】⚠️\n"
            "━━━━━━━━━━━━━━\n"
            "您即将执行：清除对话历史\n\n"
            "此操作将：\n"
            "🗑️ 清除所有对话历史\n"
            "💕 重置好感度为初始值\n\n"
            f'请回复"确认"继续，或回复"取消"放弃\n'
            f"（{timeout_minutes}分钟内有效）"
        )
        
        return ResponseEvent(
            content=confirm_msg,
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
            msg = f"【当前人设】(自定义)\n{preview}\n\n对我说「恢复默认」可以恢复默认人设"
        else:
            default = self.system_prompt[:100] + "..." if len(self.system_prompt) > 100 else self.system_prompt
            msg = f"【当前人设】(默认)\n{default}\n\n对我说「更改人设成xxx」可以修改人设"
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_reset(self, event: MessageEvent, args: str) -> ResponseEvent:
        """重置人设命令。
        
        先请求用户确认，确认后才执行重置操作。
        
        Args:
            event: 消息事件。
            args: 命令参数。
        
        Returns:
            响应事件。
        """
        # 检查是否已有待确认的相同操作
        pending = self._get_pending_confirmation(event)
        if pending and pending.operation == 'reset_persona':
            return ResponseEvent(
                content='⚠️ 您已经有一个恢复默认人设的待确认操作，请回复"确认"执行或"取消"放弃~',
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 设置待确认状态
        self._set_pending_confirmation(event, 'reset_persona')
        
        # 计算超时分钟数
        timeout_minutes = self._confirmation_timeout // 60
        
        confirm_msg = (
            "⚠️ 【敏感操作确认】⚠️\n"
            "━━━━━━━━━━━━━━\n"
            "您即将执行：恢复默认人设\n\n"
            "此操作将：\n"
            "🗑️ 清除所有对话历史\n"
            "💕 重置好感度为初始值\n"
            "🔄 恢复机器人默认人设\n\n"
            f'请回复"确认"继续，或回复"取消"放弃\n'
            f"（{timeout_minutes}分钟内有效）"
        )
        
        return ResponseEvent(
            content=confirm_msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _cmd_affection(self, event: MessageEvent, args: str) -> ResponseEvent:
        """处理好感度查询命令。"""
        # ========== 修改：获取人设信息 ==========
        custom_prompt = self.conversation.get_custom_prompt(event.group_id, event.user_id)
        persona_text = custom_prompt if custom_prompt else None
        info = self.affection.format_affection_info(event.group_id, event.user_id, persona_text)
        # ========== 修改结束 ==========
        
        hint = self.affection.get_personality_hint()
        
        content = f"{info}\n\n{hint}"
        
        return ResponseEvent(
            content=content,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )

    
    # ========== 敏感操作确认机制 ==========
    
    def _get_confirmation_key(self, event: MessageEvent) -> Tuple[int, int]:
        """生成确认键。
        
        Args:
            event: 消息事件。
            
        Returns:
            (group_id, user_id) 元组。
        """
        return (event.group_id, event.user_id)
    
    def _cleanup_expired_confirmations(self) -> None:
        """清理过期的待确认操作。"""
        current_time = time.time()
        expired_keys = [
            key for key, pending in self._pending_confirmations.items()
            if pending.expire_time < current_time
        ]
        for key in expired_keys:
            del self._pending_confirmations[key]
    
    def _get_pending_confirmation(self, event: MessageEvent) -> Optional[PendingConfirmation]:
        """获取待确认的操作（会自动清理过期记录）。
        
        Args:
            event: 消息事件。
            
        Returns:
            待确认的操作，如果没有则返回 None。
        """
        self._cleanup_expired_confirmations()
        key = self._get_confirmation_key(event)
        return self._pending_confirmations.get(key)
    
    def _set_pending_confirmation(
        self, 
        event: MessageEvent, 
        operation: str, 
        data: Dict[str, Any] = None
    ) -> None:
        """设置待确认的操作。
        
        Args:
            event: 消息事件。
            operation: 操作类型。
            data: 附加数据。
        """
        key = self._get_confirmation_key(event)
        expire_time = time.time() + self._confirmation_timeout
        self._pending_confirmations[key] = PendingConfirmation(
            operation=operation,
            expire_time=expire_time,
            event=event,
            data=data or {}
        )
    
    def _clear_pending_confirmation(self, event: MessageEvent) -> None:
        """清除待确认的操作。
        
        Args:
            event: 消息事件。
        """
        key = self._get_confirmation_key(event)
        self._pending_confirmations.pop(key, None)
    
    async def _handle_confirm(self, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理确认意图。
        
        Args:
            event: 消息事件。
            
        Returns:
            响应事件。
        """
        pending = self._get_pending_confirmation(event)
        
        if not pending:
            # 没有待确认的操作
            return ResponseEvent(
                content="当前没有需要确认的待执行操作~",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 清除待确认状态
        self._clear_pending_confirmation(event)
        
        # 根据操作类型执行相应逻辑
        if pending.operation == 'reset_persona':
            return await self._execute_reset_persona(event)
        elif pending.operation == 'clear_history':
            return await self._execute_clear_history(event)
        elif pending.operation == 'set_persona':
            persona_text = pending.data.get('persona_text', '')
            return await self._execute_set_persona(event, persona_text)
        
        return ResponseEvent(
            content="操作已确认执行！",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _handle_cancel(self, event: MessageEvent) -> Optional[ResponseEvent]:
        """处理取消意图。
        
        Args:
            event: 消息事件。
            
        Returns:
            响应事件。
        """
        pending = self._get_pending_confirmation(event)
        
        if not pending:
            # 没有待确认的操作
            return ResponseEvent(
                content="当前没有需要取消的待执行操作~",
                target_user_id=event.user_id,
                target_group_id=event.group_id,
                reply_to_message_id=event.message_id if event.is_group else None
            )
        
        # 清除待确认状态
        self._clear_pending_confirmation(event)
        
        # 获取操作的中文名称
        operation_names = {
            'reset_persona': '恢复默认人设',
            'clear_history': '清除对话历史',
            'set_persona': '设置新人设'
        }
        op_name = operation_names.get(pending.operation, '操作')
        
        return ResponseEvent(
            content=f"✅ 已取消{op_name}操作~",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _execute_reset_persona(self, event: MessageEvent) -> ResponseEvent:
        """执行人设重置（确认后）。"""
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
    
    async def _execute_clear_history(self, event: MessageEvent) -> ResponseEvent:
        """执行清除历史（确认后）。"""
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        return ResponseEvent(
            content="🗑️ 已清除对话历史，好感度已重置！",
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
    
    async def _execute_set_persona(
        self, 
        event: MessageEvent, 
        persona_text: str
    ) -> ResponseEvent:
        """执行设置人设（确认后）。"""
        # 清除历史记录和好感度
        self.conversation.clear_context(event.group_id, event.user_id)
        self.affection.reset_affection(event.group_id, event.user_id)
        
        # 设置新人设
        self.conversation.set_custom_prompt(event.group_id, event.user_id, persona_text)
        
        # 生成好感度配置预览
        config_preview = ""
        try:
            config = await self.affection.generate_affection_config_for_persona(persona_text)
            level_neg = config.level_names.get((-100, -99), "死敌")
            level_zero = config.level_names.get((0, 15), "陌生")
            level_max = config.level_names.get((100, 101), "灵魂伴侣")
            config_preview = f"\n💕 好感度阶段: {level_neg} → {level_zero} → {level_max}"
        except Exception as e:
            print(f"[!] 生成好感度配置预览失败: {e}")
        
        preview = persona_text[:50] + "..." if len(persona_text) > 50 else persona_text
        
        msg = (
            "✨ 【人设已更新】✨\n"
            "━━━━━━━━━━━━━━\n"
            f"📝 {preview}\n"
            "━━━━━━━━━━━━━━\n"
            "🗑️ 对话历史已清除\n"
            "💕 好感度已重置"
            f"{config_preview}"
        )
        
        return ResponseEvent(
            content=msg,
            target_user_id=event.user_id,
            target_group_id=event.group_id,
            reply_to_message_id=event.message_id if event.is_group else None
        )
