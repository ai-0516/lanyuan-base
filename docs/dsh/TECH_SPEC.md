# 兰园公共底座 v2 — 技术方案（TECH_SPEC）

> Issue: #86 · 基于 #82 spike 结论（docs/dsh/spike.md，已 merge）
> 范围：**agent 子系统 DSH 重写**。Python 后端（认证/业务 API/MySQL）保持，业务数据模型见 v1 docs/TECH_SPEC.md（本文不重复）
> 状态：待用户审阅 → dev-lead review

## 目录

- [1. 概述与范围](#1-概述与范围)
- [2. 技术选型（v2 增量）](#2-技术选型v2-增量)
- [3. 系统架构](#3-系统架构)
- [4. 事件层（薄）：过滤起步，扩展点预留](#4-事件层薄过滤起步扩展点预留)
- [5. 会话策略（MySQL + get-or-load-or-create）](#5-会话策略mysql-persistencebackend--get-or-load-or-create)
- [6. MCP 工具桥](#6-mcp-工具桥)
- [7. dsh/ 家目录](#7-dsh-家目录)
- [8. 数据模型（v2 增量）](#8-数据模型v2-增量)
- [9. API 设计（v2 变更）](#9-api-设计v2-变更)
- [10. 前端 v2（小程序）](#10-前端-v2小程序)
- [11. 部署（微信云托管）](#11-部署微信云托管)
- [12. 非功能需求](#12-非功能需求)
- [13. 实施计划（里程碑）](#13-实施计划里程碑)
- [14. 待确认项](#14-待确认项)

## 1. 概述与范围

### 1.1 目标

用 DSH（DeepSeek Harness）替换 v1 自研 harness（backend/app/harness/：agent loop、streaming、context、adapters），agent 的循环、会话日志、上下文组装、压缩、工具调度全部在 DSH 进程内完成。Python 侧只留「壳」：认证、业务 API、工具执行桥、SSE 透传。

### 1.2 形态（spike 实验 1a/1c/1d/1e 验证）

**形态二**：Python FastAPI 保持 + npm 完整版 DSH runtime 子进程（stdio JSON-RPC）。不对前端暴露任何 endpoint。

```
微信小程序 → FastAPI（唯一后端，不变）
              ├─ /api/v2/ai/chat/ws（WebSocket，2026-09-04 路线2：SSE → WS 统一）:
              │      认证（首帧 token）→ session 归属 → Python SDK → DSH runtime（Node 子进程）
              │      → on_notification 事件层过滤 → WS 逐帧 JSON（§4，事件格式原样）
              ├─ 业务工具桥: 每 worker 一个 Python MCP server 进程（@tool schema 复用）
              └─ MySQL（业务数据 + 会话日志）
```

### 1.3 与 v1 的关系

- v1/v2 双版本并行期：v2 后端独立部署，`/ai/chat` 逐步切流
- 前端 v2 小程序消费 **DSH 事件集（后端白名单过滤后）**——事件格式保留 DSH 原样，后端只发前端关心的子集（§4）
- 排除决策（用户已确认，spike.md 落档）：
  - **fork 分支语义**（git 心智不 human，lanyuan 无此场景）
  - **session-projection**（单前端直连事件流，前端本地维护状态即可；投影是整值语义表达不了流式序列）
  - **npm CLI 用法**（v2 后端 SDK 驱动，无 CLI 场景）
  - **不做 v1 瘦身翻译**（v1 token/done 契约退役）；事件层**薄实现**：初期只做过滤，翻译能力留扩展点按需加入（§4，2026-08-23 用户定）

## 2. 技术选型（v2 增量）

| 组件 | 选型 | 依据 |
|---|---|---|
| DSH runtime | **零 examples + 能力裁剪**：官方正式包 7 个（dsh / dsh-app-boot / dsh-sdk-jsonrpc-server / dsh-llm-deepseek / dsh-session-persistence-jsonl / dsh-session-checkpoint-policy / dsh-mcp-client，全 0.1.1-rc.2）+ 本地自写（spine 骨架、bin 入口，§7.4）；**不装 bash/subprocess/fs-local**（无 shell/文件场景，§7.1b） | spike 1d 验证 rc.2 批次；用户 2026-08-23 定：零 examples + 能力裁剪 |
| Python SDK | `deepseek-harness-sdk`（`DeepSeekHarness` 高级 API） | spike 1a/1d：SDK 拉起 runtime 三链路 |
| 包管理 | **pnpm** | spike 1d：npm install 服务器稳定崩溃（内存）；pnpm 成功 |
| 工具桥 | `fastmcp`（Python MCP server）+ `@deepseek-ai/dsh-mcp-client` | spike 3/3b：agent 真实调用业务工具 |
| 会话持久化 | JSONL（短期）→ MySQL PersistenceBackend（中期，自研插件） | spike 2h + MySQL 落地清单 |
| 搜索投影 | SQLite FTS5（session-query-sqlite 官方插件） | 查询层组合定稿 |
| LLM | deepseek-v4-flash（可配置） | v1 沿用 |

**不使用**：session-projection、fork、npm CLI、自建 sdk-runtime（npm 形态已就绪，无需 build exe——spike 1d 结论）。

## 3. 系统架构

### 3.1 进程模型

```
FastAPI (uvicorn, --workers 1 起步)
  └─ DSH 包装层（backend/app/ai/dsh_runtime.py）
       ├─ DeepSeekHarness 实例（每 worker 常驻一个）
       │    └─ DSH runtime 子进程（Node，stdio JSON-RPC）
       ├─ 生命周期: lifespan startup 启动 / shutdown 关闭
       ├─ 崩溃重启: catch TransportClosedError → close() + start()（4b 实验：直接 start() 无效）
       └─ 环境变量: 显式管理 DSH_HOME / DSH_CWD，不继承 shell 残留（2g 实验；M3 起 jsonl 条目已删，DSH_SESSION_ROOT 不再注入）
  └─ MCP server 挂载（§6.1：tools/mcp_server/main.py 的 mcp_app，/mcp 端点，streamable-http）
```

- 进程内多 session 并发：Node 异步事件循环，通知按 sessionId 过滤（实验 4a：3 session 并发互不干扰）
- 多 worker 需 session 亲和（`--workers 1` 起步规避；v1 本就每 worker 一份内存缓存）

### 3.2 目录结构

```
lanyuan-base/
├── backend/
│   └── app/
│       ├── ai/                    # v2 DSH 集成层（替换 harness/）
│       │   ├── dsh_runtime.py     # 包装层：生命周期/重启/事件订阅
│       │   ├── event_layer.py     # 事件层（薄）：初期白名单过滤，扩展点预留（§4）
│       │   └── services/ai_service.py  # v2 会话统一创建点 get_or_create_session_v2（§5.3/§9.1，PR #97 review 合并）
│       └── tools/mcp_server/      # MCP 基础设施（@mcp_tool 装饰器 + http_app 挂载，§6.1/§6.4b；工具定义在 app/api/v1/ 业务 endpoint 上，@tool 旁叠加）
├── miniprogram/                   # 前端 v2（消费 DSH 事件集）
└── dsh/                           # DSH 运行时一体化家目录（删除即卸载）
    ├── package.json               # 正式包依赖 + file: 本地插件 + bin 入口
    ├── cordis-lanyuan.yml         # 运行时配置（见 §7）
    ├── bin/dsh-jsonrpc-agent.js   # 自写 runtime 入口（替代官方 demo bin，§7.4）
    ├── node_modules/              # pnpm install 产物（部署时生成）
    ├── spine/                     # 自写 agent 骨架插件 @lanyuan/dsh-agent-spine（§7.4）
    └── mysql-persistence/         # 中期：MySQL PersistenceBackend 插件（TS → dist/）
```

### 3.3 请求数据流（WebSocket /api/v2/ai/chat/ws）

2026-09-04 路线2：SSE（wx.request enableChunked）在微信云托管不可用
（callContainer 不支持流式）→ v2 对话流式**统一 WebSocket 单一通道**，
事件协议不变（§4 白名单 {type, data} 原样），仅传输从 SSE 帧换 WS 逐帧 JSON。

```
1. 前端 wx.connectSocket（wss://云托管域名 或 开发 ws://localhost）→ 首帧
   {token, session_id, message}（JWT 放帧内，不进 URL/header）
2. FastAPI 校验 token（4401）→ session owner 归属（4403，M3 起复用/恢复）
3. harness.run(prompt, on_notification=...)
4. on_notification 实时到达 → event_layer 过滤 → WS 帧 {type, data} → 前端（§4）
5. turn/end / idle 兜底 → 服务端关闭连接（1000）；失败先推 error 帧再关闭
6. 回复即 DSH session 日志（MySQL events 表，M3 起）；前端历史列表从日志派生（§10）
7. 工具调用 → DSH 内部调 MCP server（§6）→ 结果回 agent → 继续/结束
```

## 4. 事件层（薄）：过滤起步，扩展点预留

### 4.1 定位（2026-08-23 用户定）

**做一个薄薄的事件翻译层**（`backend/app/ai/event_layer.py`），初期只做**白名单过滤**——只把前端关心的 event 发过去；后期**如果有必要**再加入其他功能（事件改写/翻译）。不预判具体需求，层内留扩展点。

原则「向 DSH 靠近，不让 DSH 向我们靠近」不变：**默认保持 DSH 事件原样**（type + data 不改写）；任何改写/翻译必须由明确消费方需求驱动才加入。

### 4.2 事件白名单（初期唯一职责：过滤）

`session.event` 通知 payload：`{type, data, ...}`。**✅ = 白名单（发前端）**，**❌ = 后端消费/丢弃**：

| type | data 关键字段 | 前端用途 | 白名单 |
|---|---|---|---|
| `assistant/chunk` | `chunk.type`=text-delta / reasoning-delta / block-start / block-end / usage / finish；`chunk.text`；`turn`、`step` | 正文流 | ✅ **仅 text-delta** |
| `step/start` | `turn`、`step` | **气泡边界**——承接 v1 `message:start` 粒度（step = 一次 LLM 调用，`agent.ts:279`；v1 的 turn 概念） | ✅ |
| `user/message` | content | **用户气泡数据源**（agent.ts:283：prompt → user/message append → 事件发出；前端以事件流为单一数据源渲染用户消息） | ✅ |
| `turn/start` | `turn` | 回合边界（一次 user_prompt 处理开始，对应 v1 `loop:start`）——前端重置回合状态 | ✅ |
| `turn/end` | `reason.kind`=completed / max-tokens / error | 回合收尾；**done 判定** | ✅ |
| `tool/call` | `name`（mcp__lanyuan__*）、`arguments` | 工具过程展示（**前端不关心**——工具使用对用户透明，2026-08-23 用户定） | ❌ |
| `tool/result` | `message` | 同上 | ❌ |
| `session/title` | title | 会话标题（前端暂不展示） | ❌（历史列表需要时再开） |
| `request/header` | — | 诊断 | ❌ |
| `agent/inbox/spliced` | — | 注入确认（内部） | ❌ |
| `step/end` | `turn`、`step` | 气泡清理可用「下个 step/start 删空气泡」替代，无需转发 | ❌ |
| `assistant/chunk` 子类型 reasoning-delta / block-start / block-end / usage / finish | — | thinking 暂不展示；用量无展示需求 | ❌ |
| 通知 `session.status` | status=idle | 后端 done 判定用（不转发） | ❌ |

白名单可扩展：未来需要 thinking（reasoning-delta）/ 用量（usage）/ 工具过程（tool/call、tool/result）/ 标题时，加回即可（格式零改动）。

### 4.3 事件层职责（event_layer.py）

| 职责 | 说明 |
|---|---|
| 事件过滤 | 白名单（§4.2）：非白名单事件只后端消费，不产出给前端（初期唯一职责） |
| 帧化（传输层） | 2026-09-04 起 v2 chat 统一 WebSocket：WS 逐帧 JSON `{type, data}`（白名单事件原样透传）；SSE 帧函数 format_sse 随 SSE 通道退役删除 |
| 认证 | 请求鉴权通过才建立流（WS：首帧 token 校验） |
| user_id 绑定 | 会话与用户绑定（§6 注入用） |
| done 判定 | `turn/end`（reason.kind）或 `session.status=idle` 兜底 → 关流 |
| 错误处理 | runtime 崩溃（TransportClosedError）→ error 帧（WS `{type:"error"}`）+ 日志；**不暴露内部错误详情**（v1 规则沿用：只写「请重试」，traceback 记 error.log） |
| 断连 | 前端断开 → 取消 run（cancel 语义） |
| 扩展点（预留） | 翻译/改写按需加入：管道式结构（filter → [改写] → frame），初期改写环节为空 |

### 4.4 明确不做（初期）

- ❌ v1 瘦身翻译（text-delta→token 契约退役；白名单事件结构原样）
- ❌ 事件字段删减/改写（初期只按事件/子类型粒度过滤；改写属于扩展点，有需求再启用）
- ❌ reasoning-delta 转发（thinking 暂不展示，前端无消费方——白名单外过滤，未来需要再加回）

## 5. 会话策略：MySQL PersistenceBackend + get-or-load-or-create（2026-08-23 用户定）

**v2 会话目标**：MySQL 持久化 + get-or-load-or-create 恢复，配套使用（持久化提供 load 数据源，恢复提供 load 逻辑）。过渡期（M1/M2）每请求新 session（**无历史注入**），M3 落地 get-or-load-or-create 后恢复会话。

### 5.1 过渡期（M1/M2）：每请求新 session（无注入）

- **不做历史注入**（2026-08-23 用户定）：前期 server 无 resume 能力 → 每次请求直接当**新 session** 处理（uuid），agent 只带本次请求上下文
- 历史注入方案（实验 2h 验证过可行）**不采用**——会话恢复的正确路径是 M3 的 get-or-load-or-create（DSH 持久化 + 服务端恢复），而非 FastAPI 侧组装历史 content blocks；前期简化为「新 session 直连」等待 M3
- M1/M2 前端历史列表暂不保证（M3 起从 DSH 日志派生，§10.4）

### 5.2 MySQL PersistenceBackend 插件（M3，v2 会话组成部分）

两层架构（PersistenceCoordinator 编排层全复用 + 物理层 8 hook 只写 MySQL store）：
- 代码位置：`dsh/mysql-persistence/`（TS → dist/，file: 本地依赖零 publish）
- 表结构：sessions / events / persistence_state（见 §8）
- 8 hook 实现要点（照官方 SqliteStore 翻译，~300-400 行）：
  - `appendBatch`：InnoDB 事务 + `SELECT ... FOR UPDATE` 锁行读 tail → 校验 `first.seq === next seq` → 批量 INSERT → revision+1
  - `loadStoredFrom?`：实现（`WHERE seq >= ?`）
  - TornMarker 用 number（MySQL 事务原子提交，torn 罕见）
  - 不做 chunk 打包 codec（行数非瓶颈）
- 两个前提：cordis.yml 已移除默认 jsonl persistence（PR #97 snxly review：直接删插件条目而非 `disabled: true`）；运行时不重建（npm install 是部署步骤）

### 5.3 get-or-load-or-create（M3，v2 会话组成部分）

- 官方 rc.5 缺口确认：`handleRequest` 仅 initialize/session/prompt/shutdown；`getOrCreateSession` 只查内存（"发现框架空白"叙事）
- **统一创建点（PR #97 review 定案）**：session 的创建（id 生成 + owner 映射）由
  FastAPI 侧 `ai_service.get_or_create_session_v2` 统一负责（前端先调
  `POST /api/v2/ai/session` 拿 session_id，再带 id 发对话请求，§9.1）；
  DSH 侧只负责把已创建的 session「物化」为 live agent——不再承担创建职责
- 设计（已定稿）：不在协议加新方法，在 `session/prompt` 内部扩展：
  ```
  prompt(id) → 内存有？用内存的
             : 持久化有？resume 成 live session（核心 log-seed 重放；
               「前端创建未对话」的空 session 也能 resume——loadStored 返回
               header + 空 events，空 agent 首次 followup 正常，实测验证）
             : 都没有？create（极端兜底：session 不在 DB 的异常路径）
  ```
- Python SDK 零改动（服务端策略）；collision 守卫保留为 load 失败兜底
- 三个边界：并发写 owner 机制 + workers=1/session 亲和；无条件 load（id 即身份）；load 语义界定写文档
- 实现 = 本地 `@lanyuan/dsh-sdk-jsonrpc-server` 插件替换官方 sdk-jsonrpc-server（同 mysql-persistence 套路）
- 恢复后「每请求新 session（无注入）」退役：正常对话复用 session（省 token、日志即历史），重启后首请求自动恢复

### 5.4 环境变量管理（2g 实验教训）

`DSH_SESSION_ROOT` 残留曾会静默改变 jsonl 落盘位置并间接导致 id collision 误判。M3 起 jsonl persistence 条目已移除（snxly review），`DSH_SESSION_ROOT` 无消费方，backend 不再注入（dsh_runtime 只管理 DSH_HOME / DSH_CWD / LANYUAN_*）。backend 启动时显式设置/清除全部 DSH 环境变量，不继承 shell 残留。

## 6. MCP 工具桥

### 6.1 架构（实验 3/3b 验证 + M2 定案；M2 review 传输层定案：stdio → streamable-http 挂载）

```
FastAPI 进程（uvicorn worker）
  ├─ /api/v2/ai/chat/ws: DSH runtime（Node 子进程）→ 桥插件（HTTP client）
  └─ /mcp: MCP server 挂载（fastmcp streamable-http，§6.2）
       └─ 业务工具（@mcp_tool 原生注册，§6.4b，连 MySQL）
```

- 工具注册名：`mcp__<serverName>__<rawName>`（如 `mcp__lanyuan__get_my_profile`）
- transport：**streamable-http 挂载 FastAPI /mcp**（M2 review 定：MCP server 能力独立于 DSH runtime——DSH 只是 HTTP client，工具/API 同进程、同认证/事务体系；`http_app(path="/")` 避免与 mount 前缀叠加 404）
- 桥插件连接：`StreamableHTTPClientTransport`（MCP SDK 正式库）消费 `LANYUAN_MCP_URL`；启动窗口内**有界重试**（FastAPI lifespan 预热 DSH runtime 时尚未 listen，重试等 /mcp 就绪；超过窗口抛错 = 装配失败不静默缺工具）
- **认证（PR #94 review 修复）**：`http_app(middleware=[McpAuthMiddleware])`——内部共享密钥 header（`X-Lanyuan-Internal-Token`，env `LANYUAN_MCP_TOKEN`），所有请求（含 GET 初始化）在 streamable-http 路由前校验，未认证 401（tools/list 也在门内）。密钥由 FastAPI 侧 `_runtime_env()` 注入 DSH 子进程 env（显式 env 优先，未配置进程内自动生成同值）；桥插件缺失密钥 → 拒绝连接（fail-closed）。**外部 client 无法直连 /mcp**（§6.3 信任前提收紧）
- 崩溃恢复：MCP server 随 FastAPI 生命周期（同进程）；DSH runtime 崩溃重启 → cordis 重新装配 → 桥重连 /mcp
- 启动时序：lifespan 合并 `mcp_app.lifespan`（fastmcp session manager 依赖）；DSH 预热改后台任务（避免「预热依赖 listen、listen 依赖 lifespan 完成」死锁；首请求前未完成则懒启动兜底，DshRuntime 加锁跨线程安全）

### 6.2 配置模板（v2 cordis-lanyuan.yml 的 bridge 部分）

```yaml
- id: lanyuan-mcp-client
  name: '@lanyuan/dsh-mcp-client'
  config:
    serverName: lanyuan
    url: !!js process.env.LANYUAN_MCP_URL ?? 'http://127.0.0.1:8000/mcp/'
    toolCallTimeoutMs: 60000
```

URL 由 FastAPI 侧 `_runtime_env()` 注入（`LANYUAN_MCP_URL`，与 FastAPI 部署端口绑定；生产云托管环境变量覆盖）。

**认证（PR #94 review 修复）**：请求带 `X-Lanyuan-Internal-Token`，密钥来自 env `LANYUAN_MCP_TOKEN`
（FastAPI 侧 `_runtime_env()` 注入 DSH 子进程 env——显式 env 优先，未配置进程内自动生成同值）；
桥插件缺失密钥 → 拒绝连接（fail-closed）。生产部署时可在环境变量显式固定（FastAPI 与 DSH 共享）。

### 6.3 user_id 注入（安全设计，防 LLM 伪造越权）——M2 定案

**原则（已定）**：身份由桥层强制绑定，LLM 永不提供/自填身份。

**信任前提（PR #94 review 修复）**：`_meta.user_id` 只在**已通过传输层内部认证**的
client 上采信——`/mcp` 挂内部共享密钥中间件（§6.1），唯一持有密钥的是本进程 DSH
子进程（桥），外部 client 直连 /mcp 在传输层即被 401 拒绝（此前无认证时任何网络
可达者都可伪造 `_meta.user_id` 冒充任意用户，review 实测越权）。桥层注入 user_id
的取值来自 session id（非模型输入），传输层认证保证「能到达工具的 client 只有桥」，
两层共同构成身份边界。

- 请求级：FastAPI 认证后，session_id 编码 user_id——`v2-{user_id}-{uuid4()}`（§5.1 过渡期每请求新 session）
- 桥层：自写 MCP client 插件 `@lanyuan/dsh-mcp-client`（官方 dsh-mcp-client 的自写重写）的 executor 从 `exec.agent.session.id` 解析 user_id，注入 callTool 请求的 `_meta.user_id`（MCP 协议 `RequestParams._meta`，官方 mcp-client 不携带故自写）
- 工具级：MCP server 端工具签名**不含** user_id 参数（LLM 不可见，tools/list 确认）；执行时从 `ctx.request_context.meta.user_id` 提取（fastmcp 的 Meta 是 pydantic 模型，extra=allow）
- 校验：`_meta` 缺失/无 user_id 字段 → `PermissionError` 拒绝执行（工具失败，LLM 可见错误不可见身份）；任何工具实现不得信任模型输入中的身份字段
- **候选机制定案（§14 待确认项 2）**：`_meta` 透传 + 自写桥插件注入（官方 mcp-client callTool 无 `_meta` 扩展点 → 自写插件用 MCP SDK 正式库实现，注入点自由）

**M3 演进（2026-08-31 用户定：身份查询插件）**：session_id 编码 user_id 是 **M2 过渡方案**——SDK 通道限制（`harness.run` 只接受 session_id，DSH 无请求级 middleware、sdk-jsonrpc-server 不透传自定义字段）下的唯一零侵入通道，**非终态设计**。M3 随会话持久化改为**身份查询插件**：

```
FastAPI（身份权威，JWT 验证处）：
  session_id = {纯 uuid}（不再编码 user_id）
  → 记录映射 {session_id → owner_user_id}（并入 M3 sessions 表 owner 字段，§8.2）
  → 内部身份端点 GET /api/v2/internal/sessions/{id}/owner（internal token 防护；
     PR #97 review：v2 端点统一挂 /api/v2，mcp-client 结果按 session 缓存）
DSH 侧（扩展 @lanyuan/dsh-mcp-client）：
  execute 时：session_id = exec.agent.session.id（纯 uuid）
  → HTTP 查询内部身份端点 → user_id → 注入 _meta.user_id（§6.3 工具级不变）
  → 查不到 → 拒绝（fail-closed）
```

- 收益：session_id 恢复纯会话标识（不再有字符串格式约定）；身份权威收归 FastAPI；M3 会话复用天然满足"同会话多轮身份一致"
- **入口归属校验（PR #97 dev-lead review）**：`WS /api/v2/ai/chat/ws`（2026-09-04 SSE → WS）在进入 DSH
  前校验 `sessions.owner_user_id == 调用者 JWT user_id`（§9.1）——session_id 本身
  不构成身份凭证，授权以 JWT 为准；越权/无映射 → 403（fail-closed）。与本节
  桥层注入形成闭环：**入口（FastAPI 校验调用者身份）+ 执行（桥按 session 查
  owner 注入工具身份）**两层都以 DB owner 映射为真源，任何一层都不信任模型输入
- 与 §5.2/§8.2（M3 会话持久化）绑定：映射表即 sessions 表 owner 字段，桥插件查询逻辑 M2→M3 零重做

### 6.4 首批工具清单（v1 既有 @tool 迁移）

| 工具 | 说明 |
|---|---|
| **19 个业务工具**（profile 3：get_my_profile/update_my_profile/get_user_public；posts 6；comments 3；notifications 3；memory 4） | 全部 `@mcp_tool` 注册（@tool 旁叠加，§6.4b）；endpoint 路径不变（/api/v1/*，v1/v2 仅限 /ai/chat） |
| ~~`search_history`~~ | **不迁移**——v2 历史搜索由 DSH session-query 能力覆盖（§6.4b） |

### 6.4b 工具注册（M2 review 定案：@mcp_tool 写在业务 endpoint 上，@tool 旁叠加）

**v1/v2 仅限 /ai/chat（2026-08-30 用户定）**——业务 endpoint（用户/帖子/评论/通知/
记忆）不属于 v1/v2 之争，文件永存、路径不变；`@mcp_tool` **直接写在业务 endpoint
的 @tool 旁边**（同一函数双注册，装饰器均返回原函数，FastAPI 无感）：

```python
@router.get("/user/me")
@mcp_tool(result_formatter=_format_get_my_profile)   # 注册进 MCP server（v2 agent 用）
@tool(result_formatter=_format_get_my_profile)       # 注册进 v1 ToolRegistry（v1 agent 用）
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """业务描述进 MCP schema；db/user_id 是注入参数（LLM 不可见）"""
    ...
    return api_success(user)  # model 原样（HTTP 消费，同 v1）
```

- **双注册语义**：@tool 与 @mcp_tool 签名一致（name + result_formatter）、各自注册到
  自己的体系——短期两者共存（v1 agent 用 @tool、v2 agent 用 @mcp_tool）；后期删 @tool
  机制（装饰器行 + tool_registry）即完成切换，业务文件不动、@mcp_tool 保留
- 注入识别与 v1 @tool 一致：`Depends(get_db)` → db 会话注入，`Depends(get_current_user)`
  → user_id 注入（LLM 不可见）。HTTP 模式 FastAPI Depends 正常解析；MCP 模式
  user_id 来自 `_meta`（§6.3），db 由装饰器注入 async_session_factory 会话
  （请求级 commit/rollback，对齐 get_db 契约）
- **MCP 模式执行链**（同 v1 ToolDef.execute）：解包 api_success → `_to_dict`
  （model→dict，跳过 created_at/updated_at）→ result_formatter 输出（JSON 字符串，
  LLM 读 formatter 投影）；无 formatter 时直接返回解包后的 data（结构化 dict）
- **Pydantic model 参数展平**（同 v1 _flatten_model）：create_post 的 content/images
  等展平为独立字段进 schema，执行时按字段重建 model 实例
- 隐私保护承担者与 v1 一致：**formatter 删减**（`result_formatter` 参数）
- **search_history 不迁移**——v2 历史搜索由 DSH session-query 能力覆盖；
  其余 19 个业务工具全部 @mcp_tool 注册（M2 review 用户定：工具面全量铺开）
- MCP server（tools/mcp_server/main.py）不 import 业务模块——注册由 app.main
  import 业务文件触发（装饰器执行即注册，无注册表遍历/无 v1 依赖）

## 7. dsh/ 家目录

### 7.1 package.json（零 examples 依赖，0.1.1-rc.2）

```jsonc
{
  "dependencies": {
    // ① 聚合包（CLI + cordis 运行时 + 插件生态；注意：不含 core 包，见下）
    "@deepseek-ai/dsh": "0.1.1-rc.2",
    // ② core 包（dsh 聚合 62 个里没有它们——spine 的宿主，必须显式声明）：
    "@deepseek-ai/cordis": "4.0.1",
    "@deepseek-ai/dsh-agent": "0.1.1-rc.2",
    "@deepseek-ai/dsh-agent-loop": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session": "0.1.1-rc.2",
    "@deepseek-ai/dsh-scope": "0.1.1-rc.2",
    "@deepseek-ai/dsh-invariants": "0.1.1-rc.2",
    "@deepseek-ai/dsh-tools": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session-title": "0.1.1-rc.2",
    "@deepseek-ai/dsh-system-prompt": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm-retry": "0.1.1-rc.2",
    "@deepseek-ai/dsh-home-paths": "0.1.1-rc.2",
    // ③ 激活插件（cordis.yml 引用；pnpm 严格模式要求显式声明，即使 dsh 已聚合）：
    "@deepseek-ai/dsh-app-boot": "0.1.1-rc.2",           // boot 核心（自写 runtime bin 用，§7.4）
    "@deepseek-ai/dsh-sdk-jsonrpc-server": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm-deepseek": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session-persistence-jsonl": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session-checkpoint-policy": "0.1.1-rc.2",
    "@deepseek-ai/dsh-mcp-client": "0.1.1-rc.2",
    // 裁剪说明：subprocess-local / bash-local / fs-local 不激活（§7.1b）；core 包按需裁剪（goal/skill 等 spine 不 import 的不用声明）
    // ④ 本地插件（file:，零 publish）：
    "@lanyuan/dsh-agent-spine": "file:./spine",
    "@lanyuan/dsh-session-persistence-mysql": "file:./mysql-persistence",  // 中期
    "@lanyuan/dsh-sdk-jsonrpc-server": "file:./server"                // 中期
  },
  "devDependencies": { "typescript": "^5.x" },  // 编译本地插件（spine/mysql-persistence）
  "bin": { "dsh-jsonrpc-agent": "bin/dsh-jsonrpc-agent.js" }
}
```

> **依赖类型应用（2026-08-23 澄清）**：dependencies = 运行时必需（聚合包 + core 包 + 激活插件 + 本地插件，装齐）；peerDependencies = 插件对宿主的声明（自写 spine 的 package.json 用它声明 @deepseek-ai/dsh-agent 等 core 包，宿主 = 本根 package.json 显式声明——避免重复安装/版本统一）；devDependencies = 仅构建期（typescript 编译本地插件，生产镜像不装）。⚠️ **dsh 聚合（62 依赖）不含 core 包**（dsh-agent/agent-loop/llm/session 等均不在内，已逐一核对）——core 包必须显式声明（官方 sdk-runtime 118 依赖同样如此）。

> **版本说明**：0.1.1-rc.2 在 npm 的 `next` 标签（`latest` 停旧版 0.0.1-rc.x）——安装时显式锁定 `0.1.1-rc.2`，不能裸装（会拿到 latest 旧版）。

> **dsh 聚合包与激活清单（2026-08-23 查证）**：`@deepseek-ai/dsh` 是**超级聚合包**（62 直接依赖：CLI + cordis 运行时 + 50+ 个 dsh-* 插件，含我们不用的 bash/fs/pwsh/subagent/workflow 等）——安装后 node_modules 为全量（~341M，磁盘不减）。「裁剪」的真实语义是 **cordis.yml 激活清单裁剪**（运行时只装配激活的插件，其余零加载）。pnpm 严格模式因此要求：① cordis.yml 引用的每个插件必须显式声明（虽然 dsh 已聚合，spike 1d 教训）；② 代码 import 的包（如自写 bin import dsh-app-boot）也要显式声明。`dsh-app-boot` 不是聚合包（1 依赖 js-yaml，1216 行 boot 逻辑；peer 9 个 cordis 生态由 dsh 提供）。

### 7.1b 依赖清单：每个包干什么、为什么依赖

**官方正式包（7，激活）**——按运行时角色分组：

| 包 | 组 | 干什么 | 为什么依赖（缺了会怎样） | v2 备注 |
|---|---|---|---|---|
| `@deepseek-ai/dsh` | 启动·核心 | runtime 核心聚合：cordis 装配、dsh CLI、plugin 管理、agent loop / session / surface 等一切核心机制 | 整个 runtime 的地基；所有插件 peer-depend 它 | cordis.yml 无直接条目，但 pnpm 严格模式要求显式声明 |
| `@deepseek-ai/dsh-app-boot` | 启动·核心 | app bin 的共享 boot 胶水：.env 加载、fail-loud 守卫、config 解析、Loader boot 序列 | 自写 runtime bin 的底层依赖（`boot()` 是唯一干活函数） | 替代官方 demo bin 后引入（§7.4） |
| `@deepseek-ai/dsh-sdk-jsonrpc-server` | 协议层 | stdio JSON-RPC 服务端插件：initialize / session/prompt / shutdown | 没有它 SDK 无法与 runtime 通信，对话链路断 | **中期被本地 @lanyuan/dsh-sdk-jsonrpc-server 替换（get-or-load-or-create）** |
| `@deepseek-ai/dsh-llm-deepseek` | 骨架·模型 | DeepSeek chat-completions 适配器（LLM seam 的实现） | 没有它 agent 没有模型通道，无法生成回复 | 模型 = deepseek-v4-flash |
| `@deepseek-ai/dsh-session-persistence-jsonl` | 会话·持久化 | JSONL 会话日志落盘 backend（崩溃恢复/审计/回放） | 没有它会话不落盘，进程内多轮无日志 | **中期换自写 mysql 插件（disabled 默认 jsonl）** |
| `@deepseek-ai/dsh-session-checkpoint-policy` | 会话·持久化 | 语义化持久化时机：`llm/stream` 前 / 顶层 `tools/execute` 前 / `agent/pre-step` 前强制 `sessions.flush()`（源码 83 行，三个边界监听） | 去掉后靠 write-behind 自动兜底（200ms deadline）——崩溃最多丢 200ms 缓冲；**但工具副作用可能先执行后落盘**（副作用无日志 → 恢复不一致/审计缺口）；且失去 fail-closed 保护。对「日志即历史/审计」的 v2 会话模型**必须保留** | 与 persistence 配套 |
| `@deepseek-ai/dsh-subprocess-local` | ~~能力·执行~~ **裁剪** | 本地子进程服务（bash 执行器的底层） | **不需要**：唯一消费者是 bash（已裁）；mcp-client 自己 spawn（只复用 dsh-subprocess 的 env scrub 定义，不走此服务） | 将来需要 agent 跑命令再加回 |
| `@deepseek-ai/dsh-bash-local` | ~~能力·执行~~ **裁剪** | bash 执行器（agent 跑 shell 命令） | **不需要**：社区问答助手无 shell 场景；spine 配置 `toolBash: false` 即不 mount（index.ts:250-253） | 将来需要再加回 + 开配置 |
| `@deepseek-ai/dsh-fs-local` | ~~能力·文件~~ **裁剪** | 文件系统服务（ctx.fs） | **不需要**：fs 的消费者 = workspaceContext + skill filesystem，均可关（`workspaceContext: false`、`skills.enabled: false`）；关后无人消费 | 将来需要 agent 读写文件再加回 |

**本地插件（3→4，file: 依赖零 publish）**：

| 包 | 干什么 | 为什么需要 | 何时引入 |
|---|---|---|---|
| `@lanyuan/dsh-agent-spine`（`file:./spine`） | 自写 agent 骨架：agent 创建、回合调度、LLM 路由、session、标题 | 官方骨架是 examples 包（不依赖，§7.4）；内部依赖 core 包 dsh-agent / dsh-agent-loop / dsh-llm / dsh-session / dsh-session-title / dsh-scope / dsh-invariants / dsh-home-paths（0.1.1-rc.2 已确认） | M1 |
| `@lanyuan/dsh-mcp-client`（`file:./mcp-client`） | MCP client 插件（官方 dsh-mcp-client 的自写重写）：HTTP 消费挂载在 FastAPI /mcp 的 MCP server（StreamableHTTPClientTransport）、listTools、注册进 ctx.tools、callTool 注入 user_id（§6.3） | 官方 mcp-client 的 callTool 无 `_meta` 扩展点（user_id 注入无落点）；自写用 MCP SDK 正式库（`@modelcontextprotocol/sdk`，非 examples）实现，executor 注入点自由 | M2 |
| `@lanyuan/dsh-session-persistence-mysql`（`file:./mysql-persistence`） | MySQL 持久化 backend（8 hook，§5.2/§8.2） | v2 会话真源 = MySQL；官方无网络数据库 backend（框架空白） | M3 |
| `@lanyuan/dsh-sdk-jsonrpc-server`（`file:./server`） | JSON-RPC server 插件：getOrCreateSession → get-or-load-or-create | 官方 server 缺口（rc.5 确认只查内存）→ 服务端恢复策略（§5.3） | M3 |

**bin 入口（1，自写）**：

| 入口 | 干什么 | 为什么自写 |
|---|---|---|
| `dsh-jsonrpc-agent`（`bin/dsh-jsonrpc-agent.js`） | runtime 启动入口：读 DSH_CORDIS_CONFIG → boot() → 信号处理 | 官方 bin 是 examples 包（62 行薄封装）；自写 ~20 行等价实现（§7.4） |

### 7.2 cordis-lanyuan.yml

基于 spike `cordis-jsonrpc.yml` **裁剪为 6 插件**（去掉 subprocess/bash/fs-local）：sdk-jsonrpc-server / agent-core(**@lanyuan/dsh-agent-spine**) / llm-deepseek / sessions(jsonl) / session-checkpoints + `mcp-lanyuan` 条目（§6.2）。**spine 配置裁剪**（§7.1b 依据）：

```yaml
- id: agent-core
  name: '@lanyuan/dsh-agent-spine'
  config:
    toolBash: false              # 不 mount bash 工具（bash-local 不装）
    workspaceContext: false      # 不读工作区文件（fs-local 不装）
    skills: { enabled: false }   # 不用技能目录（fs 另一消费者，一并关）
    persona: >-                  # 兰园 persona
      你是兰园社区的 AI 小助手…
```

中期替换：sessions → mysql 插件（disabled 默认 jsonl）、sdk-jsonrpc-server → 本地 server 插件。

### 7.3 pnpm 注意事项

- **严格模式**：cordis.yml 用到的每个插件必须显式声明在 package.json（spike 1d 教训）
- **原生模块 build scripts 默认忽略**（node-pty/koffi 等）：bash 已裁剪（§7.1b），**无需 approve-builds**；若将来加回 bash 需处理（node-pty）
- 版本锁定 0.1.1-rc.2（部分包无此版本，对齐时 notarget——按需安装，不强行全量）

### 7.4 零 examples 依赖：自写 spine + 自写 bin（2026-08-23 用户定）

**不依赖任何官方 examples 包**——`dsh-agent-spine-demo` 和 `dsh-sdk-jsonrpc-demo` 都在官方仓库 `packages/examples/` 下（acp-demo / agent-spine-demo / jsonrpc-demo），只是示例用途；虽然官方 sdk-runtime 打包根（118 依赖）自己也包含它们，但作为 lanyuan 生产依赖不合理（examples 语义、可能随官方版本变动）。全部替代为本地实现：

| 官方 examples 包 | 替代 | 依据 |
|---|---|---|
| `dsh-sdk-jsonrpc-demo`（bin 启动器） | 自写 `dsh/bin/dsh-jsonrpc-agent.js`（~20 行） | 官方 bin 只是 `@deepseek-ai/dsh-app-boot` 的 62 行薄封装（boot + 信号处理，解包确认）；官方无正式替代包（dsh-sdk-jsonrpc-agent 404） |
| `dsh-agent-spine-demo`（agent 骨架插件） | 自写 `dsh/spine/` → `@lanyuan/dsh-agent-spine` | 官方骨架 = 295 行「组装器」（index.ts 265 + invariant 30），所有能力来自正式 core 包（peer 依赖）；MIT license 可合法搬运 |

**@lanyuan/dsh-agent-spine 实现要点**：
- 照 `packages/examples/agent-spine-demo/src/index.ts`（295 行）搬组装逻辑：agent 创建、回合调度（dsh-agent-loop）、LLM 路由、session、标题
- 依赖 core 包（全 0.1.1-rc.2 已确认）：`dsh-agent` / `dsh-agent-loop` / `dsh-llm` / `dsh-session` / `dsh-session-title` / `dsh-scope` / `dsh-invariants` / `dsh-tools` / `dsh-system-prompt` / `dsh-llm-retry` / `dsh-home-paths`——**声明方式：根 package.json dependencies 显式声明（宿主）+ spine/package.json peerDependencies（插件惯例，避免重复安装）**
- 可裁剪：goal / round-driver / skill / bash 等 lanyuan 不需要的组件（社区问答场景无 goal 模式）——对应 core 包也不用声明；**例外：`dsh-jobs-local` 保留**（官方 agent 无条件 mount jobs 组件，裁剪会破坏组装，§7.1b 已声明）
- 独立 npm 包目录（tsc → dist），与 mysql-persistence 同套路

**自写 bin 实现要点**：
- 读 `DSH_CORDIS_CONFIG` → `boot()`（dsh-app-boot）→ stdin/SIGTERM 信号处理
- SDK `runtime_bin` 指向 `node_modules/.bin/dsh-jsonrpc-agent`（自写 bin）；入口归 lanyuan 控制（M3 换 server 插件时入口不动）

## 8. 数据模型（v2 增量）

### 8.1 MySQL 业务数据（用户/帖子等 v1 表不变）

**v1 历史对话数据定位（2026-08-23 用户定：C 不管）**：v2 **不再维护** conversation / message 表（停止写入），前端历史列表改走 DSH session 日志派生（§10）。v1 旧对话沉底（保留在库不删，但不接入 v2 展示/搜索/注入；搜索 tool 只覆盖 v2 起的新对话）。表结构不在 v2 中演进，后续如需清理另议。

**搜索从 MySQL 解放**：v1 #42 MySQL FTS 卡点 → v2 搜索走 SQLite FTS5 投影（§8.3）。

### 8.2 MySQL PersistenceBackend 表结构（M3）

```
sessions(id VARCHAR(64) PK, version, created_at, cwd, parent_session,
         seed_length, origin, delegation_depth, agent_preset,
         incarnation CHAR(36), revision BIGINT,
         owner_user_id BIGINT NULL)   -- M3 身份映射（§6.3 身份查询插件：session_id → owner）
events(session_id, seq, type, time, data JSON, source_event_seqs JSON,
       surface_op, ignorable, PK(session_id, seq), FK → sessions CASCADE)
persistence_state(singleton TINYINT PK, store_id CHAR(36))
```

- 表结构真源 = **backend/alembic migration**（PR #97 review 定案：v2 会话三表由
  alembic 统一管理，`c2f7a9d4e5b6`；DSH 插件不再自建表，`dsh/mysql-persistence/
  src/schema.ts` 的 DDL 仅供单测自建表用，两处必须同步）
- revision 格式：`${storeIdentity}:incarnation:${incarnation}:revision:${revision}`，storeIdentity=`mysql:${host}:${db}:store:${store_id}`
- data/source_event_seqs 用 JSON 列

### 8.3 SQLite FTS5 搜索投影（session-query-sqlite 官方插件）

- 唯一读入口 `persistence.inspect(id)`（index.ts:508），与介质解耦——切 MySQL 自动索引 MySQL 数据，**零修改**
- 索引维护：revision 驱动的会话粒度对账（`_reconcile`）——非 seq 水印；revision 变 → 整会话 inspect 重建
- 部署行为：云托管容器文件系统非持久化 → 每次部署自动全量重建（数据零风险，接受自动重建，不引入持久化卷）
- FTS 内容：6 类事件有文本（user/message、assistant/message、tool/call、tool/result、todo/write、turn/end 错误原因）；chunk/header/step 不进索引；体量约为原始 10-50%
- 边界：最终一致（异步对账，刚写完可能延迟可见——产品决策，见 §14）；workers=1 规避多副本

## 9. API 设计（v2 变更）

### 9.1 /api/v2/ai/chat（v2 专属路径，与 v1 区分）

- **路径：`WS /api/v2/ai/chat/ws`**（2026-08-23 用户定版本化 `/api/v2`；2026-09-04 路线2：传输改 WebSocket——SSE 的 enableChunked 在微信云托管 callContainer 不可用。事件集与 v1 完全不同（DSH 事件 vs token/done）；v1 的 `/api/v1/ai/chat`（SSE）保留给旧前端/兼容期）
- **会话创建：`POST /api/v2/ai/session`**（PR #97 review 定案：前端先创建 session，
  再发起对话——`ai_service.get_or_create_session_v2` 为统一创建点，复用该用户
  最近 session 或新建 `{uuid}` + owner 映射；返回 `{session_id}`）
- **历史列表（M4 新增）：`GET /api/v2/ai/session/{session_id}/messages`**
  （§10.4 数据源 = DSH session 日志派生；before_seq 游标 + 倒序 + has_more；
  归属校验与 /chat 一致——非 owner 403）
- 请求：认证（JWT）+ 消息 + 会话 id（**必填**，由 /api/v2/ai/session 先获取；
  DSH 侧 get-or-load-or-create 复用/恢复/物化）
- **归属校验（PR #97 dev-lead review）**：chat 入口校验 `session owner == 调用者`
  （sessions 表 owner_user_id）——不匹配或 owner 缺失 → 403（统一 403 防 session
  枚举）。否则调用者 B 持 A 的 session_id 可 resume A 的会话上下文，且工具执行
  身份来自 session owner（get_my_profile/记忆等均为 A 的），即横向越权
  （PR #94 /mcp 同类修复）。**威胁模型取舍**：session_id 不是无条件 bearer 凭证——
  必须由 JWT 验证过的调用者身份授权；创建点（POST /api/v2/ai/session）写入
  owner 映射，绕过创建点直接构造 id 的请求会被 403 拒绝（fail-closed）。
- 响应事件集：**DSH 事件白名单子集**（§4.2），不再是 v1 的 token/done/error
- `done` 语义：`turn/end`（reason.kind）→ 前端关流；`session.status=idle` 兜底（后端消费，不转发）
- 错误：runtime 崩溃 → `error` 帧（文案「请重试」，详情只进日志）

### 9.2 其余 API

维持 `/api/v1` 不变（v1 TECH_SPEC §4）——**业务 API（用户/帖子/评论/通知/记忆）
不属于 v1/v2 之争（2026-08-30 用户定）**：文件永存、路径不变，`@mcp_tool` 直接
挂在业务 endpoint 上（@tool 旁叠加，§6.4b）——**不新增 v2 业务端点**；
v2 对话流式只新增 `WS /api/v2/ai/chat/ws`（§9.1；2026-09-04 SSE POST 退役）。

### 9.3 搜索能力

**前端不做搜索 API/搜索页**（2026-08-23 用户定）——搜索是给 LLM 的能力：v1 用 `search_history` tool，v2 由 DSH session-query 覆盖（§6.4b 定：不迁移业务工具）。

## 10. 前端 v2（小程序）

### 10.0 双版本策略（2026-08-23 用户定）

**忽略 v1，以 v2 为准**——不做并行期，v2 前端直接替换（v1 前端契约/页面退役）。

### 10.1 事件消费

- 用户消息：`user/message` 事件渲染用户气泡（事件流单一数据源，前端不做本地乐观渲染）
- AI 气泡：**`step/start` 承接 v1 `message:start` 语义**（每次 LLM 调用一条气泡，粒度对齐）——`step/start` 开新气泡（若上一个气泡为空则先删——纯工具步骤无文字不显示）；`assistant/chunk`（text-delta）追加当前气泡；`turn/end` 收尾（最终气泡仍空则丢弃）
- 回合边界：`turn/start` = 一次 user_prompt 处理开始（对应 v1 `loop:start`，前端重置回合状态，不建气泡）
- thinking：后端已过滤（§4.2），前端不收到 reasoning-delta——暂不展示思考过程
- 工具过程：**不展示**（tool/call、tool/result 后端已过滤，§4.2——工具使用对用户透明）
- 错误/重试：`turn/end` reason.kind=error / SSE error 帧；**v1 `retry_wait` 无对应**——DSH llm-retry 在 turn 内静默重试（前端透明，最终 turn/end reason 反映结果）
- v1 事件对照：token→assistant/chunk(text-delta)、done→turn/end、error→turn/end reason=error / error 帧、message:start→**step/start**（粒度=一次 LLM 调用）、loop:start→turn/start、retry_wait→无（静默重试）

### 10.2 状态维护

前端本地 state（不引 session-projection）：消息列表（user/message + text-delta 追加）、thinking 展开态、当前回合状态。

### 10.3 组件树变化

v1 ai-chat 页改造：token 追加逻辑 → DSH 事件分发（user/message 渲染用户气泡 + text-delta 追加）；新增 thinking 折叠区（暂不渲染，§10.1）。其余页面不动。

### 10.4 历史列表

- 数据源 = **DSH session 日志派生**（M3 起：MySQL events → 消息序列，或 FTS5 投影）；v1 旧对话不展示（§8.1 C 定案）
- v1 conversation/message 表不再读取
- **API（M4 落地）**：`GET /api/v2/ai/session/{session_id}/messages?before_seq=&limit=`
  （§9.1 新增，与 v1 /api/v1/ai/messages 同款分页契约：游标 + 倒序 + has_more）
  - 投影规则（`backend/app/ai/history.py`，与前端流式消费语义一致 §10.1）：
    `user/message` → 用户气泡；`step/start` + `assistant/chunk(text-delta)` → assistant
    气泡；纯工具步骤/空回复丢弃；reasoning-delta 不投影
  - **分页 = turn 级**（同轮不拆分）：游标 cursor = 本页最旧 turn/start 的 seq
    （加载更早 = 取 seq < cursor 的 turn）；`limit` = 每页最多 turn 数。为什么
    不用消息/事件 seq 游标：DSH 真实事件序是 turn/start → step/start →
    user/message → chunk → turn/end——assistant 段起始 seq 小于同轮 user 消息
    seq，按 seq 分页会把一轮对话拆到不同页（回复先于提问）
  - `events.data` 经 text() 原生查询是 **str**（MySQL/SQLite 驱动均不反序列化
    JSON 列）——投影层显式 json.loads（`_as_dict`），ORM 类型化查询返回 dict
    时兼容
  - 归属校验与 /chat 一致（owner 必须是调用者，403 防 session 枚举）

## 11. 部署（微信云托管）

### 11.1 镜像

- 基础镜像：Node（runtime）+ Python（FastAPI + MCP server）双运行时，或分层多阶段
- 构建：`pnpm install`（dsh/ 目录）→ tsc 编译本地插件（中期）→ backend 依赖安装
- 启动：uvicorn（workers=1 起步）+ lifespan 拉起 DSH runtime
- 环境变量：DEEPSEEK_API_KEY（凭证 seam）、DSH_*（显式设置）
- **Dockerfile（M4 落地，仓库根 `Dockerfile`）**：多阶段构建
  - 阶段 1（dsh-builder）：`node:22-slim` + corepack pnpm →
    `pnpm install --frozen-lockfile`（file: 本地插件 install 自动跑 prepare tsc
    编译到 lib/，再显式 `pnpm run build` 兜底）
  - 阶段 2（runtime）：`python:3.12-slim` + Node 22 二进制（COPY 自 node 官方镜像；
    node:20 跑 pnpm 11 报 node:sqlite 缺失——2026-09-03 docker 实测；npm/npx 不拷，
    runtime 不依赖且 symlink 被 COPY 解引用会损坏）
    → uv export --frozen --no-dev --no-install-project 生成 requirements
    （本地零网络，uv.lock 仍单一真源）→ pip -i 清华 安装（构建环境直连
    pypi.org/Fastly 不通，2026-09-03 docker 实测）→ COPY
    dsh/ 家目录（pnpm 产物）+ backend（app/ + alembic/ + tools/，含 MCP server）
  - CMD：`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80 --workers 1`
  - 必需 env：DEEPSEEK_API_KEY、DATABASE_URL（MySQL，DSH 侧由 dsh_runtime
    推导注入 LANYUAN_MYSQL_*）；可选：LANYUAN_MCP_TOKEN（未配置进程内自动
    生成）、LANYUAN_MCP_URL、DSH_DIR、V2_LLM_MODEL
  - **部署端口绑定（PR #98 review 修复）**：CMD 监听 80 → Dockerfile 显式
    `ENV LANYUAN_MCP_URL=http://127.0.0.1:80/mcp/`（默认 8000 与部署端口
    不一致会断 MCP 工具桥）+ `ENV DSH_DIR=/app/dsh`（镜像打平 backend 层级
    后 parents[3] 推导失效，显式注入 dsh 家目录；云托管可配）

### 11.2 体积（实验 5 数据）

- npm node_modules ~341M；exe 197M（不用）；Python venv ~328M
- 镜像预估 1.2G 量级——**云托管镜像限制待确认**（§14）
- 不需要自建 exe（体积收益有限 + fork 税，spike exe 定制评估）

### 11.3 启动

- spawn + initialize 0.30s（常驻模式仅 worker 启动一次）；首 run 0.86s
- 崩溃恢复：TransportClosedError → close+start

## 12. 非功能需求

### 12.1 性能

- 首 token ~1.2s（实验 2b，与 v1 相当）
- 并发：单 runtime 多 session 安全（实验 4a）；workers=1 起步

### 12.2 安全

- user_id 桥层强制注入（§6.3），LLM 永不提供身份
- **/mcp 传输层内部认证（PR #94 review 修复）**：内部共享密钥 header
  （`X-Lanyuan-Internal-Token`，env `LANYUAN_MCP_TOKEN`），未认证请求 401——
  外部 client 无法直连 /mcp 伪造 `_meta.user_id` 冒充用户（此前实测可越权改他人资料/冒充发帖）
- 密钥：DEEPSEEK_API_KEY 走凭证 seam/env，不硬编码、不进 git
- 错误信息不外泄（内部详情只进 error.log）

### 12.3 可观测

- DSH 侧：jsonl session 日志（审计/回放）
- FastAPI 侧：error.log traceback；DSH 事件流日志（调试开关）

## 13. 实施计划（里程碑）

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| **M1 骨架** | dsh/ 家目录 + 自写 spine/bin + SDK 拉起 runtime + 事件层（过滤）+ 最短对话（每请求新 session **无注入**）——**含裁剪验证**：无 bash/subprocess/fs 配置跑通对话 + MCP 工具桥（§7.1b 三个能力包去掉后实测） | spike 1d/1e、2a-2h |
| **M2 工具桥** | MCP server（首批工具）+ user_id 注入机制验证 | spike 3/3b |
| **M3 会话** | **MySQL PersistenceBackend 插件 + @lanyuan/dsh-sdk-jsonrpc-server（get-or-load-or-create）** | MySQL 落地清单 + §5.2/5.3 |
| **M4 前端 v2 + 部署** | 小程序事件消费改造（v2 直接替换 v1）+ Docker + 云托管 | §10、§11 |

每个里程碑独立 PR + dev-lead review；实现顺序串行（一个功能块一个 issue）。

## 14. 待确认项

已定案（2026-08-23 用户）：thinking 前端暂不展示（§4.4）；搜索只做 LLM tool 不做前端 API（§9.3）；FTS5 延迟可见可接受（§8.3）；忽略 v1 以 v2 为准（§10.0）；MCP 每 worker 一个（§3.1）；会话 = MySQL + get-or-load-or-create（§5）；v1 历史对话不维护不迁移（§8.1 C）；**过渡期每请求新 session 无注入（2h 历史注入不采用，§5.1）**。

| # | 项 | 现状 | 谁定 |
|---|---|---|---|
| 1 | 微信云托管镜像大小限制 | 已实测 docker 构建 **1.33GB**（PR #99：node:22 + pip 清华源；分层 pip 依赖 456M + dsh/ 313M + node 144M + 基础 ~180M + 代码 ~240M）；云托管侧上限 ≥2GB 是否满足待云托管环境确认 | M4 实测 |
| 2 | user_id 注入具体机制（MCP _meta 透传 vs DSH 插件钩子） | **已定案（M2）**：session_id 编码 user_id + 自写桥插件注入 callTool `_meta`（§6.3）——**M2 过渡方案** | dev/M2 ✅ |
| 3 | user_id 注入终态（session_id 编码退役） | **已定案（M3）**：身份查询插件——session_id 恢复纯 uuid，FastAPI 内部身份端点 + 桥插件查询，映射并入 sessions 表 owner_user_id（§6.3/§8.2） | 用户 2026-08-31 ✅ |

---

*本文所有实验结论引用自 docs/dsh/spike.md（#82，已 merge）；格式参照 v1 docs/TECH_SPEC.md。*
