"""DeepSeek LLM 服务。

实现 DeepSeek API 调用。
"""

import json
from typing import Any, AsyncIterator, Dict, List, Optional

import requests

from qq_bot.services.llm.base import LLMService, ChatMessage, ChatResponse
from qq_bot.core.exceptions import LLMError


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
        **kwargs
    ):
        """初始化 DeepSeek 服务。
        
        Args:
            api_key: DeepSeek API 密钥。
            model: 模型名称。
            base_url: API 基础 URL。
            timeout: 请求超时时间。
        """
        super().__init__(api_key, model, **kwargs)
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
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
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data["choices"][0]
            message = choice["message"]
            usage = data.get("usage", {})
            
            return ChatResponse(
                content=message["content"],
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0)
                },
                finish_reason=choice.get("finish_reason", ""),
                raw_response=data
            )
            
        except requests.HTTPError as e:
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
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "stream": True
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        payload.update(kwargs)
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                stream=True,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if not line:
                    continue
                
                line = line.decode("utf-8")
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
                    
        except requests.HTTPError as e:
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
