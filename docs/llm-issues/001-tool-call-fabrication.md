---
title: "LLM 不调 create_post 工具，直接回复\"已发布\""
date: 2026-07-26
status: resolved
---

## 症状

用户多次要求发帖，AI 每次都回复"帖子已发布成功"，但日志显示 `finish_reason=stop, tool_calls=0`，DeepSeek 根本没有调用 `create_post` 工具。

## 根因

历史消息中存的是 LLM 编造的"帖子已发布"纯文本。当构建上下文传给 DeepSeek 时，模型从历史中学到了"直接写文字回复就行，不用调工具"。

## 解决方案

**回填真实 tool_call 结构**（commit `294a8d1`）：

1. **Message 模型**：`role` 改为 `String(20)`（支持 "tool" 角色），新增 `tool_calls`(Text) 和 `tool_call_id`(String) 字段，`content` 改为可空
2. **保存逻辑**：Agent Loop 结束后，不再只存最终文本，而是逐轮从 `agent.get_log()` 取数据：
   - 有 tool_calls 的轮次 → 存 `role="assistant"` + `tool_calls` JSON
   - 工具执行结果 → 存 `role="tool"` + `tool_call_id`
   - 最终纯文本 → 存 `role="assistant"` + `content`
3. **上下文重建**（`context.py`）：`build_deepseek_messages()` 根据 DB 中的 role/tool_calls/tool_call_id 重建 OpenAI 兼容的消息结构
4. **SSE 过滤**（commit `c0965e9`）：`tool_call` 内部事件不再透传给前端，避免 `[object Object]` 显示问题

## 验证

新建会话后，连续三次"再发一次"请求，DeepSeek 每次都返回 `finish_reason=tool_calls`，agent 正常执行 `create_post`：

| # | 用户消息 | finish_reason | tool_calls | post_id |
|---|----------|---------------|------------|---------|
| 1 | 帮我发帖租个房子，98平就行（→ 追问详情 → 补充三室/高层/暖气） | tool_calls | create_post | 19 |
| 2 | 把这个帖子再发一次 | tool_calls | create_post | 20 |
| 3 | 再发一次 | tool_calls → stop | create_post → 正常回复 | 21 |

## 诊断工具

调试过程中开发了 `replay-llm` 命令行工具：
- `--truncate N`：截取前 N 条消息重放，用于定位历史上下文污染的边界
- `--replay`：执行完整 Agent Loop（含多轮工具调用），模拟真实执行过程
- 日志记录在 `logs/llm-requests/YYYY-MM-DD.jsonl`，包含完整 turns[] 和 model/api_url

## 已验证的方向

| 方案 | 说明 | 结果 |
|------|------|------|
| 强化 System Prompt | 增加"你必须调用 create_post 工具...不能只回复文字" | ❌ 无效 |
| 修复 context window | 原来取最早 20 条改为取全部消息（DB/LLM 一致） | ❌ 无效 |
| 回填真实 tool_call | 让 LLM 在历史中看到真实的 tool_call + tool result 结构 | ✅ 有效 |

## 怀疑方向

1. **历史中存在幻觉回复** — AI 之前回复过"已发布"的文字，模型倾向于模仿自己的历史行为 ✅ **确认，已修复**
2. **System Prompt 被历史稀释** — 用户已经跟 AI 聊了 20+ 轮，历史消息中的 user/assistant 示例对模型行为的权重高于 system prompt ✅ 已验证
3. **DeepSeek system prompt 权重偏低** — 某些模型对 system 角色的指令敏感度低于对 user/assistant 对话示例的模仿 ✅ 已验证（通过回填真实 tool_call 解决）

## 后续优化方向（非必选）

- [ ] 在用户消息（user role）中追加强烈指令（而非仅在 system prompt 中强调）
- [ ] 在 Agent Loop 层做 fallback 逻辑：如果用户意图需要工具但 LLM 未触发，自动重试
