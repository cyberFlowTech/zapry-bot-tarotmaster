"""
林晚晴 AI 对话服务
基于 OpenAI，整合心理咨询师人设
"""

import openai
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
import logging
import os

logger = logging.getLogger(__name__)

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
    """林晚晴 AI 对话系统"""
    
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
            logger.info("✅ AsyncOpenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"❌ AsyncOpenAI 客户端初始化失败: {e}")
            self.client = None
    
    async def chat(self, user_message: str, user_name: str = "朋友", 
                   conversation_history: list = None, tarot_context: str = None,
                   memory_context: str = None) -> str:
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
            
            # 调用 OpenAI（异步，不阻塞事件循环）
            response = await self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=800,
                top_p=0.9,
                frequency_penalty=0.3,
                presence_penalty=0.3
            )
            
            reply = response.choices[0].message.content.strip()
            
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


# 全局实例
elena_ai = ElenaAI()
