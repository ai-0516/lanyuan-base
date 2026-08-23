# DSH（DeepSeek Harness）集成 Spike 结论

> Issue: #82 · 日期：2026-08-18 · 状态：待用户审阅（未建 PR）
> 全部实验在 `spike/` 目录，使用 DEEPSEEK_API_KEY 真实调用（deepseek-v4-flash）

## 形态背景：三种集成形态（2026-08-10 方案讨论定）

「形态二」是本 spike 的验证对象，先交代三种形态的定义与选型依据：

| 形态 | 描述 | 社区插件自由度 | 评价 |
|---|---|---|---|
| **形态一** | Python SDK + 官方 bundled exe（开箱即用） | ❌ 插件集编译时固定，新增社区插件 = 改依赖 + 重新 build exe | 适合先跑通学习 DSH，不追社区插件 |
| **形态二** ⭐ | **Python FastAPI 后端保持 + npm 完整版 DSH runtime 子进程**（stdio JSON-RPC，不对前端暴露任何 endpoint） | ✅ `npm install` + cordis.yml 加一行配置即可 | **推荐**：后端零重写，满足「社区找插件配置一下」核心诉求 |
| **形态三** | 后端全改 TS/Node | ✅ 最高（还能直接用 DSH web-app/API 层） | 业务全重写（认证/帖子/评论/记忆/评测全 TS 化），不建议 |

形态二要点：前端只连 FastAPI `/ai/chat`（SSE 契约不变，零改动）；每个 FastAPI worker 一个常驻 DSH 子进程（lifespan 管理）；业务工具走 MCP 桥（Python 原生，`@tool` schema 可复用）；`user_id` 桥层强制注入防越权。

## 结论速览

**形态二可行**：Python FastAPI 后端保持 + DSH runtime 子进程（Node），5 项实验全部通过。v2 可进入 TECH_SPEC 设计阶段。

| # | 实验 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | DSH runtime 跑通 | ✅ | SDK exe / 自定义 cordis.yml 链路跑通；npm 形态见 1d/1e |
| 2 | SSE 流式转发 | ✅ | `text-delta` 事件逐 token，首 token ~1.2s；**跨进程会话恢复受限（重要发现）** |
| 3 | MCP 工具桥 | ✅ | Python MCP server → dsh-mcp-client → agent 真实调用业务工具 |
| 4 | 并发 + 崩溃重启 | ✅ | 3 session 并发互不干扰；kill 后 close()+start() 恢复服务 |
| 5 | 打包体积/启动 | ✅ | exe 197M / npm 341M / spawn 0.30s |

## 实验 1：runtime 跑通（npm 版）

两条链路实测成功（npm CLI 仅作旁证，不采用）：

1. **SDK + bundled exe**（零配置）：`harness.run()` → `finish=completed`，93 个事件
2. **npm 版 CLI**：`npm install @deepseek-ai/dsh@0.1.0-rc.7`，`dsh --profile headless "任务"` 真实调用 DeepSeek API；内置 web search 工具被 agent 真实调用（搜索 V4 Pro 新闻并返回带引用来源的总结）——**不采用**：v2 后端是 SDK 驱动（无 CLI 使用场景），此链路仅作 npm 形态可用性旁证
3. **自定义配置**：`--patch`（persona 覆盖生效）；SDK `cordis=` 参数（自定义 cordis.yml persona 生效，agent 自称「兰园社区 AI 小助手」）

**发现**：npm 发布是分阶段的——`@deepseek-ai/dsh` CLI（rc.7）已发布，但 **jsonrpc-agent 相关包（dsh-sdk-jsonrpc-server 等）尚未发布**（8/10 rc.1 → 8/17 rc.7，节奏很快）。SDK 驱动 npm 自定义 runtime 需等这些包发布，或从源码 build（本地 pnpm build 遇 core dump，未走通）。

## 实验 2：SSE 流式转发（含关键发现）

### 事件结构（实测确认）

```
session.event 事件流（on_notification 实时到达）：
  assistant/chunk:  data.chunk = {type: "text-delta", text: "..."}      → SSE token
                    data.chunk = {type: "reasoning-delta", ...}         → thinking（前端不渲染，丢弃）
                    block-start / block-end / usage / finish
  tool/call:        data = {turn, step, callId, name, arguments}
  tool/result:      data = {turn, step, message: {source, content}}
  turn/start|end:   data.reason.kind = completed|max-tokens|error
  user/message, session/title, request/header, agent/inbox/spliced
```

翻译层（已验证端到端，spike 阶段验证传输可行性）：`text-delta` → `event: token\ndata: {"content": <text>}`，`turn/end` → `event: done`。首 token 延迟 1.2-1.3s（与 v1 相当），多轮同一 session 正常（turn 递增）。

**⚠️ 方向修正（2026-08-23 用户定）**：v2 **不做瘦身翻译**——DSH 定义了更丰富的 event 类型（reasoning-delta / tool/call / tool/result / usage 等），原则是「向 DSH 靠近，不让 DSH 向我们靠近」：FastAPI SSE 层只做传输适配（认证 + user_id 绑定 + SSE 帧包装 + 错误处理 + done 判定），**原样透传 `session.event`（type + data）**，信息不丢；前端 v2 直接消费 DSH 事件集（thinking 展示、工具调用过程、usage 统计均为产品价值）。**（2026-08-23 晚精化：后端白名单过滤，只发前端关心的子集——thinking/usage/内部事件不转发，事件格式仍原样；见 TECH_SPEC §4）**

### ⚠️ 关键发现：跨进程会话恢复不可用（id collision）

- SDK 的 `session/prompt` 只从**内存 registry** get-or-create session，**不 load 磁盘 JSONL**
- 进程重启后用同一 session id → `session "x" already has a persisted log on disk that does not match this live session (id collision)`
- 根因：JSONRPC 协议只有 `initialize` / `session/prompt` / `shutdown` 三个方法，**没有 load/resume/fork**；JSONL 持久化的设计目标是崩溃恢复/审计/回放（同一 runtime 生命周期内），不是「重启后恢复用户会话」
- 同一进程内多次 run 同 session：正常（live session 在内存，seed 续接）

### v2 会话策略（已验证替代方案）

**MySQL 为历史真源 + 每请求新建 DSH session + 历史注入**：

- `session/prompt` 接受 content blocks 数组（无 role，作为一条 user 消息）
- 实测：注入 `[历史1, 历史2, 新问题]` 三块文本 → agent 正确理解上下文（「您刚才把地暖调到了 22°C」）
- 前端历史展示/搜索仍走 MySQL（不变）；DSH session 承载单次请求处理 + JSONL 审计
- 跨会话压缩（rotation）由 FastAPI 侧沿用 v1 逻辑；DSH 的 compaction 只作用于单请求内

### 环境变量坑

`DSH_SESSION_ROOT` 残留环境变量会静默改变 session 落盘位置（写错目录）并间接导致 id collision 误判。生产代码必须显式管理 DSH 相关环境变量，不继承 shell 残留。

## 实验 3：MCP 工具桥（v2 工具方案）

- Python 侧：`fastmcp` 写 MCP server（stdio），暴露 `search_history` / `get_profile`（模拟业务工具）
- DSH 侧：`@deepseek-ai/dsh-mcp-client`（已在 @deepseek-ai/dsh 依赖内，rc.7），cordis patch 用 **`- insert:` 语法**（不是直接条目！）
- 实测：agent 真实调用 `mcp__lanyuan__search_history`，正确返回并总结模拟数据
- 工具名：`mcp__<serverName>__<rawName>`；支持 stdio / streamable-http 两种 transport
- **驱动脚本补齐（2026-08-23 spike 3b）**：8-18 时拉起方（SDK + cordis 配 mcp-client + run）是临时命令没留文件 → 补 `3b_mcp_dsh_drive.py` + `npm-dsh/cordis-mcp.yml`（npm runtime 8 插件 + mcp-lanyuan 条目，`command: .venv/bin/python` spawn 子进程）→ 全链路复跑通过：agent 真实调用 `mcp__lanyuan__search_history` 5 次（连续换 query），正确总结「地暖从 25°C 调到 22°C」，tool_calls=5 tool_results=5；**实验 3 现可完整复现**。说明：npm 形态直接写完整 cordis.yml 加条目即可（等价 8-18 的 `- insert:` patch 语法，后者是 bundled exe 改默认配置的姿势）

**含义**：lanyuan 业务工具（Python，SQLAlchemy + JWT）包成 MCP server 即可接入 DSH，`@tool` 装饰器的 schema 生成可复用（MCP 参数也是 JSON Schema）。user_id 注入仍需桥层强制覆盖（防 LLM 伪造越权，方案设计时细化）。

## 实验 4：并发 + 崩溃重启

- **并发**：一个 runtime 子进程 3 个 session 并发 run（asyncio.to_thread），全部完成互不干扰（1.7s / 1.1s / 3.8s）
- **崩溃重启**：kill 子进程 → 下次调用抛 `TransportClosedError` → **`close()` + `start()`** 恢复（⚠️ 直接 `start()` 无效：`_initialized` 幂等保护会跳过重启）→ 新请求正常服务
- v2 包装层职责：catch TransportClosedError → close+start → 当前请求失败重试/提示

## 实验 5：打包体积与启动

| 项 | 大小 |
|---|---|
| bundled exe（linux-x64 单文件） | 197M |
| npm 版 node_modules（dsh + mcp-client） | 341M |
| Python venv（sdk + fastmcp） | 328M |

- runtime spawn + initialize：**0.30s**（常驻模式下仅 worker 启动时一次）
- 首个 run（含 LLM）：0.86s
- 微信云托管镜像预估：exe 方案 ~600M-800M，npm 方案 ~1.2G（具体限制待确认）

## v2 架构建议（spike 结论推导）

```
小程序 SSE → FastAPI（不变）
  ├─ /ai/chat: 认证 → MySQL 读历史组装 → 注入新 DSH session（uuid）
  │            → on_notification 透传 session.event → SSE（不做翻译，见实验 2 方向修正）
  │            → 回复写回 MySQL
  ├─ 业务工具桥: 每 worker 一个 Python MCP server 进程（复用 @tool schema）
  │             配置模板 = `spike/npm-dsh/cordis-mcp.yml`（实验 3 已验证）
  └─ DSH runtime: 每 worker 一个常驻子进程（lifespan 管理 + close/start 重启包装）
```

- runtime 载体：**先用 bundled exe 开发**（开箱即用），等 jsonrpc-agent npm 包发布后切 npm 形态（社区插件自由）
- 会话：MySQL 真源 + 每请求注入（见实验 2）
- 生命周期：`--workers 1` 起步（v1 本就每 worker 一份内存缓存）；多 worker 需 session 亲和性
- 前端：SSE **过滤透传 DSH 事件**（后端白名单只发前端关心的子集，事件格式原样；见 TECH_SPEC §4），前端 v2 消费白名单事件集；thinking 展示为产品决策（暂不展示）

## exe 定制评估（2026-08-21 补充）

可以定制：exe 的插件集 = `python/sdk-runtime/package.json` 依赖闭包（打包根），删/加依赖 + 重建 exe 即得定制版（官方机制：加一行依赖 + `scripts/build-exe-for-python-sdk.ts`）。但落地前评估：

**打包 ≠ 加载**：exe 内置插件是「可用池」，cordis.yml 决定运行时实际加载哪些——不配置的插件不占内存、不注册工具。运行时裁剪零成本，是优先手段。

| 层次 | 手段 | 效果 |
|---|---|---|
| 运行时裁剪（轻） | 自己的 cordis.yml 只配置需要的插件 | 行为/内存/工具面可控，零构建成本 |
| 打包裁剪（重） | fork sdk-runtime 清单，重建 exe | 文件体积变小（部署相关） |

**三个现实约束**：
1. 体积收益有限：197M 中 Node runtime 是固定大头（嵌入式 Node 约 100M 量级），插件 JS 只占一部分，全删预计省 30-50M——镜像层面差别不大，除非部署体积有硬红线
2. 维护成本高：官方 8 天 7 个 rc 的节奏，自建 exe = 每次上游升级重新 merge + pnpm build + 打包（fork 税）；服务器实测 `pnpm build` core dump，构建链有环境门槛
3. jsonrpc-agent npm 包发布后，插件自由走「npm install + cordis.yml + 裸插件机制」，不需要碰 exe

**何时值得自建 exe**：① 部署体积被微信云托管硬性卡死；② 私有 TS 插件需编译进 runtime 免子进程。其余情况 cordis.yml 裁剪 + MCP 桥 + npm 形态足够。

**v2 建议路径**：默认 cordis.yml 运行时裁剪 + MCP 工具桥；部署体积超限再自建最小 exe（Mac 可构建、CI 化）；需内置私有 TS 插件时在清单中加包或走 npm 裸插件机制。

## SDK 协议扩展方向：session 跨进程恢复（2026-08-22 补充）

**问题定位**：跨进程会话恢复不可用，既不是 Python SDK 的问题（忠实实现协议），也不是 DSH 核心的问题（核心完整支持 replay/fork/resume，`ctx.sessions.load`、`Session` log-seed 构造、coordinator adopt 机制都在）——缺口在 **JSON-RPC 协议层**（`dsh-sdk-jsonrpc-server` 只有 initialize/session/prompt/shutdown 三个方法，未暴露加载/恢复）。2026-08-23 解包官方 0.0.1-rc.5 确认缺口仍在：`handleRequest` 仍只 3 方法，`getOrCreateSession` 仍只查内存 sessions map，无 load 磁盘路径（官方已迭代到 rc.5 未补——"发现框架空白"叙事成立）。

**改进方向（设计定稿）**：不在协议加新方法，而是在现有 `session/prompt` 内部扩展：

```
getOrCreateSession → get-or-load-or-create：
  prompt(id) → 内存有？用内存的
               : 持久化有？load 成 live session（核心 log-seed 重放）
               : 都没有？新建
  → 注入消息 → 追加
```

**设计要点**：
1. 恢复是**服务端策略**而非客户端操作——`harness.run(prompt, session_id)` 语义不变，Python SDK 零改动，能力透明获得
2. 完全复用核心能力（log-seed 构造 Session），不发明新机制；collision 守卫保留为 load 失败的兜底（日志损坏等异常路径）
3. 三个边界：① 并发写需 owner 机制 + workers=1/session 亲和；② 无条件 load（id 即身份），sessionId 命名策略防意外复用；③ load 语义对「新对话 vs 恢复」的界定要写进文档

**对 lanyuan v2 的意义**：能力落地后，「每请求新 session + 历史注入」策略可退役——正常对话复用 session（省 token、日志即历史），重启后首请求自动恢复，hybrid 简化为零代码自动恢复。

**落地形态确认（2026-08-23）**：**不需要自己 build sdk-runtime**——npm 形态已就绪（`@deepseek-ai/dsh`、`dsh-sdk-jsonrpc-server`、`dsh-agent-spine-demo`、`dsh-sdk-jsonrpc-demo` 等全部为 **0.1.1-rc.2 统一发布批次**）；SDK `HarnessConfig.runtime_bin`/`launch_args_override` 支持指向任意 runtime（client.py:28-30），默认才走 bundled exe。落地 = 官方 npm 包原样 install + cordis.yml 把 sdk-jsonrpc-server 换成本地 `@lanyuan/dsh-server` 插件（file: 依赖，同 MySQL backend 套路）+ SDK 指向 npm runtime 入口。**无需 build 官方 exe/wheel**；唯一要 build 的是我们自己的插件（tsc → dist）与 npm install（部署步骤）。

**验证路径**：改本地 server 插件的 `getOrCreateSession` → get-or-load-or-create（内存有？用内存 → 持久化有？load 成 live session → 都没有？新建），复用核心 log-seed 重放；如需在官方源码验证才需 pnpm build（服务器 core dump，疑似内存不足，可加 `NODE_OPTIONS=--max-old-space-size=4096` 或 Mac 上构建）。

**launch 参数组合已验证（2026-08-23 spike 1d/1e）**：✅ 通过——SDK `runtime_bin` 指向 `node_modules/.bin/dsh-jsonrpc-agent`（`@deepseek-ai/dsh-sdk-jsonrpc-demo@0.1.1-rc.2` 的 bin），`env DSH_CORDIS_CONFIG` 指向 cordis.yml，SDK 成功拉起 npm runtime 进程并跑通真实对话（finish_reason=completed，108 events）。**版本硬性要求：必须用 0.1.1-rc.2 系列**（官方统一发布批次）——旧版 spine-demo rc.1 引用多个 npm 上不存在的包（dsh-skill-local/dsh-bash-env/dsh-goal-session 等，发布缺口）。**安装用 pnpm**（服务器 npm install 稳定崩溃疑似内存，pnpm 成功）；pnpm 严格模式要求 **cordis.yml 用到的每个插件显式声明在 package.json**（正是官方 sdk-runtime 打包清单 118 依赖的职责，lanyuan 落地照抄此模式）；pnpm 默认忽略原生模块 build scripts（node-pty/koffi 等，bash 能力受限，需 `pnpm approve-builds`，核心对话链路不受影响）。

**不做的（排除决策）**：fork 分支语义（从历史切点派生新会话，UI「分支对话」）——git 心智模型，不 human：人类对话只有两条路，要么继续同一会话（=get-or-load-or-create 恢复），要么带着理解开新话题（=摘要+新会话注入）。v2 明确不做，lanyuan-base 无此场景（2026-08-22 定）。

**session-projection（多视图状态投影）**：lanyuan 是单前端 + 事件流直连，前端从事件流本地维护状态即可，无第二视图 → **暂无使用场景，v2 不引入**（2026-08-23 定）。补充边界：投影是整值 last-wins 语义（title/todos/goal），表达不了流式序列（token 逐字）——所以它既不替代事件透传、也不做「翻译层」；未来若出现独立视图（工作台页展示 agent 状态、多端共享）再启用（DSH 内建，cordis.yml 加插件 + 注册投影单元即可）。

## MySQL PersistenceBackend 落地清单（2026-08-22 补充）

**结论**：不用改 deepseek-harness repo、不用重建运行时、不用 npm publish。DSH 持久化是两层架构——`PersistenceCoordinator`（编排层：write-behind 缓冲/游标/adoption/崩溃修复排序，完全复用）+ `PersistenceBackend<TornMarker>`（物理层 8 hook，coordinator.ts:127 明说 "over files, rows, an object store, …"）。只需照官方 `SqliteStore`（store.ts:56）写一个 MySQL store。

**代码位置**：`lanyuan-base/dsh/` 一体化家目录（与 backend/、miniprogram/ 平级，Node 侧全在此子树，删除即卸载 DSH 集成）：

```
lanyuan-base/
├── backend/                        # Python FastAPI（不动）
├── miniprogram/                    # 微信小程序（不动）
└── dsh/                            # DSH 运行时（完整 npm 项目）
    ├── package.json                # @deepseek-ai/dsh + file: 本地插件依赖
    ├── cordis-lanyuan.yml          # 运行时配置（MySQL backend 插件 + 禁用默认持久化）
    ├── node_modules/               # npm install 产物（部署时生成）
    └── mysql-persistence/          # 插件包（TS → dist/）
        ├── src/index.ts            # MySqlSessionPersistence extends SessionPersistence
        └── src/store.ts            # MySqlStore implements PersistenceBackend<number>
```

**表结构**（照官方 SQLite DDL 映射，schema.sql）：`sessions(id, version, created_at, cwd, parent_session, seed_length, origin, delegation_depth, agent_preset, incarnation, revision)` + `events(session_id, seq, type, time, data, source_event_seqs, surface_op, ignorable, PK(session_id, seq), FK → sessions)` + `persistence_state(singleton, store_id)`。data/source_event_seqs 用 JSON 列。

**8 hook 实现要点**（照 SqliteStore 翻译）：

| hook | MySQL 要点 |
|---|---|
| `loadStored(id)` | 读 sessions + events 行 → `StoredPrefix{meta, events, revision, tornMarker}` |
| `readStoredRevision(id)` | 只 SELECT revision/incarnation |
| `loadStoredFrom?(id, fromSeq)` | 应实现（`WHERE seq >= ?`，按 seq 寻址天然适合） |
| `appendBatch(meta, events, isMaterialized)` | InnoDB 事务：`!isMaterialized` 时 materialize+首批**原子提交**；`SELECT ... FOR UPDATE` 锁行读 tail → 校验 `first.seq === next seq` → 批量 INSERT → revision+1 |
| `commitRepair(meta, tornMarker, closers)` | tornMarker 则 DELETE seq>= 截断；closers 校验续接后 INSERT；revision+1 |
| `list()` | SELECT sessions → headers |
| `locate?()` | undefined（无每会话独立 artifact，同 SQLite） |
| `close?()` | 关连接池 |

revision = `` `${storeIdentity}:incarnation:${incarnation}:revision:${revision}` ``（storeIdentity 换 `mysql:${host}:${db}:store:${store_id}`）；TornMarker 用 number（MySQL 事务原子提交，torn 场景实际罕见）。**不做 chunk 打包 codec**（SQLite schema-17 的 packChunkRuns 是物理层优化，MySQL 行数非瓶颈，省掉可简化 torn 语义）。

**加载机制（不 publish 的依据）**：`cordis-plugin-loader` 的 `import()`（lib/index.js:259）——`.` 开头=相对路径导入（`new URL(name, baseUrl)`），否则=npm 包名解析。用 **file: 本地依赖 + 包名** 而非相对路径：`package.json` 声明 `"@lanyuan/dsh-session-persistence-mysql": "file:./mysql-persistence"`，npm install 装进 `dsh/node_modules`（@deepseek-ai/* 复用运行时版本，mysql2 自动装），cordis.yml 写包名。物理行为等价 publish 后安装，但零发布、代码私有。

**两个前提**：① 禁用默认持久化后端——`ctx.sessionPersistence` 单实例互斥，cordis.yml 把默认 sqlite persistence 插件标 `disabled: true` 换成本插件；② 运行时**不重建**——npm 形态 runtime 就是 `@deepseek-ai/dsh` npm 包，`npm install` 是部署步骤不是打包步骤；exe 形态才需重建（内置插件集编译时固定），v2 用 npm 形态与其无关。

**三条路线**：A. 私有本地（推荐起步，零成本落地）；B. 私有 publish（GitHub Packages，多项目复用才需要）；C. 公有贡献——发 npm 或给 deepseek-harness 提 PR 合入官方（第三个 backend：官方只有 JSONL/SQLite 两个本地后端，无网络数据库后端=框架空白），面试叙事价值，代码实战验证后再走。

**影响面排查（其他插件零代码修改）**：默认 cordis.yml 实际用 **JSONL**（`root: $DSH_SESSION_ROOT ?? './.sessions'`），SQLite 只是可选后端——切换是「jsonl → mysql」。排查结论：
- **接口消费者（20+ 包）透明**：core/agent、agent-loop、tools、subagent、schedule、feedback、hooks、workspace、apiproxy 等全走 `ctx.sessionPersistence` 接口，backend 换介质无感知
- **独立 SQLite 使用者不改**：`session-query-sqlite`（搜索索引）与 `storage-sqlite`（DSH 内部 KV）都是**派生/无关存储**，不是 session log 真源，保留或禁用均可
- **测试确认项（非修改）**：session-checkpoints 的 durable 语义（append 返回即落库，事务提交后 resolve）、session-projection-cache（JSON 文件缓存，可丢弃重建）、apiproxy session-export（`supportsRawArtifacts=false` 路径，照 SQLite）

**查询层组合：MySQL 真源 + SQLite FTS5 搜索投影（2026-08-22 定）**：持久化介质（真源）与查询介质（投影）是**正交维度**，官方本就任意组合（JSONL/SQLite × FTS5）。推荐组合：persistence=MySQL（只写+恢复）、session-query=SQLite FTS5（搜索投影，本地文件）：

```
MySQL（真源）→ inspect(id) 接口（解耦点）→ session-query-sqlite 建 FTS5 索引（派生投影）
```

- session-query-sqlite 唯一读入口是 `persistence.inspect(id)`（index.ts:508），从不直接碰文件——切 MySQL 后自动索引 MySQL 数据，零修改
- **索引维护机制（自动，零代码）**：不是 seq 水印增量，而是 **revision 驱动的会话粒度对账**（`_reconcile` index.ts:395）——本地 `persisted_sessions` 表记录每个会话已索引的 revision，与 `readStoredRevision`（轻量读，不读日志）比对；revision 变了才 inspect 整个会话重建该会话的 FTS 文档，消失的会话删条目，全部变化一个事务提交 + `global_generation` 递增
- **部署场景行为（2026-08-22 定）**：微信云托管容器文件系统非持久化 → **每次重新部署索引文件丢失 → 启动对账自动全量重建**（从 MySQL inspect 重建，耗时∝历史量）；切 MySQL 首次启动也全量重建一次（storeIdentity 变了 → 所有 revision 不同）。**数据零风险**（MySQL 真源不动，丢的只是可重建投影），**接受自动重建，不引入持久化卷**——历史量真到重建太慢再挂卷（revision 对账自然变增量，部署调优非架构改动）
- 三个边界：① 最终一致（异步/批量对账，刚写完的会话可能延迟可见——v1 search_history 是实时 LIKE，延迟是产品决策）；② 多实例各持一份索引副本（workers=1 规避）；③ 重建耗时随历史量线性（社区场景可接受，需实测）
- 价值：**搜索从 MySQL 里解放**——v1 search_history 卡在 MySQL FTS（#42 未落地：中文分词/相关性/索引维护），v2 搜索不碰 MySQL，直接复用官方成熟的 FTS5 实现（高亮/分页/中文处理已踩平）

## 风险与待确认

1. ~~jsonrpc-agent npm 包未发布~~（已过时：0.1.1-rc.2 统一发布，npm 形态已就绪且 launch 参数组合已验证 2026-08-23）→ 新风险：pnpm 忽略原生模块 build scripts（node-pty 等，bash 能力需 `pnpm approve-builds`）
2. 微信云托管镜像大小限制（spike 无 docker，未实测构建）
3. MCP 工具桥的 user_id 注入设计（安全细节）
4. 多 worker 下 DSH 子进程 × worker 数（内存放大，v2 初版 workers=1 规避）
5. reasoning 流（thinking）是否透传前端：v1 不回传展示，v2 可作产品增量

## 复现方式

所有脚本在 `spike/` 目录（worktree `feat/82-dsh-spike`），依赖 `spike/.venv`（deepseek-harness-sdk 0.1.0rc7 + fastmcp）与 `spike/npm-dsh`（@deepseek-ai/dsh rc.7 + dsh-mcp-client rc.7）。运行前需 `unset DSH_SESSION_ROOT DSH_HOME DSH_CWD`（避免环境残留干扰）并 export DEEPSEEK_API_KEY。
