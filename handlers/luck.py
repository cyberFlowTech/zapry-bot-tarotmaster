import random
from telegram import Update
from telegram.ext import ContextTypes
import datetime

async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日能量指数（林晚晴版本）"""
    user = update.effective_user
    # Use user ID and date as seed for consistent daily luck
    today = datetime.date.today().isoformat()
    seed_val = f"{user.id}-{today}"
    random.seed(seed_val)
    
    score = random.randint(0, 100)
    
    # Reset seed to random
    random.seed()

    comment = ""
    emoji = ""
    if score >= 90:
        emoji = "✨"
        comment = "今天的能量流动很顺畅！适合尝试新事物，或者推进一直想做的计划。"
    elif score >= 75:
        emoji = "🌟"
        comment = "今天的状态不错，做事会比较顺利。适合主动出击，抓住机会。"
    elif score >= 60:
        emoji = "🌿"
        comment = "平稳的一天。不会有什么大起大落，适合按部就班地完成手头的事。"
    elif score >= 40:
        emoji = "🍃"
        comment = "今天可能会遇到一些小挑战，保持耐心和专注，慢慢就会过去。"
    else:
        emoji = "🌧️"
        comment = "今天的能量有点低。不如放慢节奏，多照顾自己，给自己一些休息的时间。"

    text = (
        f"{emoji} {user.first_name}，今天的能量指数\n\n"
        f"📊 指数：{score}/100\n\n"
        f"💭 {comment}\n\n"
        f"记住，数字只是参考，你的心态和行动才是关键。\n\n"
        f"— Elena 🌿"
    )
    
    # 引用回复，Zapry 不支持时降级
    try:
        await update.message.reply_text(
            text,
            reply_to_message_id=update.message.message_id
        )
    except Exception:
        await update.message.reply_text(text)
