#!/usr/bin/env python3
"""
Chat robot module for NapCat bot - contains chat functionality (moved from bot.py).
Each robot module should expose a create_robot(config) factory that returns an instance
with async methods handle_group(data, send_group_reply) and handle_private(data, send_private_msg).

=== METADATA ===
name: chat
desc: AI聊天模式，支持人设定制和上下文记忆
cmds: /help,/clean,/history,/setprompt,/getprompt,/reset
features: 直接设置人设(无需确认),查看当前人设
=== END ===
"""

import os
import json
import re
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import tiktoken
import requests

from deepseek_api import DeepSeekAPI

# 导入消息存储模块（已移到主目录）
try:
    from message_store import get_message_store
    MESSAGE_STORE_AVAILABLE = True
except Exception as e:
    print(f"[!] 消息存储模块加载失败: {e}")
    MESSAGE_STORE_AVAILABLE = False

# 导入好感度系统
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from affection_system import get_affection_manager
    AFFECTION_SYSTEM_AVAILABLE = True
except Exception as e:
    print(f"[!] 好感度系统加载失败: {e}")
    AFFECTION_SYSTEM_AVAILABLE = False

# 导入数据库工具
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db_utils import get_db_manager, json_dumps, json_loads
    DB_UTILS_AVAILABLE = True
except Exception as e:
    print(f"[!] 数据库工具加载失败: {e}")
    DB_UTILS_AVAILABLE = False

# 导入新闻服务
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from news_service import get_news_service, init_news_service_from_config, get_cached_news_service
    NEWS_SERVICE_AVAILABLE = True
except Exception as e:
    print(f"[!] 新闻服务加载失败: {e}")
    NEWS_SERVICE_AVAILABLE = False

# 全局变量：机器人QQ号（由 ModeManager 设置）
self_id = None


# ========== 配置参数 ==========
@dataclass
class BotConfig:
    """机器人配置"""
    # 连接配置
    napcat_ws_url: str = ""
    listen_host: str = ""
    listen_port: int = 0
    token: str = ""
    
    # AI 配置
    deepseek_api_key: str = ""
    system_prompt: str = ""
    
    # 限制参数（可调整）- 使用 token 数量限制
    max_context: int = 5           # 上下文最多保留多少条
    max_input_tokens: int = 100    # 输入最多多少 token
    max_output_tokens: int = 300   # 输出最多多少 token
    max_prompt_tokens: int = 500   # 人设最多多少 token
    
    # 线程池配置
    max_workers: int = 1           # 最大并发线程数
    # 可选共享线程池（由 ModeManager 提供以节省资源），若设置则机器人使用它而不是创建自己的 executor
    shared_executor: object = field(default=None, repr=False)
    
    # 每日定时总结配置
    daily_summary_enabled: bool = False
    daily_summary_group_id: int = 0
    daily_summary_max_tokens: int = 0
    daily_summary_hour: int = 0
    daily_summary_minute: int = 0
    
    # 消息存储配置
    message_retention_days: int = 7
    # 群聊时参考的最近消息数量
    group_context_messages: int = 10
    # 调试模式
    debug_mode: bool = False

# === AGENT-FRIENDLY-API START ===
# Convenience helpers for programmatic/agent use (start)

def create_chat_robot_instance(config=None):
    """Create and return a ChatRobot instance for programmatic use."""
    return ChatRobot(config)


def get_conversation_preview(group_id, user_id, max_messages=50, config=None):
    """Return up to max_messages most recent messages for (group_id,user_id).
    Each item is a dict as stored by ConversationManager.
    """
    robot = create_chat_robot_instance(config)
    ctx = robot.conversation.get_context(group_id, user_id)
    if not isinstance(ctx, list):
        return ctx
    return ctx[-max_messages:]

# Convenience helpers for programmatic/agent use (end)
# === AGENT-FRIENDLY-API END ===

# ========== 对话上下文管理 ==========


# ========== 对话上下文管理 ==========
class ConversationManager:
    """管理群聊中每个人的对话上下文（支持持久化）"""
    
    def __init__(self, max_context: int = 5, storage_file: str = None):
        # 结构: {(group_id, user_id): deque([...])}
        self.contexts: Dict[Tuple[int, int], deque] = {}
        self.max_context = max_context
        # 始终使用 db 文件
        self.db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'chat_history.db')
        self.db_path = os.path.abspath(self.db_path)
        self._lock = threading.Lock()
        
        # 自定义人设存储: {(group_id, user_id): custom_prompt}
        self.custom_prompts: Dict[Tuple[int, int], str] = {}
        
        # 初始化数据库
        self._init_db()
        # 加载历史记录
        self._load()
    
    def _init_db(self):
        """初始化数据库表"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.db_path)
            create_sql = """
                CREATE TABLE IF NOT EXISTS chat_contexts (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    messages TEXT,  -- JSON 格式存储消息列表
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

    def _load(self):
        """从数据库加载历史记录"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.db_path)
            max_storage = self.max_context * 10
            
            # 加载对话历史
            rows = db.fetchall("SELECT * FROM chat_contexts")
            for row in rows:
                try:
                    group_id = row['group_id']
                    user_id = row['user_id']
                    messages = json_loads(row['messages']) or []
                    # 只加载最新的记录
                    if len(messages) > max_storage:
                        messages = messages[-max_storage:]
                    self.contexts[(group_id, user_id)] = deque(messages, maxlen=self.max_context)
                except Exception as e:
                    print(f"[!] 加载聊天记录失败 ({row.get('group_id')},{row.get('user_id')}): {e}")
                    continue
            
            # 加载自定义人设
            prompt_rows = db.fetchall("SELECT * FROM custom_prompts")
            for row in prompt_rows:
                try:
                    group_id = row['group_id']
                    user_id = row['user_id']
                    self.custom_prompts[(group_id, user_id)] = row['prompt']
                except Exception as e:
                    continue
            
            print(f"[*] 已加载 {len(self.contexts)} 个用户的历史记录, {len(self.custom_prompts)} 个自定义人设")
        except Exception as e:
            print(f"[!] 加载历史记录失败: {e}")
    
    def _save(self):
        """保存历史记录到数据库（自动截断过长记录）"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.db_path)
            max_storage = self.max_context * 10  # 持久化最多保存10倍上下文长度
            
            # 保存对话历史
            for (group_id, user_id), messages in self.contexts.items():
                msg_list = list(messages)
                # 如果超过限制，只保留最新的记录
                if len(msg_list) > max_storage:
                    msg_list = msg_list[-max_storage:]
                    print(f"[*] 截断用户 {group_id},{user_id} 的历史记录: {len(messages)} -> {max_storage}")
                
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
    
    def get_context(self, group_id: int, user_id: int) -> List[dict]:
        """获取某人的对话上下文"""
        key = (group_id, user_id)
        with self._lock:
            if key not in self.contexts:
                self.contexts[key] = deque(maxlen=self.max_context)
            return list(self.contexts[key])
    
    def add_message(self, group_id: int, user_id: int, role: str, content: str, nickname: str = None):
        """添加一条消息到上下文并持久化"""
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
    
    def clear_context(self, group_id: int, user_id: int):
        """清空某人的上下文并更新持久化"""
        key = (group_id, user_id)
        with self._lock:
            if key in self.contexts:
                self.contexts[key].clear()
                self._save()
            # 从数据库中删除记录
            if DB_UTILS_AVAILABLE:
                try:
                    db = get_db_manager(self.db_path)
                    db.execute(
                        "DELETE FROM chat_contexts WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id)
                    )
                except Exception as e:
                    print(f"[!] 删除聊天记录失败: {e}")
    
    def set_custom_prompt(self, group_id: int, user_id: int, prompt: str):
        """设置自定义人设"""
        key = (group_id, user_id)
        with self._lock:
            self.custom_prompts[key] = prompt
            self._save()
    
    def get_custom_prompt(self, group_id: int, user_id: int) -> str:
        """获取自定义人设（如果没有返回None）"""
        key = (group_id, user_id)
        with self._lock:
            return self.custom_prompts.get(key)
    
    def clear_custom_prompt(self, group_id: int, user_id: int):
        """清除自定义人设"""
        key = (group_id, user_id)
        with self._lock:
            if key in self.custom_prompts:
                del self.custom_prompts[key]
                self._save()
            # 从数据库中删除记录
            if DB_UTILS_AVAILABLE:
                try:
                    db = get_db_manager(self.db_path)
                    db.execute(
                        "DELETE FROM custom_prompts WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id)
                    )
                except Exception as e:
                    print(f"[!] 删除自定义人设失败: {e}")


# ========== Chat 机器人 ==========
class ChatRobot:
    """聊天机器人逻辑（独立模块）"""
    def __init__(self, config: BotConfig = None):
        self.config = config or BotConfig()
        self.use_ai = bool(self.config.deepseek_api_key)
        self.ai = DeepSeekAPI(api_key=self.config.deepseek_api_key) if self.use_ai else None
        self.conversation = ConversationManager(max_context=self.config.max_context)
        # 使用共享 executor（若提供）以节省资源；否则创建私有 executor 保持兼容性
        self.pending_prompts: Dict[Tuple[int, int], str] = {}
        self._prompt_lock = threading.Lock()
        self.commands = {
            "/help": ("显示帮助菜单", self._cmd_help),
            "/ping": ("测试连通性", self._cmd_ping),
            "/clean": ("清除对话历史", self._cmd_clear),
            "/history": ("显示对话历史", self._cmd_history),
            "/setprompt": ("更改人设", self._cmd_setprompt),
            "/getprompt": ("查看当前人设", self._cmd_getprompt),
            "/reset": ("恢复默认人设", self._cmd_reset),
            "/affection": ("查看好感度", self._cmd_affection),
        }
        
        # 初始化好感度管理器
        self.affection_manager = None
        if AFFECTION_SYSTEM_AVAILABLE:
            try:
                self.affection_manager = get_affection_manager(
                    api_key=self.config.deepseek_api_key
                )
                # 解析默认人设并更新到好感度系统
                default_personality = self.affection_manager.parse_personality_from_text(
                    self.config.system_prompt
                )
                self.affection_manager.update_personality(default_personality)
                print("[*] 好感度系统已启用")
            except Exception as e:
                print(f"[!] 好感度系统初始化失败: {e}")
        
        # 初始化新闻服务（延迟初始化，在第一次对话时触发）
        self.news_service = None
        self.news_service_initialized = False
        # 延迟创建 executor：如果 config 提供 shared_executor 则使用它
        if getattr(self.config, 'shared_executor', None):
            self.executor = self.config.shared_executor
            self._owns_executor = False
        else:
            self.executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
            self._owns_executor = True
        print(f"[*] ChatRobot 配置: max_context={self.config.max_context}, max_input_tokens={self.config.max_input_tokens}, max_output_tokens={self.config.max_output_tokens}")
        print(f"[*] AI 状态: {'已启用' if self.use_ai else '未启用（模拟模式）'}")
        if getattr(self.config, 'debug_mode', False):
            print("[DEBUG] ChatRobot 调试模式已启用，将输出完整的 system prompt 和对话数据")

    # ---------- 工具函数 ----------
    def extract_text(self, message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return "".join(seg.get("data", {}).get("text", "") 
                          for seg in message if seg.get("type") == "text")
        return str(message)

    def count_tokens(self, text: str) -> int:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except:
            chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            other_chars = len(text) - chinese_chars
            return int(chinese_chars * 1.5 + other_chars * 0.3)

    # ---------- 命令实现 ----------
    async def _cmd_help(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        help_text = "【小音理的帮助菜单】\n"
        help_text += "-" * 15 + "\n"
        for cmd, (desc, _) in self.commands.items():
            help_text += f"{cmd} - {desc}\n"
        help_text += "-" * 15 + "\n"
        help_text += "也可以直接说:\n"
        help_text += "更改人设/清除历史/查看历史"
        await send_func(*send_args, help_text)

    async def _cmd_ping(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        await send_func(*send_args, "pong! 🏓")

    async def _cmd_clear(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        self.conversation.clear_context(group_id, user_id)
        # 重置好感度
        if self.affection_manager:
            self.affection_manager.reset_affection(group_id, user_id)
        await send_func(*send_args, "已清除对话历史，好感度已重置！")

    async def _cmd_history(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        context = self.conversation.get_context(group_id, user_id)
        if not context:
            await send_func(*send_args, "暂无对话历史")
            return
        history_text = f"【对话历史】共{len(context)}条\n"
        history_text += "-" * 20 + "\n"
        for i, msg in enumerate(context[-15:], 1):  # 只显示最近15条
            nickname = msg.get("nickname", "未知")[:8]  # 限制昵称长度
            content = msg["content"][:20]  # 限制内容长度
            if len(msg["content"]) > 20:
                content += "..."
            history_text += f"{i}.[{nickname}]{content}\n"
        await send_func(*send_args, history_text)

    async def _cmd_setprompt(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """触发人设设置流程（传统方式，保留兼容）"""
        key = (group_id, user_id)
        with self._prompt_lock:
            self.pending_prompts[key] = ""
        msg = "请直接发送新人设内容，我会直接生效（无需确认）。\n注意：更改人设将清空对话历史！"
        await send_func(*send_args, msg)

    async def _cmd_getprompt(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """查看当前人设"""
        custom_prompt = self.conversation.get_custom_prompt(group_id, user_id)
        if custom_prompt:
            # 显示自定义人设的前100字
            preview = custom_prompt[:100] + "..." if len(custom_prompt) > 100 else custom_prompt
            msg = f"【当前人设】(自定义)\n{preview}\n\n使用 /reset 恢复默认人设"
        else:
            # 显示默认人设
            default = self.config.system_prompt[:100] + "..." if len(self.config.system_prompt) > 100 else self.config.system_prompt
            msg = f"【当前人设】(默认)\n{default}\n\n使用 /setprompt 更改人设"
        await send_func(*send_args, msg)

    async def _cmd_reset(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        self.conversation.clear_custom_prompt(group_id, user_id)
        self.conversation.clear_context(group_id, user_id)
        # 重置好感度并恢复默认人设配置
        if self.affection_manager:
            self.affection_manager.reset_affection(group_id, user_id)
            # 从默认 system_prompt 解析默认人设
            default_personality = self.affection_manager.parse_personality_from_text(
                self.config.system_prompt
            )
            self.affection_manager.update_personality(default_personality)
        
        # 构建美观的重置成功提示
        reset_msg = (
            "🔄 【已恢复默认人设】🔄\n"
            "━━━━━━━━━━━━━━\n"
            "✅ 人设已恢复为默认值\n"
            "🗑️ 对话历史已清除\n"
            "💕 好感度已重置\n"
            "━━━━━━━━━━━━━━\n"
            "🌟 让我们重新开始吧~"
        )
        await send_func(*send_args, reset_msg)
    
    async def _cmd_affection(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """查看好感度"""
        if self.affection_manager:
            info = self.affection_manager.format_affection_info(group_id, user_id)
            hint = self.affection_manager.get_personality_hint()
            await send_func(*send_args, f"{info}\n\n{hint}")
        else:
            await send_func(*send_args, "好感度系统当前不可用")

    async def handle_command(self, text: str, group_id: int, user_id: int, 
                            send_func, *send_args) -> bool:
        if text in self.commands:
            _, handler = self.commands[text]
            await handler(group_id, user_id, send_func, *send_args)
            return True
        return False

    async def set_persona_directly(self, persona_text: str, group_id: int, user_id: int,
                                   send_func, *send_args) -> bool:
        """直接设置人设（供Agent调用，无需确认）"""
        try:
            self.conversation.clear_context(group_id, user_id)
            self.conversation.set_custom_prompt(group_id, user_id, persona_text)
            
            # 重置好感度并更新好感度系统的人设配置
            if self.affection_manager:
                self.affection_manager.reset_affection(group_id, user_id)
                # 解析新人设并更新好感度系统配置
                new_personality = self.affection_manager.parse_personality_from_text(persona_text)
                self.affection_manager.update_personality(new_personality)
            
            preview = persona_text[:50] + "..." if len(persona_text) > 50 else persona_text
            
            # 构建美观的人设更新提示
            msg = (
                "✨ 【人设已更新】✨\n"
                "━━━━━━━━━━━━━━\n"
                f"📝 {preview}\n"
                "━━━━━━━━━━━━━━\n"
                "🗑️ 对话历史已清除\n"
                "💕 好感度已重置"
            )
            
            await send_func(*send_args, msg)
            return True
        except Exception as e:
            print(f"[!] 设置人设失败: {e}")
            await send_func(*send_args, "设置人设失败，请重试")
            return False

    async def get_current_persona(self, group_id: int, user_id: int) -> str:
        """获取当前人设文本"""
        custom = self.conversation.get_custom_prompt(group_id, user_id)
        return custom if custom else self.config.system_prompt

    async def process_message(self, text: str, group_id: int, user_id: int,
                             send_func, nickname: str = None, card: str = None, sex: str = None, 
                             *send_args, timestamp: int = None, is_group: bool = False) -> None:
        # 保存参数供后续使用
        self._last_timestamp = timestamp
        self._last_is_group = is_group
        
        key = (group_id, user_id)
        with self._prompt_lock:
            if key in self.pending_prompts:
                # 简化流程：直接设置人设，无需确认
                self.pending_prompts.pop(key, None)
                await self.set_persona_directly(text, group_id, user_id, send_func, *send_args)
                return
        token_count = self.count_tokens(text)
        if token_count > self.config.max_input_tokens:
            await send_func(
                *send_args,
                f"消息太长了（约{token_count}个token），请控制在{self.config.max_input_tokens}个token以内~"
            )
            return
        if self.use_ai and self.ai:
            try:
                loop = asyncio.get_event_loop()
                # 如果 executor 被共享，run_in_executor 也能正常工作
                # 使用 functools.partial 传递额外的关键字参数
                import functools
                func = functools.partial(
                    self._call_ai_with_context,
                    timestamp=timestamp, is_group=is_group
                )
                result = await loop.run_in_executor(
                    self.executor,
                    func,
                    group_id, user_id, text, nickname, card, sex
                )
                
                # 解析返回结果
                if isinstance(result, tuple):
                    reply, affection_change_info = result
                else:
                    reply, affection_change_info = result, None
                
                # 组装最终回复：AI回复 + 好感度信息
                final_reply = reply
                if self.affection_manager:
                    # 获取当前好感度状态
                    current_value = self.affection_manager.get_affection_value(group_id, user_id)
                    level = self.affection_manager.get_affection_level(current_value)
                    
                    # 添加好感度信息（单独一行）
                    if affection_change_info:
                        change, reason, new_val = affection_change_info
                        change_symbol = "📈" if change > 0 else "📉"
                        affection_line = f"\n\n────────────\n💕 好感度 {level}（{new_val}/100）{change_symbol}{change:+d}"
                    else:
                        affection_line = f"\n\n────────────\n💕 好感度 {level}（{current_value}/100）"
                    
                    final_reply = reply + affection_line
                
                await send_func(*send_args, final_reply)
                
                # 存储机器人自己的消息，标记对话目标
                if MESSAGE_STORE_AVAILABLE:
                    try:
                        from message_store import get_message_store
                        store = get_message_store()
                        import time
                        store.add_message(
                            msg_type='group' if is_group else 'private',
                            user_id=self_id if 'self_id' in globals() and self_id else 0,
                            group_id=group_id if is_group else 0,
                            nickname='音理',
                            content=reply,
                            raw_message=reply,
                            msg_id=int(time.time() * 1000),
                            timestamp=int(time.time()),
                            reply_to=None,
                            target_user_id=user_id  # 标记这是在和谁对话
                        )
                        print(f"[DEBUG] 机器人回复已存储: target_user_id={user_id}")
                    except Exception as e:
                        print(f"[!] 存储机器人消息失败: {e}")
            except Exception as e:
                print(f"[!] 处理失败: {e}")
                import traceback
                traceback.print_exc()
                await send_func(*send_args, "抱歉，处理出错了。")
        else:
            reply = f"收到: {text}\n(模拟模式)"
            await send_func(*send_args, reply)

    def _call_ai_with_context(self, group_id: int, user_id: int, user_msg: str, 
                               nickname: str = None, card: str = None, sex: str = None,
                               timestamp: int = None, is_group: bool = False) -> str:
        try:
            context = self.conversation.get_context(group_id, user_id)
            custom_prompt = self.conversation.get_custom_prompt(group_id, user_id)
            system_prompt = custom_prompt if custom_prompt else self.config.system_prompt
            
            # 获取当前好感度值（用于后续评估变化）
            current_affection = 0
            if self.affection_manager:
                current_affection = self.affection_manager.get_affection_value(group_id, user_id)
            
            messages = [{"role": "system", "content": system_prompt}]
            
            # DEBUG: 输出完整的 system prompt
            if getattr(self.config, 'debug_mode', False):
                print("\n" + "=" * 60)
                print("[DEBUG] ===== SYSTEM PROMPT (完整) =====")
                print("=" * 60)
                print(system_prompt)
                print("=" * 60)
                print("[DEBUG] ===== END SYSTEM PROMPT =====")
                print("=" * 60 + "\n")

            # ===== 第一点五个 System Prompt：固定的聊天要求 ====
            message_prompt = """称呼规则：
- 请根据系统提供的"当前对话者信息"中的名字和性别来决定如何称呼对方

聊天：
- 你可视聊天氛围，主动并自然地和对方聊及今天的新闻内容
- 请你依据你对对方的好感度变更语气
- 当前内容与你之前的聊天内容保持非重复性
- 你可以获取到群聊相关记录，其中“与<昵称>对话的分身”代表着是你的分身和<昵称>的聊天记录

输出格式：
- 你在QQ中对话，因此不要使用MD格式，而是使用适合QQ聊天的格式
- 请避免长篇大论，控制字数在100字以内
"""
            messages.append({"role": "system", "content": str(message_prompt)})
            
            # ===== 群聊上下文 =====
            print(f"[DEBUG] 群聊上下文检查: is_group={is_group}, timestamp={timestamp}, MESSAGE_STORE_AVAILABLE={MESSAGE_STORE_AVAILABLE}")
            if is_group and timestamp and MESSAGE_STORE_AVAILABLE:
                print(f"[DEBUG] 群聊上下文条件满足，开始加载...")
                try:
                    from message_store import get_message_store
                    store = get_message_store()
                    
                    # 获取全局 self_id
                    global self_id
                    if 'self_id' not in globals() or self_id is None:
                        self_id = 0
                    
                    # 获取最近群消息数量
                    context_limit = getattr(self.config, 'group_context_messages', 10)
                    print(f"[DEBUG] 正在获取群聊上下文: group_id={group_id}, limit={context_limit}, before_time={timestamp}")
                    recent_messages = store.get_recent_group_messages(
                        group_id=group_id,
                        limit=context_limit,
                        before_time=timestamp
                    )
                    
                    print(f"[DEBUG] 获取到 {len(recent_messages) if recent_messages else 0} 条群聊消息")
                    
                    if recent_messages:
                        # 格式化群聊历史
                        group_context_lines = []
                        group_context_lines.append("【群聊上下文（最近{}条）】".format(len(recent_messages)))
                        
                        # 缓存用户昵称，用于查询target_user_id对应的昵称
                        user_nickname_cache = {}
                        
                        # 先遍历一遍，收集所有用户的昵称
                        for msg in recent_messages:
                            if msg.user_id != self_id and msg.nickname:
                                user_nickname_cache[msg.user_id] = msg.nickname
                        
                        # 按时间正序处理
                        for msg in reversed(recent_messages):
                            # 确定发送者名称
                            if msg.user_id == self_id:
                                # 机器人自己的消息，检查是否有对话目标
                                if msg.target_user_id:
                                    # 从缓存中获取目标用户的昵称
                                    target_nickname = user_nickname_cache.get(
                                        msg.target_user_id, 
                                        f"用户{msg.target_user_id}"
                                    )
                                    sender_name = f"与{target_nickname}对话的分身"
                                else:
                                    sender_name = "音理"
                            else:
                                sender_name = msg.nickname if msg.nickname else f"用户{msg.user_id}"
                            
                            content = msg.content
                            
                            # 处理引用消息
                            if msg.reply_to:
                                replied_msg = store.get_message_by_id(msg.reply_to)
                                if replied_msg:
                                    # 确定被引用消息的发送者名称
                                    if replied_msg.user_id == self_id:
                                        if replied_msg.target_user_id:
                                            replied_name = user_nickname_cache.get(
                                                replied_msg.target_user_id,
                                                f"用户{replied_msg.target_user_id}"
                                            )
                                            replied_name = f"与{replied_name}对话的分身"
                                        else:
                                            replied_name = "音理"
                                    else:
                                        replied_name = replied_msg.nickname if replied_msg.nickname else f"用户{replied_msg.user_id}"
                                    content = f"[引用{replied_name}:{replied_msg.content[:30]}...]{content}"
                            
                            group_context_lines.append(f"{sender_name}: {content}")
                        
                        group_context_lines.append("【以上是群聊历史，供你参考当前聊天氛围】")
                        group_context_text = "\n".join(group_context_lines)
                        messages.append({"role": "system", "content": group_context_text})
                        
                        # ===== 验证日志 =====
                        if getattr(self.config, 'debug_mode', False):
                            print(f"\n[群聊上下文] 已成功加载 {len(recent_messages)} 条消息:")
                            for line in group_context_lines:
                                print(f"  {line}")
                            print()
                        # ====================
                        
                        if getattr(self.config, 'debug_mode', False):
                            print(f"[DEBUG] 已添加群聊上下文: {len(recent_messages)}条消息")
                    else:
                        print(f"[DEBUG] 没有获取到群聊历史消息")
                except Exception as e:
                    print(f"[!] 添加群聊上下文失败: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                if not is_group:
                    print(f"[DEBUG] 跳过群聊上下文: 不是群聊")
                elif not timestamp:
                    print(f"[DEBUG] 跳过群聊上下文: 没有时间戳")
                elif not MESSAGE_STORE_AVAILABLE:
                    print(f"[DEBUG] 跳过群聊上下文: 消息存储不可用")
            
            # ===== 第二个 System Prompt：新闻资讯 =====
            # 延迟初始化新闻服务（仅在有对话时触发）
            if NEWS_SERVICE_AVAILABLE and not self.news_service_initialized:
                try:
                    from config_loader import load_config
                    config = load_config()
                    if config.get("news_enabled", True):
                        self.news_service = init_news_service_from_config(config)
                        if self.news_service:
                            print("[*] 新闻服务已初始化")
                except Exception as e:
                    print(f"[!] 新闻服务初始化失败: {e}")
                self.news_service_initialized = True
            
            # 获取新闻并添加到 system prompt（作为第二个 prompt）
            if self.news_service:
                try:
                    news_content = self.news_service.get_news_for_prompt()
                    if news_content:
                        messages.append({"role": "system", "content": news_content})
                        print(f"[*] 已添加新闻到 system prompt")
                except Exception as e:
                    print(f"[!] 获取新闻失败: {e}")
            # ========================================
            
            # 第三个 System Prompt：构建对话者信息
            user_info_parts = []
            # 优先使用群名片(card)，如果没有则使用昵称(nickname)
            display_name = card if card else nickname
            if display_name:
                user_info_parts.append(f"名字：{display_name}")
            # 添加性别信息
            if sex and sex != "unknown":
                sex_str = "男" if sex == "male" else "女" if sex == "female" else "未知"
                user_info_parts.append(f"性别：{sex_str}")
            if user_info_parts:
                user_info = "，".join(user_info_parts)
                messages.append({"role": "system", "content": f"当前对话者信息：{user_info}。请根据这些信息用合适的称呼回应对方。"})
            
            # 添加当前时间信息（年月日时分秒）
            from datetime import datetime
            current_time = datetime.now().strftime("%Y年%m月%d日 %H时%M分%S秒")
            messages.append({"role": "system", "content": f"当前时间：{current_time}"})
            
            for msg in context:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": user_msg})
            
            # DEBUG: 输出完整的对话 messages
            if getattr(self.config, 'debug_mode', False):
                print("\n" + "=" * 60)
                print("[DEBUG] ===== FULL MESSAGES (完整对话) =====")
                print("=" * 60)
                import json
                for i, msg in enumerate(messages):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    print(f"\n[Message {i}] Role: {role}")
                    print("-" * 40)
                    print(content)
                    print("-" * 40)
                print("\n" + "=" * 60)
                print(f"[DEBUG] ===== END MESSAGES (共 {len(messages)} 条) =====")
                print("=" * 60 + "\n")
            
            prompt_type = "自定义" if custom_prompt else "默认"
            display_name_str = card if card else (nickname or '未知')
            sex_str = sex if sex else '未知'
            print(f"[AI线程] 调用 DeepSeek... 上下文: {len(context)}条, 名字: {display_name_str}, 性别: {sex_str}, 人设: {prompt_type}")
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.deepseek_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "max_tokens": self.config.max_output_tokens
                },
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            reply = result["choices"][0]["message"]["content"]
            self.conversation.add_message(group_id, user_id, "user", user_msg, nickname)
            self.conversation.add_message(group_id, user_id, "assistant", reply, "音理")
            
            # 评估并更新好感度（传入当前人设文本）
            affection_change_info = None
            if self.affection_manager:
                try:
                    # 传入人设文本，让AI根据人设智能评估
                    change, reason = self.affection_manager.evaluate_affection_change(
                        user_msg, reply, current_affection, persona_text=system_prompt
                    )
                    if change != 0:
                        new_val, actual_change, _ = self.affection_manager.update_affection(
                            group_id, user_id, change, reason, user_msg, reply
                        )
                        if actual_change != 0:
                            print(f"[AI线程] 好感度变化: {current_affection} -> {new_val} ({actual_change:+d}, {reason})")
                            affection_change_info = (actual_change, reason, new_val)
                except Exception as e:
                    print(f"[!] 好感度更新失败: {e}")
            
            print(f"[AI线程] 回复: {reply[:50]}...")
            
            # 返回回复内容和好感度变化信息（用于后续附加显示）
            if affection_change_info:
                return reply, affection_change_info
            return reply, None
        except Exception as e:
            print(f"[!] AI 调用失败: {e}")
            return "抱歉，我暂时无法回答。"

    def _get_group_context(self, group_id: int, current_user_id: int, 
                           current_timestamp: int, limit: int = 10) -> List[Dict]:
        """
        获取群聊上下文（最近n条消息）
        
        Args:
            group_id: 群号
            current_user_id: 当前正在对话的用户ID（用于识别"自己"的消息）
            current_timestamp: 当前消息时间戳（获取此时间之前的消息）
            limit: 获取消息数量
        
        Returns:
            List[Dict]: 格式化的消息列表
        """
        if not MESSAGE_STORE_AVAILABLE:
            return []
        
        try:
            from message_store import get_message_store
            store = get_message_store()
            
            # 获取最近n条消息（在当前消息之前）
            messages = store.get_recent_group_messages(
                group_id=group_id,
                limit=limit,
                before_time=current_timestamp
            )
            
            if not messages:
                return []
            
            # 格式化消息，并处理引用
            formatted = []
            for msg in messages:
                # 确定发送者身份
                if msg.user_id == self_id:
                    # 这是机器人自己发的消息
                    # 由于可能存在多个人同时聊天，将自己标记为"音理"
                    sender_name = "音理"
                else:
                    sender_name = msg.nickname if msg.nickname else f"用户{msg.user_id}"
                
                content = msg.content
                
                # 如果有引用消息，尝试获取引用内容
                if msg.reply_to:
                    replied_msg = store.get_message_by_id(msg.reply_to)
                    if replied_msg:
                        replied_name = replied_msg.nickname if replied_msg.nickname else f"用户{replied_msg.user_id}"
                        if replied_msg.user_id == self_id:
                            replied_name = "音理"
                        content = f"[引用{replied_name}的话]{content}"
                
                formatted.append({
                    'sender': sender_name,
                    'user_id': msg.user_id,
                    'content': content,
                    'timestamp': msg.timestamp,
                    'is_self': msg.user_id == self_id
                })
            
            # 反转列表，按时间正序排列（最早的在前）
            formatted.reverse()
            return formatted
            
        except Exception as e:
            print(f"[!] 获取群聊上下文失败: {e}")
            return []
    
    async def handle_group(self, data: dict, send_group_reply, sender_info: dict = None):
        # 确保如果机器人有私有 executor，它会在模块卸载或程序退出时被关闭由创建方负责
        group_id = data.get("group_id")
        user_id = data.get("user_id")
        message_id = data.get("message_id")
        sender = data.get("sender", {})
        nickname = sender.get("nickname", "未知")
        text = self.extract_text(data.get("message", []))
        raw = data.get("raw_message", "")
        timestamp = data.get("time", int(time.time()))
        is_at_me = False  # manager already checked @
        if not text:
            return
        clean_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', text).strip()
        if not clean_msg:
            await send_group_reply(group_id, user_id, message_id, "有什么可以帮你的吗？")
            return
        # 获取sender_info中的信息
        card = None
        sex = None
        if sender_info:
            card = sender_info.get('card')
            sex = sender_info.get('sex')
        await self.process_message(clean_msg, group_id, user_id,
                                   send_group_reply, nickname, card, sex, group_id, user_id, message_id,
                                   timestamp=timestamp, is_group=True)

    async def handle_private(self, data: dict, send_private_msg, sender_info: dict = None):
        user_id = data.get("user_id")
        sender = data.get("sender", {})
        nickname = sender.get("nickname", "未知")
        sex = sender.get("sex", "unknown")  # 默认从sender获取
        
        # 如果传入了sender_info，使用其中的信息（可能包含从API获取的更准确的性别）
        if sender_info:
            sex = sender_info.get('sex', sex)
            nickname = sender_info.get('nickname', nickname)
        
        text = self.extract_text(data.get("message", []))
        if not text.strip():
            return
        # 私聊没有card（群名片），传入None
        await self.process_message(text, 0, user_id,
                                   send_private_msg, nickname, None, sex, user_id)


# 工厂函数
def create_robot(config: BotConfig = None):
    return ChatRobot(config)

# 清理函数（可选）：如果机器人拥有私有 executor，外部可以调用此函数来释放资源
def cleanup_robot(robot: ChatRobot):
    try:
        if hasattr(robot, '_owns_executor') and robot._owns_executor and hasattr(robot, 'executor'):
            robot.executor.shutdown(wait=True)
    except Exception:
        pass

if __name__ == "__main__":
    # 仅用于模块级调试
    cfg = BotConfig()
    r = create_robot(cfg)
    print("Chat robot ready")
