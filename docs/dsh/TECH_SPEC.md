# 兰园公共底座 v2 — 技术方案（TECH_SPEC）

> Issue: #86 · 基于 #82 spike 结论（docs/dsh/spike.md，已 merge）
> 范围：**agent 子系统 DSH 重写**。Python 后端（认证/业务 API/MySQL）保持，业务数据模型见 v1 docs/TECH_SPEC.md（本文不重复）
> 状态：待用户审阅 → dev-lead review

## 目录

- [1. 概述与范围](#1-概述与范围)
- [2. 技术选型（v2 增量）](#2-技术选型v2-增量)
- [3. 系统架构](#3-系统架构)
- [4. SSE 协议：透传 DSH 事件](#4-sse-协议透传-dsh-事件)
- [5. 会话策略（三段演进）](#5-会话策略三段演进)
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
              ├─ /ai/chat: 认证 → 会话组装 → Python SDK → DSH runtime（Node 子进程）
              │            → on_notification 透传 session.event → SSE（不做翻译）
              ├─ 业务工具桥: 每 worker 一个 Python MCP server 进程（@tool schema 复用）
              └─ MySQL（业务数据 + 会话投影）
```

### 1.3 与 v1 的关系

- v1/v2 双版本并行期：v2 后端独立部署，`/ai/chat` 逐步切流
- 前端 v2 小程序消费 DSH 事件集（见 §10）；v1 前端契约（token/done/error）不再维护
- 排除决策（用户已确认，spike.md 落档）：
  - **fork 分支语义**（git 心智不 human，lanyuan 无此场景）
  - **session-projection**（单前端直连事件流，前端本地维护状态即可；投影是整值语义表达不了流式序列）
  - **npm CLI 用法**（v2 后端 SDK 驱动，无 CLI 场景）
  - **不做事件翻译层**（见 §4）

## 2. 技术选型（v2 增量）

| 组件 | 选型 | 依据 |
|---|---|---|
| DSH runtime | `@deepseek-ai/*` **0.1.1-rc.2 统一批次**（dsh / dsh-sdk-jsonrpc-server / dsh-agent-spine-demo / dsh-sdk-jsonrpc-demo / dsh-llm-deepseek / dsh-session-persistence-jsonl / dsh-session-checkpoint-policy / dsh-subprocess-local / dsh-bash-local / dsh-fs-local / dsh-mcp-client） | spike 1d：旧 rc.1 引用 npm 不存在的包（发布缺口）；rc.2 批次验证跑通 |
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
       └─ 环境变量: 显式管理 DSH_SESSION_ROOT / DSH_HOME / DSH_CWD，不继承 shell 残留（2g 实验）
  └─ MCP server 子进程（每 worker 一个，Python，stdio）
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
│       │   ├── sse_passthrough.py # SSE 透传（帧包装/错误/done 判定）
│       │   ├── session_service.py # 会话组装（短期注入）/ MySQL 读写
│       │   └── mcp_bridge.py      # MCP server 进程管理（spawn/健康检查）
│       └── tools/mcp_server/      # Python 业务工具 MCP server（复用 @tool schema）
├── miniprogram/                   # 前端 v2（消费 DSH 事件集）
└── dsh/                           # DSH 运行时一体化家目录（删除即卸载）
    ├── package.json               # 11 个 0.1.1-rc.2 依赖 + file: 本地插件
    ├── cordis-lanyuan.yml         # 运行时配置（见 §7）
    ├── node_modules/              # pnpm install 产物（部署时生成）
    └── mysql-persistence/         # 中期：MySQL PersistenceBackend 插件（TS → dist/）
```

### 3.3 请求数据流（/ai/chat）

```
1. 前端 → POST /ai/chat（认证通过）
2. FastAPI 组装请求：短期 = 读 MySQL 历史 → content blocks + 新问题
3. harness.run(prompt, on_notification=...)
4. on_notification 实时到达 → sse_passthrough 逐事件包装 SSE 帧 → 前端
5. 回复写回 MySQL（message/conversation 投影）
6. 工具调用 → DSH 内部调 MCP server（§6）→ 结果回 agent → 继续/结束
```

## 4. SSE 协议：透传 DSH 事件

### 4.1 原则（用户 2026-08-23 定）

**向 DSH 靠近，不让 DSH 向我们靠近**。FastAPI SSE 层**只做传输适配**，不做事件翻译——reasoning/tool/usage 信息不丢。

### 4.2 DSH 事件集（实验 2a/2c/2d 实测）

`session.event` 通知 payload：`{type, data, ...}`

| type | data 关键字段 | 前端用途 |
|---|---|---|
| `assistant/chunk` | `chunk.type`=text-delta / reasoning-delta / block-start / block-end / usage / finish；`chunk.text` | 正文流 / thinking / 用量 |
| `tool/call` | `name`（mcp__lanyuan__*）、`arguments` | 工具调用过程展示 |
| `tool/result` | `message` | 工具结果展示 |
| `turn/start` / `turn/end` | `reason.kind`=completed / max-tokens / error | 回合边界；**turn/end = done 判定** |
| `user/message` | content | 用户消息回显 |
| `session/title` | title | 会话标题 |
| `request/header` | — | 诊断 |
| `agent/inbox/spliced` | — | 注入确认 |
| 通知 `session.status` | status=idle | 兜底 done 判定（turn/end 丢失时） |

### 4.3 传输适配层职责（sse_passthrough.py）

| 职责 | 说明 |
|---|---|
| SSE 帧包装 | `event: <type>\ndata: <json>\n\n`（原样透传 type + data） |
| 认证 | 请求鉴权通过才建立流 |
| user_id 绑定 | 会话与用户绑定（§6 注入用） |
| done 判定 | `turn/end`（reason.kind）或 `session.status=idle` 兜底 → 关流 |
| 错误处理 | runtime 崩溃（TransportClosedError）→ SSE error 帧 + 日志；**不暴露内部错误详情**（v1 规则沿用：只写「请重试」，traceback 记 error.log） |
| 断连 | 前端断开 → 取消 run（cancel 语义） |

### 4.4 明确不做

- ❌ text-delta→token 等瘦身翻译（v1 契约退役）
- ❌ reasoning-delta 丢弃（thinking 展示 = 产品决策，见 §14）
- ❌ 事件字段删减

## 5. 会话策略（三段演进）

### 5.1 短期（M1/M2 落地）：每请求新 session + 历史注入

- 已验证（实验 2h）：`session/prompt` 注入 `[历史1, 历史2, 新问题]` content blocks → agent 正确理解上下文（「地暖 22°C」）
- 前端历史展示/搜索仍走 MySQL（不变）
- DSH session 承载单次请求处理 + JSONL 审计
- 跨会话压缩（rotation）FastAPI 侧沿用 v1 逻辑

### 5.2 中期（M3）：MySQL PersistenceBackend 插件

两层架构（PersistenceCoordinator 编排层全复用 + 物理层 8 hook 只写 MySQL store）：
- 代码位置：`dsh/mysql-persistence/`（TS → dist/，file: 本地依赖零 publish）
- 表结构：sessions / events / persistence_state（见 §8）
- 8 hook 实现要点（照官方 SqliteStore 翻译，~300-400 行）：
  - `appendBatch`：InnoDB 事务 + `SELECT ... FOR UPDATE` 锁行读 tail → 校验 `first.seq === next seq` → 批量 INSERT → revision+1
  - `loadStoredFrom?`：实现（`WHERE seq >= ?`）
  - TornMarker 用 number（MySQL 事务原子提交，torn 罕见）
  - 不做 chunk 打包 codec（行数非瓶颈）
- 两个前提：cordis.yml 禁用默认 jsonl persistence（`disabled: true`）；运行时不重建（npm install 是部署步骤）

### 5.3 长期：get-or-load-or-create（官方缺口，服务端策略）

- 官方 rc.5 缺口确认：`handleRequest` 仅 initialize/session/prompt/shutdown；`getOrCreateSession` 只查内存（"发现框架空白"叙事）
- 设计（已定稿）：不在协议加新方法，在 `session/prompt` 内部扩展：
  ```
  prompt(id) → 内存有？用内存的
             : 持久化有？load 成 live session（核心 log-seed 重放）
             : 都没有？新建
  ```
- Python SDK 零改动（服务端策略）；collision 守卫保留为 load 失败兜底
- 三个边界：并发写 owner 机制 + workers=1/session 亲和；无条件 load（id 即身份）；load 语义界定写文档
- 实现 = 本地 `@lanyuan/dsh-server` 插件替换官方 sdk-jsonrpc-server（同 mysql-persistence 套路）

### 5.4 环境变量管理（2g 实验教训）

`DSH_SESSION_ROOT` 残留会静默改变落盘位置并间接导致 id collision 误判。backend 启动时显式设置/清除全部 DSH 环境变量，不继承 shell 残留。

## 6. MCP 工具桥

### 6.1 架构（实验 3/3b 验证）

```
DSH runtime
  └─ @deepseek-ai/dsh-mcp-client 插件（cordis.yml 一个实例 = 一个 server）
       spawn → Python MCP server（fastmcp，stdio）
                 └─ 业务工具（复用 @tool schema + 执行逻辑，连 MySQL）
```

- 工具注册名：`mcp__<serverName>__<rawName>`（`mcp__lanyuan__search_history`）
- 支持 stdio / streamable-http 两种 transport（v2 用 stdio）
- 断线自动重连（指数退避，默认开）、崩溃 supervisor 重启、HMR 热更新
- 配置模板 = `spike/npm-dsh/cordis-mcp.yml`（已验证）

### 6.2 配置模板（v2 cordis-lanyuan.yml 的 mcp 部分）

```yaml
- id: mcp-lanyuan
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: lanyuan
    transport: stdio
    command: /path/to/backend/.venv/bin/python
    args: ['/path/to/backend/tools/mcp_server/main.py']
```

### 6.3 user_id 注入（安全设计，防 LLM 伪造越权）

**原则（已定）**：身份由桥层强制绑定，LLM 永不提供/自填身份。

- 请求级：FastAPI 认证后，把 `user_id` 绑定到 DSH 请求上下文（短期注入方案：随会话元数据传递）
- 工具级：MCP server 端工具签名**不含** user_id 参数（LLM 不可见）；执行时身份来自桥层绑定而非 LLM 参数
- 候选机制（实现时验证，见 §14）：MCP `callTool` 的 `_meta` 扩展透传 / DSH 侧本地插件在调用路径注入
- 校验：任何工具实现不得信任模型输入中的身份字段

### 6.4 首批工具清单（v1 既有 @tool 迁移）

| 工具 | 说明 |
|---|---|
| `search_history` | 搜索用户过往对话（v1 #42 卡点 → v2 搜索走 SQLite FTS5 投影，见 §8） |
| `get_profile` | 当前用户资料（昵称/社区/楼栋/单元/房号） |
| 其余 v1 工具按需迁移 | 发帖/评论/记忆类（里程碑内逐个搬） |

## 7. dsh/ 家目录

### 7.1 package.json（显式 11 依赖，0.1.1-rc.2）

```jsonc
{
  "dependencies": {
    "@deepseek-ai/dsh": "0.1.1-rc.2",
    "@deepseek-ai/dsh-agent-spine-demo": "0.1.1-rc.2",
    "@deepseek-ai/dsh-sdk-jsonrpc-server": "0.1.1-rc.2",
    "@deepseek-ai/dsh-sdk-jsonrpc-demo": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm-deepseek": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session-persistence-jsonl": "0.1.1-rc.2",
    "@deepseek-ai/dsh-session-checkpoint-policy": "0.1.1-rc.2",
    "@deepseek-ai/dsh-subprocess-local": "0.1.1-rc.2",
    "@deepseek-ai/dsh-bash-local": "0.1.1-rc.2",
    "@deepseek-ai/dsh-fs-local": "0.1.1-rc.2",
    "@deepseek-ai/dsh-mcp-client": "0.1.1-rc.2",
    // 中期追加（file: 本地插件，零 publish）：
    "@lanyuan/dsh-session-persistence-mysql": "file:./mysql-persistence",
    "@lanyuan/dsh-server": "file:./server"
  }
}
```

### 7.2 cordis-lanyuan.yml

基于 spike `cordis-jsonrpc.yml`（8 插件）：sdk-jsonrpc-server / agent-core(spine) / llm-deepseek / sessions(jsonl) / session-checkpoints / subprocess / bash / fs-local + `mcp-lanyuan` 条目（§6.2）。中期替换：sessions → mysql 插件（disabled 默认 jsonl）、sdk-jsonrpc-server → 本地 server 插件。

### 7.3 pnpm 注意事项

- **严格模式**：cordis.yml 用到的每个插件必须显式声明在 package.json（spike 1d 教训）
- **原生模块 build scripts 默认忽略**（node-pty/koffi 等）：bash 能力受限需 `pnpm approve-builds`；核心对话链路不受影响
- 版本锁定 0.1.1-rc.2（部分包无此版本，对齐时 notarget——按需安装，不强行全量）

## 8. 数据模型（v2 增量）

### 8.1 MySQL 业务数据（v1 不变）

conversation / message 仍为业务数据 + 前端历史投影（写路径 v2 保持）。**搜索从 MySQL 解放**：v1 #42 MySQL FTS 卡点 → v2 搜索走 SQLite FTS5 投影（§8.3）。

### 8.2 MySQL PersistenceBackend 表结构（中期）

```
sessions(id VARCHAR(64) PK, version, created_at, cwd, parent_session,
         seed_length, origin, delegation_depth, agent_preset,
         incarnation CHAR(36), revision BIGINT)
events(session_id, seq, type, time, data JSON, source_event_seqs JSON,
       surface_op, ignorable, PK(session_id, seq), FK → sessions CASCADE)
persistence_state(singleton TINYINT PK, store_id CHAR(36))
```

- revision 格式：`${storeIdentity}:incarnation:${incarnation}:revision:${revision}`，storeIdentity=`mysql:${host}:${db}:store:${store_id}`
- data/source_event_seqs 用 JSON 列

### 8.3 SQLite FTS5 搜索投影（session-query-sqlite 官方插件）

- 唯一读入口 `persistence.inspect(id)`（index.ts:508），与介质解耦——切 MySQL 自动索引 MySQL 数据，**零修改**
- 索引维护：revision 驱动的会话粒度对账（`_reconcile`）——非 seq 水印；revision 变 → 整会话 inspect 重建
- 部署行为：云托管容器文件系统非持久化 → 每次部署自动全量重建（数据零风险，接受自动重建，不引入持久化卷）
- FTS 内容：6 类事件有文本（user/message、assistant/message、tool/call、tool/result、todo/write、turn/end 错误原因）；chunk/header/step 不进索引；体量约为原始 10-50%
- 边界：最终一致（异步对账，刚写完可能延迟可见——产品决策，见 §14）；workers=1 规避多副本

## 9. API 设计（v2 变更）

### 9.1 /ai/chat（SSE 透传后）

- 请求：不变（认证 + 消息 + 会话 id）
- 响应事件集：**DSH 原始事件透传**（§4.2 表），不再是 v1 的 token/done/error
- `done` 语义：`turn/end`（reason.kind）→ 前端关流；`session.status=idle` 兜底
- 错误：runtime 崩溃 → `error` 帧（文案「请重试」，详情只进日志）

### 9.2 其余 API

全部不变（v1 TECH_SPEC §4）。v2 只改 /ai/chat 内部实现。

### 9.3 新增（如需要）

- 搜索接口（SQLite FTS5 投影）——复用 session-query 能力，API 形态 v1 search_history 对齐，待 v2 前端需求确认（§14）

## 10. 前端 v2（小程序）

### 10.1 事件消费

- 消息流：`assistant/chunk`（text-delta）追加气泡；`turn/end` 收尾
- thinking：`assistant/chunk`（reasoning-delta）→ 可折叠思考区（产品决策，§14）
- 工具过程：`tool/call` / `tool/result` → 过程卡片（工具名 + 参数 + 结果）
- 多轮：`message:start` 语义由 `turn/start` 承接（新气泡）
- 错误/重试：`turn/end` reason.kind=error / SSE error 帧

### 10.2 状态维护

前端本地 state（不引 session-projection）：当前工具、thinking 展开态、消息列表、用量统计（usage 事件）。

### 10.3 组件树变化

v1 ai-chat 页改造：token 追加逻辑 → DSH 事件分发；新增 thinking 折叠区 / 工具过程卡片组件。其余页面不动。

## 11. 部署（微信云托管）

### 11.1 镜像

- 基础镜像：Node（runtime）+ Python（FastAPI + MCP server）双运行时，或分层多阶段
- 构建：`pnpm install`（dsh/ 目录）→ tsc 编译本地插件（中期）→ backend 依赖安装
- 启动：uvicorn（workers=1 起步）+ lifespan 拉起 DSH runtime
- 环境变量：DEEPSEEK_API_KEY（凭证 seam）、DSH_*（显式设置）

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
- 密钥：DEEPSEEK_API_KEY 走凭证 seam/env，不硬编码、不进 git
- 错误信息不外泄（内部详情只进 error.log）

### 12.3 可观测

- DSH 侧：jsonl session 日志（审计/回放）
- FastAPI 侧：error.log traceback；DSH 事件流日志（调试开关）

## 13. 实施计划（里程碑）

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| **M1 骨架** | dsh/ 家目录 + SDK 拉起 runtime + SSE 透传 + 最短对话（每请求新 session） | spike 1d/1e、2a-2h |
| **M2 工具桥** | MCP server（首批工具）+ user_id 注入机制验证 | spike 3/3b |
| **M3 会话** | MySQL PersistenceBackend 插件（或按需直接做 get-or-load-or-create 本地插件） | MySQL 落地清单 |
| **M4 前端 v2 + 部署** | 小程序事件消费改造 + Docker + 云托管 | §10、§11 |

每个里程碑独立 PR + dev-lead review；实现顺序串行（一个功能块一个 issue）。

## 14. 待确认项

| # | 项 | 现状 | 谁定 |
|---|---|---|---|
| 1 | thinking（reasoning-delta）是否前端展示 | 技术就绪，产品决策 | 用户 |
| 2 | 微信云托管镜像大小限制 | 未实测 docker 构建 | 用户/M4 实测 |
| 3 | user_id 注入具体机制（MCP _meta 透传 vs DSH 插件钩子） | 原则已定，机制实现时 spike 验证 | dev/M2 |
| 4 | 搜索接口 API 形态（v2 前端是否需要独立搜索页） | 依赖前端需求 | 用户 |
| 5 | 历史注入的延迟可见性（FTS5 异步对账）是否可接受 | 技术边界已明确 | 用户 |
| 6 | v2 前端双版本切换策略（v1 小程序 vs v2 小程序并行/替换） | 未定 | 用户 |
| 7 | MCP server 进程数（每 worker 一个 vs 全局一个） | 默认每 worker 一个，量大再评估 | dev/M4 |

---

*本文所有实验结论引用自 docs/dsh/spike.md（#82，已 merge）；格式参照 v1 docs/TECH_SPEC.md。*
