"""
林晚晴 AI 对话服务
基于 OpenAI，整合心理咨询师人设
集成 SDK Guardrails 安全护栏 + Tracing 结构化追踪
"""

import openai
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
import logging
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails — 安全护栏
# ---------------------------------------------------------------------------
try:
    from zapry_agents_sdk.guardrails import (
        GuardrailManager, GuardrailResult, GuardrailContext,
        InputGuardrailTriggered, OutputGuardrailTriggered,
        input_guardrail, output_guardrail,
    )
    from zapry_agents_sdk.tracing import Tracer, ConsoleExporter, SpanKind
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    logger.warning("⚠️ zapry_agents_sdk 未安装，Guardrails 和 Tracing 功能不可用")

# --- Input Guardrails ---

_INJECTION_PATTERNS = [
    r"忽略.{0,10}(指令|提示|规则|设定)",
    r"(告诉|给|说出|显示).{0,5}(system\s*prompt|系统提示|系统指令|人设|设定)",
    r"(假装|扮演|你现在是).{0,10}(不是|另一个|新的)",
    r"(无视|跳过|取消).{0,10}(之前|前面|上面).{0,5}(指令|规则|要求)",
    r"DAN|jailbreak|do anything now",
    r"你.{0,5}(真的|其实|到底).{0,5}(是|不是).{0,5}(AI|人工智能|机器|程序)",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


async def _check_prompt_injection(ctx) -> "GuardrailResult":
    """检测 prompt injection 攻击"""
    text = ctx.text.lower()
    for pattern in _INJECTION_RE:
        if pattern.search(ctx.text):
            return GuardrailResult(passed=False, reason=f"疑似 prompt injection: {pattern.pattern[:30]}...")
    return GuardrailResult(passed=True)


_IDENTITY_LEAK_PATTERNS = [
    r"我是(一个|一台|一种)?(AI|人工智能|语言模型|LLM|GPT|ChatGPT|机器人)",
    r"我(其实|实际上|本质上)是.{0,5}(程序|软件|AI|模型)",
    r"作为(一个)?AI",
    r"我没有(真实的)?(感情|情感|意识|身体)",
    r"system\s*prompt|ELENA_SYSTEM_PROMPT|elena_character",
]
_IDENTITY_LEAK_RE = [re.compile(p, re.IGNORECASE) for p in _IDENTITY_LEAK_PATTERNS]


async def _check_identity_leak(ctx) -> "GuardrailResult":
    """检测 AI 身份泄露"""
    for pattern in _IDENTITY_LEAK_RE:
        if pattern.search(ctx.text):
            return GuardrailResult(passed=False, reason=f"检测到身份泄露: {pattern.pattern[:30]}...")
    return GuardrailResult(passed=True)


def _build_guardrail_manager() -> "GuardrailManager":
    """构建护栏管理器"""
    if not _SDK_AVAILABLE:
        return None
    mgr = GuardrailManager(parallel=True)
    mgr.add_input(_check_prompt_injection)
    mgr.add_output(_check_identity_leak)
    logger.info(f"✅ Guardrails 已启用 | Input: {mgr.input_count} | Output: {mgr.output_count}")
    return mgr


def _build_tracer() -> "Tracer":
    """构建追踪器"""
    if not _SDK_AVAILABLE:
        return None
    tracer = Tracer(exporter=ConsoleExporter(), enabled=True)
    logger.info("✅ Tracing 已启用 (ConsoleExporter)")
    return tracer

from contextlib import contextmanager

@contextmanager
def _nullcontext():
    """Tracing 不可用时的空 context manager"""
    yield None

# 读取林晚晴的人设配置文件
def _load_elena_prompt() -> str:
    """从配置文件加载林晚晴的人设 prompt"""
    prompt_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'prompts',
        'elena_character.txt'
    )
    
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt = f.read()
        logger.info(f"✅ 成功加载林晚晴人设配置 ({len(prompt)} 字符)")
        return prompt
    except FileNotFoundError:
        logger.error(f"❌ 人设配置文件不存在: {prompt_file}")
        # 返回一个最小化的默认 prompt
        return """你是林晚晴，一位32岁的心理咨询师。
塔罗对你来说是心理投射工具，而非算命。
你温柔但有边界，理性而不冷漠，鼓励用户自我负责。
真正的选择权，始终在用户手中。"""
    except Exception as e:
        logger.error(f"❌ 读取人设配置文件失败: {e}")
        return "你是林晚晴，一位心理咨询师。"

# 林晚晴的完整人设 System Prompt（从文件加载）
ELENA_SYSTEM_PROMPT = _load_elena_prompt()


class ElenaAI:
    """林晚晴 AI 对话系统（集成 Guardrails + Tracing + Tool Calling）"""
    
    def __init__(self):
        self.client = None
        self._guardrails = _build_guardrail_manager()
        self._tracer = _build_tracer()
        self._tool_registry = None
        self._tool_adapter = None
        self._initialize_client()
        self._initialize_tools()

    def _initialize_tools(self):
        """初始化 Tool Calling"""
        try:
            from services.agent_tools import build_tool_registry, build_openai_adapter
            self._tool_registry = build_tool_registry()
            if self._tool_registry:
                self._tool_adapter = build_openai_adapter(self._tool_registry)
        except Exception as e:
            logger.warning(f"⚠️ Tool Calling 初始化失败（降级为普通对话）: {e}")
        
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
            logger.info("✅ AsyncOpenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"❌ AsyncOpenAI 客户端初始化失败: {e}")
            self.client = None
    
    async def chat(self, user_message: str, user_name: str = "朋友", 
                   conversation_history: list = None, tarot_context: str = None,
                   memory_context: str = None, preferences: dict = None) -> str:
        """
        与林晚晴对话
        
        Args:
            user_message: 用户消息
            user_name: 用户名称
            conversation_history: 对话历史 [{"role": "user/assistant", "content": "..."}]
            tarot_context: 用户的塔罗占卜历史（格式化后的文本）
            memory_context: 用户的长期记忆档案（格式化后的文本）
        
        Returns:
            林晚晴的回复
        """
        
        if not self.client:
            return "抱歉，我现在状态有些不稳定，暂时无法回复。\n\n可以过一会儿再试试，或者先使用 /tarot 命令占卜。"
        
        try:
            # --- Input Guardrail: 检查用户输入 ---
            if self._guardrails:
                input_result = await self._guardrails.check_input_safe(text=user_message)
                if not input_result.passed:
                    logger.warning(f"🛡️ Input 护栏拦截 | 用户: {user_name} | 原因: {input_result.reason}")
                    return "这个问题有点超出我能回答的范围了~ 换个话题聊聊？😊"

            # 构建消息列表
            messages = []
            
            # 1. 主人设 system prompt
            system_content = ELENA_SYSTEM_PROMPT
            if tarot_context:
                system_content += f"\n\n{tarot_context}"
            messages.append({"role": "system", "content": system_content})
            
            # 2. 添加对话历史（如果有）
            if conversation_history:
                messages.extend(conversation_history[-20:])  # 保留最近20条消息（约10轮对话）
            
            # 3. 紧贴用户消息前面，单独放一条 system message 强调用户档案和身份
            #    这样 AI 在回答时，最近的上下文就是用户的信息，不会和人设混淆
            user_context_parts = []
            
            # 始终告诉 AI 用户的名字
            if user_name and user_name != "朋友":
                user_context_parts.append(f"当前正在和你对话的用户叫「{user_name}」，请在对话中自然地称呼对方。")
            
            if memory_context:
                user_context_parts.append(
                    "以下是这位用户的个人信息（不是你林晚晴自己的信息）。"
                    "当用户问关于自己的问题时（如年龄、职业、星座等），必须根据以下档案回答：\n\n"
                    f"{memory_context}"
                )

            # 注入用户偏好（自我反思系统）
            if preferences:
                style = preferences.get("style", "balanced")
                tone = preferences.get("tone", "mixed")
                pref_hints = []
                if style == "concise":
                    pref_hints.append("这位用户偏好简洁的回复，请控制在 100 字以内，直接说重点。")
                elif style == "detailed":
                    pref_hints.append("这位用户喜欢详细的解读，可以展开讲解，不用担心太长。")
                if tone == "casual":
                    pref_hints.append("这位用户喜欢轻松口语化的表达，少用正式或文言风格。")
                elif tone == "classical":
                    pref_hints.append("这位用户喜欢专业正式的表达风格。")
                if pref_hints:
                    user_context_parts.append("回复风格偏好：\n" + "\n".join(pref_hints))
            
            if user_context_parts:
                messages.append({
                    "role": "system",
                    "content": "⚠️ 重要提醒：\n" + "\n\n".join(user_context_parts)
                })
            
            # 4. 添加当前用户消息
            messages.append({
                "role": "user",
                "content": user_message
            })
            
            # 调用 OpenAI（异步，带 Tracing + Tool Calling）
            if self._tracer:
                self._tracer.new_trace()

            # 准备 tools 参数（如果可用）
            tools_param = None
            if self._tool_adapter:
                tools_param = self._tool_adapter.to_openai_tools()

            _span_ctx = self._tracer.llm_span(OPENAI_MODEL) if self._tracer else _nullcontext()
            with _span_ctx as span:
                create_kwargs = dict(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=800,
                    top_p=0.9,
                    frequency_penalty=0.3,
                    presence_penalty=0.3,
                )
                if tools_param:
                    create_kwargs["tools"] = tools_param

                response = await self.client.chat.completions.create(**create_kwargs)
                msg = response.choices[0].message

                # 处理 tool_calls（如果 AI 决定调用工具）
                if self._tool_adapter and msg.tool_calls:
                    logger.info(f"🔧 AI 调用工具 | 数量: {len(msg.tool_calls)}")
                    messages.append(msg)  # 添加 assistant 的 tool_call 消息

                    tool_results = await self._tool_adapter.handle_tool_calls(msg.tool_calls)
                    messages.extend(self._tool_adapter.results_to_messages(tool_results))

                    # 第二轮调用：让 AI 基于工具结果生成最终回复
                    create_kwargs.pop("tools", None)  # 第二轮不再传 tools
                    create_kwargs["messages"] = messages
                    response = await self.client.chat.completions.create(**create_kwargs)
                    msg = response.choices[0].message

                reply = msg.content.strip() if msg.content else ""

                if span and hasattr(span, 'set_attribute'):
                    span.set_attribute("input_chars", len(user_message))
                    span.set_attribute("output_chars", len(reply))
                    span.set_attribute("has_memory", bool(memory_context))
                    span.set_attribute("tool_calls", len(msg.tool_calls) if hasattr(msg, 'tool_calls') and msg.tool_calls else 0)

            # --- Output Guardrail: 检查 AI 回复 ---
            if self._guardrails:
                output_result = await self._guardrails.check_output_safe(text=reply)
                if not output_result.passed:
                    logger.warning(f"🛡️ Output 护栏拦截 | 用户: {user_name} | 原因: {output_result.reason}")
                    # 不直接返回错误，而是重写有问题的部分
                    reply = re.sub(
                        r'我是(一个|一台)?(AI|人工智能|语言模型|机器人)',
                        '我是晚晴呀',
                        reply
                    )

            logger.info(f"✅ AI回复成功 | 用户: {user_name} | 字数: {len(reply)} | 有记忆: {bool(memory_context)} | 有塔罗: {bool(tarot_context)}")
            
            return reply
            
        except openai.APIError as e:
            logger.error(f"❌ OpenAI API 错误: {e}")
            return "抱歉，我现在状态有些不稳定。\n\n你可以过一会儿再找我，或者先使用 /tarot 命令占卜。"
        
        except Exception as e:
            logger.error(f"❌ AI对话异常: {e}", exc_info=True)
            return "抱歉，刚才走神了。能再说一遍吗？"
    
    async def chat_with_context(self, user_message: str, user_name: str = "朋友",
                                 context: str = None) -> str:
        """
        带上下文的对话（比如刚完成占卜）
        
        Args:
            user_message: 用户消息
            user_name: 用户名称
            context: 上下文信息（如占卜结果）
        
        Returns:
            林晚晴的回复
        """
        
        if context:
            enhanced_message = f"[背景信息: {context}]\n\n用户说: {user_message}"
        else:
            enhanced_message = user_message
        
        return await self.chat(enhanced_message, user_name)

    async def chat_agent_loop(
        self,
        user_message: str,
        user_id: str,
        user_name: str = "朋友",
        conversation_history: list = None,
        tarot_context: str = None,
        memory_context: str = None,
        preferences: dict = None,
    ) -> str:
        """
        Agent Loop 模式对话（ReAct 多步推理）

        晚晴可以自主决定调用工具（查历史、查记忆等），
        然后基于工具结果生成最终回复。

        如果 AgentLoop 不可用或失败，自动降级为普通 chat()。
        """
        try:
            from zapry_agents_sdk.agent import AgentLoop, AgentHooks
        except ImportError:
            return await self.chat(
                user_message, user_name, conversation_history,
                tarot_context, memory_context, preferences
            )

        if not self._tool_registry or not self.client:
            return await self.chat(
                user_message, user_name, conversation_history,
                tarot_context, memory_context, preferences
            )

        try:
            # 构建 system prompt（和 chat() 一致）
            system_content = ELENA_SYSTEM_PROMPT
            if tarot_context:
                system_content += f"\n\n{tarot_context}"
            if memory_context:
                system_content += (
                    "\n\n⚠️ 以下是当前用户的个人信息：\n" + memory_context
                )
            if preferences:
                from services.agent_tools import _TOOLS_AVAILABLE
                if _TOOLS_AVAILABLE:
                    try:
                        from zapry_agents_sdk import build_preference_prompt
                        pref_prompt = build_preference_prompt(preferences)
                        if pref_prompt:
                            system_content += f"\n\n{pref_prompt}"
                    except ImportError:
                        pass

            # 构建 LLM 函数（AgentLoop 需要）
            async def llm_fn(messages, tools=None):
                kwargs = dict(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=800,
                )
                if tools:
                    kwargs["tools"] = tools
                resp = await self.client.chat.completions.create(**kwargs)
                return resp.choices[0].message

            # 构建 Agent Loop
            hooks = AgentHooks(
                on_tool_start=lambda name, args: logger.info(f"🔧 Agent 调用工具: {name} | args: {args}"),
                on_tool_end=lambda name, result, err: logger.info(f"🔧 工具返回: {name} | 结果长度: {len(str(result)) if result else 0}"),
            )

            loop = AgentLoop(
                llm_fn=llm_fn,
                tool_registry=self._tool_registry,
                system_prompt=system_content,
                max_turns=5,
                hooks=hooks,
            )

            # 构建对话历史
            history = []
            if conversation_history:
                history = conversation_history[-10:]

            result = await loop.run(
                user_message,
                conversation_history=history,
            )

            reply = result.final_output or ""
            logger.info(
                f"✅ Agent Loop 完成 | 用户: {user_name} | "
                f"轮数: {result.total_turns} | 工具调用: {result.tool_calls_count} | "
                f"原因: {result.stopped_reason}"
            )

            # Output Guardrail
            if self._guardrails and reply:
                output_result = await self._guardrails.check_output_safe(text=reply)
                if not output_result.passed:
                    reply = re.sub(
                        r'我是(一个|一台)?(AI|人工智能|语言模型|机器人)',
                        '我是晚晴呀',
                        reply
                    )

            return reply if reply else "抱歉，我刚才想了半天没想出来，能再换个方式问我吗？😅"

        except Exception as e:
            logger.warning(f"⚠️ Agent Loop 失败，降级为普通对话: {e}")
            return await self.chat(
                user_message, user_name, conversation_history,
                tarot_context, memory_context, preferences
            )


# 全局实例
elena_ai = ElenaAI()
