# AI Harness 思考笔记

> **用途**: 实现 AI tab 过程中的讨论草稿，记录认知演进  
> **不是**: 技术参考文档（那是 TECH_SPEC 的事）

---

## 1. 初始问题

通过实现这个 AI tab，我想理解的核心问题：

**什么是 AI Harness？**

不是调 API 发 prompt —— 那太浅了。Harness 是 AI 能力和真实世界之间的"接口层"：

```
用户输入 → [Harness] → LLM → [Harness] → 工具/数据 → 用户输出
```

它的职责包括：
- 对话管理（session、历史、上下文窗口）
- 工具调用（function calling → 执行 → 结果回填）
- 流式传输（SSE、打字机效果）
- 权限/隔离（用户 A 不能用用户 B 的会话）
- 优雅降级（API 不可用时的模拟模式）
- 持久化（消息入库、会话恢复）

---

## 2. 两条参考线

### learn-claude-code（教学路线）

源码路径：`/home/ubuntu/workspace/learn-claude-code/`（只读）

https://github.com/shareAI-lab/learn-claude-code

从零到完整 coding agent 的 20 个步骤，每个步骤一个独立文件：

| 步骤 | 核心概念 | 一句话 |
|------|---------|--------|
| s01 | Agent Loop | `while True` + 工具调用 → 结果回填 → 继续 |
| s02 | Tool Use | 从 1 个工具扩展到 N 个 + 分发映射表 |
| s03 | Permission | 危险操作需要用户确认 |
| s04 | Hooks | 工具执行前后的拦截点 |
| s05 | Todo Write | 任务规划与追踪 |
| s06 | Subagent | 子代理委派 |
| s07 | Skill Loading | 经验复用 |
| s08 | Context Compact | 上下文窗口撑爆时的压缩 |
| s09 | Memory | 持久记忆 |
| s10 | System Prompt | 系统提示工程 |
| s11 | Error Recovery | 容错与重试 |
| s12 | Task System | DAG 任务编排 |
| s13 | Background Tasks | 后台任务 |
| s14 | Cron Scheduler | 定时任务 |
| s15 | Agent Teams | 多 Agent 协作 |
| s16 | Team Protocols | 团队通信协议 |
| s17 | Autonomous Agents | 自主运行模式 |
| s18 | Worktree Isolation | 工作目录隔离 |
| s19 | MCP Plugin | 外部工具集成 |
| s20 | Comprehensive | 完整系统 |

核心认知：**s01 的 30 行 `while True` 就是整个代码助手的内核。** 后面所有步骤都是在这个循环上叠加的保护和扩展机制。模型负责"要不要调工具、调哪个"，harness 负责"调了就跑、结果喂回去"。

### Hermes Agent（生产参考）

Hermes Agent 不是教学项目，它是在这个循环上叠加了完整的保护系统：
- `agent/agent_runtime_helpers.py` — Agent 运行时基础设施
- `agent/agent_init.py` — 初始化和配置加载
- `run_agent.py` — 主入口

关键差异（参考 learn-claude-code s01 README 的"深入 CC 源码"一节）：
- 教学版看 `stop_reason == "tool_use"`；生产版看内容里有没有 `tool_use` 块（流式响应中 stop_reason 不可靠）
- 教学版用 `messages` 一个数组；生产版用 `State` 对象 10 个字段追踪各种状态
- 教学版 1 条退出路径；生产版多条退出/恢复路径（blocking limit、prompt too long、abort、hook stop、max turns...）
- 教学版顺序执行工具；生产版可并行（根据工具 concurrency-safe 标记）

---

## 3. 差异：coding agent vs 社区 chatbot

learn-claude-code 是 **coding agent** — 它的工具是 bash/read/write/edit，目标是写代码。

我们的场景是 **社区 AI 助手** — 工具是查询暖气费、查帖子、通知等，目标是回答小区生活问题。

共同点（harness 消除的差异）：

| 维度 | Coding Agent | 社区 Chatbot |
|------|-------------|-------------|
| 对话 | 单轮任务为主 | 多轮持久对话 |
| 工具 | 文件系统操作 | 数据库/API 查询 |
| 上下文 | 整个项目文件 | 最近 N 条对话 |
| 输出 | 代码 + 文件改动 | 自然语言 + SSE 流式 |
| 安全 | 不能 rm -rf / | 不能查别人隐私数据 |
| 退路 | 人工修复 | 模拟回复 |

**harness 要做的是：把"从 LLM 到工具"这个模式抽象出来，不管工具背后是 bash 还是 SQL。**

---

## 4. 当前项目的 Harness 现状

目前 AI tab 的 `ai_service.py`（~186 行）已经包含了：

```
POST /ai/session  → 查找/创建会话 + 返回历史 20 条
POST /ai/chat     → 存用户消息 → 调 DeepSeek (SSE) → 逐 token 返回 → 存回复
```

但它把"Agent Loop"（调 LLM）、"Tool Dispatch"（尚无）、"Context Management"（硬编码 20 条）、"Session Management" 全部混在一个文件里。

如果按 learn-claude-code 的模块化思路拆，可以拆成：

```
harness/
├── session.py      # 会话创建/查找/复用（已实现，可提取）
├── context.py      # 上下文窗口管理（目前硬编码 20 条）
├── loop.py         # Agent Loop（while True + stop condition）
├── tool_use.py     # 工具定义 + 分发映射（目前无工具）
├── streaming.py    # SSE 转发（已实现，在 ai_service.py 混着）
└── memory.py       # 消息持久化（已实现，在 ai_service.py 混着）
```

---

## 5. 讨论计划

你想按 learn-claude-code 的节奏一步步搭，每个模块讨论清楚再写代码。

从当前 `ai_service.py` 的 186 行出发，可以按这个顺序演进：

1. **抽取模块边界** — 把 session / loop / streaming / context / memory 拆开
2. **Agent Loop** — 先不加工具，只做纯对话：`while True`（发消息 → DeepSeek 回复 → 是否调工具？→ 是则执行 → 继续；否则结束）
3. **Tool Use** — 定义第一个工具（比如 `get_community_info`），实现 `TOOL_HANDLERS` 分发
4. **Tool Result** — 把工具结果回填到 DeepSeek，让模型基于结果生成自然语言回复
5. **Permission** — 敏感操作需要用户确认（比如查别人隐私数据）
6. 依次类推...

---

## 讨论区

### 2026-07-25: Context 压缩策略

**问题**：对话长了以后需要压缩上下文，压缩后的消息怎么维护？

**参考 Hermes Agent 的做法**（`conversation_compression.py`）：
- 压缩前：session_id = "abc"，messages 全在 DB
- 压缩中：调辅助模型生成摘要，创建新 session（"def"）
- 压缩后：session_id = "def"，messages = [摘要, tail]
- 旧 session "abc" 保留原始消息，可追溯

**对我们的启示**：
- 新建 session 比 inject 摘要消息更合理
- 旧 session 作为原始记录保留，方便 debug/审计/RAG
- AIAgent 轻量（只持有 user_id + session_id），不缓存任何消息内容
- 所有状态在 MySQL，压缩只是"换一个 session_id 指针"

### 2026-07-25: AI 不调 create_post 工具（待修复）

**症状**：
用户让 AI 发帖，AI 回复"帖子已发布"但 DB 中没有记录。从日志确认 `finish_reason=stop`（非 `tool_calls`），LLM 编了回复文字但没有触发工具调用。

**尝试过的方案（无效）**：
1. 强化 system prompt：强调"必须调用工具，不能只回复文字"
2. 修复 `get_recent_messages`：原来是取最早 20 条，现在是取全部消息

**怀疑方向**：
- System Prompt 可能被上下文中的历史消息"稀释"了——用户已经跟 AI 聊了 21 轮，历史中 AI 曾经回复过"已发布"，模型倾向于模仿历史行为
- DeepSeek 的 system prompt 权重可能不够高，历史 user/assistant 消息对模型行为的影响更大
- 可以尝试：在用户消息末尾注入更直接的指令，或者在 Agent Loop 层面加"工具调用保证"逻辑

**下一步**：
- 检查 messages 数组中 System Prompt 是否仍在第一位置
- 考虑在用户消息后面追加一条 system 风格的消息强调工具调用
- 或考虑 Agent Loop 层面做 fallback：如果 LLM 纯文本回复但用户意图明显需要工具，重试
