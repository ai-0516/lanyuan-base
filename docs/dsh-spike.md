# DSH（DeepSeek Harness）集成 Spike 结论

> Issue: #82 · 日期：2026-08-18 · 状态：待用户审阅（未建 PR）
> 全部实验在 `spike/` 目录，使用 DEEPSEEK_API_KEY 真实调用（deepseek-v4-flash）

## 结论速览

**形态二可行**：Python FastAPI 后端保持 + DSH runtime 子进程（Node），5 项实验全部通过。v2 可进入 TECH_SPEC 设计阶段。

| # | 实验 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | DSH runtime 跑通 | ✅ | SDK exe / npm CLI / 自定义 cordis.yml 三链路全部跑通 |
| 2 | SSE 流式转发 | ✅ | `text-delta` 事件逐 token，首 token ~1.2s；**跨进程会话恢复受限（重要发现）** |
| 3 | MCP 工具桥 | ✅ | Python MCP server → dsh-mcp-client → agent 真实调用业务工具 |
| 4 | 并发 + 崩溃重启 | ✅ | 3 session 并发互不干扰；kill 后 close()+start() 恢复服务 |
| 5 | 打包体积/启动 | ✅ | exe 197M / npm 341M / spawn 0.30s |

## 实验 1：runtime 跑通（npm 版）

三条链路均实测成功：

1. **SDK + bundled exe**（零配置）：`harness.run()` → `finish=completed`，93 个事件
2. **npm 版 CLI**：`npm install @deepseek-ai/dsh@0.1.0-rc.7`，`dsh --profile headless "任务"` 真实调用 DeepSeek API；内置 web search 工具被 agent 真实调用（搜索 V4 Pro 新闻并返回带引用来源的总结）
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

翻译层（已验证端到端）：`text-delta` → `event: token\ndata: {"content": <text>}`，`turn/end` → `event: done`。首 token 延迟 1.2-1.3s（与 v1 相当），多轮同一 session 正常（turn 递增）。

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
  │            → on_notification 翻译 SSE（text-delta→token, turn/end→done）
  │            → 回复写回 MySQL
  ├─ 业务工具桥: 每 worker 一个 Python MCP server 进程（复用 @tool schema）
  └─ DSH runtime: 每 worker 一个常驻子进程（lifespan 管理 + close/start 重启包装）
```

- runtime 载体：**先用 bundled exe 开发**（开箱即用），等 jsonrpc-agent npm 包发布后切 npm 形态（社区插件自由）
- 会话：MySQL 真源 + 每请求注入（见实验 2）
- 生命周期：`--workers 1` 起步（v1 本就每 worker 一份内存缓存）；多 worker 需 session 亲和性
- 前端：SSE 契约不变，**零改动**；thinking（reasoning-delta）是否展示为产品决策

## 风险与待确认

1. jsonrpc-agent npm 包未发布（影响 npm 形态时机；发布节奏 8 天 7 个 rc，预期很快）
2. 微信云托管镜像大小限制（spike 无 docker，未实测构建）
3. MCP 工具桥的 user_id 注入设计（安全细节）
4. 多 worker 下 DSH 子进程 × worker 数（内存放大，v2 初版 workers=1 规避）
5. reasoning 流（thinking）是否透传前端：v1 不回传展示，v2 可作产品增量

## 复现方式

所有脚本在 `spike/` 目录（worktree `feat/82-dsh-spike`），依赖 `spike/.venv`（deepseek-harness-sdk 0.1.0rc7 + fastmcp）与 `spike/npm-dsh`（@deepseek-ai/dsh rc.7 + dsh-mcp-client rc.7）。运行前需 `unset DSH_SESSION_ROOT DSH_HOME DSH_CWD`（避免环境残留干扰）并 export DEEPSEEK_API_KEY。
