# Zapry Open API 兼容性需求文档

> 提交方：塔罗牌运势 Bot 开发团队
> 日期：2026-02-12
> 版本：v1.0
> 对接标准：Telegram Bot API 6.x

---

## 一、背景

我们基于 `python-telegram-bot` SDK 开发了一个 Telegram Bot，通过 Zapry Open API（`https://openapi.mimo.immo/bot`）接入 Zapry 平台。

在实际对接过程中，我们发现 Zapry Open API 与标准 Telegram Bot API 存在若干差异，导致我们需要在代码中做大量兼容适配。本文档梳理了所有已发现的差异点，希望 Zapry 开发团队能对照 Telegram Bot API 官方规范逐项对齐，以降低开发者的适配成本。

Telegram Bot API 官方文档：https://core.telegram.org/bots/api

---

## 二、问题清单

### 【P0 - 严重】数据结构缺失或格式错误

以下问题直接导致 SDK 解析失败或功能不可用，需优先修复。

---

#### 问题 1：User 对象 `first_name` 为空

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有消息 |
| **当前表现** | Webhook 推送的 `from` 字段中 `first_name` 为空字符串 `""` |
| **期望行为** | `first_name` 应返回用户在 Zapry 上设置的昵称/显示名 |
| **Telegram 规范** | `first_name` 为 User 对象的**必填字段**（Required） |

**Zapry 实际返回：**
```json
{
  "from": {
    "id": "548348",
    "first_name": ""
  }
}
```

**Telegram 标准返回：**
```json
{
  "from": {
    "id": 548348,
    "is_bot": false,
    "first_name": "张三",
    "last_name": "Zhang",
    "username": "zhangsan",
    "language_code": "zh-hans"
  }
}
```

**影响**：Bot 无法获取用户名称，无法在回复中称呼用户，严重影响用户体验。

---

#### 问题 2：User 对象 `is_bot` 字段缺失

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有消息 |
| **当前表现** | `from` 对象中不包含 `is_bot` 字段 |
| **期望行为** | 普通用户返回 `false`，Bot 用户返回 `true` |
| **Telegram 规范** | `is_bot` 为 User 对象的**必填字段**（Required） |

**影响**：`python-telegram-bot` SDK 在反序列化时会报 `TypeError: missing 1 required positional argument: 'is_bot'`，我们目前通过 Monkey Patch 硬编码补全。

---

#### 问题 3：User 对象 `id` 为字符串类型

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有消息 |
| **当前表现** | `from.id` 为字符串 `"548348"` |
| **期望行为** | `id` 应为整数 `548348` |
| **Telegram 规范** | `id` 类型为 `Integer`（64-bit） |

**影响**：SDK 内部使用整数做比较和路由，字符串类型会导致匹配失败。我们目前在接收层做了 `int()` 转换。

---

#### 问题 4：User 对象缺少 `username` 字段

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 所有消息 |
| **当前表现** | `from` 对象中不包含 `username` 字段 |
| **期望行为** | 如果用户设置了用户名，应返回 `username` 字段 |
| **Telegram 规范** | `username` 为 Optional 字段，但设置了用户名的用户应返回 |

**影响**：Bot 无法通过 `@username` 方式提及用户。

---

#### 问题 5：私聊 Chat 对象 `id` 返回 Bot 用户名

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有私聊消息 |
| **当前表现** | 私聊时 `chat.id` 返回 `"zapry_tarot_bot"`（Bot 用户名字符串） |
| **期望行为** | 私聊时 `chat.id` 应等于发消息用户的 `id`（整数） |
| **Telegram 规范** | 私聊中 `chat.id` = `user.id`，类型为 `Integer` |

**Zapry 实际返回：**
```json
{
  "chat": {
    "id": "zapry_tarot_bot",
    "type": "private"
  }
}
```

**Telegram 标准返回：**
```json
{
  "chat": {
    "id": 548348,
    "first_name": "张三",
    "type": "private"
  }
}
```

**影响**：Bot 向用户发送消息时，`chat_id` 错误导致发送失败。我们目前通过解析 `from.id` 来替换 `chat.id`。

---

#### 问题 6：Chat 对象 `type` 字段为空

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有消息 |
| **当前表现** | `chat.type` 为空字符串 `""` |
| **期望行为** | 私聊返回 `"private"`，群组返回 `"group"` 或 `"supergroup"` |
| **Telegram 规范** | `type` 为 Chat 对象的**必填字段**（Required） |

**影响**：SDK 无法判断消息来源（私聊/群组），影响消息路由逻辑。

---

#### 问题 7：群聊 Chat `id` 带 `g_` 前缀

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 所有群聊消息 |
| **当前表现** | 群聊 `chat.id` 格式为 `"g_117686311051260010"` |
| **期望行为** | 群聊 `chat.id` 应为负整数，如 `-1001176863110` |
| **Telegram 规范** | 群组/超级群组的 `chat.id` 为负整数 |

**影响**：SDK 通过数值正负判断聊天类型，字符串格式导致逻辑异常。

---

#### 问题 8：Message 对象缺少 `entities` 字段

| 项目 | 说明 |
|------|------|
| **严重程度** | P0 |
| **影响范围** | 所有命令消息 |
| **当前表现** | 用户发送 `/tarot` 等命令时，Message 中不包含 `entities` 字段 |
| **期望行为** | 以 `/` 开头的命令文本，应包含 `bot_command` 类型的 entity |
| **Telegram 规范** | 命令消息必须包含 `entities` 数组，其中有 `type: "bot_command"` |

**Telegram 标准返回：**
```json
{
  "text": "/tarot 测事业",
  "entities": [
    {
      "offset": 0,
      "length": 6,
      "type": "bot_command"
    }
  ]
}
```

**影响**：`python-telegram-bot` 的 `CommandHandler` 依赖 `entities` 来识别命令，缺失会导致所有命令失效。我们目前通过 Monkey Patch 自动生成 entity。

---

### 【P1 - 重要】API 方法缺失或行为不一致

以下问题导致部分功能不可用，需要补充实现。

---

#### 问题 9：不支持 `sendChatAction` 方法

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 用户体验 |
| **当前表现** | 调用 `sendChatAction` 返回 404 |
| **期望行为** | 支持发送 `typing` 等状态提示 |
| **Telegram 规范** | [sendChatAction](https://core.telegram.org/bots/api#sendchataction) |

**影响**：Bot 回复较慢时，用户看不到"正在输入..."提示，体验较差。

---

#### 问题 10：不支持 `editMessageText` 方法

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 消息编辑 |
| **当前表现** | 调用 `editMessageText` 返回 404 |
| **期望行为** | 支持编辑已发送的消息 |
| **Telegram 规范** | [editMessageText](https://core.telegram.org/bots/api#editmessagetext) |

**影响**：无法实现消息内容实时更新（如占卜进度更新），只能发送新消息。

---

#### 问题 11：不支持 `reply_to_message_id` 参数

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 群聊体验 |
| **当前表现** | `sendMessage` 带 `reply_to_message_id` 参数时，可能不生效或报错 |
| **期望行为** | 回复的消息应引用原消息（显示引用框） |
| **Telegram 规范** | [sendMessage](https://core.telegram.org/bots/api#sendmessage) 的 `reply_to_message_id` 参数 |

**影响**：群聊中多人同时 @Bot 时，无法明确回复的是谁的消息，造成混乱。

---

#### 问题 12：不支持 Markdown 格式

| 项目 | 说明 |
|------|------|
| **严重程度** | P2 |
| **影响范围** | 消息样式 |
| **当前表现** | `parse_mode: "Markdown"` 或 `"MarkdownV2"` 不生效，标记符号原样显示 |
| **期望行为** | 支持基础 Markdown 格式（加粗、斜体、代码块） |
| **Telegram 规范** | [Formatting options](https://core.telegram.org/bots/api#formatting-options) |

**影响**：消息无法加粗、斜体等，格式单调。我们目前在发送前清除所有 Markdown 标记。

---

#### 问题 13：`answerCallbackQuery` 行为异常

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 按钮交互 |
| **当前表现** | `answerCallbackQuery` 要求额外的 `chat_id` 参数，或调用失败 |
| **期望行为** | 只需 `callback_query_id` 即可调用成功 |
| **Telegram 规范** | [answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery) |

**影响**：按钮点击后的确认提示（如 toast 或弹窗）无法显示。

---

### 【P2 - 一般】@Mention 检测差异

---

#### 问题 14：群组 @mention 的 entity 格式异常

| 项目 | 说明 |
|------|------|
| **严重程度** | P1 |
| **影响范围** | 群组 @Bot 功能 |
| **当前表现** | mention entity 的 `length` 为 `0`，`entity.user.username` 存储的是 Bot 显示名而非技术用户名 |
| **期望行为** | `length` 应为 mention 文本的实际长度，`username` 应为 Bot 的技术用户名 |
| **Telegram 规范** | [MessageEntity](https://core.telegram.org/bots/api#messageentity) |

**Zapry 实际返回：**
```json
{
  "entities": [
    {
      "type": "mention",
      "offset": 0,
      "length": 0,
      "user": {
        "id": "548348",
        "username": "塔罗牌运势"
      }
    }
  ]
}
```

**Telegram 标准返回：**
```json
{
  "entities": [
    {
      "type": "mention",
      "offset": 0,
      "length": 9,
      "user": {
        "id": 548348,
        "is_bot": true,
        "first_name": "塔罗牌运势",
        "username": "tarot_bot"
      }
    }
  ]
}
```

**影响**：Bot 无法通过标准方式检测 @mention，需要多种兼容逻辑才能正确识别。

---

## 三、问题汇总

| 编号 | 优先级 | 类别 | 简述 | 状态 |
|------|--------|------|------|------|
| 1 | P0 | User 对象 | `first_name` 为空 | ✅ 已修复 (2026-02) |
| 2 | P0 | User 对象 | `is_bot` 缺失 | ✅ 已修复 (2026-02) |
| 3 | P0 | User 对象 | `id` 应为整数，实际为字符串 | ❌ 未修复 |
| 4 | P1 | User 对象 | `username` 缺失 | ❌ 未修复 |
| 5 | P0 | Chat 对象 | 私聊 `chat.id` 返回 Bot 用户名 | ✅ 已修复 (2026-02) |
| 6 | P0 | Chat 对象 | `type` 为空 | ✅ 已修复 (2026-02) |
| 7 | P1 | Chat 对象 | 群聊 `id` 带 `g_` 前缀 | ❌ 未修复 |
| 8 | P0 | Message 对象 | 命令消息缺少 `entities` | ✅ 已修复 (2026-02) |
| 9 | P1 | API 方法 | 不支持 `sendChatAction` | ❌ 未修复 |
| 10 | P1 | API 方法 | 不支持 `editMessageText` | ❌ 未修复 |
| 11 | P1 | API 方法 | 不支持 `reply_to_message_id` | ❌ 未修复 |
| 12 | P2 | API 方法 | 不支持 Markdown 格式 | ❌ 未修复 |
| 13 | P1 | API 方法 | `answerCallbackQuery` 行为异常 | ❌ 未修复 |
| 14 | P1 | Entity 对象 | @mention entity 格式异常 | ❌ 未修复 |

- **已修复（5 个）**：问题 1, 2, 5, 6, 8 — 数据结构核心字段已对齐
- **未修复 P0（1 个）**：问题 3 — ID 类型仍为字符串
- **未修复 P1（6 个）**：问题 4, 7, 9, 10, 11, 13, 14 — 功能缺失或行为不一致
- **未修复 P2（1 个）**：问题 12 — Markdown 支持

---

## 四、建议

1. **数据结构优先对齐 Telegram 规范**（P0 项）：User 对象补全 `first_name`、`is_bot`、`id` 类型修正；Chat 对象修正 `id`、`type`。这些是基础中的基础，修复后可大幅降低开发者适配成本。

2. **补充核心 API 方法**（P1 项）：`sendChatAction`、`editMessageText`、`reply_to_message_id` 是 Bot 常用功能，缺失会严重限制 Bot 的交互能力。

3. **提供官方兼容性文档**：建议 Zapry 提供一份官方的 API 差异文档，明确哪些 Telegram Bot API 功能已支持、哪些尚未支持，帮助开发者提前规划。

4. **统一数据类型**：所有 `id` 字段统一为整数类型，与 Telegram 标准一致。

---

## 五、附录：我方当前兼容处理

为应对上述差异，我们在代码中做了以下适配：

- **Monkey Patch 层**（`utils/private_api_bot.py`）：在 SDK 反序列化层拦截并修正数据格式
- **降级策略**：所有不支持的 API 调用均用 try-except 包裹，失败时降级为可用方案
- **Markdown 清理**（`utils/zapry_compat.py`）：发送前自动清除所有 Markdown 标记

### 2026-02 更新

感谢 Zapry 团队修复了问题 1, 2, 5, 6, 8，这些修复大幅提升了基础数据结构的兼容性。

我方已将对应的兼容代码调整为**防御性模式**（保留逻辑但降级日志），确保：
- 即使 Zapry 侧回退，Bot 仍能正常运行
- 不会产生额外的性能开销
- 日志中不再出现已修复问题的 INFO 级别告警

**仍需 Zapry 修复的核心问题**（按优先级排序）：
1. **问题 3**（P0）：ID 字段统一为整数类型 — 这是最基础的类型安全问题
2. **问题 7**（P1）：群聊 ID 去掉 `g_` 前缀，改为标准负整数
3. **问题 9-11**（P1）：补充 `sendChatAction`、`editMessageText`、`reply_to_message_id` 支持
4. **问题 14**（P1）：@mention entity 格式标准化（`offset`/`length` 正确）

---

*文档最后更新：2026-02-12*
