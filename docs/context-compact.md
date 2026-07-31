# Context Compact — 上下文压缩管线

> **创建**: 2026-07-31 | **作者**: Dev
> **关联**: Issue #8
> **参考**: [learn-claude-code s08](https://github.com/shareAI-lab/learn-claude-code/tree/main/s08_context_compact)

---

## 1. 问题

当前上下文窗口无上限：`context.get_recent_messages` 读取全部消息，AIAgent 循环内每 turn 回填工具结果，messages 只增不减。长对话必然顶到 DeepSeek 64K 上下文，API 返回 413（`payload_too_large`），此前直接降级为模拟回复。

## 2. 架构

```
ai_service.py          ← 承上启下：数据准备 + 持久化（不变）
  └── agent.py (AIAgent.run)
        ├── context_compact.snip_compact()    ← L1: 裁剪中间消息（0 API）
        ├── context_compact.micro_compact()   ← L2: 旧工具结果占位（0 API）
        ├── compact_history()                 ← L4: token 超阈值 → LLM 摘要（1 API）
        └── streaming.retry_deepseek_chat()
              └── reactive_compact()          ← 413 → 应急压缩 → 重试（1 API）
```

**分层边界**（与 #10 错误恢复一致）：
- `ai_service` 只做承上启下，不感知压缩
- `agent` 每 turn 调 LLM 前跑便宜层（L1/L2），超阈值跑 L4
- `streaming` 负责与模型打交道：413 时压缩后重试
- `context_compact.py` 是纯函数模块：只操作内存 messages（OpenAI/DeepSeek 兼容格式），不碰 DB

## 3. 压缩管线

核心原则：**便宜的先跑，贵的后跑**。执行顺序照 CC 源码（budget → snip → micro → auto）。

### L1: snip_compact — 裁剪中间消息

消息数 > 50 → 保留头部 3 条 + 尾部 47 条，中间替换为占位符 `[snipped N messages from conversation middle]`。

**配对保护**（OpenAI 格式）：`assistant(tool_calls)` 与其后的 `role=tool` 结果消息是 API 强制配对，裁剪边界若落在配对中间则整体移动边界。

### L2: micro_compact — 旧工具结果占位

保留最近 3 个 `role=tool` 结果，更早的（内容 > 120 字符）替换为 `[Earlier tool result compacted. Re-run if needed.]`。

只替换内容不删消息——占位不破坏 API 要求的配对结构。

### L4: compact_history — LLM 摘要

字符估算超阈值（60K，≈30K~50K token）→ 调 LLM 生成摘要，全部历史替换为单条摘要消息（保留 system）。

摘要 prompt 带硬约束：`CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.`（参考 CC 压缩 prompt 双端约束，防止摘要调用触发工具）。

**摘要失败容错**：跳过压缩返回原消息（上游主调用可能仍成功；若 413 由 reactive 兜底）。

### 应急: reactive_compact — 413 处理

主调用返回 413 → 保留尾部 5 条（配对保护）+ 头部 LLM 摘要 → 重试 1 次。

**摘要失败 → 强裁剪兜底**：system + 前 3 条 + 尾部 5 条，保证 413 一定有压缩动作，不会无限循环。

## 4. PAYLOAD_TOO_LARGE 联动（#10 遗留技术债闭环）

```python
RETRY_CONFIG[LLMStatus.PAYLOAD_TOO_LARGE] = {
    "max_retries": 1,
    "base_delay_ms": 0,
    "jitter": False,
    "compress_before_retry": True,   # 重试前对 messages 做 reactive_compact
}
```

`retry_deepseek_chat` 在重试前检查 `compress_before_retry` 标记 → 原地替换压缩后的 messages → 重试。413 重试后仍失败 → 维持既有 fallback 降级。

## 5. 关键设计决策

### 5.1 为什么放 agent 而不是 service

| 方案 | 评价 |
|------|------|
| service 组装时压缩一次 | ❌ 管不到 agent 内部 turn 间消息增长（工具结果回填） |
| **agent 循环内每 turn 压缩** | ✅ 参考 s08 做法；压缩只操作内存 messages，不违反「AIAgent = 纯 LLM 层 no DB」 |

### 5.2 为什么省略 s08 的 L3（tool_result_budget 持久化磁盘）

s08 的 L3 解决「read_file 读大文件」场景。本项目工具全是社区操作 API，无大文件读取；`ToolDef.execute()` 的 result_formatter 已清洗超大结果。不为不存在的场景写持久化机制。

### 5.3 token 估算为什么用字符数

DeepSeek 无官方 tokenizer（tiktoken 仅 OpenAI 模型）。用 `len(json.dumps(messages))` 字符近似，中文 1 字 ≈ 1 token，保守偏大——触发偏早不偏晚，安全。

### 5.4 压缩不动 DB

压缩只变换发送给 LLM 的 messages（内存），DB 始终保存完整历史。`ai_service` 持久化逻辑读取 `agent.get_log()`（turn_trace），与压缩后的 messages 无关。

## 6. 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_context_compact.py` | snip/micro/reactive 纯函数：裁剪、占位、配对不拆散、system 保留、摘要失败兜底（37 项） |
| `tests/test_harness_errors.py` | PTL 配置可重试 + 413 → 压缩 → 重试成功 / 重试耗尽 fallback |

全量测试：`155 passed, 4 skipped`（含 #8 新增 33 项）。

## 7. 关联

- [learn-claude-code s08](https://github.com/shareAI-lab/learn-claude-code/tree/main/s08_context_compact)
- [error-recovery.md](error-recovery.md) — PAYLOAD_TOO_LARGE 恢复策略更新
- Issue [#15 Model Adapter](https://github.com/ai-0516/lanyuan-base/issues/15) — 压缩管线的配对识别未来基于内部统一格式（adapter 化的受益者）
- Issue [#9 Memory](https://github.com/ai-0516/lanyuan-base/issues/9) — 跨会话记忆（压缩的长期补充）
