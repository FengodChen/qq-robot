"""对话上下文管理模块。

管理群聊中每个人的对话上下文，支持持久化存储。
"""

import json
import time
import threading
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

from qq_bot.services.storage.db import get_db_manager, json_dumps, json_loads


@dataclass
class ChatMessage:
    """聊天消息记录。
    
    Attributes:
        role: 消息角色 (user/assistant/system)。
        content: 消息内容。
        nickname: 发送者昵称。
        timestamp: 消息时间戳。
    """
    role: str
    content: str
    nickname: Optional[str] = None
    timestamp: float = 0.0
    
    def __post_init__(self):
        """初始化时间戳。"""
        if self.timestamp == 0.0:
            self.timestamp = time.time()
    
    def to_dict(self) -> Dict:
        """转换为字典。"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ChatMessage":
        """从字典创建实例。"""
        return cls(**data)


class ConversationManager:
    """对话上下文管理器。
    
    管理群聊中每个人的对话上下文，支持持久化到 SQLite 数据库。
    
    Attributes:
        max_context: 最大上下文消息数。
        db_path: 数据库文件路径。
    
    Example:
        >>> manager = ConversationManager(max_context=5)
        >>> manager.add_message(123456, 789012, "user", "你好", "用户")
        >>> context = manager.get_context(123456, 789012)
    """
    
    def __init__(self, max_context: int = 5, db_path: Optional[Path] = None):
        """初始化对话管理器。
        
        Args:
            max_context: 上下文最多保留的消息条数。
            db_path: 数据库文件路径，默认为 data/chat_history.db。
        """
        self.max_context = max_context
        self.contexts: Dict[Tuple[int, int], deque] = {}
        self.custom_prompts: Dict[Tuple[int, int], str] = {}
        self._lock = threading.Lock()
        
        # 设置数据库路径
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "chat_history.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        self._load()
    
    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        try:
            db = get_db_manager(self.db_path)
            create_sql = """
                CREATE TABLE IF NOT EXISTS chat_contexts (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    messages TEXT,
                    PRIMARY KEY (group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS custom_prompts (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    prompt TEXT,
                    PRIMARY KEY (group_id, user_id)
                );
            """
            db.init_tables(create_sql)
        except Exception as e:
            print(f"[!] 初始化聊天记录数据库失败: {e}")
    
    def _load(self) -> None:
        """从数据库加载历史记录。"""
        try:
            db = get_db_manager(self.db_path)
            max_storage = self.max_context * 10
            
            # 加载对话历史
            rows = db.fetchall("SELECT * FROM chat_contexts")
            for row in rows:
                try:
                    group_id = row["group_id"]
                    user_id = row["user_id"]
                    messages = json_loads(row["messages"]) or []
                    
                    # 只加载最新的记录
                    if len(messages) > max_storage:
                        messages = messages[-max_storage:]
                    
                    self.contexts[(group_id, user_id)] = deque(
                        messages, maxlen=self.max_context
                    )
                except Exception as e:
                    print(f"[!] 加载聊天记录失败 ({row.get('group_id')},{row.get('user_id')}): {e}")
                    continue
            
            # 加载自定义人设
            prompt_rows = db.fetchall("SELECT * FROM custom_prompts")
            for row in prompt_rows:
                try:
                    group_id = row["group_id"]
                    user_id = row["user_id"]
                    self.custom_prompts[(group_id, user_id)] = row["prompt"]
                except Exception:
                    continue
            
            print(f"[*] 已加载 {len(self.contexts)} 个用户的历史记录, {len(self.custom_prompts)} 个自定义人设")
        except Exception as e:
            print(f"[!] 加载历史记录失败: {e}")
    
    def _save(self) -> None:
        """保存历史记录到数据库。"""
        try:
            db = get_db_manager(self.db_path)
            max_storage = self.max_context * 10
            
            # 保存对话历史
            for (group_id, user_id), messages in self.contexts.items():
                msg_list = list(messages)
                if len(msg_list) > max_storage:
                    msg_list = msg_list[-max_storage:]
                
                messages_json = json_dumps(msg_list)
                db.execute(
                    "INSERT OR REPLACE INTO chat_contexts (group_id, user_id, messages) VALUES (?, ?, ?)",
                    (group_id, user_id, messages_json)
                )
            
            # 保存自定义人设
            for (group_id, user_id), prompt in self.custom_prompts.items():
                db.execute(
                    "INSERT OR REPLACE INTO custom_prompts (group_id, user_id, prompt) VALUES (?, ?, ?)",
                    (group_id, user_id, prompt)
                )
        except Exception as e:
            print(f"[!] 保存历史记录失败: {e}")
    
    def get_context(self, group_id: int, user_id: int) -> List[Dict]:
        """获取某人的对话上下文。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            消息列表，每条消息为字典格式。
        """
        key = (group_id, user_id)
        with self._lock:
            if key not in self.contexts:
                self.contexts[key] = deque(maxlen=self.max_context)
            return list(self.contexts[key])
    
    def add_message(
        self, 
        group_id: int, 
        user_id: int, 
        role: str, 
        content: str, 
        nickname: Optional[str] = None
    ) -> None:
        """添加一条消息到上下文并持久化。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            role: 消息角色 (user/assistant)。
            content: 消息内容。
            nickname: 发送者昵称。
        """
        key = (group_id, user_id)
        with self._lock:
            if key not in self.contexts:
                self.contexts[key] = deque(maxlen=self.max_context)
            
            self.contexts[key].append({
                "role": role,
                "content": content,
                "nickname": nickname,
                "timestamp": time.time()
            })
            self._save()
    
    def clear_context(self, group_id: int, user_id: int) -> None:
        """清空某人的上下文并更新持久化。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        """
        key = (group_id, user_id)
        with self._lock:
            if key in self.contexts:
                self.contexts[key].clear()
                self._save()
            
            # 从数据库中删除记录
            try:
                db = get_db_manager(self.db_path)
                db.execute(
                    "DELETE FROM chat_contexts WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id)
                )
            except Exception as e:
                print(f"[!] 删除聊天记录失败: {e}")
    
    def set_custom_prompt(self, group_id: int, user_id: int, prompt: str) -> None:
        """设置自定义人设。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            prompt: 人设提示词。
        """
        key = (group_id, user_id)
        with self._lock:
            self.custom_prompts[key] = prompt
            self._save()
    
    def get_custom_prompt(self, group_id: int, user_id: int) -> Optional[str]:
        """获取自定义人设。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            自定义人设文本，如果没有则返回 None。
        """
        key = (group_id, user_id)
        with self._lock:
            return self.custom_prompts.get(key)
    
    def clear_custom_prompt(self, group_id: int, user_id: int) -> None:
        """清除自定义人设。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        """
        key = (group_id, user_id)
        with self._lock:
            if key in self.custom_prompts:
                del self.custom_prompts[key]
                self._save()
            
            # 从数据库中删除记录
            try:
                db = get_db_manager(self.db_path)
                db.execute(
                    "DELETE FROM custom_prompts WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id)
                )
            except Exception as e:
                print(f"[!] 删除自定义人设失败: {e}")
    
    def get_formatted_history(
        self, 
        group_id: int, 
        user_id: int, 
        max_messages: int = 15
    ) -> str:
        """获取格式化的对话历史文本。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            max_messages: 最大返回消息数。
        
        Returns:
            格式化的历史记录文本。
        """
        context = self.get_context(group_id, user_id)
        if not context:
            return "暂无对话历史"
        
        lines = [f"【对话历史】共{len(context)}条", "-" * 20]
        for i, msg in enumerate(context[-max_messages:], 1):
            nickname = msg.get("nickname", "未知")[:8]
            content = msg["content"][:20]
            if len(msg["content"]) > 20:
                content += "..."
            lines.append(f"{i}.[{nickname}]{content}")
        
        return "\n".join(lines)
