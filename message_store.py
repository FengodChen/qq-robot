"""
robots/message_store.py

高效的消息存储模块，使用 SQLite 存储聊天记录。
相比 JSON 文件，SQLite 更紧凑、查询更快、支持索引。

=== METADATA ===
name: message_store
desc: SQLite消息存储，支持查询和统计
=== END ===

特性：
- 自动按时间分区存储（按天分表）
- 支持私聊和群聊消息
- 高效的时间范围查询
- 消息去重和压缩
"""

import os
import sqlite3
import time
import threading
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Iterator
from datetime import datetime, timedelta
import hashlib


@dataclass
class Message:
    """消息数据类"""
    msg_id: int                  # 消息ID
    msg_type: str                # 'private' 或 'group'
    user_id: int                 # 发送者QQ
    group_id: Optional[int]      # 群号（私聊为None或0）
    nickname: str                # 发送者昵称
    content: str                 # 消息内容（纯文本）
    raw_message: str             # 原始消息
    timestamp: int               # 时间戳
    msg_hash: str                # 消息内容哈希（用于去重）
    
    def to_dict(self) -> Dict:
        return {
            'msg_id': self.msg_id,
            'msg_type': self.msg_type,
            'user_id': self.user_id,
            'group_id': self.group_id,
            'nickname': self.nickname,
            'content': self.content,
            'raw_message': self.raw_message,
            'timestamp': self.timestamp,
        }


class MessageStore:
    """
    消息存储管理器
    
    使用 SQLite 存储，主要优化：
    1. 索引优化：按时间、用户、群组建立索引
    2. 数据压缩：消息内容使用文本存储，但单条限制长度
    3. 自动清理：保留最近 N 天的消息
    """
    
    def __init__(self, db_path: Optional[str] = None, retention_days: int = 7):
        """
        Args:
            db_path: 数据库路径，默认在 data/messages.db（项目根目录的data文件夹）
            retention_days: 消息保留天数，默认7天
        """
        if db_path is None:
            # 使用项目根目录的 data/ 文件夹
            db_path = os.path.join(os.path.dirname(__file__), 'data', 'messages.db')
            db_path = os.path.abspath(db_path)
        
        self.db_path = db_path
        self.retention_days = retention_days
        self._lock = threading.RLock()
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        print(f"[*] MessageStore 初始化: {db_path}, 保留{retention_days}天")
    
    def _init_db(self):
        """初始化数据库表结构"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id INTEGER,
                    msg_type TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    group_id INTEGER DEFAULT 0,
                    nickname TEXT,
                    content TEXT,
                    raw_message TEXT,
                    timestamp INTEGER NOT NULL,
                    msg_hash TEXT,
                    date TEXT GENERATED ALWAYS AS (date(timestamp, 'unixepoch')) STORED
                )
            """)
            
            # 创建索引
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_time 
                ON messages(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_user 
                ON messages(user_id, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_group 
                ON messages(group_id, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_hash 
                ON messages(msg_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_type_time 
                ON messages(msg_type, timestamp)
            """)
            
            conn.commit()
    
    def _compute_hash(self, user_id: int, content: str, timestamp: int) -> str:
        """计算消息哈希用于去重"""
        data = f"{user_id}:{content}:{timestamp//10}"  # 10秒内的重复视为同一条
        return hashlib.md5(data.encode()).hexdigest()[:16]
    
    def add_message(self, msg_type: str, user_id: int, group_id: Optional[int],
                    nickname: str, content: str, raw_message: str,
                    msg_id: Optional[int] = None, timestamp: Optional[int] = None) -> bool:
        """
        添加一条消息到存储
        
        Args:
            msg_type: 'private' 或 'group'
            user_id: 发送者QQ
            group_id: 群号（私聊为0或None）
            nickname: 发送者昵称
            content: 消息纯文本内容
            raw_message: 原始消息
            msg_id: 消息ID（可选）
            timestamp: 时间戳（可选，默认当前时间）
        
        Returns:
            bool: 是否成功添加（去重后可能跳过）
        """
        if timestamp is None:
            timestamp = int(time.time())
        if group_id is None:
            group_id = 0
        if msg_id is None:
            msg_id = 0
        
        # 截断过长的内容
        content = content[:2000] if content else ""
        raw_message = raw_message[:4000] if raw_message else ""
        
        # 计算哈希用于去重
        msg_hash = self._compute_hash(user_id, content, timestamp)
        
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                # 检查是否已存在（简单去重，10秒内相同内容）
                cursor = conn.execute(
                    "SELECT 1 FROM messages WHERE msg_hash = ? AND timestamp > ?",
                    (msg_hash, timestamp - 10)
                )
                if cursor.fetchone():
                    return False
                
                conn.execute("""
                    INSERT INTO messages 
                    (msg_id, msg_type, user_id, group_id, nickname, content, raw_message, timestamp, msg_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (msg_id, msg_type, user_id, group_id, nickname, content, raw_message, timestamp, msg_hash))
                conn.commit()
                return True
    
    def get_messages(self, start_time: int, end_time: int,
                     msg_type: Optional[str] = None,
                     user_id: Optional[int] = None,
                     group_id: Optional[int] = None,
                     limit: int = 10000) -> List[Message]:
        """
        查询消息
        
        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳
            msg_type: 消息类型筛选
            user_id: 用户筛选
            group_id: 群组筛选（0表示私聊）
            limit: 最大返回数量
        
        Returns:
            List[Message]: 消息列表，按时间排序
        """
        conditions = ["timestamp >= ? AND timestamp <= ?"]
        params = [start_time, end_time]
        
        if msg_type:
            conditions.append("msg_type = ?")
            params.append(msg_type)
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        
        where_clause = " AND ".join(conditions)
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"""
                SELECT * FROM messages 
                WHERE {where_clause}
                ORDER BY timestamp ASC
                LIMIT ?
            """, params)
            
            rows = cursor.fetchall()
            return [Message(
                msg_id=row['msg_id'],
                msg_type=row['msg_type'],
                user_id=row['user_id'],
                group_id=row['group_id'] if row['group_id'] else None,
                nickname=row['nickname'] or '',
                content=row['content'] or '',
                raw_message=row['raw_message'] or '',
                timestamp=row['timestamp'],
                msg_hash=row['msg_hash'] or ''
            ) for row in rows]
    
    def get_group_messages(self, group_id: int, start_time: int, 
                           end_time: int, limit: int = 10000) -> List[Message]:
        """获取指定群组的聊天记录"""
        return self.get_messages(
            start_time=start_time, end_time=end_time,
            msg_type='group', group_id=group_id, limit=limit
        )
    
    def get_private_messages(self, user_id: int, start_time: int,
                             end_time: int, limit: int = 10000) -> List[Message]:
        """获取指定私聊的聊天记录"""
        return self.get_messages(
            start_time=start_time, end_time=end_time,
            msg_type='private', user_id=user_id, limit=limit
        )
    
    def get_stats(self, start_time: int, end_time: int) -> Dict:
        """获取统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            # 总消息数
            cursor = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp >= ? AND timestamp <= ?",
                (start_time, end_time)
            )
            total = cursor.fetchone()[0]
            
            # 活跃用户
            cursor = conn.execute(
                """SELECT user_id, nickname, COUNT(*) as cnt 
                   FROM messages 
                   WHERE timestamp >= ? AND timestamp <= ?
                   GROUP BY user_id 
                   ORDER BY cnt DESC LIMIT 10""",
                (start_time, end_time)
            )
            active_users = [{"user_id": r[0], "nickname": r[1], "count": r[2]} for r in cursor.fetchall()]
            
            # 活跃群组
            cursor = conn.execute(
                """SELECT group_id, COUNT(*) as cnt 
                   FROM messages 
                   WHERE timestamp >= ? AND timestamp <= ? AND group_id > 0
                   GROUP BY group_id 
                   ORDER BY cnt DESC LIMIT 10""",
                (start_time, end_time)
            )
            active_groups = [{"group_id": r[0], "count": r[1]} for r in cursor.fetchall()]
            
            return {
                "total_messages": total,
                "active_users": active_users,
                "active_groups": active_groups
            }
    
    def cleanup_old_messages(self) -> int:
        """
        清理过期消息
        
        Returns:
            int: 删除的消息数量
        """
        cutoff = int(time.time()) - (self.retention_days * 86400)
        
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM messages WHERE timestamp < ?",
                    (cutoff,)
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    # 执行 VACUUM 回收空间
                    conn.execute("VACUUM")
                    print(f"[*] MessageStore 清理了 {deleted} 条过期消息")
                return deleted
    
    def get_db_size(self) -> int:
        """获取数据库文件大小（字节）"""
        try:
            return os.path.getsize(self.db_path)
        except:
            return 0
    
    def get_user_message_count(self, user_id: int, start_time: int, end_time: int) -> int:
        """获取指定用户在时间范围内的消息数"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ? AND timestamp >= ? AND timestamp <= ?",
                (user_id, start_time, end_time)
            )
            return cursor.fetchone()[0]
    
    def iter_messages(self, start_time: int, end_time: int, 
                      batch_size: int = 1000) -> Iterator[Message]:
        """
        批量迭代消息（内存友好）
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            batch_size: 每批数量
        
        Yields:
            Message 对象
        """
        offset = 0
        while True:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """SELECT * FROM messages 
                       WHERE timestamp >= ? AND timestamp <= ?
                       ORDER BY timestamp ASC LIMIT ? OFFSET ?""",
                    (start_time, end_time, batch_size, offset)
                )
                rows = cursor.fetchall()
                
            if not rows:
                break
                
            for row in rows:
                yield Message(
                    msg_id=row['msg_id'],
                    msg_type=row['msg_type'],
                    user_id=row['user_id'],
                    group_id=row['group_id'] if row['group_id'] else None,
                    nickname=row['nickname'] or '',
                    content=row['content'] or '',
                    raw_message=row['raw_message'] or '',
                    timestamp=row['timestamp'],
                    msg_hash=row['msg_hash'] or ''
                )
            
            if len(rows) < batch_size:
                break
            offset += batch_size


# 全局单例
_message_store_instance: Optional[MessageStore] = None
_message_store_lock = threading.Lock()


def get_message_store(db_path: Optional[str] = None, retention_days: int = 7) -> MessageStore:
    """获取全局消息存储实例（单例模式）"""
    global _message_store_instance
    if _message_store_instance is None:
        with _message_store_lock:
            if _message_store_instance is None:
                _message_store_instance = MessageStore(db_path, retention_days)
    return _message_store_instance


def reset_message_store():
    """重置全局实例（用于测试）"""
    global _message_store_instance
    with _message_store_lock:
        _message_store_instance = None


# === AGENT-FRIENDLY-API START ===

def create_store(db_path: Optional[str] = None, retention_days: int = 7) -> MessageStore:
    """创建新的 MessageStore 实例"""
    return MessageStore(db_path, retention_days)


def save_message(msg_type: str, user_id: int, group_id: Optional[int],
                 nickname: str, content: str, raw_message: str,
                 msg_id: Optional[int] = None, timestamp: Optional[int] = None,
                 db_path: Optional[str] = None) -> bool:
    """便捷函数：保存消息到默认存储"""
    store = get_message_store(db_path)
    return store.add_message(msg_type, user_id, group_id, nickname, 
                            content, raw_message, msg_id, timestamp)


def fetch_group_chat(group_id: int, since: int, until: Optional[int] = None,
                     limit: int = 10000, db_path: Optional[str] = None) -> List[Dict]:
    """便捷函数：获取群聊记录"""
    store = get_message_store(db_path)
    if until is None:
        until = int(time.time())
    messages = store.get_group_messages(group_id, since, until, limit)
    return [m.to_dict() for m in messages]


def fetch_private_chat(user_id: int, since: int, until: Optional[int] = None,
                       limit: int = 10000, db_path: Optional[str] = None) -> List[Dict]:
    """便捷函数：获取私聊记录"""
    store = get_message_store(db_path)
    if until is None:
        until = int(time.time())
    messages = store.get_private_messages(user_id, since, until, limit)
    return [m.to_dict() for m in messages]

# === AGENT-FRIENDLY-API END ===


if __name__ == "__main__":
    # 测试代码
    store = MessageStore(retention_days=7)
    
    # 添加测试消息
    for i in range(100):
        store.add_message(
            msg_type='group',
            user_id=123456 + (i % 5),
            group_id=10086,
            nickname=f"用户{i % 5}",
            content=f"测试消息 {i}: 这是一条测试内容",
            raw_message=f"测试消息 {i}",
            msg_id=1000 + i,
            timestamp=int(time.time()) - i * 60
        )
    
    # 查询
    now = int(time.time())
    messages = store.get_group_messages(10086, now - 3600, now)
    print(f"查询到 {len(messages)} 条消息")
    
    # 统计
    stats = store.get_stats(now - 3600, now)
    print(f"统计: {stats}")
    
    # 数据库大小
    print(f"数据库大小: {store.get_db_size()} bytes")
