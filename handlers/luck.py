"""
今日能量指数
"""
import random
import datetime
from telegram import Update
from telegram.ext import ContextTypes

_LEVELS = [
    (90, "✨", "今天的能量流动很顺畅！适合尝试新事物，或者推进一直想做的计划。"),
    (75, "🌟", "今天的状态不错，做事会比较顺利。适合主动出击，抓住机会。"),
    (60, "🌿", "平稳的一天。不会有什么大起大落，适合按部就班地完成手头的事。"),
    (40, "🍃", "今天可能会遇到一些小挑战，保持耐心和专注，慢慢就会过去。"),
    (0,  "🌧️", "今天的能量有点低。不如放慢节奏，多照顾自己，给自己一些休息的时间。"),
]


async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日能量指数"""
    user = update.effective_user

    # 用户 ID + 日期做种子，保证每天固定
    random.seed(f"{user.id}-{datetime.date.today().isoformat()}")
    score = random.randint(0, 100)
    random.seed()

    emoji, comment = "🌿", ""
    for threshold, e, c in _LEVELS:
        if score >= threshold:
            emoji, comment = e, c
            break

    text = (
        f"{emoji} {user.first_name}，今天的能量指数\n\n"
        f"📊 指数：{score}/100\n\n"
        f"💭 {comment}\n\n"
        f"记住，数字只是参考，你的心态和行动才是关键。\n\n"
        f"— 晚晴 🌿"
    )

    try:
        await update.message.reply_text(text, reply_to_message_id=update.message.message_id)
    except Exception:
        await update.message.reply_text(text)
