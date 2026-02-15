import random
from telegram import Update
from telegram.ext import ContextTypes

# Elena's gentle responses
ELENA_RESPONSES = [
    "从能量流动来看，这件事虽然会有些波折，但最终会有转机。保持耐心，相信过程。",
    "现在的时机还不够成熟，不如先观察、积累，等待更好的契机。",
    "我感觉到一些积极的能量在汇聚，如果你准备好了，可以尝试向前迈一步。",
    "趋势显示是正向的，如果你内心已经有答案了，那就跟随你的直觉吧。",
    "今天可能不太适合匆忙决定，给自己一些时间和空间，答案会慢慢清晰。",
    "我看到一些好的可能性，但需要你付出行动。塔罗只是指引，具体怎么做，还是要看你自己。",
    "目前的趋势是开放的，这意味着有很多可能性。试着问自己：什么是你真正想要的？",
    "我感觉你可能会遇到一些贵人或机会，保持开放的心态去接收吧。",
    "有时候，顺其自然反而是最好的选择。别太勉强自己。",
    "你比你想象的更有力量。相信自己的判断，你已经知道答案了。",
]

async def fortune_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """快速求问（林晚晴的简短指引）"""
    user_name = update.effective_user.first_name
    question = ' '.join(context.args)

    if not question:
        text = (
            f"嗨 {user_name}，想问什么呢？\n\n"
            f"直接在命令后面告诉我：\n"
            f"/fortune 你的问题\n\n"
            f"这个是快速求问，我会给你一个简短的指引。\n"
            f"如果想要更详细的解读，可以用 /tarot 哦。\n\n"
            f"— Elena 🌿"
        )
        try:
            await update.message.reply_text(text, reply_to_message_id=update.message.message_id)
        except Exception:
            await update.message.reply_text(text)
        return

    # TODO: Add LLM integration here
    response = random.choice(ELENA_RESPONSES)
    
    text = (
        f"💭 关于「{question}」\n\n"
        f"{response}\n\n"
        f"记住，这只是一个简短的指引。如果想深入了解，建议用 /tarot 占卜。\n\n"
        f"— Elena"
    )
    try:
        await update.message.reply_text(text, reply_to_message_id=update.message.message_id)
    except Exception:
        await update.message.reply_text(text)
