"""
SQLite 数据库连接管理
- 单例连接池
- 自动建表
- WAL 模式（提升并发读写性能）
- 异步安全封装
"""

import sqlite3
import json
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 默认数据库路径
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "elena.db"

def _get_db_path() -> str:
    """从环境变量或配置获取数据库路径"""
    custom_path = os.getenv("DATABASE_PATH", "").strip()
    if custom_path:
        return custom_path
    return str(DEFAULT_DB_PATH)


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _get_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

        # 确保数据目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（懒初始化）"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,   # 允许多线程访问
                timeout=30.0,              # 等待锁的超时（秒）
            )
            # 返回 dict-like 的 Row 对象
            self._conn.row_factory = sqlite3.Row
            # 开启 WAL 模式：读写并发不互斥
            self._conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL 同步模式：比 FULL 快，WAL 下仍然安全
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # 开启外键约束
            self._conn.execute("PRAGMA foreign_keys=ON")
            # 设置缓存大小（约 8MB）
            self._conn.execute("PRAGMA cache_size=-8000")
            # 将临时表存储在内存中
            self._conn.execute("PRAGMA temp_store=MEMORY")
            # 设置 mmap 大小（64MB），加速读取
            self._conn.execute("PRAGMA mmap_size=67108864")
            logger.info(f"✅ SQLite 连接已建立 | 路径: {self.db_path}")
        return self._conn

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("🔒 SQLite 连接已关闭")

    # ------------------------------------------------------------------
    # 初始化：建表
    # ------------------------------------------------------------------

    def init_tables(self):
        """创建所有表（幂等操作，可重复执行）"""
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES_SQL)
        conn.commit()
        logger.info("✅ 数据库表初始化完成")

    # ------------------------------------------------------------------
    # 异步执行封装
    # ------------------------------------------------------------------

    async def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """异步执行写操作（自带锁）"""
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._execute_sync, sql, params
            )

    async def execute_many(self, sql: str, params_list: List[tuple]) -> None:
        """批量执行"""
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._execute_many_sync, sql, params_list
            )

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """查询单行"""
        loop = asyncio.get_event_loop()
        row = await loop.run_in_executor(
            None, self._fetch_one_sync, sql, params
        )
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> List[dict]:
        """查询多行"""
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._fetch_all_sync, sql, params
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 同步执行（供 run_in_executor 调用）
    # ------------------------------------------------------------------

    def _execute_sync(self, sql: str, params: tuple) -> sqlite3.Cursor:
        conn = self._get_conn()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor

    def _execute_many_sync(self, sql: str, params_list: List[tuple]) -> None:
        conn = self._get_conn()
        conn.executemany(sql, params_list)
        conn.commit()

    def _fetch_one_sync(self, sql: str, params: tuple) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        return conn.execute(sql, params).fetchone()

    def _fetch_all_sync(self, sql: str, params: tuple) -> List[sqlite3.Row]:
        conn = self._get_conn()
        return conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------
    # 同步接口（供非 async 上下文使用，如 /forget 命令）
    # ------------------------------------------------------------------

    def execute_sync(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """同步执行写操作"""
        conn = self._get_conn()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor

    def fetch_one_sync(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """同步查询单行"""
        conn = self._get_conn()
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all_sync(self, sql: str, params: tuple = ()) -> List[dict]:
        """同步查询多行"""
        conn = self._get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


# ======================================================================
# 建表 SQL
# ======================================================================

_CREATE_TABLES_SQL = """

-- =====================================================================
-- 用户记忆表：存储 Elena 对用户的长期记忆
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_memories (
    user_id         TEXT PRIMARY KEY,
    user_name       TEXT DEFAULT '朋友',
    memory_data     TEXT NOT NULL DEFAULT '{}',   -- JSON 格式的完整记忆
    conversation_count  INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================================================
-- 对话缓冲表：暂存用户对话，用于定期提取记忆
-- =====================================================================
CREATE TABLE IF NOT EXISTS conversation_buffer (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    role            TEXT NOT NULL,                -- 'user' 或 'assistant'
    content         TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_conv_buffer_user ON conversation_buffer(user_id);

-- =====================================================================
-- 记忆提取记录：记录每次提取的时间，控制提取频率
-- =====================================================================
CREATE TABLE IF NOT EXISTS extraction_log (
    user_id         TEXT PRIMARY KEY,
    last_extraction TEXT DEFAULT (datetime('now', 'localtime')),
    extraction_count INTEGER DEFAULT 0
);

-- =====================================================================
-- 塔罗占卜历史：持久化存储用户的占卜记录
-- =====================================================================
CREATE TABLE IF NOT EXISTS tarot_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    question        TEXT NOT NULL,
    cards           TEXT NOT NULL,                -- JSON: [{"position":"过去","card":"...","meaning":"..."}]
    interpretation  TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tarot_user ON tarot_readings(user_id);
CREATE INDEX IF NOT EXISTS idx_tarot_user_id_desc ON tarot_readings(user_id, id DESC);

-- =====================================================================
-- 群组数据表：群组运势
-- =====================================================================
CREATE TABLE IF NOT EXISTS group_fortunes (
    group_id        TEXT NOT NULL,
    fortune_date    TEXT NOT NULL,                -- YYYY-MM-DD
    fortune_data    TEXT NOT NULL,                -- JSON
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (group_id, fortune_date)
);

-- =====================================================================
-- 群组排行榜：每日占卜排行
-- =====================================================================
CREATE TABLE IF NOT EXISTS group_rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    user_name       TEXT NOT NULL,
    positive_count  INTEGER DEFAULT 0,
    cards           TEXT NOT NULL,                -- JSON: ["card1", "card2", ...]
    ranking_date    TEXT NOT NULL,                -- YYYY-MM-DD
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(group_id, user_id, ranking_date)
);
CREATE INDEX IF NOT EXISTS idx_ranking_group_date ON group_rankings(group_id, ranking_date);

-- =====================================================================
-- PK 对战记录
-- =====================================================================
CREATE TABLE IF NOT EXISTS pk_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT NOT NULL,
    user1_id        TEXT NOT NULL,
    user1_name      TEXT NOT NULL,
    user1_cards     TEXT NOT NULL,                -- JSON
    user1_score     INTEGER NOT NULL,
    user2_id        TEXT NOT NULL,
    user2_name      TEXT NOT NULL,
    user2_cards     TEXT NOT NULL,                -- JSON
    user2_score     INTEGER NOT NULL,
    winner_id       TEXT,                         -- NULL = 平局
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pk_group ON pk_records(group_id);

-- =====================================================================
-- 用户余额：USDT 充值余额
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_balances (
    user_id         TEXT PRIMARY KEY,
    balance         REAL DEFAULT 0.0,
    total_recharged REAL DEFAULT 0.0,
    total_spent     REAL DEFAULT 0.0,
    updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================================================
-- 用户热钱包：HD 派生的用户专属充值地址
-- =====================================================================
CREATE TABLE IF NOT EXISTS user_wallets (
    user_id         TEXT PRIMARY KEY,
    wallet_index    INTEGER UNIQUE NOT NULL,      -- HD 派生索引
    address         TEXT UNIQUE NOT NULL,          -- BSC 地址（0x...）
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================================================
-- 充值订单：链上 USDT 充值记录
-- =====================================================================
CREATE TABLE IF NOT EXISTS recharge_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    order_id        TEXT UNIQUE NOT NULL,
    amount          REAL NOT NULL,
    deposit_address TEXT,                          -- 用户充值的热钱包地址
    status          TEXT DEFAULT 'pending',        -- pending / confirmed / expired / swept
    tx_hash         TEXT,
    sweep_tx_hash   TEXT,                          -- 归集交易哈希
    from_address    TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    confirmed_at    TEXT,
    expired_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_recharge_user ON recharge_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_recharge_status ON recharge_orders(status);
CREATE INDEX IF NOT EXISTS idx_recharge_deposit ON recharge_orders(deposit_address);

-- =====================================================================
-- 消费记录：高级功能扣费流水
-- =====================================================================
CREATE TABLE IF NOT EXISTS spend_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    feature         TEXT NOT NULL,                -- 'tarot_detail' / 'ai_chat' / 'tarot_reading'
    amount          REAL NOT NULL,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_spend_user ON spend_records(user_id);

-- =====================================================================
-- 每日免费用量追踪
-- =====================================================================
CREATE TABLE IF NOT EXISTS daily_usage (
    user_id         TEXT NOT NULL,
    usage_date      TEXT NOT NULL,                -- YYYY-MM-DD
    tarot_count     INTEGER DEFAULT 0,
    chat_count      INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);

"""


# ======================================================================
# 全局单例
# ======================================================================

db = Database()
