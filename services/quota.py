"""
配额检查服务：每日免费次数 + 余额扣费逻辑

统一入口：check_and_deduct(feature, user_id)
- 先检查免费额度
- 免费次数用完后检查余额并扣费
- 返回结构化结果（允许/拒绝/原因）
"""

import logging
from datetime import date
from typing import NamedTuple

from db.database import db
from services.payment import payment_manager
from config import (
    FREE_TAROT_DAILY,
    FREE_CHAT_DAILY,
    PRICE_TAROT_DETAIL,
    PRICE_TAROT_READING,
    PRICE_AI_CHAT,
)

logger = logging.getLogger(__name__)


class QuotaResult(NamedTuple):
    """配额检查结果"""
    allowed: bool       # 是否允许使用
    is_free: bool       # 本次是否免费
    cost: float         # 本次实际扣费金额（免费则为 0）
    remaining_free: int # 剩余免费次数（-1 表示无限制）
    balance: float      # 当前余额
    message: str        # 提示信息（余额不足时使用）


# 功能 -> (每日免费次数, 超额单价, 用量字段名)
_FEATURE_CONFIG = {
    "tarot_reading": (FREE_TAROT_DAILY, PRICE_TAROT_READING, "tarot_count"),
    "tarot_detail":  (0, PRICE_TAROT_DETAIL, None),    # 深度解读无免费额度
    "ai_chat":       (FREE_CHAT_DAILY, PRICE_AI_CHAT, "chat_count"),
}


class QuotaManager:
    """配额管理器"""

    async def check_and_deduct(self, feature: str, user_id: str) -> QuotaResult:
        """
        检查配额并扣费（如果需要）

        流程：
        1. 查询今日已用次数
        2. 如果在免费额度内 → 允许 + 计数 +1
        3. 如果超出免费额度 → 检查余额 → 扣费或拒绝
        """
        config = _FEATURE_CONFIG.get(feature)
        if not config:
            logger.warning(f"⚠️ 未知功能: {feature}，默认允许")
            return QuotaResult(True, True, 0, -1, 0, "")

        free_limit, price, usage_field = config
        today = date.today().isoformat()

        # 获取今日用量
        used_count = 0
        if usage_field:
            used_count = await self._get_daily_usage(user_id, today, usage_field)

        # 检查是否在免费额度内
        if free_limit > 0 and used_count < free_limit:
            # 免费额度内：计数 +1，允许使用
            if usage_field:
                await self._increment_usage(user_id, today, usage_field)
            remaining = free_limit - used_count - 1
            logger.info(f"🆓 免费使用 | 用户: {user_id} | 功能: {feature} | 剩余: {remaining}")
            return QuotaResult(
                allowed=True,
                is_free=True,
                cost=0,
                remaining_free=remaining,
                balance=await payment_manager.get_balance(user_id),
                message=""
            )

        # 超出免费额度（或无免费额度）：需要扣费
        balance = await payment_manager.get_balance(user_id)

        if balance < price:
            # 余额不足
            remaining = 0
            msg = self._build_insufficient_message(feature, price, balance, free_limit)
            logger.info(f"🚫 配额不足 | 用户: {user_id} | 功能: {feature} | 需要: {price} | 余额: {balance}")
            return QuotaResult(
                allowed=False,
                is_free=False,
                cost=price,
                remaining_free=0,
                balance=balance,
                message=msg
            )

        # 余额充足：扣费
        success = await payment_manager.deduct_balance(user_id, price, feature)
        if not success:
            # 并发下可能扣费失败
            return QuotaResult(
                allowed=False,
                is_free=False,
                cost=price,
                remaining_free=0,
                balance=balance,
                message="扣费时出现问题，请稍后重试。"
            )

        # 扣费成功，也要计数
        if usage_field:
            await self._increment_usage(user_id, today, usage_field)

        new_balance = await payment_manager.get_balance(user_id)
        logger.info(f"💳 付费使用 | 用户: {user_id} | 功能: {feature} | 扣费: {price} | 余额: {new_balance}")
        return QuotaResult(
            allowed=True,
            is_free=False,
            cost=price,
            remaining_free=0,
            balance=new_balance,
            message=""
        )

    # ------------------------------------------------------------------
    # 查询（不扣费）
    # ------------------------------------------------------------------

    async def check_only(self, feature: str, user_id: str) -> QuotaResult:
        """只检查配额，不扣费（用于提前预检）"""
        config = _FEATURE_CONFIG.get(feature)
        if not config:
            return QuotaResult(True, True, 0, -1, 0, "")

        free_limit, price, usage_field = config
        today = date.today().isoformat()

        used_count = 0
        if usage_field:
            used_count = await self._get_daily_usage(user_id, today, usage_field)

        balance = await payment_manager.get_balance(user_id)

        if free_limit > 0 and used_count < free_limit:
            return QuotaResult(True, True, 0, free_limit - used_count, balance, "")

        if balance >= price:
            return QuotaResult(True, False, price, 0, balance, "")

        msg = self._build_insufficient_message(feature, price, balance, free_limit)
        return QuotaResult(False, False, price, 0, balance, msg)

    async def get_daily_summary(self, user_id: str) -> dict:
        """获取用户今日用量摘要"""
        today = date.today().isoformat()
        row = await db.fetch_one(
            "SELECT * FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today)
        )
        tarot_used = row["tarot_count"] if row else 0
        chat_used = row["chat_count"] if row else 0
        balance = await payment_manager.get_balance(user_id)

        return {
            "tarot_used": tarot_used,
            "tarot_free_remaining": max(0, FREE_TAROT_DAILY - tarot_used),
            "tarot_free_limit": FREE_TAROT_DAILY,
            "chat_used": chat_used,
            "chat_free_remaining": max(0, FREE_CHAT_DAILY - chat_used),
            "chat_free_limit": FREE_CHAT_DAILY,
            "balance": balance,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_daily_usage(self, user_id: str, today: str, field: str) -> int:
        """获取今日某项用量"""
        row = await db.fetch_one(
            f"SELECT {field} FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today)
        )
        return row[field] if row else 0

    async def _increment_usage(self, user_id: str, today: str, field: str):
        """增加今日用量"""
        await db.execute(
            f"""
            INSERT INTO daily_usage (user_id, usage_date, {field})
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, usage_date) DO UPDATE
            SET {field} = {field} + 1
            """,
            (user_id, today)
        )

    def _build_insufficient_message(self, feature: str, price: float, balance: float, free_limit: int) -> str:
        """构建余额不足的提示消息（林晚晴口吻）"""
        feature_names = {
            "tarot_reading": "塔罗占卜",
            "tarot_detail": "深度解读",
            "ai_chat": "AI 对话",
        }
        name = feature_names.get(feature, feature)

        if free_limit > 0:
            msg = (
                f"今天的免费{name}次数已经用完了呢。\n\n"
                f"继续使用需要 {price} USDT，"
            )
        else:
            msg = f"使用{name}功能需要 {price} USDT，"

        if balance > 0:
            msg += f"你当前余额 {balance:.4f} USDT，还差一点点。\n\n"
        else:
            msg += "你还没有充值过呢。\n\n"

        msg += "使用 /recharge 充值 USDT 即可解锁~ 💎"
        return msg


# 全局单例
quota_manager = QuotaManager()
