# 钩子系统文档

## 架构

```
生产者 ── emit(name, data) ──→ asyncio.Queue ──→ consumer Task ──→ 消费者 handler
                                                                      │
                                                              @on("agent:start")
                                                              @on("llm:start")
                                                                  ...
```

**角色：**

| 层 | 模块 | 职责 |
|----|------|------|
| 生产者 | `agent.py` | 编排层，发出所有事件 |
| 管道 | `hooks/events.py` | 同步 put_nowait 入队，后台 consumer 循环消费 |
| 消费者 | `hooks/log.py` | 终端日志（用户消息 + AI 决策） |
| 消费者 | `hooks/jsonl.py` | JSONL 文件日志（完整轮次数据） |

## 设计原则

1. **钩子是辅助功能** — 移除所有钩子，核心流程不受影响
2. **生产者不关心消费者** — emit() 只负责将 Event 丢进队列，不知道也不关心多少 handler 在处理
3. **消费者不关心生产者** — handler 只接收 `data: dict`，不知道事件从哪来
4. **异常不传播** — handler 抛异常只记日志，不阻断 consumer 循环
5. **加新钩子不改现有代码** — 新建文件写 `@on(...)` 即可

6. **并行安全** — 需要跨事件积累状态的钩子通过 `req_id` 区分不同请求，用 dict 而非全局变量

## 事件清单

所有事件名和数据定义在 `events.py`，通过 TypedDict 提供类型提示。

| 事件常量 | 数据形状 | 触发时机 | 注册的 handler |
|----------|---------|---------|---------------|
| `AGENT_START` | `AgentStartData` | AIAgent.run() 入口 | log + jsonl |
| `TURN_START` | `TurnStartData` | 每轮开始时，初始化本轮数据 | jsonl |
| `LLM_START` | `LlmStartData` | LLM 调用前，填入 messages/tools | jsonl |
| `LLM_END` | `LlmEndData` | LLM 返回后，记录 AI 决策 | log + jsonl |
| `TOOL_START` | `ToolStartData` | 每个工具执行前 | log |
| `TOOL_END` | `ToolEndData` | 每个工具执行后，追加结果到本轮 | jsonl |
| `TURN_END` | `TurnEndData` | 每轮结束时，保存本轮到 entry | jsonl |
| `AGENT_END` | `AgentEndData` | 循环结束，写入文件 | jsonl |

### AgentStartData

```python
class AgentStartData(TypedDict):
    meta: dict          # {"session_id": int, "user_message": str}
```

### TurnStartData

```python
class TurnStartData(TypedDict):
    turn: int           # 当前轮次（从 0 开始）
```

### TurnEndData

```python
class TurnEndData(TypedDict):
    turn: int

### LlmStartData

```python
class LlmStartData(TypedDict):
    turn: int           # 当前轮次（从 0 开始）
    messages_sent: list  # 发送给 LLM 的完整 messages 列表
    tools_sent: list | None  # 发送的 tool definitions
```

### LlmEndData

```python
class LlmEndData(TypedDict):
    turn: int
    finish_reason: str       # "stop" | "tool_calls" | "error"
    tokens: int              # 本轮 token 数
    content: str             # AI 回复全文
    tool_calls: list         # model 请求的工具调用列表
    tool_calls_count: int
```

### ToolStartData

```python
class ToolStartData(TypedDict):
    tool_name: str    # "create_post", "get_profile" 等
    tool_call_id: str # LLM tool call 的 id
```

### ToolEndData

```python
class ToolEndData(TypedDict):
    tool_name: str
    tool_call_id: str
    result: str       # 工具执行结果
```

### AgentEndData

```python
class AgentEndData(TypedDict):
    total_turns: int
    error: str | None  # 正常退出为 None，超限时为错误信息
```

## 文件结构

```
app/harness/
├── agent.py               # 生产者：所有 events.emit() 调用
├── tool_registry.py        # 纯工具调度，不依赖事件
└── hooks/
    ├── __init__.py         # 导入即激活
    ├── events.py           # 事件系统（Event, on, emit, reset, 常量, TypedDict）
    ├── log.py              # 终端日志（用户消息 + AI 决策）
    └── jsonl.py            # JSONL 日志（完整轮次数据）
```

## 添加新钩子

三步：

### 1. 定义事件（如不需要可不加）

`events.py` 已有事件可能已覆盖需求。需要加新事件时：

```python
# events.py
AGENT_DECISION = "agent:decision"

class AgentDecisionData(TypedDict):
    turn: int
    action: str
    reasoning: str
```

### 2. 写钩子文件

```python
# app/harness/hooks/stats.py
import time
from app.harness.hooks import events
from app.harness.hooks.events import on

_start_time = 0.0

@on(events.AGENT_START)
async def on_start(data: events.AgentStartData):
    global _start_time
    _start_time = time.monotonic()

@on(events.AGENT_END)
async def on_end(data: events.AgentEndData):
    elapsed = time.monotonic() - _start_time
    print(f"[stats] agent 耗时 {elapsed:.2f}s, {data['total_turns']} 轮")
```

### 3. 注册到 `__init__.py`

```python
# app/harness/hooks/__init__.py
from . import events
from . import jsonl
from . import log
from . import stats  # ← 加一行
```

完成。不需要改 producer 的代码。

## 测试

```python
# tests/test_hooks.py — 测试自有事件
from app.harness.hooks.events import on, emit, reset

@on("my:event")
async def my_handler(data: dict):
    results.append(data["x"])

results = []
emit("my:event", {"x": 1})
await _drain()
assert results == [1]
```

内置钩子验证：

```python
from app.harness.hooks import events

assert "tool:start" in events._handlers   # log 注册了
assert "agent:end" in events._handlers    # log + jsonl 各一个
```
