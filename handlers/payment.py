"""
充值与余额命令处理器
/recharge - USDT 充值（展示用户专属热钱包地址）
/balance  - 查看余额和用量
/topup    - 管理员手动充值
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.payment import payment_manager
from services.quota import quota_manager
from services.wallet import wallet_manager
from config import (
    HD_MNEMONIC,
    PRICE_TAROT_DETAIL,
    PRICE_TAROT_READING,
    PRICE_AI_CHAT,
    FREE_TAROT_DAILY,
    FREE_CHAT_DAILY,
    ADMIN_USER_IDS,
)
import logging

logger = logging.getLogger(__name__)


# ========== 安全回复 ==========

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


# ========== /recharge 充值命令 ==========

async def recharge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    USDT 充值命令
    /recharge - 展示用户专属充值地址
    """
    user_id = str(update.effective_user.id)

    # 检查 HD 钱包是否已配置
    if not HD_MNEMONIC:
        await _safe_reply(
            update.message,
            "充值功能暂未开放，请联系管理员配置。"
        )
        return

    # 获取或创建用户专属充值钱包
    try:
        wallet = await wallet_manager.get_or_create_wallet(user_id)
    except RuntimeError as e:
        logger.error(f"❌ 钱包创建失败: {e}")
        await _safe_reply(update.message, "充值功能暂时不可用，请稍后再试。")
        return

    deposit_address = wallet["address"]

    # 创建充值订单
    order = await payment_manager.create_recharge_order(user_id, deposit_address)

    # 获取当前余额
    balance = await payment_manager.get_balance(user_id)

    text = (
        f"💎 USDT 充值\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"你的专属充值地址（BSC / BEP-20）：\n\n"
        f"{deposit_address}\n\n"
        f"⚠️ 重要提醒：\n"
        f"• 仅支持 BSC 链（BNB Smart Chain）的 USDT\n"
        f"• 这是你的专属地址，可以多次使用\n"
        f"• 转任意金额，到账后自动充值\n"
        f"• 转账后约 1-3 分钟自动到账\n"
        f"• 请勿向此地址转入其他代币\n\n"
    )

    if balance > 0:
        text += f"💰 当前余额：{balance:.4f} USDT\n\n"

    text += (
        f"转账完成后，我会主动通知你到账~ ✨\n\n"
        f"— Elena 🌿"
    )

    keyboard = [[InlineKeyboardButton("💰 查看余额", callback_data='check_balance')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _safe_reply(update.message, text, reply_markup=reply_markup)
    logger.info(f"💎 充值页面 | 用户: {user_id} | 地址: {deposit_address[:12]}...")


# ========== /balance 余额命令 ==========

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    查看余额和今日用量
    /balance - 显示余额 + 免费额度使用情况
    """
    user_id = str(update.effective_user.id)

    import asyncio
    balance_info, daily_summary = await asyncio.gather(
        payment_manager.get_balance_info(user_id),
        quota_manager.get_daily_summary(user_id)
    )

    balance = balance_info["balance"]
    total_recharged = balance_info["total_recharged"]
    total_spent = balance_info["total_spent"]

    text = (
        f"💰 我的账户\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"💎 当前余额：{balance:.4f} USDT\n\n"
    )

    if total_recharged > 0:
        text += (
            f"📊 历史统计：\n"
            f"  累计充值：{total_recharged:.4f} USDT\n"
            f"  累计消费：{total_spent:.4f} USDT\n\n"
        )

    text += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"📋 今日免费额度\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"🎴 塔罗占卜：{daily_summary['tarot_used']}/{daily_summary['tarot_free_limit']} 次"
        f"（剩余 {daily_summary['tarot_free_remaining']} 次）\n"
        f"💬 AI 对话：{daily_summary['chat_used']}/{daily_summary['chat_free_limit']} 次"
        f"（剩余 {daily_summary['chat_free_remaining']} 次）\n\n"
    )

    text += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"💎 功能价格\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📖 深度解读：{PRICE_TAROT_DETAIL} USDT/次\n"
        f"🎴 塔罗占卜：每天 {FREE_TAROT_DAILY} 次免费，超额 {PRICE_TAROT_READING} USDT/次\n"
        f"💬 AI 对话：每天 {FREE_CHAT_DAILY} 次免费，超额 {PRICE_AI_CHAT} USDT/次\n"
        f"✨ /luck, /fortune, /history 等：免费\n\n"
    )

    text += "使用 /recharge 充值 USDT 解锁更多功能~ 💎\n\n— Elena 🌿"

    keyboard = [[InlineKeyboardButton("💎 去充值", callback_data='go_recharge')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _safe_reply(update.message, text, reply_markup=reply_markup)


# ========== 回调处理 ==========

async def check_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看余额的回调按钮"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = str(query.from_user.id)
    balance = await payment_manager.get_balance(user_id)

    text = f"💰 当前余额：{balance:.4f} USDT\n\n使用 /balance 查看详细用量信息。"
    await context.bot.send_message(chat_id=query.message.chat.id, text=text)


async def go_recharge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """去充值的回调按钮 — 直接展示用户专属充值地址"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = str(query.from_user.id)
    chat_id = query.message.chat.id

    if not HD_MNEMONIC:
        await context.bot.send_message(chat_id=chat_id, text="充值功能暂未开放，请联系管理员。")
        return

    try:
        wallet = await wallet_manager.get_or_create_wallet(user_id)
    except RuntimeError:
        await context.bot.send_message(chat_id=chat_id, text="充值功能暂时不可用，请稍后再试。")
        return

    deposit_address = wallet["address"]
    balance = await payment_manager.get_balance(user_id)

    # 创建充值订单
    await payment_manager.create_recharge_order(user_id, deposit_address)

    text = (
        f"💎 USDT 充值\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"你的专属充值地址（BSC / BEP-20）：\n\n"
        f"{deposit_address}\n\n"
        f"⚠️ 重要提醒：\n"
        f"• 仅支持 BSC 链（BNB Smart Chain）的 USDT\n"
        f"• 这是你的专属地址，可以多次使用\n"
        f"• 转任意金额，到账后自动充值\n"
        f"• 转账后约 1-3 分钟自动到账\n\n"
    )

    if balance > 0:
        text += f"💰 当前余额：{balance:.4f} USDT\n\n"

    text += "转账完成后，我会主动通知你~ ✨\n\n— Elena 🌿"

    keyboard = [[InlineKeyboardButton("💰 查看余额", callback_data='check_balance')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


# ========== 管理员命令 ==========

async def topup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    管理员手动充值命令
    /topup <user_id> <amount> - 手动为用户充值
    """
    admin_id = str(update.effective_user.id)

    if admin_id not in ADMIN_USER_IDS:
        await _safe_reply(update.message, "你没有权限执行此操作。")
        return

    if not context.args or len(context.args) < 2:
        await _safe_reply(
            update.message,
            "用法：/topup <用户ID> <金额>\n\n例如：/topup 548348 10"
        )
        return

    try:
        target_user_id = context.args[0]
        amount = float(context.args[1])
        if amount <= 0:
            await _safe_reply(update.message, "金额必须大于 0。")
            return
    except ValueError:
        await _safe_reply(update.message, "参数格式错误。用法：/topup <用户ID> <金额>")
        return

    new_balance = await payment_manager.add_balance(target_user_id, amount, tx_hash="manual_topup")

    text = (
        f"✅ 手动充值成功\n\n"
        f"用户 ID：{target_user_id}\n"
        f"充值金额：{amount} USDT\n"
        f"当前余额：{new_balance:.4f} USDT"
    )
    await _safe_reply(update.message, text)
    logger.info(f"🔧 管理员手动充值 | 管理员: {admin_id} | 用户: {target_user_id} | 金额: {amount}")
