# LLM 问题记录

> 记录 AI 对话中遇到的 LLM 行为问题及尝试过的解决方案  
> **目的**：避免重复踩坑，积累调优经验

---

## 问题1：LLM 不调 create_post 工具，直接回复"已发布"

**日期**：2026-07-26
**状态**：✅ 已解决

### 症状

用户多次要求发帖，AI 每次都回复"帖子已发布成功"，但日志显示 `finish_reason=stop, tool_calls=0`，DeepSeek 根本没有调用 `create_post` 工具。

### 根因

历史消息中存的是 LLM 编造的"帖子已发布"纯文本。当构建上下文传给 DeepSeek 时，模型从历史中学到了"直接写文字回复就行，不用调工具"。

### 解决方案

**回填真实 tool_call 结构**（commit `294a8d1`）：

1. **Message 模型**：`role` 改为 `String(20)`（支持 "tool" 角色），新增 `tool_calls`(Text) 和 `tool_call_id`(String) 字段，`content` 改为可空
2. **保存逻辑**：Agent Loop 结束后，不再只存最终文本，而是逐轮从 `agent.get_log()` 取数据：
   - 有 tool_calls 的轮次 → 存 `role="assistant"` + `tool_calls` JSON
   - 工具执行结果 → 存 `role="tool"` + `tool_call_id`
   - 最终纯文本 → 存 `role="assistant"` + `content`
3. **上下文重建**（`context.py`）：`build_deepseek_messages()` 根据 DB 中的 role/tool_calls/tool_call_id 重建 OpenAI 兼容的消息结构
4. **SSE 过滤**（commit `c0965e9`）：`tool_call` 内部事件不再透传给前端，避免 `[object Object]` 显示问题

### 验证

新建会话后，连续三次"再发一次"请求，DeepSeek 每次都返回 `finish_reason=tool_calls`，agent 正常执行 `create_post`：

| # | 用户消息 | finish_reason | tool_calls | post_id |
|---|----------|---------------|------------|---------|
| 1 | 帮我发帖租个房子，98平就行（→ 追问详情 → 补充三室/高层/暖气） | tool_calls | create_post | 19 |
| 2 | 把这个帖子再发一次 | tool_calls | create_post | 20 |
| 3 | 再发一次 | tool_calls → stop | create_post → 正常回复 | 21 |

### 诊断工具

调试过程中开发了 `replay-llm` 命令行工具：
- `--truncate N`：截取前 N 条消息重放，用于定位历史上下文污染的边界
- `--replay`：执行完整 Agent Loop（含多轮工具调用），模拟真实执行过程
- 日志记录在 `logs/llm-requests/YYYY-MM-DD.jsonl`，包含完整 turns[] 和 model/api_url

### 已验证的方向

| 方案 | 说明 | 结果 |
|------|------|------|
| 强化 System Prompt | 增加"你必须调用 create_post 工具...不能只回复文字" | ❌ 无效 |
| 修复 context window | 原来取最早 20 条改为取全部消息（DB/LLM 一致） | ❌ 无效 |
| 回填真实 tool_call | 让 LLM 在历史中看到真实的 tool_call + tool result 结构 | ✅ 有效 |

### 怀疑方向

1. **历史中存在幻觉回复** — AI 之前回复过"已发布"的文字，模型倾向于模仿自己的历史行为 ✅ **确认，已修复**
2. **System Prompt 被历史稀释** — 用户已经跟 AI 聊了 20+ 轮，历史消息中的 user/assistant 示例对模型行为的权重高于 system prompt ✅ 已验证
3. **DeepSeek system prompt 权重偏低** — 某些模型对 system 角色的指令敏感度低于对 user/assistant 对话示例的模仿 ✅ 已验证（通过回填真实 tool_call 解决）

### 后续优化方向（非必选）

- [ ] 在用户消息（user role）中追加强烈指令（而非仅在 system prompt 中强调）
- [ ] 在 Agent Loop 层做 fallback 逻辑：如果用户意图需要工具但 LLM 未触发，自动重试

---

## 问题2：截断历史后 LLM 行为不一致

**日期**：2026-07-26
**状态**：✅ 已解决（关联问题 1，回填 tool_call 后此问题自然消失）

### 症状

用 `replay-llm --truncate 4` 只传前 4 条消息时，LLM 反过来向用户提问（"请补充帖子详细信息"）。

但在真实对话中（38 条完整历史），LLM 直接"发布"了帖子（实际没调工具，只是编了文字）。

### 根因

与问题 1 同根同源——历史中存在大量 LLM 编造的"帖子已发布"文字，模型要从历史中学到了"不用调工具也能发帖"。截断后去掉了污染数据，LLM 恢复了正常行为（反问用户），反而暴露了问题所在。

### 解决方案

回填真实 tool_call 结构后（问题 1 的修复），所有历史消息中都包含正确的 tool_call/tool 记录，模型不再从历史中学到坏习惯。截断/不截断的行为差异自然消失。

---

## 问题3：DeepSeek 推理模型要求 assistant 消息必须传回 `reasoning_content`

**日期**：2026-07-27
**状态**：✅ 已解决

### 症状

AI 回复「AI回复被中断，请重试」。Server log 显示：
```
DeepSeek API error: status=400
body={"error":{"message":"The `reasoning_content` in the thinking mode must be passed back to the API."}}
```

### 触发条件

Agent Loop 中的第二轮调用（tool 结果回填后再次请求 DeepSeek）返回 400。

### 根因

DeepSeek 推理模型（deepseek-v4-flash 等）在响应用户请求时，delta 中会包含 `reasoning_content` 字段（思考过程）。当模型在相同轮次中还发起了 tool_call，流结束后必须将 `reasoning_content` 原样传回第二轮请求的 assistant 消息中。

原代码在 `agent.py` 中构建 assistant 消息时只传了 `content: None` 和 `tool_calls: [...]`，漏掉了 `reasoning_content` 字段。DeepSeek API 校验到缺少该字段后拒绝请求（400）。

### 解决方案

1. **`streaming.py`**：在解析 SSE delta 时捕获 `reasoning_content`，累积后 yield `("reasoning", 完整文本)` 事件
2. **`agent.py`**：处理 `"reasoning"` 事件，保存到 `self._reasoning_content`。回填 assistant 消息时判断若有 `reasoning_content` 则加入消息体

### 验证

jsonl 日志中第二轮消息的 assistant 条目包含 `"reasoning_content": "用户想查看自己发布的帖子..."`，对话正常完成。

---

## 问题4：tool 返回巨量原始数据，LLM 看不懂且撑爆消息体

**日期**：2026-07-27
**状态**：✅ 已解决（通过 `result_formatter` 模式）

### 症状

数据过大导致 MySQL TEXT 溢出（DataError），且 LLM 收到的 tool 结果是 `<User object at 0x...>` 或含 base64 头像的百 KB JSON。

### 根因

`@tool` 装饰器默认返回 `json.dumps(api_success(result))` 的原始 API 响应。但 HTTP 客户端和 LLM 需要的是两套数据：

| 消费者 | 需要 | 问题 |
|--------|------|------|
| HTTP 客户端 | 完整数据（含头像、时间等） | — |
| LLM | 精炼摘要（作者、标题、点赞数） | 原始 JSON 含大量无用信息 |

具体问题：
1. **`list_posts`**：返回 15 条帖子，每条含 base64 头像（50-100KB/个）→ 总数据量数百 KB，MySQL TEXT 溢出
2. **`get_my_profile`**：`json.dumps(SQLAlchemy User object, default=str)` 输出 `<User object at 0x...>`，LLM 完全读不懂

### 解决方案

**引入 `result_formatter` 参数**（commit `4c1c0d7`）：

```python
def _format_posts(data: dict) -> str:
    items = data.get("items", [])
    return "\n".join(
        f"  #{p['id']} {p['user']['nickname']}：{p['content'][:80]} [{len(p['comments'])}条评论]"
        for p in items
    )

@tool(result_formatter=_format_posts)
async def list_posts(...):
    return api_success(result)
```

**执行流程**：
```
ToolDef.execute()
  → 调函数 → api_success(完整数据)
  → 自动解包取 data
  → 有 result_formatter？ → 调它，返回摘要文本
  → 没有？ → json.dumps + base64 清洗 + 50KB 截断兜底
```

同时增加两重兜底：
1. **SQLAlchemy 模型自动转 dict**：检测 `_sa_instance_state` 后用 `__table__.columns` 转为 `{col: value}`
2. **base64 头像清洗**：正则替换 `data:image/...;base64,...` 为空字符串

### 验证

- `list_posts`：从数百 KB 降到 ~200 字（"共 15 条帖子：\n  #24 一五OO：求租车位... [1条评论, 1赞]\n  ..."）
- `get_my_profile`：从 `<User object at 0x...>` 变为 `{"id": 10, "nickname": "一五OO", ...}`
- jsonl 日志中 `turns[].tool_results[].result` 内容精炼可读

### 教训

**每个 tool 都应该考虑给 LLM 看什么，而不只是把 HTTP 响应原样丢过去。** HTTP 要完整，LLM 要精炼，两者是不同需求。`result_formatter` 是 @tool 的可选参数，简单 tool 不需要，数据量大时必须用。
