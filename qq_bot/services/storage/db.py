"""数据库工具模块。

提供 SQLite 数据库连接管理和常用操作。
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DatabaseManager:
    """数据库管理器。
    
    管理单个 SQLite 数据库的连接，提供线程安全的操作接口。
    
    Example:
        >>> db = DatabaseManager("data/bot.db")
        >>> db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        >>> user = db.fetchone("SELECT * FROM users WHERE id = ?", (1,))
    """
    
    def __init__(self, db_path: str | Path):
        """初始化数据库管理器。
        
        Args:
            db_path: 数据库文件路径。
        """
        self.db_path = Path(db_path).resolve()
        self._local = threading.local()
        self._lock = threading.RLock()
        
        # 确保目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地连接。"""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path, 
                check_same_thread=False
            )
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection
    
    @contextmanager
    def _get_cursor(self):
        """获取数据库游标的上下文管理器。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
    
    def execute(self, sql: str, parameters: Tuple = ()) -> int:
        """执行 SQL 语句。
        
        Args:
            sql: SQL 语句。
            parameters: 参数元组。
            
        Returns:
            影响的行数。
        """
        with self._lock:
            with self._get_cursor() as cursor:
                cursor.execute(sql, parameters)
                return cursor.rowcount
    
    def executemany(self, sql: str, parameters: List[Tuple]) -> int:
        """批量执行 SQL 语句。
        
        Args:
            sql: SQL 语句。
            parameters: 参数列表。
            
        Returns:
            影响的行数。
        """
        with self._lock:
            with self._get_cursor() as cursor:
                cursor.executemany(sql, parameters)
                return cursor.rowcount
    
    def fetchone(self, sql: str, parameters: Tuple = ()) -> Optional[Dict[str, Any]]:
        """查询单条记录。
        
        Args:
            sql: SQL 语句。
            parameters: 参数元组。
            
        Returns:
            记录字典，如果没有找到则返回 None。
        """
        with self._get_cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def fetchall(self, sql: str, parameters: Tuple = ()) -> List[Dict[str, Any]]:
        """查询多条记录。
        
        Args:
            sql: SQL 语句。
            parameters: 参数元组。
            
        Returns:
            记录字典列表。
        """
        with self._get_cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def fetchval(
        self, 
        sql: str, 
        parameters: Tuple = (), 
        default: Any = None
    ) -> Any:
        """查询单个值。
        
        Args:
            sql: SQL 语句。
            parameters: 参数元组。
            default: 默认值。
            
        Returns:
            查询结果的第一个值，如果没有找到则返回默认值。
        """
        with self._get_cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
            if row:
                return row[0]
            return default
    
    def close(self) -> None:
        """关闭当前线程的连接。"""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
    
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在。
        
        Args:
            table_name: 表名。
            
        Returns:
            表是否存在。
        """
        sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
        return self.fetchval(sql, (table_name,), False) is not False
    
    def init_tables(self, create_sql: str) -> None:
        """初始化表结构。
        
        Args:
            create_sql: 创建表的 SQL 脚本。
        """
        with self._get_cursor() as cursor:
            cursor.executescript(create_sql)
    
    def get_table_info(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表结构信息。
        
        Args:
            table_name: 表名。
            
        Returns:
            列信息列表。
        """
        return self.fetchall(f"PRAGMA table_info({table_name})")


# 全局数据库管理器缓存
_db_managers: Dict[str, DatabaseManager] = {}
_db_lock = threading.Lock()


def get_db_manager(db_path: str | Path) -> DatabaseManager:
    """获取数据库管理器实例（单例模式）。
    
    Args:
        db_path: 数据库文件路径。
        
    Returns:
        DatabaseManager 实例。
    """
    abs_path = str(Path(db_path).resolve())
    with _db_lock:
        if abs_path not in _db_managers:
            _db_managers[abs_path] = DatabaseManager(abs_path)
        return _db_managers[abs_path]


def close_all_databases() -> None:
    """关闭所有数据库连接。"""
    with _db_lock:
        for manager in _db_managers.values():
            manager.close()
        _db_managers.clear()


# JSON 辅助函数
import json


def json_dumps(data: Any) -> str:
    """将数据序列化为 JSON 字符串。"""
    return json.dumps(data, ensure_ascii=False)


def json_loads(data: str | None) -> Any:
    """将 JSON 字符串反序列化为数据。"""
    if data is None:
        return None
    return json.loads(data)
