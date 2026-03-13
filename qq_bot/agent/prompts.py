"""Prompt 模板定义。

提供意图识别相关的所有 Prompt 模板。
"""

from string import Template


class IntentPrompts:
    """意图识别 Prompt 模板。
    
    集中管理所有与意图识别相关的 Prompt，便于维护和国际化。
    """
    
    # 系统提示模板
    INTENT_CLASSIFICATION_SYSTEM = """你是一个意图识别助手，需要准确判断用户的真实意图。

可选意图类型：
- chat(普通聊天)：日常对话、提问、交流等
- summarize(总结)：要求总结聊天记录，如"总结一下"、"概括今天的聊天"
- set_persona(更改人设)：用户明确要求机器人改变角色/性格/行为模式
- get_persona(查看人设)：询问当前人设是什么
- reset_persona(恢复默认)：恢复默认设置/人设，如"恢复默认"、"重置人设"、"/reset"
- clear_history(清除历史)：清除对话历史，如"清除历史"、"忘掉之前的对话"
- view_history(查看历史)：查看之前的对话记录
- view_affection(查看好感度)：询问好感度、亲密度等
- help(帮助)：请求帮助、使用说明，如"/help"、"帮助"、"怎么用"
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
   - "/reset" 命令
   - 注意：单纯的"重置"、"重新开始"可能指清除历史，需结合上下文判断

3. clear_history(清除历史)判断规则：
   - 用户要求清除/删除/忘记对话历史
   - 典型表达："清除历史"、"删除记录"、"忘掉之前的对话"、"重新开始"
   - 注意：与reset_persona的区别 - 仅清除历史而不恢复默认人设

4. help(帮助)判断规则：
   - 用户请求帮助、使用说明、功能列表
   - 典型表达："/help"、"帮助"、"怎么用"、"你会做什么"、"有什么功能"

5. view_affection(查看好感度)判断规则：
   - 用户询问好感度、亲密度、关系值
   - 典型表达："好感度"、"亲密度"、"/affection"、"我们关系怎么样"

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

用户："/help"
{"intent":"help","confidence":0.95,"parameters":{},"reason":"明确的帮助命令"}

用户："好感度"
{"intent":"view_affection","confidence":0.95,"parameters":{},"reason":"明确的查看好感度请求"}

用户："我是医生"
{"intent":"chat","confidence":0.9,"parameters":{},"reason":"用户自我介绍，非设置人设"}

用户："你觉得医生这个职业怎么样"
{"intent":"chat","confidence":0.9,"parameters":{},"reason":"普通对话，包含角色词汇但不是设置人设"}"""

    # 人设提取系统提示
    PERSONA_EXTRACTION_SYSTEM = """你是一个文本提取助手，需要从用户消息中提取人设描述内容。

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


class IntentKeywords:
    """意图关键词映射。
    
    用于快速意图检查的关键词列表。
    """
    
    # 各意图类型的关键词
    SUMMARIZE = ['总结', '概括', '摘要', '汇总', '整理', '总结一下', '概括一下']
    SET_PERSONA = ['更改人设', '修改人设', '变成', '扮演', '设定为', '人设改成', '人设改为', '设定人设', '新人设']
    GET_PERSONA = ['当前人设', '人设是什么', '查看人设', '现在人设']
    CLEAR_HISTORY = ['清除', '清空', '删除', '忘掉', '忘记', '重置', '清掉']
    VIEW_HISTORY = ['历史', '记录', '查看', '之前', '说过', '聊了']
    VIEW_AFFECTION = ['好感度', '亲密度', '关系值', '友好度', '喜欢度']
    HELP = ['帮助', 'help', '怎么用', '说明', '文档', '指南']
    
    # 命令映射（向后兼容）
    COMMAND_MAP = {
        '/help': ('help', 0.95),
        '/affection': ('view_affection', 0.95),
        '/history': ('view_history', 0.95),
        '/clean': ('clear_history', 0.95),
        '/clear': ('clear_history', 0.95),
        '/reset': ('reset_persona', 0.95),
        '/getprompt': ('get_persona', 0.95),
        '/setprompt': ('set_persona', 0.95),
    }
    
    # 特定组合关键词（优先级更高）
    CLEAR_HISTORY_COMPOUND = ['清除历史', '清空历史', '删除历史', '清除记录', '清空记录', '删除记录']
    
    # 需要排除的前缀（避免误判）
    EXCLUDE_PREFIXES = ['我是', '我叫', '他是', '她是', '你是', '你叫']
