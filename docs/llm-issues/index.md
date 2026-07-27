# LLM 问题记录

> 记录 AI 对话中遇到的 LLM 行为问题及尝试过的解决方案。
> **目的**：避免重复踩坑，积累调优经验。

## 汇总

| # | 标题 | 日期 | 状态 |
|---|------|------|------|
| [001](001-tool-call-fabrication) | LLM 不调 create_post 工具，直接回复"已发布" | 2026-07-26 | ✅ |
| [002](002-history-truncation) | 截断历史后 LLM 行为不一致 | 2026-07-26 | ✅ |
| [003](003-reasoning-content) | DeepSeek 推理模型要求传回 `reasoning_content` | 2026-07-27 | ✅ |
| [004](004-tool-data-overload) | tool 返回巨量原始数据，LLM 看不懂且撑爆消息体 | 2026-07-27 | ✅ |
