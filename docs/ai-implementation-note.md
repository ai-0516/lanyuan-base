# AI 对话实现记录

> **创建**: 2026-07-25 | **作者**: Dev  
> **关联**: `docs/PRD.md` US-05, `docs/TECH_SPEC.md` §2.4 场景 B, `docs/design/v3-ai-chat.html`
> **状态**: v1 已上线 → 计划重构为 `harness/` 模块化架构

---

## 0. 路线图

### 当前（v1 — 单体实现）

`backend/app/services/ai_service.py`（~186 行）把会话、流式、上下文、持久化混在一个文件里。能跑，但不好理解和扩展。

### 目标（v2 — harness 模块化）

参考 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 的逐步搭建思路，把 `ai_service.py` 拆为 `backend/harness/` 目录下的独立模块：

```
backend/harness/
├── session.py      # 会话管理（创建/查找/复用）
├── context.py      # 上下文窗口（消息历史组装 + 裁剪策略）
├── loop.py         # Agent Loop（while True + stop condition）
├── tool_use.py     # 工具定义 + 分发映射表 TOOL_HANDLERS
├── streaming.py    # SSE 收发抽象
└── memory.py       # 消息持久化（数据库读写）
```

当前 `ai_service.py` 的代码分布在 4 个模块中已有实现（session、streaming、memory 的雏形、context 的硬编码 20 条），loop 层和 tool_use 层是新增的核心。

> 详细讨论记录见 `docs/ai-harness-notes.md`

---

## 1. 架构总览（v1）

```
┌──────────────┐     POST /ai/chat (SSE)      ┌──────────────────┐
│  微信小程序    │  ──────────────────────────→  │   FastAPI 后端    │
│  (ai-chat/)   │  ←── event: token / done ───  │  (StreamingRes.)  │
│              │                               │                  │
│  打字机效果    │                               │  MySQL (历史消息)  │
│  enableChunked│                               │                  │
│  onChunkRecv  │                               │  DeepSeek API    │
└──────────────┘                                │  (httpx stream)  │
                                                └──────────────────┘
```

两个 API 端点（均在 `/api/v1/ai/`）：

| 方法 | 路径 | 用途 | 响应 |
|------|------|------|------|
| POST | `/session` | 获取/创建会话 | JSON `{ session_id, title, messages[] }` |
| POST | `/chat` | 发送消息 | SSE 流 (`text/event-stream`) |

---

## 2. SSE 事件协议

后端 `StreamingResponse` 产出的每行格式：

```
event: {type}\ndata: {json_payload}\n\n
```

| 事件 | data 格式 | 触发时机 | 前端处理 |
|------|-----------|----------|----------|
| `token` | 字符串 `"暖"` | DeepSeek 返回内容 delta | 追加到最后一个 AI 气泡 |
| `status` | 字符串 `"正在查询..."` | 中间状态（工具调用） | 显示加载动画（当前未实现） |
| `done` | 空字符串 `""` | 流结束 | 恢复输入框，停止 loading |
| `error` | 字符串 `"错误描述"` | DeepSeek 返回错误 | 显示「AI 回复被中断，请重试」 |

**注意**：当前 MVP 阶段未启用工具调用，`status` 事件不会被触发。

---

## 3. 两种运行模式

### 模式 A：模拟模式（默认）

- 触发条件：`DEEPSEEK_API_KEY` 为空
- 行为：构造固定格式的模拟回复，包含用户原文
- 数据流：`save_user_msg → yield token(mock) → yield done → save_assistant_msg → commit`
- 返回事件数：2（token + done）

### 模式 B：DeepSeek 真实模式

- 触发条件：`settings.DEEPSEEK_API_KEY` 非空
- 调用 `https://api.deepseek.com/v1/chat/completions` + `stream=True`
- 拼接的 messages：
  ```
  [{role: "system", content: "你是兰园社区助手..."}, ...历史20条..., {role: "user", content: message}]
  ```
- System Prompt 内容：*"你是兰园社区助手，帮助小区业主解答供暖、停车等小区生活问题。请用温暖亲切的语气回复。"*
- 数据流：`save_user_msg → http_stream → 逐 token yield → yield done → save_assistant_msg → commit`

### 错误处理

- 后端 HTTP 非 200 → yield `error` 事件 + 保存错误回复到数据库
- 网络异常/超时（60s）→ yield `error` 事件 + 保存错误回复
- session 归属校验失败（跨用户伪造 session_id）→ yield `error`，不保存任何内容

---

## 4. 会话生命周期

```
首次进入 AI Tab                   再次进入 AI Tab
      │                                │
      ▼                                ▼
POST /ai/session              POST /ai/session
      │                                │
      ▼                                ▼
查询最近会话                   查询最近会话
      │                                │
   ┌──┤                              ┌─┤
   │ 无                             │ 有
   ▼                                ▼
创建新会话                       返回历史消息（最早 20 条）
   │                                │
   ▼                                ▼
返回 { session_id,              返回 { session_id,
  messages: [] }                   messages: [...] }
```

**关键规则**：
- 按 `user_id` + `updated_at DESC` 查最近一条会话
- 会话复用不区分 active/inactive（始终复用最近一条）
- 消息历史按 `(conversation_id, created_at ASC)` 取最早 20 条作为 DeepSeek 上下文
- 前端只需持有 `session_id`，不参与创建逻辑

---

## 5. 前端 SSE 解析逻辑

### 核心代码路径

```javascript
// 1. 发起 chunked 请求
wx.request({
  url: `${BASE_URL}/ai/chat`,
  method: 'POST',
  enableChunked: true,        // 关键：开启分块传输
  responseType: 'text',
  data: { session_id, message },
  ...
});

// 2. 接收数据块
task.onChunkReceived((res) => {
  const chunk = new TextDecoder('utf-8').decode(res.data);
  buffer += chunk;

  // 3. 按 \n 拆分，逐行处理
  const lines = buffer.split('\n');
  buffer = lines.pop() || '';  // 保留不完整行

  for (const line of lines) {
    if (line.startsWith('event: done'))  → 结束
    if (line.startsWith('event: error')) → 错误处理
    if (line.startsWith('data: '))       → JSON 解析 + 追加气泡
  }
});
```

### 掉队处理（不完整行缓存）

`buffer` 变量在函数闭包中维护。例如：
```
Chunk 1: "event: token\ndata: \"暖"
Chunk 2: "气\"\n\nevent: done\ndata: \"\"\n\n"
```
Chunk 1 后 buffer 持有 `data: "暖`（不完整行），Chunk 2 拼回完整。

### 降级编码

`TextDecoder` 不可用时，fallback 到 percent-encode + `decodeURIComponent`，保证 UTF-8 中文正确。

### 打字机效果

1. `onSend` 先在消息列表末尾插入一个空的 `{role: 'assistant', content: ''}`
2. `appendToAiBubble(text)` 直接追加到 `messages[-1].content`
3. `setData()` 触发的渲染已足够快，微信小程序不需要 requestAnimationFrame

---

## 6. 与设计稿对齐检查

| 设计稿要素 | 实现 | 备注 |
|-----------|------|------|
| AI 气泡暖白背景（#fffaf5）+ 左下 4rpx 圆角 | ✅ `border-bottom-left-radius: 8rpx` | 小程序 rpx 2x 换算 |
| 用户气泡陶土渐变（--ember → --clay）+ 右下 4rpx 圆角 | ✅ | 同上 |
| 底部 textarea + 圆形箭头按钮 | ✅ | |
| Enter 发送 / Shift+Enter 换行 | ✅ | `bindconfirm` + `bindlinechange` |
| 发送后清空+重置高度 | ✅ | `inputValue: ''` + auto-height |
| 消息 fadeIn 动画 | ✅ | `animation: fadeIn 0.3s ease` |
| 新消息自动滚底 | ✅ | `scroll-into-view="{{lastMsgId}}"` |
| 输入框禁用（loading 期间） | ✅ | `disabled="{{isLoading}}"` |
| 无欢迎页/无历史列表 | ✅ | 进入即显示对话界面 |
| 无快捷提问 | ✅ | 只有输入框 |

---

## 7. 测试策略

### 测试分层

| 层级 | 文件 | 覆盖内容 |
|------|------|----------|
| API 测试 | `tests/test_api.py` | `test_ai_session`, `test_ai_chat` — HTTP 接口完整通路 |
| E2E 测试 | `tests/test_e2e.py::TestAI` | session 创建、复用、归属校验、mock chat |
| Service 测试 | `tests/test_services.py::TestAISession` | get_or_create_session 完整逻辑 |
| Service 测试 | `tests/test_services.py::TestAIStreamChat` | stream_chat 事件序列、数据持久化、会话时间刷新 |

### 已覆盖的分支

- ✅ 首次创建会话
- ✅ 复用最近会话
- ✅ 返回最早 20 条消息
- ✅ 用户会话隔离
- ✅ 无效 session_id（跨用户访问）
- ✅ 模拟回复（无 API Key）
- ✅ 用户消息入库
- ✅ AI 回复入库
- ✅ 会话 updated_at 刷新

### 未覆盖（后续补充）

- ❌ DeepSeek API 真实调用（需 API Key + mock httpx）
- ❌ 工具调用（tool_call）逻辑
- ❌ SSE 数据跨 chunk 边界测试
- ❌ 网络超时/重试

---

## 8. 已知限制与后续优化

### 当前限制

1. **消息上下文**：最早 20 条，长对话会丢失最早消息。TECH_SPEC 规划后续用 Redis List 做滑动窗口
2. **无工具调用**：System Prompt 未定义任何 tool。业务方（地暖缴费等）可自行添加 tool definitions
3. **单 worker**：Uvicorn 1 worker，进程内 TTLCache 无竞争问题
4. **无会话管理 UI**：PRD 明确 Won't Have，会话由后端自动管理
5. **前端无 status 事件处理**：目前后端未 emit status，工具调用场景需补全

### 配置项

`.env` 或环境变量：

```env
DEEPSEEK_API_KEY=sk-xxxxx
DEEPSEEK_MODEL=deepseek-chat           # 或 deepseek-reasoner
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

### 后续优化方向（已定稿）

按 `docs/ai-harness-notes.md` 的讨论节奏，v2 的重构顺序：

1. **抽取模块边界** — 从 `ai_service.py` 中拆出 `session.py`、`context.py`、`streaming.py`、`memory.py`
2. **Agent Loop** — 实现 `loop.py`，明确 `while True` 的退出条件
3. **Tool Use** — 第一个工具定义 + `TOOL_HANDLERS` 分发映射
4. **Tool Result** — 工具结果回填 LLM
5. **Permission** — 敏感操作的确认防护
6. 后续依次 ...
