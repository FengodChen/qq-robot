#!/usr/bin/env python3
"""
SQLite 数据库 TUI 查看器
用于以只读方式浏览 data/ 目录下的 .db 文件
"""

import sqlite3
import os
from pathlib import Path
from typing import List, Tuple, Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import (
    Tree, DataTable, Static, Header, Footer,
    DirectoryTree, TabbedContent, TabPane, Label
)
from textual.reactive import reactive
from textual.binding import Binding


class DatabaseReader:
    """数据库读取器（只读模式）"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        
    def connect(self) -> bool:
        """连接数据库（只读模式）"""
        try:
            # 使用 URI 模式以只读方式打开
            uri = f"file:{self.db_path}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
            self.cursor = self.conn.cursor()
            return True
        except sqlite3.Error as e:
            return False
    
    def close(self):
        """关闭连接"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
    
    def get_tables(self) -> List[str]:
        """获取所有表名"""
        self.cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row[0] for row in self.cursor.fetchall()]
    
    def get_table_schema(self, table_name: str) -> List[Tuple]:
        """获取表结构"""
        self.cursor.execute(f"PRAGMA table_info({table_name})")
        return self.cursor.fetchall()
    
    def get_table_data(self, table_name: str, limit: int = 100, offset: int = 0) -> Tuple[List[str], List[Tuple]]:
        """获取表数据"""
        # 获取数据
        self.cursor.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (limit, offset))
        
        # 从查询结果获取列名（确保与数据列数一致）
        columns = [desc[0] for desc in self.cursor.description] if self.cursor.description else []
        rows = self.cursor.fetchall()
        
        return columns, rows
    
    def get_row_count(self, table_name: str) -> int:
        """获取表的总行数"""
        self.cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        return self.cursor.fetchone()[0]
    
    def execute_query(self, query: str) -> Tuple[List[str], List[Tuple]]:
        """执行自定义查询"""
        self.cursor.execute(query)
        columns = [desc[0] for desc in self.cursor.description] if self.cursor.description else []
        rows = self.cursor.fetchall()
        return columns, rows


class TableBrowser(Static):
    """表数据浏览器"""
    
    def __init__(self, db_reader: DatabaseReader, table_name: str):
        self.db_reader = db_reader
        self.table_name = table_name
        self.current_offset = 0
        self.page_size = 50
        self.columns = []
        super().__init__()
    
    def compose(self) -> ComposeResult:
        with Vertical():
            # 表信息和分页控制
            row_count = self.db_reader.get_row_count(self.table_name)
            schema = self.db_reader.get_table_schema(self.table_name)
            
            info_text = f"表: {self.table_name} | 总行数: {row_count} | 列数: {len(schema)}"
            yield Label(info_text, id="table_info")
            
            # 结构信息
            schema_text = " | ".join([f"{col[1]} ({col[2]})" for col in schema])
            yield Label(f"结构: {schema_text}", id="schema_info")
            
            # 数据表格
            yield DataTable(id="data_table")
            
            # 分页提示
            yield Label(f"每页显示 {self.page_size} 条，使用 [←/→] 翻页", id="page_info")
    
    def on_mount(self):
        """组件挂载时加载数据"""
        self.load_data()
    
    def load_data(self):
        """加载当前页数据"""
        table = self.query_one("#data_table", DataTable)
        # 完全清除表格（包括列定义）
        table.clear(True)
        
        columns, rows = self.db_reader.get_table_data(
            self.table_name, 
            limit=self.page_size, 
            offset=self.current_offset
        )
        
        self.columns = columns
        
        if not columns:
            return
        
        # 设置列
        for col in columns:
            table.add_column(col, width=min(30, max(10, len(col) + 2)))
        
        # 添加数据（截断长文本，确保列数匹配）
        for row in rows:
            display_row = []
            for i, val in enumerate(row):
                if i >= len(columns):
                    break  # 忽略超出列数的数据
                text = str(val) if val is not None else "NULL"
                # 截断过长的文本
                if len(text) > 100:
                    text = text[:97] + "..."
                display_row.append(text)
            
            # 如果数据列不足，用空字符串填充
            while len(display_row) < len(columns):
                display_row.append("")
            
            table.add_row(*display_row)
        
        # 更新分页信息
        row_count = self.db_reader.get_row_count(self.table_name)
        total_pages = (row_count + self.page_size - 1) // self.page_size
        current_page = self.current_offset // self.page_size + 1
        page_label = self.query_one("#page_info", Label)
        page_label.update(f"第 {current_page}/{total_pages} 页 | 共 {row_count} 条 | [←] 上一页 [→] 下一页")
    
    def action_prev_page(self):
        """上一页"""
        if self.current_offset >= self.page_size:
            self.current_offset -= self.page_size
            self.load_data()
    
    def action_next_page(self):
        """下一页"""
        row_count = self.db_reader.get_row_count(self.table_name)
        if self.current_offset + self.page_size < row_count:
            self.current_offset += self.page_size
            self.load_data()


class DatabaseViewer(App):
    """数据库查看器 TUI 应用"""
    
    CSS = """
    Screen { align: center middle; }
    
    #main_container {
        width: 100%;
        height: 100%;
    }
    
    #sidebar {
        width: 25%;
        height: 100%;
        border: solid $primary;
        padding: 1;
    }
    
    #content {
        width: 75%;
        height: 100%;
        border: solid $primary;
        padding: 1;
    }
    
    #db_tree {
        width: 100%;
        height: 90%;
    }
    
    #sidebar_title {
        height: auto;
        content-align: center middle;
        text-style: bold;
        background: $primary;
        color: $text;
        padding: 1;
    }
    
    #table_info {
        text-style: bold;
        background: $surface;
        padding: 1;
    }
    
    #schema_info {
        color: $text-muted;
        padding: 1;
    }
    
    #page_info {
        text-align: center;
        padding: 1;
        background: $surface-darken-1;
    }
    
    #data_table {
        width: 100%;
        height: 1fr;
    }
    
    #welcome {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
    }
    
    .db_node {
        text-style: bold;
    }
    
    .table_node {
        color: $success;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "退出", show=True),
        Binding("r", "refresh", "刷新", show=True),
        Binding("left", "prev_page", "上一页", show=False),
        Binding("right", "next_page", "下一页", show=False),
    ]
    
    current_db = reactive[DatabaseReader | None](None)
    current_table = reactive[str]("")
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.db_readers: dict[str, DatabaseReader] = {}
        self.db_tables: dict[str, List[str]] = {}
        super().__init__()
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with Horizontal(id="main_container"):
            # 左侧边栏：数据库和表列表
            with Vertical(id="sidebar"):
                yield Label("📁 数据库浏览器", id="sidebar_title")
                yield Tree("数据库", id="db_tree")
            
            # 右侧内容区
            with Vertical(id="content"):
                yield Static(
                    "欢迎使用 SQLite 数据库查看器\n\n"
                    "📂 从左侧选择数据库和表查看数据\n"
                    "⚠️  只读模式，不会修改任何数据\n\n"
                    "快捷键:\n"
                    "  [q] 退出\n"
                    "  [r] 刷新列表\n"
                    "  [←/→] 翻页",
                    id="welcome"
                )
        
        yield Footer()
    
    def on_mount(self):
        """应用挂载时加载数据库列表"""
        self.load_databases()
    
    def load_databases(self):
        """加载 data/ 目录下的所有 .db 文件"""
        tree = self.query_one("#db_tree", Tree)
        tree.clear()
        tree.root.expand()
        
        # 查找所有 .db 文件
        db_files = sorted(self.data_dir.glob("*.db"))
        
        if not db_files:
            tree.root.add_leaf("未找到 .db 文件")
            return
        
        for db_path in db_files:
            db_name = db_path.name
            reader = DatabaseReader(str(db_path))
            
            if reader.connect():
                self.db_readers[db_name] = reader
                tables = reader.get_tables()
                self.db_tables[db_name] = tables
                
                # 添加数据库节点
                db_node = tree.root.add(db_name, expand=False)
                db_node.allow_expand = True
                db_node.data = {"type": "db", "name": db_name}
                
                # 添加表节点
                for table_name in tables:
                    table_node = db_node.add_leaf(table_name)
                    table_node.data = {"type": "table", "db": db_name, "table": table_name}
                
                if not tables:
                    db_node.add_leaf("(空数据库)")
    
    def on_tree_node_selected(self, event: Tree.NodeSelected):
        """处理树节点选择事件"""
        node = event.node
        if not node.data:
            return
        
        data = node.data
        
        if data["type"] == "table":
            db_name = data["db"]
            table_name = data["table"]
            self.show_table(db_name, table_name)
    
    def show_table(self, db_name: str, table_name: str):
        """显示表数据"""
        # 更新当前状态
        self.current_db = self.db_readers.get(db_name)
        self.current_table = table_name
        
        # 清除欢迎信息
        content = self.query_one("#content", Vertical)
        content.remove_children()
        
        # 添加表浏览器
        if self.current_db:
            browser = TableBrowser(self.current_db, table_name)
            content.mount(browser)
    
    def action_refresh(self):
        """刷新数据库列表"""
        # 关闭现有连接
        for reader in self.db_readers.values():
            reader.close()
        self.db_readers.clear()
        self.db_tables.clear()
        
        # 重新加载
        self.load_databases()
        
        # 重置内容区
        content = self.query_one("#content", Vertical)
        content.remove_children()
        content.mount(Static(
            "欢迎使用 SQLite 数据库查看器\n\n"
            "📂 从左侧选择数据库和表查看数据\n"
            "⚠️  只读模式，不会修改任何数据\n\n"
            "快捷键:\n"
            "  [q] 退出\n"
            "  [r] 刷新列表\n"
            "  [←/→] 翻页",
            id="welcome"
        ))
    
    def action_prev_page(self):
        """上一页"""
        browser = self.query_one("TableBrowser")
        if browser:
            browser.action_prev_page()
    
    def action_next_page(self):
        """下一页"""
        browser = self.query_one("TableBrowser")
        if browser:
            browser.action_next_page()
    
    def on_unmount(self):
        """应用卸载时关闭所有连接"""
        for reader in self.db_readers.values():
            reader.close()


def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="SQLite 数据库 TUI 查看器 - 只读浏览 .db 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python db_viewer.py              # 使用默认 data/ 目录
  python db_viewer.py /path/to/db  # 指定数据库目录

快捷键:
  q          退出
  r          刷新列表
  ←/→        翻页
  ↑/↓        导航
  Enter      展开/选择
        """
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="data",
        help="包含 .db 文件的目录路径 (默认: data)"
    )
    
    args = parser.parse_args()
    
    if not os.path.isdir(args.directory):
        print(f"错误: 目录 '{args.directory}' 不存在")
        sys.exit(1)
    
    app = DatabaseViewer(args.directory)
    app.run()


if __name__ == "__main__":
    main()
