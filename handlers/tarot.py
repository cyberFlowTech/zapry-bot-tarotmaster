"""
渐进式塔罗占卜系统
固定3张牌阵：过去 → 现在 → 未来
整合群组排行榜、今日运势等功能
"""
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.tarot_data import TarotDeck, POSITIONS, POSITION_LABELS, _card_short_name
from services.group_manager import group_manager
from services.tarot_history import tarot_history_manager
from services.quota import quota_manager
from utils.zapry_compat import clean_markdown
from config import PRICE_TAROT_DETAIL

_logger = logging.getLogger(__name__)
_deck = TarotDeck()


# ═══════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════

def _clean(text: str) -> str:
    """清理 Markdown（Zapry 不支持）"""
    return clean_markdown(text)


async def _safe_reply(message, text: str, reply_markup=None):
    """引用回复，Zapry 不支持时自动降级"""
    try:
        return await message.reply_text(
            text, reply_to_message_id=message.message_id, reply_markup=reply_markup
        )
    except Exception:
        return await message.reply_text(text, reply_markup=reply_markup)


async def _send(query, context, text: str, reply_markup=None):
    """回调查询后发送消息（通用）"""
    try:
        await query.answer()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=_clean(text),
        reply_markup=reply_markup,
    )


def _get_spread(context) -> tuple:
    """获取当前牌阵和问题，返回 (spread, question) 或 (None, None)"""
    spread = context.user_data.get("tarot_spread")
    question = context.user_data.get("tarot_question", "未指定问题")
    return (spread, question) if spread else (None, None)


async def _send_session_expired(query, context):
    """牌局中断提示"""
    await _send(query, context, "不好意思，刚才的牌局好像中断了 😅\n\n重新发 /tarot 加上问题，我们再来一次~")


# ═══════════════════════════════════════════════════
# /tarot 命令入口
# ═══════════════════════════════════════════════════

async def tarot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用法: /tarot 你的问题"""
    _logger.info(f"🎴 tarot_command user={update.effective_user.id if update.effective_user else 'None'}")

    if not context.args:
        await _safe_reply(update.message, _clean(
            "想问什么呢？把问题告诉我~ 🔮\n\n"
            "像这样就好：\n"
            "/tarot 我应该换工作吗\n"
            "/tarot 这段感情有结果吗\n"
            "/tarot 现在适合投资吗\n\n"
            "问题越具体，我看得越清楚哦~\n\n"
            "— 晚晴 🌿"
        ))
        return

    question = " ".join(context.args).strip()

    if len(question) < 2:
        await _safe_reply(update.message, "这个问题有点简短呢，能说得再具体一些吗？💭")
        return
    if len(question) > 200:
        await _safe_reply(update.message, "问题太长了呢，试试精简到 200 字以内？\n抓住核心的困惑就好，越聚焦越看得清~ 💭")
        return

    # 配额检查
    user_id = str(update.effective_user.id)
    quota = await quota_manager.check_and_deduct("tarot_reading", user_id)
    if not quota.allowed:
        await _safe_reply(update.message, _clean(quota.message))
        return

    cost_hint = ""
    if not quota.is_free:
        cost_hint = f"\n\n💳 这次占卜用了 {quota.cost} USDT，余额还有 {quota.balance:.2f}"
    elif quota.remaining_free >= 0:
        cost_hint = f"\n\n🆓 今天还剩 {quota.remaining_free} 次免费占卜"

    # 初始化牌局
    context.user_data["tarot_question"] = question
    context.user_data["tarot_spread"] = _deck.get_three_card_spread()
    context.user_data["tarot_current_card"] = 0

    keyboard = [[InlineKeyboardButton("🎴 我准备好了", callback_data="reveal_card_1")]]
    await _safe_reply(update.message, _clean(
        f"🔮 收到你的问题\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"现在，闭上眼睛，在心中默念这个问题三次...\n\n"
        f"塔罗之灵会为你揭示：\n"
        f"🎴 过去 - 事情的根源\n"
        f"🎴 现在 - 当前的状态\n"
        f"🎴 未来 - 发展的趋势\n\n"
        f"准备好后，点击下方按钮，我们开始。{cost_hint}"
    ), reply_markup=InlineKeyboardMarkup(keyboard))


# ═══════════════════════════════════════════════════
# 渐进式翻牌
# ═══════════════════════════════════════════════════

async def reveal_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """翻牌回调: reveal_card_1 / 2 / 3"""
    query = update.callback_query
    chat_id = query.message.chat.id
    try:
        await query.answer()
    except Exception:
        pass

    try:
        card_num = int(query.data.split("_")[-1])
        spread, question = _get_spread(context)
        if not spread:
            await context.bot.send_message(chat_id=chat_id, text="不好意思，牌局中断了 😅\n重新发 /tarot 加上问题再来~")
            return

        await context.bot.send_message(chat_id=chat_id, text="🎴 翻牌中...")
        await asyncio.sleep(1)

        card = spread[card_num - 1]
        position = POSITIONS[card_num - 1]
        pos_info = POSITION_LABELS[position]
        sym = "🔸" if "正位" in card["orientation"] else "🔹"

        text = _clean(
            f"🎴 第 {card_num} 张牌 - {position}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{sym} {card['name_full']}\n\n"
            f"📍 位置意义: {pos_info['intro']}\n"
            f"💭 解读方向: {pos_info['context']}\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"🔍 牌面信息:\n{card['deep_meaning']}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"进度: {card_num}/3"
        )

        keyboard = []
        if card_num < 3:
            next_pos = POSITIONS[card_num]
            keyboard.append([InlineKeyboardButton(f"➡️ 翻开第 {card_num + 1} 张 ({next_pos})", callback_data=f"reveal_card_{card_num + 1}")])
            keyboard.append([InlineKeyboardButton("⏸️ 让我想想", callback_data="pause_reading")])
        else:
            keyboard.append([InlineKeyboardButton("📊 查看完整解读", callback_data="show_final_result")])

        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["tarot_current_card"] = card_num

    except Exception as e:
        _logger.error(f"翻牌时出错: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="翻牌时出了点小状况 😅 重新发 /tarot 加上问题再来~")


# ═══════════════════════════════════════════════════
# 暂停 / 结果 / 详细解读 / 再来 / 运势
# ═══════════════════════════════════════════════════

async def pause_reading_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """暂停阅读"""
    query = update.callback_query
    current = context.user_data.get("tarot_current_card", 0)
    next_pos = POSITIONS[current] if current < 3 else "未来"

    keyboard = [[InlineKeyboardButton(f"🎴 继续翻开 ({next_pos})", callback_data=f"reveal_card_{current + 1}")]]
    await _send(query, context,
        f"⏸️ 已暂停\n━━━━━━━━━━━━━━━━━\n\n"
        f"💭 停下来，让刚才那张牌的信息在心中沉淀...\n\n"
        f"想想看:\n"
        f"• 这张牌与你的问题有什么共鸣？\n"
        f"• 它是否点出了某个你忽略的细节？\n"
        f"• 它传递的能量是鼓励还是提醒？\n\n"
        f"准备好后，我们继续翻开下一张牌。",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_final_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示最终结果"""
    query = update.callback_query
    chat_id = query.message.chat.id
    try:
        await query.answer()
    except Exception:
        pass

    spread, question = _get_spread(context)
    if not spread:
        await _send_session_expired(query, context)
        return

    positive = sum(1 for c in spread if "正位" in c["orientation"])

    # 群组排行
    chat = query.message.chat
    if chat.type in ("group", "supergroup"):
        user = query.from_user
        group_manager.add_user_divination(
            str(chat.id), str(user.id), user.first_name,
            positive, [c["name_full"] for c in spread],
        )

    brief = _deck.generate_brief_interpretation(spread, question)

    # 保存历史
    user_id = str(query.from_user.id)
    cards_for_db = [
        {"position": POSITIONS[i], "card": spread[i]["name_full"], "meaning": spread[i].get("meaning", "")}
        for i in range(3)
    ]
    await tarot_history_manager.save_reading(user_id, question, cards_for_db, brief)

    result_text = _clean(
        f"🔮 塔罗占卜结果\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{brief}"
    )

    keyboard = [
        [InlineKeyboardButton(f"📖 看完整故事线 ({PRICE_TAROT_DETAIL} USDT)", callback_data="tarot_detail")],
        [InlineKeyboardButton("🔁 再占一次", callback_data="tarot_again"),
         InlineKeyboardButton("🌙 今日运势", callback_data="tarot_luck")],
    ]
    if chat.type in ("group", "supergroup"):
        keyboard.insert(1, [InlineKeyboardButton("🏆 查看群排行", callback_data="show_ranking")])

    await context.bot.send_message(chat_id=chat_id, text=result_text, reply_markup=InlineKeyboardMarkup(keyboard))


async def tarot_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """深度解读（付费）"""
    query = update.callback_query
    spread, question = _get_spread(context)
    if not spread:
        await _send_session_expired(query, context)
        return

    # 扣费
    user_id = str(query.from_user.id)
    quota = await quota_manager.check_and_deduct("tarot_detail", user_id)
    if not quota.allowed:
        keyboard = [
            [InlineKeyboardButton("💎 去充值", callback_data="go_recharge")],
            [InlineKeyboardButton("🔁 再占一次", callback_data="tarot_again")],
        ]
        await _send(query, context,
            f"📖 深度解读\n━━━━━━━━━━━━━━━━━\n\n{quota.message}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    cost_line = f"\n\n💳 这次深度解读用了 {quota.cost} USDT，余额还有 {quota.balance:.2f}"

    detailed = _deck.generate_spread_interpretation(spread, question)

    result_text = (
        f"📖 深度解读\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n\n"
        f"🎴 牌阵:\n"
        f"过去: {_card_short_name(spread[0])}({spread[0]['orientation']})\n"
        f"现在: {_card_short_name(spread[1])}({spread[1]['orientation']})\n"
        f"未来: {_card_short_name(spread[2])}({spread[2]['orientation']})\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{detailed}"
        f"{cost_line}"
    )

    keyboard = [
        [InlineKeyboardButton("🔁 开始新占卜", callback_data="tarot_again")],
        [InlineKeyboardButton("🌙 今日运势", callback_data="tarot_luck")],
    ]
    await _send(query, context, result_text, reply_markup=InlineKeyboardMarkup(keyboard))


async def tarot_luck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日运势"""
    query = update.callback_query
    user_name = query.from_user.first_name or "匿名"
    luck = _deck.get_simple_reading(user_name)

    keyboard = [[InlineKeyboardButton("🔮 塔罗占卜", callback_data="back_to_tarot")]]
    await _send(query, context,
        f"{luck}\n━━━━━━━━━━━━━━━━━\n💫 每天只能看一次运势哦，明天再来找我~",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def tarot_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新占卜 / 从运势返回"""
    query = update.callback_query
    for key in ("tarot_question", "tarot_spread", "tarot_current_card"):
        context.user_data.pop(key, None)

    await _send(query, context,
        "好的，开始新的一局~ 🔮\n\n"
        "发 /tarot 加上你的问题就好：\n"
        "• /tarot 我应该换工作吗\n"
        "• /tarot 这段感情有结果吗\n\n"
        "有什么困惑，尽管问~"
    )

# 从今日运势返回 → 复用 tarot_again
back_to_tarot_callback = tarot_again_callback


# ═══════════════════════════════════════════════════
# 历史查询
# ═══════════════════════════════════════════════════

async def tarot_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看塔罗占卜历史"""
    user_id = str(update.effective_user.id)
    readings = await tarot_history_manager.get_recent_readings(user_id, limit=10)

    if not readings:
        await _safe_reply(update.message, _clean(
            "你还没有占卜过呢~\n\n"
            "想试试的话，发 /tarot 加上问题就好 🔮\n\n"
            "— 晚晴 🌿"
        ))
        return

    lines = ["🎴 你的塔罗占卜历史\n━━━━━━━━━━━━━━━━━\n"]
    for i, reading in enumerate(reversed(readings), 1):
        lines.append(f"【{len(readings) - i + 1}】{reading['timestamp']}")
        lines.append(f"💭 {reading['question']}\n")
        lines.append("牌面：")
        for card_info in reading["cards"]:
            lines.append(f"  {card_info['position']}: {card_info['card']}")
        lines.append("")
        if i < len(readings):
            lines.append("━━━━━━━━━━━━━━━━━\n")

    total = await tarot_history_manager.get_reading_count(user_id)
    lines.append(f"一共占了 {total} 次~\n")
    lines.append("聊天的时候我会参考这些记录，给你更连贯的建议 💭\n")
    lines.append("— 晚晴 🌿")

    await _safe_reply(update.message, _clean("\n".join(lines)))


# ═══════════════════════════════════════════════════
# 群组排行榜
# ═══════════════════════════════════════════════════

async def show_ranking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """群组排行榜"""
    from handlers.group import show_ranking_callback as group_ranking
    await group_ranking(update, context)
