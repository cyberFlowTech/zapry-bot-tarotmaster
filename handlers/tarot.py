"""
渐进式塔罗占卜系统
固定3张牌阵：过去 → 现在 → 未来
整合群组排行榜、今日运势等功能
文案由产品经理专业打磨
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import asyncio
import datetime
from services.tarot_data import TarotDeck
from services.group_manager import group_manager
from services.tarot_history import tarot_history_manager
from services.quota import quota_manager
from utils.zapry_compat import clean_markdown
from config import PRICE_TAROT_READING, PRICE_TAROT_DETAIL

tarot_deck = TarotDeck()

import logging
_tarot_logger = logging.getLogger(__name__)


async def _safe_reply(message, text: str, reply_markup=None):
    """安全引用回复，Zapry 不支持时自动降级"""
    try:
        return await message.reply_text(
            text,
            reply_to_message_id=message.message_id,
            reply_markup=reply_markup
        )
    except Exception:
        return await message.reply_text(text, reply_markup=reply_markup)


def _clean_text_for_zapry(text: str) -> str:
    """清理文本中的 Markdown 标记（Zapry 不支持 Markdown）"""
    return clean_markdown(text)


async def _save_tarot_reading_to_history(user_id: str, question: str, spread: list, interpretation: str):
    """
    保存塔罗占卜记录到 SQLite
    供后续 AI 对话时参考
    """
    cards = [
        {
            'position': pos,
            'card': spread[i]['name_full'],
            'meaning': spread[i].get('meaning', '')
        }
        for i, pos in enumerate(['过去', '现在', '未来'])
    ]
    
    await tarot_history_manager.save_reading(
        user_id=user_id,
        question=question,
        cards=cards,
        interpretation=interpretation,
    )


async def tarot_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    查看塔罗占卜历史（从 SQLite 加载）
    """
    user_id = str(update.effective_user.id)
    readings = await tarot_history_manager.get_recent_readings(user_id, limit=10)
    
    if not readings:
        text = _clean_text_for_zapry(
            "你还没有占卜记录呢。\n\n"
            "想开始的话，使用：\n"
            "/tarot 你的问题\n\n"
            "— Elena 🌿"
        )
        await _safe_reply(update.message, text)
        return
    
    # 构建历史记录展示（最新的在前）
    history_text = "🎴 你的塔罗占卜历史\n"
    history_text += "━━━━━━━━━━━━━━━━━\n\n"
    
    for i, reading in enumerate(reversed(readings), 1):
        history_text += f"【{len(readings) - i + 1}】{reading['timestamp']}\n"
        history_text += f"💭 {reading['question']}\n\n"
        history_text += "牌面：\n"
        for card_info in reading['cards']:
            history_text += f"  {card_info['position']}: {card_info['card']}\n"
        history_text += "\n"
        if i < len(readings):
            history_text += "━━━━━━━━━━━━━━━━━\n\n"
    
    total = await tarot_history_manager.get_reading_count(user_id)
    history_text += f"共 {total} 次占卜\n\n"
    history_text += "💡 提示：和我聊天时，我可以参考这些占卜结果，给你更连贯的建议。\n\n"
    history_text += "— Elena 🌿"
    
    text = _clean_text_for_zapry(history_text)
    
    await _safe_reply(update.message, text)


def _generate_position_advice(position: str, card: dict, orientation: str) -> str:
    """根据牌位、牌面和正逆位生成具体的行动建议"""
    card_name = card['name'].split('(')[0].strip()
    is_upright = "正位" in orientation
    
    # 根据位置生成框架性建议
    if position == "过去":
        if is_upright:
            return f"{card_name}在过去位显示，这段经历为你奠定了良好基础。回顾这些积累，它们是你当下的优势。别忘记这份初心和经验。"
        else:
            return f"{card_name}逆位提醒，过去某些未解决的问题可能在影响现状。不必沉湎于过往，但要从中吸取教训，避免重蹈覆辙。"
    
    elif position == "现在":
        if is_upright:
            return f"{card_name}正位代表你当前状态良好。这是把握机会的时刻，相信自己的判断，积极行动，顺势而为。"
        else:
            return f"{card_name}逆位显示当前遇到阻碍。不要硬冲，先停下来审视局面，调整策略或心态，必要时寻求帮助。"
    
    else:  # 未来
        if is_upright:
            return f"{card_name}正位预示前景光明。保持当前方向，耐心前行，你的努力会有好结果。对未来保持信心和期待。"
        else:
            return f"{card_name}逆位警示未来可能的挑战。提前做好准备，留有备选方案，保持灵活。困难是暂时的，关键在于如何应对。"


def _generate_timeline_advice(spread: list) -> str:
    """生成时间线上的行动建议"""
    past_upright = "正位" in spread[0]['orientation']
    present_upright = "正位" in spread[1]['orientation']
    future_upright = "正位" in spread[2]['orientation']
    
    # 短期建议（基于现在牌）
    if present_upright:
        short_term = "✓ 近期(1-2周): 当前势头良好，是推进计划的好时机。把握这段时间，做重要的决定或行动。"
    else:
        short_term = "⚠ 近期(1-2周): 现在不宜冒进，先解决眼前的问题，调整状态，做好准备工作。"
    
    # 中期建议（基于现在→未来的转变）
    if present_upright and future_upright:
        mid_term = "✓ 中期(1-3月): 保持当前策略，稳步推进。好运气会延续，但不要松懈。"
    elif not present_upright and future_upright:
        mid_term = "↗ 中期(1-3月): 局面会好转。现在的努力会有回报，坚持下去，转机即将出现。"
    elif present_upright and not future_upright:
        mid_term = "↘ 中期(1-3月): 可能遇到新挑战。趁现在顺利时多做储备，提前布局应对变化。"
    else:
        mid_term = "⟳ 中期(1-3月): 调整期会持续一段时间。专注于内功修炼，不急于求成。"
    
    # 长期建议（基于整体趋势）
    positive_count = sum(1 for c in spread if "正位" in c['orientation'])
    if positive_count >= 2:
        long_term = "✓ 长期(3月+): 整体趋势向好，值得长期投入。建立系统，着眼未来，布局长远目标。"
    elif positive_count == 1:
        long_term = "→ 长期(3月+): 需要耐心和毅力。成功需要时间积累，保持定力，稳扎稳打。"
    else:
        long_term = "⚡ 长期(3月+): 可能需要重新规划方向。这是转型期，勇于做出改变，别死守旧路。"
    
    return f"{short_term}\n\n{mid_term}\n\n{long_term}"


def _generate_risk_opportunity(spread: list) -> str:
    """分析风险点和机会点"""
    risks = []
    opportunities = []
    
    # 分析每张牌
    for idx, card in enumerate(spread):
        position = ["过去", "现在", "未来"][idx]
        card_name = card['name'].split('(')[0].strip()
        is_upright = "正位" in card['orientation']
        
        if is_upright:
            # 正位 = 机会
            if idx == 0:
                opportunities.append(f"• 过去的{card_name}经验是你的优势资源")
            elif idx == 1:
                opportunities.append(f"• 当前{card_name}的能量支持你采取行动")
            else:
                opportunities.append(f"• 未来{card_name}的趋势值得期待和布局")
        else:
            # 逆位 = 风险
            if idx == 0:
                risks.append(f"• 警惕过去{card_name}的问题再次出现")
            elif idx == 1:
                risks.append(f"• 当前{card_name}逆位是主要挑战点")
            else:
                risks.append(f"• 未来{card_name}需要提前防范")
    
    # 组合风险
    risk_count = sum(1 for c in spread if "逆位" in c['orientation'])
    if risk_count == 0:
        risks.append("• 整体风险较低，主要是别掉以轻心")
    elif risk_count == 3:
        risks.append("• 全逆位警示：可能需要暂停，重新评估整个计划")
    
    # 组合机会
    opp_count = sum(1 for c in spread if "正位" in c['orientation'])
    if opp_count == 3:
        opportunities.append("• 天时地利人和，这是难得的完美时机")
    
    risk_text = "\n".join(risks) if risks else "• 暂无明显风险"
    opp_text = "\n".join(opportunities) if opportunities else "• 需要创造机会"
    
    return f"🚨 需要注意:\n{risk_text}\n\n✨ 可以把握:\n{opp_text}"


async def _send_message(query, context, text, reply_markup=None):
    """发送消息的兼容函数（Zapry 兼容）"""
    try:
        await query.answer()
    except Exception:
        pass
    
    text = _clean_text_for_zapry(text)
    chat_id = query.message.chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup
    )


async def tarot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    塔罗占卜命令入口
    用法: /tarot 你的问题
    """
    _tarot_logger.info(f"🎴 tarot_command 被调用 user={update.effective_user.id if update.effective_user else 'None'}")
    
    # 无参数 - 显示使用说明
    if not context.args:
        text = _clean_text_for_zapry(
            "嗨，想问塔罗吗？\n\n"
            "直接把问题跟在命令后面就好，像这样：\n"
            "/tarot 你的问题\n\n"
            "比如说：\n"
            "• /tarot 我应该换工作吗\n"
            "• /tarot 这段感情有结果吗\n"
            "• /tarot 现在适合投资吗\n\n"
            "问题越具体，我能给你的指引就越清晰。\n\n"
            "对了，塔罗揭示的是趋势，不是命令。\n"
            "真正的选择权，始终在你自己手中。\n\n"
            "— Elena 🌿"
        )
        await _safe_reply(update.message, text)
        return
    
    # 获取问题
    question = ' '.join(context.args).strip()
    
    # 问题长度验证
    if len(question) < 2:
        await _safe_reply(update.message, "💭 问题有点太简短了呢，能说得再具体一些吗？")
        return
    
    if len(question) > 200:
        await _safe_reply(
            update.message,
            "💭 问题有点太长了，能精简到200字以内吗？\n\n抓住核心的困惑，会更容易看清方向。"
        )
        return
    
    # 配额检查：每日免费次数 + 超额扣费
    user_id = str(update.effective_user.id)
    quota_result = await quota_manager.check_and_deduct("tarot_reading", user_id)
    if not quota_result.allowed:
        await _safe_reply(update.message, _clean_text_for_zapry(quota_result.message))
        return

    # 如果是付费使用，附加提示
    cost_hint = ""
    if not quota_result.is_free:
        cost_hint = f"\n\n💳 本次占卜消耗 {quota_result.cost} USDT，余额 {quota_result.balance:.4f} USDT"
    elif quota_result.remaining_free >= 0:
        cost_hint = f"\n\n🆓 今日免费占卜剩余 {quota_result.remaining_free} 次"

    # 初始化会话 - 准备抽牌
    context.user_data['tarot_question'] = question
    context.user_data['tarot_spread'] = tarot_deck.get_three_card_spread()
    context.user_data['tarot_current_card'] = 0  # 当前显示到第几张（0=还没开始）
    
    # 准备阶段 - 建立仪式感
    keyboard = [[InlineKeyboardButton("🎴 我准备好了", callback_data='reveal_card_1')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = _clean_text_for_zapry(
        f"🔮 收到你的问题\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"现在，闭上眼睛，在心中默念这个问题三次...\n\n"
        f"塔罗之灵会为你揭示：\n"
        f"🎴 过去 - 事情的根源\n"
        f"🎴 现在 - 当前的状态\n"
        f"🎴 未来 - 发展的趋势\n\n"
        f"准备好后，点击下方按钮，我们开始。"
        f"{cost_hint}"
    )
    
    await _safe_reply(update.message, text, reply_markup=reply_markup)


async def reveal_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """渐进式翻牌回调 - reveal_card_1, reveal_card_2, reveal_card_3"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    try:
        await query.answer()
    except Exception:
        pass
    
    try:
        # 解析当前是第几张牌
        card_num = int(query.data.split('_')[-1])
        
        # 获取牌阵和问题
        spread = context.user_data.get('tarot_spread')
        question = context.user_data.get('tarot_question', '未指定问题')
        
        if not spread:
            await context.bot.send_message(
                chat_id=chat_id,
                text="💭 抱歉，我们的连接好像断了。\n\n可以重新输入：\n/tarot 你的问题"
            )
            return
        
        # 显示翻牌动画
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎴 翻牌中..."
        )
        await asyncio.sleep(1)
        
        # 获取当前牌
        card = spread[card_num - 1]
        position_names = ["过去", "现在", "未来"]
        position = position_names[card_num - 1]
        
        # 生成单张牌解读
        card_symbol = "🔸" if "正位" in card['orientation'] else "🔹"
        card_name = card['name'].split('(')[0].strip()
        
        # 根据牌位生成精准文案和解读
        position_meanings = {
            "过去": {
                "intro": "事情的根源",
                "context": "回顾引发当前局面的关键因素",
            },
            "现在": {
                "intro": "当前的状态", 
                "context": "你目前所处的核心处境与挑战",
            },
            "未来": {
                "intro": "发展的趋势",
                "context": "事情可能的走向和你需要准备的",
            }
        }
        
        pos_info = position_meanings[position]
        
        # 获取完整的牌面含义和深度解读
        card_meaning = card['meaning']
        
        # 获取深度含义（更丰富的信息）
        if "正位" in card['orientation']:
            deep_meaning = card.get('deep_meaning_upright', card_meaning)
        else:
            deep_meaning = card.get('deep_meaning_reversed', card_meaning)
        
        # 生成针对此位置的行动建议
        action_advice = _generate_position_advice(position, card, card['orientation'])
        
        text = _clean_text_for_zapry(
            f"🎴 第 {card_num} 张牌 - {position}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{card_symbol} {card['name_full']}\n\n"
            f"📍 位置意义: {pos_info['intro']}\n"
            f"💭 解读方向: {pos_info['context']}\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"🔍 牌面信息:\n"
            f"{deep_meaning}\n\n"
            f"💡 针对【{position}】的建议:\n"
            f"{action_advice}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"进度: {card_num}/3"
        )
        
        # 构建按钮
        keyboard = []
        
        if card_num < 3:
            # 还有牌可以翻
            next_position = position_names[card_num]
            keyboard.append([
                InlineKeyboardButton(
                    f"➡️ 翻开第 {card_num + 1} 张 ({next_position})", 
                    callback_data=f'reveal_card_{card_num + 1}'
                )
            ])
            keyboard.append([
                InlineKeyboardButton("⏸️ 让我想想", callback_data='pause_reading')
            ])
        else:
            # 全部翻完 - 显示总结按钮
            keyboard.append([
                InlineKeyboardButton("📊 查看完整解读", callback_data='show_final_result')
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )
        
        # 更新当前进度
        context.user_data['tarot_current_card'] = card_num
        
    except Exception as e:
        _tarot_logger.error(f"翻牌时出错: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ 翻牌时出现问题，请重新开始：\n/tarot 你的问题"
        )


async def pause_reading_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用户暂停阅读"""
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception:
        pass
    
    current_card = context.user_data.get('tarot_current_card', 0)
    position_names = ["过去", "现在", "未来"]
    next_position = position_names[current_card] if current_card < 3 else "未来"
    
    keyboard = [[
        InlineKeyboardButton(
            f"🎴 继续翻开 ({next_position})", 
            callback_data=f'reveal_card_{current_card + 1}'
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = _clean_text_for_zapry(
        f"⏸️ 已暂停\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"💭 停下来，让刚才那张牌的信息在心中沉淀...\n\n"
        f"想想看:\n"
        f"• 这张牌与你的问题有什么共鸣？\n"
        f"• 它是否点出了某个你忽略的细节？\n"
        f"• 它传递的能量是鼓励还是提醒？\n\n"
        f"准备好后，我们继续翻开下一张牌。"
    )
    
    await _send_message(query, context, text, reply_markup)


async def show_final_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示最终完整结果"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    try:
        await query.answer()
    except Exception:
        pass
    
    # 获取牌阵和问题
    spread = context.user_data.get('tarot_spread')
    question = context.user_data.get('tarot_question', '未指定问题')
    
    if not spread:
        await context.bot.send_message(
            chat_id=chat_id,
            text="💭 抱歉，我们的连接好像断了。\n\n可以重新输入：\n/tarot 你的问题"
        )
        return
    
    # 计算正位牌数量
    positive_count = sum(1 for c in spread if "正位" in c['orientation'])
    
    # 如果在群组，加入排行榜
    chat = query.message.chat
    if chat.type in ['group', 'supergroup']:
        user = query.from_user
        group_manager.add_user_divination(
            str(chat.id),
            str(user.id),
            user.first_name,
            positive_count,
            [c['name_full'] for c in spread]
        )
    
    # 生成完整解读（含星级、关联分析）
    brief_interpretation = tarot_deck.generate_brief_interpretation(spread, question)
    
    # 保存占卜历史到 SQLite（供后续AI对话使用）
    user_id = str(query.from_user.id)
    await _save_tarot_reading_to_history(user_id, question, spread, brief_interpretation)
    
    # 结果页面
    result_text = (
        f"🔮 塔罗占卜结果\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{brief_interpretation}"
    )
    
    # 按钮（深度解读标注价格）
    detail_btn_text = f"📖 查看深度解读 ({PRICE_TAROT_DETAIL} USDT)"
    keyboard = [
        [InlineKeyboardButton(detail_btn_text, callback_data='tarot_detail')],
        [
            InlineKeyboardButton("🔁 再占一次", callback_data='tarot_again'),
            InlineKeyboardButton("🌙 今日运势", callback_data='tarot_luck')
        ],
    ]
    
    # 如果在群组，添加排行榜按钮
    if chat.type in ['group', 'supergroup']:
        keyboard.insert(1, [InlineKeyboardButton("🏆 查看群排行", callback_data='show_ranking')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    result_text = _clean_text_for_zapry(result_text)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=result_text,
        reply_markup=reply_markup
    )


async def tarot_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示深度解读 - 提供更多可操作的建议（付费功能）"""
    query = update.callback_query
    
    spread = context.user_data.get('tarot_spread')
    question = context.user_data.get('tarot_question', '未指定问题')
    
    if not spread:
        await _send_message(
            query, context,
            text="💭 抱歉，我们的连接好像断了。\n\n可以重新开始：\n/tarot 你的问题"
        )
        return
    
    # 付费门槛：深度解读需要扣费
    user_id = str(query.from_user.id)
    quota_result = await quota_manager.check_and_deduct("tarot_detail", user_id)
    if not quota_result.allowed:
        # 余额不足 — 显示充值引导，保留按钮让用户充值后重试
        keyboard = [
            [InlineKeyboardButton("💎 去充值", callback_data='go_recharge')],
            [InlineKeyboardButton("🔁 再占一次", callback_data='tarot_again')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await _send_message(
            query, context,
            text=f"📖 深度解读\n━━━━━━━━━━━━━━━━━\n\n{quota_result.message}",
            reply_markup=reply_markup
        )
        return

    # 扣费成功提示
    cost_line = f"\n\n💳 本次消耗 {quota_result.cost} USDT，余额 {quota_result.balance:.4f} USDT"

    # 生成深度解读
    detailed_interpretation = tarot_deck.generate_spread_interpretation(spread, question)
    
    # 生成时间线建议
    timeline_advice = _generate_timeline_advice(spread)
    
    # 生成风险与机会点
    risk_opportunity = _generate_risk_opportunity(spread)
    
    result_text = (
        f"📖 深度解读\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💭 {question}\n\n"
        f"🎴 牌阵:\n"
        f"过去: {spread[0]['name'].split('(')[0]}({spread[0]['orientation']})\n"
        f"现在: {spread[1]['name'].split('(')[0]}({spread[1]['orientation']})\n"
        f"未来: {spread[2]['name'].split('(')[0]}({spread[2]['orientation']})\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{detailed_interpretation}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⏰ 时间线建议:\n"
        f"{timeline_advice}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ 风险与机会:\n"
        f"{risk_opportunity}"
        f"{cost_line}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔁 开始新占卜", callback_data='tarot_again')],
        [InlineKeyboardButton("🌙 今日运势", callback_data='tarot_luck')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await _send_message(query, context, result_text, reply_markup)


async def tarot_luck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示今日运势"""
    query = update.callback_query
    user = query.from_user
    user_name = user.first_name or "匿名"
    
    luck_reading = tarot_deck.get_simple_reading(user_name)
    
    result_text = (
        f"{luck_reading}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💫 每天只能抽取一次哦，明天再来吧"
    )
    
    keyboard = [[InlineKeyboardButton("🔮 塔罗占卜", callback_data='back_to_tarot')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await _send_message(query, context, result_text, reply_markup)


async def tarot_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """重新开始占卜"""
    query = update.callback_query
    
    # 清理会话数据
    for key in ['tarot_question', 'tarot_spread', 'tarot_current_card']:
        context.user_data.pop(key, None)
    
    text = _clean_text_for_zapry(
        "🔮 开始新的占卜\n"
        "━━━━━━━━━━━━━━━━━\n"
        "请输入：/tarot 你的问题\n\n"
        "💡 比如：\n"
        "• /tarot 我应该换工作吗\n"
        "• /tarot 这段感情有结果吗\n\n"
        "有什么困惑，就直接问吧。我在这里听你说。"
    )
    
    await _send_message(query, context, text)


async def back_to_tarot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """从今日运势返回"""
    query = update.callback_query
    
    text = _clean_text_for_zapry(
        "想占卜的话，直接这样输入：\n"
        "/tarot 你的问题\n\n"
        "比如：\n"
        "• /tarot 我应该换工作吗\n"
        "• /tarot 这段感情有结果吗\n\n"
        "有什么困惑，随时找我。\n\n"
        "— Elena 🌿"
    )
    
    await _send_message(query, context, text)


# 群组排行榜回调（复用 group.py 的功能）
async def show_ranking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示群组排行榜"""
    from handlers.group import show_ranking_callback as group_ranking
    await group_ranking(update, context)
