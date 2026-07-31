# NeMo Relay vs replay-llm 对比分析

> 调研日期：2026-07-29  
> 分析目标：Hermes Agent 新集成的 NeMo Relay 可观测性方案，寻找 replay-llm 可借鉴的设计

---

## 一、两个系统全景对比

| 维度 | NeMo Relay（Hermes） | replay-llm（兰园项目） |
|------|---------------------|----------------------|
| **定位** | NVIDIA 运行时 + 可观测性中间件 | 纯调试 CLI 工具 |
| **数据源** | 拦截运行时 API 请求/响应（实时） | 回读 JSONL 日志文件（离线） |
| **输出格式** | ATOF（JSONL 流）+ ATIF（轨迹 JSON） | 终端打印 + 模拟 Loop |
| **消费方式** | 插件式（可注册多个 observer） | 单一 CLI 命令 |
| **架构层级** | provider 拦截层 + agent 生命周期 + 可观测插件 | 日志读取 + LLM 回放 |

---

## 二、NeMo Relay 的核心设计

### 2.1 Observer Hooks 契约（`hermes.observer.v1`）

Hermes 定义了一个**与后端无关的可观测性接口**，分为 4 族：

```
Session Lifecycle    用户会话起止（on_session_start/end/finalize/reset）
Turn-Scoped LLM      用户轮次范围（pre_llm_call / post_llm_call）
Request-Scoped API   单次API请求（pre_api_request / post_api_request / api_request_error）
Tool Lifecycle       工具调用（pre_tool_call / post_tool_call / transform_tool_result）
```

**关键区别：** Hermes 区分了**逻辑轮次（turn）**和**物理请求（API request）**。一个 turn 内可能包含多个 API 请求（工具调用多次），用不同的 ID 层级来关联：

```
session_id → turn_id → api_request_id → tool_call_id
                                  ↓
                            api_call_count（重试计数）
```

### 2.2 Correlation ID 体系

```python
session_id    # 对话身份
task_id       # 子任务身份（subagent 场景）
turn_id       # 用户轮次身份（共享于同一轮的工具和 API 请求）
api_request_id  # 单次 provider 请求的不透明 ID
api_call_count  # 同一 turn 内 API 请求的尝试次数（含重试）
tool_call_id    # provider 返回的 tool_call ID
```

相比我们目前的实现：我们只有 `session_id` 和 `tool_call_id`，缺少 `turn_id` 和 `api_request_id`。

### 2.3 输出格式：ATOF + ATIF

**ATOF（Agent Trajectory Observability Format）：** JSONL 事件流，每条一个事件，记录完整的 LLM 和工具生命周期。**本质上和我们 JSONL 日志做的事一样**。

**ATIF（Agent Trajectory Interchange Format）：** **结构化的轨迹 JSON**，将多个相关事件聚合成一个可重放、可评估、可分析的统一记录。ATIF 包含：
- Turn-by-turn 的 tool 输入/输出
- 完整的 usage 和 timing 元数据
- 子 agent 轨迹嵌入
- 确定的非 LLM 编排步骤

**这才是我们的 replay-llm 最值得借鉴的地方！**

### 2.4 Provider Codec（编解码层）

Relay 在 provider 请求/响应路径上插入了**可还原 codec**：

```
provider_request → Relay codec encode → managed execution → Relay codec decode → response
                                                                      ↓
                                                               interceptors 可改写
```

这是 NVidia 给 Relay 设计的核心能力：codec 做"无损往返"编码，interceptor 在中间做改写。我们 `_strip_avatar` 做的就是类似的事，但没有做成清晰的 intercept 层。

---

## 三、我们的 hooks 系统 vs NeMo Observer Hooks

| 维度 | 我们的 hooks | Hermes Observer |
|------|-------------|-----------------|
| 事件数 | 8（agent/turn/llm/tool x start/end） | 11+（session/turn/API/tool/approval） |
| 驱动方式 | `emit()` → Queue → consumer | `ctx.register_hook()` 注册回调 |
| fail-open | 钩子异常只记日志不抛 | ✓ 同样 fail-open |
| 类型系统 | TypedDict 基类 | `**kwargs` 宽松 + `telemetry_schema_version` |
| 返回值 | 忽略（纯辅助） | 部分可改写行为（`pre_tool_call` 可 block） |

**观察：** 我们的架构方向是对的（事件驱动 + fail-open + 纯辅助），但 Hermes 在**事件粒度**和**ID 体系**上更精细。

---

## 四、replay-llm 可借鉴的改进

### 1️⃣ 高优先级：结构化轨迹导出（ATIF 模式）

目前 replay-llm 只在终端打印 + 模拟执行。如果能导出一个**标准化的轨迹文件**，可以：

- 离线分析 LLM 行为模式
- 对比不同 system prompt 的效果
- 复现并分享异常行为给团队成员
- **面试素材**：一个完整的「记录 → 导出 → 分析 → 重放」闭环

**提议格式（简版 ATIF）：**

```json
{
  "schema": "lanyuan.trajectory.v1",
  "request_id": "req_20260726_...",
  "session_id": 5,
  "model": "deepseek-v4-flash",
  "user_message": "帮我发个求租帖",
  "turns": [
    {
      "turn": 1,
      "finish_reason": "tool_calls",
      "usage": {"prompt_tokens": 1200, "completion_tokens": 180},
      "assistant_content": "好的，我来帮你发布！",
      "tool_calls": [
        {
          "tool_call_id": "call_xxx",
          "tool_name": "create_post",
          "arguments": {"content": "🏠 求租..."},
          "result": {"success": true, "post_id": 21}
        }
      ]
    },
    {
      "turn": 2,
      "finish_reason": "stop",
      "usage": {"prompt_tokens": 1500, "completion_tokens": 80},
      "assistant_content": "发布成功啦！🎉"
    }
  ],
  "total_usage": {"prompt": 2700, "completion": 260},
  "duration_ms": 12500,
  "error": null
}
```

**实现成本：** 低。JSONL 里已经有 `turns` 字段，只是加一个导出命令 `replay-llm --id xxx --export trajectory.json`

### 2️⃣ 中优先级：完善 Correlation ID 体系

在我们的 hooks 事件中补充 `turn_id` 和 `api_request_id`：

| 当前 | 建议加入 |
|------|---------|
| `req_id`（全局唯一） | 保留 `req_id` |
| `session_id` | 保留 |
| （缺） | 新增 `turn_id` — 由 `agent.py` 在每轮循环开始时生成 |
| （缺） | 新增 `api_call_count` — 同一轮内 API 请求计数 |

**好处：** 日志中能精确关联"一次用户请求 → N 轮 Agent Loop → M 次 API 调用"，而不是平铺的事件流。

### 3️⃣ 中优先级：区分 Turn Scope 和 API Request Scope

当前事件层级：

```
agent:start → turn:start → llm:start → llm:end → tool:start → tool:end → ... → turn:end → agent:end
```

建议调整为：

```
agent:start → turn:start
                ├── api:start (api_call_count=1)
                │   ├── llm:start → llm:end
                │   └── [tool loop] tool:start → tool:end
                ├── api:start (api_call_count=2)
                │   ├── llm:start → llm:end
                │   └── [tool loop] ...
                └── ...
              → turn:end → agent:end
```

**好处：** 工具调用导致的多轮 API 请求在日志中清晰可见，且能区分"第一轮 LLM 返回了哪些工具"和"第二轮 LLM 又调了什么"。

### 4️⃣ 低优先级：Provider Codec 层（refine 已有逻辑）

当前 `_strip_avatar` 嵌入在 `ToolDef.execute()` 中，不是独立的代码层。可以改为：

```python
# 在发送给 LLM 前经过 codec
class ProviderCodec:
    """Provider 请求/响应编解码"""
    
    def encode_request(self, body: dict) -> dict:
        """发送前：去掉 avatar、截断等"""
        return strip_avatar(truncate_large(body))
    
    def decode_response(self, response: dict) -> dict:
        """收到后：提取 usage、重建 tool_calls 等"""
        return extract_usage(response)
```

**好处：** 职责清晰，方便测试，扩展性更好（想加敏感字段过滤、格式转换都很方便）。

### 5️⃣ 低优先级：Tool 调用去重统计

Hermes ATIF 用 `tool_call_id` 去重统计工具调用次数（重试不计入唯一调用）。我们的 `stats.py` 当前只统计 token，可以加入按工具名分的调用频次统计。

---

## 五、总结：借鉴优先级

| 优先级 | 改进点 | 预估工作量 | 面试价值 |
|--------|--------|-----------|---------|
| 🔴 P0 | 结构化轨迹导出（ATIF 模式） | ~半天 | ⭐⭐⭐⭐⭐ |
| 🟡 P1 | Correlation ID 补充（turn_id / api_request_id） | ~半天 | ⭐⭐⭐⭐ |
| 🟡 P1 | Turn/API 请求 scope 区分 | ~1天 | ⭐⭐⭐⭐ |
| 🟢 P2 | Provider Codec 层 | ~半天 | ⭐⭐⭐ |
| 🟢 P2 | Tool 调用去重统计 | ~2小时 | ⭐⭐ |

**最推荐的第一个改进：结构化轨迹导出**

> 目前 replay-llm 能重放 Agent Loop，但重放结果只输出到终端。  
> 如果能导出标准化的轨迹 JSON，就能做离线分析、prompt 调优对比、错误复现分享——这是从"调试工具"到"可观测性平台"的关键一步。

---

## 关联文档

- [ATIF 概述](docs/atif.md)
- [工作流定义](../issues/1)
- [结构化轨迹导出 Issue](../issues/2)
