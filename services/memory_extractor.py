"""
记忆提取器
使用AI从对话中提取用户的关键信息，更新用户档案
"""

import json
import logging
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL

logger = logging.getLogger(__name__)


# 记忆提取专用模型（使用便宜的模型降低成本）
EXTRACTION_MODEL = "gpt-3.5-turbo"  # 比GPT-4便宜60%

# 记忆提取提示词
MEMORY_EXTRACTION_PROMPT = """你是一个信息提取专家。请从以下对话中提取【用户】的关键信息。

【最高优先级规则】
1. 只提取【用户】自己说的关于自己的信息。用户的消息标记为"用户:"。
2. 【绝对禁止】把 Elena（AI助手）说的关于她自己的信息当成用户的信息！
   - Elena 说"我住在上海" → 这是 Elena 的信息，不是用户的，忽略！
   - Elena 说"我在浙江大学读书" → 这是 Elena 的信息，不是用户的，忽略！
   - Elena 说"我是心理咨询师" → 这是 Elena 的信息，不是用户的，忽略！
   - Elena 说"我经历了感情转折" → 这是 Elena 的信息，不是用户的，忽略！
3. 只有当【用户】说"我住在北京""我18岁""我是学生"时，才提取对应信息。
4. 不要推测或编造。如果某个字段没有用户自己说的信息，保持为空/null。

分析对话，提取以下信息（JSON格式）：

{{
  "basic_info": {{
    "age": null,
    "gender": null,
    "location": null,
    "occupation": null,
    "school": null,
    "major": null
  }},
  "personality": {{
    "traits": [],
    "values": [],
    "communication_style": ""
  }},
  "life_context": {{
    "relationships": {{
      "romantic": "",
      "family": "",
      "friends": ""
    }},
    "concerns": [],
    "goals": [],
    "recent_events": []
  }},
  "interests": [],
  "conversation_summary": ""
}}

字段说明：
- basic_info: age=年龄(数字), gender=性别, location=居住地, occupation=职业, school=学校, major=专业
- personality: traits=性格特点, values=价值观, communication_style=沟通风格
- life_context: relationships(romantic=感情状态, family=家庭情况, friends=朋友关系), concerns=当前困扰(最多3个), goals=目标愿望(最多3个), recent_events=近期重要事件(最多2个)
- interests=兴趣爱好
- conversation_summary=用一句话总结这个用户的特点(50字以内)

【对话内容】
{conversations}

【当前已有的用户信息】
{current_memory}

请输出JSON格式的提取结果。只输出JSON，不要其他文字。"""


class MemoryExtractor:
    """记忆提取器"""
    
    def __init__(self):
        """初始化异步 OpenAI 客户端"""
        try:
            if OPENAI_BASE_URL:
                self.client = AsyncOpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url=OPENAI_BASE_URL
                )
            else:
                self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            logger.info("✅ 记忆提取器 AsyncOpenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"❌ 记忆提取器 AsyncOpenAI 客户端初始化失败: {e}")
            self.client = None
    
    async def extract_from_conversations(
        self, 
        conversations: list, 
        current_memory: dict
    ) -> dict:
        """
        从对话中提取关键信息
        
        Args:
            conversations: 对话列表 [{"role": "user/assistant", "content": "..."}]
            current_memory: 当前的用户档案
        
        Returns:
            提取的信息（增量更新）
        """
        if not self.client:
            logger.warning("⚠️ OpenAI 客户端未初始化，跳过记忆提取")
            return {}
        
        if not conversations:
            logger.warning("⚠️ 没有对话内容，跳过记忆提取")
            return {}
        
        try:
            # 格式化对话内容
            conv_text = self._format_conversations(conversations)
            
            # 格式化当前记忆
            memory_text = self._format_current_memory(current_memory)
            
            # 构建提取提示
            prompt = MEMORY_EXTRACTION_PROMPT.format(
                conversations=conv_text,
                current_memory=memory_text
            )
            
            logger.info(f"🧠 开始记忆提取 | 对话数: {len(conversations)} | Prompt长度: {len(prompt)}")
            
            # 调用AI提取（异步，不阻塞事件循环）
            response = await self.client.chat.completions.create(
                model=EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": (
                        "你是一个信息提取专家，输出标准JSON格式。\n"
                        "你的任务是从对话中提取【用户】自己的个人信息。\n"
                        "对话中有两个角色：'用户'是你要提取信息的人，'Elena'是AI助手。\n"
                        "【关键】Elena说的关于她自己的任何信息（职业、学校、居住地、经历等），绝对不能当作用户的信息！"
                    )},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # 进一步降低随机性，提高提取准确性
                max_tokens=1000
            )
            
            # 解析结果
            result_text = response.choices[0].message.content.strip()
            
            # 提取JSON（可能包含在代码块中）
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            extracted_info = json.loads(result_text)
            
            logger.info(f"✅ 记忆提取成功 | 提取字段数: {len(str(extracted_info))}")
            logger.debug(f"提取结果: {json.dumps(extracted_info, ensure_ascii=False, indent=2)}")
            
            return extracted_info
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ 记忆提取结果解析失败: {e}")
            logger.error(f"原始结果: {result_text[:200]}")
            return {}
        except Exception as e:
            logger.error(f"❌ 记忆提取失败: {e}", exc_info=True)
            return {}
    
    def _format_conversations(self, conversations: list) -> str:
        """格式化对话内容"""
        formatted = ""
        for i, msg in enumerate(conversations, 1):
            role = "用户" if msg["role"] == "user" else "Elena"
            formatted += f"{i}. {role}: {msg['content']}\n"
        return formatted
    
    def _format_current_memory(self, memory: dict) -> str:
        """格式化当前记忆（简化版）"""
        if not memory or memory.get('conversation_count', 0) == 0:
            return "（无）"
        
        formatted = ""
        
        basic = memory.get('basic_info', {})
        if basic:
            formatted += f"基本信息: {json.dumps(basic, ensure_ascii=False)}\n"
        
        personality = memory.get('personality', {})
        if personality:
            formatted += f"性格: {json.dumps(personality, ensure_ascii=False)}\n"
        
        life_context = memory.get('life_context', {})
        if life_context:
            formatted += f"生活背景: {json.dumps(life_context, ensure_ascii=False)}\n"
        
        interests = memory.get('interests', [])
        if interests:
            formatted += f"兴趣: {', '.join(interests)}\n"
        
        summary = memory.get('conversation_summary', '')
        if summary:
            formatted += f"总结: {summary}\n"
        
        return formatted or "（无）"


# 导出单例
memory_extractor = MemoryExtractor()
