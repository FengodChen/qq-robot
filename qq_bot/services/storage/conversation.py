"""对话上下文管理。

管理用户的对话历史和自定义人设。
"""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qq_bot.services.storage.db import DatabaseManager, get_db_manager, json_dumps, json_loads


@dataclass
class ChatMessage:
    """聊天消息。"""
    role: str  # "user" | "assistant"
    content: str
    nickname: str = ""
    timestamp: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(**data)


class ConversationManager:
    """对话上下文管理器。
    
    管理每个用户的对话历史和自定义人设，支持持久化。
    
    Attributes:
        max_context: 最大上下文消息数。
        db_path: 数据库路径。
    """
    
    def __init__(
        self,
        max_context: int = 20,
        db_path: str | Path | None = None
    ):
        """初始化对话管理器。
        
        Args:
            max_context: 最大上下文消息数。
            db_path: 数据库路径，默认为 data/conversations.db。
        """
        self.max_context = max_context
        
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "conversations.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 内存缓存: {(group_id, user_id): deque}
        self._contexts: Dict[Tuple[int, int], deque] = {}
        self._custom_prompts: Dict[Tuple[int, int], str] = {}
        self._lock = threading.Lock()
        
        # 初始化数据库
        self._init_db()
        self._load()
    
    def _get_db(self) -> DatabaseManager:
        """获取数据库管理器。"""
        return get_db_manager(self.db_path)
    
    def _init_db(self) -> None:
        """初始化数据库表。"""
        create_sql = """
            CREATE TABLE IF NOT EXISTS conversations (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                messages TEXT,  -- JSON 格式
                PRIMARY KEY (group_id, user_id)
            );
            
            CREATE TABLE IF NOT EXISTS custom_prompts (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                prompt TEXT,
                PRIMARY KEY (group_id, user_id)
            );
        """
        self._get_db().init_tables(create_sql)
    
    def _load(self) -> None:
        """从数据库加载数据。"""
        try:
            db = self._get_db()
            max_storage = self.max_context * 10
            
            # 加载对话历史
            rows = db.fetchall("SELECT * FROM conversations")
            for row in rows:
                try:
                    group_id = row["group_id"]
                    user_id = row["user_id"]
                    messages = json_loads(row["messages"]) or []
                    
                    # 截断过长的历史
                    if len(messages) > max_storage:
                        messages = messages[-max_storage:]
                    
                    self._contexts[(group_id, user_id)] = deque(
                        [ChatMessage.from_dict(m) for m in messages],
                        maxlen=self.max_context
                    )
                except Exception as e:
                    print(f"[!] 加载对话失败 ({row.get('group_id')},{row.get('user_id')}): {e}")
                    continue
            
            # 加载自定义人设
            prompt_rows = db.fetchall("SELECT * FROM custom_prompts")
            for row in prompt_rows:
                try:
                    group_id = row["group_id"]
                    user_id = row["user_id"]
                    self._custom_prompts[(group_id, user_id)] = row["prompt"]
                except Exception:
                    continue
            
            print(f"[*] 已加载 {len(self._contexts)} 个对话, {len(self._custom_prompts)} 个自定义人设")
            
        except Exception as e:
            print(f"[!] 加载对话数据失败: {e}")
    
    def _save(self) -> None:
        """保存数据到数据库。"""
        try:
            db = self._get_db()
            max_storage = self.max_context * 10
            
            # 保存对话
            for (group_id, user_id), messages in self._contexts.items():
                msg_list = list(messages)
                if len(msg_list) > max_storage:
                    msg_list = msg_list[-max_storage:]
                
                messages_json = json_dumps([m.to_dict() for m in msg_list])
                db.execute(
                    "INSERT OR REPLACE INTO conversations (group_id, user_id, messages) VALUES (?, ?, ?)",
                    (group_id, user_id, messages_json)
                )
            
            # 保存人设
            for (group_id, user_id), prompt in self._custom_prompts.items():
                db.execute(
                    "INSERT OR REPLACE INTO custom_prompts (group_id, user_id, prompt) VALUES (?, ?, ?)",
                    (group_id, user_id, prompt)
                )
                
        except Exception as e:
            print(f"[!] 保存对话数据失败: {e}")
    
    def get_context(self, group_id: int, user_id: int) -> List[ChatMessage]:
        """获取用户的对话上下文。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
            
        Returns:
            消息列表。
        """
        key = (group_id, user_id)
        with self._lock:
            if key not in self._contexts:
                self._contexts[key] = deque(maxlen=self.max_context)
            return list(self._contexts[key])
    
    def add_message(
        self,
        group_id: int,
        user_id: int,
        role: str,
        content: str,
        nickname: str = ""
    ) -> None:
        """添加消息到上下文。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
            role: 角色（"user" 或 "assistant"）。
            content: 消息内容。
            nickname: 发送者昵称。
        """
        key = (group_id, user_id)
        with self._lock:
            if key not in self._contexts:
                self._contexts[key] = deque(maxlen=self.max_context)
            
            self._contexts[key].append(ChatMessage(
                role=role,
                content=content,
                nickname=nickname,
                timestamp=time.time()
            ))
            self._save()
    
    def clear_context(self, group_id: int, user_id: int) -> None:
        """清空对话上下文。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
        """
        key = (group_id, user_id)
        with self._lock:
            if key in self._contexts:
                self._contexts[key].clear()
            
            # 从数据库删除
            self._get_db().execute(
                "DELETE FROM conversations WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            )
    
    def set_custom_prompt(self, group_id: int, user_id: int, prompt: str) -> None:
        """设置自定义人设。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
            prompt: 人设提示词。
        """
        key = (group_id, user_id)
        with self._lock:
            self._custom_prompts[key] = prompt
            self._save()
    
    def get_custom_prompt(self, group_id: int, user_id: int) -> Optional[str]:
        """获取自定义人设。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
            
        Returns:
            人设提示词，如果没有则返回 None。
        """
        key = (group_id, user_id)
        with self._lock:
            return self._custom_prompts.get(key)
    
    def clear_custom_prompt(self, group_id: int, user_id: int) -> None:
        """清除自定义人设。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
        """
        key = (group_id, user_id)
        with self._lock:
            self._custom_prompts.pop(key, None)
            self._get_db().execute(
                "DELETE FROM custom_prompts WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            )
    
    def format_context_for_prompt(
        self,
        group_id: int,
        user_id: int,
        max_messages: int = 10
    ) -> str:
        """格式化上下文用于 Prompt。
        
        Args:
            group_id: 群号（私聊为 0）。
            user_id: 用户 QQ。
            max_messages: 最大消息数。
            
        Returns:
            格式化的上下文字符串。
        """
        messages = self.get_context(group_id, user_id)
        if not messages:
            return ""
        
        # 只取最近的 N 条
        messages = messages[-max_messages:]
        
        lines = []
        for msg in messages:
            role_display = "用户" if msg.role == "user" else "助手"
            if msg.nickname:
                lines.append(f"{msg.nickname}({role_display}): {msg.content}")
            else:
                lines.append(f"{role_display}: {msg.content}")
        
        return "\n".join(lines)
