"""
自然语言意图识别路由器
通过 LLM 将用户的自然语言转化为可执行的命令意图
"""

import json
import logging
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL

logger = logging.getLogger(__name__)

# 意图识别专用模型（轻量、快速、低成本）
INTENT_MODEL = "gpt-4o-mini"

# 意图识别 System Prompt
INTENT_SYSTEM_PROMPT = """你是一个意图分类器。根据用户发送给塔罗牌解读师"林晚晴"的消息，判断用户的意图。

可能的意图如下：
- tarot：用户想要占卜/算卦/测运势/塔罗（需提取占卜问题）
- tarot_history：用户想查看自己的占卜历史/记录
- memory：用户想查看林晚晴记住了什么关于自己的信息
- forget：用户想让林晚晴忘记/清除关于自己的记忆
- clear_history：用户想清除聊天记录/对话历史
- luck：用户想看今日运势/今天运气/每日能量
- fortune：用户想快速求一个指引（类似简短占卜，不是完整塔罗）
- intro：用户想了解林晚晴是谁
- help：用户想知道有哪些功能/怎么用
- recharge：用户想充值/购买/付费
- balance：用户想查看余额/账户/剩余次数
- chat：普通聊天、倾诉、闲聊（默认）

判断规则（非常重要）：
1. 只有用户**明确表达了想做某件事**时，才返回对应意图
2. 如果用户只是在倾诉或闲聊，一律返回 chat
3. "最近感情不太好" → chat（倾诉，不是要占卜）
4. "帮我看看感情运" → tarot（明确要占卜）
5. "测一下事业" → tarot（明确要占卜）
6. "帮我占卜一下" → tarot
7. "帮我算一卦" → tarot
8. "我好难过" → chat（倾诉）
9. "你记得我吗" → memory（想看记忆）
10. "你是谁啊" → intro（想了解林晚晴）
11. "你能做什么" → help（想知道功能）
12. "忘了我吧" → forget（想清除记忆）
13. "今天运势怎么样" → luck（今日运势）
14. "今天运气好不好" → luck
15. "看看我的占卜记录" → tarot_history（查看历史）
16. "给我一个指引" → fortune（快速求问）
17. "我想充值" → recharge（充值）
18. "怎么付费" → recharge（充值）
19. "还有多少余额" → balance（查余额）
20. "还剩几次" → balance（查余额）
21. 宁可返回 chat 也不要误判！模棱两可时一定返回 chat

对于 tarot 意图，需要提取用户想问的具体问题（query 字段）。
- "帮我测测爱情" → query="爱情运势"
- "占卜一下事业发展" → query="事业发展"
- "帮我算一卦" → query="综合运势"（没有具体方向时用这个）

输出严格 JSON 格式，不要输出任何其他内容：
{"intent": "xxx", "query": "xxx"}

query 字段只在 intent=tarot 或 intent=fortune 时需要填写有意义的内容，其他意图 query 设为空字符串 ""。"""


import re

# 关键词快速匹配表（避免简单场景也调用 LLM）
_KEYWORD_PATTERNS = [
    # (pattern, intent, query_extractor)
    (re.compile(r"^(帮我|给我|来|想)(占卜|测|算|看看|抽[一]?[张个]?牌)(.{0,30})$"), "tarot", lambda m: m.group(3).strip() or "综合运势"),
    (re.compile(r"^(测一?[下测]|占卜一?下|算[一]?卦|塔罗)(.{0,30})$"), "tarot", lambda m: m.group(2).strip() or "综合运势"),
    (re.compile(r"(占卜|塔罗|测一?[下测]|算[一]?卦).{0,5}(感情|爱情|事业|工作|财运|学业|运势|健康)"), "tarot", lambda m: m.group(2) + "运势"),
    (re.compile(r"(感情|爱情|事业|工作|财运|学业|运势|健康).{0,5}(占卜|塔罗|测|算|运势)"), "tarot", lambda m: m.group(1) + "运势"),
    (re.compile(r"^(今[天日]|每日).{0,3}(运势|运气|能量)"), "luck", None),
    (re.compile(r"^(看看|查看|翻翻).{0,3}(占卜|塔罗).{0,3}(记录|历史)"), "tarot_history", None),
    (re.compile(r"^(你|晚晴).{0,3}(记得|记住|知道).{0,5}(我|关于我)"), "memory", None),
    (re.compile(r"^(忘[了掉]我|清除.{0,3}记忆|别记我)"), "forget", None),
    (re.compile(r"^(清[除空]|删除).{0,3}(聊天|对话|消息).{0,3}(记录|历史)"), "clear_history", None),
    (re.compile(r"^你是谁"), "intro", None),
    (re.compile(r"^(有什么功能|怎么用|能做什么|帮助|功能列表)"), "help", None),
    (re.compile(r"(充值|充钱|付费|购买|买|开通|解锁).{0,5}(USDT|会员|高级|功能)?"), "recharge", None),
    (re.compile(r"(余额|账户|剩余|还[有剩]几次|用量|额度)"), "balance", None),
]

# 明显是普通聊天的模式（直接短路，不调 LLM）
_CHAT_SHORTCUTS = [
    re.compile(r"^.{1,4}$"),  # 极短消息（"嗯""好的""哈哈"等）大概率是闲聊
    re.compile(r"^(嗯|好的|哈哈|哈|ok|OK|好|谢谢|对|是的|明白|知道了|了解|收到)[\s!！.。~]*$"),
    re.compile(r"^(早|早安|午安|晚安|你好|hi|hello|hey)[\s!！~]*$", re.IGNORECASE),
]


class IntentRouter:
    """自然语言意图识别路由器"""

    def __init__(self):
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """初始化异步 OpenAI 客户端"""
        try:
            if OPENAI_BASE_URL:
                self.client = AsyncOpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url=OPENAI_BASE_URL
                )
            else:
                self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            logger.info("✅ 意图识别路由器初始化成功")
        except Exception as e:
            logger.error(f"❌ 意图识别路由器初始化失败: {e}")
            self.client = None

    async def detect(self, message: str) -> dict:
        """
        识别用户消息的意图

        Args:
            message: 用户的原始消息文本

        Returns:
            {"intent": "tarot|chat|memory|...", "query": "占卜问题（仅tarot意图）"}
        """
        default_result = {"intent": "chat", "query": ""}

        if not message or not message.strip():
            return default_result

        msg = message.strip()

        # === 快速短路：明显的闲聊直接返回 chat，省一次 LLM 调用 ===
        for pattern in _CHAT_SHORTCUTS:
            if pattern.match(msg):
                logger.debug(f"⚡ 快速短路 chat | 消息: {msg[:20]}")
                return default_result

        # === 关键词匹配：常见意图直接匹配，省一次 LLM 调用 ===
        for pattern, intent, query_fn in _KEYWORD_PATTERNS:
            m = pattern.search(msg)
            if m:
                query = query_fn(m) if query_fn else ""
                logger.info(f"⚡ 关键词匹配 | 消息: {msg[:30]} | 意图: {intent} | query: {query}")
                return {"intent": intent, "query": query}

        # === 需要 LLM 判断的复杂场景 ===
        if not self.client:
            logger.warning("⚠️ 意图识别客户端未初始化，默认走 chat")
            return default_result

        try:
            response = await self.client.chat.completions.create(
                model=INTENT_MODEL,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": message}
                ],
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)

            intent = result.get("intent", "chat")
            query = result.get("query", "")

            # 校验 intent 合法性
            valid_intents = {
                "tarot", "tarot_history", "memory", "forget",
                "clear_history", "fortune", "luck", "intro", "help", "chat"
            }
            if intent not in valid_intents:
                logger.warning(f"⚠️ 未知意图 '{intent}'，回退到 chat")
                intent = "chat"

            logger.info(f"🎯 意图识别 | 消息: {message[:30]}... | 意图: {intent} | query: {query[:30]}")
            return {"intent": intent, "query": query}

        except json.JSONDecodeError as e:
            logger.error(f"❌ 意图识别 JSON 解析失败: {e}")
            return default_result
        except Exception as e:
            logger.error(f"❌ 意图识别失败: {e}", exc_info=True)
            return default_result


# 导出单例
intent_router = IntentRouter()
