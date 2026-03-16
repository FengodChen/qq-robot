"""命令行入口。

提供命令行接口启动机器人。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from qq_bot import create_app
from qq_bot.core.config import BotConfig

# 默认配置文件内容
DEFAULT_CONFIG_CONTENT = '''# QQ Bot + DeepSeek AI 配置文件

version: "2.0"

# ===========================================
# LLM API 配置
# ===========================================
llm:
  provider: deepseek
  api_key: "your-deepseek-api-key"
  model: deepseek-chat
  base_url: "https://api.deepseek.com/v1"
  timeout: 60
  max_retries: 3

# ===========================================
# 火山引擎 Ark API 配置（新闻搜索）
# ===========================================
ark:
  api_key: "your-ark-api-key"
  model: "your-ark-model"
  base_url: "https://ark.cn-beijing.volces.com/api/v3"

# ===========================================
# OneBot 协议配置
# ===========================================
onebot:
  token: "your-qq-bot-token"
  napcat_ws_url: "ws://127.0.0.1:3000/"
  listen_host: "0.0.0.0"
  listen_port: 3001
  reconnect_interval: 5
  heartbeat_interval: 30

# ===========================================
# 存储配置
# ===========================================
storage:
  data_dir: "data"
  message_retention_days: 7
  conversation_max_context: 10
  conversation_max_storage: 100

# ===========================================
# 聊天插件配置
# ===========================================
chat:
  enabled: true
  system_prompt: |
    你是一个友好的AI助手，可以帮助用户解答问题。
  max_input_tokens: 500
  max_output_tokens: 100
  max_prompt_tokens: 500
  group_context_messages: 10
  dynamic_persona_enabled: true
  affection_enabled: true

# ===========================================
# 总结插件配置
# ===========================================
summary:
  enabled: true
  max_tokens: 4000
  default_window: "1h"
  max_window_days: 3

# ===========================================
# 每日定时总结配置
# ===========================================
daily_summary:
  enabled: true
  group_id: 123456789
  hour: 23
  minute: 0

# ===========================================
# 新闻服务配置
# ===========================================
news:
  enabled: true
  probability: 1.0
  cache_hours: 6.0
  system_prompt: "你是一个新闻助手。请使用 web_search 工具搜索今天的最新真实新闻，提供简洁准确的新闻摘要。"
  user_prompt: "请搜索今天（{date}）的最新重要新闻，列出3-5条真实新闻，每条用一句话概括，总字数控制在200字以内。"

# ===========================================
# 插件列表
# ===========================================
plugins:
  - chat
  - summary

# ===========================================
# 调试配置
# ===========================================
debug:
  enabled: false
  log_level: INFO
  save_prompts: false
  save_requests: false

# ===========================================
# 提示词配置
# ===========================================
prompts:
  # 聊天插件提示词
  chat:
    # 好感度等级描述
    level_descriptions:
      死敌: "你们是死敌关系，彼此憎恨"
      憎恨: "你憎恨这个用户"
      厌恶: "你厌恶这个用户"
      反感: "你对这个用户有反感"
      疏离: "你们关系疏离，有距离感"
      陌生: "你们刚刚认识，彼此还不太了解"
      关系起步: "有过几次简单交流，正在互相了解"
      熟悉: "比较了解彼此，关系比较自然"
      友好: "关系不错的朋友，相处融洽"
      亲密: "很亲近的关系，彼此信任"
      至交: "非常重要的关系，如同至交好友"
      灵魂伴侣: "灵魂交融的关系，彼此是唯一的存在"
    
    # 好感度语气描述
    tone_descriptions:
      死敌: "你对用户怀有极致的憎恨，语气冰冷刺骨，每一个字都像淬了毒的刀刃。"
      憎恨: "你对用户怀有强烈的负面情绪，语气极其冷漠、尖锐，充满敌意。"
      厌恶: "你对用户有明显的反感，态度冷淡疏离，不愿多交流。"
      反感: "你对用户有负面印象，态度冷淡客气但保持距离。"
      疏离: "你对用户保持明显的距离感，态度冷淡客气但疏离。"
      陌生: "你对用户完全是陌生人的态度，回答礼貌但极其疏远正式。"
      关系起步: "你对用户保持基本的礼貌友好，但仍然有明显的距离感。",
      熟悉: "你对用户比较放松，会偶尔主动关心，语气较为亲切自然。"
      友好: "你对用户很友善，会使用轻松活泼的语气，经常会开玩笑。"
      亲密: "你对用户非常亲近，语气温柔宠溺，充满关心和依赖。"
      至交: "你对用户毫无保留，语气极其亲密宠溺甚至带点任性。"
      灵魂伴侣: "你对用户的爱意已经超越了世俗的理解，达到了灵魂交融的境界。"
    
    # 聊天要求提示词
    chat_requirements: |
      称呼规则：
      - 请根据系统提供的"当前对话者信息"中的名字和性别来决定如何称呼对方
      
      聊天：
      - 你可视聊天氛围，主动并自然地和对方聊及今天的新闻内容
      - 请你依据你对对方的好感度变更语气
      - 当前内容与你之前的聊天内容保持非重复性
      - 你可以获取到群聊相关记录，其中"与<昵称>对话的分身"代表着是你的分身和<昵称>的聊天记录
      
      输出格式：
      - 你在QQ中对话，因此不要使用MD格式，而是使用适合QQ聊天的格式
      - 请避免长篇大论，控制字数在100字以内
    
    # 帮助菜单文本
    help_text: |
      【小音理的帮助菜单】
      你可以直接对我说：
      
      【人设相关】
      · "更改人设成xxx" - 修改我的人设
      · "查看人设" - 看当前人设
      · "恢复默认" - 恢复默认人设
      
      【对话管理】
      · "清除历史" - 清除对话记录
      · "查看历史" - 看最近对话
      
      【好感度系统】
      · "好感度" - 查看我们的关系值
      
      【总结功能】
      · "总结一下" - 总结最近1小时的聊天
      · "总结今天的聊天" - 支持自然语言表达时间
      
      我会理解你的自然语言指令，直接说出来就好~
    
    # 人设提取系统提示词
    persona_extraction: |
      你是一个人设提取助手。你的任务是从用户的指令中提取纯粹的人设描述，并将其转换为以"你是"开头的角色设定语句。
      
      【任务说明】
      1. 从用户消息中提取人设的核心描述
      2. 将描述转换为以"你是"开头的角色设定语句
      3. 去除所有指令性词汇，保留纯粹的人设内容
      
      【示例】
      
      输入: "你现在的人设是一个可爱的JK少女"
      输出: {"persona_text": "你是一个可爱的JK少女", "success": true}
      
      输入: "更改人设成温柔的大姐姐"
      输出: {"persona_text": "你是一个温柔的大姐姐，说话温柔体贴，会照顾人", "success": true}
      
      输入: "扮演一只傲娇的猫娘"
      输出: {"persona_text": "你是一只傲娇的猫娘，有着猫耳和尾巴，说话带着喵~的口癖", "success": true}
      
      输入: "设定为知识渊博的教授"
      输出: {"persona_text": "你是一位知识渊博的教授，说话严谨专业，喜欢引用经典", "success": true}
      
      输入: "你现在是狂热的电竞观众精通LOL比赛"
      输出: {"persona_text": "你是一位狂热的电竞观众，精通LOL比赛，对赛事和选手如数家珍", "success": true}
      
      【规则】
      1. 必须以"你是"开头
      2. 补充适当的性格/行为描述，使人设更完整（2-3句话）
      3. 去除所有指令性词汇：更改人设、修改人设、设定为、变成、扮演等
      4. 如果提取失败或没有有效内容，返回success: false
      
      只返回JSON格式，不要有任何其他说明。
    
    # 好感度评估系统提示词（聊天插件中使用）
    affection_evaluation: |
      你是一个好感度评估助手。请根据对话上下文评估用户消息对好感度的影响。
      
      【评估规则】
      1. 好感度变化范围: -5 到 +5
      2. 评估标准:
         +5: 极度感动/被深深打动
         +3~+4: 非常愉快/被关心
         +1~+2: 比较愉快/友好
         0: 中性（普通对话）
         -1~-2: 轻微不悦
         -3~-4: 明显不悦
         -5: 极度愤怒/伤心
      
      【输出格式】
      必须只返回纯 JSON 对象，禁止添加任何其他内容：
      - 不要添加 markdown 代码块标记（```）
      - 不要添加任何说明文字
      - 确保 JSON 格式完整且有效
      - reason尽可能简短明了
      
      格式示例：
      {{"change": 2, "reason": "话语很温暖"}}

  # Agent 提示词
  agent:
    # 意图分类系统提示词
    intent_classification: |
      你是一个意图识别助手，需要准确判断用户的真实意图。
      
      可选意图类型：
      - chat(普通聊天)：日常对话、提问、交流等
      - summarize(总结)：要求总结聊天记录，如"总结一下"、"概括今天的聊天"
      - set_persona(更改人设)：用户明确要求机器人改变角色/性格/行为模式
      - get_persona(查看人设)：询问当前人设是什么
      - reset_persona(恢复默认)：恢复默认设置/人设，如"恢复默认"、"重置人设"
      - clear_history(清除历史)：清除对话历史，如"清除历史"、"忘掉之前的对话"
      - view_history(查看历史)：查看之前的对话记录
      - view_affection(查看好感度)：询问好感度、亲密度等
      - help(帮助)：请求帮助、使用说明，如"帮助"、"怎么用"
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
    
    # 人设提取系统提示词（Agent用）
    persona_extraction: |
      你是一个文本提取助手，需要从用户消息中提取人设描述内容。
      
      任务：从用户设置人设的指令中提取出纯粹的人设描述部分，去除所有指令性词汇。
      
      【输入示例和输出要求】
      
      输入: "更改人设你是狂热的电竞观众精通LOL比赛"
      输出: {"persona_text": "你是狂热的电竞观众精通LOL比赛", "success": true}
      
      输入: "设定为知识渊博的教授"
      输出: {"persona_text": "你是一名知识渊博的教授", "success": true}
      
      【规则】
      1. 去除指令性词汇：更改人设、修改人设、人设改成、人设改为、设定为、设定成、变成、扮演、重置人设、重置为等
      2. 保留人设的核心描述内容
      3. 去除前后多余的标点符号和空格
      4. 如果提取失败或没有有效内容，返回success: false
      
      只返回JSON格式，不要有任何其他说明。

  # 好感度系统提示词
  affection:
    # 人设喜好生成提示词
    preference_generation: |
      你是一个角色分析师。请根据给定的人设描述，分析该角色的兴趣爱好、喜欢的事物和讨厌的事物（雷点）。
      
      【任务要求】
      1. interests: 角色的兴趣爱好（3-5个）
      2. favorite_things: 角色特别喜欢的事物（3-5个）
      3. dislikes: 角色讨厌的事物/雷点（3-5个）
      4. personality_summary: 角色性格的一句话摘要
      
      【输出格式】
      只返回 JSON 格式，不要有任何其他说明：
      {
        "interests": ["兴趣1", "兴趣2", "兴趣3"],
        "favorite_things": ["喜欢的事物1", "喜欢的事物2", "喜欢的事物3"],
        "dislikes": ["雷点1", "雷点2", "雷点3"],
        "personality_summary": "角色性格描述"
      }
      
      【示例】
      人设：你是一个可爱的JK少女，喜欢猫咪和甜点，讨厌早起和下雨天。
      输出：
      {
        "interests": ["时尚", "社交媒体", "购物", "美妆"],
        "favorite_things": ["猫咪", "甜点", "自拍", "可爱的东西"],
        "dislikes": ["早起", "下雨天", "考试", "被说教"],
        "personality_summary": "活泼可爱的JK少女，喜欢可爱事物"
      }
    
    # 好感度评估系统提示词
    evaluation: |
      你是一个好感度评估助手。请根据用户的消息和当前人设，评估这次对话对好感度的影响。
      
      【评估规则】
      1. 好感度变化范围: -5 到 +5
      2. 评估标准:
         +5: 极度感动/被深深打动（如：救了角色、深情的告白、极其贴心的行为）
         +3~+4: 非常愉快/被关心（如：聊到特别喜欢的事物、收到礼物、被夸奖）
         +1~+2: 比较愉快/友好（如：礼貌问候、正常交流、轻微关心）
         0: 中性（普通对话，无明显情绪波动）
         -1~-2: 轻微不悦（如：语气冷淡、轻微冒犯、触及轻微雷点）
         -3~-4: 明显不悦（如：触及雷点、语气恶劣、让人不舒服）
         -5: 极度愤怒/伤心（如：严重侮辱、恶意攻击、触及底线）
      
      3. 考虑因素:
         - 是否触及人设的喜好/雷点
         - 用户语气是否友善/恶劣
         - 当前好感度水平（高好感度时更容易获得好感）
         - 对话的情感价值
      
      【输出格式】
      只返回 JSON 格式，不要有任何其他说明：
      {
        "change": 变化值(-5到5),
        "reason": "变化原因（简洁描述）"
      }

  # 总结服务提示词
  summary:
    # 总结指令
    instructions: |
      【总结要求】
      1. 语气要符合你的人设，温柔自然，不要太正式或生硬
      2. 使用适合QQ聊天的格式，不要使用Markdown（如 **粗体**、*斜体*、# 标题等）
      3. 可以使用QQ表情符号（如 ✨、🌟、💕、😊 等）增加亲和力
      4. 分点总结，格式示例：
         💬 主要话题：xxx
         👥 活跃群友：xxx、xxx
         ✨ 有趣内容：xxx
      5. 可以适当加入一些温馨的互动感，比如"大家聊得很开心呢~"
      6. 内容详略得当，自然流畅即可，不需要刻意压缩字数
      
      请用纯文本格式输出总结。

# ===========================================
# 工作线程数
# ===========================================
max_workers: 10
'''


def create_parser() -> argparse.ArgumentParser:
    """创建参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="qq-bot",
        description="QQ Bot - 基于 OneBot 11 协议的 QQ 机器人"
    )
    
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # init 命令
    init_parser = subparsers.add_parser("init", help="初始化配置文件")
    init_parser.add_argument(
        "-o", "--output",
        default="config.yaml",
        help="输出文件路径"
    )
    
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    """初始化配置文件。"""
    output_path = Path(args.output)
    
    if output_path.exists():
        print(f"[!] 配置文件已存在: {output_path}")
        return 1
    
    output_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    print(f"[*] 配置文件已创建: {output_path}")
    print("[*] 请编辑配置文件，填入必要的 API Key 和 Token")
    
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    """运行机器人。"""
    print("=" * 60)
    print("QQ Bot v2.0")
    print("=" * 60)
    
    config_path = Path(args.config)
    
    # 检查配置文件是否存在
    if not config_path.exists():
        print(f"[!] 配置文件不存在: {config_path}")
        print("[*] 正在自动创建默认配置文件...")
        
        config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
        print(f"[*] 配置文件已创建: {config_path}")
        print("=" * 60)
        print("[!] 请先编辑配置文件，填入必要的 API Key 和 Token:")
        print(f"    1. llm.api_key - DeepSeek API Key")
        print(f"    2. ark.api_key - ARK API Key")
        print(f"    3. onebot.token - QQ Bot Token")
        print(f"    4. onebot.napcat_ws_url - NapCat WebSocket 地址")
        print("=" * 60)
        print("[*] 配置完成后重新运行程序")
        return 0
    
    # 加载配置
    try:
        config = BotConfig.from_yaml(args.config)
    except Exception as e:
        print(f"[!] 加载配置失败: {e}")
        return 1
    
    # 覆盖调试模式
    if args.debug:
        config.debug.enabled = True
    
    # 检查必要配置
    if not config.llm.api_key:
        print("[!] 警告: DeepSeek API Key 未设置")
    if not config.onebot.token:
        print("[!] 警告: QQ Bot Token 未设置")
    
    # 创建并运行应用
    app = create_app(config)
    
    try:
        await app.run()
    except KeyboardInterrupt:
        print("\n[*] 正在停止...")
        await app.shutdown()
        print("[*] 已停止")
    
    return 0


async def main_async() -> int:
    """异步主函数。"""
    parser = create_parser()
    args = parser.parse_args()
    
    if args.command == "init":
        return cmd_init(args)
    else:
        return await cmd_run(args)


def main() -> int:
    """主函数。"""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
