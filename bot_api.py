#!/usr/bin/env python3
"""
Bot API Coordinator: loads robot modules from robots/ and routes messages.
Supports automatic mode selection via Bot Agent.

=== METADATA ===
name: bot_api
desc: 机器人API协调器，加载模块、路由消息、自动选择模式执行功能
modes: chat(聊天对话,支持人设定制), summary(聊天记录总结)
=== END ===
"""

import os
import sys
import json
import re
import time
import asyncio
import importlib
import glob
import threading

import websockets

# 导入配置加载模块
from config_loader import load_config

# 导入数据库工具
try:
    from db_utils import get_db_manager
    DB_UTILS_AVAILABLE = True
except Exception as e:
    print(f"[!] 数据库工具加载失败: {e}")
    DB_UTILS_AVAILABLE = False

# 导入消息存储模块（已移到主目录）
try:
    from message_store import get_message_store
    MESSAGE_STORE_AVAILABLE = True
except Exception as e:
    print(f"[!] 消息存储模块加载失败: {e}")
    MESSAGE_STORE_AVAILABLE = False

# 导入Bot Agent（自然语言处理）
try:
    from bot_agent import BotAgent, create_agent
    BOT_AGENT_AVAILABLE = True
except Exception as e:
    print(f"[!] Bot Agent 加载失败: {e}")
    BOT_AGENT_AVAILABLE = False

# Try to import BotConfig from chat module for default configuration
try:
    from robots.chat import BotConfig
except Exception:
    from dataclasses import dataclass, field
    @dataclass
    class BotConfig:
        napcat_ws_url: str = ""
        listen_host: str = ""
        listen_port: int = 0
        token: str = ""
        deepseek_api_key: str = ""
        system_prompt: str = ""
        max_context: int = 10
        max_input_tokens: int = 100
        max_output_tokens: int = 300
        max_prompt_tokens: int = 500
        max_workers: int = 1
        # 每日定时总结配置
        daily_summary_enabled: bool = False
        daily_summary_group_id: int = 0
        daily_summary_max_tokens: int = 0
        daily_summary_hour: int = 0
        daily_summary_minute: int = 0
        # 消息存储配置
        message_retention_days: int = 7


class ModeManager:
    """Coordinator that manages modes (robot modules) and routes incoming messages."""
    def __init__(self, config: BotConfig = None):
        self.config = config or BotConfig()
        self.send_ws = None
        self.recv_ws = None
        self.self_id = None
        self.pending_responses = {}
        self.modes = {}  # name -> module
        self.current_mode = None  # 全局默认模式名
        self.current_robot = None  # 全局默认机器人实例
        # 每个 (group_id, user_id) 的独立模式与机器人实例
        self.user_modes = {}  # (group_id, user_id) -> {'mode': name, 'robot': instance}
        self.user_modes_db = os.path.join(os.path.dirname(__file__), 'data', 'user_modes.db')
        self.user_modes_db = os.path.abspath(self.user_modes_db)
        # 初始化数据库
        self._init_user_modes_db()
        # 共享 executor，用于节省资源
        self.shared_executor = None
        # 延迟加载 user_modes（由 _load_user_modes 处理），将在 load_modes 后调用以确保模式已导入
        # 在 load_modes() 的末尾会调用 _load_user_modes()
        # 消息存储
        self.message_store = None
        self._cleanup_timer = None
        if MESSAGE_STORE_AVAILABLE:
            try:
                retention_days = getattr(config, 'message_retention_days', 7)
                self.message_store = get_message_store(retention_days=retention_days)
                print("[*] 消息存储已启用")
            except Exception as e:
                print(f"[!] 消息存储初始化失败: {e}")
        
        # 初始化 Bot Agent（自然语言处理）
        self.bot_agent = None
        if BOT_AGENT_AVAILABLE:
            try:
                api_key = getattr(config, 'deepseek_api_key', None)
                self.bot_agent = create_agent(api_key=api_key, mode_manager=self)
                print("[*] Bot Agent 已启用（自然语言交互）")
            except Exception as e:
                print(f"[!] Bot Agent 初始化失败: {e}")
        
        # 初始化每日定时总结机器人
        self.daily_summary_robot = None
        self._daily_summary_config = {
            'enabled': getattr(config, 'daily_summary_enabled', False),
            'group_id': getattr(config, 'daily_summary_group_id', 0),
            'max_tokens': getattr(config, 'daily_summary_max_tokens', 0),
            'hour': getattr(config, 'daily_summary_hour', 0),
            'minute': getattr(config, 'daily_summary_minute', 0),
        }
        
        # 用户性别/昵称信息缓存: {(group_id, user_id): {'sex': 'male'/'female', 'nickname': '...', 'card': '...', 'timestamp': 1234567890}}
        self._user_info_cache = {}
        self._user_info_cache_ttl = 3600  # 缓存有效期1小时

    async def connect_sender(self):
        headers = {"Authorization": f"Bearer {self.config.token}"} if self.config.token else {}
        try:
            self.send_ws = await websockets.connect(self.config.napcat_ws_url, additional_headers=headers)
            print(f"✓ 已连接到 NapCat 正向 WS")
            asyncio.create_task(self._handle_send_responses())
        except Exception as e:
            print(f"✗ 连接正向 WS 失败: {e}")

    async def _handle_send_responses(self):
        try:
            async for message in self.send_ws:
                try:
                    data = json.loads(message)
                    echo = data.get("echo")
                    if echo and echo in self.pending_responses:
                        fut = self.pending_responses.pop(echo)
                        if not fut.done():
                            fut.set_result(data)
                except:
                    pass
        except:
            pass

    async def send_api(self, action: str, params: dict) -> dict:
        if not self.send_ws:
            return {}
        echo = f"req_{int(time.time() * 1000)}"
        request = {"action": action, "params": params, "echo": echo}
        future = asyncio.get_event_loop().create_future()
        self.pending_responses[echo] = future
        try:
            await self.send_ws.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout=5.0)
        except asyncio.TimeoutError:
            return {}
        except Exception as e:
            print(f"[!] API 请求失败: {e}")
            return {}
        finally:
            self.pending_responses.pop(echo, None)

    async def send_private_msg(self, user_id: int, message: str):
        print(f"[发送私聊] -> {user_id}: {message[:50]}...")
        result = await self.send_api("send_private_msg", {
            "user_id": user_id,
            "message": [{"type": "text", "data": {"text": message}}]
        })
        return result

    async def send_group_reply(self, group_id: int, user_id: int, message_id: int, message: str):
        print(f"[发送群聊] -> 群{group_id} 引用{message_id} @ {user_id}: {message[:50]}...")
        msg_segments = [
            {"type": "reply", "data": {"id": str(message_id)}},
            {"type": "at", "data": {"qq": str(user_id)}},
            {"type": "text", "data": {"text": f" {message}"}}
        ]
        result = await self.send_api("send_group_msg", {
            "group_id": group_id,
            "message": msg_segments
        })
        return result

    async def send_group_msg(self, group_id: int, message: str):
        """直接发送群消息（不带引用和@）"""
        print(f"[发送群聊] -> 群{group_id}: {message[:50]}...")
        result = await self.send_api("send_group_msg", {
            "group_id": group_id,
            "message": [{"type": "text", "data": {"text": message}}]
        })
        return result

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        """获取群成员详细信息（包括性别）"""
        result = await self.send_api("get_group_member_info", {
            "group_id": group_id,
            "user_id": user_id,
            "no_cache": True
        })
        if result and result.get("status") == "ok":
            return result.get("data", {})
        return {}

    def _get_cached_user_info(self, group_id: int, user_id: int) -> dict:
        """获取缓存的用户信息，如果过期则返回None"""
        key = (group_id, user_id)
        cached = self._user_info_cache.get(key)
        if cached:
            # 检查缓存是否过期
            if time.time() - cached.get('timestamp', 0) < self._user_info_cache_ttl:
                return cached
        return None

    def _set_cached_user_info(self, group_id: int, user_id: int, info: dict):
        """设置用户信息缓存"""
        key = (group_id, user_id)
        self._user_info_cache[key] = {
            'sex': info.get('sex', 'unknown'),
            'nickname': info.get('nickname', ''),
            'card': info.get('card', ''),
            'timestamp': time.time()
        }

    async def get_user_info_with_cache(self, group_id: int, user_id: int, sender: dict = None) -> dict:
        """
        获取用户信息（带缓存），优先从缓存获取，如果没有则调用API
        返回: {'sex': '...', 'nickname': '...', 'card': '...'}
        """
        # 首先检查缓存
        cached = self._get_cached_user_info(group_id, user_id)
        if cached:
            print(f"[*] 使用缓存的用户信息: group={group_id}, user={user_id}, sex={cached.get('sex')}")
            return cached
        
        # 尝试从 sender 获取
        sex = sender.get('sex', 'unknown') if sender else 'unknown'
        nickname = sender.get('nickname', '未知') if sender else '未知'
        card = sender.get('card', '') if sender else ''
        
        # 如果性别信息缺失或为unknown，调用API获取
        if not sex or sex == 'unknown':
            try:
                member_info = await self.get_group_member_info(group_id, user_id)
                if member_info:
                    sex = member_info.get('sex', 'unknown') or 'unknown'
                    nickname = member_info.get('nickname', nickname) or nickname
                    card = member_info.get('card', card) or card
                    print(f"[*] 通过API获取到用户信息: group={group_id}, user={user_id}, sex={sex}")
            except Exception as e:
                print(f"[!] 获取群成员信息失败: {e}")
        
        # 构建结果并缓存
        result = {'sex': sex, 'nickname': nickname, 'card': card}
        self._set_cached_user_info(group_id, user_id, result)
        return result

    def extract_text(self, message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return "".join(seg.get("data", {}).get("text", "") 
                          for seg in message if seg.get("type") == "text")
        return str(message)

    def load_modes(self):
        base = os.path.join(os.path.dirname(__file__), "robots")
        py_files = glob.glob(os.path.join(base, "*.py"))
        for p in py_files:
            name = os.path.basename(p)[:-3]
            if name == "__init__":
                continue
            try:
                module = importlib.import_module(f"robots.{name}")
                if hasattr(module, "create_robot"):
                    self.modes[name] = module
            except Exception as e:
                print(f"[!] 导入 robots.{name} 失败: {e}")
        if not self.modes:
            print("[!] 未发现 robots 模块")
        else:
            print(f"[*] 发现模式: {', '.join(self.modes.keys())}")
        # 创建共享 executor 供机器人复用以节省资源（仅在未创建时）
        try:
            from concurrent.futures import ThreadPoolExecutor
            if not hasattr(self, 'shared_executor') or self.shared_executor is None:
                self.shared_executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
        except Exception as e:
            print(f"[!] 创建共享 executor 失败: {e}")
        # 在模式加载完后，加载持久化的 user_modes（此时 modes 已可用）
        self._load_user_modes()

    def get_user_robot(self, group_id: int, user_id: int):
        """返回指定 (group_id, user_id) 的机器人实例；若不存在则基于当前默认模式创建一个。"""
        key = (group_id, user_id)
        entry = self.user_modes.get(key)
        if entry and entry.get('robot'):
            return entry.get('robot')
        # 确定要使用的模式：优先使用用户已选模式，再用全局 current_mode，否则选 'chat' 或第一个可用模式
        mode_name = entry.get('mode') if entry else None
        if not mode_name:
            mode_name = self.current_mode
        if not mode_name and self.modes:
            mode_name = 'chat' if 'chat' in self.modes else next(iter(self.modes.keys()))
        if not mode_name or mode_name not in self.modes:
            return None
        try:
            module = self.modes[mode_name]
            # 提供共享 executor 给机器人以节省资源
            cfg = self.config
            # 如果 ModeManager 有 shared_executor 属性则注入
            if hasattr(self, 'shared_executor') and self.shared_executor is not None:
                try:
                    # 复制配置并设置 shared_executor
                    cfg = type(self.config)(**vars(self.config))
                    setattr(cfg, 'shared_executor', self.shared_executor)
                except Exception:
                    cfg = self.config
            robot = module.create_robot(cfg)
            # 缓存机器人实例
            self.user_modes[key] = {'mode': mode_name, 'robot': robot}
            # 保存仅模式名持久化，不持久化实例
            self._save_user_modes()
            return robot
        except Exception as e:
            print(f"[!] 创建用户机器人失败 ({group_id},{user_id}): {e}")
            return None

    def _init_user_modes_db(self):
        """初始化 user_modes 数据库表"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.user_modes_db)
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
        except Exception as e:
            print(f"[!] 初始化 user_modes 数据库失败: {e}")

    def _load_user_modes(self):
        """从数据库加载 user_modes（只包含模式名），机器人实例按需创建"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.user_modes_db)
            
            # 加载默认模式
            default_mode = db.fetchval("SELECT value FROM config WHERE key = 'default_mode'")
            if default_mode:
                self.current_mode = default_mode
            
            # 加载用户模式
            users = db.fetchall("SELECT group_id, user_id, mode FROM user_modes")
            for row in users:
                try:
                    key = (row['group_id'], row['user_id'])
                    self.user_modes[key] = {'mode': row['mode'], 'robot': None}
                except Exception:
                    continue
        except Exception as e:
            print(f"[!] 加载 user_modes 失败: {e}")

    def _save_user_modes(self):
        """将 user_modes（只保存模式名）持久化到数据库"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.user_modes_db)
            
            # 保存默认模式
            db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ('default_mode', self.current_mode)
            )
            
            # 获取数据库中现有的所有记录
            existing = db.fetchall("SELECT group_id, user_id FROM user_modes")
            existing_keys = {(row['group_id'], row['user_id']) for row in existing}
            current_keys = set()
            
            # 批量插入或更新
            insert_data = []
            for (g, u), v in self.user_modes.items():
                if v and v.get('mode'):
                    current_keys.add((g, u))
                    insert_data.append((g, u, v.get('mode')))
            
            if insert_data:
                db.executemany(
                    "INSERT OR REPLACE INTO user_modes (group_id, user_id, mode) VALUES (?, ?, ?)",
                    insert_data
                )
            
            # 删除不再存在的记录
            keys_to_delete = existing_keys - current_keys
            for g, u in keys_to_delete:
                db.execute(
                    "DELETE FROM user_modes WHERE group_id = ? AND user_id = ?",
                    (g, u)
                )
        except Exception as e:
            print(f"[!] 保存 user_modes 失败: {e}")

    def _store_message(self, msg_type: str, data: dict, text: str):
        """存储消息到数据库"""
        if not self.message_store:
            return
        try:
            user_id = data.get("user_id", 0)
            group_id = data.get("group_id") if msg_type == "group" else 0
            nickname = data.get("sender", {}).get("nickname", "未知")
            raw = data.get("raw_message", "")
            msg_id = data.get("message_id", 0)
            timestamp = data.get("time", int(time.time()))
            
            self.message_store.add_message(
                msg_type=msg_type,
                user_id=user_id,
                group_id=group_id,
                nickname=nickname,
                content=text,
                raw_message=raw,
                msg_id=msg_id,
                timestamp=timestamp
            )
        except Exception as e:
            # 存储失败不应影响主流程
            print(f"[!] 消息存储失败: {e}")

    async def _handle_group_message(self, data: dict):
        group_id = data.get("group_id")
        user_id = data.get("user_id")
        message_id = data.get("message_id")
        sender = data.get("sender", {})
        
        text = self.extract_text(data.get("message", []))
        raw = data.get("raw_message", "")
        
        # 存储所有群消息
        self._store_message("group", data, text)
        
        # 检查是否@机器人
        is_at_me = self.self_id and f"[CQ:at,qq={self.self_id}]" in raw
        if not is_at_me:
            return
        
        # 只有在@机器人时才获取用户信息（带缓存）
        user_info = await self.get_user_info_with_cache(group_id, user_id, sender)
        nickname = user_info.get('nickname', '未知')
        card = user_info.get('card', '')
        sex = user_info.get('sex', 'unknown')
        
        clean_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', text).strip()
        if not clean_msg:
            await self.send_group_reply(group_id, user_id, message_id, "有什么可以帮你的吗？")
            return
        
        # 使用 Bot Agent 处理自然语言（包括以 / 开头的命令）
        if self.bot_agent:
            try:
                context = {
                    'group_id': group_id,
                    'user_id': user_id,
                    'message_id': message_id,
                    'nickname': nickname,
                    'card': card,  # 群名片
                    'sex': sex,    # 性别
                    'sender': sender,  # 完整sender信息
                    'is_group': True
                }
                handled, response = await self.bot_agent.process_message(clean_msg, context)
                if handled:
                    if response:  # 如果返回了消息内容
                        await self.send_group_reply(group_id, user_id, message_id, response)
                    return
                # 如果 Bot Agent 没有处理，继续走原有流程
            except Exception as e:
                print(f"[!] Bot Agent 处理失败: {e}")
        
        # 普通聊天消息 - 交给 chat 模式处理
        robot = self.get_user_robot(group_id, user_id)
        if robot and hasattr(robot, "handle_group"):
            # 传递完整的sender信息，包括群名片和性别
            await robot.handle_group(data, self.send_group_reply, sender_info={
                'nickname': nickname,
                'card': card,
                'sex': sex
            })
        else:
            await self.send_group_reply(group_id, user_id, message_id, "当前没有可用的机器人模式。")

    async def _handle_private_message(self, data: dict):
        user_id = data.get("user_id")
        nickname = data.get("sender", {}).get("nickname", "未知")
        text = self.extract_text(data.get("message", []))
        
        # 存储所有私聊消息
        self._store_message("private", data, text)
        
        if not text.strip():
            return
        
        # 使用 Bot Agent 处理自然语言（包括以 / 开头的命令）
        if self.bot_agent:
            try:
                context = {
                    'group_id': 0,
                    'user_id': user_id,
                    'message_id': 0,
                    'nickname': nickname,
                    'is_group': False
                }
                handled, response = await self.bot_agent.process_message(text.strip(), context)
                if handled:
                    if response:  # 如果返回了消息内容
                        await self.send_private_msg(user_id, response)
                    return
                # 如果 Bot Agent 没有处理，继续走原有流程
            except Exception as e:
                print(f"[!] Bot Agent 处理失败: {e}")
        
        # 普通聊天消息 - 交给 chat 模式处理
        robot = self.get_user_robot(0, user_id)
        if robot and hasattr(robot, "handle_private"):
            await robot.handle_private(data, self.send_private_msg)
        else:
            await self.send_private_msg(user_id, "当前没有可用的机器人模式。")

    async def handle_incoming(self, websocket):
        print(f"\n[*] NapCat 反向 WS 已连接: {websocket.remote_address}")
        self.recv_ws = websocket
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get("self_id") and not self.self_id:
                        self.self_id = data.get("self_id")
                        print(f"[*] 机器人 QQ: {self.self_id}")
                    post_type = data.get("post_type")
                    if post_type == "message":
                        msg_type = data.get("message_type")
                        if msg_type == "private":
                            # 使用 create_task 并发处理消息，避免阻塞主循环
                            asyncio.create_task(self._handle_private_message(data))
                        elif msg_type == "group":
                            # 使用 create_task 并发处理消息，避免阻塞主循环
                            asyncio.create_task(self._handle_group_message(data))
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"[!] 处理消息出错: {e}")
        except websockets.exceptions.ConnectionClosed:
            print("\n[*] NapCat 反向 WS 连接已关闭")
            self.recv_ws = None

    def _cleanup_messages(self):
        """定期清理过期消息"""
        if self.message_store:
            try:
                deleted = self.message_store.cleanup_old_messages()
                if deleted > 0:
                    print(f"[*] 清理了 {deleted} 条过期消息")
            except Exception as e:
                print(f"[!] 清理消息失败: {e}")

    async def _periodic_cleanup(self):
        """定期执行清理任务（每6小时）"""
        while True:
            await asyncio.sleep(6 * 3600)  # 6小时
            self._cleanup_messages()

    async def start(self):
        print("=" * 60)
        print("机器人集线器（支持自动模式选择）")
        print("=" * 60)
        print("\n[*] 连接 NapCat 正向 WS...")
        await self.connect_sender()
        print(f"[*] 启动反向 WS 服务器: ws://{self.config.listen_host}:{self.config.listen_port}/")
        
        # 启动定时清理任务
        if self.message_store:
            self._cleanup_timer = asyncio.create_task(self._periodic_cleanup())
        
        # 启动每日定时总结机器人
        if self._daily_summary_config['enabled']:
            try:
                from robots.daily_summary import DailySummaryRobot
                # 复制配置并注入到机器人配置中
                cfg = type(self.config)(**vars(self.config))
                setattr(cfg, 'daily_summary_enabled', self._daily_summary_config['enabled'])
                setattr(cfg, 'daily_summary_group_id', self._daily_summary_config['group_id'])
                setattr(cfg, 'daily_summary_max_tokens', self._daily_summary_config['max_tokens'])
                setattr(cfg, 'daily_summary_hour', self._daily_summary_config['hour'])
                setattr(cfg, 'daily_summary_minute', self._daily_summary_config['minute'])
                
                self.daily_summary_robot = DailySummaryRobot(cfg, mode_manager=self)
                self.daily_summary_robot.start()
            except Exception as e:
                print(f"[!] 启动每日定时总结失败: {e}")
        
        async with websockets.serve(self.handle_incoming, self.config.listen_host, self.config.listen_port):
            print("\n" + "=" * 60)
            print("机器人已启动")
            print(f"默认模式: {self.current_mode}")
            if self.message_store:
                db_size = self.message_store.get_db_size()
                print(f"消息存储: 已启用 (数据库大小: {db_size / 1024 / 1024:.2f} MB)")
            if self.bot_agent:
                print(f"自然语言: 已启用 (支持自然语言命令)")
            print("=" * 60 + "\n")
            try:
                await asyncio.Future()
            finally:
                # 取消定时任务
                if self._cleanup_timer:
                    self._cleanup_timer.cancel()
                    try:
                        await self._cleanup_timer
                    except asyncio.CancelledError:
                        pass
                # 停止每日定时总结机器人
                if self.daily_summary_robot:
                    try:
                        self.daily_summary_robot.stop()
                    except Exception:
                        pass
                # 程序退出时持久化 user_modes 并关闭共享 executor
                try:
                    self._save_user_modes()
                except Exception:
                    pass
                try:
                    if hasattr(self, 'shared_executor') and self.shared_executor is not None:
                        self.shared_executor.shutdown(wait=True)
                except Exception:
                    pass
                # 最后一次清理
                self._cleanup_messages()


def main():
    # 加载配置
    config_data = load_config("config.yaml")
    
    # 检查必要配置
    if not config_data.get('deepseek_api_key'):
        print("[!] 警告: DeepSeek API Key 未设置，请在 config.yaml 中配置")
    if not config_data.get('qq_bot_token'):
        print("[!] 警告: QQ Bot Token 未设置，请在 config.yaml 中配置")
    
    config = BotConfig(
        napcat_ws_url=config_data.get('napcat_ws_url'),
        listen_host=config_data.get('listen_host'),
        listen_port=int(config_data.get('listen_port')),
        token=config_data.get('qq_bot_token'),
        deepseek_api_key=config_data.get('deepseek_api_key'),
        system_prompt=config_data.get('system_prompt', ''),
        max_context=config_data.get('max_context'),
        max_input_tokens=config_data.get('max_input_tokens'),
        max_output_tokens=config_data.get('max_output_tokens'),
        max_prompt_tokens=config_data.get('max_prompt_tokens'),
        max_workers=config_data.get('max_workers'),
        daily_summary_enabled=config_data.get('daily_summary_enabled'),
        daily_summary_group_id=config_data.get('daily_summary_group_id'),
        daily_summary_max_tokens=config_data.get('daily_summary_max_tokens'),
        daily_summary_hour=config_data.get('daily_summary_hour'),
        daily_summary_minute=config_data.get('daily_summary_minute'),
        message_retention_days=config_data.get('message_retention_days'),
    )
    mgr = ModeManager(config)
    mgr.load_modes()
    
    # 设置默认模式为 chat
    if 'chat' in mgr.modes:
        mgr.current_mode = 'chat'
    elif mgr.modes:
        mgr.current_mode = next(iter(mgr.modes.keys()))
    
    try:
        asyncio.run(mgr.start())
    except KeyboardInterrupt:
        print("\n[*] 正在停止...")
        try:
            if mgr.current_robot and hasattr(mgr.current_robot,'executor'):
                mgr.current_robot.executor.shutdown(wait=True)
            # 关闭所有用户机器人中的 executor（如果存在）
            for v in mgr.user_modes.values():
                robot = v.get('robot') if isinstance(v, dict) else None
                if robot and hasattr(robot, 'executor'):
                    try:
                        if hasattr(robot, '_owns_executor') and robot._owns_executor:
                            robot.executor.shutdown(wait=True)
                    except Exception:
                        pass
            # 关闭共享 executor
            try:
                if hasattr(mgr, 'shared_executor') and mgr.shared_executor is not None:
                    mgr.shared_executor.shutdown(wait=True)
            except Exception:
                pass
        except Exception:
            pass
        print("[*] 已停止")


if __name__ == "__main__":
    main()
