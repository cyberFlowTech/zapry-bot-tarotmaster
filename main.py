import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

from config import (
    BOT_TOKEN,
    DEBUG,
    HELLO_WORLD_ENABLED,
    HELLO_WORLD_PORT,
    HELLO_WORLD_TEXT,
    LOG_FILE,
    RUNTIME_MODE,
    TELEGRAM_API_BASE_URL,
    TG_PLATFORM,
    WEBAPP_HOST,
    WEBAPP_PORT,
    WEBHOOK_PATH,
    WEBHOOK_SECRET_TOKEN,
    WEBHOOK_URL,
    get_current_config_summary,
)
from utils.private_api_bot import PrivateAPIExtBot, apply_private_api_compatibility

# 在模块加载时应用兼容层（Monkey Patch User.de_json）
# 这确保所有来自 webhook 的 User 对象都会被自动规范化
apply_private_api_compatibility()


def setup_logging() -> logging.Logger:
    """统一初始化日志：终端 + 可选文件。"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_level = logging.DEBUG if DEBUG else logging.INFO

    logging.basicConfig(level=log_level, format=log_format, force=True)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if LOG_FILE:
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        file_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(file_handler)

    logger = logging.getLogger(__name__)
    if LOG_FILE:
        logger.info("日志已写入文件: %s", LOG_FILE)
    return logger


logger = setup_logging()


def start_hello_world_server(port: int, text: str) -> ThreadingHTTPServer:
    """启动最小 HTTP 服务，用于验证公网连通。"""

    class HelloHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), HelloHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def log_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """记录用户对机器人输入的信息（仅日志，不阻断后续处理）"""
    user = update.effective_user
    user_info = f"{user.first_name}(id:{user.id})" if user else "未知用户"
    chat_id = update.effective_chat.id if update.effective_chat else "?"

    if update.message and update.message.text:
        text = update.message.text.strip()
        logger.info("[用户输入] chat_id=%s 用户=%s 内容=%s", chat_id, user_info, text)
    elif update.callback_query:
        data = update.callback_query.data or ""
        logger.info("[用户输入] chat_id=%s 用户=%s 回调=%s", chat_id, user_info, data)
    elif update.inline_query and update.inline_query.query:
        logger.info("[用户输入] 用户=%s 内联查询=%s", user_info, update.inline_query.query.strip())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message."""
    logger.error(f"🚀🚀🚀 start 命令被调用! user={update.effective_user.id if update.effective_user else 'None'}")
    user = update.effective_user.first_name or "朋友"
    
    welcome_text = (
        f"你好 {user}，我是林晚晴。\n\n"
        "很高兴认识你。我是一名塔罗牌解读师，也是你的陪伴者。\n\n"
        "💭 在这里，你可以：\n"
        "• 和我自由聊天，分享你的困惑\n"
        "• 使用 /tarot 进行塔罗占卜\n"
        "• 使用 /intro 更多了解我\n"
        "• 使用 /help 查看所有功能\n\n"
        "我用塔罗这套象征系统，帮你看清内心的状态。\n"
        "但记住，塔罗揭示的是趋势，真正的选择权在你手中。\n\n"
        "有什么想聊的吗？我在这里听你说。\n\n"
        "— Elena 🌿"
    )
    
    try:
        result = await update.message.reply_text(
            welcome_text,
            reply_to_message_id=update.message.message_id
        )
        logger.error(f"✅ start 消息发送成功! message_id={result.message_id}")
    except Exception:
        try:
            result = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text
            )
            logger.error(f"✅ start 消息发送成功(降级)! message_id={result.message_id}")
        except Exception as e:
            logger.error(f"❌ start 消息发送失败: {e}", exc_info=True)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 API 错误，避免刷屏"""
    err = context.error
    logger.error(f"❌ 错误发生! 类型: {type(err).__name__}")
    logger.error(f"   错误内容: {err}")
    
    if isinstance(err, NetworkError) and "provider not found" in str(err):
        logger.warning("私有 API 返回 provider 错误，请检查 mimo.immo 后台配置: %s", err)
    else:
        logger.exception("处理更新时出错: %s", err)
    
    # 尝试通知用户（用林晚晴的口吻）
    try:
        if update and hasattr(update, 'effective_chat') and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="抱歉，我刚才走神了一下。能再说一遍吗？\n\n如果一直有问题，可以过一会儿再试试。"
            )
    except Exception as notify_err:
        logger.error(f"无法发送错误通知: {notify_err}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a help message."""
    chat = update.effective_chat
    
    from config import FREE_TAROT_DAILY, FREE_CHAT_DAILY, PRICE_TAROT_DETAIL, PRICE_TAROT_READING, PRICE_AI_CHAT

    # 基础命令
    base_help = f"""🌙 林晚晴 - 功能列表

━━━━━━━━━━━━━━━━━
💬 对话功能
━━━━━━━━━━━━━━━━━

私聊我，直接聊天即可
在群组中@我，我也会回复

/intro - 了解我是谁
/clear - 清除对话历史
/memory - 查看我记住的关于你的信息
/forget - 清除我的所有记忆

💡 Elena会记住你告诉我的事情，这样能给你更贴心的建议。

━━━━━━━━━━━━━━━━━
🎴 塔罗占卜
━━━━━━━━━━━━━━━━━

/tarot [问题] - 塔罗占卜（渐进式翻牌）
/fortune [问题] - 快速求问
/luck - 今日运势
/history - 查看我的占卜历史

✨ 塔罗特点：
• 逐张翻牌，仪式感满满
• 过去→现在→未来 三张牌阵
• 每张牌单独解读 + 完整故事线
• 深度分析：时间线建议 + 风险机会
• Elena会记住你的占卜结果，在对话中参考

示例：
• /tarot 我应该换工作吗
• /tarot 这段感情有结果吗

━━━━━━━━━━━━━━━━━
💎 充值 & 高级功能
━━━━━━━━━━━━━━━━━

/recharge [金额] - USDT 充值（默认 10 USDT）
/balance - 查看余额和今日用量

📋 免费额度（每日刷新）：
• 塔罗占卜 {FREE_TAROT_DAILY} 次/天
• AI 对话 {FREE_CHAT_DAILY} 次/天
• /luck, /fortune, /history 等不限

💎 高级功能定价：
• 📖 深度解读 {PRICE_TAROT_DETAIL} USDT/次
• 🎴 超额塔罗 {PRICE_TAROT_READING} USDT/次
• 💬 超额对话 {PRICE_AI_CHAT} USDT/次
"""
    
    # 群组功能
    group_help = """
━━━━━━━━━━━━━━━━━
👥 群组功能
━━━━━━━━━━━━━━━━━

/group_fortune - 查看群今日运势
/ranking - 群运势排行榜
/pk - 塔罗对决（回复对手消息）

💡 群组玩法：
• 在群里使用 /tarot 占卜，结果会自动加入排行榜
• 每天看看谁的运势最好
• 和好友PK，比拼牌面能量！
• @我聊天，我也会回复
"""
    
    # 根据是否在群组显示不同内容
    if chat.type in ['group', 'supergroup']:
        help_text = base_help + group_help
    else:
        help_text = base_help + "\n\n💡 将我添加到群组，解锁更多群组互动功能！"
    
    help_text += "\n━━━━━━━━━━━━━━━━━\n\n记住：我不替你做决定，只帮你看清选择。\n真正的力量，在你自己手中。\n\n— Elena 🌿"
    
    try:
        await update.message.reply_text(
            help_text,
            reply_to_message_id=update.message.message_id
        )
    except Exception:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=help_text
        )


def build_application() -> Application:
    if not BOT_TOKEN:
        logger.error("No BOT_TOKEN found! Please set TELEGRAM_BOT_TOKEN in .env file.")
        exit(1)

    if TELEGRAM_API_BASE_URL:
        bot = PrivateAPIExtBot(
            token=BOT_TOKEN,
            base_url=TELEGRAM_API_BASE_URL,
            base_file_url=TELEGRAM_API_BASE_URL.replace("/bot", "/file/bot"),
        )
        builder = ApplicationBuilder().bot(bot)
    else:
        builder = ApplicationBuilder().token(BOT_TOKEN)
    
    # 注册生命周期回调（链上监听等后台服务）
    builder.post_init(post_init)
    builder.post_shutdown(post_shutdown)
    
    application = builder.build()
    
    # 导入塔罗占卜 handlers（渐进式抽牌）
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
        tarot_history_command  # 新增：查看占卜历史
    )
    from handlers.fortune import fortune_command
    from handlers.luck import luck_command
    from handlers.group import (
        group_daily_fortune_command,
        ranking_command,
        pk_command,
        accept_pk_callback,
        reject_pk_callback,
        my_daily_fortune_callback,
        show_ranking_callback,
        my_pk_stats_callback
    )
    # 导入 AI 对话处理器
    from handlers.chat import (
        handle_private_message,
        handle_group_mention,
        clear_history_command,
        elena_intro_command,
        memory_command,        # 新增：查看档案
        forget_command         # 新增：清除档案
    )
    # 导入支付处理器
    from handlers.payment import (
        recharge_command,
        balance_command,
        topup_command,
        check_balance_callback,
        go_recharge_callback,
    )

    application.add_handler(TypeHandler(Update, log_user_input), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # AI 对话相关
    application.add_handler(CommandHandler("intro", elena_intro_command))
    application.add_handler(CommandHandler("about", elena_intro_command))
    application.add_handler(CommandHandler("clear", clear_history_command))
    application.add_handler(CommandHandler("memory", memory_command))  # 新增：查看档案
    application.add_handler(CommandHandler("forget", forget_command))  # 新增：清除档案
    
    # 塔罗占卜相关（渐进式抽牌）
    application.add_handler(CommandHandler("tarot", tarot_command))
    application.add_handler(CommandHandler("history", tarot_history_command))  # 新增：查看占卜历史
    application.add_handler(CallbackQueryHandler(reveal_card_callback, pattern="^reveal_card_"))
    application.add_handler(CallbackQueryHandler(pause_reading_callback, pattern="^pause_reading$"))
    application.add_handler(CallbackQueryHandler(show_final_result_callback, pattern="^show_final_result$"))
    application.add_handler(CallbackQueryHandler(tarot_detail_callback, pattern="^tarot_detail$"))
    application.add_handler(CallbackQueryHandler(tarot_luck_callback, pattern="^tarot_luck$"))
    application.add_handler(CallbackQueryHandler(tarot_again_callback, pattern="^tarot_again$"))
    application.add_handler(CallbackQueryHandler(back_to_tarot_callback, pattern="^back_to_tarot$"))
    application.add_handler(CallbackQueryHandler(show_ranking_callback, pattern="^show_ranking$"))
    
    # 群组功能相关
    application.add_handler(CommandHandler("group_fortune", group_daily_fortune_command))
    application.add_handler(CommandHandler("ranking", ranking_command))
    application.add_handler(CommandHandler("pk", pk_command))
    application.add_handler(CallbackQueryHandler(accept_pk_callback, pattern="^accept_pk_"))
    application.add_handler(CallbackQueryHandler(reject_pk_callback, pattern="^reject_pk_"))
    application.add_handler(CallbackQueryHandler(my_daily_fortune_callback, pattern="^my_daily_fortune$"))
    application.add_handler(CallbackQueryHandler(show_ranking_callback, pattern="^show_ranking$"))
    application.add_handler(CallbackQueryHandler(my_pk_stats_callback, pattern="^my_pk_stats$"))
    
    # 其他功能
    application.add_handler(CommandHandler("fortune", fortune_command))
    application.add_handler(CommandHandler("luck", luck_command))
    
    # 支付相关
    application.add_handler(CommandHandler("recharge", recharge_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("topup", topup_command))  # 管理员手动充值
    application.add_handler(CallbackQueryHandler(check_balance_callback, pattern="^check_balance$"))
    application.add_handler(CallbackQueryHandler(go_recharge_callback, pattern="^go_recharge$"))
    
    # AI 对话处理器（必须放在最后，作为兜底处理）
    # 私聊消息处理
    from telegram.ext import MessageHandler, filters
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_private_message
        ),
        group=10  # 低优先级，让命令先处理
    )
    # 群组@消息处理
    application.add_handler(
        MessageHandler(
            filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_group_mention
        ),
        group=10
    )

    application.add_error_handler(error_handler)
    return application


async def post_init(application: Application) -> None:
    """应用初始化后的回调：启动链上监听等后台服务"""
    from services.chain_monitor import chain_monitor
    chain_monitor.set_bot(application.bot)
    await chain_monitor.start()


async def post_shutdown(application: Application) -> None:
    """应用关闭前的回调：停止后台服务"""
    from services.chain_monitor import chain_monitor
    await chain_monitor.stop()


def run_application(application: Application) -> None:
    should_start_hello = HELLO_WORLD_ENABLED or RUNTIME_MODE == "temporary"
    if should_start_hello:
        try:
            start_hello_world_server(HELLO_WORLD_PORT, HELLO_WORLD_TEXT)
            logger.info("Hello 页面已启动: http://127.0.0.1:%s/", HELLO_WORLD_PORT)
        except OSError as exc:
            logger.warning("Hello 页面启动失败（端口 %s 可能被占用）: %s", HELLO_WORLD_PORT, exc)

    if RUNTIME_MODE == "webhook":
        if not WEBHOOK_URL:
            logger.error("RUNTIME_MODE=webhook 但 WEBHOOK_URL 为空，请在 .env 中配置后重试。")
            exit(1)
        if should_start_hello and HELLO_WORLD_PORT == WEBAPP_PORT:
            logger.warning(
                "HELLO_WORLD_PORT 与 WEBAPP_PORT 相同，Webhook 模式下 hello 页面将启动失败，请使用不同端口。"
            )
        webhook_full_url = WEBHOOK_URL.rstrip("/") + ("/" + WEBHOOK_PATH.strip("/") if WEBHOOK_PATH else "")
        logger.info("Webhook 模式: %s", webhook_full_url)
        print("Fortune Master Bot is starting (Webhook mode)...")
        application.run_webhook(
            listen=WEBAPP_HOST,
            port=WEBAPP_PORT,
            url_path=WEBHOOK_PATH.strip("/") if WEBHOOK_PATH else "",
            webhook_url=webhook_full_url,
            secret_token=WEBHOOK_SECRET_TOKEN or None,
        )
    else:
        print("Fortune Master Bot is starting (Temporary mode: Polling + optional hello page)...")
        application.run_polling()


def init_database() -> None:
    """初始化 SQLite 数据库（建表）"""
    from db.database import db
    db.init_tables()
    # 追加创建 chat_history 表（新增模块）
    from services.chat_history import chat_history_manager
    chat_history_manager.ensure_table()
    logger.info("✅ SQLite 数据库初始化完成")


def main() -> None:
    logger.info(get_current_config_summary())
    init_database()
    application = build_application()
    run_application(application)


if __name__ == "__main__":
    main()
