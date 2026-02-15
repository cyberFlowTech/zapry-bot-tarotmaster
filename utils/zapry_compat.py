"""
Zapry 平台全局兼容配置
统一管理所有 Zapry 特性和兼容性处理

已修复（2026-02 确认）：问题1(first_name),2(is_bot),5(私聊chat.id),6(chat.type),8(entities)
仍需兼容：问题3(ID字符串),4(username),7(g_前缀),9-14(API方法差异)
"""
from config import TG_PLATFORM

# 是否使用 Zapry 平台
IS_ZAPRY = (TG_PLATFORM == "zapry")

# Zapry 平台限制和特性（已修复的标记为 False/空）
ZAPRY_LIMITATIONS = {
    "supports_markdown": False,          # 问题14: 不支持 Markdown 格式（未修复）
    "supports_edit_message": False,      # 问题10: 不支持 editMessageText（未修复）
    "supports_answer_callback": False,   # 问题11: answerCallbackQuery 需要 chat_id（未修复）
    "supports_chat_action": False,       # 问题9: 不支持 sendChatAction（未修复）
    "id_fields_are_strings": True,       # 问题3: 所有 ID 都是字符串（未修复）
    "group_id_has_prefix": True,         # 问题7: 群聊 ID 带 g_ 前缀（未修复）
    "user_missing_username": True,       # 问题4: User 缺少 username（未修复）
    # 以下问题已由 Zapry 修复（2026-02），兼容代码保留防御
    # "user_missing_fields": [],         # 问题1,2: first_name 和 is_bot 已修复
    # "chat_wrong_id": False,            # 问题5: 私聊 chat.id 已修复
    # "chat_missing_type": False,        # 问题6: chat.type 已修复
    # "message_missing_entities": False,  # 问题8: entities 已修复
}


def should_use_markdown() -> bool:
    """是否应该使用 Markdown 格式"""
    if IS_ZAPRY:
        return False
    return True


def should_edit_message() -> bool:
    """是否应该编辑消息（否则发送新消息）"""
    if IS_ZAPRY:
        return False
    return True


def get_parse_mode() -> str:
    """获取应该使用的 parse_mode"""
    if IS_ZAPRY:
        return None  # Zapry 不支持
    return 'Markdown'


def clean_markdown(text: str) -> str:
    """
    清理文本中的 Markdown 标记
    Zapry 平台不支持 Markdown 渲染，AI 回复中的 **加粗** 等标记
    会原样显示给用户，所以需要去除。
    """
    import re
    # **加粗** → 加粗
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # __加粗__ → 加粗
    text = re.sub(r'__(.+?)__', r'\1', text)
    # *斜体* → 斜体（避免误伤 ** 中的 *）
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    # _斜体_ → 斜体
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'\1', text)
    # `代码` → 代码
    text = re.sub(r'`(.+?)`', r'\1', text)
    # ### 标题 → 标题
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text
