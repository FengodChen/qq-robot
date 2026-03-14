#!/usr/bin/env python3
"""
Bot Agent: 自然语言交互代理

=== METADATA ===
name: bot_agent
desc: 自然语言交互代理，理解用户意图并执行操作
modes: chat(聊天), summary(总结)
intents: 聊天,总结,更改人设,清除历史,查看历史,帮助
=== END ===
"""

import os
import re
import json
import asyncio
import glob
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from deepseek_api import DeepSeekAPI


class IntentType(Enum):
    """用户意图类型"""
    CHAT = "chat"                    # 普通聊天
    SUMMARIZE = "summarize"          # 总结聊天记录
    SET_PERSONA = "set_persona"      # 更改人设
    GET_PERSONA = "get_persona"      # 查看当前人设
    RESET_PERSONA = "reset_persona"  # 恢复默认人设
    CLEAR_HISTORY = "clear_history"  # 清除历史
    VIEW_HISTORY = "view_history"    # 查看历史
    VIEW_AFFECTION = "view_affection"  # 查看好感度
    HELP = "help"                    # 帮助
    UNKNOWN = "unknown"              # 未知


@dataclass
class ModuleMetadata:
    """模块元数据"""
    name: str
    description: str
    functions: List[Dict]
    raw_metadata: str


@dataclass
class IntentResult:
    """意图识别结果"""
    intent: IntentType
    confidence: float
    parameters: Dict[str, Any] = None
    reason: str = ""


class MetadataParser:
    """元数据解析器 - 解析简化的METADATA标识语句块"""
    
    # 简化的统一标识语句块格式
    METADATA_START = "=== METADATA ==="
    METADATA_END = "=== END ==="
    
    def __init__(self, project_root: str = None):
        self.project_root = project_root or os.path.dirname(os.path.abspath(__file__))
        self.modules_metadata: Dict[str, ModuleMetadata] = {}
        self.bot_api_metadata: Optional[ModuleMetadata] = None
        
    def _extract_metadata_block(self, content: str) -> Optional[str]:
        """从文件内容中提取METADATA标识语句块"""
        start_idx = content.find(self.METADATA_START)
        end_idx = content.find(self.METADATA_END)
        
        if start_idx == -1 or end_idx == -1:
            return None
            
        return content[start_idx + len(self.METADATA_START):end_idx].strip()
    
    def _parse_simple_format(self, text: str) -> Dict:
        """解析简化的key: value格式"""
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ': ' in line:
                key, value = line.split(': ', 1)
                result[key.strip()] = value.strip()
        return result
    
    def parse_file(self, filepath: str, module_name: str) -> Optional[ModuleMetadata]:
        """解析单个文件的元数据"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            metadata_block = self._extract_metadata_block(content)
            if not metadata_block:
                return None
            
            # 解析简化的元数据格式
            data = self._parse_simple_format(metadata_block)
            
            return ModuleMetadata(
                name=data.get('name', module_name),
                description=data.get('desc', ''),
                functions=[],  # 简化版不解析详细功能
                raw_metadata=metadata_block
            )
            
        except Exception as e:
            print(f"[!] 解析 {filepath} 元数据失败: {e}")
            return None
    
    def load_all_metadata(self) -> Dict[str, ModuleMetadata]:
        """加载所有模块的元数据"""
        # 加载 bot_api.py
        bot_api_path = os.path.join(self.project_root, 'bot_api.py')
        if os.path.exists(bot_api_path):
            self.bot_api_metadata = self.parse_file(bot_api_path, 'bot_api')
        
        # 加载 robots/ 目录下的所有模块
        robots_dir = os.path.join(self.project_root, 'robots')
        if os.path.exists(robots_dir):
            for py_file in glob.glob(os.path.join(robots_dir, '*.py')):
                name = os.path.basename(py_file)[:-3]
                if name == '__init__':
                    continue
                metadata = self.parse_file(py_file, name)
                if metadata:
                    self.modules_metadata[name] = metadata
        
        # 加载自身
        agent_path = os.path.join(self.project_root, 'bot_agent.py')
        if os.path.exists(agent_path):
            self.modules_metadata['bot_agent'] = self.parse_file(agent_path, 'bot_agent')
        
        return self.modules_metadata
    
    def get_available_modes(self) -> List[str]:
        """获取所有可用的模式名称"""
        # 从已加载的模块中获取（排除bot_agent）
        return [name for name in self.modules_metadata.keys() if name not in ['bot_agent', 'message_store']]
    
    def get_metadata_summary(self) -> str:
        """获取精简的元数据摘要（用于DeepSeek prompt，token友好）"""
        parts = []
        parts.append("模式:")
        for name, metadata in self.modules_metadata.items():
            if name not in ['bot_agent', 'message_store']:
                parts.append(f"- {name}: {metadata.description}")
        return '\n'.join(parts)


class BotAgent:
    """机器人代理 - 处理自然语言交互"""
    
    def __init__(self, api_key: Optional[str] = None, mode_manager=None):
        self.metadata_parser = MetadataParser()
        self.metadata_parser.load_all_metadata()
        
        self.deepseek = DeepSeekAPI(api_key=api_key) if api_key else None
        self.mode_manager = mode_manager  # ModeManager 实例
        
        # 调试模式
        self.debug_mode = getattr(mode_manager, 'debug_mode', False) if mode_manager else False
        if self.debug_mode:
            print("[DEBUG] BotAgent 调试模式已启用")
    
    def _build_intent_prompt(self, message: str) -> Tuple[str, str]:
        """构建意图识别prompt，使用DeepSeek AI判断所有敏感操作"""
        
        system_prompt = """你是一个意图识别助手，需要准确判断用户的真实意图。

可选意图类型：
- chat(普通聊天)：日常对话、提问、交流等
- summarize(总结)：要求总结聊天记录，如"总结一下"、"概括今天的聊天"
- set_persona(更改人设)：用户明确要求机器人改变角色/性格/行为模式
- get_persona(查看人设)：询问当前人设是什么
- reset_persona(恢复默认)：恢复默认人设，如"恢复默认"、"重置人设"
- clear_history(清除历史)：清除对话历史，如"清除历史"、"忘掉之前的对话"
- view_history(查看历史)：查看之前的对话记录
- view_affection(查看好感度)：询问好感度、亲密度等
- help(帮助)：请求帮助、使用说明，如"帮助"、"怎么用"、"你会做什么"
- unknown(未知)：无法判断意图

【重要判断规则】

1. set_persona(更改人设)判断规则：
   - 必须是用户主动要求机器人改变角色/性格/行为模式
   - 典型表达："你扮演xxx"、"更改人设为xxx"、"变成xxx"、"以后你是xxx"、"设定为xxx"
   - 必须是对机器人的直接指令，而非陈述句
   - 【排除】用户自我介绍："我是医生"、"我叫小明" → chat
   - 【排除】描述第三方："他是老师"、"她是姐姐" → chat
   - 【排除】普通对话中包含角色词汇："我觉得医生很辛苦" → chat

2. reset_persona(恢复默认)判断规则：
   - 用户明确要求恢复默认设置/人设
   - 典型表达："恢复默认"、"恢复默认人设"、"重置人设"、"重置为默认"
   - "恢复默认"、"重置人设"等自然语言表达
   - 注意：单纯的"重置"、"重新开始"可能指清除历史，需结合上下文判断

3. clear_history(清除历史)判断规则：
   - 用户要求清除/删除/忘记对话历史
   - 典型表达："清除历史"、"删除记录"、"忘掉之前的对话"、"重新开始"
   - 注意：与reset_persona的区别 - 仅清除历史而不恢复默认人设

4. help(帮助)判断规则：
   - 用户请求帮助、使用说明、功能列表
   - 典型表达："帮助"、"怎么用"、"你会做什么"、"有什么功能"

5. view_affection(查看好感度)判断规则：
   - 用户询问好感度、亲密度、关系值
   - 典型表达："好感度"、"亲密度"、"我们关系怎么样"

6. 置信度说明：
   - 0.9-1.0：非常明确的指令
   - 0.7-0.9：比较明确的意图
   - 0.5-0.7：意图不太明确，可能隐含在对话中
   - <0.5：无法判断，建议作为chat

返回JSON格式：
{
  "intent": "意图类型",
  "confidence": 0.0-1.0,
  "parameters": {},
  "reason": "判断理由"
}

示例：
用户："你扮演一只可爱的猫娘"
{"intent":"set_persona","confidence":0.95,"parameters":{},"reason":"明确的角色扮演指令"}

用户："恢复默认人设"
{"intent":"reset_persona","confidence":0.95,"parameters":{},"reason":"明确的恢复默认指令"}

用户："忘掉我们之前的对话"
{"intent":"clear_history","confidence":0.9,"parameters":{},"reason":"要求清除对话历史"}

用户："帮助"
{"intent":"help","confidence":0.95,"parameters":{},"reason":"明确的帮助请求"}

用户："好感度"
{"intent":"view_affection","confidence":0.95,"parameters":{},"reason":"明确的查看好感度请求"}

用户："我是医生"
{"intent":"chat","confidence":0.9,"parameters":{},"reason":"用户自我介绍，非设置人设"}

用户："你觉得医生这个职业怎么样"
{"intent":"chat","confidence":0.9,"parameters":{},"reason":"普通对话，包含角色词汇但不是设置人设"}"""
        
        return system_prompt, message
    
    def classify_intent(self, message: str) -> IntentResult:
        """使用DeepSeek AI分类用户意图。
        
        所有意图识别都通过LLM完成，不使用关键词匹配。
        """
        # 如果没有配置DeepSeek，默认作为聊天
        if not self.deepseek:
            return IntentResult(
                intent=IntentType.CHAT,
                confidence=0.5,
                reason="无LLM服务，默认作为普通聊天"
            )
        
        # 使用DeepSeek AI进行意图识别
        try:
            system_prompt, user_msg = self._build_intent_prompt(message)
            
            # DEBUG: 输出意图识别的完整 prompt
            if self.debug_mode:
                print("\n" + "=" * 60)
                print("[DEBUG] ===== INTENT CLASSIFICATION PROMPT =====")
                print("=" * 60)
                print(f"\n[SYSTEM PROMPT]:\n{system_prompt}\n")
                print("-" * 40)
                print(f"\n[USER MESSAGE]:\n{user_msg}\n")
                print("=" * 60)
                print("[DEBUG] ===== END INTENT PROMPT =====")
                print("=" * 60 + "\n")
            
            response = self.deepseek.chat(
                user_msg=user_msg,
                system_msg=system_prompt
            )
            
            # 解析JSON响应
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                result = json.loads(json_match.group())
                
                intent_str = result.get('intent', 'unknown')
                try:
                    intent = IntentType(intent_str)
                except ValueError:
                    intent = IntentType.UNKNOWN
                
                confidence = result.get('confidence', 0.5)
                reason = result.get('reason', 'AI判断')
                
                # 置信度阈值判断
                if confidence >= 0.7:
                    # 高置信度
                    return IntentResult(
                        intent=intent,
                        confidence=confidence,
                        parameters=result.get('parameters', {}),
                        reason=f"[AI判断] {reason}"
                    )
                elif confidence >= 0.5:
                    # 中等置信度
                    return IntentResult(
                        intent=intent,
                        confidence=confidence,
                        parameters=result.get('parameters', {}),
                        reason=f"[AI判断-中等置信度] {reason}"
                    )
                else:
                    # 低置信度，默认作为聊天
                    return IntentResult(
                        intent=IntentType.CHAT,
                        confidence=0.5,
                        reason="AI置信度较低，作为普通聊天"
                    )
            
        except Exception as e:
            print(f"[!] DeepSeek意图识别失败: {e}")
        
        # DeepSeek失败时，默认作为聊天处理
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=0.5,
            reason="意图识别失败，作为普通聊天"
        )
    
    async def process_message(self, message: str, context: Dict) -> Tuple[bool, str]:
        """
        处理用户消息，返回(是否已处理, 响应消息)
        
        Args:
            message: 用户消息（已去除@等）
            context: 上下文信息，包含group_id, user_id, message_id等
        """
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        message_id = context.get('message_id', 0)
        
        # 1. 意图识别
        intent_result = self.classify_intent(message)
        print(f"[*] 意图识别: {intent_result.intent.value}, 置信度: {intent_result.confidence:.2f}, 原因: {intent_result.reason}")
        
        # 2. 根据意图执行相应操作
        if intent_result.intent == IntentType.SUMMARIZE:
            return await self._handle_summarize(message, intent_result, context)
        
        elif intent_result.intent == IntentType.SET_PERSONA:
            return await self._handle_set_persona(message, intent_result, context)
        
        elif intent_result.intent == IntentType.GET_PERSONA:
            return await self._handle_get_persona(message, intent_result, context)
        
        elif intent_result.intent == IntentType.RESET_PERSONA:
            return await self._handle_reset(message, intent_result, context)
        
        elif intent_result.intent == IntentType.CLEAR_HISTORY:
            return await self._handle_clear_history(message, intent_result, context)
        
        elif intent_result.intent == IntentType.VIEW_HISTORY:
            return await self._handle_view_history(message, intent_result, context)
        
        elif intent_result.intent == IntentType.VIEW_AFFECTION:
            return await self._handle_view_affection(message, intent_result, context)
        
        elif intent_result.intent == IntentType.HELP:
            return await self._handle_help(message, intent_result, context)
        
        elif intent_result.intent == IntentType.CHAT:
            # 普通聊天，不处理，交给当前模式的机器人
            return False, ""
        
        # 未知意图，也交给当前模式的机器人处理
        return False, ""
    
    async def _handle_summarize(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理总结请求 - 直接执行总结，不切换模式"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        message_id = context.get('message_id', 0)
        
        # 使用自然语言解析时间窗口
        from robots.summary import parse_natural_time_window
        seconds, window_text, error_msg = parse_natural_time_window(message)
        
        # 如果时间范围超出限制，返回错误提示
        if error_msg:
            return True, error_msg
        
        # 直接调用 summary 模块执行总结
        if self.mode_manager and 'summary' in self.mode_manager.modes:
            try:
                # 获取 summary 机器人实例
                summary_module = self.mode_manager.modes['summary']
                summary_robot = summary_module.create_robot(self.mode_manager.config)
                
                # 执行总结
                summary = await summary_robot._generate_and_summarize(
                    group_id=group_id, user_id=user_id if not group_id else None, 
                    seconds=seconds,
                    max_tokens=4000, window_text=window_text
                )
                
                return True, summary
            except Exception as e:
                print(f"[!] 总结执行失败: {e}")
                import traceback
                traceback.print_exc()
                return True, f"总结执行失败: {e}"
        
        return True, "总结功能暂时不可用，请检查配置"
    
    def _extract_persona_text(self, message: str) -> str:
        """
        使用DeepSeek AI从消息中提取人设内容
        返回提取到的人设描述，如果提取失败则返回原消息
        """
        system_prompt = """你是一个文本提取助手，需要从用户消息中提取人设描述内容。

任务：从用户设置人设的指令中提取出纯粹的人设描述部分，去除所有指令性词汇。

【输入示例和输出要求】

输入: "更改人设你是狂热的电竞观众精通LOL比赛"
输出: {"persona_text": "你是狂热的电竞观众精通LOL比赛", "success": true}

输入: "你現在是一名獵鷹粉絲，而且是一位資深cs玩家"
输出: {"persona_text": "你現在是一名獵鷹粉絲，而且是一位資深cs玩家", "success": true}

输入: "变成温柔的大姐姐"
输出: {"persona_text": "温柔的大姐姐", "success": true}

输入: "扮演一只可爱的猫娘"
输出: {"persona_text": "一只可爱的猫娘", "success": true}

输入: "设定为知识渊博的教授"
输出: {"persona_text": "知识渊博的教授", "success": true}

【规则】
1. 去除指令性词汇：更改人设、修改人设、人设改成、人设改为、设定为、设定成、变成、扮演、重置人设、重置为等
2. 保留人设的核心描述内容
3. 如果内容以"你"开头且符合人设语境，保留"你"字
4. 去除前后多余的标点符号和空格
5. 如果提取失败或没有有效内容，返回success: false

只返回JSON格式，不要有任何其他说明。"""
        
        try:
            # DEBUG: 输出人设提取的完整 prompt
            if self.debug_mode:
                print("\n" + "=" * 60)
                print("[DEBUG] ===== PERSONA EXTRACTION PROMPT =====")
                print("=" * 60)
                print(f"\n[SYSTEM PROMPT]:\n{system_prompt}\n")
                print("-" * 40)
                print(f"\n[USER MESSAGE]:\n{message}\n")
                print("=" * 60)
                print("[DEBUG] ===== END PERSONA EXTRACTION PROMPT =====")
                print("=" * 60 + "\n")
            
            response = self.deepseek.chat(
                user_msg=message,
                system_msg=system_prompt
            )
            
            # 解析JSON响应
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                result = json.loads(json_match.group())
                if result.get('success') and result.get('persona_text'):
                    return result.get('persona_text', '').strip()
        except Exception as e:
            print(f"[!] AI提取人设内容失败: {e}")
        
        # 如果AI提取失败，返回原消息（让调用方处理）
        return message
    
    async def _handle_set_persona(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理更改人设 - 直接设置，无需确认"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        # 安全检查：排除自我介绍句式
        if message.startswith('我是') or message.startswith('我叫') or message.startswith('他是') or message.startswith('她是'):
            # 这不是设置人设，而是普通聊天，不处理
            return False, ""
        
        # 使用AI提取人设内容
        persona_text = self._extract_persona_text(message)
        
        if not persona_text or len(persona_text) < 3:
            # 没有提取到有效人设内容，提示用户直接输入
            return True, "请直接告诉我要变成什么人设，比如:\"更改人设成温柔的大姐姐\""
        
        # 安全检查：提取的内容不应该只是人称代词或简单的身份词
        simple_identities = ['姐姐', '哥哥', '妹妹', '弟弟', '妈妈', '爸爸', '老师', '医生', '我', '你', '他', '她']
        if persona_text in simple_identities:
            # 可能是误判，不处理
            print(f"[*] 人设设置被阻止，提取内容过于简单: {persona_text}")
            return False, ""
        
        # 如果当前是chat模式，直接设置人设
        if self.mode_manager:
            # 获取 chat 机器人实例
            if 'chat' in self.mode_manager.modes:
                try:
                    chat_module = self.mode_manager.modes['chat']
                    # 创建或获取 chat 机器人
                    robot = self.mode_manager.get_user_robot(group_id, user_id)
                    if robot and hasattr(robot, 'set_persona_directly'):
                        # 构造一个捕获响应的send_func
                        responses = []
                        async def capture_send(*args):
                            if len(args) > 0:
                                responses.append(args[-1])
                        
                        await robot.set_persona_directly(persona_text, group_id, user_id, capture_send)
                        if responses:
                            return True, responses[0]
                except Exception as e:
                    print(f"[!] 设置人设失败: {e}")
        
        return True, "更改人设功能暂时不可用"
    
    async def _handle_get_persona(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理查看当前人设"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        if self.mode_manager:
            robot = self.mode_manager.get_user_robot(group_id, user_id)
            if robot and hasattr(robot, 'get_current_persona'):
                persona = await robot.get_current_persona(group_id, user_id)
                is_custom = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda: robot.conversation.get_custom_prompt(group_id, user_id) is not None
                )
                
                prefix = "【当前人设】" + ("(自定义)\n" if is_custom else "(默认)\n")
                preview = persona[:150] + "..." if len(persona) > 150 else persona
                return True, prefix + preview
        
        return True, "查看人设功能暂时不可用"
    
    async def _handle_reset(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理恢复默认人设"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        if self.mode_manager:
            robot = self.mode_manager.get_user_robot(group_id, user_id)
            if robot and hasattr(robot, 'conversation'):
                # 清除自定义人设并清空对话上下文
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: (robot.conversation.clear_custom_prompt(group_id, user_id), robot.conversation.clear_context(group_id, user_id))
                )
                # 重置好感度并恢复默认人设配置
                if hasattr(robot, 'affection_manager') and robot.affection_manager:
                    robot.affection_manager.reset_affection(group_id, user_id)
                    # 从默认 system_prompt 解析默认人设
                    default_personality = robot.affection_manager.parse_personality_from_text(
                        robot.config.system_prompt
                    )
                    robot.affection_manager.update_personality(default_personality)
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
                return True, reset_msg
        
        return True, "恢复默认人设功能暂时不可用"
    
    async def _handle_clear_history(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理清除历史"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        if self.mode_manager:
            robot = self.mode_manager.get_user_robot(group_id, user_id)
            if robot and hasattr(robot, 'conversation'):
                robot.conversation.clear_context(group_id, user_id)
                # 重置好感度
                if hasattr(robot, 'affection_manager') and robot.affection_manager:
                    robot.affection_manager.reset_affection(group_id, user_id)
                return True, "已经清除我们的对话历史啦~ 好感度也已重置~"
        
        return True, "清除历史功能暂时不可用"
    
    async def _handle_view_history(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理查看历史"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        if self.mode_manager:
            robot = self.mode_manager.get_user_robot(group_id, user_id)
            if robot and hasattr(robot, 'conversation'):
                context_history = robot.conversation.get_context(group_id, user_id)
                if not context_history:
                    return True, "我们还没有对话历史呢~"
                
                # 格式化历史记录（QQ友好格式）
                history_text = f"【对话历史】共{len(context_history)}条\n"
                history_text += "-" * 20 + "\n"
                for i, msg in enumerate(context_history[-10:], 1):  # 只显示最近10条
                    role = "你" if msg.get("role") == "user" else "我"
                    content = msg.get("content", "")[:25]
                    if len(msg.get("content", "")) > 25:
                        content += "..."
                    history_text += f"{i}.[{role}]{content}\n"
                
                return True, history_text
        
        return True, "查看历史功能暂时不可用"
    
    async def _handle_view_affection(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理查看好感度"""
        group_id = context.get('group_id', 0)
        user_id = context.get('user_id', 0)
        
        if self.mode_manager:
            robot = self.mode_manager.get_user_robot(group_id, user_id)
            if robot and hasattr(robot, 'affection_manager') and robot.affection_manager:
                info = robot.affection_manager.format_affection_info(group_id, user_id)
                
                # 添加如何提升好感度的提示
                hint = robot.affection_manager.get_personality_hint()
                full_response = f"{info}\n\n{hint}\n\n💝 好感度小贴士：\n• 聊我喜欢的话题更容易获得好感哦~\n• 真诚的态度比简单的问候更有效\n• 避免粗鲁或负面的言语"
                return True, full_response
        
        return True, "好感度功能暂时不可用"
    
    async def _handle_help(self, message: str, intent: IntentResult, context: Dict) -> Tuple[bool, str]:
        """处理帮助请求"""
        help_text = """我可以帮你做这些事哦：

【聊天功能】
· 直接和我对话聊天
· "更改人设成xxx" - 修改人设(直接生效)
· "查看人设" - 看当前人设
· "清除历史" - 清除对话记录
· "查看历史" - 看最近对话
· "好感度" - 查看我们的关系值

【总结功能】
· "总结一下" - 总结最近1小时的聊天
· "总结过去30分钟的聊天" - 支持任意时间范围
· "总结2小时内的消息" - 使用自然语言指定时间
· "总结今天的聊天" - 支持多种表达方式

⚠️ 注意：最多只能总结最近3天的聊天记录哦~

【好感度系统】
· 真诚的态度比简单的问候更有效
· 粗鲁或负面的言语会减少好感度
· 好感度会影响我的聊天语气
· 修改/重置人设时好感度会重置

需要帮助就 @我 说出来吧~"""
        return True, help_text


def create_agent(api_key: Optional[str] = None, mode_manager=None) -> BotAgent:
    """创建BotAgent实例的工厂函数"""
    return BotAgent(api_key=api_key, mode_manager=mode_manager)


# 便捷函数
def classify_intent_sync(message: str, api_key: Optional[str] = None) -> Dict:
    """同步方式分类意图（用于测试）"""
    agent = BotAgent(api_key=api_key)
    result = agent.classify_intent(message)
    return {
        'intent': result.intent.value,
        'confidence': result.confidence,
        'parameters': result.parameters,
        'reason': result.reason
    }


if __name__ == "__main__":
    # 测试代码
    import os
    
    print("=" * 60)
    print("Bot Agent 测试 (使用DeepSeek AI判断意图)")
    print("=" * 60)
    
    # 测试元数据解析
    print("\n[1] 测试元数据解析:")
    parser = MetadataParser()
    modules = parser.load_all_metadata()
    print(f"加载了 {len(modules)} 个模块的元数据")
    for name in modules:
        print(f"  - {name}: {modules[name].description[:50]}...")
    
    # 测试意图分类
    print("\n[2] 测试意图分类:")
    print("说明: 设置人设、重置人设、清除历史等敏感操作现在完全由DeepSeek AI判断")
    print("-" * 60)
    
    test_messages = [
        # 总结相关
        ("总结一下今天的聊天", "总结功能"),
        ("概括一下刚才的聊天内容", "总结功能"),
        
        # 人设相关 - 这些将由AI判断
        ("更改人设，变成医生", "设置人设-明确指令"),
        ("你扮演一只可爱的猫娘", "设置人设-扮演"),
        ("以后你是温柔的大姐姐", "设置人设-角色设定"),
        ("我是医生", "普通聊天-自我介绍"),
        ("我觉得医生这个职业很好", "普通聊天-含角色词汇"),
        ("他是老师", "普通聊天-描述第三方"),
        
        # 恢复默认 - 这些将由AI判断
        ("恢复默认人设", "恢复默认-明确指令"),
        ("重置人设", "恢复默认-重置"),
        ("恢复默认", "恢复默认-自然语言"),
        
        # 清除历史 - 这些将由AI判断
        ("清除历史记录", "清除历史-明确指令"),
        ("忘掉我们之前的对话", "清除历史-忘记"),
        ("重新开始吧", "清除历史-重新开始"),
        
        # 查看相关（关键词匹配）
        ("查看历史", "查看历史"),
        ("当前人设是什么", "查看人设"),
        ("好感度", "查看好感度"),
        
        # 普通聊天
        ("你好呀", "普通聊天"),
        ("帮助", "帮助"),
    ]
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    agent = BotAgent(api_key=api_key)
    
    for msg, desc in test_messages:
        result = agent.classify_intent(msg)
        print(f"\n[{desc}]")
        print(f"  消息: '{msg}'")
        print(f"  意图: {result.intent.value} (置信度: {result.confidence:.2f})")
        print(f"  原因: {result.reason}")
