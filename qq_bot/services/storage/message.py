"""消息存储服务。

提供高效的消息存储和查询功能，使用 SQLite 存储聊天记录。
"""

import hashlib
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from qq_bot.services.storage.db import DatabaseManager, get_db_manager, json_dumps, json_loads
from qq_bot.services.storage.base import StorageService


@dataclass
class Message:
    """消息数据类。
    
    Attributes:
        msg_id: 消息 ID。
        msg_type: 消息类型，'private' 或 'group'。
        user_id: 发送者 QQ。
        group_id: 群号（私聊为 0）。
        nickname: 发送者昵称。
        content: 消息内容（纯文本）。
        raw_message: 原始消息。
        timestamp: 时间戳。
        msg_hash: 消息内容哈希（用于去重）。
        reply_to: 引用的消息 ID（若无则为 None）。
        target_user_id: 对话目标用户 ID。
    """
    msg_id: int
    msg_type: str
    user_id: int
    group_id: int
    nickname: str
    content: str
    raw_message: str
    timestamp: float
    msg_hash: str = ""
    reply_to: Optional[int] = None
    target_user_id: Optional[int] = None
    
    def __post_init__(self):
        """初始化时计算哈希（如果未提供）。"""
        if not self.msg_hash:
            self.msg_hash = self._compute_hash()
    
    def _compute_hash(self) -> str:
        """计算消息哈希用于去重。"""
        data = f"{self.user_id}:{self.content}:{int(self.timestamp)//10}"
        return hashlib.md5(data.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典创建实例。"""
        return cls(**data)


class MessageStore(StorageService):
    """消息存储管理器。
    
    使用 SQLite 存储消息，支持高效的时间范围查询和自动清理。
    
    Example:
        >>> store = MessageStore("data/messages.db", retention_days=7)
        >>> store.add_message(msg_type="group", user_id=123456, ...)
        >>> messages = store.get_messages_since(group_id=123456, since=time.time()-3600)
    """
    
    def __init__(
        self, 
        db_path: str | Path | None = None,
        retention_days: int = 7
    ):
        """初始化消息存储。
        
        Args:
            db_path: 数据库路径，默认为 data/messages.db。
            retention_days: 消息保留天数。
        """
        if db_path is None:
            db_path = Path("data") / "messages.db"
        
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self._lock = threading.RLock()
        
        # 确保目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化数据库
        self._init_db()
    
    def _get_db(self) -> DatabaseManager:
        """获取数据库管理器。"""
        return get_db_manager(self.db_path)
    
    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        create_sql = """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id INTEGER,
                msg_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                group_id INTEGER DEFAULT 0,
                nickname TEXT,
                content TEXT,
                raw_message TEXT,
                timestamp REAL NOT NULL,
                msg_hash TEXT,
                reply_to INTEGER DEFAULT NULL,
                target_user_id INTEGER DEFAULT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_msg_time 
                ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_msg_user 
                ON messages(user_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_msg_group 
                ON messages(group_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_msg_hash 
                ON messages(msg_hash);
            CREATE INDEX IF NOT EXISTS idx_msg_type_time 
                ON messages(msg_type, timestamp);
        """
        self._get_db().init_tables(create_sql)
    
    async def initialize(self) -> None:
        """初始化存储服务（接口要求）。"""
        # 数据库已在 __init__ 中初始化
        pass
    
    async def close(self) -> None:
        """关闭存储服务。"""
        # 数据库连接由全局管理器管理
        pass
    
    async def health_check(self) -> bool:
        """健康检查。"""
        try:
            self._get_db().fetchone("SELECT 1")
            return True
        except Exception:
            return False
    
    def add_message(
        self,
        msg_type: str,
        user_id: int,
        group_id: int = 0,
        nickname: str = "",
        content: str = "",
        raw_message: str = "",
        msg_id: int = 0,
        timestamp: Optional[float] = None,
        reply_to: Optional[int] = None,
        target_user_id: Optional[int] = None
    ) -> bool:
        """添加一条消息到存储。
        
        Args:
            msg_type: 'private' 或 'group'。
            user_id: 发送者 QQ。
            group_id: 群号（私聊为 0）。
            nickname: 发送者昵称。
            content: 消息内容。
            raw_message: 原始消息。
            msg_id: 消息 ID。
            timestamp: 时间戳（默认当前时间）。
            reply_to: 引用的消息 ID。
            target_user_id: 对话目标用户 ID。
            
        Returns:
            是否成功添加（去重后可能跳过）。
        """
        if timestamp is None:
            timestamp = time.time()
        
        # 截断过长内容
        content = content[:2000] if content else ""
        raw_message = raw_message[:4000] if raw_message else ""
        
        # 创建消息对象计算哈希
        msg = Message(
            msg_id=msg_id,
            msg_type=msg_type,
            user_id=user_id,
            group_id=group_id,
            nickname=nickname,
            content=content,
            raw_message=raw_message,
            timestamp=timestamp,
            reply_to=reply_to,
            target_user_id=target_user_id
        )
        
        with self._lock:
            db = self._get_db()
            
            # 检查是否已存在（10秒内相同内容）
            existing = db.fetchone(
                "SELECT 1 FROM messages WHERE msg_hash = ? AND timestamp > ?",
                (msg.msg_hash, timestamp - 10)
            )
            if existing:
                return False
            
            # 插入消息
            db.execute(
                """INSERT INTO messages 
                   (msg_id, msg_type, user_id, group_id, nickname, content, 
                    raw_message, timestamp, msg_hash, reply_to, target_user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg.msg_id, msg.msg_type, msg.user_id, msg.group_id, msg.nickname,
                 msg.content, msg.raw_message, msg.timestamp, msg.msg_hash,
                 msg.reply_to, msg.target_user_id)
            )
            return True
    
    def get_messages_since(
        self,
        since: float,
        group_id: Optional[int] = None,
        user_id: Optional[int] = None,
        msg_type: Optional[str] = None,
        limit: int = 1000
    ) -> List[Message]:
        """获取指定时间之后的消息。
        
        Args:
            since: 起始时间戳。
            group_id: 筛选群号（可选）。
            user_id: 筛选用户（可选）。
            msg_type: 筛选消息类型（可选）。
            limit: 最大返回数量。
            
        Returns:
            消息列表。
        """
        conditions = ["timestamp >= ?"]
        params: List[Any] = [since]
        
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        if msg_type is not None:
            conditions.append("msg_type = ?")
            params.append(msg_type)
        
        where_clause = " AND ".join(conditions)
        sql = f"""SELECT msg_id, msg_type, user_id, group_id, nickname, content,
                         raw_message, timestamp, msg_hash, reply_to, target_user_id
                  FROM messages 
                  WHERE {where_clause}
                  ORDER BY timestamp ASC
                  LIMIT ?"""
        params.append(limit)
        
        rows = self._get_db().fetchall(sql, tuple(params))
        return [Message(**row) for row in rows]
    
    def get_messages_in_range(
        self,
        start: float,
        end: float,
        group_id: Optional[int] = None,
        user_id: Optional[int] = None
    ) -> List[Message]:
        """获取指定时间范围内的消息。
        
        Args:
            start: 起始时间戳。
            end: 结束时间戳。
            group_id: 筛选群号（可选）。
            user_id: 筛选用户（可选）。
            
        Returns:
            消息列表。
        """
        conditions = ["timestamp >= ?", "timestamp <= ?"]
        params: List[Any] = [start, end]
        
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        where_clause = " AND ".join(conditions)
        sql = f"""SELECT msg_id, msg_type, user_id, group_id, nickname, content,
                         raw_message, timestamp, msg_hash, reply_to, target_user_id
                  FROM messages 
                  WHERE {where_clause}
                  ORDER BY timestamp ASC"""
        
        rows = self._get_db().fetchall(sql, tuple(params))
        return [Message(**row) for row in rows]
    
    def get_message_stats(
        self,
        start: float,
        end: float,
        group_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """获取消息统计信息。
        
        Args:
            start: 起始时间戳。
            end: 结束时间戳。
            group_id: 群号（可选）。
            
        Returns:
            统计信息字典。
        """
        conditions = ["timestamp >= ?", "timestamp <= ?"]
        params: List[Any] = [start, end]
        
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        
        where_clause = " AND ".join(conditions)
        
        # 总消息数
        total = self._get_db().fetchval(
            f"SELECT COUNT(*) FROM messages WHERE {where_clause}",
            tuple(params),
            0
        )
        
        # 活跃用户
        active_users = self._get_db().fetchval(
            f"SELECT COUNT(DISTINCT user_id) FROM messages WHERE {where_clause}",
            tuple(params),
            0
        )
        
        # 消息类型分布
        type_dist = self._get_db().fetchall(
            f"""SELECT msg_type, COUNT(*) as count 
                FROM messages 
                WHERE {where_clause}
                GROUP BY msg_type""",
            tuple(params)
        )
        
        return {
            "total_messages": total,
            "active_users": active_users,
            "type_distribution": {row["msg_type"]: row["count"] for row in type_dist}
        }
    
    def cleanup_old_messages(self) -> int:
        """清理过期消息。
        
        Returns:
            删除的消息数量。
        """
        cutoff = time.time() - (self.retention_days * 86400)
        
        with self._lock:
            count = self._get_db().execute(
                "DELETE FROM messages WHERE timestamp < ?",
                (cutoff,)
            )
            return count
    
    def get_db_size(self) -> int:
        """获取数据库文件大小（字节）。
        
        Returns:
            文件大小。
        """
        try:
            return self.db_path.stat().st_size
        except FileNotFoundError:
            return 0


# 全局实例缓存
_message_store: Optional[MessageStore] = None


def get_message_store(
    db_path: str | Path | None = None,
    retention_days: int = 7
) -> MessageStore:
    """获取消息存储实例（单例模式）。
    
    Args:
        db_path: 数据库路径。
        retention_days: 保留天数。
        
    Returns:
        MessageStore 实例。
    """
    global _message_store
    if _message_store is None:
        _message_store = MessageStore(db_path, retention_days)
    return _message_store
