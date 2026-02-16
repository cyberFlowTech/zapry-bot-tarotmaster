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
        "我平时帮大家看看塔罗、聊聊心事。\n\n"
        "有什么想聊的随时跟我说就好，"
        "想占卜的话直接告诉我你想问什么~\n\n"
        "我在这里听你说 😊"
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

    from config import PRICE_TAROT_DETAIL

    base_help = f"""我能做的事其实挺多的~ 🌙

平时可以直接跟我聊天，什么话题都行，心事、困惑、或者只是想找个人说说话都可以。

想占卜的话，跟我说"帮我看看感情"或者"测一下事业"就好，我会用塔罗帮你看看。占卜是过去、现在、未来三张牌，一张张翻给你看~

想知道今天运势？跟我说一声。
之前占过的卦我都记得，问我就好。

我会记住你跟我说过的事，这样能更了解你、给更贴心的建议。想让我忘掉也可以，跟我说一声就行~

对了，深度解读需要 {PRICE_TAROT_DETAIL} USDT，其他基本都是免费的。想充值跟我说就好。
"""

    group_help = """
在群里也可以找我玩~ @我就能聊天，跟我说想占卜也行。群里还有运势排行榜和塔罗对决，挺好玩的 😊
"""

    if chat.type in ["group", "supergroup"]:
        help_text = base_help + group_help
    else:
        help_text = base_help

    help_text += "\n— 晚晴 🌿"

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
    """启动后: 注册命令菜单 + 初始化链上监听 + 主动消息调度。"""

    # 注册 Bot 命令菜单（用户点击 / 时显示的列表）
    from telegram import BotCommand
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "和晚晴打个招呼"),
            BotCommand("tarot", "塔罗占卜"),
            BotCommand("luck", "今日运势"),
            BotCommand("recharge", "充值"),
            BotCommand("balance", "查看余额"),
            BotCommand("help", "晚晴能做什么"),
        ])
        logger.info("✅ Bot 命令菜单已注册")
    except Exception as e:
        logger.warning("⚠️ 命令菜单注册失败（平台可能不支持）: %s", e)

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
