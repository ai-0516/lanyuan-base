# LLM 问题记录

> 记录 AI 对话中遇到的 LLM 行为问题及尝试过的解决方案。
> **目的**：避免重复踩坑，积累调优经验。

## 汇总

| # | 标题 | 模块 | 日期 | 状态 |
|---|------|------|------|------|
| [001](001-tool-call-fabrication) | LLM 编造回复不调工具 + 截断历史后行为不一致 | `replay-llm` | 2026-07-26 | ✅ |
| [002](002-reasoning-content) | DeepSeek 推理模型要求传回 `reasoning_content` | `@tool` annotation | 2026-07-27 | ✅ |
| [003](003-tool-data-overload) | tool 返回巨量原始数据，LLM 看不懂且撑爆消息体 | `@tool` annotation | 2026-07-27 | ✅ |

> **模块说明**：
> - `replay-llm` — CLI 命令行工具，重放 LLM 请求日志，支持截断历史对比行为差异
> - `@tool` annotation — 通过装饰器将 router 函数自动注册为 AI 工具，含 `result_formatter`、base64 清洗、SQLAlchemy 模型自动转 dict 等机制
