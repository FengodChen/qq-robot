"""好感度系统模块。

管理用户与机器人的好感度关系，影响聊天语气。
支持基于 LLM 的好感度评估和人设喜好/雷点生成。
"""

import json
import hashlib
import time
import threading
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
class UserAffection:
    """用户好感度数据。
    
    Attributes:
        user_id: 用户 QQ 号。
        group_id: 群组 ID。
        value: 当前好感度值（-100~100）。
        records: 好感度变化记录列表。
        last_interaction: 最后交互时间戳。
    """
    user_id: int
    group_id: int
    value: int = 0
    records: List[Dict] = None
    last_interaction: int = 0
    
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
        (15, 35): "初识",
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
        
        # 初始化数据库
        self._init_db()
        self._load()
        
        print(f"[*] AffectionManager 初始化完成，共 {len(self._data)} 个用户数据")
    
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
            
            # 用户好感度表
            create_affection_sql = """
                CREATE TABLE IF NOT EXISTS affection_data (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    value INTEGER DEFAULT 0,
                    records TEXT,
                    last_interaction INTEGER DEFAULT 0,
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
            
        except Exception as e:
            print(f"[!] 初始化好感度数据库失败: {e}")
    
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
                    
                    affection = UserAffection(
                        user_id=user_id,
                        group_id=group_id,
                        value=row["value"],
                        records=records,
                        last_interaction=row["last_interaction"]
                    )
                    self._data[(group_id, user_id)] = affection
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
            
            print(f"[*] 已加载 {len(self._data)} 个用户的好感度数据，{len(self._persona_preferences)} 个人设喜好配置")
                    
        except Exception as e:
            print(f"[!] 加载数据失败: {e}")
    
    def _save(self) -> None:
        """保存数据到数据库。"""
        try:
            db = get_db_manager(self.db_path)
            
            for key, affection in self._data.items():
                group_id, user_id = key
                records_json = json_dumps(affection.records[-self.MAX_RECORDS:])
                db.execute(
                    """INSERT OR REPLACE INTO affection_data 
                       (group_id, user_id, value, records, last_interaction) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (group_id, user_id, affection.value, records_json, affection.last_interaction)
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
    
    def get_affection_level(self, value: int) -> str:
        """根据好感度值获取等级名称。
        
        Args:
            value: 好感度值。
        
        Returns:
            等级名称。
        """
        for (min_val, max_val), level in self.LEVELS.items():
            if min_val <= value < max_val:
                return level
        return "未知"
    
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
    
    def get_affection_prompt(self, group_id: int, user_id: int) -> str:
        """获取好感度相关的系统提示词片段。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            描述当前好感度状态的文本。
        """
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value)
        
        # 根据好感度等级生成语气描述
        tone = self._tone_descriptions.get(level, "你对用户保持中立态度。")
        
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
    
    def format_affection_info(self, group_id: int, user_id: int) -> str:
        """格式化显示好感度信息。
        
        Args:
            group_id: 群组 ID。
            user_id: 用户 ID。
        
        Returns:
            格式化的好感度信息文本。
        """
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value)
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
        sorted_levels = sorted(self.LEVELS.items(), key=lambda x: x[0][0])
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
    
    async def generate_persona_preferences(self, persona_text: str) -> PersonaPreferences:
        """使用 LLM 生成人设的喜好/雷点配置。
        
        Args:
            persona_text: 人设文本。
        
        Returns:
            生成的人设喜好/雷点数据。
        """
        persona_hash = self._get_persona_hash(persona_text)
        
        # 检查是否已存在
        if persona_hash in self._persona_preferences:
            return self._persona_preferences[persona_hash]
        
        # 如果没有 LLM 服务，返回默认配置
        if not self._llm:
            print(f"[!] 没有 LLM 服务，使用默认人设喜好配置")
            preferences = PersonaPreferences(
                persona_hash=persona_hash,
                interests=["聊天", "交流"],
                favorite_things=["友好的对话"],
                dislikes=["粗鲁", "侮辱", "恶意攻击"],
                personality_summary="默认性格"
            )
            self._persona_preferences[persona_hash] = preferences
            self._save_persona_preferences(preferences)
            return preferences
        
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
            # 返回默认配置
            preferences = PersonaPreferences(
                persona_hash=persona_hash,
                interests=["聊天", "交流"],
                favorite_things=["友好的对话"],
                dislikes=["粗鲁", "侮辱", "恶意攻击"],
                personality_summary="默认性格"
            )
            self._persona_preferences[persona_hash] = preferences
            self._save_persona_preferences(preferences)
            return preferences
    
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
