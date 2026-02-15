"""
BSC 链上 USDT 交易监听服务（HD 热钱包版）

使用 BSC 公共 RPC 端点（免费，无需 API Key）直接查询链上事件，
不依赖 BscScan/Etherscan 付费 API。

工作流程：
1. 启动时加载所有用户热钱包地址到缓存
2. 定时通过 eth_getLogs 查询 USDT Transfer 事件
3. 筛选 to_address 属于用户热钱包的交易
4. to_address 直接映射用户 → 零碰撞确认到账
5. 到账后触发归集：签名 USDT transfer 到冷钱包并广播
"""

import asyncio
import logging
from typing import Optional

import httpx

from config import (
    BSC_WALLET_ADDRESS,
    BSC_USDT_CONTRACT,
    CHAIN_POLL_INTERVAL,
    HD_MNEMONIC,
)
from services.payment import payment_manager
from services.wallet import wallet_manager

logger = logging.getLogger(__name__)

# BSC 公共 RPC 端点（免费，无需 API Key）
# 备用列表：如果主节点不稳定，自动切换
BSC_RPC_ENDPOINTS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://bsc.publicnode.com",
]

# ERC-20 Transfer 事件签名：Transfer(address,address,uint256)
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

USDT_DECIMALS = 18


class ChainMonitor:
    """BSC 链上 USDT 交易监听（HD 热钱包版，RPC 直连）"""

    def __init__(self):
        self._running = False
        self._last_block = 0
        self._task: Optional[asyncio.Task] = None
        self._bot = None
        self._processed_hashes: set[str] = set()
        self._rpc_index = 0  # 当前使用的 RPC 端点索引

    def set_bot(self, bot):
        self._bot = bot

    @property
    def _rpc_url(self) -> str:
        return BSC_RPC_ENDPOINTS[self._rpc_index % len(BSC_RPC_ENDPOINTS)]

    def _rotate_rpc(self):
        """切换到下一个 RPC 端点"""
        self._rpc_index = (self._rpc_index + 1) % len(BSC_RPC_ENDPOINTS)
        logger.info(f"🔄 切换 RPC 端点: {self._rpc_url}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self):
        if not HD_MNEMONIC:
            logger.warning("⚠️ HD_MNEMONIC 未配置，链上监听未启动")
            return

        if self._running:
            return

        await wallet_manager.load_cache()

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"✅ BSC 链上监听已启动 | RPC: {self._rpc_url} | "
            f"冷钱包: {BSC_WALLET_ADDRESS[:10] + '...' if BSC_WALLET_ADDRESS else '未配置'} | "
            f"间隔: {CHAIN_POLL_INTERVAL}s"
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 BSC 链上监听已停止")

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _poll_loop(self):
        self._last_block = await self._get_block_number()
        if self._last_block > 0:
            # 回溯 200 个区块（约 10 分钟）
            self._last_block = max(0, self._last_block - 200)
            logger.info(f"📦 链上监听起始区块: {self._last_block}")

        while self._running:
            try:
                await self._check_new_transfers()
                await payment_manager.expire_old_orders()
            except Exception as e:
                logger.error(f"❌ 链上监听异常: {e}", exc_info=True)
            await asyncio.sleep(CHAIN_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # 交易检测（eth_getLogs）
    # ------------------------------------------------------------------

    async def _check_new_transfers(self):
        """查询 USDT Transfer 事件，筛选转入热钱包的交易"""
        current_block = await self._get_block_number()
        if current_block <= self._last_block:
            return

        hot_addresses = await wallet_manager.get_all_addresses()
        if not hot_addresses:
            self._last_block = current_block
            return

        # 查询区间不超过 5000 块（RPC 限制）
        from_block = self._last_block + 1
        to_block = min(current_block, from_block + 4999)

        # 通过 eth_getLogs 查询 USDT Transfer 事件
        logs = await self._get_transfer_logs(from_block, to_block)
        self._last_block = to_block

        if not logs:
            return

        matched_count = 0
        for log in logs:
            tx_hash = log.get("transactionHash", "")
            if not tx_hash or tx_hash in self._processed_hashes:
                continue

            # Transfer 事件的 topics:
            # topics[0] = Transfer 事件签名
            # topics[1] = from 地址（左补零到 32 字节）
            # topics[2] = to 地址（左补零到 32 字节）
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            to_addr = "0x" + topics[2][-40:]  # 取最后 20 字节
            to_addr_lower = to_addr.lower()

            if to_addr_lower not in hot_addresses:
                continue

            # 解析转账金额
            data = log.get("data", "0x0")
            amount = self._parse_log_amount(data)
            if amount <= 0:
                continue

            from_addr = "0x" + topics[1][-40:]

            # 通过地址确认到账
            confirmed = await payment_manager.confirm_order_by_address(
                deposit_address=to_addr_lower,
                amount=amount,
                tx_hash=tx_hash,
                from_address=from_addr,
            )

            if confirmed:
                matched_count += 1
                self._processed_hashes.add(tx_hash)
                user_id = confirmed.get("user_id", "?")
                logger.info(
                    f"✅ 热钱包到账 | 用户: {user_id} | 地址: {to_addr[:12]}... | "
                    f"金额: {amount} USDT | tx: {tx_hash[:20]}..."
                )
                await self._notify_user(user_id, amount, tx_hash)
                asyncio.create_task(self._sweep_to_cold(to_addr_lower, amount, tx_hash))

        if matched_count > 0:
            logger.info(f"🔗 本轮确认 {matched_count} 笔充值")

        # 防止缓存无限增长
        if len(self._processed_hashes) > 10000:
            self._processed_hashes = set(list(self._processed_hashes)[-5000:])

    # ------------------------------------------------------------------
    # 自动归集（热钱包 → 冷钱包）
    # ------------------------------------------------------------------

    async def _sweep_to_cold(self, hot_address: str, usdt_amount: float, deposit_tx_hash: str):
        """将热钱包中的 USDT 归集到冷钱包"""
        try:
            if not BSC_WALLET_ADDRESS:
                logger.warning("⚠️ 冷钱包地址未配置，跳过归集")
                return

            user_id = await wallet_manager.get_user_by_address(hot_address)
            if not user_id:
                logger.error(f"❌ 未找到热钱包用户: {hot_address}")
                return

            wallet = await wallet_manager.get_wallet_by_user(user_id)
            if not wallet:
                logger.error(f"❌ 未找到热钱包信息: {hot_address}")
                return

            wallet_index = wallet["wallet_index"]
            wallet_address = wallet["address"]

            # 获取 nonce 和 gas price
            nonce = await self._get_nonce(wallet_address)
            gas_price = await self._get_gas_price()

            if nonce is None or gas_price is None:
                logger.error("❌ 获取 nonce/gasPrice 失败，跳过归集")
                return

            # 检查 BNB 余额
            bnb_balance = await self._get_balance(wallet_address)
            gas_needed = 60000 * gas_price
            if bnb_balance < gas_needed:
                logger.warning(
                    f"⚠️ 热钱包 BNB 不足 | 地址: {wallet_address[:12]}... | "
                    f"BNB: {bnb_balance / 1e18:.6f} | 需要: {gas_needed / 1e18:.6f}"
                )
                return

            usdt_amount_wei = int(usdt_amount * (10 ** USDT_DECIMALS))

            # 签名归集交易
            raw_tx = wallet_manager.build_sweep_tx(
                wallet_index=wallet_index,
                usdt_amount_wei=usdt_amount_wei,
                nonce=nonce,
                gas_price=gas_price,
            )

            # 广播
            sweep_tx_hash = await self._send_raw_transaction(raw_tx)
            if sweep_tx_hash:
                logger.info(
                    f"✅ 归集成功 | {wallet_address[:12]}... → 冷钱包 | "
                    f"金额: {usdt_amount} USDT | sweep_tx: {sweep_tx_hash[:20]}..."
                )
                from db.database import db
                order = await db.fetch_one(
                    """SELECT * FROM recharge_orders
                       WHERE deposit_address = ? AND tx_hash = ?
                       ORDER BY confirmed_at DESC LIMIT 1""",
                    (hot_address, deposit_tx_hash)
                )
                if order:
                    await payment_manager.mark_order_swept(order["order_id"], sweep_tx_hash)
            else:
                logger.error(f"❌ 归集交易广播失败 | 地址: {wallet_address[:12]}...")

        except Exception as e:
            logger.error(f"❌ 归集异常 | 地址: {hot_address[:12]}... | 错误: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # BSC RPC 调用（JSON-RPC 标准接口，免费无需 API Key）
    # ------------------------------------------------------------------

    async def _rpc_call(self, method: str, params: list) -> Optional[dict]:
        """通用 JSON-RPC 调用，带自动重试和端点切换"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        for attempt in range(len(BSC_RPC_ENDPOINTS)):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(self._rpc_url, json=payload)
                    data = resp.json()
                if "error" in data:
                    logger.warning(f"RPC 错误 ({self._rpc_url}): {data['error']}")
                    self._rotate_rpc()
                    continue
                return data
            except Exception as e:
                logger.warning(f"RPC 请求失败 ({self._rpc_url}): {e}")
                self._rotate_rpc()
        logger.error(f"❌ 所有 RPC 端点均失败 | method: {method}")
        return None

    async def _get_block_number(self) -> int:
        data = await self._rpc_call("eth_blockNumber", [])
        if data and "result" in data:
            return int(data["result"], 16)
        return 0

    async def _get_transfer_logs(self, from_block: int, to_block: int) -> list:
        """查询 USDT 合约的 Transfer 事件日志"""
        filter_params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": BSC_USDT_CONTRACT,
            "topics": [TRANSFER_EVENT_TOPIC],
        }
        data = await self._rpc_call("eth_getLogs", [filter_params])
        if data and "result" in data:
            return data["result"]
        return []

    async def _get_nonce(self, address: str) -> Optional[int]:
        data = await self._rpc_call("eth_getTransactionCount", [address, "latest"])
        if data and "result" in data:
            return int(data["result"], 16)
        return None

    async def _get_gas_price(self) -> Optional[int]:
        data = await self._rpc_call("eth_gasPrice", [])
        if data and "result" in data:
            return int(data["result"], 16)
        return None

    async def _get_balance(self, address: str) -> int:
        """获取 BNB 余额（wei）"""
        data = await self._rpc_call("eth_getBalance", [address, "latest"])
        if data and "result" in data:
            return int(data["result"], 16)
        return 0

    async def _send_raw_transaction(self, raw_tx_hex: str) -> Optional[str]:
        """广播签名交易"""
        hex_data = raw_tx_hex if raw_tx_hex.startswith("0x") else f"0x{raw_tx_hex}"
        data = await self._rpc_call("eth_sendRawTransaction", [hex_data])
        if data and "result" in data:
            result = data["result"]
            if isinstance(result, str) and result.startswith("0x") and len(result) == 66:
                return result
        if data and "error" in data:
            logger.error(f"❌ 交易广播失败: {data['error']}")
        return None

    def _parse_log_amount(self, data_hex: str) -> float:
        """解析 Transfer 事件的 data 字段（金额）"""
        try:
            return int(data_hex, 16) / (10 ** USDT_DECIMALS)
        except (ValueError, TypeError):
            return 0.0

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------

    async def _notify_user(self, user_id: str, amount: float, tx_hash: str):
        if not self._bot:
            return
        balance = await payment_manager.get_balance(user_id)
        text = (
            f"🎉 充值到账通知\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"💰 充值金额：{amount:.6f} USDT\n"
            f"💎 当前余额：{balance:.4f} USDT\n\n"
            f"🔗 交易哈希：\n{tx_hash}\n\n"
            f"充值已到账，你可以使用高级功能啦~ ✨\n\n"
            f"• 📖 深度解读\n"
            f"• 🎴 无限占卜\n"
            f"• 💬 无限对话\n\n"
            f"— Elena 🌿"
        )
        try:
            await self._bot.send_message(chat_id=int(user_id), text=text)
        except Exception as e:
            logger.error(f"❌ 发送到账通知失败 | 用户: {user_id} | 错误: {e}")


# 全局单例
chain_monitor = ChainMonitor()
