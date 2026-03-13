"""意图识别模块。

使用 DeepSeek API 进行用户意图识别。

Example:
    >>> from qq_bot.agent import IntentClassifier, IntentType, IntentResult
    >>> from qq_bot.services.llm.deepseek import DeepSeekService
    >>> 
    >>> llm = DeepSeekService(api_key="sk-xxx")
    >>> classifier = IntentClassifier(llm_service=llm)
    >>> 
    >>> result = await classifier.classify_intent("总结一下今天的聊天")
    >>> print(result.intent)  # IntentType.SUMMARIZE
    >>> print(result.confidence)  # 0.9
"""

from qq_bot.agent.intents import IntentResult, IntentType
from qq_bot.agent.classifier import IntentClassifier
from qq_bot.agent.prompts import IntentKeywords, IntentPrompts

__all__ = [
    # 意图类型
    "IntentType",
    "IntentResult",
    # 分类器
    "IntentClassifier",
    # Prompt 和关键词
    "IntentKeywords",
    "IntentPrompts",
]
