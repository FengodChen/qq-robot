"""新闻服务模块。

使用火山引擎 Ark API 的 Web Search 工具获取实时新闻，支持文件缓存避免频繁请求。
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from qq_bot.core.config import ArkConfig, NewsConfig


class NewsService:
    """新闻服务。
    
    使用 Ark API 的 Web Search 工具获取实时新闻，支持文件缓存。
    """
    
    def __init__(
        self, 
        ark_config: ArkConfig, 
        news_config: NewsConfig,
        cache_path: str = "data/news_cache.json"
    ):
        """初始化新闻服务。
        
        Args:
            ark_config: Ark API 配置
            news_config: 新闻服务配置
            cache_path: 缓存文件路径
        """
        self.ark_config = ark_config
        self.news_config = news_config
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    async def fetch_news(self) -> str:
        """获取新闻。
        
        优先从缓存获取，缓存过期则调用 API。
        
        Returns:
            新闻内容，获取失败返回空字符串
        """
        # 如果服务未启用，直接返回空字符串
        if not self.news_config.enabled:
            print("[News] 新闻服务未启用")
            return ""
        
        # 检查缓存是否有效
        cached = self._load_cache()
        if cached and not self._is_cache_expired(cached):
            print(f"[News] 使用缓存的新闻内容")
            return cached.get("content", "")
        
        # 调用 API 获取新闻
        try:
            print(f"[News] 正在从 Ark API 获取实时新闻...")
            news_content = await self._fetch_from_api()
            if news_content:
                self._save_cache(news_content)
                print(f"[News] 新闻获取成功并缓存")
            return news_content
        except Exception as e:
            print(f"[!] 获取新闻失败: {e}")
            # 如果 API 失败但缓存存在，返回缓存（即使过期）
            if cached:
                print(f"[News] 返回过期缓存内容")
                return cached.get("content", "")
            return ""
    
    async def _fetch_from_api(self) -> str:
        """从 Ark API 使用 Web Search 工具获取实时新闻。
        
        Returns:
            格式化的新闻内容
            
        Raises:
            requests.RequestException: API 请求失败
        """
        today = datetime.now().strftime("%Y年%m月%d日")
        
        headers = {
            "Authorization": f"Bearer {self.ark_config.api_key}",
            "Content-Type": "application/json"
        }
        
        # 使用 Responses API + Web Search 工具
        payload = {
            "model": self.ark_config.model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "你是一个新闻助手。请使用 web_search 工具搜索今天的最新真实新闻，提供简洁准确的新闻摘要。"
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"请搜索今天（{today}）的最新重要新闻，列出3-5条真实新闻，每条用一句话概括，总字数控制在200字以内。"
                        }
                    ]
                }
            ],
            "tools": [
                {
                    "type": "web_search",
                    "max_keyword": 3,
                    "limit": 10
                }
            ],
            "max_tool_calls": 2,
            "temperature": 0.3
        }
        
        response = requests.post(
            f"{self.ark_config.base_url}/responses",
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        
        # 解析 Responses API 的返回结果
        content = self._parse_response(data)
        return content.strip()
    
    def _parse_response(self, data: dict) -> str:
        """解析 Responses API 的返回结果。
        
        Args:
            data: API 响应数据
            
        Returns:
            提取的新闻内容
        """
        try:
            # 查找 message 类型的输出项
            output = data.get("output", [])
            
            for item in output:
                if item.get("type") == "message":
                    content_list = item.get("content", [])
                    for content_item in content_list:
                        if content_item.get("type") == "output_text":
                            return content_item.get("text", "")
            
            # 如果没有找到，尝试其他格式
            if "text" in data:
                return data["text"]
            
            # 返回原始数据的一部分用于调试
            return str(data)[:500]
            
        except Exception as e:
            print(f"[!] 解析响应失败: {e}")
            return ""
    
    def _load_cache(self) -> Optional[dict]:
        """从缓存文件加载。
        
        Returns:
            缓存数据字典，不存在或解析失败返回 None
        """
        if not self.cache_path.exists():
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[!] 加载新闻缓存失败: {e}")
            return None
    
    def _save_cache(self, content: str) -> None:
        """保存到缓存文件。
        
        Args:
            content: 新闻内容
        """
        cache_data = {
            "content": content,
            "timestamp": time.time(),
            "expires_at": time.time() + (self.news_config.cache_hours * 3600)
        }
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[!] 保存新闻缓存失败: {e}")
    
    def _is_cache_expired(self, cache: dict) -> bool:
        """检查缓存是否过期。
        
        Args:
            cache: 缓存数据字典
            
        Returns:
            True 表示已过期，False 表示未过期
        """
        expires_at = cache.get("expires_at", 0)
        return time.time() > expires_at
    
    def clear_cache(self) -> None:
        """清除缓存文件。"""
        if self.cache_path.exists():
            try:
                self.cache_path.unlink()
                print(f"[News] 缓存已清除")
            except IOError as e:
                print(f"[!] 清除新闻缓存失败: {e}")
