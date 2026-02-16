"""
林晚晴对话处理器
处理私聊消息和群组@消息
集成长期记忆系统（SQLite 版）
集成自然语言意图识别路由
"""

from telegram import Update
from telegram.ext import ContextTypes
from services.ai_chat import elena_ai
from services.user_memory import user_memory_manager
from services.conversation_buffer import conversation_buffer
from services.memory_extractor import memory_extractor
from services.chat_history import chat_history_manager
from services.tarot_history import tarot_history_manager
from services.intent_router import intent_router
from services.quota import quota_manager
from utils.zapry_compat import clean_markdown
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ========== 用户名获取 ==========

def get_display_name(user) -> str:
    """
    获取用户的最佳显示名称。
    优先级：first_name > username > last_name > "朋友"
    
    Zapry 已修复 first_name（问题1），现在正常返回用户昵称。
    保留多级降级逻辑作为防御性编程。
    """
    # 优先用 first_name（标准 Telegram 字段）
    name = user.first_name or ""
    
    # 如果 first_name 是纯数字（可能是 Zapry 用 ID 补全的），尝试 username
    if name and not name.isdigit():
        return name
    
    # 尝试 username
    if user.username:
        return user.username
    
    # 尝试 last_name
    if user.last_name:
        return user.last_name
    
    # first_name 是数字也比"朋友"好
    if name:
        return name
    
    return "朋友"


# ========== 安全回复：自动引用 + Zapry 降级 ==========

async def safe_reply(message, text: str, quote: bool = True):
    """
    安全发送回复消息，自动引用原消息。
    如果平台不支持 reply_to_message_id（如 Zapry），则自动降级为普通消息。
    
    Args:
        message: update.message 对象
        text: 回复文本
        quote: 是否引用原消息（默认 True）
    """
    if quote:
        try:
            return await message.reply_text(
                text,
                reply_to_message_id=message.message_id
            )
        except Exception as e:
            logger.debug(f"引用回复失败（平台可能不支持），降级为普通回复: {e}")
    
    return await message.reply_text(text)


# ========== 意图路由：自然语言 → 命令执行 ==========

# Elena 风格的过渡话术（让命令触发不那么突兀）
_TRANSITION_MESSAGES = {
    "tarot": "好呀，帮你看看~ 🔮\n\n稍等，我来准备牌...",
    "tarot_history": "让我翻翻你之前的占卜记录~ 📖",
    "memory": "让我想想我记得你什么~ 🤔",
    "forget": None,  # forget 命令自带回复，不需要过渡
    "clear_history": None,  # clear 命令自带回复，不需要过渡
    "luck": "帮你看看今天的运势~ ✨",
    "fortune": None,  # fortune 命令自带回复
    "intro": None,  # intro 命令自带完整回复
    "help": None,  # help 命令自带完整回复
    "recharge": None,  # recharge 命令自带完整回复
    "balance": None,  # balance 命令自带完整回复
}


async def _route_to_command(update: Update, context: ContextTypes.DEFAULT_TYPE, intent_result: dict):
    """
    根据意图识别结果，路由到对应的命令处理函数
    """
    intent = intent_result["intent"]
    query = intent_result.get("query", "")

    logger.info(f"🚀 意图路由 | intent={intent} | query={query[:50]}")

    # 发送过渡话术（如果有），引用用户原消息
    transition = _TRANSITION_MESSAGES.get(intent)
    if transition:
        await safe_reply(update.message, transition)

    # 根据意图调用对应 handler
    if intent == "tarot":
        # 设置 context.args 模拟 /tarot <问题>
        context.args = query.split() if query else []
        from handlers.tarot import tarot_command
        await tarot_command(update, context)

    elif intent == "tarot_history":
        from handlers.tarot import tarot_history_command
        await tarot_history_command(update, context)

    elif intent == "memory":
        await memory_command(update, context)

    elif intent == "forget":
        await forget_command(update, context)

    elif intent == "clear_history":
        await clear_history_command(update, context)

    elif intent == "luck":
        from handlers.luck import luck_command
        await luck_command(update, context)

    elif intent == "fortune":
        # fortune 需要问题参数，类似 tarot
        context.args = query.split() if query else []
        from handlers.fortune import fortune_command
        await fortune_command(update, context)

    elif intent == "intro":
        await elena_intro_command(update, context)

    elif intent == "help":
        from main import help_command
        await help_command(update, context)

    elif intent == "recharge":
        from handlers.payment import recharge_command
        await recharge_command(update, context)

    elif intent == "balance":
        from handlers.payment import balance_command
        await balance_command(update, context)

    else:
        logger.warning(f"⚠️ 未处理的意图: {intent}")


# ========== 后台任务 ==========

async def _post_reply_tasks(user_id: str, user_message: str, reply: str, user_memory: dict):
    """
    回复用户之后的后台任务（持久化 + 记忆提取 + 反馈检测）
    完全异步执行，不影响用户体验
    """
    try:
        import asyncio
        # 并行写入：对话历史 + 缓冲区
        await asyncio.gather(
            chat_history_manager.add_message(user_id, "user", user_message),
            chat_history_manager.add_message(user_id, "assistant", reply),
            conversation_buffer.add_message(user_id, "assistant", reply),
        )

        # 检查是否需要记忆提取
        if await conversation_buffer.should_extract(user_id):
            logger.info(f"🧠 触发记忆提取 | 用户: {user_id}")
            pending = await conversation_buffer.get_and_clear(user_id)
            if pending:
                extracted_info = await memory_extractor.extract_from_conversations(
                    pending, user_memory
                )
                if extracted_info:
                    await user_memory_manager.update_user_memory(user_id, extracted_info)
                    logger.info(f"✅ 用户档案已更新 | 用户: {user_id}")

        # 自我反思：检测用户反馈信号并调整偏好
        await _detect_and_adapt(user_id, user_message, user_memory)

    except Exception as e:
        logger.error(f"❌ 后台任务失败: {e}", exc_info=True)


# ========== 自我反思：反馈检测 ==========

# 反馈信号 → 偏好调整映射
_FEEDBACK_PATTERNS = {
    "style": {
        "concise": ["太长了", "啰嗦", "简短点", "说重点", "太多了", "精简", "简洁"],
        "detailed": ["详细说说", "展开讲讲", "多说一些", "说详细点", "具体讲讲"],
    },
    "tone": {
        "casual": ["说人话", "白话", "通俗点", "别那么正式", "轻松一点"],
        "classical": ["专业一些", "正式一些", "文雅一些"],
    },
}

async def _detect_and_adapt(user_id: str, user_message: str, user_memory: dict):
    """检测用户反馈信号，自动调整偏好"""
    msg = user_message.strip()
    if len(msg) > 50:
        return  # 长消息不太可能是反馈

    preferences = user_memory.get("preferences", {})
    changed = False

    for pref_key, patterns in _FEEDBACK_PATTERNS.items():
        for value, keywords in patterns.items():
            for kw in keywords:
                if kw in msg:
                    old_val = preferences.get(pref_key, "balanced")
                    if old_val != value:
                        preferences[pref_key] = value
                        preferences["updated_at"] = datetime.now().isoformat()
                        changed = True
                        logger.info(
                            f"🔄 偏好调整 | 用户: {user_id} | "
                            f"{pref_key}: {old_val} → {value} | 触发词: {kw}"
                        )
                    break

    if changed:
        user_memory["preferences"] = preferences
        await user_memory_manager.update_user_memory(user_id, {"preferences": preferences})


# ========== 消息处理 ==========


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理私聊消息
    林晚晴会回复所有私聊消息（除了命令）
    集成长期记忆系统
    """
    
    # 如果是命令，不处理（让命令处理器处理）
    if update.message.text and update.message.text.startswith('/'):
        return
    
    user = update.effective_user
    user_id = str(user.id)
    user_name = get_display_name(user)
    user_message = update.message.text or ""
    
    logger.info(f"💬 收到私聊 | 用户: {user_name} ({user_id}) | 内容: {user_message[:50]}")
    
    # 如果消息为空（可能是图片等），友好提示
    if not user_message.strip():
        await safe_reply(
            update.message,
            "我看到你发了东西，不过我暂时只能看懂文字呢~ 😊\n\n"
            "想占卜的话发 /tarot 加上问题，\n"
            "想聊天直接打字就好~"
        )
        return
    
    # 发送"正在输入"状态（Zapry 不支持，跳过）
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.debug(f"发送 typing 状态失败（平台可能不支持）: {e}")
    
    # === 意图识别 + 数据预加载（并行执行） ===
    # 意图识别和数据库查询同时进行，大幅减少总延迟
    intent_task = asyncio.create_task(intent_router.detect(user_message))
    memory_task = asyncio.create_task(user_memory_manager.get_user_memory(user_id))
    history_task = asyncio.create_task(chat_history_manager.get_history(user_id, limit=40))
    tarot_task = asyncio.create_task(tarot_history_manager.get_recent_readings(user_id, limit=5))
    
    # 等待意图识别先完成（如果是命令意图，可以提前返回，不必等数据加载）
    try:
        intent_result = await intent_task
        if intent_result["intent"] != "chat":
            logger.info(f"🎯 私聊意图路由 | 用户: {user_name} | 意图: {intent_result['intent']}")
            # 取消不需要的数据加载任务
            for task in [memory_task, history_task, tarot_task]:
                task.cancel()
            await _route_to_command(update, context, intent_result)
            return
    except Exception as e:
        logger.error(f"❌ 意图识别异常，回退到正常对话: {e}")
    
    # === AI 对话配额检查 ===
    quota_result = await quota_manager.check_and_deduct("ai_chat", user_id)
    if not quota_result.allowed:
        # 取消数据加载任务
        for task in [memory_task, history_task, tarot_task]:
            task.cancel()
        await safe_reply(update.message, quota_result.message)
        return

    # === 等待数据加载完成（已在后台并行运行） ===
    user_memory, conversation_history, tarot_readings = await asyncio.gather(
        memory_task, history_task, tarot_task
    )
    
    # 每次都同步最新的平台用户名到记忆档案
    if user_name and user_name != "朋友":
        user_memory['user_name'] = user_name
        # 同步到 basic_info.nickname，让 AI 和记忆系统都能访问
        if 'basic_info' not in user_memory:
            user_memory['basic_info'] = {}
        if not user_memory['basic_info'].get('nickname'):
            user_memory['basic_info']['nickname'] = user_name
    memory_context = user_memory_manager.format_memory_for_ai(user_memory)
    tarot_context = tarot_history_manager.format_readings_for_ai(tarot_readings)
    
    # 添加用户消息到缓冲区（不阻塞主流程，fire-and-forget）
    asyncio.create_task(conversation_buffer.add_message(user_id, "user", user_message))
    
    # 5. 调用 AI 获取回复（注入用户偏好）
    preferences = user_memory.get("preferences", {})
    reply = await elena_ai.chat(
        user_message=user_message,
        user_name=user_name,
        conversation_history=conversation_history,
        tarot_context=tarot_context,
        memory_context=memory_context,
        preferences=preferences
    )
    
    # 6. 清理 Markdown 标记（AI 回复可能带 **加粗** 等，Zapry 不支持）
    reply = clean_markdown(reply)
    
    # 7. 先回复用户（最高优先级，不让用户等任何后处理）
    await safe_reply(update.message, reply)
    logger.info(f"✅ 私聊回复成功 | 用户: {user_name}")
    
    # 8. 后处理：持久化 + 记忆提取（全部后台化，不阻塞下一条消息）
    asyncio.create_task(_post_reply_tasks(user_id, user_message, reply, user_memory))



async def handle_group_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理群组@消息
    当用户在群里@机器人时，林晚晴会回复
    """
    
    # 检查是否是命令
    if update.message.text and update.message.text.startswith('/'):
        return
    
    # 检查是否@了机器人
    bot_username = context.bot.username
    bot_name = context.bot.name if hasattr(context.bot, 'name') else None  # 可能是显示名
    message_text = update.message.text or ""
    
    # 判断是否@了机器人
    is_mentioned = False
    
    # 方式1: 检查 entities 中的 mention（标准 Telegram + Zapry 兼容）
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "mention":
                # 标准方式：通过 offset+length 从文本中提取
                if entity.length > 0:
                    mention_text = message_text[entity.offset:entity.offset + entity.length]
                    if bot_username and bot_username.lower() in mention_text.lower():
                        is_mentioned = True
                        break
                
                # Zapry 兼容（问题4 未修复）：length=0 但 entity.user 有信息
                if not is_mentioned and hasattr(entity, 'user') and entity.user:
                    entity_username = entity.user.username or ""
                    if bot_username and entity_username.lower() == bot_username.lower():
                        is_mentioned = True
                        break
                    # Zapry 可能用显示名（如"塔罗牌运势"）代替 username
                    if entity_username and entity_username in message_text:
                        is_mentioned = True
                        break
    
    # 方式2: 文本匹配 @bot_username
    if not is_mentioned and bot_username:
        if f"@{bot_username}" in message_text:
            is_mentioned = True
    
    # 方式3: 通过 bot ID 匹配（Zapry 的 entity.user.id 可能是 bot 的 ID）
    if not is_mentioned and update.message.entities:
        for entity in update.message.entities:
            if entity.type == "mention" and hasattr(entity, 'user') and entity.user:
                entity_user_id = entity.user.id
                bot_id = context.bot.id
                if entity_user_id and bot_id and str(entity_user_id) == str(bot_id):
                    is_mentioned = True
                    break
    
    # 如果没有@机器人，不处理
    if not is_mentioned:
        return
    
    user = update.effective_user
    user_name = get_display_name(user)
    
    # 移除@机器人的部分，获取真正的消息内容
    clean_message = message_text
    if bot_username:
        clean_message = clean_message.replace(f"@{bot_username}", "").strip()
    # Zapry 兼容：@后面可能是显示名（如 @塔罗牌运势）
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "mention" and hasattr(entity, 'user') and entity.user:
                display_name = entity.user.username or ""
                if display_name:
                    clean_message = clean_message.replace(f"@{display_name}", "").strip()
    
    user_id = str(user.id)
    
    logger.info(f"💬 收到群组@消息 | 用户: {user_name} ({user.id}) | 群组: {update.effective_chat.id} | 内容: {clean_message[:50]}")
    
    # 如果清理后的消息为空
    if not clean_message:
        await safe_reply(
            update.message,
            "你好呀，找我有事吗？😊\n\n"
            "想占卜发 /tarot 加上问题，\n"
            "想聊天直接 @我说就好~"
        )
        return
    
    # 发送"正在输入"状态（Zapry 不支持，跳过）
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.debug(f"发送 typing 状态失败（平台可能不支持）: {e}")
    
    # === 自然语言意图识别（群聊也支持） ===
    try:
        intent_result = await intent_router.detect(clean_message)
        if intent_result["intent"] != "chat":
            logger.info(f"🎯 群聊意图路由 | 用户: {user_name} | 意图: {intent_result['intent']}")
            await _route_to_command(update, context, intent_result)
            return
    except Exception as e:
        logger.error(f"❌ 群聊意图识别异常，回退到正常对话: {e}")
    
    # 加载用户档案（私聊中积累的记忆，群聊中也能用）
    user_memory = await user_memory_manager.get_user_memory(user_id)
    memory_context = user_memory_manager.format_memory_for_ai(user_memory)
    
    # 加载塔罗历史
    tarot_readings = await tarot_history_manager.get_recent_readings(user_id, limit=3)
    tarot_context = tarot_history_manager.format_readings_for_ai(tarot_readings) if tarot_readings else None
    
    # 群组对话不保存历史（避免多人对话混乱），但加载用户档案
    preferences = user_memory.get("preferences", {})
    reply = await elena_ai.chat(
        user_message=clean_message,
        user_name=user_name,
        conversation_history=None,
        tarot_context=tarot_context,
        preferences=preferences,
        memory_context=memory_context
    )
    
    # 清理 Markdown 标记
    reply = clean_markdown(reply)
    
    # 回复时引用用户消息
    await safe_reply(update.message, reply)
    
    logger.info(f"✅ 群组回复成功 | 用户: {user_name} | 群组: {update.effective_chat.id}")


async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    清除对话历史命令（短期记忆）
    /clear - 清除与林晚晴的对话历史
    """
    user_id = str(update.effective_user.id)
    
    # 清除持久化的对话历史
    await chat_history_manager.clear_history(user_id)
    
    # 兼容：也清 context.user_data（以防还有残留引用）
    context.user_data['conversation_history'] = []
    
    await safe_reply(
        update.message,
        "好的，我们的聊天记录清空了~\n\n"
        "就像翻开了新的一页。\n\n"
        "有什么想聊的吗？我在这里听你说 😊"
    )
    
    logger.info(f"🗑️ 对话历史已清除 | 用户: {user_id}")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    查看用户档案命令
    /memory - 查看林晚晴记住的关于我的信息
    """
    user_id = str(update.effective_user.id)
    user_memory = await user_memory_manager.get_user_memory(user_id)
    
    if user_memory.get('conversation_count', 0) == 0:
        await safe_reply(
            update.message,
            "我们还没有好好聊过呢~\n\n"
            "多和我说说话，我会慢慢了解你的 😊\n\n"
            "— 晚晴 🌿"
        )
        return
    
    # 构建档案展示
    memory_text = "🌙 我记得的关于你的事~\n"
    memory_text += "━━━━━━━━━━━━━━━━━\n\n"
    
    # 基本信息
    basic = user_memory.get('basic_info', {})
    if any(basic.values()):
        memory_text += "关于你：\n"
        if basic.get('age'):
            memory_text += f"  年龄: {basic['age']}岁\n"
        if basic.get('gender'):
            memory_text += f"  性别: {basic['gender']}\n"
        if basic.get('location'):
            memory_text += f"  位置: {basic['location']}\n"
        if basic.get('occupation'):
            memory_text += f"  职业: {basic['occupation']}\n"
        if basic.get('school'):
            memory_text += f"  学校: {basic['school']}\n"
        if basic.get('major'):
            memory_text += f"  专业: {basic['major']}\n"
        memory_text += "\n"
    
    # 性格特征
    personality = user_memory.get('personality', {})
    if personality.get('traits'):
        memory_text += f"💭 性格: {', '.join(personality['traits'])}\n\n"
    
    # 生活背景
    life_context = user_memory.get('life_context', {})
    if life_context.get('concerns'):
        memory_text += f"🤔 当前困扰: {', '.join(life_context['concerns'][:3])}\n\n"
    if life_context.get('goals'):
        memory_text += f"🎯 目标: {', '.join(life_context['goals'][:3])}\n\n"
    
    # 兴趣爱好
    interests = user_memory.get('interests', [])
    if interests:
        memory_text += f"💝 兴趣: {', '.join(interests[:5])}\n\n"
    
    # 总结
    summary = user_memory.get('conversation_summary', '')
    if summary:
        memory_text += f"📝 我的印象: {summary}\n\n"
    
    memory_text += "━━━━━━━━━━━━━━━━━\n\n"
    memory_text += f"我们已经聊了 {user_memory.get('conversation_count', 0)} 次了~\n\n"
    memory_text += "这些帮助我更懂你，给你更贴心的建议 💭\n\n"
    memory_text += "想清除记忆的话，发 /forget 就好。\n\n"
    memory_text += "— 晚晴 🌿"
    
    await safe_reply(update.message, memory_text)
    
    logger.info(f"👀 查看档案 | 用户: {user_id}")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    清除用户档案命令
    /forget - 清除林晚晴关于我的所有记忆
    """
    user_id = str(update.effective_user.id)
    user_memory = await user_memory_manager.get_user_memory(user_id)
    
    if user_memory.get('conversation_count', 0) == 0:
        await safe_reply(
            update.message,
            "其实我还没有记住你什么呢~\n\n"
            "不用担心，你的隐私很安全 😊\n\n"
            "— 晚晴 🌿"
        )
        return
    
    # 删除档案（异步）
    success = await user_memory_manager.delete_user_memory(user_id)
    
    # 清空对话缓冲区
    conversation_buffer.clear_buffer_sync(user_id)
    
    # 清空持久化对话历史
    chat_history_manager.clear_history_sync(user_id)
    
    # 兼容：也清 context.user_data
    context.user_data['conversation_history'] = []
    
    if success:
        await safe_reply(
            update.message,
            "好的，我把关于你的一切都忘掉了~\n\n"
            "就像我们第一次见面一样。\n\n"
            "以后想让我重新了解你，随时来找我聊天就好 😊\n\n"
            "— 晚晴 🌿"
        )
        logger.info(f"🗑️ 用户档案已删除 | 用户: {user_id}")
    else:
        await safe_reply(
            update.message,
            "抱歉，清除的时候出了点小状况 😅\n\n"
            "过一会儿再试试好吗？\n\n"
            "— 晚晴 🌿"
        )



async def elena_intro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    林晚晴自我介绍命令
    /intro 或 /about - 了解林晚晴
    """
    
    intro_text = (
        "🌙 你好，我是晚晴\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "很高兴认识你~ 我是一名塔罗牌解读师，现在住在上海。\n\n"
        "平时主要做这些事：\n"
        "• 一对一塔罗解读\n"
        "• 塔罗工作坊和教学\n"
        "• 女性成长话题分享\n\n"
        "💫 关于塔罗\n\n"
        "我在复旦读心理学硕士的时候，研究荣格的原型理论，那时第一次接触到塔罗。"
        "后来发现，塔罗不是算命，而是一套象征系统，能帮人看清自己的内心。\n\n"
        "🎴 我的理念\n\n"
        "• 塔罗揭示的是趋势，不是命令\n"
        "• 我不替你做决定，只帮你看清选择\n"
        "• 真正的力量，始终在你自己手中\n\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "想占卜的话发 /tarot 加上问题，\n"
        "想聊天的话，随时找我就好~\n\n"
        "我在这里听你说 😊\n\n"
        "— 晚晴 🌿"
    )
    
    await safe_reply(update.message, intro_text)
    
    logger.info(f"ℹ️ 自我介绍 | 用户: {update.effective_user.id}")


async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    主动消息开关
    /notify - 开启/关闭晚晴的主动问候
    """
    from services.proactive import proactive_scheduler

    user_id = str(update.effective_user.id)
    currently_enabled = await proactive_scheduler.is_enabled(user_id)

    if currently_enabled:
        await proactive_scheduler.disable_user(user_id)
        await safe_reply(
            update.message,
            "好的，我不会主动打扰你了~\n\n想重新开启的话，随时发 /notify 就好 😊"
        )
        logger.info(f"🔕 主动消息已关闭 | 用户: {user_id}")
    else:
        await proactive_scheduler.enable_user(user_id)
        await safe_reply(
            update.message,
            "已开启~ 我会在这些时候主动找你：\n\n"
            "🌙 每天中午推送今日塔罗能量\n"
            "🎂 你生日那天送祝福\n"
            "🌿 节气的时候提醒你\n"
            "💭 占卜几天后回访你的感受\n\n"
            "不想收了随时发 /notify 关掉~"
        )
        logger.info(f"🔔 主动消息已开启 | 用户: {user_id}")
