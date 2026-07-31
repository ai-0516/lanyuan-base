# Error Recovery — LLM 调用错误分类与自动恢复

> **创建**: 2026-07-30 | **作者**: Dev
> **关联**: Issue #10, PR #12
> **参考**: [learn-claude-code s11](https://github.com/shareAI-lab/learn-claude-code/tree/main/s11_error_recovery)

---

## 1. 问题

Agent Loop 中 LLM 调用没有任何错误恢复机制。任何 API 错误（429 限流、529 过载、超时）都直接 yield error 给前端，对话中断。生产环境中 API 错误是常态，容错是 harness 的必备能力。

## 2. 架构

```python
ai_service.py
  └── agent.py (AIAgent.run)
        ├── retry_deepseek_chat()   ← L1: 退避重试
        │     └── deepseek_chat()   ← 错误分类 + 重大日志
        └── fallback mock reply     ← L2: 降级模拟回复
```

### 三层恢复

| 层 | 做的事 | 代码位置 | 触发条件 |
|----|--------|---------|---------|
| L1 | 指数退避重试 | `streaming.py:retry_deepseek_chat()` | 429/529/timeout；413（先应急压缩再重试，`compress_before_retry`） |
| L2 | 降级为模拟回复 | `streaming.py:retry_deepseek_chat()` fallback 分支 | 重试耗尽 / 不可重试错误 |
| L3 | 兜底异常捕获 | `agent.py` + `ai_service.py` try/except | 任意未捕获异常 |

L1 在前端无感知（等待期间 yield `token("AI 正在飞速思考中……")`），L2 用户看到"AI 暂时无法回复"，
L3 用户看到"AI 回复被中断"。

## 3. 错误码设计

### LLMStatus 枚举

```python
class LLMStatus(str, Enum):
    # HTTP 层
    RATE_LIMIT = "rate_limit"           # 429 → 退避重试 3 次
    OVERLOADED = "overloaded"           # 529/503 → 退避重试 3 次
    AUTH_FAILED = "auth_failed"         # 401/403 → 直接降级
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413 → 压缩上下文后重试 1 次（#8）
    BAD_REQUEST = "bad_request"         # 400 → 直接降级

    # 传输层
    TIMEOUT = "timeout"                 # 60s 超时 → 重试 1 次
    NETWORK_ERROR = "network_error"     # DNS/拒绝连接 → 重试 1 次
    SSE_DISCONNECTED = "sse_disconnected"   # 流中断 → yield error + 重大日志
    SSE_PARSE_ERROR = "sse_parse_error"     # JSON 解析失败 → 重大日志

    # 恢复层
    RETRY_EXHAUSTED = "retry_exhausted"     # 重试耗尽 → 降级（已被 fallback 事件替代，枚举值保留）
    UNEXPECTED = "unexpected_error"         # 未分类 → 降级
```

### HTTP 状态码映射

```python
HTTP_STATUS_MAP = {
    429: LLMStatus.RATE_LIMIT,
    529: LLMStatus.OVERLOADED,
    503: LLMStatus.OVERLOADED,
    401: LLMStatus.AUTH_FAILED,
    403: LLMStatus.AUTH_FAILED,
    413: LLMStatus.PAYLOAD_TOO_LARGE,
    400: LLMStatus.BAD_REQUEST,
}
```

### 恢复策略表

```python
RETRY_CONFIG = {
    LLMStatus.RATE_LIMIT:        {"max_retries": 3, "base_delay_ms": 500, "jitter": True},
    LLMStatus.OVERLOADED:        {"max_retries": 3, "base_delay_ms": 500, "jitter": True},
    LLMStatus.TIMEOUT:           {"max_retries": 1, "base_delay_ms": 1000, "jitter": False},
    LLMStatus.NETWORK_ERROR:     {"max_retries": 1, "base_delay_ms": 1000, "jitter": False},
    LLMStatus.PAYLOAD_TOO_LARGE: {"max_retries": 1, "base_delay_ms": 0, "jitter": False,
                                   "compress_before_retry": True},  # 413 → 应急压缩后重试（#8）
    LLMStatus.AUTH_FAILED:       None,  # 不可重试
    LLMStatus.BAD_REQUEST:       None,  # 不可重试
    LLMStatus.SSE_DISCONNECTED:  {"max_retries": 1, ...},  # 可重试 1 次（buffer 防重复）
    LLMStatus.SSE_PARSE_ERROR:   None,
    LLMStatus.UNEXPECTED:        None,
}
```

## 4. 退避算法

```python
def retry_delay(status, attempt, retry_after=None):
    if retry_after:
        return retry_after                     # 服务器 Retry-After 优先
    base = min(500 * (2 ** attempt), 32000)     # 指数增长，上限 32s
    if jitter:
        base += random(0, base * 0.25)          # + 0~25% 随机抖动
    return base / 1000                          # 转秒
```

| 尝试 | 基础 | + 抖动 |
|------|------|--------|
| 1 | 500ms | 0~125ms |
| 2 | 1000ms | 0~250ms |
| 3 | 2000ms | 0~500ms |
| 7+ | 32000ms（上限） | 0~8000ms |

## 5. SSE 流中断处理

> **注意**：这里的"断流"特指**后端 → LLM 的 HTTP SSE 请求**，不是前端 → 后端的 SSE 推送。
> 前端断线重连属于前端模块职责，不在此范围。

**流中断判断**：SSE 流结束但没有收到 `[DONE]` 标记，且已收到部分 token，且没有工具调用。

**当前方案**：可重试 1 次（`retry_deepseek_chat` 会缓存第一次调用的 token，只 yield 成功那次的结果，不会重复）。

若重试仍失败，记 `_critical_error` 日志（`logs/critical-errors.log`）。

**原因**：
- 重试时缓存 token，成功后只 yield 成功那次的结果，不会重复

**重大日志格式**：

```python
_critical_logger.error(
    "code=sse_disconnected ts=... tokens_before=42 has_tool_calls=false"
)
```

## 6. 关键设计决策

### 6.1 错误码为什么用 Enum

| 方案 | 评价 |
|------|------|
| 散落字符串 | ❌ 拼写风险，IDE 无补全 |
| 数字常量（429=限流） | ❌ `code == 429` 不直观 |
| **Enum 命名常量** | ✅ `code == LLMStatus.RATE_LIMIT` 语义明确 |

HTTP 状态码到枚举的映射在 `HTTP_STATUS_MAP` 内部完成，不暴露给外层。

### 6.2 恢复策略为什么用配置表

新增错误码只需加一行 `RETRY_CONFIG`，不改 if/else 链。不可重试的配置为 `None`，设计意图清晰。

### 6.3 日志在源文件记，不走事件 hook

**选择**：直接在 `streaming.py` / `agent.py` 里用 `logger.error/exception` 记录。

**原因**：
- 异常位置一目了然（文件名 + 行号）
- 日志内容灵活，不受事件数据结构限制
- 不需要外部消费者感知

**对比**：hook 适合结构化数据消费（jsonl 的请求日志），不适合异常排查。

### 6.4 async generator 的重试实现

`deepseek_chat()` 是 async generator（yield token → tool_call → done），不能直接用 try/except + while 循环包裹。

**方案**：`retry_deepseek_chat()` 是 wrapper generator，内部调用 `deepseek_chat()`，捕获 error 事件后回退重试。

- **首次尝试**（attempt 0）：直接 yield 所有事件，完全流式
- **重试**（attempt 1+）：缓存 token，成功后才一次性 yield（避免重复输出）
- 重试等待期间先 yield `token("AI 正在飞速思考中……")`，让前端有反馈
- 重试耗尽后 yield `fallback` 事件（不再 yield `error`），agent.py 收到后转为降级回复

```python
for attempt in range(_max_possible):
    error_data = None
    is_retry = attempt > 0
    retry_buf = []

    async for event, data in deepseek_chat(messages, tools=tools):
        if event == "error":
            error_data = data
            break
        if is_retry:
            retry_buf.append((event, data))  # 重试时缓存
        else:
            yield (event, data)  # 首次尝试：直接流式

    if error_data is None:
        if is_retry and retry_buf:
            for evt, dat in retry_buf:
                yield (evt, dat)
        return

    # 错误处理... 重试决策...
    yield ("token", "AI 正在飞速思考中……")  # 填充等待
    await asyncio.sleep(delay)
```

**`fallback` 事件**：重试耗尽时 yield `("fallback", {"message": "..."})` 代替原来的 `("error", {"code": RETRY_EXHAUSTED})`，agent.py 按事件类型友好处理，不再感知重试细节。

## 7. 排除的方案

| 步骤 | 概念 | 排除理由 |
|------|------|---------|
| s03 Permission | 工具执行前权限判断 | 工具都是后端 API，不存在本地权限问题 |
| s05 Todo Write | Agent 任务规划 | 社区助手任务简单，不需要 |

## 8. 关联

- [2026-07-30 daily log](daily/2026-07-30.md)
- [learn-claude-code s11](https://github.com/shareAI-lab/learn-claude-code/tree/main/s11_error_recovery)
- [context-compact.md](context-compact.md) — 上下文压缩管线（#8，PAYLOAD_TOO_LARGE 压缩重试的基础）
- Issue [#8 Context Compact](../issues/8) — 已实现，413 压缩重试闭环
- Issue [#9 Memory](../issues/9) — 跨会话记忆（后续模块）
