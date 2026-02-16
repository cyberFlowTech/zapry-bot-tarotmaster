"""
Fortune Master (运势大师) — 林晚晴 AI 塔罗 Bot

使用 zapry-bot-sdk 构建，支持 Telegram 和 Zapry 双平台。
"""

import logging
import os
import sys

# SDK 路径（开发阶段，SDK 尚未发布到 PyPI）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SDK_CANDIDATES = [
    os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "related-codes", "zapry-bot-sdk-python")),  # 本地开发
    os.path.normpath(os.path.join(_THIS_DIR, "..", "zapry-bot-sdk-python")),  # 服务器部署
]
for _sdk in _SDK_CANDIDATES:
    if os.path.isdir(_sdk) and _sdk not in sys.path:
        sys.path.insert(0, _sdk)
        break

from zapry_bot_sdk import ZapryBot, BotConfig
from zapry_bot_sdk.utils.logger import setup_logging
from zapry_bot_sdk.utils.telegram_compat import ZapryCompat

from telegram import Update
from telegram.ext import ContextTypes

# ── 加载业务配置（SDK 配置之外的部分）──
from config import (
    DEBUG,
    LOG_FILE,
    get_current_config_summary,
)


# ── 初始化日志 ──
sdk_logger = setup_logging(debug=DEBUG, log_file=LOG_FILE)
logger = logging.getLogger(__name__)


# ── 初始化 SDK ──
config = BotConfig.from_env()
bot = ZapryBot(config)
compat = ZapryCompat(is_zapry=config.is_zapry)


# ═══════════════════════════════════════════════════
# 基础命令
# ═══════════════════════════════════════════════════

@bot.command("start")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """欢迎消息。"""
    user = update.effective_user.first_name or "朋友"

    welcome_text = (
        f"你好 {user}，我是晚晴 🌙\n\n"
        "很高兴认识你~\n\n"
        "我是一名塔罗牌解读师，平时帮大家看看牌面、聊聊困惑。\n\n"
        "你可以：\n"
        "• 直接和我聊天，说什么都可以\n"
        "• 发 /tarot 加上问题，我帮你占卜\n"
        "• 发 /help 看看我还能做什么\n\n"
        "塔罗揭示的是趋势，真正做决定的人，始终是你。\n\n"
        "有什么想聊的吗？我在这里听你说~\n\n"
        "— 晚晴 🌿"
    )

    try:
        await update.message.reply_text(
            welcome_text,
            reply_to_message_id=update.message.message_id
        )
    except Exception:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text
            )
        except Exception as e:
            logger.error("❌ start 消息发送失败: %s", e, exc_info=True)


@bot.command("help")
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助信息。"""
    chat = update.effective_chat

    from config import (
        FREE_TAROT_DAILY, FREE_CHAT_DAILY,
        PRICE_TAROT_DETAIL, PRICE_TAROT_READING, PRICE_AI_CHAT,
    )

    base_help = f"""嘿，我来介绍一下我能做的事~ 🌙
━━━━━━━━━━━━━━━━━

💬 和我聊天
━━━━━━━━━━━━━━━━━

直接发消息给我就好，什么都可以聊。
在群里 @我，我也会回复~

/intro - 想更了解我的话
/memory - 看看我记住了你什么
/clear - 清空我们的聊天记录
/forget - 让我忘掉关于你的一切

我会记住你告诉我的事，这样能给你更贴心的建议 💭

━━━━━━━━━━━━━━━━━
🎴 塔罗占卜
━━━━━━━━━━━━━━━━━

/tarot 你的问题 - 正式占卜（一张张翻牌）
/fortune 你的问题 - 快速求个指引
/luck - 看看今天的运势
/history - 翻翻以前的占卜记录

占卜是过去→现在→未来三张牌，
每张牌单独解读，最后有完整的故事线~

试试看：
• /tarot 我应该换工作吗
• /tarot 这段感情有结果吗

━━━━━━━━━━━━━━━━━
💎 关于充值
━━━━━━━━━━━━━━━━━

每天有免费额度：占卜 {FREE_TAROT_DAILY} 次，聊天 {FREE_CHAT_DAILY} 次。
运势、快速求问、历史记录这些都不限~

用完了也没关系，充一点 USDT 就能继续：
• 📖 深度解读 {PRICE_TAROT_DETAIL} USDT
• 🎴 超额占卜 {PRICE_TAROT_READING} USDT
• 💬 超额聊天 {PRICE_AI_CHAT} USDT

/recharge - 充值
/balance - 看看余额
"""

    group_help = """
━━━━━━━━━━━━━━━━━
👥 群里的玩法
━━━━━━━━━━━━━━━━━

/group_fortune - 今天群里的运势
/ranking - 看看谁运势最好
/pk - 和朋友来一场塔罗对决

在群里占卜会自动加入排行榜，
@我也可以直接聊天哦~
"""

    if chat.type in ["group", "supergroup"]:
        help_text = base_help + group_help
    else:
        help_text = base_help + "\n\n把我拉进群组，还有更多好玩的~ 👥"

    help_text += "\n━━━━━━━━━━━━━━━━━\n\n记住，我不替你做决定，只帮你看清选择。\n真正的力量，在你自己手中~\n\n— 晚晴 🌿"

    try:
        await update.message.reply_text(
            help_text,
            reply_to_message_id=update.message.message_id,
        )
    except Exception:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=help_text,
        )


# ═══════════════════════════════════════════════════
# 注册业务模块 Handler
# ═══════════════════════════════════════════════════

def register_handlers():
    """从各业务模块导入并注册 Handler。"""

    # ── AI 对话 ──
    from handlers.chat import (
        handle_private_message,
        handle_group_mention,
        clear_history_command,
        elena_intro_command,
        memory_command,
        forget_command,
        notify_command,
    )
    bot.add_command("intro", elena_intro_command)
    bot.add_command("about", elena_intro_command)
    bot.add_command("clear", clear_history_command)
    bot.add_command("memory", memory_command)
    bot.add_command("forget", forget_command)
    bot.add_command("notify", notify_command)

    # ── 塔罗占卜 ──
    from handlers.tarot import (
        tarot_command,
        reveal_card_callback,
        pause_reading_callback,
        show_final_result_callback,
        tarot_detail_callback,
        tarot_luck_callback,
        tarot_again_callback,
        back_to_tarot_callback,
        show_ranking_callback,
        tarot_history_command,
    )
    bot.add_command("tarot", tarot_command)
    bot.add_command("history", tarot_history_command)
    bot.add_callback_query("^reveal_card_", reveal_card_callback)
    bot.add_callback_query("^pause_reading$", pause_reading_callback)
    bot.add_callback_query("^show_final_result$", show_final_result_callback)
    bot.add_callback_query("^tarot_detail$", tarot_detail_callback)
    bot.add_callback_query("^tarot_luck$", tarot_luck_callback)
    bot.add_callback_query("^tarot_again$", tarot_again_callback)
    bot.add_callback_query("^back_to_tarot$", back_to_tarot_callback)
    bot.add_callback_query("^show_ranking$", show_ranking_callback)

    # ── 群组功能 ──
    from handlers.group import (
        group_daily_fortune_command,
        ranking_command,
        pk_command,
        accept_pk_callback,
        reject_pk_callback,
        my_daily_fortune_callback,
        show_ranking_callback as group_show_ranking_callback,
        my_pk_stats_callback,
    )
    bot.add_command("group_fortune", group_daily_fortune_command)
    bot.add_command("ranking", ranking_command)
    bot.add_command("pk", pk_command)
    bot.add_callback_query("^accept_pk_", accept_pk_callback)
    bot.add_callback_query("^reject_pk_", reject_pk_callback)
    bot.add_callback_query("^my_daily_fortune$", my_daily_fortune_callback)
    bot.add_callback_query("^my_pk_stats$", my_pk_stats_callback)

    # ── 其他功能 ──
    from handlers.fortune import fortune_command
    from handlers.luck import luck_command
    bot.add_command("fortune", fortune_command)
    bot.add_command("luck", luck_command)

    # ── 支付 ──
    from handlers.payment import (
        recharge_command,
        balance_command,
        topup_command,
        check_balance_callback,
        go_recharge_callback,
    )
    bot.add_command("recharge", recharge_command)
    bot.add_command("balance", balance_command)
    bot.add_command("topup", topup_command)
    bot.add_callback_query("^check_balance$", check_balance_callback)
    bot.add_callback_query("^go_recharge$", go_recharge_callback)

    # ── AI 对话 (兜底，放最后) ──
    from telegram.ext import filters
    bot.add_message(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_private_message,
        group=10,
    )
    bot.add_message(
        filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_group_mention,
        group=10,
    )


# ═══════════════════════════════════════════════════
# 生命周期钩子
# ═══════════════════════════════════════════════════

@bot.on_post_init
async def post_init(application):
    """启动后: 初始化链上监听 + 主动消息调度。"""
    from services.chain_monitor import chain_monitor
    chain_monitor.set_bot(application.bot)
    await chain_monitor.start()

    from services.proactive import proactive_scheduler
    proactive_scheduler.set_bot(application.bot)
    await proactive_scheduler.start()


@bot.on_post_shutdown
async def post_shutdown(application):
    """关闭前: 停止后台服务。"""
    from services.chain_monitor import chain_monitor
    await chain_monitor.stop()

    from services.proactive import proactive_scheduler
    await proactive_scheduler.stop()


# ═══════════════════════════════════════════════════
# 错误处理
# ═══════════════════════════════════════════════════

@bot.on_error
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理 — 林晚晴风格。"""
    from telegram.error import NetworkError
    err = context.error
    logger.error("❌ 错误: %s — %s", type(err).__name__, err)

    if isinstance(err, NetworkError) and "provider not found" in str(err):
        logger.warning("Zapry API 返回 provider 错误: %s", err)
    else:
        logger.exception("处理更新时出错: %s", err)

    try:
        if update and hasattr(update, "effective_chat") and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="啊，我刚才走神了一下 😅 能再说一遍吗？\n\n如果一直有问题，过一会儿再找我就好~",
            )
    except Exception as notify_err:
        logger.error("无法发送错误通知: %s", notify_err)


# ═══════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════

def init_database():
    """初始化 SQLite 数据库。"""
    from db.database import db
    db.init_tables()
    from services.chat_history import chat_history_manager
    chat_history_manager.ensure_table()
    logger.info("✅ SQLite 数据库初始化完成")


# ═══════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════

def main():
    logger.info(get_current_config_summary())
    init_database()
    register_handlers()
    bot.run()


if __name__ == "__main__":
    main()
