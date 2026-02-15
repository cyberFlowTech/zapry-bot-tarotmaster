import os
from dotenv import load_dotenv

load_dotenv()


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ===== TG 平台切换（快速切换 Telegram / Zapry）=====
# telegram : 使用官方 Telegram API
# zapry    : 使用 Zapry 私有化 TG 服务
TG_PLATFORM = os.getenv("TG_PLATFORM", "telegram").strip().lower()
if TG_PLATFORM not in {"telegram", "zapry"}:
    TG_PLATFORM = "telegram"

# ===== Telegram 基础配置 =====
# 根据平台自动选择对应的配置
if TG_PLATFORM == "zapry":
    BOT_TOKEN = os.getenv("ZAPRY_BOT_TOKEN")
    TELEGRAM_API_BASE_URL = os.getenv("ZAPRY_API_BASE_URL", "https://openapi.mimo.immo/bot").strip()
else:  # telegram
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_API_BASE_URL = ""  # 官方 API 不需要自定义 base_url

# ===== 运行模式（只改这一项）=====
# webhook   : 平台回调到你的服务（推荐线上）
# temporary : 本地临时调试（polling + 可选 hello 页面）
RUNTIME_MODE = os.getenv("RUNTIME_MODE", "webhook").strip().lower()
if RUNTIME_MODE not in {"webhook", "temporary"}:
    RUNTIME_MODE = "webhook"

# ===== Webhook 配置（RUNTIME_MODE=webhook 时生效）=====
# 根据平台自动选择对应的 Webhook URL
if TG_PLATFORM == "zapry":
    WEBHOOK_URL = os.getenv("ZAPRY_WEBHOOK_URL", "").strip()
else:  # telegram
    WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "").strip()
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0").strip()
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8443"))
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()

# ===== 临时调试页面（hello world）=====
HELLO_WORLD_ENABLED = _to_bool(os.getenv("HELLO_WORLD_ENABLED"), default=False)
HELLO_WORLD_PORT = int(os.getenv("HELLO_WORLD_PORT", "8080"))
HELLO_WORLD_TEXT = os.getenv("HELLO_WORLD_TEXT", "hello world")

# ===== 数据库配置 =====
DATABASE_PATH = os.getenv("DATABASE_PATH", "").strip()  # 为空则使用默认路径 data/elena.db

# ===== 其他配置 =====
DEBUG = _to_bool(os.getenv("DEBUG"), default=False)
LOG_FILE = os.getenv("LOG_FILE", "").strip()

# ===== OpenAI 配置 =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()  # 如果使用国内中转，填写中转地址
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()  # 默认使用 gpt-4o-mini

# ===== USDT 充值 & 付费功能配置 =====
# BSC 链上收款（冷钱包 — 归集目标地址）
BSC_WALLET_ADDRESS = os.getenv("BSC_WALLET_ADDRESS", "").strip()  # 冷钱包地址（归集目标）
BSC_USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"   # USDT BEP-20 合约地址（固定）
CHAIN_POLL_INTERVAL = int(os.getenv("CHAIN_POLL_INTERVAL", "30"))  # 链上轮询间隔（秒）
RECHARGE_ORDER_EXPIRE = int(os.getenv("RECHARGE_ORDER_EXPIRE", "3600"))  # 充值订单过期时间（秒）

# HD 热钱包（为每个用户派生专属充值地址）
HD_MNEMONIC = os.getenv("HD_MNEMONIC", "").strip()                # HD 钱包助记词（务必保密！）
SWEEP_GAS_RESERVE_BNB = float(os.getenv("SWEEP_GAS_RESERVE_BNB", "0.001"))  # 归集时预留 Gas

# 定价（USDT）
PRICE_TAROT_DETAIL = float(os.getenv("PRICE_TAROT_DETAIL", "0.5"))    # 深度解读单价
PRICE_TAROT_READING = float(os.getenv("PRICE_TAROT_READING", "0.3"))  # 超额塔罗占卜单价
PRICE_AI_CHAT = float(os.getenv("PRICE_AI_CHAT", "0.1"))             # 超额 AI 对话单价

# 每日免费额度
FREE_TAROT_DAILY = int(os.getenv("FREE_TAROT_DAILY", "1"))   # 每天免费塔罗占卜次数
FREE_CHAT_DAILY = int(os.getenv("FREE_CHAT_DAILY", "10"))    # 每天免费 AI 对话次数

# 管理员 ID（可手动充值、查询余额等）
ADMIN_USER_IDS = [uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]


def get_current_config_summary() -> str:
    """返回当前配置摘要，方便调试"""
    wallet_display = f"{BSC_WALLET_ADDRESS[:10]}...{BSC_WALLET_ADDRESS[-6:]}" if BSC_WALLET_ADDRESS else "未配置"
    return f"""
==================== 当前配置 ====================
TG 平台: {TG_PLATFORM.upper()}
Bot Token: {BOT_TOKEN[:20]}... (已隐藏)
API Base URL: {TELEGRAM_API_BASE_URL or '官方API'}
运行模式: {RUNTIME_MODE.upper()}
Webhook URL: {WEBHOOK_URL[:50]}... (已截断) if WEBHOOK_URL else '未配置'
监听端口: {WEBAPP_PORT}
OpenAI Model: {OPENAI_MODEL}
OpenAI Base URL: {OPENAI_BASE_URL or '官方API'}
--- 付费功能 ---
BSC 冷钱包: {wallet_display}
HD 钱包: {'已配置' if HD_MNEMONIC else '未配置'}
定价: 深度解读 {PRICE_TAROT_DETAIL} / 塔罗 {PRICE_TAROT_READING} / 对话 {PRICE_AI_CHAT} USDT
免费额度: 塔罗 {FREE_TAROT_DAILY}次/天, 对话 {FREE_CHAT_DAILY}次/天
================================================
"""

