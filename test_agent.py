#!/usr/bin/env python3
"""
Bot Agent 测试脚本
用于测试自然语言理解和意图识别功能
"""

import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot_agent import BotAgent, MetadataParser, IntentType, create_agent


def test_metadata_parsing():
    """测试元数据解析"""
    print("=" * 40)
    print("测试 1: 元数据解析")
    print("=" * 40)
    
    parser = MetadataParser()
    modules = parser.load_all_metadata()
    
    print(f"\n成功加载 {len(modules)} 个模块:\n")
    
    for name, meta in modules.items():
        print(f"【{name}】")
        print(f"  描述: {meta.description[:40]}...")
        print(f"  功能: {len(meta.functions)} 个")
        print()
    
    # 获取可用模式
    modes = parser.get_available_modes()
    print(f"可用模式: {', '.join(modes)}")
    print()


def test_intent_classification():
    """测试意图分类"""
    print("=" * 40)
    print("测试 2: 意图分类")
    print("=" * 40)
    
    # 不使用DeepSeek API，仅测试关键词匹配
    agent = BotAgent(api_key=None)
    
    test_cases = [
        ("总结一下今天的聊天", IntentType.SUMMARIZE),
        ("帮我概括刚才的对话", IntentType.SUMMARIZE),
        ("更改人设成医生", IntentType.SET_PERSONA),
        ("设定新人设", IntentType.SET_PERSONA),
        ("清除历史", IntentType.CLEAR_HISTORY),
        ("清空对话记录", IntentType.CLEAR_HISTORY),
        ("查看历史", IntentType.VIEW_HISTORY),
        ("之前的对话", IntentType.VIEW_HISTORY),
        ("帮助", IntentType.HELP),
        ("怎么用", IntentType.HELP),
        ("你好呀", IntentType.CHAT),
        ("今天天气怎么样", IntentType.CHAT),
        ("讲个笑话", IntentType.CHAT),
    ]
    
    print()
    correct = 0
    print("消息                    识别结果        置信度")
    print("-" * 40)
    for msg, expected in test_cases:
        result = agent.classify_intent(msg)
        status = "OK" if result.intent == expected else "FAIL"
        if result.intent == expected:
            correct += 1
        print(f"{msg:16s} {result.intent.value:12s} {result.confidence:.1f} {status}")
    
    print()
    print(f"准确率: {correct}/{len(test_cases)} ({100*correct/len(test_cases):.0f}%)")
    print()


def test_metadata_summary():
    """测试元数据摘要"""
    print("=" * 40)
    print("测试 3: 元数据摘要")
    print("=" * 40)
    
    parser = MetadataParser()
    parser.load_all_metadata()
    
    summary = parser.get_metadata_summary()
    print("\n元数据摘要:\n")
    print(summary[:500])
    print("...")
    print()


def test_with_deepseek():
    """测试DeepSeek意图识别（需要API密钥）"""
    print("=" * 40)
    print("测试 4: DeepSeek 意图识别")
    print("=" * 40)
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("\n! 未设置 DEEPSEEK_API_KEY，跳过此测试")
        print("  设置: export DEEPSEEK_API_KEY='your-key'")
        return
    
    agent = create_agent(api_key=api_key)
    
    test_messages = [
        "请总结一下今天的聊天记录",
        "你能扮演一个医生吗",
        "忘掉我们之前的对话",
        "我想看看之前的聊天记录",
        "你可以做什么",
    ]
    
    print()
    for msg in test_messages:
        result = agent.classify_intent(msg)
        print(f"'{msg}'")
        print(f"  意图: {result.intent.value}")
        print(f"  置信: {result.confidence:.2f}")
        print(f"  理由: {result.reason}")
        print()


def main():
    """主函数"""
    print("\n" + "=" * 40)
    print("Bot Agent 测试套件")
    print("=" * 40)
    print()
    
    try:
        test_metadata_parsing()
        test_intent_classification()
        test_metadata_summary()
        test_with_deepseek()
        
        print("=" * 40)
        print("所有测试完成!")
        print("=" * 40)
        
    except Exception as e:
        print(f"\nX 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
