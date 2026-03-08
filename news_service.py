#!/usr/bin/env python3
"""
新闻服务模块 - 使用火山引擎 Ark API 获取最新新闻
提供新闻搜索和缓存功能，每6小时更新一次（仅在有对话时触发）
"""

import os
import time
import json
import threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import requests


@dataclass
class NewsItem:
    """单条新闻"""
    title: str
    summary: str
    source: str = ""
    publish_time: str = ""
    url: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NewsCache:
    """新闻缓存数据"""
    news_list: List[NewsItem]
    fetch_time: float
    formatted_news: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "news_list": [n.to_dict() for n in self.news_list],
            "fetch_time": self.fetch_time,
            "formatted_news": self.formatted_news
        }


class NewsService:
    """
    新闻服务类
    - 使用火山引擎 Ark API 搜索今日新闻（使用 web_search 工具）
    - 6小时缓存，避免频繁调用
    - 仅在有对话时触发获取（由调用方控制）
    """
    
    # Ark API 配置 - 使用 responses API 端点（支持 tools）
    ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        cache_hours: float = 6.0,
        cache_file: str = "data/news_cache.json"
    ):
        """
        初始化新闻服务
        
        Args:
            api_key: Ark API Key，如果不提供则从环境变量或 config.yaml 获取
            model: Ark 模型名称
            cache_hours: 缓存时间（小时）
            cache_file: 缓存文件路径
        """
        self.api_key = api_key
        self.model = model or "doubao-seed-2-0-mini-260215"
        self.cache_hours = cache_hours
        self.cache_file = cache_file
        self._cache: Optional[NewsCache] = None
        self._lock = threading.Lock()
        self._enabled = bool(self.api_key)
        
        # 确保缓存目录存在
        cache_dir = os.path.dirname(cache_file)
        if cache_dir and not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        
        # 尝试加载持久化缓存
        self._load_cache_from_file()
        
        if self._enabled:
            print(f"[*] 新闻服务已启用，模型: {self.model}, 缓存时间: {cache_hours}小时")
        else:
            print("[!] 新闻服务未启用（未配置 API Key）")
    
    def _load_cache_from_file(self):
        """从文件加载缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                news_list = [NewsItem(**item) for item in data.get("news_list", [])]
                self._cache = NewsCache(
                    news_list=news_list,
                    fetch_time=data.get("fetch_time", 0),
                    formatted_news=data.get("formatted_news", "")
                )
                print(f"[*] 已加载新闻缓存，共 {len(news_list)} 条，缓存时间: {datetime.fromtimestamp(self._cache.fetch_time)}")
        except Exception as e:
            print(f"[!] 加载新闻缓存失败: {e}")
            self._cache = None
    
    def _save_cache_to_file(self):
        """保存缓存到文件"""
        if not self._cache:
            return
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] 保存新闻缓存失败: {e}")
    
    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效（未过期）"""
        if not self._cache:
            return False
        
        elapsed_hours = (time.time() - self._cache.fetch_time) / 3600
        return elapsed_hours < self.cache_hours
    
    def _fetch_news_from_api(self) -> Optional[NewsCache]:
        """
        从 Ark API 获取新闻（使用 web_search 工具）
        
        Returns:
            NewsCache 对象，获取失败返回 None
        """
        if not self.api_key:
            print("[!] 无法获取新闻：未配置 Ark API Key")
            return None
        
        try:
            # 构建请求 - 使用官方推荐的 responses API 格式
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            # 请求获取今日热点新闻 - 使用 web_search 工具
            payload = {
                "model": self.model,
                "stream": False,
                "tools": [
                    {"type": "web_search"}
                ],
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "今天有什么热点新闻？请列出5-8条最重要的国内外新闻，每条包含标题、简短摘要和来源。"
                            }
                        ]
                    }
                ]
            }
            
            print(f"[*] 正在从 Ark API 获取新闻（使用 web_search 工具）...")
            response = requests.post(
                self.ARK_BASE_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            
            result = response.json()
            
            # 解析 responses API 的输出
            content = self._extract_content_from_response(result)
            
            if not content:
                print("[!] API 返回内容为空")
                return None
            
            # 尝试解析 JSON 格式
            news_list = self._parse_news_content(content)
            
            if not news_list:
                # 如果没有解析到结构化新闻，使用原始内容
                formatted_news = self._format_raw_news(content)
                cache = NewsCache(
                    news_list=[NewsItem(title="今日热点新闻", summary="详见下方内容", source="网络搜索")],
                    fetch_time=time.time(),
                    formatted_news=formatted_news
                )
            else:
                # 格式化新闻为文本
                formatted_news = self._format_news(news_list)
                cache = NewsCache(
                    news_list=news_list,
                    fetch_time=time.time(),
                    formatted_news=formatted_news
                )
            
            print(f"[*] 成功获取 {len(news_list) if news_list else '文本'} 新闻")
            return cache
            
        except Exception as e:
            print(f"[!] 获取新闻失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _extract_content_from_response(self, result: Dict) -> str:
        """从 responses API 响应中提取内容"""
        try:
            # responses API 返回格式：output 数组中包含 content
            output = result.get("output", [])
            if not output:
                return ""
            
            # 查找 assistant 角色的消息
            for item in output:
                if item.get("role") == "assistant":
                    content_list = item.get("content", [])
                    texts = []
                    for content in content_list:
                        if content.get("type") == "output_text":
                            texts.append(content.get("text", ""))
                    return "\n".join(texts)
            
            # 备选：尝试其他可能的格式
            if "choices" in result:
                return result["choices"][0]["message"]["content"]
            
            return str(result)
        except Exception as e:
            print(f"[!] 解析响应失败: {e}")
            return str(result)
    
    def _parse_news_content(self, content: str) -> List[NewsItem]:
        """解析新闻内容，支持多种格式"""
        news_list = []
        
        try:
            import re
            
            # 尝试 JSON 格式解析
            try:
                data = json.loads(content)
                if "news" in data:
                    for item in data["news"]:
                        news_list.append(NewsItem(
                            title=item.get("title", ""),
                            summary=item.get("summary", ""),
                            source=item.get("source", ""),
                            publish_time=item.get("publish_time", ""),
                            url=item.get("url", "")
                        ))
                    return news_list
            except json.JSONDecodeError:
                pass
            
            # 尝试从 Markdown 代码块中提取 JSON
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', content)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    if "news" in data:
                        for item in data["news"]:
                            news_list.append(NewsItem(
                                title=item.get("title", ""),
                                summary=item.get("summary", ""),
                                source=item.get("source", ""),
                                publish_time=item.get("publish_time", ""),
                                url=item.get("url", "")
                            ))
                        return news_list
                except:
                    pass
            
            # 解析 web_search 返回的文本格式
            # 支持多种格式：
            # 1. **标题**：内容 / 1. 标题：内容
            # 2. **新闻标题**（加粗作为标题）
            # 3. 数字序号开头的新闻标题
            lines = content.strip().split('\n')
            
            current_news = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # 跳过分隔线、空行等
                if line in ['---', '***', '___'] or line.startswith('---') or line.startswith('==='):
                    continue
                
                # 匹配 **标题**：xxx 格式
                title_match = re.match(r'^\d+[.、]?\s*\*\*标题\*\*[:：]\s*(.+)', line)
                if title_match:
                    title = title_match.group(1).strip()
                    # 尝试在同一样式中获取摘要
                    # 有些格式是：标题：xxx，摘要/内容：yyy
                    if '，' in title and ('摘要' in title or '内容' in title):
                        parts = title.split('，', 1)
                        title = parts[0].strip()
                        summary_part = parts[1].strip()
                        summary_match = re.match(r'(?:摘要|内容)[:：]\s*(.+)', summary_part)
                        summary = summary_match.group(1) if summary_match else summary_part
                    else:
                        summary = ""
                    news_list.append(NewsItem(title=title, summary=summary, source=""))
                    current_news = news_list[-1]
                    continue
                
                # 匹配 标题：xxx 格式（不带**）
                title_match2 = re.match(r'^\d+[.、]?\s*标题[:：]\s*(.+)', line)
                if title_match2:
                    title = title_match2.group(1).strip()
                    news_list.append(NewsItem(title=title, summary="", source=""))
                    current_news = news_list[-1]
                    continue
                
                # 匹配 **xxx** 加粗格式作为标题（修复：移除 len(news_list) == 0 限制）
                bold_match = re.match(r'^\d+[.、]?\s*\*\*(.+?)\*\*', line)
                if bold_match:
                    title = bold_match.group(1).strip()
                    # 检查后面是否还有内容（同一行）
                    remaining = line[bold_match.end():].strip()
                    # 去掉前导的冒号或空格
                    remaining = re.sub(r'^[:：]\s*', '', remaining)
                    summary = remaining if remaining else ""
                    news_list.append(NewsItem(title=title, summary=summary, source=""))
                    current_news = news_list[-1]
                    continue
                
                # 匹配 数字序号 + 内容 格式（如 "1. 新闻标题" 或 "1、新闻标题"）
                simple_match = re.match(r'^\d+[.、]\s+(.+)', line)
                if simple_match and len(simple_match.group(1)) > 5:
                    title = simple_match.group(1).strip()
                    # 如果标题太长，可能是包含摘要，尝试分割
                    if len(title) > 50 and ('，' in title or '。' in title):
                        # 尝试找到第一个句子结束的位置
                        for sep in ['。', '，', '；']:
                            if sep in title:
                                parts = title.split(sep, 1)
                                if len(parts[0]) > 10:
                                    news_list.append(NewsItem(title=parts[0].strip(), summary=parts[1].strip() if len(parts) > 1 else "", source=""))
                                    current_news = news_list[-1]
                                    break
                        else:
                            news_list.append(NewsItem(title=title[:50], summary=title[50:], source=""))
                            current_news = news_list[-1]
                    else:
                        news_list.append(NewsItem(title=title, summary="", source=""))
                        current_news = news_list[-1]
                    continue
                
                # 如果不是新的标题行，可能是当前新闻的摘要或补充内容
                if current_news and len(line) > 5:
                    # 避免重复添加标题到摘要
                    if not line.startswith(current_news.title[:20]):
                        if current_news.summary:
                            current_news.summary += " " + line
                        else:
                            current_news.summary = line
            
            # 如果没有解析到结构化新闻，但整体内容有意义
            if not news_list and len(content) > 50:
                # 按段落分割
                paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
                for i, para in enumerate(paragraphs[:8], 1):  # 最多8条
                    # 提取第一行作为标题
                    para_lines = para.split('\n')
                    title = para_lines[0][:50]  # 限制标题长度
                    summary = '\n'.join(para_lines[1:])[:100] if len(para_lines) > 1 else ""
                    news_list.append(NewsItem(title=title, summary=summary, source="网络"))
            
        except Exception as e:
            print(f"[!] 解析新闻内容失败: {e}")
        
        return news_list
    
    def _format_raw_news(self, content: str) -> str:
        """将原始内容格式化为新闻文本"""
        lines = ["📰 今日热点新闻：", ""]
        lines.append(content)
        return "\n".join(lines)
    
    def _format_news(self, news_list: List[NewsItem]) -> str:
        """将新闻列表格式化为文本"""
        if not news_list:
            return ""
        
        lines = ["📰 今日热点新闻："]
        for i, news in enumerate(news_list, 1):
            lines.append(f"\n{i}. {news.title}")
            if news.summary:
                lines.append(f"   {news.summary}")
            if news.source:
                lines.append(f"   📌 {news.source}")
        
        return "\n".join(lines)
    
    def get_news(self, force_refresh: bool = False) -> str:
        """
        获取新闻内容（带缓存）
        
        Args:
            force_refresh: 强制刷新缓存
            
        Returns:
            格式化的新闻文本，获取失败返回空字符串
        """
        if not self._enabled:
            return ""
        
        with self._lock:
            # 检查缓存是否有效
            if not force_refresh and self._is_cache_valid():
                print("[*] 使用缓存的新闻数据")
                return self._cache.formatted_news if self._cache else ""
            
            # 获取新数据
            new_cache = self._fetch_news_from_api()
            if new_cache:
                self._cache = new_cache
                self._save_cache_to_file()
                return new_cache.formatted_news
            elif self._cache:
                # 获取失败但有过期缓存，返回过期缓存
                print("[!] 获取新闻失败，使用过期缓存")
                return self._cache.formatted_news
            else:
                return ""
    
    def get_news_for_prompt(self) -> str:
        """
        获取用于 system prompt 的新闻内容
        如果缓存过期，会尝试获取新数据
        
        Returns:
            格式化的新闻文本，适合作为 system prompt 的一部分
        """
        news = self.get_news()
        if not news:
            return ""
        
        # 包装成 system prompt 格式
        return f"【当前新闻资讯】\n{news}\n💡 你可以在闲聊时就以上新闻与对话者进行讨论，并主动分享你的看法和感受。"
    
    def clear_cache(self):
        """清除缓存"""
        with self._lock:
            self._cache = None
            if os.path.exists(self.cache_file):
                try:
                    os.remove(self.cache_file)
                except Exception as e:
                    print(f"[!] 删除缓存文件失败: {e}")
        print("[*] 新闻缓存已清除")
    
    def get_cache_status(self) -> Dict[str, Any]:
        """获取缓存状态信息"""
        if not self._cache:
            return {
                "has_cache": False,
                "is_valid": False,
                "news_count": 0,
                "last_fetch": None,
                "elapsed_hours": None
            }
        
        elapsed_hours = (time.time() - self._cache.fetch_time) / 3600
        return {
            "has_cache": True,
            "is_valid": self._is_cache_valid(),
            "news_count": len(self._cache.news_list),
            "last_fetch": datetime.fromtimestamp(self._cache.fetch_time).strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_hours": round(elapsed_hours, 2),
            "cache_hours": self.cache_hours
        }


# 全局实例（延迟初始化）
_news_service_instance: Optional[NewsService] = None
_news_service_lock = threading.Lock()


def get_news_service(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    cache_hours: float = 6.0
) -> Optional[NewsService]:
    """
    获取新闻服务全局实例（单例模式）
    
    Args:
        api_key: Ark API Key
        model: Ark 模型名称
        cache_hours: 缓存时间（小时）
        
    Returns:
        NewsService 实例，如果未启用则返回 None
    """
    global _news_service_instance
    
    with _news_service_lock:
        if _news_service_instance is None:
            _news_service_instance = NewsService(
                api_key=api_key,
                model=model,
                cache_hours=cache_hours
            )
        return _news_service_instance


def init_news_service_from_config(config: Dict[str, Any]) -> Optional[NewsService]:
    """
    从配置字典初始化新闻服务
    
    Args:
        config: 配置字典（通常来自 config.yaml）
        
    Returns:
        NewsService 实例，如果未启用则返回 None
    """
    global _news_service_instance
    
    # 检查是否启用新闻功能
    if not config.get("news_enabled", True):
        print("[*] 新闻功能已禁用")
        return None
    
    api_key = config.get("ark_api_key")
    model = config.get("ark_model", "doubao-seed-2-0-mini-260215")
    cache_hours = config.get("news_cache_hours", 6.0)
    
    with _news_service_lock:
        _news_service_instance = NewsService(
            api_key=api_key,
            model=model,
            cache_hours=cache_hours
        )
    
    return _news_service_instance


def get_cached_news_service() -> Optional[NewsService]:
    """获取已初始化的新闻服务实例"""
    return _news_service_instance


if __name__ == "__main__":
    # 测试代码
    import yaml
    
    print("=" * 60)
    print("新闻服务测试")
    print("=" * 60)
    
    # 尝试从配置文件加载
    config_path = "config.yaml"
    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    
    # 初始化服务
    service = init_news_service_from_config(config)
    
    if service:
        print("\n[缓存状态]")
        status = service.get_cache_status()
        for k, v in status.items():
            print(f"  {k}: {v}")
        
        print("\n[获取新闻]")
        news = service.get_news()
        if news:
            print(news[:500] + "..." if len(news) > 500 else news)
        else:
            print("未能获取新闻")
        
        print("\n[System Prompt 格式]")
        prompt_news = service.get_news_for_prompt()
        if prompt_news:
            print(prompt_news[:500] + "..." if len(prompt_news) > 500 else prompt_news)
    else:
        print("新闻服务未启用")
