"""
HD 热钱包服务

职责：
- 从助记词派生用户专属 BSC 充值地址（BIP-44）
- 为每个用户分配唯一的派生索引，持久化到 user_wallets 表
- 构造并签名 USDT 归集交易（热钱包 → 冷钱包）

安全说明：
- 助记词存在 .env 环境变量中，不落库
- 子钱包私钥不持久化，每次需要时实时从助记词+index派生
- 冷钱包私钥不接触代码，归集只从热钱包单向转出
"""

import logging
from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount

from db.database import db
from config import HD_MNEMONIC, BSC_WALLET_ADDRESS, BSC_USDT_CONTRACT

logger = logging.getLogger(__name__)

# 启用 HD 钱包功能（eth-account 要求显式启用）
Account.enable_unaudited_hdwallet_features()

# BSC 链 ID
BSC_CHAIN_ID = 56

# ERC-20 transfer(address,uint256) 函数选择器
ERC20_TRANSFER_SELECTOR = "0xa9059cbb"

# USDT 精度（BSC 上是 18 位）
USDT_DECIMALS = 18


class WalletManager:
    """HD 热钱包管理器"""

    def __init__(self):
        self._mnemonic = HD_MNEMONIC
        # 地址 → user_id 的内存缓存（启动时从 DB 加载）
        self._address_to_user: dict[str, str] = {}

    # ------------------------------------------------------------------
    # HD 派生
    # ------------------------------------------------------------------

    def _derive_account(self, index: int) -> LocalAccount:
        """从助记词派生第 index 个子钱包账户"""
        if not self._mnemonic:
            raise RuntimeError("HD_MNEMONIC 未配置，无法派生钱包")
        return Account.from_mnemonic(
            self._mnemonic,
            account_path=f"m/44'/60'/0'/0/{index}"
        )

    def derive_address(self, index: int) -> str:
        """派生第 index 个地址（不暴露私钥）"""
        acct = self._derive_account(index)
        return acct.address

    def get_private_key(self, index: int) -> str:
        """获取第 index 个子钱包的私钥（仅归集时使用）"""
        acct = self._derive_account(index)
        return acct.key.hex()

    # ------------------------------------------------------------------
    # 用户地址分配
    # ------------------------------------------------------------------

    async def get_or_create_wallet(self, user_id: str) -> dict:
        """
        获取或创建用户的专属充值钱包
        返回 {"user_id", "wallet_index", "address"}
        """
        # 1. 查询已有钱包
        existing = await db.fetch_one(
            "SELECT * FROM user_wallets WHERE user_id = ?",
            (user_id,)
        )
        if existing:
            return dict(existing)

        # 2. 分配新索引（取当前最大 index + 1）
        max_row = await db.fetch_one(
            "SELECT MAX(wallet_index) as max_idx FROM user_wallets"
        )
        next_index = (max_row["max_idx"] or -1) + 1 if max_row else 0

        # 3. 派生地址
        address = self.derive_address(next_index)

        # 4. 持久化
        await db.execute(
            """
            INSERT INTO user_wallets (user_id, wallet_index, address)
            VALUES (?, ?, ?)
            """,
            (user_id, next_index, address)
        )

        # 5. 更新缓存
        self._address_to_user[address.lower()] = user_id

        logger.info(f"🔑 新钱包创建 | 用户: {user_id} | index: {next_index} | 地址: {address}")
        return {
            "user_id": user_id,
            "wallet_index": next_index,
            "address": address,
        }

    async def get_user_by_address(self, address: str) -> Optional[str]:
        """通过热钱包地址查找对应的 user_id"""
        addr_lower = address.lower()

        # 先查缓存
        if addr_lower in self._address_to_user:
            return self._address_to_user[addr_lower]

        # 查 DB
        row = await db.fetch_one(
            "SELECT user_id FROM user_wallets WHERE LOWER(address) = ?",
            (addr_lower,)
        )
        if row:
            self._address_to_user[addr_lower] = row["user_id"]
            return row["user_id"]

        return None

    async def get_wallet_by_user(self, user_id: str) -> Optional[dict]:
        """通过 user_id 查找钱包信息"""
        row = await db.fetch_one(
            "SELECT * FROM user_wallets WHERE user_id = ?",
            (user_id,)
        )
        return dict(row) if row else None

    async def get_all_addresses(self) -> set[str]:
        """获取所有热钱包地址集合（小写）"""
        rows = await db.fetch_all("SELECT address FROM user_wallets")
        addresses = {row["address"].lower() for row in rows}
        # 同步更新缓存
        for row in rows:
            if row["address"].lower() not in self._address_to_user:
                user_row = await db.fetch_one(
                    "SELECT user_id FROM user_wallets WHERE address = ?",
                    (row["address"],)
                )
                if user_row:
                    self._address_to_user[row["address"].lower()] = user_row["user_id"]
        return addresses

    async def load_cache(self):
        """启动时加载所有地址映射到缓存"""
        rows = await db.fetch_all("SELECT user_id, address FROM user_wallets")
        for row in rows:
            self._address_to_user[row["address"].lower()] = row["user_id"]
        if rows:
            logger.info(f"🔑 已加载 {len(rows)} 个热钱包地址到缓存")

    # ------------------------------------------------------------------
    # 归集交易构造
    # ------------------------------------------------------------------

    def build_sweep_tx(
        self,
        wallet_index: int,
        usdt_amount_wei: int,
        nonce: int,
        gas_price: int,
    ) -> str:
        """
        构造并签名 USDT 归集交易（热钱包 → 冷钱包）

        Args:
            wallet_index: 热钱包的 HD 派生索引
            usdt_amount_wei: 归集的 USDT 数量（wei 单位，即 amount * 10^18）
            nonce: 热钱包地址的当前 nonce
            gas_price: Gas 价格（wei）

        Returns:
            签名后的原始交易十六进制字符串（可直接广播）
        """
        if not BSC_WALLET_ADDRESS:
            raise RuntimeError("BSC_WALLET_ADDRESS（冷钱包）未配置")

        # 构造 ERC-20 transfer 调用数据
        # transfer(address to, uint256 amount)
        to_padded = BSC_WALLET_ADDRESS.lower().replace("0x", "").zfill(64)
        amount_padded = hex(usdt_amount_wei)[2:].zfill(64)
        data = ERC20_TRANSFER_SELECTOR + to_padded + amount_padded

        # 构造交易
        tx = {
            "to": BSC_USDT_CONTRACT,
            "value": 0,
            "gas": 60000,  # ERC-20 transfer 通常消耗 ~45000 gas
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": BSC_CHAIN_ID,
            "data": data,
        }

        # 用子钱包私钥签名
        acct = self._derive_account(wallet_index)
        signed = acct.sign_transaction(tx)
        return signed.raw_transaction.hex()


# 全局单例
wallet_manager = WalletManager()
