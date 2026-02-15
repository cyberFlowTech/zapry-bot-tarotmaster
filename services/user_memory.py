"""
用户记忆管理器（SQLite 版）
- 持久化存储：数据安全，重启不丢失
- 内存缓存：热点用户 5 分钟 TTL，减少数据库查询
- ACID 事务：原子写入，不会出现数据损坏
- 接口不变：对上层代码完全透明
"""

import copy
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from db.database import db

logger = logging.getLogger(__name__)


class UserMemoryManager:
    """用户记忆管理器（SQLite 版）"""

    def __init__(self):
        self._cache: dict = {}
        self._cache_expiry: dict = {}
        self.CACHE_TTL_SECONDS = 300  # 缓存 5 分钟

    # ------------------------------------------------------------------
    # 公共接口（与旧版完全一致）
    # ------------------------------------------------------------------

    async def get_user_memory(self, user_id: str) -> dict:
        """获取用户档案（优先命中缓存）"""
        # 1. 检查缓存
        cached = self._get_from_cache(user_id)
        if cached is not None:
            return cached

        # 2. 从数据库加载
        memory = await self._load_from_db(user_id)

        # 3. 写入缓存
        self._set_cache(user_id, memory)
        return memory

    async def save_user_memory(self, user_id: str, memory: dict) -> bool:
        """保存用户档案到数据库"""
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            memory['last_updated'] = now
            memory_json = json.dumps(memory, ensure_ascii=False)

            await db.execute(
                """INSERT INTO user_memories (user_id, user_name, memory_data, conversation_count, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     user_name = excluded.user_name,
                     memory_data = excluded.memory_data,
                     conversation_count = excluded.conversation_count,
                     updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    memory.get('user_name', '朋友'),
                    memory_json,
                    memory.get('conversation_count', 0),
                    now,
                ),
            )

            # 更新缓存
            self._set_cache(user_id, memory)
            logger.info(f"💾 保存档案成功 | 用户: {user_id}")
            return True

        except Exception as e:
            logger.error(f"❌ 保存档案失败 | 用户: {user_id} | {e}")
            return False

    async def update_user_memory(self, user_id: str, updates: dict) -> bool:
        """增量更新用户档案"""
        memory = await self.get_user_memory(user_id)
        self._deep_merge(memory, updates)
        memory['conversation_count'] = memory.get('conversation_count', 0) + 1
        return await self.save_user_memory(user_id, memory)

    async def delete_user_memory(self, user_id: str) -> bool:
        """删除用户档案"""
        try:
            await db.execute("DELETE FROM user_memories WHERE user_id = ?", (user_id,))
            self._invalidate_cache(user_id)
            logger.info(f"🗑️ 删除档案成功 | 用户: {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 删除档案失败 | 用户: {user_id} | {e}")
            return False

    # ------------------------------------------------------------------
    # 同步接口（供 /memory, /forget 等同步上下文使用）
    # ------------------------------------------------------------------

    def get_user_memory_sync(self, user_id: str) -> dict:
        """同步获取用户档案"""
        cached = self._get_from_cache(user_id)
        if cached is not None:
            return cached

        row = db.fetch_one_sync(
            "SELECT memory_data FROM user_memories WHERE user_id = ?",
            (user_id,),
        )
        if row:
            memory = json.loads(row['memory_data'])
            self._set_cache(user_id, memory)
            return memory

        memory = self._create_empty_memory(user_id)
        self._set_cache(user_id, memory)
        return memory

    def delete_user_memory_sync(self, user_id: str) -> bool:
        """同步删除用户档案"""
        try:
            db.execute_sync("DELETE FROM user_memories WHERE user_id = ?", (user_id,))
            self._invalidate_cache(user_id)
            logger.info(f"🗑️ 删除档案成功 | 用户: {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 删除档案失败 | 用户: {user_id} | {e}")
            return False

    # ------------------------------------------------------------------
    # 格式化（给 AI 用）
    # ------------------------------------------------------------------

    @staticmethod
    def format_memory_for_ai(memory: dict) -> str:
        """将用户档案格式化为 AI 可读文本"""
        if not memory or memory.get('conversation_count', 0) == 0:
            return ""

        parts = []

        # 用户名称 — 最前面
        user_name = memory.get('user_name', '')
        if user_name and user_name != '朋友':
            parts.append(f"- 用户的名字/昵称：{user_name}")

        # 基本信息 — 最重要，放最前面，用醒目格式
        basic = memory.get('basic_info', {})
        basic_items = []
        
        # 昵称（如果用户在聊天中自报过名字，可能和平台用户名不同）
        nickname = basic.get('nickname', '')
        if nickname and nickname != user_name:
            basic_items.append(f"- 用户的昵称：{nickname}")
        
        for key, label in [
            ('age', '年龄'), ('gender', '性别'), ('location', '位置'),
            ('occupation', '职业'), ('school', '学校'), ('major', '专业'),
        ]:
            val = basic.get(key)
            if val:
                suffix = "岁" if key == 'age' else ""
                basic_items.append(f"- 用户的{label}：{val}{suffix}")
        if basic_items:
            parts.append("用户基本信息：")
            parts.extend(basic_items)
            parts.append("")

        # 性格
        personality = memory.get('personality', {})
        traits = personality.get('traits', [])
        if traits:
            parts.append(f"性格特点: {', '.join(traits)}")
        values = personality.get('values', [])
        if values:
            parts.append(f"价值观: {', '.join(values)}")
        if traits or values:
            parts.append("")

        # 生活背景
        life = memory.get('life_context', {})
        has_life = False
        rels = life.get('relationships', {})
        for key, label in [('romantic', '感情'), ('family', '家庭'), ('friends', '朋友')]:
            val = rels.get(key)
            if val:
                if not has_life:
                    parts.append("生活背景:")
                    has_life = True
                parts.append(f"  {label}: {val}")
        concerns = life.get('concerns', [])
        if concerns:
            if not has_life:
                parts.append("生活背景:")
                has_life = True
            parts.append(f"  当前困扰: {', '.join(concerns[:3])}")
        goals = life.get('goals', [])
        if goals:
            parts.append(f"  目标: {', '.join(goals[:3])}")
        events = life.get('recent_events', [])
        if events:
            parts.append(f"  近期事件: {events[0]}")
        if has_life:
            parts.append("")

        # 兴趣
        interests = memory.get('interests', [])
        if interests:
            parts.append(f"兴趣爱好: {', '.join(interests[:5])}\n")

        # 总结
        summary = memory.get('conversation_summary', '')
        if summary:
            parts.append(f"用户特点: {summary}\n")

        count = memory.get('conversation_count', 0)
        parts.append(f"（已与我对话 {count} 次）")
        parts.append("当用户问关于自己的问题时，直接用上面的信息回答，像朋友一样自然地说出来。")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 内部方法：缓存
    # ------------------------------------------------------------------

    def _get_from_cache(self, user_id: str) -> Optional[dict]:
        if user_id not in self._cache:
            return None
        if datetime.now() > self._cache_expiry[user_id]:
            self._invalidate_cache(user_id)
            return None
        logger.debug(f"✅ 缓存命中 | 用户: {user_id}")
        return copy.deepcopy(self._cache[user_id])

    def _set_cache(self, user_id: str, memory: dict) -> None:
        self._cache[user_id] = copy.deepcopy(memory)
        self._cache_expiry[user_id] = datetime.now() + timedelta(
            seconds=self.CACHE_TTL_SECONDS
        )

    def _invalidate_cache(self, user_id: str) -> None:
        self._cache.pop(user_id, None)
        self._cache_expiry.pop(user_id, None)

    # ------------------------------------------------------------------
    # 内部方法：数据库 IO
    # ------------------------------------------------------------------

    async def _load_from_db(self, user_id: str) -> dict:
        """从数据库加载用户档案"""
        row = await db.fetch_one(
            "SELECT memory_data FROM user_memories WHERE user_id = ?",
            (user_id,),
        )
        if row:
            try:
                data = json.loads(row['memory_data'])
                logger.debug(f"✅ 加载档案 | 用户: {user_id}")
                return data
            except json.JSONDecodeError as e:
                logger.error(f"❌ 档案 JSON 解析失败 | 用户: {user_id} | {e}")

        return self._create_empty_memory(user_id)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _create_empty_memory(user_id: str, user_name: str = None) -> dict:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return {
            "user_id": user_id,
            "user_name": user_name or "朋友",
            "created_at": now,
            "last_updated": now,
            "conversation_count": 0,
            "basic_info": {},
            "personality": {"traits": [], "values": [], "communication_style": ""},
            "life_context": {
                "relationships": {},
                "concerns": [],
                "goals": [],
                "recent_events": [],
            },
            "interests": [],
            "tarot_summary": {
                "total_readings": 0,
                "common_topics": [],
                "last_reading": {},
            },
            "conversation_summary": "",
            "meta": {"memory_extraction_count": 0, "last_extraction": None},
        }

    @staticmethod
    def _deep_merge(target: dict, source: dict) -> None:
        """深度合并（就地修改 target）"""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                UserMemoryManager._deep_merge(target[key], value)
            elif key in target and isinstance(target[key], list) and isinstance(value, list):
                seen = set()
                merged = []
                for item in target[key] + value:
                    s = str(item)
                    if s not in seen:
                        seen.add(s)
                        merged.append(item)
                target[key] = merged
            else:
                target[key] = value


# 导出单例
user_memory_manager = UserMemoryManager()
