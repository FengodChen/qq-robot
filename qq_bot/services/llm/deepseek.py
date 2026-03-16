"""DeepSeek LLM 服务。

实现 DeepSeek API 调用。
"""

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from qq_bot.services.llm.base import LLMService, ChatMessage, ChatResponse
from qq_bot.core.exceptions import LLMError
from qq_bot.utils.debug_logger import log_llm_context


class DeepSeekService(LLMService):
    """DeepSeek API 服务。
    
    Example:
        >>> service = DeepSeekService(api_key="sk-xxx", model="deepseek-chat")
        >>> response = await service.chat([
        ...     ChatMessage(role="system", content="你是助手"),
        ...     ChatMessage(role="user", content="你好")
        ... ])
    """
    
    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"
    
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        timeout: int = 60,
        debug: bool = False,
        **kwargs
    ):
        """初始化 DeepSeek 服务。
        
        Args:
            api_key: DeepSeek API 密钥。
            model: 模型名称。
            base_url: API 基础 URL。
            timeout: 请求超时时间。
            debug: 是否启用调试模式，启用时会输出完整上下文。
        """
        super().__init__(api_key, model, **kwargs)
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.debug = debug
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self._semaphore = asyncio.Semaphore(5)  # 限制5个并发
    

    
    def _format_messages(self, messages: List[ChatMessage]) -> List[Dict[str, str]]:
        """格式化消息为 API 格式。"""
        return [
            {
                "role": msg.role,
                "content": msg.content,
                **({"name": msg.name} if msg.name else {})
            }
            for msg in messages
        ]
    
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> ChatResponse:
        """非流式聊天。"""
        # Debug 输出完整上下文
        if self.debug:
            log_llm_context("LLM 聊天上下文", messages, model=self.model)
        
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "stream": False
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        # 添加其他参数
        payload.update(kwargs)
        
        async with self._semaphore:  # 并发控制
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    choice = data["choices"][0]
                    message = choice["message"]
                    usage = data.get("usage", {})
                    
                    result = ChatResponse(
                        content=message["content"],
                        usage={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0)
                        },
                        finish_reason=choice.get("finish_reason", ""),
                        raw_response=data
                    )
                    
                    # Debug 输出响应内容
                    if self.debug:
                        from qq_bot.utils.debug_logger import log_compact_debug, log_debug_block
                        # 使用 repr 显示原始内容，便于看清特殊字符
                        content_repr = repr(result.content)
                        if len(content_repr) > 500:
                            content_repr = content_repr[:250] + " ... " + content_repr[-250:]
                        log_compact_debug("LLM 响应", 
                                          finish_reason=result.finish_reason,
                                          tokens=f"{usage.get('total_tokens', 0)}",
                                          content_len=len(result.content))
                        # 单独输出完整内容便于查看
                        log_debug_block("LLM 响应内容", content_repr)
                    
                    return result
                    
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if hasattr(e, 'response') else None
                raise LLMError(
                    f"DeepSeek API 请求失败: {e}",
                    provider="deepseek",
                    status_code=status_code
                )
            except (KeyError, IndexError) as e:
                raise LLMError(
                    f"解析 DeepSeek 响应失败: {e}",
                    provider="deepseek"
                )
            except Exception as e:
                raise LLMError(
                    f"DeepSeek 请求异常: {e}",
                    provider="deepseek"
                )
    
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式聊天。"""
        # Debug 输出完整上下文
        if self.debug:
            log_llm_context("LLM 流式上下文", messages, model=self.model)
        
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "stream": True
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        payload.update(kwargs)
        
        async with self._semaphore:  # 并发控制
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload
                    ) as response:
                        response.raise_for_status()
                        
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            
                            if not line.startswith("data: "):
                                continue
                            
                            data = line[6:]  # 移除 "data: " 前缀
                            if data == "[DONE]":
                                break
                            
                            try:
                                chunk = json.loads(data)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
                            
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if hasattr(e, 'response') else None
                raise LLMError(
                    f"DeepSeek 流式请求失败: {e}",
                    provider="deepseek",
                    status_code=status_code
                )
            except Exception as e:
                raise LLMError(
                    f"DeepSeek 流式请求异常: {e}",
                    provider="deepseek"
                )
