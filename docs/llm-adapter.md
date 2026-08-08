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
    tool_name: NotRequired[str]   # agent 回填必带；DB 回读时按 tool_call_id 从 assistant tool_calls 匹配，匹配不到缺省
    content: str
    is_error: NotRequired[bool]   # 默认 False；agent 回填时映射 tool_status == "error"


Message = UserMessage | AssistantMessage | ToolResultMessage
```

设计决策：

| 决策 | 理由 |
|---|---|
| 工具参数用**对象** | Anthropic `input` 就是对象；to_openai 时 `json.dumps` 序列化即可。反方向（解析响应）Anthropic 给对象、OpenAI 给字符串，统一到对象 |
| `toolResult` 独立 role | OpenAI 叫 `tool`、Anthropic 包在 `user` 里——canonical 独立，配对判断 `role == "toolResult"` 一句话 |
| 保留 `tool_name`（可选） | 配对不需要它（`tool_call_id` 就够），但调试日志 / 压缩占位符显示有实际价值；agent 回填时信息在手边（`tc.function.name`）。NotRequired：DB 无此列，回读时按 `tool_call_id` 从 assistant 的 tool_calls JSON 匹配提取，匹配不到缺省——不改表结构（2026-08-07 review 定） |
| 保留 `is_error`（默认 False） | agent.py 已有 `tool_status = "ok" \| "error"`（L245-249），回填时丢弃了错误语义。Anthropic `tool_result` 原生支持 `is_error`（工具失败模型可感知），to_anthropic 直接透传；OpenAI 侧无此字段（错误文本已在 content），转换忽略（2026-08-07 review 定） |
| `thinking` block | DeepSeek 推理模型的 `reasoning_content`，agent 现状已回传该字段；canonical 化后放 assistant.content。**`block.type` 区分答复 vs 推理**：`{"type":"thinking"}`=推理、`{"type":"text"}`=答复——三种 block 可共存（先思考→调工具→给答复） |
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

### 5.1 LLMAdapter 抽象基类（2026-08-07 review 定）

新增 `app/harness/adapters/llm_adapter.py`——**上层只关心「canonical 与 LLM 协议互转」，不感知具体格式**：

```python
class LLMAdapter(ABC):
    """协议适配器：canonical 与 LLM 协议互转，上层不感知具体格式"""
    protocol: Protocol  # Protocol.OPENAI | Protocol.ANTHROPIC

    # ── 协议相关 HTTP 元信息（streaming.py 编排 HTTP 时读取）──
    endpoint_path: str          # base_url 后的路径后缀（openai: /chat/completions；anthropic: /v1/messages）
    build_headers() -> dict     # 认证 + 版本头（adapter 内部读 settings.LLM_API_KEY）
    is_end(data_str, data) -> bool  # SSE data 行是否流结束（openai: [DONE]；anthropic: message_stop）

    # ── state 语义查询（review #53 第二轮）──
    # state 内部结构是协议相关的（openai 写 tool_acc、anthropic 写 tool_uses），
    # 但 streaming 只通过以下接口查询，不直读 state key——断流检测 / 日志协议无关。
    has_tokens(state) -> bool       # 是否产出了文本 token（断流检测）
    token_count(state) -> int       # 已产出 token 数（日志）
    has_tool_calls(state) -> bool   # 是否累积了工具调用（断流检测）

    @abstractmethod
    def canonical_to_llm(self, messages: list[Message], tools: list[dict] | None) -> dict:
        """canonical → 该协议请求体（messages + 可选 tools 转换）"""

    @abstractmethod
    def llm_to_canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
        """增量解析：喂一个 SSE 事件 dict → 本次产出事件（跨事件状态存 state）"""

    @abstractmethod
    def finalize(self, state: dict) -> list[tuple[str, object]]:
        """流结束收尾：reasoning 合并 / tool_call 输出 / done / usage"""


class OpenAIAdapter(LLMAdapter): ...      # protocol=Protocol.OPENAI，openai.py
class AnthropicAdapter(LLMAdapter): ...   # protocol=Protocol.ANTHROPIC，anthropic.py
```

- `streaming.py` 不写 if/else：`resolve_provider()` 返回协议 → 查表拿 adapter 实例 → 调统一接口（§6.2）
- HTTP 传输留在 streaming.py（重试 / mock / 错误分类都是**协议无关**的编排，不进 adapter）
- 工具 schema 内部保持 OpenAI 形状（`{type:"function", function:{name, description, parameters}}`），由各子类的 `canonical_to_llm` 内部转换

### 5.2 OpenAIAdapter — canonical → OpenAI 兼容格式（`/chat/completions`）

`canonical_to_llm`：

- `user`：`content` 字符串直传；若为 block 列表则取 text blocks 拼接
- `assistant`：
  - `thinking` blocks → 顶层 `reasoning_content` 字段（DeepSeek 扩展，多块拼接）。**硬约束（DeepSeek V4）**：模型返回 `reasoning_content` 后，后续 turn 必须原样回传，否则 HTTP 400 `"reasoning_content must be passed back"`（Hermes #15700/#17212/#17825 三案教训）——canonical 的 thinking block 必须保留在内存历史中，`to_openai` 折叠回 `reasoning_content` 是**必须路径**，不可丢弃（agent.py L262-263 现状已实现，canonical 化后保持）
  - `text` blocks → `content`（拼接字符串；无文本则为 `None`）
  - `toolCall` blocks → `tool_calls` 数组：`{"id", "type": "function", "function": {"name", "arguments": json.dumps(arguments, ensure_ascii=False)}}`
- `toolResult` → `{"role": "tool", "tool_call_id", "content"}`（`is_error` 忽略——OpenAI 无此字段，错误文本已在 content）

### 5.3 AnthropicAdapter — canonical → Anthropic 格式（`/v1/messages`）

`canonical_to_llm`：

- `system`（若在列表中）→ **提取到顶层 `system` 参数**（Anthropic 不走 messages 的 system role）
- `user`（文本）→ `{"role": "user", "content": [{"type": "text", "text"}]}`
- `assistant`：block 直译
  - `text` → `{"type": "text", "text"}`
  - `thinking` → `{"type": "thinking", "thinking"}`（若端点不支持则实测后降级为 text 或丢弃，见 §10）
  - `toolCall` → `{"type": "tool_use", "id", "name", "input": arguments}`
- `toolResult` → **包进 `user` 消息的 `tool_result` block**：`{"role": "user", "content": [{"type": "tool_result", "tool_use_id", "content", "is_error"?}]}`——`is_error` 为 True 时携带（Anthropic 原生字段）；**content 为空时用 `"(no output)"` 占位**（Anthropic 拒绝空 content）。**连续多条 toolResult 合并到同一条 user 消息**（扩展其 tool_result 列表，Anthropic 合法且更精简，参考 Hermes `convert_messages_to_anthropic`）
- tools 转换：`{"type":"function","function":{"name","description","parameters"}}` → `{"name","description","input_schema": parameters}`；**重复工具名去重**（Anthropic 拒绝重复 tool name，把硬失败转 warning，参考 Hermes `convert_tools_to_anthropic`）

**Anthropic 硬约束**（必须遵守）：

1. `tool_result` 只能出现在 `user` 消息里，且含 `tool_result` 的 user 消息**不能混入其他 block 类型** → 纯文本 user 消息与 toolResult 消息互不合并
2. `tool_result` 必须紧跟其 `tool_use`（转换保持输入顺序即天然满足，前提是内存消息顺序正确——agent 回填保证）
3. 连续 toolResult 可合并（见上）；合并后其 tool_result 列表顺序 = 输入顺序

### 5.4 响应解析（`llm_to_canonical` 增量 + `finalize` 收尾）

统一产出事件（与现状 `streaming.py` 完全一致）：`token` / `reasoning_token` / `reasoning` / `tool_call` / `usage` / `done` / `error`。

#### OpenAIAdapter.llm_to_canonical

从现状 `deepseek_chat` 的 SSE 解析逻辑**原样抽出**（`choices[0].delta`、`reasoning_content`、`content`、`tool_calls` index 合并、`usage`、`[DONE]`），不改变行为。

#### AnthropicAdapter.llm_to_canonical

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

### 5.5 SDK 决策（2026-08-07 review 定）

**不引入 openai / anthropic 官方 python SDK**，保持 httpx 手写。理由：

- 手写 SSE 恰恰是因为需要 SDK 抽象不掉的精细控制：`reasoning_content`（DeepSeek 扩展）、tool_calls 跨 chunk 增量合并、错误体分类（HTTP_STATUS_MAP）、流中断检测（SSE_DISCONNECTED）
- pi-ai 同场景也是自研传输层（只用官方 SDK 的类型定义，不用于网络请求）
- 引入两个重量级依赖增加体积与维护成本；DeepSeek anthropic 端点用官方 anthropic SDK 还需覆盖 base_url，纯增加适配层

adapter 子类已把协议细节封装，将来若真要换 SDK 只动对应子类。

## 6. 配置

### 6.1 settings 变更（`app/config.py`）

```python
LLM_PROVIDER: str = "deepseek"   # 厂商维度（deepseek | 未来 qwen/moonshot/zhipu/anthropic）
LLM_PROTOCOL: str = "openai"     # 协议维度（openai | anthropic，对应该协议的 adapter）
LLM_BASE_URL: str = ""           # 空 → 按 (provider, protocol) 默认值
LLM_MODEL: str = ""              # 空 → 按 provider 默认值（deepseek-v4-flash）
LLM_API_KEY: str = ""
```

> review #53 第二轮：provider 与 protocol 拆成两个正交维度。同一厂商可支持
> 多种协议（deepseek 同时提供 openai 兼容与 anthropic 兼容端点），protocol
> 决定用哪套协议转换；base_url 默认值按 (provider, protocol) 二元查表。

删除：`DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL`（同步改所有引用点，grep 全量清理）。

### 6.2 provider 映射表

新增 `app/harness/adapters/providers.py`：

```python
class Protocol(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"

# provider 公共配置（与协议无关）+ protocols 协议特殊配置（可扩展 dict）
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "default_model": "deepseek-v4-flash",          # LLM_MODEL 为空时使用
        "requires_reasoning_echo": True,               # quirk 跟 provider 走
        "protocols": {                                  # 协议特殊配置：可扩展 dict
            Protocol.OPENAI:    {"default_base_url": "https://api.deepseek.com"},             # 无 /v1 后缀
            Protocol.ANTHROPIC: {"default_base_url": "https://api.deepseek.com/anthropic"},
            # 未来扩展：直接往该 dict 加字段（协议版本、额外 header 等）
        },
    },
    # 未来：qwen/moonshot/zhipu → 各自 default_model / quirk + protocols
}

def resolve_provider() -> dict:
    """按 settings.LLM_PROVIDER + settings.LLM_PROTOCOL 解析完整配置
    （双维查表；返回 {provider, protocol, requires_reasoning_echo, base_url, model, protocol_config}）"""
```

### 6.3 `streaming.py` 改造

| 现状 | 改造后 |
|---|---|
| `deepseek_chat()` | `llm_chat()`：`resolve_provider()` 拿 adapter 实例 → 调统一接口（OpenAIAdapter 走现状 HTTP + SSE 逻辑；AnthropicAdapter 走 `POST {base}/v1/messages`，headers `x-api-key` + `anthropic-version: 2023-06-01`，`stream: true`） |
| `retry_deepseek_chat()` | `retry_llm_chat()`：重试包装逻辑不变，内部改调 `llm_chat` |
| `mock_chat()` | 不变（无 key 时的降级路径，事件契约同） |
| `_summarize` 的调用点（context_compact） | 改调 `retry_llm_chat` 或 `llm_chat`（摘要不需要重试包装，用 `llm_chat`） |

## 7. 改造点逐文件

| 文件 | 改动 |
|---|---|
| `app/config.py` | `DEEPSEEK_*` → `LLM_*` 四件套（§6.1） |
| `app/harness/adapters/__init__.py` | 导出入口 + adapter 实例查表（协议 → 实例） |
| `app/harness/adapters/messages.py` | canonical TypedDict（§4.1） |
| `app/harness/adapters/llm_adapter.py` | `LLMAdapter` 抽象基类（§5.1） |
| `app/harness/adapters/openai.py` | `OpenAIAdapter`：`canonical_to_llm` + `llm_to_canonical`（从 streaming.py 抽出，行为不变） |
| `app/harness/adapters/anthropic.py` | `AnthropicAdapter`：`canonical_to_llm` + `llm_to_canonical`（新写） |
| `app/harness/adapters/providers.py` | `PROVIDER_DEFAULTS` + `DEFAULT_BASE_URLS` + `resolve_provider`（§6.2） |
| `app/harness/streaming.py` | 按协议拿 adapter 实例路由（§6.3）；SSE 解析代码移入 adapters |
| `app/harness/agent.py` L257-270 | 回填 canonical：assistant 消息 content = `[{"type":"toolCall","id","name","arguments"}]`（+thinking block 若 `self._reasoning_content`）；tool 结果 → `{"role":"toolResult","tool_call_id","tool_name","content","is_error": tool_status=="error"}` |
| `app/harness/context_compact.py` | `_is_tool_call_message`：`role=="assistant"` 且 content 含 `toolCall` block；`_is_tool_result_message`：`role=="toolResult"`；占位符消息改 canonical（`{"role":"user","content":"[snipped N messages...]"}` → 保持 user + str 即兼容）；`_summarize` 改调 `llm_chat` |
| `app/harness/context.py` | `build_deepseek_messages` → `build_messages`：ORM → canonical（system 文本拼接保持；tool 消息 → `toolResult`；assistant+tool_calls → toolCall blocks；`reasoning_content` 列无（在 agent 回填内存态），DB 读出不涉及） |
| `app/services/ai_service.py` | `_to_openai_messages` → `_to_canonical_messages`（摘要/rotation 输入）；`_maybe_rotate` 的 `compress_context` 硬编码 → canonical 形状（toolCall block） |
| `app/harness/session.py` | `save_tool_call_message` / `save_tool_result_message` 入参保持 **OpenAI 形状**（DB 边界，`tool_calls` 列即 OpenAI 形状 JSON 旧数据兼容；canonical 化仅发生在内存消息流，不跨界到 DB） |

## 8. 测试计划

### 8.1 新增 `tests/test_adapters.py`

纯函数测试，无 HTTP（与现有模块级 mock 风格一致）：

- **OpenAIAdapter.canonical_to_llm**：纯文本 / 带 thinking（→ `reasoning_content`）/ 带 toolCall（arguments 对象 → JSON 字符串断言）/ 混合（text + toolCall 共存）/ toolResult → `role=tool`
- **AnthropicAdapter.canonical_to_llm**：纯文本 / thinking block / toolCall → `tool_use` / toolResult → `tool_result` 包 user 消息 / **连续 toolResult 合并到一条 user 消息** / **空 content → `"(no output)"`** / system 提取到顶层 / **`is_error=True` → 携带 `is_error`** / tools schema 转换（含重复名去重）
- **OpenAIAdapter.llm_to_canonical**：文本流 / reasoning / tool_calls 多 chunk index 合并 / usage / `[DONE]`
- **AnthropicAdapter.llm_to_canonical**：`text_delta` → token / `thinking_delta` → reasoning_token / `input_json_delta` 跨 chunk 拼装 → tool_call / `message_delta.stop_reason=tool_use` / usage 汇总

### 8.2 存量测试更新

- `tests/test_context_compact.py`：配对测试数据改 canonical（`{"role":"assistant","content":[{"type":"toolCall",...}]}` + `{"role":"toolResult",...}`）；`_summarize` monkeypatch 不变
- `tests/test_agent.py`：fake_source 事件格式不变（`tool_call` 事件仍 OpenAI 形状 dict），**agent 回填后**的 messages 断言改 canonical；`streaming.mock_chat` 替换点改 `llm_chat`（若改名）
- `tests/test_services.py` / `tests/test_api.py`：事件契约不变，应零改动（回归验证）

### 8.3 端到端手动验证（不进 CI，CI 无 key）

1. `LLM_PROVIDER=deepseek` + 真实 key：现有 OpenAI 路径回归（对话 + 工具调用）
2. `LLM_PROVIDER=deepseek` + `LLM_PROTOCOL=anthropic`：验证 anthropic SSE 解析（对话 + 工具调用 + 配对）
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
