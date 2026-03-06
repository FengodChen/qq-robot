#!/usr/bin/env python3
"""
数据迁移脚本 - 将 JSON 数据文件迁移到 SQLite 数据库

用法:
    python migrate_json_to_db.py [--dry-run]

参数:
    --dry-run: 仅模拟迁移，不实际写入数据库
"""

import os
import sys
import json
import argparse

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from db_utils import get_db_manager, json_dumps
except Exception as e:
    print(f"[!] 导入数据库工具失败: {e}")
    sys.exit(1)


def migrate_user_modes(dry_run: bool = False) -> bool:
    """迁移 user_modes.json 到 user_modes.db"""
    json_path = os.path.join('data', 'user_modes.json')
    db_path = os.path.join('data', 'user_modes.db')
    
    if not os.path.exists(json_path):
        print(f"[*] user_modes.json 不存在，跳过")
        return True
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if dry_run:
            print(f"[*] [DRY-RUN] 将迁移 user_modes.json")
            print(f"    - 默认模式: {data.get('default_mode', '未设置')}")
            print(f"    - 用户数: {len(data.get('users', {}))}")
            return True
        
        # 初始化数据库
        db = get_db_manager(db_path)
        create_sql = """
            CREATE TABLE IF NOT EXISTS user_modes (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """
        db.init_tables(create_sql)
        
        # 保存默认模式
        default_mode = data.get('default_mode')
        if default_mode:
            db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ('default_mode', default_mode)
            )
        
        # 保存用户模式
        users = data.get('users', {})
        insert_data = []
        for key_str, mode_name in users.items():
            try:
                g, u = map(int, key_str.split(','))
                insert_data.append((g, u, mode_name))
            except Exception as e:
                print(f"[!] 解析用户模式键失败 ({key_str}): {e}")
                continue
        
        if insert_data:
            db.executemany(
                "INSERT OR REPLACE INTO user_modes (group_id, user_id, mode) VALUES (?, ?, ?)",
                insert_data
            )
        
        print(f"[✓] 成功迁移 user_modes.json -> user_modes.db")
        print(f"    - 默认模式: {default_mode}")
        print(f"    - 用户数: {len(insert_data)}")
        return True
        
    except Exception as e:
        print(f"[!] 迁移 user_modes.json 失败: {e}")
        return False


def migrate_affection_data(dry_run: bool = False) -> bool:
    """迁移 affection_data.json 到 affection_data.db"""
    json_path = os.path.join('data', 'affection_data.json')
    db_path = os.path.join('data', 'affection_data.db')
    
    if not os.path.exists(json_path):
        print(f"[*] affection_data.json 不存在，跳过")
        return True
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if dry_run:
            print(f"[*] [DRY-RUN] 将迁移 affection_data.json")
            print(f"    - 用户数: {len(data)}")
            return True
        
        # 初始化数据库
        db = get_db_manager(db_path)
        create_sql = """
            CREATE TABLE IF NOT EXISTS affection_data (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                value INTEGER DEFAULT 0,
                records TEXT,
                last_interaction INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, user_id)
            );
        """
        db.init_tables(create_sql)
        
        # 保存数据
        insert_data = []
        for key_str, user_data in data.items():
            try:
                group_id, user_id = map(int, key_str.split(','))
                value = user_data.get('value', 0)
                records = json_dumps(user_data.get('records', []))
                last_interaction = user_data.get('last_interaction', 0)
                insert_data.append((group_id, user_id, value, records, last_interaction))
            except Exception as e:
                print(f"[!] 解析好感度键失败 ({key_str}): {e}")
                continue
        
        if insert_data:
            db.executemany(
                """INSERT OR REPLACE INTO affection_data 
                   (group_id, user_id, value, records, last_interaction) 
                   VALUES (?, ?, ?, ?, ?)""",
                insert_data
            )
        
        print(f"[✓] 成功迁移 affection_data.json -> affection_data.db")
        print(f"    - 用户数: {len(insert_data)}")
        return True
        
    except Exception as e:
        print(f"[!] 迁移 affection_data.json 失败: {e}")
        return False


def migrate_chat_history(dry_run: bool = False) -> bool:
    """迁移 chat_history.json 到 chat_history.db"""
    json_path = os.path.join('data', 'chat_history.json')
    db_path = os.path.join('data', 'chat_history.db')
    
    if not os.path.exists(json_path):
        print(f"[*] chat_history.json 不存在，跳过")
        return True
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        contexts_data = data.get("contexts", {})
        prompts_data = data.get("custom_prompts", {})
        
        if dry_run:
            print(f"[*] [DRY-RUN] 将迁移 chat_history.json")
            print(f"    - 用户历史数: {len(contexts_data)}")
            print(f"    - 自定义人设数: {len(prompts_data)}")
            return True
        
        # 初始化数据库
        db = get_db_manager(db_path)
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
        
        # 保存对话历史
        insert_contexts = []
        for key_str, messages in contexts_data.items():
            try:
                group_id, user_id = map(int, key_str.split(','))
                messages_json = json_dumps(messages)
                insert_contexts.append((group_id, user_id, messages_json))
            except Exception as e:
                print(f"[!] 解析聊天记录键失败 ({key_str}): {e}")
                continue
        
        if insert_contexts:
            db.executemany(
                "INSERT OR REPLACE INTO chat_contexts (group_id, user_id, messages) VALUES (?, ?, ?)",
                insert_contexts
            )
        
        # 保存自定义人设
        insert_prompts = []
        for key_str, prompt in prompts_data.items():
            try:
                group_id, user_id = map(int, key_str.split(','))
                insert_prompts.append((group_id, user_id, prompt))
            except Exception as e:
                print(f"[!] 解析自定义人设键失败 ({key_str}): {e}")
                continue
        
        if insert_prompts:
            db.executemany(
                "INSERT OR REPLACE INTO custom_prompts (group_id, user_id, prompt) VALUES (?, ?, ?)",
                insert_prompts
            )
        
        print(f"[✓] 成功迁移 chat_history.json -> chat_history.db")
        print(f"    - 用户历史数: {len(insert_contexts)}")
        print(f"    - 自定义人设数: {len(insert_prompts)}")
        return True
        
    except Exception as e:
        print(f"[!] 迁移 chat_history.json 失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='迁移 JSON 数据到 SQLite 数据库')
    parser.add_argument('--dry-run', action='store_true', help='仅模拟迁移，不实际写入')
    parser.add_argument('--backup', action='store_true', default=True, help='迁移后备份原 JSON 文件（默认启用）')
    args = parser.parse_args()
    
    print("=" * 60)
    print("JSON 到 SQLite 数据迁移工具")
    print("=" * 60)
    
    if args.dry_run:
        print("[*] 模拟模式（不会实际写入数据库）\n")
    
    # 执行迁移
    results = []
    results.append(migrate_user_modes(dry_run=args.dry_run))
    results.append(migrate_affection_data(dry_run=args.dry_run))
    results.append(migrate_chat_history(dry_run=args.dry_run))
    
    print("\n" + "=" * 60)
    if all(results):
        print("所有迁移成功完成！")
        
        # 备份原 JSON 文件
        if not args.dry_run and args.backup:
            print("\n[*] 备份原 JSON 文件...")
            import shutil
            from datetime import datetime
            
            backup_dir = os.path.join('data', f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
            os.makedirs(backup_dir, exist_ok=True)
            
            for json_file in ['user_modes.json', 'affection_data.json', 'chat_history.json']:
                json_path = os.path.join('data', json_file)
                if os.path.exists(json_path):
                    backup_path = os.path.join(backup_dir, json_file)
                    shutil.move(json_path, backup_path)
                    print(f"    - {json_file} -> {backup_path}")
        
        print("\n[✓] 迁移完成！现在可以使用新的数据库文件了。")
        return 0
    else:
        print("[!] 部分迁移失败，请检查错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
