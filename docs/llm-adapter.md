# LLM Adapter — 模型厂商/消息格式抽象

> **创建**: 2026-08-07 | **作者**: Dev
> **关联**: Issue [#15](https://github.com/ai-0516/lanyuan-base/issues/15)
> **参考**: [pi-ai](https://github.com/earendil-works/pi) `packages/ai` 多厂商抽象设计（canonical block 消息 + 协议转换 + 对称响应解析）

---

## 1. 问题

当前 harness 写死 DeepSeek（OpenAI 兼容格式）：

- `streaming.py` 直接调 `{DEEPSEEK_BASE_URL}/chat/completions`，请求/响应全部按 OpenAI SSE 结构硬编码（`choices[0].delta`、`reasoning_content`、tool_calls 按 index 合并）
- `agent.py` L257-270 回填 `{"role":"assistant","content":None,"tool_calls":[tc]}` + `{"role":"tool",...}` — OpenAI 形状
- `context_compact.py`（#8）配对识别判 `role=="assistant"`+`tool_calls` / `role=="tool"` — 与 OpenAI 格式强耦合
- `config.py` `DEEPSEEK_API_KEY/MODEL/BASE_URL` 前缀写死

**触发条件已满足**（2026-08-07 确认）：DeepSeek 官方提供两个协议端点（OpenAI 兼容 `/chat/completions` + Anthropic 兼容 `/v1/messages`，同一个 key）。「harness 需要支持 2+ 个协议格式时启动」成立，且 **`to_anthropic()` 可用现有 DeepSeek key 端到端验证**，无需真 Anthropic key。

## 2. 目标与非目标

### 目标

1. 定义内部统一消息格式（canonical，block 风格），贯穿 agent 循环 / 压缩管线 / DB 边界
2. Adapter 层：`to_openai()` / `to_anthropic()` 双向协议转换 + 对称的 SSE 响应解析
3. 配置化：`LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 四件套，不写死厂商前缀
4. `context_compact.py` 配对识别基于 canonical —— 一次实现，各家通用（#8 最大受益者）
5. `streaming.py` 按 `LLM_PROVIDER` 选择协议实现，事件契约不变

### 非目标

- **不做 DEEPSEEK_\* 兼容迁移**（2026-08-07 用户确认：当前单部署，直接换）
- **不做跨厂商兼容性修复层**（pi-ai 的 `transformMessages`：tool call ID 规范化 / 孤儿 toolCall 合成 / thinking 降级）。理由：无跨模型 handoff 场景，历史自产自销（canonical 单向转换），DeepSeek OpenAI 端点返回短 ID。将来真 Anthropic 报错时以实际报错为准，在 `to_anthropic` 内部补，够不上独立一层
- **不做 OpenAI 兼容厂商（Qwen/Moonshot/智谱）adapter**：走配置化，将来在 provider 映射表加一行（openai 协议 + 各自 base_url）
- 不改 `Message` 表结构（列式存储中立，见 §4.3）
- 不改事件契约（`token` / `reasoning_token` / `reasoning` / `tool_call` / `done` / `error` / `usage` / `fallback`）

## 3. 架构

canonical 贯穿 agent 层与压缩层，**协议转换只发生在 streaming 边界**：

```
DB(列式) ──ORM→canonical──> 内存 messages（agent 循环 / context_compact 全程 canonical）
                              ├─ to_openai()/to_anthropic() ─→ LLM 请求（按 LLM_PROVIDER 选协议）
                              └─ parse_*_sse() ←─ LLM 响应（归一成统一事件）
  回填 canonical → ORM 落库
```

分层边界：

- `ai_service.py`：承上启下，只做 ORM ↔ canonical 的进出转换（不感知协议）
- `agent.py`：agent 循环，messages 一律 canonical；回填 canonical
- `context_compact.py`：纯函数模块，只操作内存 canonical messages
- `streaming.py`：唯一知道协议细节的地方（请求组装 + SSE 解析 + 重试 + mock）
- `adapters/`：纯函数转换（canonical ↔ 厂商格式 + SSE 行解析），无 IO、无状态

## 4. Canonical 消息模型

### 4.1 TypedDict 定义

新增 `app/harness/adapters/messages.py`：

```python
from typing import Literal, TypedDict, NotRequired


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


class ThinkingBlock(TypedDict):
    """DeepSeek reasoning_content 的对应物（推理模型思考过程）"""
    type: Literal["thinking"]
    thinking: str


class ToolCallBlock(TypedDict):
    """工具调用请求。arguments 用对象（Anthropic input 风格），to_openai 时序列化为字符串"""
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict


class UserMessage(TypedDict):
    role: Literal["user"]
    content: str | list[TextBlock]   # DB 来的原始文本是 str，直接透传


class AssistantMessage(TypedDict):
    role: Literal["assistant"]
    content: list[TextBlock | ThinkingBlock | ToolCallBlock]  # 三种 block 可共存


class ToolResultMessage(TypedDict):
    role: Literal["toolResult"]
    tool_call_id: str
    content: str


Message = UserMessage | AssistantMessage | ToolResultMessage
```

设计决策：

| 决策 | 理由 |
|---|---|
| 工具参数用**对象** | Anthropic `input` 就是对象；to_openai 时 `json.dumps` 序列化即可。反方向（解析响应）Anthropic 给对象、OpenAI 给字符串，统一到对象 |
| `toolResult` 独立 role | OpenAI 叫 `tool`、Anthropic 包在 `user` 里——canonical 独立，配对判断 `role == "toolResult"` 一句话 |
| 不加 `tool_name` / `is_error` | 配对靠 `tool_call_id` 足够；DB 无这些列；现状无错误标志需求。将来需要再加 |
| `thinking` block | DeepSeek 推理模型的 `reasoning_content`，agent 现状已回传该字段；canonical 化后放 assistant.content |
| user 的 `content` 允许 str | DB `content` 列就是 str，避免无谓包装 |

### 4.2 三格式对照（同一片段）

```python
# OpenAI（to_openai 输出）
{"role": "assistant", "content": None,
 "tool_calls": [{"id": "call_001", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]}
{"role": "tool", "tool_call_id": "call_001", "content": "晴，25°C"}

# Anthropic（to_anthropic 输出）
{"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_001",
                                   "name": "get_weather", "input": {"city": "北京"}}]}
{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_001", "content": "晴，25°C"}]}

# canonical（内存）
{"role": "assistant", "content": [{"type": "toolCall", "id": "call_001",
                                   "name": "get_weather", "arguments": {"city": "北京"}}]}
{"role": "toolResult", "tool_call_id": "call_001", "content": "晴，25°C"}
```

### 4.3 与 DB 列的映射（表结构不变）

| canonical | Message 表 |
|---|---|
| `user` / `assistant`（纯文本） | `role` + `content` |
| `assistant`（含 toolCall block） | `role="assistant"` + `content`（文本部分）+ `tool_calls` 列（toolCall blocks 序列化为 OpenAI 形状 JSON，保持旧数据兼容） |
| `toolResult` | `role="tool"` + `content` + `tool_call_id` |

## 5. Adapter 层（协议转换）

新增 `app/harness/adapters/openai.py` 与 `anthropic.py`，全部纯函数。

### 5.1 `to_openai(messages, system) -> list[dict]`

canonical → OpenAI 兼容请求体 messages（`/chat/completions`）：

- `system` 参数（若传入）→ 第一条 `{"role": "system"}`（现有组装由 `context.py` 负责，签名设计为接收已含 system 的完整列表也可，实现时二选一，倾向**接收完整 canonical 列表**，转换函数保持纯粹）
- `user`：`content` 字符串直传；若为 block 列表则取 text blocks 拼接
- `assistant`：
  - `thinking` blocks → 顶层 `reasoning_content` 字段（DeepSeek 扩展，多块拼接）
  - `text` blocks → `content`（拼接字符串；无文本则为 `None`）
  - `toolCall` blocks → `tool_calls` 数组：`{"id", "type": "function", "function": {"name", "arguments": json.dumps(arguments, ensure_ascii=False)}}`
- `toolResult` → `{"role": "tool", "tool_call_id", "content"}`

### 5.2 `to_anthropic(messages, system) -> list[dict]`

canonical → Anthropic 请求体 messages（`/v1/messages`）：

- `system`（若在列表中）→ **提取到顶层 `system` 参数**（Anthropic 不走 messages 的 system role）
- `user`（文本）→ `{"role": "user", "content": [{"type": "text", "text"}]}`
- `assistant`：block 直译
  - `text` → `{"type": "text", "text"}`
  - `thinking` → `{"type": "thinking", "thinking"}`（若端点不支持则实测后降级为 text 或丢弃，见 §10）
  - `toolCall` → `{"type": "tool_use", "id", "name", "input": arguments}`
- `toolResult` → **包进独立 `user` 消息**：`{"role": "user", "content": [{"type": "tool_result", "tool_use_id", "content"}]}`

**Anthropic 硬约束**（必须遵守）：

1. `tool_result` 只能出现在 `user` 消息里，且含 `tool_result` 的 user 消息**不能混入其他 block 类型** → 每个 toolResult 转成独立 user 消息，不与普通文本 user 合并
2. `tool_result` 必须紧跟其 `tool_use`（转换保持输入顺序即天然满足，前提是内存消息顺序正确——agent 回填保证）
3. 相邻多条 toolResult → 相邻独立 user 消息合法（也可合并，实现选独立，简单）

### 5.3 `to_anthropic_tools(tools) -> list[dict]`

tools 定义转换（工具 schema 本身不改造，继续用 OpenAI 形状作为内部标准）：

- `{"type": "function", "function": {"name", "description", "parameters"}}`
- → `{"name", "description", "input_schema": parameters}`

### 5.4 响应解析

统一产出事件（与现状 `streaming.py` 完全一致）：`token` / `reasoning_token` / `reasoning` / `tool_call` / `usage` / `done` / `error`。

#### openai 协议：`parse_openai_stream()`

从现状 `deepseek_chat` 的 SSE 解析逻辑**原样抽出**（`choices[0].delta`、`reasoning_content`、`content`、`tool_calls` index 合并、`usage`、`[DONE]`），不改变行为。

#### anthropic 协议：`parse_anthropic_stream()`

SSE 事件流（`event:` 行 + `data:` JSON）：

| SSE 事件 | 处理 |
|---|---|
| `message_start` | 记录 `message.usage.input_tokens` |
| `content_block_start` | `type=text` / `thinking` / `tool_use` → 开新块（tool_use 记录 id/name，input 片段累积） |
| `content_block_delta` | `text_delta` → yield `token`；`thinking_delta` → 累积后 yield `reasoning_token`；`input_json_delta` → 累积 `partial_json` |
| `content_block_stop` | 块结束：`tool_use` → `json.loads` 累积的 partial_json 得到 arguments → yield `tool_call` |
| `message_delta` | `usage.output_tokens`；`delta.stop_reason`（`tool_use` / `end_turn` / `max_tokens`） |
| `message_stop` | 流结束：有 tool_call → 不 yield `done`；纯文本 → yield `done` + `usage`（若拿到） |

错误处理与现状对齐：非 200 响应按 `HTTP_STATUS_MAP` 分类（AUTH_FAILED / BAD_REQUEST / RATE_LIMIT / TIMEOUT / SSE_DISCONNECTED / SSE_PARSE_ERROR / UNEXPECTED），`x-api-key` 认证失败映射同 401。

## 6. 配置

### 6.1 settings 变更（`app/config.py`）

```python
LLM_PROVIDER: str = "deepseek"      # deepseek | deepseek-anthropic | （未来）qwen/moonshot/zhipu/anthropic
LLM_BASE_URL: str = ""              # 空 → 按 provider 默认值
LLM_MODEL: str = "deepseek-chat"    # deepseek-chat | deepseek-reasoner（pro/flash 由部署方配置）
LLM_API_KEY: str = ""
```

删除：`DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL`（同步改所有引用点，grep 全量清理）。

### 6.2 provider 映射表

新增 `app/harness/adapters/providers.py`：

```python
PROVIDER_PROTOCOLS: dict[str, dict] = {
    "deepseek":           {"protocol": "openai",    "default_base_url": "https://api.deepseek.com/v1"},
    "deepseek-anthropic": {"protocol": "anthropic", "default_base_url": "https://api.deepseek.com/anthropic"},
    # 未来：qwen/moonshot/zhipu → openai 协议 + 各自 base_url；anthropic → anthropic 协议
}

def resolve_provider() -> dict:
    """按 settings.LLM_PROVIDER 解析协议与 base_url（LLM_BASE_URL 非空则覆盖默认）"""
```

### 6.3 `streaming.py` 改造

| 现状 | 改造后 |
|---|---|
| `deepseek_chat()` | `llm_chat()`：按 `resolve_provider().protocol` 路由到 openai 实现（现状逻辑）或 anthropic 实现（新写，`POST {base}/v1/messages`，headers `x-api-key` + `anthropic-version: 2023-06-01`，`stream: true`） |
| `retry_deepseek_chat()` | `retry_llm_chat()`：重试包装逻辑不变，内部改调 `llm_chat` |
| `mock_chat()` | 不变（无 key 时的降级路径，事件契约同） |
| `_summarize` 的调用点（context_compact） | 改调 `retry_llm_chat` 或 `llm_chat`（摘要不需要重试包装，用 `llm_chat`） |

## 7. 改造点逐文件

| 文件 | 改动 |
|---|---|
| `app/config.py` | `DEEPSEEK_*` → `LLM_*` 四件套（§6.1） |
| `app/harness/adapters/__init__.py` | 导出入口 |
| `app/harness/adapters/messages.py` | canonical TypedDict（§4.1） |
| `app/harness/adapters/openai.py` | `to_openai` + `parse_openai_stream`（从 streaming.py 抽出，行为不变） |
| `app/harness/adapters/anthropic.py` | `to_anthropic` + `to_anthropic_tools` + `parse_anthropic_stream`（新写） |
| `app/harness/adapters/providers.py` | `PROVIDER_PROTOCOLS` + `resolve_provider`（§6.2） |
| `app/harness/streaming.py` | 按协议路由（§6.3）；SSE 解析代码移入 adapters |
| `app/harness/agent.py` L257-270 | 回填 canonical：assistant 消息 content = `[{"type":"toolCall",...}]`（+thinking block 若 `self._reasoning_content`）；tool 结果 → `{"role":"toolResult","tool_call_id","content"}` |
| `app/harness/context_compact.py` | `_is_tool_call_message`：`role=="assistant"` 且 content 含 `toolCall` block；`_is_tool_result_message`：`role=="toolResult"`；占位符消息改 canonical（`{"role":"user","content":"[snipped N messages...]"}` → 保持 user + str 即兼容）；`_summarize` 改调 `llm_chat` |
| `app/harness/context.py` | `build_deepseek_messages` → `build_messages`：ORM → canonical（system 文本拼接保持；tool 消息 → `toolResult`；assistant+tool_calls → toolCall blocks；`reasoning_content` 列无（在 agent 回填内存态），DB 读出不涉及） |
| `app/services/ai_service.py` | `_to_openai_messages` → `_to_canonical_messages`（摘要/rotation 输入）；`_maybe_rotate` 的 `compress_context` 硬编码 → canonical 形状（toolCall block） |
| `app/harness/session.py` | `save_tool_call_message` / `save_tool_result_message` 入参改 canonical（内部转 DB 列；`tool_calls` 列仍存 OpenAI 形状 JSON 保持旧数据兼容） |

## 8. 测试计划

### 8.1 新增 `tests/test_adapters.py`

纯函数测试，无 HTTP（与现有模块级 mock 风格一致）：

- **to_openai**：纯文本 / 带 thinking / 带 toolCall（含 arguments 对象 → JSON 字符串断言）/ 混合（text + toolCall 共存）
- **to_anthropic**：纯文本 / thinking block / toolCall → `tool_use` / toolResult → `tool_result` 包独立 user 消息 / **相邻 toolResult 不合并** / system 提取到顶层
- **to_anthropic_tools**：schema 转换断言
- **parse_openai_stream**：文本流 / reasoning / tool_calls 多 chunk index 合并 / usage / `[DONE]`
- **parse_anthropic_stream**：`text_delta` → token / `thinking_delta` → reasoning_token / `input_json_delta` 跨 chunk 拼装 → tool_call / `message_delta.stop_reason=tool_use` / usage 汇总

### 8.2 存量测试更新

- `tests/test_context_compact.py`：配对测试数据改 canonical（`{"role":"assistant","content":[{"type":"toolCall",...}]}` + `{"role":"toolResult",...}`）；`_summarize` monkeypatch 不变
- `tests/test_agent.py`：fake_source 事件格式不变（`tool_call` 事件仍 OpenAI 形状 dict），**agent 回填后**的 messages 断言改 canonical；`streaming.mock_chat` 替换点改 `llm_chat`（若改名）
- `tests/test_services.py` / `tests/test_api.py`：事件契约不变，应零改动（回归验证）

### 8.3 端到端手动验证（不进 CI，CI 无 key）

1. `LLM_PROVIDER=deepseek` + 真实 key：现有 OpenAI 路径回归（对话 + 工具调用）
2. `LLM_PROVIDER=deepseek-anthropic` + 同一 key：验证 `to_anthropic` + anthropic SSE 解析（对话 + 工具调用 + 配对）
3. `LLM_API_KEY` 为空：mock 路径不变

## 9. 风险与待确认

| 项 | 说明 | 处置 |
|---|---|---|
| DeepSeek anthropic 端点对 thinking 的支持 | `deepseek-reasoner` 的思考过程经 anthropic 端点是否以 `thinking_delta` 返回，文档未明 | 实测为准；不支持的模型自然无该事件，`reasoning_token` 缺失不影响主流程 |
| `reasoning_content` 字段（openai 协议） | DeepSeek 扩展字段，其他 OpenAI 兼容厂商（Qwen/Moonshot）是否接受 | 当前唯一 openai 厂商是 DeepSeek；将来接入新厂商报错再加开关，不预判 |
| `tools` 参数格式 | 内部工具 schema 保持 OpenAI 形状，`to_anthropic_tools` 转换 | 已在 §5.3 覆盖 |
| 413 应急压缩（`llm_reactive_compact`） | 触发后重试走同一协议路由，无额外改造 | 回归覆盖 |

## 10. 实施顺序

1. `adapters/messages.py`（canonical 类型）+ `adapters/openai.py`（抽出）+ `adapters/providers.py` + config 四件套 → 测试
2. `adapters/anthropic.py`（to_anthropic + parse）→ 单测
3. `streaming.py` 按协议路由 → 存量测试回归
4. `agent.py` / `context_compact.py` / `context.py` / `ai_service.py` / `session.py` canonical 化 → 存量测试更新 + 回归
5. 端到端手动验证（§8.3）
