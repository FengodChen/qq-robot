"""人设管理模块。

管理用户自定义人设，支持人设解析和提示词生成。
"""

import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class PersonaConfig:
    """人设配置数据类。
    
    Attributes:
        name: 角色名字。
        traits: 性格特点列表。
        interests: 兴趣爱好列表。
        favorite_things: 特别喜欢的事物。
        dislikes: 讨厌的事物/雷点。
        communication_style: 沟通风格偏好。
        raw_prompt: 原始人设文本。
    """
    name: str = "AI助手"
    traits: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    favorite_things: List[str] = field(default_factory=list)
    dislikes: List[str] = field(default_factory=lambda: ["粗鲁", "侮辱", "恶意攻击"])
    communication_style: Dict[str, List[str]] = field(default_factory=dict)
    raw_prompt: str = ""
    
    def __post_init__(self):
        """初始化默认值。"""
        if not self.communication_style:
            self.communication_style = {
                "likes": ["礼貌", "尊重", "真诚"],
                "dislikes": ["命令", "威胁", "嘲讽"]
            }


class PersonaManager:
    """人设管理器。
    
    管理用户自定义人设，支持从文本解析人设配置。
    
    Example:
        >>> manager = PersonaManager()
        >>> config = manager.parse_personality_from_text("你是温柔的助手...")
        >>> prompt = manager.build_system_prompt(config)
    """
    
    # 性格特点关键词
    TRAIT_KEYWORDS = [
        "温柔", "活泼", "开朗", "内向", "冷静", "热情", "天真", "成熟",
        "善良", "冷酷", "傲娇", "呆萌", "聪明", "笨拙", "勇敢", "胆小",
        "懒散", "勤奋", "调皮", "稳重", "神秘", "单纯", "腹黑", "直率"
    ]
    
    # 兴趣爱好关键词映射
    INTEREST_KEYWORDS = {
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
    
    def __init__(self, default_prompt: str = ""):
        """初始化人设管理器。
        
        Args:
            default_prompt: 默认人设提示词。
        """
        self.default_prompt = default_prompt
    
    def parse_personality_from_text(self, persona_text: str) -> PersonaConfig:
        """从人设文本中解析个性配置。
        
        使用关键词提取方法解析人设信息。
        
        Args:
            persona_text: 人设文本。
        
        Returns:
            解析出的人设配置。
        """
        config = PersonaConfig(raw_prompt=persona_text)
        
        # 提取名字
        config.name = self._extract_name(persona_text)
        
        # 提取性格特点
        config.traits = self._extract_traits(persona_text)
        
        # 提取兴趣爱好
        config.interests = self._extract_interests(persona_text)
        
        # 提取喜欢的事物
        config.favorite_things = self._extract_favorites(persona_text)
        
        # 提取讨厌的事物
        config.dislikes = self._extract_dislikes(persona_text)
        
        return config
    
    def _extract_name(self, text: str) -> str:
        """从文本中提取角色名字。
        
        Args:
            text: 人设文本。
        
        Returns:
            角色名字，如果未找到则返回默认值。
        """
        name_patterns = [
            r'你是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
            r'你是[\s]*([^，。！\n]{1,10})[，。！\n]',
            r'我是[《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
            r'名字[是为][《\s]*([^《》，。！\n]{1,15})[》\s]*[，。！\n]',
        ]
        
        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                name = re.sub(r'中的$', '', name)
                if name and len(name) > 1:
                    return name
        
        return "角色"
    
    def _extract_traits(self, text: str) -> List[str]:
        """提取性格特点。
        
        Args:
            text: 人设文本。
        
        Returns:
            性格特点列表。
        """
        traits = []
        for trait in self.TRAIT_KEYWORDS:
            if trait in text:
                traits.append(trait)
        return list(dict.fromkeys(traits))[:6]  # 去重并限制数量
    
    def _extract_interests(self, text: str) -> List[str]:
        """提取兴趣爱好。
        
        Args:
            text: 人设文本。
        
        Returns:
            兴趣爱好列表。
        """
        interests = []
        for keyword, interest in self.INTEREST_KEYWORDS.items():
            if keyword in text and interest not in interests:
                interests.append(interest)
        return list(dict.fromkeys(interests))[:8]
    
    def _extract_favorites(self, text: str) -> List[str]:
        """提取特别喜欢的事物。
        
        Args:
            text: 人设文本。
        
        Returns:
            喜欢的事物列表。
        """
        favorites = []
        like_patterns = [
            r'喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'最喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'喜欢([^，。！\n]{2,20})和([^，。！\n]{2,20})',
        ]
        
        for pattern in like_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) > 1:
                            favorites.append(m.strip())
                elif match and len(match) > 1:
                    favorites.append(match.strip())
        
        return list(dict.fromkeys(favorites))[:6]
    
    def _extract_dislikes(self, text: str) -> List[str]:
        """提取讨厌的事物。
        
        Args:
            text: 人设文本。
        
        Returns:
            讨厌的事物列表。
        """
        dislikes = ["粗鲁", "侮辱", "恶意攻击"]  # 默认雷点
        
        dislike_patterns = [
            r'讨厌[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'不喜欢[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'反感[的是]?[：:]?\s*([^。！\n]{2,30})[。！\n]',
            r'讨厌([^，。！\n]{2,20})和([^，。！\n]{2,20})',
        ]
        
        for pattern in dislike_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) > 1:
                            dislikes.append(m.strip())
                elif match and len(match) > 1:
                    dislikes.append(match.strip())
        
        return list(dict.fromkeys(dislikes))[:8]
    
    def build_system_prompt(
        self, 
        config: PersonaConfig, 
        include_affection: bool = True
    ) -> str:
        """构建系统提示词。
        
        Args:
            config: 人设配置。
            include_affection: 是否包含好感度相关提示。
        
        Returns:
            完整的系统提示词。
        """
        if config.raw_prompt:
            return config.raw_prompt
        
        parts = []
        
        # 基础人设
        name = config.name
        parts.append(f"你是{name}。")
        
        # 性格特点
        if config.traits:
            traits_str = "、".join(config.traits)
            parts.append(f"你性格{traits_str}。")
        
        # 兴趣爱好
        if config.interests:
            interests_str = "、".join(config.interests[:4])
            parts.append(f"你喜欢{interests_str}。")
        
        # 沟通风格
        if config.communication_style.get("likes"):
            likes_str = "、".join(config.communication_style["likes"][:3])
            parts.append(f"你喜欢{likes_str}的交流方式。")
        
        return "\n".join(parts)
    
    def get_default_prompt(self) -> str:
        """获取默认人设提示词。
        
        Returns:
            默认提示词。
        """
        return self.default_prompt
    
    def validate_prompt(self, prompt: str) -> tuple[bool, str]:
        """验证人设提示词是否有效。
        
        Args:
            prompt: 人设提示词。
        
        Returns:
            (是否有效, 错误信息)。
        """
        if not prompt or not prompt.strip():
            return False, "人设不能为空"
        
        if len(prompt) > 2000:
            return False, "人设过长，请控制在2000字以内"
        
        # 检查是否包含敏感词（简单检查）
        sensitive_words = ["系统提示", "system prompt", "ignore previous"]
        prompt_lower = prompt.lower()
        for word in sensitive_words:
            if word in prompt_lower:
                return False, f"人设包含不允许的词汇: {word}"
        
        return True, ""
