# LLM 问题记录

> 记录 AI 对话中遇到的 LLM 行为问题及尝试过的解决方案  
> **目的**：避免重复踩坑，积累调优经验

---

## 问题1：AI 不调 create_post 工具（幻觉）

**日期**：2026-07-25  
**状态**：❌ 未解决

### 症状

用户要求 AI 发帖（如"帮我租一个车位"），AI 回复"帖子已发布成功"，但 DB 中并没有记录。从日志确认：

```
LLM response: tokens=132 finish_reason=stop    ← stop，不是 tool_calls
Agent Loop: done after 1 turn(s)               ← 一轮就结束了
```

LLM 生成了纯文本回复，未触发 `create_post` 工具调用。

### 尝试过的方案

| 方案 | 说明 | 结果 |
|------|------|------|
| 强化 System Prompt | 增加"你必须调用 create_post 工具...不能只回复文字" | ❌ 无效 |
| 修复 context window | 原来取最早 20 条改为取全部消息（DB/LLM 一致） | ❌ 无效 |

### 怀疑方向

1. **System Prompt 被历史稀释** — 用户已经跟 AI 聊了 20+ 轮，历史消息中的 user/assistant 示例对模型行为的权重高于 system prompt
2. **DeepSeek system prompt 权重偏低** — 某些模型对 system 角色的指令敏感度低于对 user/assistant 对话示例的模仿
3. **历史中存在幻觉回复** — AI 之前回复过"已发布"的文字，模型倾向于模仿自己的历史行为

### 下一步尝试

- [ ] 在用户消息（user role）中追加强烈指令（而非仅在 system prompt 中强调）
- [ ] 在 Agent Loop 层做 fallback 逻辑：如果用户意图需要工具但 LLM 未触发，自动重试
- [ ] 在 messages 中注入一条 `role: "user"` 的工具使用示例（few-shot）
- [ ] 检查 DeepSeek API 返回的 finish_reason 字段确认是否收到 tool_calls

---

## 问题2：截断历史后 LLM 行为不一致

**日期**：2026-07-26
**状态**：❌ 未解决

### 症状

用 `replay-llm --truncate 4` 只传前 4 条消息时，LLM 反过来向用户提问（"请补充帖子详细信息"）。

但在真实对话中（38 条完整历史），LLM 直接"发布"了帖子（实际没调工具，只是编了文字）。

这说明：**历史上下文中的消息数量和质量直接影响 LLM 的决策**。历史越长，LLM 越倾向于模仿自己的历史行为（包括编造回复）。

### 关联

与问题 1 是同根同源——历史中存在大量 LLM 编造的"帖子已发布"文字，模型从历史中学到了"不用调工具也能发帖"。
