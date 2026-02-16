"""
Agent 工具注册

将塔罗占卜、记忆查询、历史查询等功能注册为 AI 可调用的工具，
让晚晴在对话中自主决定何时调用这些工具。

使用 SDK 的 @tool 装饰器自动生成 JSON Schema，
通过 OpenAIToolAdapter 对接 OpenAI function calling。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 SDK Tool 框架
try:
    from zapry_agents_sdk.tools import tool, ToolRegistry
    from zapry_agents_sdk.tools.openai_adapter import OpenAIToolAdapter
    _TOOLS_AVAILABLE = True
except ImportError:
    _TOOLS_AVAILABLE = False
    logger.warning("⚠️ zapry_agents_sdk.tools 未安装，Tool Calling 不可用")


# ========== 工具定义 ==========

if _TOOLS_AVAILABLE:

    @tool
    async def get_tarot_history(user_id: str, limit: int = 5) -> str:
        """查看用户最近的塔罗占卜历史记录。

        Args:
            user_id: 用户ID
            limit: 返回最近几条记录，默认5条
        """
        from services.tarot_history import tarot_history_manager
        readings = await tarot_history_manager.get_recent_readings(user_id, limit=limit)
        if not readings:
            return "该用户还没有占卜记录。"
        result = []
        for r in readings:
            cards_str = ", ".join(f"{c['position']}: {c['card']}" for c in r.get('cards', []))
            result.append(f"[{r['timestamp']}] 问题: {r['question']} | 牌面: {cards_str}")
        return "\n".join(result)


    @tool
    async def get_user_memory(user_id: str) -> str:
        """查看晚晴记住的关于该用户的信息档案。

        Args:
            user_id: 用户ID
        """
        from services.user_memory import user_memory_manager
        memory = await user_memory_manager.get_user_memory(user_id)
        return user_memory_manager.format_memory_for_ai(memory) or "暂无该用户的记忆档案。"


    @tool
    async def get_user_balance(user_id: str) -> str:
        """查看用户的 USDT 余额和今日免费额度使用情况。

        Args:
            user_id: 用户ID
        """
        from services.payment import payment_manager
        from services.quota import quota_manager
        balance = await payment_manager.get_balance(user_id)
        summary = await quota_manager.get_daily_summary(user_id)
        return (
            f"余额: {balance:.4f} USDT | "
            f"今日占卜: 已用{summary['tarot_used']}/免费{summary['tarot_free_limit']} | "
            f"今日对话: 已用{summary['chat_used']}/免费{summary['chat_free_limit']}"
        )


    @tool
    async def get_daily_fortune() -> str:
        """获取今日塔罗能量指引（抽一张牌）。"""
        from services.tarot_data import TarotDeck
        deck = TarotDeck()
        card = deck.draw_card()
        orientation = card['orientation']
        return f"今日能量牌: {card['name_full']} | 含义: {card['meaning']}"


# ========== 注册表 ==========

def build_tool_registry() -> Optional["ToolRegistry"]:
    """构建并返回工具注册表"""
    if not _TOOLS_AVAILABLE:
        return None

    registry = ToolRegistry()
    registry.register(get_tarot_history)
    registry.register(get_user_memory)
    registry.register(get_user_balance)
    registry.register(get_daily_fortune)

    logger.info(f"✅ Tool Calling 已注册 {len(registry)} 个工具")
    return registry


def build_openai_adapter(registry: "ToolRegistry") -> Optional["OpenAIToolAdapter"]:
    """构建 OpenAI Tool Calling 适配器"""
    if not _TOOLS_AVAILABLE or registry is None:
        return None
    return OpenAIToolAdapter(registry)
