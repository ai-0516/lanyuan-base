---
title: "DeepSeek 推理模型要求 assistant 消息必须传回 reasoning_content"
date: 2026-07-27
status: resolved
---

## 症状

AI 回复「AI回复被中断，请重试」。Server log 显示：

```
DeepSeek API error: status=400
body={"error":{"message":"The `reasoning_content` in the thinking mode must be passed back to the API."}}
```

## 触发条件

Agent Loop 中的第二轮调用（tool 结果回填后再次请求 DeepSeek）返回 400。

## 根因

DeepSeek 推理模型（deepseek-v4-flash 等）在响应用户请求时，delta 中会包含 `reasoning_content` 字段（思考过程）。当模型在相同轮次中还发起了 tool_call，流结束后必须将 `reasoning_content` 原样传回第二轮请求的 assistant 消息中。

原代码在 `agent.py` 中构建 assistant 消息时只传了 `content: None` 和 `tool_calls: [...]`，漏掉了 `reasoning_content` 字段。DeepSeek API 校验到缺少该字段后拒绝请求（400）。

## 解决方案

1. **`streaming.py`**：在解析 SSE delta 时捕获 `reasoning_content`，累积后 yield `("reasoning", 完整文本)` 事件
2. **`agent.py`**：处理 `"reasoning"` 事件，保存到 `self._reasoning_content`。回填 assistant 消息时判断若有 `reasoning_content` 则加入消息体

## 验证

jsonl 日志中第二轮消息的 assistant 条目包含 `"reasoning_content": "用户想查看自己发布的帖子..."`，对话正常完成。
