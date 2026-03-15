"""意图分类器。

使用 DeepSeek API 进行意图识别。
"""

import json
import re
from typing import Any, Optional

from qq_bot.agent.intents import IntentResult, IntentType
from qq_bot.core.exceptions import IntentError, LLMError
from qq_bot.services.llm.base import ChatMessage, LLMService
from qq_bot.utils.debug_logger import log_llm_context, log_compact_debug


class IntentClassifier:
    """意图分类器。
    
    结合关键词快速匹配和 DeepSeek AI 进行自然语言意图识别。
    
    Example:
        >>> classifier = IntentClassifier(llm_service)
        >>> result = await classifier.classify_intent("总结一下", context)
        >>> print(result.intent)  # IntentType.SUMMARIZE
    """
    
    # 敏感操作意图（需要较高 AI 置信度）
    SENSITIVE_INTENTS = [
        IntentType.SET_PERSONA,
        IntentType.RESET_PERSONA,
        IntentType.CLEAR_HISTORY
    ]
    
    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        debug_mode: bool = False,
        prompts: Any = None
    ):
        """初始化意图分类器。
        
        Args:
            llm_service: LLM 服务实例，用于 AI 意图识别
            debug_mode: 是否启用调试模式
            prompts: Agent 提示词配置
        """
        self.llm_service = llm_service
        self.debug_mode = debug_mode
        self.prompts = prompts
    
    async def classify_intent(
        self,
        message: str,
        context: Optional[dict[str, Any]] = None
    ) -> IntentResult:
        """分类用户意图。
        
        使用 DeepSeek API 进行意图识别。
        
        Args:
            message: 用户消息
            context: 可选的上下文信息
            
        Returns:
            意图识别结果
            
        Raises:
            IntentError: 当意图识别过程出错时
        """
        context = context or {}
        
        # 如果没有配置 LLM，默认作为聊天
        if not self.llm_service:
            return IntentResult(
                intent=IntentType.CHAT,
                confidence=0.5,
                reason="无 LLM 服务，默认作为普通聊天"
            )
        
        # 使用 DeepSeek API 进行意图识别
        try:
            return await self._ai_intent_classification(message)
            
        except (LLMError, json.JSONDecodeError) as e:
            if self.debug_mode:
                print(f"[!] AI 意图识别失败: {e}")
            
            return IntentResult(
                intent=IntentType.CHAT,
                confidence=0.5,
                reason="意图识别失败，默认作为普通聊天"
            )
    
    async def extract_persona_text(self, message: str) -> str:
        """从消息中提取人设内容。
        
        使用 AI 从用户设置人设的指令中提取纯粹的人设描述。
        
        Args:
            message: 用户消息
            
        Returns:
            提取到的人设描述，失败则返回原消息
        """
        if not self.llm_service:
            return message
        
        try:
            messages = [
                ChatMessage(
                    role="system",
                    content=self.prompts.persona_extraction
                ),
                ChatMessage(role="user", content=message)
            ]
            
            response = await self.llm_service.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=200
            )
            
            # 解析 JSON 响应
            content = response.content
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                if result.get('success') and result.get('persona_text'):
                    return result.get('persona_text', '').strip()
                    
        except Exception as e:
            if self.debug_mode:
                print(f"[!] AI 提取人设内容失败: {e}")
        
        # 如果 AI 提取失败，返回原消息
        return message
    

    
    async def _ai_intent_classification(self, message: str) -> IntentResult:
        """使用 AI 进行意图分类。
        
        Args:
            message: 用户消息
            
        Returns:
            AI 识别的意图结果
            
        Raises:
            LLMError: 当 LLM 调用失败时
            json.JSONDecodeError: 当解析响应失败时
        """
        messages = [
            ChatMessage(
                role="system",
                content=self.prompts.intent_classification
            ),
            ChatMessage(role="user", content=message)
        ]
        
        response = await self.llm_service.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=300
        )
        
        # 解析 JSON 响应
        content = response.content
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            raise IntentError("无法从 AI 响应中提取 JSON")
        
        result = json.loads(json_match.group())
        
        intent_str = result.get('intent', 'unknown')
        try:
            intent = IntentType(intent_str)
        except ValueError:
            intent = IntentType.UNKNOWN
        
        confidence = result.get('confidence', 0.5)
        reason = result.get('reason', 'AI 判断')
        parameters = result.get('parameters', {})
        
        return IntentResult(
            intent=intent,
            confidence=confidence,
            parameters=parameters,
            reason=f"[AI判断] {reason}"
        )
    

    




