"""共享总结服务。

封装消息获取、提示词构建、LLM 调用等通用逻辑，
基于已验证有效的 SummaryPlugin._generate_summary() 实现。
"""

import time
from typing import List, Optional

from qq_bot.services.llm.base import LLMService, ChatMessage
from qq_bot.services.storage.message import MessageStore
from qq_bot.core.config import BotConfig


class SummaryService:
    """共享总结服务。
    
    供手动总结（SummaryPlugin）和每日总结（DailySummaryScheduler）共同使用。
    
    Example:
        >>> service = SummaryService(llm_service, message_store, config)
        >>> result = await service.generate_summary(
        ...     group_id=123456,
        ...     since=time.time() - 3600,
        ...     window_display="最近1小时"
        ... )
    """
    
    pass  # 总结指令现在从配置读取
    
    def __init__(
        self,
        llm_service: LLMService,
        message_store: MessageStore,
        config: BotConfig
    ):
        """初始化总结服务。
        
        Args:
            llm_service: LLM 服务实例
            message_store: 消息存储实例
            config: 机器人配置（用于获取默认人设和 max_tokens）
        """
        self.llm = llm_service
        self.store = message_store
        self.config = config
        
        # 加载 config.yaml 中的默认人设（仅人设部分，不含总结指令）
        self.default_persona = self._load_default_persona()
        
        print(f"[*] SummaryService 已初始化，默认人设: {self.default_persona[:50]}...")
    
    def _load_default_persona(self) -> str:
        """加载默认人设（来自 config.chat.system_prompt）。"""
        persona = getattr(self.config.chat, 'system_prompt', '')
        if persona and persona.strip():
            return persona.strip()
        return "你说话温柔体贴，像一个关心大家的朋友。"
    
    def _build_system_prompt(self, custom_persona: Optional[str] = None) -> str:
        """构建 System Prompt：人设 + 总结指令。
        
        Args:
            custom_persona: 自定义人设，为 None 时使用 default_persona
        
        Returns:
            完整的 system prompt
        """
        persona_text = custom_persona if custom_persona else self.default_persona
        summary_instructions = self.config.prompts.summary.instructions
        return f"你是一个群聊总结助手。请根据以下人设来生成总结：\n\n{persona_text}\n\n{summary_instructions}"
    
    async def generate_summary(
        self,
        group_id: int,
        since: float,
        window_display: str,
        custom_persona: Optional[str] = None,
        max_messages: int = 200
    ) -> str:
        """生成聊天记录总结。
        
        这是核心方法，供手动总结和每日总结共同使用。
        
        Args:
            group_id: 群号
            since: 起始时间戳（获取此时间之后的消息）
            window_display: 时间窗口显示文本（如 "最近1小时"、"过去24小时"）
            custom_persona: 自定义人设（仅替换人设部分，总结指令不变）
            max_messages: 最大消息数量
        
        Returns:
            格式化后的总结文本（包含标题和分隔符）
        """
        # 1. 获取消息
        messages = self.store.get_messages_since(
            since=since,
            group_id=group_id,
            limit=5000  # 先获取较多，再裁剪
        )
        
        if not messages:
            return f"📭 {window_display}内没有聊天记录。"
        
        # 2. 转换为简单格式并限制数量
        msg_list = []
        for msg in messages[-max_messages:]:
            msg_list.append(f"{msg.nickname}: {msg.content}")
        
        chat_text = "\n".join(msg_list)
        
        # 3. 构建提示词
        system_prompt = self._build_system_prompt(custom_persona)
        user_prompt = f"""请总结以下{window_display}的群聊记录：

{chat_text}

开始总结："""
        
        # 4. 调用 LLM
        max_tokens = min(self.config.summary.max_tokens, 4096)
        response = await self.llm.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt)
            ],
            max_tokens=max_tokens,
            temperature=0.7
        )
        
        # 5. 格式化输出
        header = f"✨ {window_display}群聊小结 ✨"
        separator = "─" * 12
        return f"{header}\n{separator}\n{response.content}\n{separator}"
