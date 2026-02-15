"""
支付核心服务：余额管理、充值确认、消费扣费

充值方案（HD 热钱包版）：
- 每个用户拥有独立的充值地址（HD 派生）
- 链上监听按 to_address 直接映射用户，零碰撞
- 到账后自动归集到冷钱包

所有金额以 USDT 为单位。
"""

import uuid
import logging
from datetime import datetime
from typing import Optional

from db.database import db
from config import RECHARGE_ORDER_EXPIRE

logger = logging.getLogger(__name__)


class PaymentManager:
    """用户余额与充值订单管理"""

    # ------------------------------------------------------------------
    # 余额查询
    # ------------------------------------------------------------------

    async def get_balance(self, user_id: str) -> float:
        """获取用户当前余额"""
        row = await db.fetch_one(
            "SELECT balance FROM user_balances WHERE user_id = ?",
            (user_id,)
        )
        return row["balance"] if row else 0.0

    async def get_balance_info(self, user_id: str) -> dict:
        """获取用户完整余额信息"""
        row = await db.fetch_one(
            "SELECT * FROM user_balances WHERE user_id = ?",
            (user_id,)
        )
        if row:
            return dict(row)
        return {
            "user_id": user_id,
            "balance": 0.0,
            "total_recharged": 0.0,
            "total_spent": 0.0,
        }

    # ------------------------------------------------------------------
    # 充值（增加余额）
    # ------------------------------------------------------------------

    async def add_balance(self, user_id: str, amount: float, tx_hash: str = None) -> float:
        """
        增加用户余额（充值到账后调用）
        返回充值后的新余额
        """
        if amount <= 0:
            raise ValueError("充值金额必须大于 0")

        await db.execute(
            """
            INSERT INTO user_balances (user_id, balance, total_recharged, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + excluded.balance,
                total_recharged = total_recharged + excluded.total_recharged,
                updated_at = datetime('now', 'localtime')
            """,
            (user_id, amount, amount)
        )

        new_balance = await self.get_balance(user_id)
        logger.info(f"💰 充值成功 | 用户: {user_id} | 金额: {amount} USDT | 新余额: {new_balance} | tx: {tx_hash}")
        return new_balance

    # ------------------------------------------------------------------
    # 扣费（消费余额）
    # ------------------------------------------------------------------

    async def deduct_balance(self, user_id: str, amount: float, feature: str) -> bool:
        """
        扣除用户余额（使用付费功能时调用）
        返回是否扣费成功
        """
        if amount <= 0:
            return True

        balance = await self.get_balance(user_id)
        if balance < amount:
            logger.info(f"💸 余额不足 | 用户: {user_id} | 需要: {amount} | 当前: {balance}")
            return False

        await db.execute(
            """
            UPDATE user_balances
            SET balance = balance - ?,
                total_spent = total_spent + ?,
                updated_at = datetime('now', 'localtime')
            WHERE user_id = ?
            """,
            (amount, amount, user_id)
        )

        await db.execute(
            "INSERT INTO spend_records (user_id, feature, amount) VALUES (?, ?, ?)",
            (user_id, feature, amount)
        )

        new_balance = await self.get_balance(user_id)
        logger.info(f"💸 消费扣费 | 用户: {user_id} | 功能: {feature} | 扣费: {amount} USDT | 余额: {new_balance}")
        return True

    # ------------------------------------------------------------------
    # 充值订单管理（HD 热钱包版）
    # ------------------------------------------------------------------

    async def create_recharge_order(self, user_id: str, deposit_address: str) -> dict:
        """
        创建充值订单（HD 热钱包版）
        不再需要指定金额 — 用户转多少到账多少
        """
        order_id = f"R{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

        # 过期该用户之前未完成的订单
        await self._expire_user_pending_orders(user_id)

        await db.execute(
            """
            INSERT INTO recharge_orders (user_id, order_id, amount, deposit_address, status)
            VALUES (?, ?, 0, ?, 'pending')
            """,
            (user_id, order_id, deposit_address)
        )

        logger.info(f"📋 创建充值订单 | 用户: {user_id} | 订单: {order_id} | 充值地址: {deposit_address[:12]}...")
        return {
            "order_id": order_id,
            "deposit_address": deposit_address,
            "status": "pending",
            "user_id": user_id,
        }

    async def get_pending_order_by_address(self, deposit_address: str) -> Optional[dict]:
        """通过充值地址获取待确认订单"""
        return await db.fetch_one(
            """
            SELECT * FROM recharge_orders
            WHERE deposit_address = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (deposit_address,)
        )

    async def confirm_order_by_address(
        self, deposit_address: str, amount: float, tx_hash: str, from_address: str = None
    ) -> Optional[dict]:
        """
        通过充值地址确认订单（HD 热钱包版）
        链上监听到转入热钱包的交易后调用

        Returns:
            确认的订单信息，或 None（无匹配/已确认）
        """
        # 检查 tx_hash 是否已被使用
        if tx_hash:
            existing = await db.fetch_one(
                "SELECT id FROM recharge_orders WHERE tx_hash = ? AND status IN ('confirmed', 'swept')",
                (tx_hash,)
            )
            if existing:
                logger.debug(f"tx_hash 已处理，跳过: {tx_hash[:20]}...")
                return None

        # 查找该地址的 pending 订单
        order = await self.get_pending_order_by_address(deposit_address)

        if order:
            # 有 pending 订单 — 更新它
            await db.execute(
                """
                UPDATE recharge_orders
                SET status = 'confirmed', amount = ?, tx_hash = ?,
                    from_address = ?, confirmed_at = datetime('now', 'localtime')
                WHERE order_id = ?
                """,
                (amount, tx_hash, from_address, order["order_id"])
            )
            order_id = order["order_id"]
        else:
            # 没有 pending 订单 — 用户可能直接转账（没走 /recharge 命令）
            # 也创建一条已确认的记录
            order_id = f"A{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
            # 通过地址反查 user_id
            from services.wallet import wallet_manager
            user_id = await wallet_manager.get_user_by_address(deposit_address)
            if not user_id:
                logger.warning(f"⚠️ 未知充值地址: {deposit_address}")
                return None

            await db.execute(
                """
                INSERT INTO recharge_orders
                    (user_id, order_id, amount, deposit_address, status, tx_hash, from_address, confirmed_at)
                VALUES (?, ?, ?, ?, 'confirmed', ?, ?, datetime('now', 'localtime'))
                """,
                (user_id, order_id, amount, deposit_address, tx_hash, from_address)
            )
            order = {"user_id": user_id, "order_id": order_id, "amount": amount}

        # 增加用户余额
        user_id = order.get("user_id") or (await self.get_pending_order_by_address(deposit_address) or {}).get("user_id")
        if user_id:
            await self.add_balance(user_id, amount, tx_hash)

        logger.info(f"✅ 充值确认 | 订单: {order_id} | 地址: {deposit_address[:12]}... | 金额: {amount} USDT | tx: {tx_hash[:20]}...")
        return dict(order) if isinstance(order, dict) else order

    async def mark_order_swept(self, order_id: str, sweep_tx_hash: str):
        """标记订单已归集"""
        await db.execute(
            """
            UPDATE recharge_orders
            SET status = 'swept', sweep_tx_hash = ?
            WHERE order_id = ?
            """,
            (sweep_tx_hash, order_id)
        )

    async def _expire_user_pending_orders(self, user_id: str):
        """过期用户之前未完成的订单"""
        await db.execute(
            """
            UPDATE recharge_orders
            SET status = 'expired', expired_at = datetime('now', 'localtime')
            WHERE user_id = ? AND status = 'pending'
            """,
            (user_id,)
        )

    async def expire_old_orders(self):
        """过期超时的订单"""
        result = await db.execute(
            """
            UPDATE recharge_orders
            SET status = 'expired', expired_at = datetime('now', 'localtime')
            WHERE status = 'pending'
              AND datetime(created_at, '+' || ? || ' seconds') < datetime('now', 'localtime')
            """,
            (RECHARGE_ORDER_EXPIRE,)
        )
        if result.rowcount > 0:
            logger.info(f"🕐 过期 {result.rowcount} 个超时充值订单")

    # ------------------------------------------------------------------
    # 记录查询
    # ------------------------------------------------------------------

    async def get_spend_history(self, user_id: str, limit: int = 20) -> list:
        """获取用户消费记录"""
        return await db.fetch_all(
            "SELECT * FROM spend_records WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )

    async def get_recharge_history(self, user_id: str, limit: int = 20) -> list:
        """获取用户充值记录"""
        return await db.fetch_all(
            """
            SELECT * FROM recharge_orders
            WHERE user_id = ? AND status IN ('confirmed', 'swept')
            ORDER BY confirmed_at DESC LIMIT ?
            """,
            (user_id, limit)
        )


# 全局单例
payment_manager = PaymentManager()
