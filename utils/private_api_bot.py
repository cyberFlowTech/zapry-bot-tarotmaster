"""
私有化 Telegram API 兼容层。

部分私有化 API 服务返回的 User 对象格式与官方 API 不同，
导致 python-telegram-bot 解析失败。此模块提供兼容的 Bot 类。
"""
from telegram import User, Chat, Update
from telegram.ext import ExtBot
from typing import Optional, Any, Dict
import logging

logger = logging.getLogger(__name__)

# 保存原始的 de_json 方法
_original_user_de_json = User.de_json
_original_chat_de_json = Chat.de_json
_original_update_de_json = Update.de_json


def _patched_user_de_json(data: Optional[Dict[str, Any]], bot=None) -> Optional[User]:
    """
    修补后的 User.de_json 方法，自动规范化数据
    """
    if data is None:
        return None
    
    # 规范化数据
    data = _normalize_user_data(data)
    
    # 调用原始方法
    return _original_user_de_json(data, bot)


def _patched_chat_de_json(data: Optional[Dict[str, Any]], bot=None) -> Optional[Chat]:
    """
    修补后的 Chat.de_json 方法，自动规范化数据
    """
    if data is None:
        return None
    
    # 规范化数据
    data = _normalize_chat_data(data)
    
    # 调用原始方法
    return _original_chat_de_json(data, bot)


def _patched_update_de_json(cls, data: Optional[Dict[str, Any]], bot=None) -> Optional[Update]:
    """
    修补后的 Update.de_json 方法，在解析前修复 Zapry 的数据格式问题
    """
    if data is None:
        return None
    
    # 在解析前修复整个 Update 数据
    data = _normalize_update_data(data)
    
    # 调用原始方法
    return _original_update_de_json(data, bot)


# 全局 Monkey Patch：替换所有 de_json 方法
User.de_json = staticmethod(_patched_user_de_json)
Chat.de_json = staticmethod(_patched_chat_de_json)
Update.de_json = classmethod(_patched_update_de_json)


# User 类接受的参数字段
_USER_FIELDS = {"id", "first_name", "is_bot", "last_name", "username", "language_code",
                "can_join_groups", "can_read_all_group_messages", "supports_inline_queries",
                "is_premium", "added_to_attachment_menu", "api_kwargs"}

# 私有 API 可能使用的字段名映射
_FIELD_ALIASES = {
    "bot_id": "id",
    "user_id": "id",
    "name": "first_name",
}


def _normalize_user_data(data: dict) -> dict:
    """
    将私有 API 返回的 User 格式转换为标准格式。
    处理：嵌套的 user 对象、字段名映射、移除多余字段（如 token）
    
    Zapry 已修复的问题（2026-02 确认）：
    - 问题1: first_name 现在会返回用户昵称（不再为空）
    - 问题2: is_bot 现在会正确返回
    以下兼容代码保留作为防御性编程，避免 Zapry 回退。
    """
    if not isinstance(data, dict):
        return data
    data = dict(data)
    # 若 result 为 {"user": {...}, "token": "..."} 等嵌套结构，提取 user
    if "user" in data and isinstance(data["user"], dict):
        data = data["user"].copy()
    # 字段名映射
    for old_key, new_key in _FIELD_ALIASES.items():
        if old_key in data and new_key not in data:
            data[new_key] = data.pop(old_key)
    
    # 转换 ID 为整数（问题3 尚未修复，仍需转换）
    if "id" in data and isinstance(data["id"], str):
        try:
            data["id"] = int(data["id"])
        except ValueError:
            # 如果是 bot 用户名（如 "zapry_tarot_bot"），保留字符串
            logger.warning(f"⚠️  User ID 无法转换为整数: {data['id']}")
    
    # 防御性补全 first_name（问题1 已由 Zapry 修复，此处保留兜底）
    if not data.get("first_name"):
        if data.get("username"):
            data["first_name"] = data["username"]
        elif data.get("last_name"):
            data["first_name"] = data["last_name"]
        elif data.get("name"):
            data["first_name"] = data["name"]
        elif data.get("is_bot") and "id" in data:
            data["first_name"] = str(data["id"])
        else:
            data["first_name"] = ""
        if data["first_name"]:
            logger.debug(f"🔧 补全缺失的 first_name: {data['first_name']}")
    
    # 防御性补全 is_bot（问题2 已由 Zapry 修复，此处保留兜底）
    if "is_bot" not in data:
        data["is_bot"] = False
        logger.debug("🔧 补全缺失的 is_bot: False")
    
    # 移除 User 不接受的字段（token 等），保留 User 接受的字段
    return {k: v for k, v in data.items() if k in _USER_FIELDS}


# Chat 类接受的参数字段
_CHAT_FIELDS = {"id", "type", "title", "username", "first_name", "last_name", "is_forum",
                "photo", "active_usernames", "emoji_status_custom_emoji_id", "bio",
                "has_private_forwards", "has_restricted_voice_and_video_messages",
                "join_to_send_messages", "join_by_request", "description", "invite_link",
                "pinned_message", "permissions", "slow_mode_delay", "message_auto_delete_time",
                "has_aggressive_anti_spam_enabled", "has_hidden_members", "has_protected_content",
                "sticker_set_name", "can_set_sticker_set", "linked_chat_id", "location",
                "api_kwargs"}


def _normalize_chat_data(data: dict) -> dict:
    """
    将私有 API 返回的 Chat 格式转换为标准格式。

    Zapry 已修复的问题（2026-02 确认）：
    - 问题5: 私聊 chat.id 现在返回用户数字 ID（不再是 bot 用户名）
    - 问题6: chat.type 现在正确返回 "private"/"group"
    
    仍需处理的问题：
    - 问题7: 群聊 chat.id 仍带 "g_" 前缀
    - ID 类型仍可能为字符串，需转为整数
    """
    if not isinstance(data, dict):
        return data

    data = dict(data)

    if "id" in data:
        chat_id = data["id"]
        if isinstance(chat_id, str):
            if chat_id.startswith("g_"):
                # 问题7 未修复：群组 ID 仍带 "g_" 前缀
                raw_id = chat_id[2:]
                try:
                    data["id"] = int(raw_id)
                    logger.debug(f"🔧 群组 Chat ID 转换: '{chat_id}' -> {data['id']}")
                except ValueError:
                    logger.warning(f"⚠️  群组 Chat ID 无法转换: {chat_id}")
                # 确保 type 是 group
                if not data.get("type") or data["type"] == "private":
                    data["type"] = "group"
            else:
                try:
                    data["id"] = int(chat_id)
                    logger.debug(f"🔧 Chat ID 转换: '{chat_id}' -> {data['id']}")
                except ValueError:
                    logger.warning(f"⚠️  Chat ID 无法转换为整数: {chat_id}")

    # 防御性补全 type（问题6 已由 Zapry 修复，此处保留兜底）
    if not data.get("type"):
        data["type"] = "private"
        logger.debug("🔧 补全缺失的 Chat.type: private")

    return {k: v for k, v in data.items() if k in _CHAT_FIELDS}


def _normalize_update_data(update_data: dict) -> dict:
    """
    递归规范化 Update 数据中的所有 User 对象和 Chat 对象。
    
    处理 Zapry 平台特有的数据格式差异：
    - User 对象规范化（first_name、is_bot 补全）
    - Chat 对象规范化（ID 类型转换、type 补全）
    - Message 中 chat.id 和 entities 修复
    """
    if not isinstance(update_data, dict):
        return update_data
    
    # 复制数据避免修改原始数据
    normalized = {}
    
    for key, value in update_data.items():
        if key == "message" and isinstance(value, dict):
            # 特殊处理 message：修复 Zapry 的 chat.id bug
            normalized[key] = _fix_message_chat_id(value)
        elif key == "callback_query" and isinstance(value, dict):
            # callback_query 也可能包含 message
            normalized[key] = _fix_callback_query(value)
        elif key == "from" or key == "user" or key == "forward_from" or key == "via_bot":
            # 这是 User 对象，需要规范化
            if isinstance(value, dict):
                normalized[key] = _normalize_user_data(value)
            else:
                normalized[key] = value
        elif key == "chat":
            # Chat 对象
            if isinstance(value, dict):
                normalized[key] = _normalize_chat_data(value)
            else:
                normalized[key] = value
        elif isinstance(value, dict):
            # 递归处理嵌套的字典
            normalized[key] = _normalize_update_data(value)
        elif isinstance(value, list):
            # 处理列表中的字典
            normalized[key] = [
                _normalize_update_data(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[key] = value
    
    return normalized


def _fix_message_chat_id(message_data: dict) -> dict:
    """
    修复 Zapry 的 message 数据问题。

    Zapry 已修复（2026-02）：
    - 问题5: 私聊 chat.id 现在正确返回用户 ID
    - 问题8: 命令消息现在包含 entities

    仍需处理：
    - 问题7: 群聊 chat.id 仍带 "g_" 前缀
    - ID 类型仍可能为字符串
    
    防御性保留所有兼容逻辑，确保向后兼容。
    """
    message_data = dict(message_data)

    if "chat" in message_data and isinstance(message_data["chat"], dict):
        chat = dict(message_data["chat"])
        chat_id = chat.get("id")
        chat_type = (chat.get("type") or "").lower()

        if isinstance(chat_id, str):
            if chat_id.startswith("g_"):
                # ===== 群聊：去掉 "g_" 前缀，转为整数 =====
                raw_id = chat_id[2:]  # 去掉 "g_"
                try:
                    chat["id"] = int(raw_id)
                    logger.info(f"🔧 修复 Zapry 群组 Chat ID: '{chat_id}' -> {chat['id']}")
                except ValueError:
                    logger.warning(f"⚠️  群组 Chat ID 转换失败: {chat_id}")
                # 确保 type 是 group
                if not chat_type or chat_type == "private":
                    chat["type"] = "group"
            else:
                # ===== 私聊或其他：尝试转为整数 =====
                try:
                    chat["id"] = int(chat_id)
                    logger.debug(f"🔧 Chat ID 转换: '{chat_id}' -> {chat['id']}")
                except ValueError:
                    # chat.id 是不可解析的字符串（如 bot 用户名）
                    # → 用 from.id（发送者 ID）作为 chat.id
                    if "from" in message_data and isinstance(message_data["from"], dict):
                        real_user_id = message_data["from"].get("id")
                        if real_user_id:
                            logger.debug(f"🔧 修复 Zapry 私聊 Chat ID: '{chat_id}' -> {real_user_id}")
                            chat["id"] = real_user_id
                    # 私聊场景下确保 type 正确
                    if not chat_type:
                        chat["type"] = "private"

            message_data["chat"] = chat

        # 修复空的 chat.type（callback_query 的 message 可能是空的）
        if not chat.get("type"):
            chat["type"] = "private"
            message_data["chat"] = chat

    # 修复缺失的 entities（用于命令识别）
    text = message_data.get("text", "")
    if text and text.startswith("/") and "entities" not in message_data:
        command_end = text.find(" ") if " " in text else len(text)
        command_text = text[:command_end]
        message_data["entities"] = [{
            "type": "bot_command",
            "offset": 0,
            "length": len(command_text)
        }]
        logger.debug(f"🔧 添加缺失的 entities: {command_text}")

    return message_data


def _fix_callback_query(callback_query_data: dict) -> dict:
    """修复 callback_query 中的 message"""
    callback_query_data = dict(callback_query_data)
    
    if "message" in callback_query_data and isinstance(callback_query_data["message"], dict):
        callback_query_data["message"] = _fix_message_chat_id(callback_query_data["message"])
    
    return callback_query_data


class PrivateAPIExtBot(ExtBot):
    """
    兼容私有化 Telegram API 的 ExtBot。

    当私有 API 返回 `name` 而非 `first_name` 时，自动转换以兼容标准库。
    """

    async def get_me(
        self,
        *,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
        api_kwargs=None,
    ):
        """覆盖 get_me，在解析前规范化 User 数据。"""
        result = await self._post(
            "getMe",
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            connect_timeout=connect_timeout,
            pool_timeout=pool_timeout,
            api_kwargs=api_kwargs,
        )
        result = _normalize_user_data(result)
        self._bot_user = User.de_json(result, self)
        return self._bot_user
    
    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = None,
        show_alert: bool = None,
        url: str = None,
        cache_time: int = None,
        *,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
        api_kwargs=None,
    ):
        """
        覆盖 answer_callback_query，Zapry 需要额外的 chat_id 参数
        但我们没有 chat_id，所以传空字符串或者直接忽略这个错误
        """
        # Zapry 的 answerCallbackQuery 要求 chat_id，但我们无法获取
        # 尝试直接调用，如果失败就忽略（用户仍能看到按钮响应）
        try:
            return await super().answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                show_alert=show_alert,
                url=url,
                cache_time=cache_time,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
                api_kwargs=api_kwargs,
            )
        except Exception as e:
            # Zapry 的 answerCallbackQuery 失败，记录但不影响主流程
            logger.warning(f"⚠️  answerCallbackQuery 失败（Zapry 兼容性问题）: {e}")
            return True  # 返回 True 让程序继续执行


def apply_private_api_compatibility():
    """
    应用私有化 API 兼容补丁
    必须在创建 Application 之前调用
    
    Zapry 已修复（2026-02）：问题1,2,5,6,8
    仍需兼容：问题3(ID类型),4(mention),7(g_前缀),9-14
    """
    logger.info("✅ 已启用 Zapry API 兼容层（防御性模式）")
    logger.info("   - User/Chat 数据自动规范化")
    logger.info("   - 群聊 g_ 前缀 ID 自动转换")
    logger.info("   - 命令 entities 防御性补全")
