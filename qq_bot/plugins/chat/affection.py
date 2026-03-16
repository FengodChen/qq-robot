"""好感度系统模块。

管理用户与机器人的好感度关系，影响聊天语气。
支持基于 LLM 的好感度评估和人设喜好/雷点生成。
"""

import json
import hashlib
import time
import threading
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict

from qq_bot.services.storage.db import get_db_manager, json_dumps, json_loads


@dataclass
class AffectionRecord:
    """好感度变化记录。
    
    Attributes:
        timestamp: 记录时间戳。
        change: 变化值（可为负）。
        reason: 变化原因。
        user_message: 用户消息（精简）。
        bot_reply: 机器人回复（精简）。
        old_value: 变化前的好感度。
        new_value: 变化后的好感度。
    """
    timestamp: int
    change: int
    reason: str
    user_message: str
    bot_reply: str
    old_value: int
    new_value: int
    
    def to_dict(self) -> Dict:
        """转换为字典。"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AffectionRecord":
        """从字典创建实例。"""
        return cls(**data)


@dataclass
class MaxAffectionReward:
    """满好感度奖励记录。"""
    first_reached_at: int
    last_reward_at: int
    reward_count: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "MaxAffectionReward":
        return cls(**data)


@dataclass
class UserAffection:
    """用户好感度数据。
    
    Attributes:
        user_id: 用户 QQ 号。
        group_id: 群组 ID。
        value: 当前好感度值（-100~100）。
        records: 好感度变化记录列表。
        last_interaction: 最后交互时间戳。
        max_affection_data: 满好感度奖励记录。
    """
    user_id: int
    group_id: int
    value: int = 0
    records: List[Dict] = None
    last_interaction: int = 0
    max_affection_data: Optional[MaxAffectionReward] = None  # 新增
    
    def __post_init__(self):
        """初始化默认值并确保值在有效范围内。"""
        if self.records is None:
            self.records = []
        self.value = max(-100, min(100, self.value))


@dataclass
class PersonaPreferences:
    """人设喜好/雷点数据。
    
    Attributes:
        persona_hash: 人设文本的 MD5 哈希值，作为唯一标识。
        interests: 兴趣爱好列表。
        favorite_things: 特别喜欢的事物。
        dislikes: 讨厌的事物/雷点。
        personality_summary: 人设性格摘要。
        generated_at: 生成时间戳。
    """
    persona_hash: str
    interests: List[str]
    favorite_things: List[str]
    dislikes: List[str]
    personality_summary: str = ""
    generated_at: int = 0
    
    def __post_init__(self):
        """初始化默认值。"""
        if self.generated_at == 0:
            self.generated_at = int(time.time())
    
    def to_dict(self) -> Dict:
        """转换为字典。"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "PersonaPreferences":
        """从字典创建实例。"""
        return cls(**data)


@dataclass
class PersonaAffectionConfig:
    """人设好感度配置。"""
    persona_hash: str
    level_names: Dict[Tuple[int, int], str]  # 等级名称映射
    level_descriptions: Dict[str, str]  # 等级描述映射
    tone_descriptions: Dict[str, str]  # 语气描述映射
    generated_at: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "persona_hash": self.persona_hash,
            "level_names": {f"{k[0]}_{k[1]}": v for k, v in self.level_names.items()},
            "level_descriptions": self.level_descriptions,
            "tone_descriptions": self.tone_descriptions,
            "generated_at": self.generated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "PersonaAffectionConfig":
        level_names = {}
        for k, v in data.get("level_names", {}).items():
            parts = k.split("_")
            level_names[(int(parts[0]), int(parts[1]))] = v
        return cls(
            persona_hash=data["persona_hash"],
            level_names=level_names,
            level_descriptions=data.get("level_descriptions", {}),
            tone_descriptions=data.get("tone_descriptions", {}),
            generated_at=data.get("generated_at", 0)
        )


class AffectionManager:
    """好感度管理器。
    
    管理每个用户独立的好感度值，支持基于规则的评估。
    
    Attributes:
        LEVELS: 好感度等级定义。
        MAX_RECORDS: 最大记录数。

    Example:
        >>> manager = AffectionManager()
        >>> value = manager.get_affection_value(123456, 789012)
        >>> new_val, change, _ = manager.update_affection(123456, 789012, 2, "友好交流")
    """
    
    # 好感度等级定义 (-100~100)
    LEVELS = {
        (-100, -99): "死敌",
        (-99, -70): "憎恨",
        (-70, -40): "厌恶",
        (-40, -20): "反感",
        (-20, 0): "疏离",
        (0, 15): "陌生",
        (15, 35): "<关系初建>",
        (35, 55): "熟悉",
        (55, 75): "友好",
        (75, 90): "亲密",
        (90, 100): "至交",
        (100, 101): "灵魂伴侣"
    }
    
    # 最大记录数
    MAX_RECORDS = 50
    
    def __init__(self, db_path: Optional[Path] = None, llm_service: Any = None, 
                 prompts: Any = None, tone_descriptions: Optional[Dict[str, str]] = None):
        """初始化好感度管理器。
        
        Args:
            db_path: 数据库文件路径，默认为 data/affection_data.db。
            llm_service: LLM 服务实例，用于生成人设喜好/雷点和评估好感度。
            prompts: 好感度提示词配置。
            tone_descriptions: 语气描述配置（来自 chat prompts）。
        """
        if db_path is None:
            db_path = Path("data") / "affection_data.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._data: Dict[Tuple[int, int], UserAffection] = {}
        self._persona_preferences: Dict[str, PersonaPreferences] = {}
        self._lock = threading.RLock()
        self._llm = llm_service
        self._prompts = prompts
        self._tone_descriptions = tone_descriptions or {}
        
        self._persona_affection_configs: Dict[str, PersonaAffectionConfig] = {}
        
        # 初始化默认配置（向后兼容）
        self._default_config = self._create_default_config()
        
        # 初始化数据库
        self._init_db()
        self._load()
        
        print(f"[*] AffectionManager 初始化完成，共 {len(self._data)} 个用户数据")
    
    def _create_default_config(self) -> PersonaAffectionConfig:
        """创建默认好感度配置（仅作为结构模板，不应直接使用）。"""
        return PersonaAffectionConfig(
            persona_hash="default",
            level_names={
                (-100, -99): "<关系极差>",
                (-99, -70): "<关系很差>",
                (-70, -40): "<关系较差>",
                (-40, -20): "<关系不佳>",
                (-20, 0): "<关系冷淡>",
                (0, 15): "<关系初建>",
                (15, 35): "<关系浅显>",
                (35, 55): "<关系尚可>",
                (55, 75): "<关系良好>",
                (75, 90): "<关系亲密>",
                (90, 100): "<关系极好>",
                (100, 101): "<关系圆满>"
            },
            level_descriptions={
                "<关系极差>": "关系处于最低谷，极度恶劣",
                "<关系很差>": "关系非常糟糕，充满负面情感",
                "<关系较差>": "关系明显不佳，存在较大隔阂",
                "<关系不佳>": "关系不太好，有一定负面印象",
                "<关系冷淡>": "关系较为冷淡，保持距离",
                "<关系初建>": "关系刚刚建立，尚未深入",
                "<关系浅显>": "关系尚浅，正在逐步了解",
                "<关系尚可>": "关系还可以，有一定交流基础",
                "<关系良好>": "关系不错，相处较为融洽",
                "<关系亲密>": "关系很亲密，彼此较为信任",
                "<关系极好>": "关系非常好，彼此高度认可",
                "<关系圆满>": "关系达到圆满状态"
            },
            tone_descriptions={
                "<关系极差>": "你对用户怀有极致的负面情感，语气冰冷刺骨，充满敌意。",
                "<关系很差>": "你对用户怀有强烈的负面情绪，语气极其冷漠、尖锐。",
                "<关系较差>": "你对用户有明显的反感，态度冷淡疏离，不愿多交流。",
                "<关系不佳>": "你对用户有负面印象，态度冷淡客气但保持距离。",
                "<关系冷淡>": "你对用户保持明显的距离感，态度冷淡客气但疏离。",
                "<关系初建>": "你对用户保持礼貌但疏远的态度，回答正式而简短。",
                "<关系浅显>": "你对用户保持基本的礼貌友好，但仍然有明显的距离感。",
                "<关系尚可>": "你对用户比较放松，会偶尔主动关心，语气较为亲切自然。",
                "<关系良好>": "你对用户很友善，会使用轻松活泼的语气，经常会开玩笑。",
                "<关系亲密>": "你对用户非常亲近，语气温柔宠溺，充满关心和依赖。",
                "<关系极好>": "你对用户毫无保留，语气极其亲密宠溺甚至带点任性。",
                "<关系圆满>": "你对用户的情感已经超越了普通关系，达到了完美的境界。"
            }
        )
    
    def set_llm_service(self, llm_service: Any) -> None:
        """设置 LLM 服务。
        
        Args:
            llm_service: LLM 服务实例。
        """
        self._llm = llm_service
    
    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        try:
            db = get_db_manager(self.db_path)
            
            # 用户好感度表 - 添加 max_affection_data 字段
            create_affection_sql = """
                CREATE TABLE IF NOT EXISTS affection_data (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    value INTEGER DEFAULT 0,
                    records TEXT,
                    last_interaction INTEGER DEFAULT 0,
                    max_affection_data TEXT,
                    PRIMARY KEY (group_id, user_id)
                );
            """
            db.init_tables(create_affection_sql)
            
            # 人设喜好/雷点表
            create_persona_sql = """
                CREATE TABLE IF NOT EXISTS persona_preferences (
                    persona_hash TEXT PRIMARY KEY,
                    interests TEXT,
                    favorite_things TEXT,
                    dislikes TEXT,
                    personality_summary TEXT,
                    generated_at INTEGER DEFAULT 0
                );
            """
            db.init_tables(create_persona_sql)
            
            # 新增：人设好感度配置表
            create_config_sql = """
                CREATE TABLE IF NOT EXISTS persona_affection_configs (
                    persona_hash TEXT PRIMARY KEY,
                    level_names TEXT,
                    level_descriptions TEXT,
                    tone_descriptions TEXT,
                    generated_at INTEGER DEFAULT 0
                );
            """
            db.init_tables(create_config_sql)
            
            # 兼容：检查并添加 max_affection_data 列
            self._migrate_add_max_affection_column()
            
            # 兼容：修复 persona_affection_configs 表结构
            self._migrate_fix_persona_affection_configs_table()
            
        except Exception as e:
            print(f"[!] 初始化好感度数据库失败: {e}")
    
    def _migrate_add_max_affection_column(self) -> None:
        """兼容：添加 max_affection_data 列到现有表。"""
        try:
            db = get_db_manager(self.db_path)
            # 检查列是否存在
            columns = db.get_table_info("affection_data")
            column_names = [col["name"] for col in columns]
            if "max_affection_data" not in column_names:
                db.execute("ALTER TABLE affection_data ADD COLUMN max_affection_data TEXT")
                print("[*] 数据库迁移：添加 max_affection_data 列")
        except Exception as e:
            print(f"[!] 数据库迁移失败: {e}")
    
    def _migrate_fix_persona_affection_configs_table(self) -> None:
        """迁移：修复 persona_affection_configs 表结构。
        
        旧表有 group_id/user_id 列，新表应该只有 persona_hash 作为唯一键。
        """
        try:
            db = get_db_manager(self.db_path)
            columns = db.get_table_info("persona_affection_configs")
            column_names = [col["name"] for col in columns]
            
            # 检查是否需要迁移（有 group_id 列表示旧表结构）
            if "group_id" not in column_names:
                return  # 已经是新表结构，不需要迁移
            
            print("[*] 数据库迁移：修复 persona_affection_configs 表结构...")
            
            # 1. 重命名旧表
            db.execute("ALTER TABLE persona_affection_configs RENAME TO persona_affection_configs_old")
            
            # 2. 创建新表（正确结构）
            create_new_sql = """
                CREATE TABLE persona_affection_configs (
                    persona_hash TEXT PRIMARY KEY,
                    level_names TEXT,
                    level_descriptions TEXT,
                    tone_descriptions TEXT,
                    generated_at INTEGER DEFAULT 0
                );
            """
            db.init_tables(create_new_sql)
            
            # 3. 迁移数据（按 persona_hash 分组，取第一个）
            rows = db.fetchall("""
                SELECT DISTINCT persona_hash, level_names, level_descriptions, 
                               tone_descriptions, generated_at
                FROM persona_affection_configs_old
                WHERE persona_hash IS NOT NULL
            """)
            
            for row in rows:
                db.execute(
                    """INSERT OR REPLACE INTO persona_affection_configs 
                       (persona_hash, level_names, level_descriptions, tone_descriptions, generated_at) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        row["persona_hash"],
                        row["level_names"],
                        row["level_descriptions"],
                        row["tone_descriptions"],
                        row["generated_at"] or int(time.time())
                    )
                )
            
            # 4. 删除旧表
            db.execute("DROP TABLE persona_affection_configs_old")
            
            print(f"[*] 数据库迁移完成：已迁移 {len(rows)} 条人设好感度配置")
            
        except Exception as e:
            print(f"[!] 数据库迁移失败: {e}")
    
    def _load(self) -> None:
        """从数据库加载数据。"""
        try:
            db = get_db_manager(self.db_path)
            
            # 加载用户好感度数据
            rows = db.fetchall("SELECT * FROM affection_data")
            for row in rows:
                try:
                    group_id = row["group_id"]
                    user_id = row["user_id"]
                    records = json_loads(row["records"]) or []
                    
                    # 解析 max_affection_data
                    max_affection_data = None
                    if row.get("max_affection_data"):
                        try:
                            data = json_loads(row["max_affection_data"])
                            max_affection_data = MaxAffectionReward.from_dict(data)
                        except:
                            pass
                    
                    affection = UserAffection(
                        user_id=user_id,
                        group_id=group_id,
                        value=row["value"],
                        records=records,
                        last_interaction=row["last_interaction"],
                        max_affection_data=max_affection_data
                    )
                    self._data[(group_id, user_id)] = affection
                    
                    # 兼容：如果用户满好感度但没有奖励记录，自动初始化
                    if affection.value >= 100 and affection.max_affection_data is None:
                        affection.max_affection_data = MaxAffectionReward(
                            first_reached_at=int(time.time()),
                            last_reward_at=int(time.time()),
                            reward_count=0
                        )
                        print(f"[*] 兼容处理：用户 ({group_id},{user_id}) 已达成满好感度，初始化奖励记录")
                except Exception as e:
                    print(f"[!] 加载好感度数据失败 ({row.get('group_id')},{row.get('user_id')}): {e}")
                    continue
            
            # 加载人设喜好/雷点数据
            persona_rows = db.fetchall("SELECT * FROM persona_preferences")
            for row in persona_rows:
                try:
                    preferences = PersonaPreferences(
                        persona_hash=row["persona_hash"],
                        interests=json_loads(row["interests"]) or [],
                        favorite_things=json_loads(row["favorite_things"]) or [],
                        dislikes=json_loads(row["dislikes"]) or [],
                        personality_summary=row["personality_summary"],
                        generated_at=row["generated_at"]
                    )
                    self._persona_preferences[row["persona_hash"]] = preferences
                except Exception as e:
                    print(f"[!] 加载人设喜好数据失败 ({row.get('persona_hash')}): {e}")
                    continue
            
            # 加载人设好感度配置
            config_rows = db.fetchall("SELECT * FROM persona_affection_configs")
            for row in config_rows:
                try:
                    config = PersonaAffectionConfig.from_dict({
                        "persona_hash": row["persona_hash"],
                        "level_names": json_loads(row["level_names"]) or {},
                        "level_descriptions": json_loads(row["level_descriptions"]) or {},
                        "tone_descriptions": json_loads(row["tone_descriptions"]) or {},
                        "generated_at": row["generated_at"]
                    })
                    self._persona_affection_configs[row["persona_hash"]] = config
                except Exception as e:
                    print(f"[!] 加载人设好感度配置失败 ({row.get('persona_hash')}): {e}")
                    continue
            
            print(f"[*] 已加载 {len(self._data)} 个用户的好感度数据，"
                  f"{len(self._persona_preferences)} 个人设喜好配置，"
                  f"{len(self._persona_affection_configs)} 个人设好感度配置")
                    
        except Exception as e:
            print(f"[!] 加载数据失败: {e}")
    
    def _save(self) -> None:
        """保存数据到数据库。"""
        try:
            db = get_db_manager(self.db_path)
            
            for key, affection in self._data.items():
                group_id, user_id = key
                records_json = json_dumps(affection.records[-self.MAX_RECORDS:])
                max_affection_json = json_dumps(affection.max_affection_data.to_dict() if affection.max_affection_data else None)
                db.execute(
                    """INSERT OR REPLACE INTO affection_data 
                       (group_id, user_id, value, records, last_interaction, max_affection_data) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (group_id, user_id, affection.value, records_json, affection.last_interaction, max_affection_json)
                )
        except Exception as e:
            print(f"[!] 保存好感度数据失败: {e}")
    
    def _save_persona_preferences(self, preferences: PersonaPreferences) -> None:
        """保存人设喜好/雷点到数据库。
        
        Args:
            preferences: 人设喜好/雷点数据。
        """
        try:
            db = get_db_manager(self.db_path)
            db.execute(
                """INSERT OR REPLACE INTO persona_preferences 
                   (persona_hash, interests, favorite_things, dislikes, personality_summary, generated_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    preferences.persona_hash,
                    json_dumps(preferences.interests),
                    json_dumps(preferences.favorite_things),
                    json_dumps(preferences.dislikes),
                    preferences.personality_summary,
                    preferences.generated_at
                )
            )
        except Exception as e:
            print(f"[!] 保存人设喜好数据失败: {e}")
    
    def _save_persona_affection_config(self, config: PersonaAffectionConfig) -> None:
        """保存人设好感度配置到数据库。"""
        try:
            db = get_db_manager(self.db_path)
            db.execute(
                """INSERT OR REPLACE INTO persona_affection_configs 
                   (persona_hash, level_names, level_descriptions, tone_descriptions, generated_at) 
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    config.persona_hash,
                    json_dumps({f"{k[0]}_{k[1]}": v for k, v in config.level_names.items()}),
                    json_dumps(config.level_descriptions),
                    json_dumps(config.tone_descriptions),
                    config.generated_at
                )
            )
        except Exception as e:
            print(f"[!] 保存人设好感度配置失败: {e}")
    
    def get_affection(self, group_id: int, user_id: int) -> UserAffection:
        """获取用户的好感度数据（不存在则创建）。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            用户好感度数据。
        """
        key = (group_id, user_id)
        with self._lock:
            if key not in self._data:
                self._data[key] = UserAffection(
                    user_id=user_id,
                    group_id=group_id,
                    value=0,
                    records=[],
                    last_interaction=0
                )
            return self._data[key]
    
    def get_affection_value(self, group_id: int, user_id: int) -> int:
        """获取用户当前好感度值。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            当前好感度值。
        """
        return self.get_affection(group_id, user_id).value
    
    def _get_config_for_persona(self, persona_text: str = None) -> PersonaAffectionConfig:
        """获取人设对应的好感度配置。
        
        Args:
            persona_text: 人设文本。
        
        Returns:
            好感度配置，如果不存在则返回默认配置。
        """
        if not persona_text:
            return self._default_config
        
        persona_hash = self._get_persona_hash(persona_text)
        return self._persona_affection_configs.get(persona_hash, self._default_config)
    
    def get_affection_level(self, value: int, persona_text: str = None) -> str:
        """根据好感度值获取等级名称。
        
        Args:
            value: 好感度值。
            persona_text: 人设文本，为None则使用默认配置。
        
        Returns:
            等级名称。
        """
        config = self._get_config_for_persona(persona_text)
        for (min_val, max_val), level in config.level_names.items():
            if min_val <= value < max_val:
                return level
        return "未知"
    
    def get_level_description(self, level: str, persona_text: str = None) -> str:
        """获取等级描述。
        
        Args:
            level: 等级名称。
            persona_text: 人设文本。
        
        Returns:
            等级描述。
        """
        config = self._get_config_for_persona(persona_text)
        return config.level_descriptions.get(level, "关系状态未知")
    
    def get_tone_description(self, level: str, persona_text: str = None) -> str:
        """获取语气描述。
        
        Args:
            level: 等级名称。
            persona_text: 人设文本。
        
        Returns:
            语气描述。
        """
        config = self._get_config_for_persona(persona_text)
        return config.tone_descriptions.get(level, "你对用户保持中立态度。")
    
    def update_affection(
        self, 
        group_id: int, 
        user_id: int, 
        change: int, 
        reason: str = "", 
        user_message: str = "", 
        bot_reply: str = ""
    ) -> Tuple[int, int, bool]:
        """更新好感度值。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            change: 变化值（-5~5）。
            reason: 变化原因。
            user_message: 用户消息。
            bot_reply: 机器人回复。
        
        Returns:
            (新值, 实际变化值, 是否有变化)。
        """
        change = max(-5, min(5, change))
        
        key = (group_id, user_id)
        with self._lock:
            affection = self.get_affection(group_id, user_id)
            old_value = affection.value
            new_value = max(-100, min(100, old_value + change))
            actual_change = new_value - old_value
            
            affection.value = new_value
            affection.last_interaction = int(time.time())
            
            if actual_change != 0:
                record = {
                    "timestamp": int(time.time()),
                    "change": actual_change,
                    "reason": reason,
                    "user_message": user_message[:100] if user_message else "",
                    "bot_reply": bot_reply[:100] if bot_reply else "",
                    "old_value": old_value,
                    "new_value": new_value
                }
                affection.records.append(record)
                
                if len(affection.records) > self.MAX_RECORDS:
                    affection.records = affection.records[-self.MAX_RECORDS:]
                
                self._save()
                return new_value, actual_change, True
            
            return new_value, 0, False
    
    def reset_affection(self, group_id: int, user_id: int) -> int:
        """重置用户好感度。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            重置后的值（始终为0）。
        """
        key = (group_id, user_id)
        with self._lock:
            affection = self.get_affection(group_id, user_id)
            old_value = affection.value
            affection.value = 0
            affection.records = []
            affection.last_interaction = int(time.time())
            self._save()
            print(f"[*] 重置用户 ({group_id},{user_id}) 好感度: {old_value} -> 0")
            return 0
    
    def get_affection_prompt(self, group_id: int, user_id: int, persona_text: str = None) -> str:
        """获取好感度相关的系统提示词片段。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            persona_text: 人设文本。
        
        Returns:
            描述当前好感度状态的文本。
        """
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value, persona_text)
        
        # 根据人设获取语气描述
        tone = self.get_tone_description(level, persona_text)
        
        prompt = f"""【好感度状态】
当前等级: {level}（{value}/100）
语气设定: {tone}

注意: 
1. 你的回应必须严格符合上述语气设定，通过用词、语气、态度自然体现关系状态
2. 绝对不要直接提及"好感度"这个概念
3. 负好感度时要体现冷淡、疏离或不耐烦；陌生时要体现距离感；高好感度时要体现亲密和依赖"""
        
        return prompt
    
    def get_recent_records(self, group_id: int, user_id: int, count: int = 5) -> List[Dict]:
        """获取最近的好感度变化记录。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            count: 返回记录数。
        
        Returns:
            记录列表。
        """
        affection = self.get_affection(group_id, user_id)
        return affection.records[-count:] if affection.records else []
    
    def format_affection_info(self, group_id: int, user_id: int, persona_text: str = None) -> str:
        """格式化显示好感度信息。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            persona_text: 人设文本。
        
        Returns:
            格式化的好感度信息文本。
        """
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value, persona_text)
        records = self.get_recent_records(group_id, user_id, 3)
        
        result = f"【好感度】{level}（{value}/100）\n"
        
        if records:
            result += "最近变化:\n"
            for record in records:
                change_str = "📈" if record["change"] > 0 else "📉"
                result += f"  {change_str} {record['change']:+.0f} - {record['reason']}\n"
        else:
            result += "还没有好感度变化记录哦~\n"
        
        # 下一等级提示
        config = self._get_config_for_persona(persona_text)
        sorted_levels = sorted(config.level_names.items(), key=lambda x: x[0][0])
        for i, ((min_val, max_val), level_name) in enumerate(sorted_levels):
            if min_val <= value < max_val:
                need = max_val - value
                if need > 0:
                    result += f"距离下一等级还需 {need} 点好感度"
                break
        
        return result
    
    def get_personality_hint(self) -> str:
        """获取好感度提示。
        
        Returns:
            提示文本。
        """
        return "💝 好感度小贴士：\n• 真诚的态度比简单的问候更有效\n• 避免粗鲁或负面的言语"
    
    # ========== LLM 相关方法 ==========
    
    def _get_persona_hash(self, persona_text: str) -> str:
        """计算人设文本的哈希值。
        
        Args:
            persona_text: 人设文本。
        
        Returns:
            MD5 哈希值。
        """
        return hashlib.md5(persona_text.encode('utf-8')).hexdigest()
    
    def get_persona_preferences(self, persona_text: str) -> Optional[PersonaPreferences]:
        """获取人设的喜好/雷点配置。
        
        Args:
            persona_text: 人设文本。
        
        Returns:
            人设喜好/雷点数据，如果不存在则返回 None。
        """
        persona_hash = self._get_persona_hash(persona_text)
        return self._persona_preferences.get(persona_hash)
    
    async def generate_affection_config_for_persona(
        self, 
        persona_text: str, 
        skip_save: bool = False,
        force: bool = False
    ) -> PersonaAffectionConfig:
        """使用 LLM 为指定人设生成好感度配置。
        
        Args:
            persona_text: 人设文本。
            skip_save: 如果为 True，只返回配置但不保存到数据库。
            force: 如果为 True，强制重新生成，不使用缓存。
        
        Returns:
            生成的好感度配置。
        """
        persona_hash = self._get_persona_hash(persona_text)
        
        # 检查是否已存在（除非 force=True）
        if not force and persona_hash in self._persona_affection_configs:
            return self._persona_affection_configs[persona_hash]
        
        # 如果没有 LLM 服务，抛出异常
        if not self._llm:
            raise RuntimeError("没有 LLM 服务，无法生成人设好感度配置")
        
        # 使用 LLM 生成
        try:
            from qq_bot.services.llm.base import ChatMessage
            
            # 从配置读取 prompt，如果没有则使用默认 prompt
            system_prompt = None
            if self._prompts and hasattr(self._prompts, 'affection_config_generation'):
                system_prompt = self._prompts.affection_config_generation
            if not system_prompt:
                raise Exception("[!] 没有人设好感度配置prompt")

            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=f"人设：{persona_text}")
            ]
            
            print(f"[*] 正在为人设生成好感度配置...")
            
            response = await self._llm.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=2048
            )
            
            # 解析 JSON 响应
            import re
            content = response.content
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                
                # 构建 level_names 字典（只处理区间格式的键，如 "-100_-99"）
                level_names = {}
                level_names_raw = result.get("level_names", {})
                invalid_keys = []
                for key, value in level_names_raw.items():
                    # 验证键格式：必须是 "数字_数字" 的区间格式
                    if not re.match(r'^-?\d+_-?\d+$', key):
                        invalid_keys.append(key)
                        continue
                    parts = key.split("_")
                    try:
                        level_names[(int(parts[0]), int(parts[1]))] = value
                    except (ValueError, IndexError):
                        invalid_keys.append(key)
                        continue
                
                if invalid_keys:
                    print(f"[!] 警告: 跳过无效的 level_names 键: {invalid_keys}")
                
                if not level_names:
                    raise ValueError("没有有效的 level_names 数据")
                
                # 获取 level_descriptions 和 tone_descriptions
                level_descriptions = result.get("level_descriptions", {})
                tone_descriptions = result.get("tone_descriptions", {})
                
                # 如果 LLM 错误地将描述合并到 level_names 中，尝试提取
                # 检查是否有中文键（非区间格式）
                for key, value in level_names_raw.items():
                    # 匹配纯中文字符（排除区间格式的键）
                    if re.match(r'^[\u4e00-\u9fff]+$', key):
                        # 可能是描述被错误地放在 level_names 中
                        if key not in level_descriptions and len(str(value)) <= 50:
                            level_descriptions[key] = value
                        if key not in tone_descriptions and len(str(value)) <= 80:
                            tone_descriptions[key] = value
                
                config = PersonaAffectionConfig(
                    persona_hash=persona_hash,
                    level_names=level_names,
                    level_descriptions=level_descriptions,
                    tone_descriptions=tone_descriptions,
                    generated_at=int(time.time())
                )
                
                # 缓存
                self._persona_affection_configs[persona_hash] = config
                
                # 保存到数据库（除非 skip_save=True）
                if not skip_save:
                    self._save_persona_affection_config(config)
                
                print(f"[*] 人设好感度配置生成完成，共 {len(level_names)} 个等级")
                return config
            else:
                raise ValueError("无法从 LLM 响应中解析 JSON")
                
        except Exception as e:
            print(f"[!] 生成人设好感度配置失败: {e}")
            # 抛出异常，必须使用 LLM 生成配置
            raise RuntimeError(f"生成人设好感度配置失败: {e}") from e
    
    def check_max_affection_reward(
        self, 
        group_id: int, 
        user_id: int, 
        old_value: int, 
        new_value: int
    ) -> Optional[str]:
        """检查并发放满好感度奖励。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
            old_value: 变化前的好感度值。
            new_value: 变化后的好感度值。
        
        Returns:
            奖励消息文本，如果没有奖励则返回 None。
        """
        key = (group_id, user_id)
        with self._lock:
            affection = self.get_affection(group_id, user_id)
            
            # 首次达到满好感度
            if new_value >= 100 and old_value < 100:
                affection.max_affection_data = MaxAffectionReward(
                    first_reached_at=int(time.time()),
                    last_reward_at=int(time.time()),
                    reward_count=0
                )
                self._save()
                
                return (
                    "\n\n✨🎉✨🎉✨🎉✨🎉✨\n"
                    "💕 恭喜！我们的关系达到了最高点！💕\n"
                    "从这一刻起，你就是我最重要的人~\n"
                    "未来的每一天，我都会用特别的方式回应你💕\n"
                    "✨🎉✨🎉✨🎉✨🎉✨"
                )
            
            # 持续奖励：每日首次互动
            if new_value >= 100 and affection.max_affection_data:
                current_time = int(time.time())
                last_reward = affection.max_affection_data.last_reward_at
                
                # 检查是否超过24小时
                if current_time - last_reward >= 24 * 3600:
                    affection.max_affection_data.last_reward_at = current_time
                    affection.max_affection_data.reward_count += 1
                    self._save()
                    
                    # 随机选择一条持续奖励消息
                    daily_messages = [
                        "\n\n💕 今天也是我们感情满满的一天呢~",
                        "\n\n💕 每天见到你，都是我最开心的时刻~",
                        "\n\n💕 我们的羁绊，比昨天更深了呢~",
                        "\n\n💕 有你陪伴的每一天，都是特别的~",
                        "\n\n💕 最喜欢你了，今天也要开心哦~"
                    ]
                    return random.choice(daily_messages)
            
            return None
    
    async def generate_persona_preferences(
        self, 
        persona_text: str,
        force: bool = False
    ) -> PersonaPreferences:
        """使用 LLM 生成人设的喜好/雷点配置。
        
        Args:
            persona_text: 人设文本。
            force: 如果为 True，强制重新生成，不使用缓存。
        
        Returns:
            生成的人设喜好/雷点数据。
        """
        persona_hash = self._get_persona_hash(persona_text)
        
        # 检查是否已存在（除非 force=True）
        if not force and persona_hash in self._persona_preferences:
            return self._persona_preferences[persona_hash]
        
        # 如果没有 LLM 服务，抛出异常
        if not self._llm:
            raise RuntimeError("没有 LLM 服务，无法生成人设喜好配置")
        
        # 使用 LLM 生成
        try:
            from qq_bot.services.llm.base import ChatMessage
            
            system_prompt = self._prompts.preference_generation
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=f"人设：{persona_text}")
            ]
            
            print(f"[*] 正在为人设生成喜好/雷点配置...")
            
            response = await self._llm.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            
            # 解析 JSON 响应
            import re
            content = response.content
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                
                preferences = PersonaPreferences(
                    persona_hash=persona_hash,
                    interests=result.get("interests", ["聊天"]),
                    favorite_things=result.get("favorite_things", ["友好的对话"]),
                    dislikes=result.get("dislikes", ["粗鲁", "侮辱"]),
                    personality_summary=result.get("personality_summary", "未知性格")
                )
                
                # 缓存并保存
                self._persona_preferences[persona_hash] = preferences
                self._save_persona_preferences(preferences)
                
                print(f"[*] 人设喜好配置生成完成: {preferences.personality_summary}")
                print(f"    兴趣: {preferences.interests}")
                print(f"    喜好: {preferences.favorite_things}")
                print(f"    雷点: {preferences.dislikes}")
                
                return preferences
            else:
                raise ValueError("无法从 LLM 响应中解析 JSON")
                
        except Exception as e:
            print(f"[!] 生成人设喜好配置失败: {e}")
            # 抛出异常，必须使用 LLM 生成配置
            raise RuntimeError(f"生成人设喜好配置失败: {e}") from e
    
    async def evaluate_affection_with_llm(
        self,
        user_message: str,
        bot_reply: str,
        persona_text: str,
        current_affection: int
    ) -> Tuple[int, str]:
        """使用 LLM 评估好感度变化。
        
        Args:
            user_message: 用户消息。
            bot_reply: 机器人回复。
            persona_text: 当前人设文本。
            current_affection: 当前好感度值。
        
        Returns:
            (变化值, 原因)。变化值范围为 -5 到 +5。
        """
        # 首先确保有该人设的喜好/雷点配置
        preferences = self.get_persona_preferences(persona_text)
        if preferences is None:
            preferences = await self.generate_persona_preferences(persona_text)
        
        # 如果没有 LLM 服务，返回无变化
        if not self._llm:
            return 0, ""
        
        try:
            from qq_bot.services.llm.base import ChatMessage
            
            system_prompt = self._prompts.evaluation.format(
                persona_text=persona_text,
                interests=', '.join(preferences.interests),
                favorite_things=', '.join(preferences.favorite_things),
                dislikes=', '.join(preferences.dislikes)
            )
            
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=f"用户消息: {user_message}\n机器人回复: {bot_reply}\n当前好感度: {current_affection}")
            ]
            
            response = await self._llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=200
            )
            
            # 解析 JSON 响应
            import re
            content = response.content
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                change = max(-5, min(5, result.get("change", 0)))
                reason = result.get("reason", "LLM评估")
                return change, reason
            else:
                raise ValueError("无法从 LLM 响应中解析 JSON")
                
        except Exception as e:
            print(f"[!] LLM 好感度评估失败: {e}")
            # 返回无变化
            return 0, ""
