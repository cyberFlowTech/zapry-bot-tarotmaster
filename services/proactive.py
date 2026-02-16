"""
主动触发调度器

让晚晴不再只是被动回复，而是主动关心用户：
- 每日运势推送（中午 12 点左右随机触发）
- 生日祝福（用户生日当天）
- 占卜回访（占卜后 3 天）
- 节气提醒（二十四节气当天）

结构与 chain_monitor.py 一致：start()/stop() 生命周期 + 后台 asyncio task。
"""

import asyncio
import logging
import random
from datetime import datetime, date, timedelta
from typing import Optional

from db.database import db
from services.tarot_data import TarotDeck

logger = logging.getLogger(__name__)

# 二十四节气近似日期（公历月-日）
SOLAR_TERMS = {
    (2, 4): "立春", (2, 19): "雨水", (3, 6): "惊蛰", (3, 21): "春分",
    (4, 5): "清明", (4, 20): "谷雨", (5, 6): "立夏", (5, 21): "小满",
    (6, 6): "芒种", (6, 21): "夏至", (7, 7): "小暑", (7, 23): "大暑",
    (8, 7): "立秋", (8, 23): "处暑", (9, 8): "白露", (9, 23): "秋分",
    (10, 8): "寒露", (10, 23): "霜降", (11, 7): "立冬", (11, 22): "小雪",
    (12, 7): "大雪", (12, 22): "冬至", (1, 6): "小寒", (1, 20): "大寒",
}

tarot_deck = TarotDeck()


class ProactiveScheduler:
    """主动消息调度器"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._bot = None
        self._today_fortune_sent = False
        self._today_date = None

    def set_bot(self, bot):
        self._bot = bot

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("✅ 主动触发调度器已启动")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 主动触发调度器已停止")

    async def _poll_loop(self):
        """每 60 秒检查一次触发条件"""
        while self._running:
            try:
                now = datetime.now()
                today = now.date()

                # 日期切换时重置状态
                if self._today_date != today:
                    self._today_date = today
                    self._today_fortune_sent = False

                # 每日运势：12:00-12:30 之间随机触发（只触发一次）
                if not self._today_fortune_sent and now.hour == 12 and now.minute <= 30:
                    # 用随机概率分散到 30 分钟内（每分钟约 3.3% 概率触发）
                    if random.random() < 0.05:
                        await self._send_daily_fortune(today)
                        self._today_fortune_sent = True

                # 生日祝福：每天 10:00 检查一次
                if now.hour == 10 and now.minute == 0:
                    await self._check_birthdays(today)

                # 节气提醒：每天 8:00 检查
                if now.hour == 8 and now.minute == 0:
                    await self._check_solar_terms(today)

                # 占卜回访：每天 15:00 检查
                if now.hour == 15 and now.minute == 0:
                    await self._check_followups(today)

            except Exception as e:
                logger.error(f"❌ 主动调度异常: {e}", exc_info=True)

            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # 每日运势推送
    # ------------------------------------------------------------------

    async def _send_daily_fortune(self, today: date):
        """向所有启用了主动推送的用户发送每日运势"""
        users = await self._get_enabled_users("daily_fortune")
        if not users:
            return

        # 抽一张每日能量牌
        card = tarot_deck.draw_card()
        card_name = card['name_full']
        is_upright = "正位" in card['orientation']

        for user_id in users:
            try:
                if is_upright:
                    text = (
                        f"嘿，中午好~ 🌙\n\n"
                        f"今天的塔罗能量牌是「{card_name}」\n\n"
                        f"整体能量不错，适合主动出击，推进你在意的事情。\n"
                        f"相信自己的直觉，今天做的选择大概率是对的~\n\n"
                        f"有什么想问的随时找我 ✨"
                    )
                else:
                    text = (
                        f"嘿，中午好~ 🌙\n\n"
                        f"今天的塔罗能量牌是「{card_name}」\n\n"
                        f"今天的节奏可以慢一些，不急着做重要决定。\n"
                        f"多听少说，照顾好自己的状态就好~\n\n"
                        f"有什么困惑随时找我聊 💭"
                    )

                await self._send_message(user_id, text)
                await self._record_sent(user_id, "daily_fortune")
            except Exception as e:
                logger.error(f"❌ 每日运势推送失败 | 用户: {user_id} | 错误: {e}")

        logger.info(f"📨 每日运势已推送 | 用户数: {len(users)} | 牌: {card_name}")

    # ------------------------------------------------------------------
    # 生日祝福
    # ------------------------------------------------------------------

    async def _check_birthdays(self, today: date):
        """检查今天是否有用户生日"""
        today_str = today.strftime("%m-%d")

        # 从 user_memories 中查找生日匹配的用户
        all_memories = await db.fetch_all(
            "SELECT user_id, memory_data FROM user_memories"
        )

        for row in all_memories:
            try:
                import json
                memory = json.loads(row["memory_data"])
                birthday = memory.get("basic_info", {}).get("birthday", "")

                if not birthday:
                    continue

                # 支持多种格式：1997-10-15, 10-15, 10月15日
                if today_str in birthday or today.strftime("%-m月%-d日") in birthday:
                    # 检查今天是否已发送过
                    if await self._already_sent_today(row["user_id"], "birthday"):
                        continue

                    # 检查用户是否启用
                    prefs = memory.get("preferences", {})
                    if not prefs.get("proactive_enabled", True):
                        continue

                    card = tarot_deck.draw_card()
                    text = (
                        f"生日快乐~ 🎂🌙\n\n"
                        f"今天是你的生日呢，我特别帮你抽了一张生日牌：\n"
                        f"「{card['name_full']}」\n\n"
                        f"{'正位的能量很好，新的一岁会有很多美好的事情发生。' if '正位' in card['orientation'] else '逆位提醒你，新的一年要更加珍惜身边的人和当下的时光。'}\n\n"
                        f"祝你新的一岁一切都越来越好~ ✨\n\n"
                        f"— 晚晴 🌿"
                    )

                    await self._send_message(row["user_id"], text)
                    await self._record_sent(row["user_id"], "birthday")
                    logger.info(f"🎂 生日祝福已发送 | 用户: {row['user_id']}")

            except Exception as e:
                logger.error(f"❌ 生日检查异常 | 用户: {row['user_id']} | 错误: {e}")

    # ------------------------------------------------------------------
    # 节气提醒
    # ------------------------------------------------------------------

    async def _check_solar_terms(self, today: date):
        """检查今天是否是节气"""
        key = (today.month, today.day)
        term_name = SOLAR_TERMS.get(key)
        if not term_name:
            return

        users = await self._get_enabled_users("solar_term")
        if not users:
            return

        for user_id in users:
            try:
                text = (
                    f"今天是{term_name}~ 🌿\n\n"
                    f"二十四节气中的{term_name}，意味着自然能量的转换。\n"
                    f"从塔罗的角度看，节气交替的日子适合静下来想想接下来的方向。\n\n"
                    f"想占卜的话随时找我~\n\n"
                    f"— 晚晴 🌿"
                )
                await self._send_message(user_id, text)
                await self._record_sent(user_id, "solar_term")
            except Exception as e:
                logger.error(f"❌ 节气提醒失败 | 用户: {user_id} | 错误: {e}")

        logger.info(f"🌿 节气提醒已推送 | {term_name} | 用户数: {len(users)}")

    # ------------------------------------------------------------------
    # 占卜回访
    # ------------------------------------------------------------------

    async def _check_followups(self, today: date):
        """检查 3 天前有占卜记录的用户，发送回访"""
        three_days_ago = (today - timedelta(days=3)).isoformat()

        readings = await db.fetch_all(
            """
            SELECT DISTINCT user_id, question
            FROM tarot_readings
            WHERE DATE(created_at) = ?
            """,
            (three_days_ago,)
        )

        for reading in readings:
            user_id = reading["user_id"]
            question = reading["question"]

            try:
                # 检查是否已发送
                if await self._already_sent_today(user_id, "followup"):
                    continue

                # 检查用户是否启用
                memory_row = await db.fetch_one(
                    "SELECT memory_data FROM user_memories WHERE user_id = ?",
                    (user_id,)
                )
                if memory_row:
                    import json
                    memory = json.loads(memory_row["memory_data"])
                    prefs = memory.get("preferences", {})
                    if not prefs.get("proactive_enabled", True):
                        continue

                short_q = question[:20] + "..." if len(question) > 20 else question
                text = (
                    f"嘿，想起前几天你问过「{short_q}」\n\n"
                    f"这几天感觉怎么样？有什么变化吗？\n\n"
                    f"如果想再看看当前的走势，随时找我占一次~\n\n"
                    f"— 晚晴 🌿"
                )

                await self._send_message(user_id, text)
                await self._record_sent(user_id, "followup")
                logger.info(f"📨 占卜回访已发送 | 用户: {user_id}")

            except Exception as e:
                logger.error(f"❌ 占卜回访失败 | 用户: {user_id} | 错误: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def _get_enabled_users(self, trigger_type: str) -> list:
        """获取启用了主动消息的用户列表"""
        rows = await db.fetch_all(
            "SELECT user_id FROM proactive_schedule WHERE trigger_type = ? AND enabled = 1",
            (trigger_type,)
        )
        return [r["user_id"] for r in rows]

    async def _already_sent_today(self, user_id: str, trigger_type: str) -> bool:
        """检查今天是否已发送过"""
        today_str = date.today().isoformat()
        row = await db.fetch_one(
            """
            SELECT last_sent FROM proactive_schedule
            WHERE user_id = ? AND trigger_type = ?
            """,
            (user_id, trigger_type)
        )
        if row and row["last_sent"] and row["last_sent"].startswith(today_str):
            return True
        return False

    async def _record_sent(self, user_id: str, trigger_type: str):
        """记录发送时间"""
        now_str = datetime.now().isoformat()
        await db.execute(
            """
            INSERT INTO proactive_schedule (user_id, trigger_type, next_trigger, last_sent, enabled)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(user_id, trigger_type) DO UPDATE SET last_sent = ?
            """,
            (user_id, trigger_type, now_str, now_str, now_str)
        )

    async def _send_message(self, user_id: str, text: str):
        """发送消息给用户"""
        if not self._bot:
            logger.warning("Bot 未设置，无法发送主动消息")
            return
        try:
            await self._bot.send_message(chat_id=int(user_id), text=text)
        except Exception as e:
            logger.error(f"❌ 发送主动消息失败 | 用户: {user_id} | 错误: {e}")

    async def enable_user(self, user_id: str):
        """为用户启用所有主动消息"""
        now_str = datetime.now().isoformat()
        for trigger_type in ["daily_fortune", "birthday", "solar_term", "followup"]:
            await db.execute(
                """
                INSERT INTO proactive_schedule (user_id, trigger_type, next_trigger, enabled)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id, trigger_type) DO UPDATE SET enabled = 1
                """,
                (user_id, trigger_type, now_str)
            )

    async def disable_user(self, user_id: str):
        """为用户关闭所有主动消息"""
        await db.execute(
            "UPDATE proactive_schedule SET enabled = 0 WHERE user_id = ?",
            (user_id,)
        )

    async def is_enabled(self, user_id: str) -> bool:
        """检查用户是否启用了主动消息"""
        row = await db.fetch_one(
            "SELECT enabled FROM proactive_schedule WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        return row["enabled"] == 1 if row else False


# 全局单例
proactive_scheduler = ProactiveScheduler()
