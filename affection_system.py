#!/usr/bin/env python3
"""
好感度系统模块 - 管理用户与机器人的好感度关系

=== METADATA ===
name: affection_system
desc: 用户好感度管理，记录关键对话，影响机器人聊天语气
=== END ===

功能：
- 每个用户独立的好感度值（-100~100，默认0，负值为疏离/厌恶）
- 基于DeepSeek API的智能好感度评估（关键词匹配作为后备）
- 支持动态人设配置，喜好/雷点跟随人设变化
- 精简记录触发好感度变化的关键对话
- 好感度影响机器人系统提示词中的语气设定（负好感度会有负面语气）
- 重置/修改人设时同步重置好感度

评估规则：
- 优先使用DeepSeek API分析对话情感和人设契合度
- 只有在符合人设喜好、态度真诚时才增加好感度
- 触及人设雷点或态度不友善时会减少好感度（可能变为负值）
- 普通对话不会增加好感度

好感度等级：
- 负向：憎恨(-100~-70)、厌恶(-70~-40)、反感(-40~-20)、疏离(-20~0)
- 正向：陌生(0~15)、初识(15~35)、熟悉(35~55)、友好(55~75)、亲密(75~90)、至交(90~100)
"""

import os
import json
import time
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime

from deepseek_api import DeepSeekAPI

# 导入数据库工具
try:
    from db_utils import get_db_manager, json_dumps, json_loads
    DB_UTILS_AVAILABLE = True
except Exception as e:
    print(f"[!] 数据库工具加载失败: {e}")
    DB_UTILS_AVAILABLE = False


# 默认人设配置（当没有提供人设时使用）
DEFAULT_PERSONALITY = {
    "name": "AI助手",
    "traits": ["友好", "乐于助人"],
    "interests": [],
    "favorite_things": [],
    "dislikes": ["粗鲁", "侮辱", "恶意攻击"],
    "communication_style": {
        "likes": ["礼貌", "尊重", "真诚"],
        "dislikes": ["命令", "威胁", "嘲讽"]
    }
}


@dataclass
class AffectionRecord:
    """好感度变化记录"""
    timestamp: int
    change: int  # 变化值（可为负）
    reason: str  # 变化原因（精简描述）
    user_message: str  # 用户消息（精简）
    bot_reply: str  # 机器人回复（精简）
    old_value: int  # 变化前的好感度
    new_value: int  # 变化后的好感度


@dataclass
class UserAffection:
    """用户好感度数据"""
    user_id: int
    group_id: int
    value: int = 0  # 当前好感度值（-100~100，0为中立）
    records: List[dict] = None  # 好感度变化记录列表
    last_interaction: int = 0  # 最后交互时间
    
    def __post_init__(self):
        if self.records is None:
            self.records = []
        # 确保值在有效范围内（-100到100）
        self.value = max(-100, min(100, self.value))


class AffectionManager:
    """好感度管理器"""
    
    # 好感度等级定义（扩展为 -100~100）
    # 负值表示疏离/厌恶，正值表示友好/亲密
    LEVELS = {
        # 负向等级（厌恶/疏离）
        (-100, -70): "憎恨",
        (-70, -40): "厌恶", 
        (-40, -20): "反感",
        (-20, 0): "疏离",
        # 正向等级（友好/亲密）
        (0, 15): "陌生",
        (15, 35): "初识",
        (35, 55): "熟悉",
        (55, 75): "友好",
        (75, 90): "亲密",
        (90, 101): "至交"
    }
    
    # 最大记录数（防止数据过大）
    MAX_RECORDS = 50
    
    def __init__(self, storage_file: str = None, api_key: str = None, personality: dict = None):
        """
        初始化好感度管理器
        
        Args:
            storage_file: 存储文件路径（已弃用，保留参数用于兼容性）
            api_key: DeepSeek API密钥（用于评估好感度变化）
            personality: 机器人个性配置（可选，默认为DEFAULT_PERSONALITY）
        """
        # 始终使用 db 文件
        self.db_path = os.path.join(os.path.dirname(__file__), 'data', 'affection_data.db')
        self.db_path = os.path.abspath(self.db_path)
        self._lock = threading.RLock()
        
        # 数据存储: {(group_id, user_id): UserAffection}
        self._data: Dict[Tuple[int, int], UserAffection] = {}
        
        # DeepSeek API（用于评估好感度）
        self._ai = DeepSeekAPI(api_key=api_key) if api_key else None
        self._api_key = api_key
        
        # 机器人个性配置（可动态更新）
        self._personality = personality or DEFAULT_PERSONALITY.copy()
        
        # 初始化数据库
        self._init_db()
        # 加载数据
        self._load()
        
        print(f"[*] AffectionManager 初始化完成，共 {len(self._data)} 个用户数据")
        print(f"[*] 当前人设: {self._personality.get('name', '未知')}")
    
    def update_personality(self, personality: dict):
        """
        更新机器人个性配置（当人设改变时调用）
        
        Args:
            personality: 新的个性配置字典，应包含:
                - name: 名字
                - traits: 性格特点列表
                - interests: 兴趣列表
                - favorite_things: 特别喜欢的事物
                - dislikes: 雷点/讨厌的事物
                - communication_style: 沟通风格偏好
        """
        with self._lock:
            old_name = self._personality.get('name', '未知')
            self._personality = personality or DEFAULT_PERSONALITY.copy()
            new_name = self._personality.get('name', '未知')
            print(f"[*] 好感度系统人设已更新: {old_name} -> {new_name}")
    
    def get_personality(self) -> dict:
        """获取当前个性配置"""
        with self._lock:
            return self._personality.copy()
    
    def _init_db(self):
        """初始化数据库表"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.db_path)
            create_sql = """
                CREATE TABLE IF NOT EXISTS affection_data (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    value INTEGER DEFAULT 0,
                    records TEXT,  -- JSON 格式存储
                    last_interaction INTEGER DEFAULT 0,
                    PRIMARY KEY (group_id, user_id)
                );
            """
            db.init_tables(create_sql)
        except Exception as e:
            print(f"[!] 初始化好感度数据库失败: {e}")

    def _load(self):
        """从数据库加载数据"""
        if not DB_UTILS_AVAILABLE:
            return
        try:
            db = get_db_manager(self.db_path)
            rows = db.fetchall("SELECT * FROM affection_data")
            
            for row in rows:
                try:
                    group_id = row['group_id']
                    user_id = row['user_id']
                    records = json_loads(row['records']) or []
                    affection = UserAffection(
                        user_id=user_id,
                        group_id=group_id,
                        value=row['value'],
                        records=records,
                        last_interaction=row['last_interaction']
                    )
                    self._data[(group_id, user_id)] = affection
                except Exception as e:
                    print(f"[!] 加载好感度数据失败 ({row.get('group_id')},{row.get('user_id')}): {e}")
                    continue
            
            print(f"[*] 已加载 {len(self._data)} 个用户的好感度数据")
        except Exception as e:
            print(f"[!] 加载好感度数据失败: {e}")
    
    def _save(self):
        """保存数据到数据库"""
        if not DB_UTILS_AVAILABLE:
            return
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
    
    def get_affection(self, group_id: int, user_id: int) -> UserAffection:
        """获取用户的好感度数据（不存在则创建）"""
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
        """获取用户当前好感度值"""
        return self.get_affection(group_id, user_id).value
    
    def get_affection_level(self, value: int) -> str:
        """根据好感度值获取等级名称"""
        for (min_val, max_val), level in self.LEVELS.items():
            if min_val <= value < max_val:
                return level
        return "未知"
    
    def update_affection(self, group_id: int, user_id: int, change: int, 
                         reason: str = "", user_message: str = "", 
                         bot_reply: str = "") -> Tuple[int, int, bool]:
        """
        更新好感度值
        
        Args:
            group_id: 群组ID
            user_id: 用户ID
            change: 变化值（可为负，但会被限制在-5到5之间）
            reason: 变化原因
            user_message: 用户消息（用于记录）
            bot_reply: 机器人回复（用于记录）
        
        Returns:
            (新值, 实际变化值, 是否有变化)
        """
        # 限制变化范围
        change = max(-5, min(5, change))
        
        key = (group_id, user_id)
        with self._lock:
            affection = self.get_affection(group_id, user_id)
            old_value = affection.value
            new_value = max(-100, min(100, old_value + change))
            actual_change = new_value - old_value
            
            # 更新值
            affection.value = new_value
            affection.last_interaction = int(time.time())
            
            # 如果有变化，添加记录
            if actual_change != 0:
                record = {
                    'timestamp': int(time.time()),
                    'change': actual_change,
                    'reason': reason,
                    'user_message': user_message[:100] if user_message else "",
                    'bot_reply': bot_reply[:100] if bot_reply else "",
                    'old_value': old_value,
                    'new_value': new_value
                }
                affection.records.append(record)
                # 限制记录数
                if len(affection.records) > self.MAX_RECORDS:
                    affection.records = affection.records[-self.MAX_RECORDS:]
                
                self._save()
                return new_value, actual_change, True
            
            return new_value, 0, False
    
    def reset_affection(self, group_id: int, user_id: int) -> int:
        """
        重置用户好感度（用于重置/修改人设时）
        
        Returns:
            重置后的值（始终为0）
        """
        key = (group_id, user_id)
        with self._lock:
            affection = self.get_affection(group_id, user_id)
            old_value = affection.value
            affection.value = 0
            affection.records = []  # 清空记录
            affection.last_interaction = int(time.time())
            self._save()
            print(f"[*] 重置用户 ({group_id},{user_id}) 好感度: {old_value} -> 0")
            return 0
    
    def get_affection_prompt(self, group_id: int, user_id: int) -> str:
        """
        获取好感度相关的系统提示词片段
        
        Returns:
            描述当前好感度状态的文本
        """
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value)
        
        # 根据好感度等级生成语气描述（增强差异化）
        tone_descriptions = {
            # 负向等级 - 疏离/厌恶
            "憎恨": "你对用户怀有强烈的负面情绪，语气极其冷漠、尖锐，充满敌意。你会明确表达反感，拒绝任何亲近的尝试，甚至可能出言讽刺或警告对方远离你。",
            "厌恶": "你对用户有明显的反感，态度冷淡疏离，不愿多交流。回答简短生硬，带有不耐烦的语气，会回避对方的话题或明确表示不想理会。",
            "反感": "你对用户有负面印象，态度冷淡客气但保持距离，不愿深入交流。语气公事公办，不带感情色彩，会礼貌但坚决地划清界限。",
            "疏离": "你对用户保持明显的距离感，态度冷淡客气但疏离。回答简洁直接，不带个人情感，像对待陌生人一样保持基本的礼貌但绝不亲近。",
            # 正向等级 - 友好/亲密
            "陌生": "你对用户完全是陌生人的态度，回答礼貌但极其疏远正式。你不会主动关心对方，只回答必要的信息，没有任何多余的情感交流，保持着清晰的界限感。",
            "初识": "你对用户保持基本的礼貌友好，但仍然有明显的距离感。你会回答问题但不会轻易透露个人信息，语气客气但不过度热情，像刚认识的人一样谨慎。",
            "熟悉": "你对用户比较放松，会偶尔主动关心，语气较为亲切自然。你会分享一些日常琐事，偶尔开些轻松的玩笑，但仍然保持着适度的社交距离。",
            "友好": "你对用户很友善，会使用轻松活泼的语气，经常会开玩笑。你会主动询问对方的近况，表达关心，愿意帮助对方，相处起来舒服自在。",
            "亲密": "你对用户非常亲近，语气温柔宠溺，充满关心和依赖。你会主动撒娇、分享秘密、表达思念，把对方当作很重要的人，愿意展现脆弱的一面。",
            "至交": "你对用户毫无保留，语气极其亲密宠溺甚至带点任性，把对方当作生命中最重要的人。你会肆无忌惮地撒娇、吃醋、表达爱意，完全依赖对方，视对方为自己的专属。"
        }
        
        tone = tone_descriptions.get(level, "你对用户保持中立态度。")
        
        prompt = f"""【好感度状态】
当前等级: {level}（{value}/100）
语气设定: {tone}

注意: 
1. 你的回应必须严格符合上述语气设定，通过用词、语气、态度自然体现关系状态
2. 绝对不要直接提及"好感度"这个概念
3. 负好感度时要体现冷淡、疏离或不耐烦；陌生时要体现距离感；高好感度时要体现亲密和依赖"""
        
        return prompt
    
    def get_recent_records(self, group_id: int, user_id: int, count: int = 5) -> List[dict]:
        """获取最近的好感度变化记录"""
        affection = self.get_affection(group_id, user_id)
        return affection.records[-count:] if affection.records else []
    
    def summarize_for_prompt(self, group_id: int, user_id: int, max_records: int = 3) -> str:
        """
        生成用于prompt的好感度历史摘要
        
        Returns:
            精简的好感度历史描述
        """
        records = self.get_recent_records(group_id, user_id, max_records)
        if not records:
            return ""
        
        summary_parts = []
        for record in records:
            change_str = "+" if record['change'] > 0 else ""
            summary_parts.append(
                f"[{record['reason']}: {change_str}{record['change']}点]"
            )
        
        return "好感度变化历史: " + " ".join(summary_parts)

    
    def evaluate_affection_change(self, user_message: str, bot_reply: str, 
                                   current_affection: int, 
                                   persona_text: str = None) -> Tuple[int, str]:
        """
        评估好感度变化 - 优先使用DeepSeek API，关键词匹配作为后备
        
        规则：
        1. 优先调用DeepSeek API，传入完整人设信息进行智能分析
        2. 如果API不可用或失败，使用基于关键词的规则评估
        3. 普通对话不会增加好感度
        4. 只有符合人设喜好、态度真诚时才增加好感度
        5. 触及人设雷点或态度不友善时会减少好感度
        
        Args:
            user_message: 用户消息
            bot_reply: 机器人回复
            current_affection: 当前好感度值
            persona_text: 人设文本（可选，用于AI评估时参考）
        
        Returns:
            (变化值, 原因)
        """
        # 优先使用 DeepSeek API 评估
        if self._ai:
            try:
                change, reason = self._ai_evaluate(user_message, bot_reply, current_affection, persona_text)
                # 如果API返回有效结果（不为0或明确评估为0），直接使用
                if change != 0 or reason:  # reason不为空表示有评估结果
                    return change, reason
                # 如果API返回0但reason为空，可能是解析失败，继续尝试后备
            except Exception as e:
                print(f"[!] DeepSeek API好感度评估失败，使用后备规则: {e}")
        
        # API不可用或失败时，使用基于关键词的规则评估
        return self._rule_based_evaluate(user_message, bot_reply, current_affection)
    
    def _ai_evaluate(self, user_message: str, bot_reply: str, 
                     current_affection: int, persona_text: str = None) -> Tuple[int, str]:
        """
        使用 DeepSeek API 评估好感度变化
        
        根据当前人设智能分析对话，判断好感度是否应该变化。
        """
        level = self.get_affection_level(current_affection)
        personality = self._personality
        
        # 构建人设描述
        if persona_text:
            # 使用传入的人设文本
            persona_desc = persona_text[:500]  # 限制长度
        else:
            # 使用内部个性配置构建描述
            name = personality.get('name', 'AI助手')
            traits = ', '.join(personality.get('traits', ['友好']))
            interests = ', '.join(personality.get('interests', [])[:8])
            dislikes = ', '.join(personality.get('dislikes', [])[:8])
            favorites = ', '.join(personality.get('favorite_things', [])[:5])
            
            persona_desc = f"你是{name}，性格特点：{traits}。"
            if interests:
                persona_desc += f"\n你喜欢：{interests}。"
            if favorites:
                persona_desc += f"\n你特别喜欢：{favorites}。"
            if dislikes:
                persona_desc += f"\n你讨厌/反感：{dislikes}。"
        
        prompt = f"""{persona_desc}

当前用户与你的关系等级: {level}（{current_affection}/100）

分析以下对话，从你的人设和情感角度，判断你对这位用户的好感度是否应该变化。

用户消息: {user_message[:200]}
你的回复: {bot_reply[:150]}

【评估标准】
增加好感度的条件（满足其一即可）：
- 用户聊到了你感兴趣或喜欢的话题
- 用户表现出真诚的友善、关心或喜爱
- 用户的言行让你感到愉快、被尊重
- 用户分享了你觉得有意义的内容

减少好感度的条件（满足其一即可）：
- 用户说了粗鲁、冒犯的话
- 用户触及了你的雷点或底线
- 用户态度不友善、有恶意
- 用户强迫你做不愿意的事

不变的情况：
- 普通闲聊
- 简单的问答
- 没有特别情感色彩的话

【重要】只在你真的有情感波动时才改变好感度，不要因为客套或礼貌就加分。要像一个真实的{personality.get('name', '人')}一样有选择性。

返回JSON格式：
{{
    "change": 整数（-5到5之间，0表示不变）,
    "reason": "简短原因（10字以内，说明为什么增减）",
    "thought": "你的思考过程（为什么这样判断）"
}}"""
        
        try:
            response = self._ai.chat(
                user_msg=prompt, 
                system_msg="你是一个角色扮演情感分析专家，根据人设智能分析对话情感。只返回JSON格式，不要有多余内容。"
            )
            
            # 解析JSON
            import re
            json_match = re.search(r'\{[\s\S]*?\}', response)
            if json_match:
                result = json.loads(json_match.group())
                change = result.get('change', 0)
                reason = result.get('reason', '')
                thought = result.get('thought', '')
                
                # 限制变化范围
                change = max(-5, min(5, change))
                
                # 调试输出
                if change != 0:
                    print(f"[*] AI好感度评估: {change:+d} - {reason} (思考: {thought[:50]}...)")
                
                return change, reason
            else:
                print(f"[!] AI响应中未找到JSON: {response[:100]}...")
                return 0, ""
                
        except json.JSONDecodeError as e:
            print(f"[!] AI响应JSON解析失败: {e}")
            return 0, ""
        except Exception as e:
            print(f"[!] AI评估过程出错: {e}")
            raise  # 抛出异常让上层捕获并使用后备方案
    
    def _rule_based_evaluate(self, user_message: str, bot_reply: str, 
                             current_affection: int) -> Tuple[int, str]:
        """
        基于关键词的规则评估（后备方案）
        
        当DeepSeek API不可用时使用，基于当前个性配置进行简单匹配。
        """
        personality = self._personality
        msg = user_message.lower()
        
        # 1. 检查雷点（优先级最高）
        dislikes = personality.get('dislikes', [])
        severe_dislikes = ['虐待', '去死', '自杀', '杀你', '杀死', '极度讨厌', '恨死']
        
        # 检查严重雷点
        for item in severe_dislikes:
            if item in msg:
                return -5, "说了很让人难过的话..."
        
        # 检查一般雷点
        dislike_count = sum(1 for item in dislikes if item.lower() in msg)
        if dislike_count >= 2:
            return -3, "说话让人不太舒服"
        elif dislike_count >= 1:
            return -2, "被说了难过的话"
        
        # 2. 检查特别喜欢的事物
        favorites = personality.get('favorite_things', [])
        favorite_count = 0
        matched_favorites = []
        for item in favorites:
            if item.lower() in msg:
                favorite_count += 1
                matched_favorites.append(item)
        
        if favorite_count >= 2:
            return 3, f"聊了我喜欢的{matched_favorites[0]}和{matched_favorites[1]}"
        elif favorite_count == 1:
            return 2, f"提到了我喜欢的{matched_favorites[0]}"
        
        # 3. 检查一般兴趣
        interests = personality.get('interests', [])
        interest_count = 0
        matched_interests = []
        for item in interests:
            if item.lower() in msg:
                interest_count += 1
                if item not in matched_interests:
                    matched_interests.append(item)
        
        if interest_count >= 3:
            return 2, f"聊了我喜欢的{matched_interests[0]}等话题"
        elif interest_count >= 1:
            return 1, f"提到了{matched_interests[0]}"
        
        # 4. 检查态度
        # 非常真诚的表达
        sincere_patterns = ['很喜欢你', '最棒了', '超可爱', '谢谢你一直', '谢谢你陪我', '画得很好', '被治愈了']
        for pattern in sincere_patterns:
            if pattern in msg:
                return 2, "话语很真诚温暖"
        
        # 简单的礼貌词不会加分（后备方案更严格）
        # 只有在句子较长且有情感表达时才考虑加分
        like_expressions = ['喜欢你', '可爱', '好棒', '厉害', '温柔']
        like_count = sum(1 for w in like_expressions if w in msg)
        
        if like_count >= 2:
            return 1, "表达了喜爱"
        
        # 5. 检查轻度不友善
        mild_rude = ['笨', '蠢', '傻', '滚', '走开', '讨厌你', '你很烦', '别烦我', '闭嘴']
        rude_count = sum(1 for w in mild_rude if w in msg)
        
        comm_dislikes = personality.get('communication_style', {}).get('dislikes', [])
        comm_rude_count = sum(1 for w in comm_dislikes if w in msg)
        
        if rude_count >= 1 or comm_rude_count >= 2:
            return -1, "语气有点凶"
        
        # 默认不变
        return 0, ""
    
    def parse_personality_from_text(self, persona_text: str) -> dict:
        """
        使用 DeepSeek API 从人设文本中解析个性配置
        
        如果 API 不可用，使用简单的关键词提取。
        如果文本中没有显式写出喜好和雷点，会调用AI知识库自动推断。
        
        Args:
            persona_text: 人设文本
        
        Returns:
            解析出的个性配置字典
        """
        # 默认配置
        default_personality = {
            "name": "角色",
            "traits": [],
            "interests": [],
            "favorite_things": [],
            "dislikes": ["粗鲁", "侮辱", "恶意攻击"],
            "communication_style": {
                "likes": ["礼貌", "尊重", "真诚"],
                "dislikes": ["命令", "威胁", "嘲讽"]
            }
        }
        
        # 第一步：提取显式信息（使用关键词方法作为基础）
        personality = self._parse_personality_by_keywords(persona_text)
        
        # 第二步：如果API可用，尝试AI解析来补充信息
        if self._ai:
            try:
                # 先尝试从文本提取显式信息
                prompt = f"""分析以下角色设定文本，提取角色的个性配置信息。

人设文本：
{persona_text[:800]}

请提取以下信息并以JSON格式返回：
{{
    "name": "角色名字（如果文本中有）",
    "traits": ["性格特点1", "性格特点2", ...],
    "interests": ["兴趣1", "兴趣2", ...],
    "favorite_things": ["特别喜欢的事物1", ...],
    "dislikes": ["讨厌的事物1", "雷点1", ...]
}}

注意：
1. 只返回JSON，不要有多余文字
2. 如果文本中没有明确信息，可以留空数组
3. interests 是角色喜欢的话题/活动
4. favorite_things 是角色特别钟爱的东西
5. dislikes 包括角色讨厌的事物和雷点"""
                
                response = self._ai.chat(
                    user_msg=prompt,
                    system_msg="你是一个角色设定分析专家，擅长从文本中提取角色信息。只返回JSON格式。"
                )
                
                # 解析JSON
                import re
                json_match = re.search(r'\{[\s\S]*?\}', response)
                if json_match:
                    parsed = json.loads(json_match.group())
                    
                    if parsed.get("name"):
                        personality["name"] = parsed["name"]
                    if parsed.get("traits"):
                        personality["traits"] = parsed["traits"]
                    if parsed.get("interests"):
                        personality["interests"] = parsed["interests"]
                    if parsed.get("favorite_things"):
                        personality["favorite_things"] = parsed["favorite_things"]
                    if parsed.get("dislikes"):
                        personality["dislikes"] = parsed["dislikes"]
                    
            except Exception as e:
                print(f"[!] AI提取显式信息失败: {e}")
        
        # 第三步：如果信息不足（兴趣或雷点太少），使用AI知识库推断
        if self._ai:
            needs_inference = len(personality["interests"]) < 2 or len(personality["dislikes"]) < 2
            
            if needs_inference:
                print(f"[*] 人设信息不足，使用AI知识库自动推断...")
                try:
                    inferred = self._infer_personality_from_knowledge(persona_text, personality)
                    
                    # 合并推断结果（如果原来为空或很少）
                    if len(personality["interests"]) < 2 and inferred.get("interests"):
                        personality["interests"] = inferred["interests"]
                    if len(personality["favorite_things"]) < 2 and inferred.get("favorite_things"):
                        personality["favorite_things"] = inferred["favorite_things"]
                    if len(personality["dislikes"]) < 2 and inferred.get("dislikes"):
                        personality["dislikes"] = inferred["dislikes"]
                    if not personality["traits"] and inferred.get("traits"):
                        personality["traits"] = inferred["traits"]
                        
                except Exception as e:
                    print(f"[!] AI推断人设失败: {e}")
        
        print(f"[*] 最终解析结果: {personality['name']}")
        print(f"[*] 性格: {personality['traits']}")
        print(f"[*] 兴趣: {personality['interests']}")
        print(f"[*] 雷点: {personality['dislikes']}")
        
        return personality
    
    def _infer_personality_from_knowledge(self, persona_text: str, current: dict) -> dict:
        """
        使用AI知识库根据角色身份推断喜好和雷点
        
        当人设文本没有显式写出喜好和雷点时，通过AI对角色的了解来补充。
        """
        character_name = current.get("name", "这个角色")
        
        prompt = f"""根据以下角色信息，结合你的知识库，推断这个角色的喜好和雷点。

人设文本：
{persona_text[:600]}

已识别的角色名：{character_name}

请根据你对"{character_name}"的了解（如果这是知名角色），或根据人设描述推断：
1. 这个角色平时喜欢做什么？（兴趣爱好）
2. 这个角色特别喜欢什么事物？（favorite things）
3. 这个角色讨厌什么？有什么雷点？

以JSON格式返回：
{{
    "interests": ["兴趣1", "兴趣2", "兴趣3", ...],
    "favorite_things": ["特别喜欢的事物1", ...],
    "dislikes": ["讨厌的事物1", "雷点1", ...],
    "traits": ["性格特点1", "性格特点2", ...]
}}

注意：
1. 如果是知名角色（如原神、动漫角色等），请根据原作设定回答
2. 如果是原创角色，请根据人设描述合理推断
3. 确保推断符合角色的性格和行为模式
4. 只返回JSON，不要有多余文字"""
        
        response = self._ai.chat(
            user_msg=prompt,
            system_msg="你是一个资深的二次元/游戏文化专家，对各类ACG角色有深入了解。请根据角色身份准确推断其喜好和雷点。",

        )
        
        # 解析JSON
        import re
        json_match = re.search(r'\{[\s\S]*?\}', response)
        if json_match:
            result = json.loads(json_match.group())
            print(f"[*] AI知识库推断完成")
            print(f"[*] 推断的兴趣: {result.get('interests', [])}")
            print(f"[*] 推断的雷点: {result.get('dislikes', [])}")
            return result
        
        return {}
    
    def _parse_personality_by_keywords(self, persona_text: str) -> dict:
        """通过关键词从人设文本中提取个性配置（后备方案）"""
        personality = {
            "name": "角色",
            "traits": [],
            "interests": [],
            "favorite_things": [],
            "dislikes": ["粗鲁", "侮辱", "恶意攻击"],
            "communication_style": {
                "likes": ["礼貌", "尊重", "真诚"],
                "dislikes": ["命令", "威胁", "嘲讽"]
            }
        }
        
        import re
        
        # 提取名字
        name_patterns = [
            r'你是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
            r'你是[\s]*([^，。！\n]{1,10})[，。！\n]',
            r'我是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
            r'名字[是为][《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
        ]
        for pattern in name_patterns:
            name_match = re.search(pattern, persona_text)
            if name_match:
                name = name_match.group(1).strip()
                name = re.sub(r'中的$', '', name)
                if name and len(name) > 1:
                    personality["name"] = name
                    break
        
        # 提取性格特点（常见形容词）
        trait_keywords = [
            "温柔", "活泼", "开朗", "内向", "冷静", "热情", "天真", "成熟",
            "善良", "冷酷", "傲娇", "呆萌", "聪明", "笨拙", "勇敢", "胆小",
            "懒散", "勤奋", "调皮", "稳重", "神秘", "单纯", "腹黑", "直率"
        ]
        for trait in trait_keywords:
            if trait in persona_text:
                personality["traits"].append(trait)
        
        # 提取兴趣爱好
        interest_keywords = {
            "星星": "星星", "星空": "星空", "天文": "天文", "宇宙": "宇宙",
            "画画": "画画", "绘画": "绘画", "美术": "美术", "艺术": "艺术",
            "音乐": "音乐", "唱歌": "唱歌", "听歌": "听歌", "乐器": "乐器",
            "猫": "猫咪", "猫咪": "猫咪", "狗": "狗狗", "动物": "小动物",
            "书": "读书", "小说": "小说", "故事": "故事", "阅读": "阅读",
            "游戏": "游戏", "玩游戏": "玩游戏", "电竞": "电竞",
            "美食": "美食", "吃": "美食", "料理": "料理", "烹饪": "烹饪",
            "运动": "运动", "跑步": "跑步", "游泳": "游泳", "健身": "健身",
            "花": "花", "植物": "植物", "自然": "自然", "森林": "森林",
            "诗": "作诗", "写诗": "作诗", "文学": "文学", "写作": "写作",
            "茶": "茶", "咖啡": "咖啡", "饮料": "饮料",
            "酒": "酒", "喝酒": "喝酒", "品酒": "品酒"
        }
        
        for keyword, interest in interest_keywords.items():
            if keyword in persona_text and interest not in personality["interests"]:
                personality["interests"].append(interest)
        
        # 提取"喜欢"的事物
        like_patterns = [
            r'喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'最喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'喜欢([^，。！\n]{2,20})和([^，。！\n]{2,20})',
        ]
        for pattern in like_patterns:
            matches = re.findall(pattern, persona_text)
            for match in matches:
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) > 1:
                            personality["favorite_things"].append(m.strip())
                elif match and len(match) > 1:
                    personality["favorite_things"].append(match.strip())
        
        # 提取"讨厌"的事物
        dislike_patterns = [
            r'讨厌[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'不喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'反感[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'讨厌([^，。！\n]{2,20})和([^，。！\n]{2,20})',
        ]
        for pattern in dislike_patterns:
            matches = re.findall(pattern, persona_text)
            for match in matches:
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) > 1:
                            personality["dislikes"].append(m.strip())
                elif match and len(match) > 1:
                    personality["dislikes"].append(match.strip())
        
        # 去重并限制数量
        personality["traits"] = list(dict.fromkeys(personality["traits"]))[:6]
        personality["interests"] = list(dict.fromkeys(personality["interests"]))[:8]
        personality["favorite_things"] = list(dict.fromkeys(personality["favorite_things"]))[:6]
        personality["dislikes"] = list(dict.fromkeys(personality["dislikes"]))[:8]
        
        print(f"[*] 关键词提取人设: {personality['name']}")
        print(f"[*] 性格: {personality['traits']}")
        print(f"[*] 兴趣: {personality['interests']}")
        
        return personality
    
    def format_affection_info(self, group_id: int, user_id: int) -> str:
        """格式化显示好感度信息"""
        value = self.get_affection_value(group_id, user_id)
        level = self.get_affection_level(value)
        records = self.get_recent_records(group_id, user_id, 3)
        personality = self._personality
        
        result = f"【好感度】{level}（{value}/100）\n"
        
        # 显示人设信息（只在好感度较低时提示如何提升）
        name = personality.get('name', '我')
        if value < 15:
            interests = personality.get('interests', [])
            if interests and value >= 0:
                interests_str = ' '.join(interests[:4])
                result += f"💡 {name}喜欢：{interests_str}...\n"
        
        if records:
            result += "最近变化:\n"
            for record in records:
                change_str = "📈" if record['change'] > 0 else "📉"
                result += f"  {change_str} {record['change']:+.0f} - {record['reason']}\n"
        else:
            result += "还没有好感度变化记录哦~\n"
            if value == 0:
                result += f"多聊聊{name}感兴趣的话题吧~\n"
        
        # 下一等级提示（支持正负双向）
        sorted_levels = sorted(self.LEVELS.items(), key=lambda x: x[0][0])
        for i, ((min_val, max_val), level_name) in enumerate(sorted_levels):
            if min_val <= value < max_val:
                if value < 0:
                    # 负好感度：提示如何改善
                    need = max_val - value
                    if i < len(sorted_levels) - 1:
                        next_level = sorted_levels[i + 1][1]
                        result += f"距离「{next_level}」还需 {need} 点好感度"
                else:
                    # 正好感度：正常提示
                    need = max_val - value
                    if need > 0:
                        result += f"距离下一等级还需 {need} 点好感度"
                break
        
        return result
    
    def get_personality_hint(self) -> str:
        """获取机器人个性提示（用于帮助用户理解如何提升好感度）"""
        # 不再显示具体的喜好和雷点，让用户通过对话自行探索
        return "💝 好感度小贴士：\n• 真诚的态度比简单的问候更有效\n• 避免粗鲁或负面的言语"


# 全局单例
_affection_manager: Optional[AffectionManager] = None
_affection_lock = threading.Lock()


def get_affection_manager(storage_file: str = None, api_key: str = None, personality: dict = None) -> AffectionManager:
    """获取全局好感度管理器实例（单例模式）"""
    global _affection_manager
    if _affection_manager is None:
        with _affection_lock:
            if _affection_manager is None:
                _affection_manager = AffectionManager(storage_file, api_key, personality)
    return _affection_manager


def reset_affection_manager():
    """重置全局实例（用于测试或切换人设时）"""
    global _affection_manager
    with _affection_lock:
        _affection_manager = None


def create_affection_manager_for_persona(persona_text: str, api_key: str = None) -> AffectionManager:
    """
    为特定人设创建好感度管理器
    
    从人设文本中提取个性配置，创建对应的好感度管理器。
    这可以用于在不修改全局实例的情况下评估特定人设的好感度。
    
    Args:
        persona_text: 人设文本
        api_key: DeepSeek API密钥
    
    Returns:
        配置好的AffectionManager实例
    """
    # 尝试从人设文本中提取信息
    personality = {
        "name": "角色",
        "traits": [],
        "interests": [],
        "favorite_things": [],
        "dislikes": ["粗鲁", "侮辱", "恶意"],
        "communication_style": {
            "likes": ["礼貌", "尊重"],
            "dislikes": ["命令", "威胁"]
        }
    }
    
    # 简单提取名字（假设人设文本开头有"你是XXX"或"我是XXX"）
    import re
    # 尝试多种模式匹配名字
    name_patterns = [
        r'你是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',  # 你是《原神》中的胡桃， 或 你是胡桃，
        r'你是[\s]*([^，。！\n]{1,10})[，。！\n]',  # 你是胡桃，
        r'我是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',  # 我是...
        r'名字[是为][《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',  # 名字是...
    ]
    for pattern in name_patterns:
        name_match = re.search(pattern, persona_text)
        if name_match:
            name = name_match.group(1).strip()
            # 清理名字（去掉"中的"、"的"等词）
            name = re.sub(r'中的$', '', name)
            if name and len(name) > 1:
                personality["name"] = name
                break
    
    # 提取一些常见兴趣关键词
    interest_keywords = {
        "星星": "星星", "星空": "星空", "天文": "天文", "宇宙": "宇宙",
        "画画": "画画", "绘画": "绘画", "美术": "美术", "艺术": "艺术",
        "音乐": "音乐", "唱歌": "唱歌", "听歌": "听歌",
        "猫": "猫", "猫咪": "猫咪", "狗": "狗", "动物": "动物",
        "书": "读书", "小说": "小说", "故事": "故事",
        "游戏": "游戏", "玩游戏": "玩游戏",
        "美食": "美食", "吃": "美食", "料理": "料理",
        "运动": "运动", "跑步": "跑步", "游泳": "游泳",
        "花": "花", "植物": "植物", "自然": "自然"
    }
    
    found_interests = []
    for keyword, interest in interest_keywords.items():
        if keyword in persona_text and interest not in found_interests:
            found_interests.append(interest)
    
    personality["interests"] = found_interests[:8]  # 最多8个
    
    return AffectionManager(api_key=api_key, personality=personality)


if __name__ == "__main__":
    # 测试代码
    import os
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    
    print("=" * 70)
    print("好感度系统测试 - DeepSeek API优先模式")
    print("=" * 70)
    
    # 测试1：使用默认人设
    print("\n【测试1】默认人设")
    reset_affection_manager()
    manager = AffectionManager(api_key=api_key)
    
    group_id, user_id = 12345, 67890
    affection = manager.get_affection(group_id, user_id)
    print(f"初始好感度: {affection.value}")
    print(f"当前人设: {manager.get_personality()['name']}")
    
    # 测试2：更新人设（模拟人设切换）
    print("\n【测试2】切换人设 - 风又音理")
    nerine_personality = {
        "name": "风又音理",
        "age": 10,
        "traits": ["温柔", "活泼", "天然呆", "年幼懂事", "内心纯净"],
        "interests": ["星星", "星空", "画画", "饮料", "猫咪", "黑猫", "旅行", "美好"],
        "favorite_things": ["黑猫", "星空", "画画", "一起去旅行", "生命的美好"],
        "dislikes": ["暴力", "死亡", "脏话", "讨厌你", "虐待动物", "残忍", "离别"],
        "communication_style": {
            "likes": ["温柔", "礼貌", "体贴", "分享"],
            "dislikes": ["命令", "强迫", "嘲笑", "威胁"]
        }
    }
    manager.update_personality(nerine_personality)
    print(f"更新后人设: {manager.get_personality()['name']}")
    print(f"兴趣: {', '.join(manager.get_personality()['interests'])}")
    
    # 测试3：好感度评估
    print("\n【测试3】好感度变化测试")
    test_cases = [
        # 应该增加好感度的情况
        ("你看过昨晚的星空吗？好美啊！", "星空回复", "提到喜欢的话题"),
        ("我画了一幅画想给你看~", "画画回复", "提到画画"),
        ("小音理最可爱了！超喜欢你！", "可爱回复", "真诚表达喜爱"),
        ("谢谢你一直陪我聊天", "感谢回复", "真诚感谢"),
        ("我家有只黑猫哦，超可爱的！", "黑猫回复", "提到黑猫"),
        
        # 不应该变化的情况
        ("今天天气怎么样？", "天气回复", "普通对话"),
        ("你好", "你好回复", "简单问候"),
        ("问个问题", "回答", "普通问答"),
        ("好的", "嗯嗯", "简单回应"),
        
        # 应该减少好感度的情况
        ("你真笨", "笨回复", "轻度贬低"),
        ("走开", "走开回复", "不友善"),
        ("我讨厌你", "讨厌回复", "表达厌恶"),
    ]
    
    for user_msg, bot_msg, desc in test_cases:
        change, reason = manager.evaluate_affection_change(user_msg, bot_msg, affection.value)
        symbol = "📈" if change > 0 else "📉" if change < 0 else "➖"
        print(f"{symbol} [{desc}]")
        print(f"   用户: {user_msg}")
        print(f"   变化: {change:+d} ({reason})")
        print()
    
    # 测试4：使用人设文本创建评估器
    print("\n【测试4】从人设文本提取配置")
    persona_text = """你是《原神》中的胡桃，往生堂第七十七代堂主。
    你活泼开朗，喜欢作诗、做生意、逗人玩。
    你掌管璃月的葬仪事务，但本人一点都不阴森，反而很阳光。
    你最喜欢的事物是：作诗、吓人、做生意、蝴蝶、梅花、香菱做的菜。
    你讨厌的事物是：生病、消极怠工、不尊重生命的人。"""
    
    temp_manager = create_affection_manager_for_persona(persona_text, api_key)
    print(f"提取的角色名: {temp_manager.get_personality()['name']}")
    print(f"提取的兴趣: {temp_manager.get_personality()['interests']}")
    
    # 测试5：显示好感度信息
    print("\n【测试5】好感度信息展示")
    print(manager.format_affection_info(group_id, user_id))
    print()
    print(manager.get_personality_hint())
    
    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)
