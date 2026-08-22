# DSH 中的 GoF 设计模式应用

> 按 GoF《设计模式》（Erich Gamma et al.，1994）的 23 个条目，逐一对应 DeepSeek Harness 源码中的具体应用。
> 证据以「仓库路径 / 文档路径」标注，可自行验证。部分模式无直接对应（DSH 的架构刻意规避某些模式的场景），如实标注。
> 整理日期：2026-08-22

## 背景

DSH 是构建在 Cordis（vendored）之上的插件化 agent harness。**一切皆插件**：agent loop、LLM adapter、工具、会话、压缩都是插件，通过 cordis.yml 组合成运行时。理解它的设计模式，等于理解「可组合的 agent 运行时」怎么写。

---

## 一、创建型模式（Creational）

| 模式 | DSH 中的应用 | 证据位置 |
|---|---|---|
| **Abstract Factory 抽象工厂** | seam 的 Provider 注册表：`ctx.llm.listProviders()`、`ctx.web` 的 searchProvider 选择——按配置返回不同 Provider 实现族 | `packages/llm/llm/src/`、`packages/web/` |
| **Builder 建造者** | ① cordis.yml 声明式组合插件树（配置即构建蓝图）② `defineTool()` 的 schema DSL 逐步构建 ToolDefinition | `docs/cordis-primer.md`、`packages/core/tools/src/schema.ts` |
| **Factory Method 工厂方法** | `ctx.agents.create()`、`createUserMessage()`、server 的 `getOrCreateSession()`、jobs 的 abstract registry（子类实现抽象方法即注册） | `packages/core/agent/`、`packages/sdk/server/src/server.ts`、`packages/jobs/jobs/src/index.ts` |
| **Prototype 原型** | 未发现直接对应。bundle/patch 层是「配置覆盖」而非原型复制 | — |
| **Singleton 单例** | **刻意规避**：Cordis 支持多 context 多实例、HMR 热卸载（注册即效果、卸载即回滚）。ctx 服务是 **per-context 单例**（每个 context 一份，非全局） | `docs/architecture.md`、Cordis primer |

## 二、结构型模式（Structural）

| 模式 | DSH 中的应用 | 证据位置 |
|---|---|---|
| **Adapter 适配器** | seam 的 Provider 实现：`dsh-llm-deepseek` / `dsh-llm-pi-ai` 适配同一 `ctx.llm`；`web-search-deepseek` 适配 `ctx.web`；session-persistence 的 JSONL / SQLite 两个 backend | `packages/llm/`、`packages/web/web-search-deepseek/`、`packages/session/session-persistence/` |
| **Bridge 桥接** | **seam 三件套**（Service Definition / Provider / Consumer）：抽象接口与具体实现彻底分离，换 Provider 不改 Consumer；llm 的 canonical ↔ wire 协议转换只在边界 | `docs/capability-seams.md`、`docs/architecture.md` |
| **Composite 组合** | ① profile → bundle → patch 三层配置树（叶子可被上层覆盖）② `cordis-plugin-group` / `cordis-plugin-include` 插件嵌套（插件可含子插件） | `docs/architecture.md`、`packages/boot/app-boot/` |
| **Decorator 装饰器** | `dsh-tool-call-timeout-policy`（包装 `tools/execute` 加超时）、`dsh-output-retention`、`dsh-repeat-tool-reminder`、`dsh-llm-retry`（包装 `llm/stream`）——不修改原实现，在管线外层叠加横切能力 | `packages/guard/`、`packages/llm/llm-retry/` |
| **Facade 外观** | `DeepSeekHarness`（Python SDK 门面：隐藏 HarnessClient + 子进程 + 协议细节）；`dsh` CLI；`@deepseek-ai/dsh` 包 | `python/sdk/src/deepseek_harness/api.py` |
| **Flyweight 享元** | 未发现直接对应（事件/消息对象随用随建，无共享池） | — |
| **Proxy 代理** | `dsh-mcp-client`（外部 MCP server 工具的本地代理）；subagent spawn provider（child agent 的本地委托）；e2b sandbox（远程沙箱的执行代理） | `packages/mcp/mcp-client/`、`packages/subagent/`、`packages/e2b/` |

## 三、行为型模式（Behavioral）

| 模式 | DSH 中的应用 | 证据位置 |
|---|---|---|
| **Chain of Responsibility 责任链** | **waterfall 事件**：`agent/pre-step`、`agent/request`、`llm/stream`、`tools/pre-execute/execute/post-execute`——listener 必须调 `next()` 委托给下一个，可拦截/改写/拒绝 | `docs/architecture.md`（turn flow） |
| **Command 命令** | **SessionEvent 追加式日志**（append-only 事件流 = 命令日志/事件溯源）；`jobs` 后台任务工具；`todo_write` | `docs/architecture.md`、`packages/jobs/` |
| **Interpreter 解释器** | `cordis-plugin-include` 解析 cordis.yml 中的 `!!js` 表达式为表达式节点，Loader 在插件激活时求值 | `docs/cordis-primer.md`、`@deepseek-ai/cordis-plugin-include` |
| **Iterator 迭代器** | `deriveMessages()` / `foldSurface()` / `foldRequestHeader()`——对 session 事件日志的折叠投影；事件流的游标式读取 | `packages/core/session/src/index.ts` |
| **Mediator 中介者** | **Cordis Context（ctx）**：插件之间不直接通信，通过 ctx 服务注册表 + 事件总线中介——插件零互相引用，可独立装卸 | Cordis primer、`docs/architecture.md` |
| **Memento 备忘录** | `dsh-session-checkpoint-policy`（request / tool-dispatch / completed-step 三档持久化检查点）；compaction 的 `compaction/start|summary|end` 事件是压缩操作的持久化痕迹 | `packages/session/session-checkpoint-policy/`、`packages/compaction/` |
| **Observer 观察者** | 事件系统主体：`ctx.on()` 订阅、`session/event` 广播（持久事件）、`agent/*`、`tools/*`（实时事件） | `docs/architecture.md`（Events 节） |
| **State 状态** | `session.status`（`'running' | 'idle'`，协议 notification）；turn/step 生命周期；mcp-client 的 reconnect 状态机（reconnecting/recovered/disabled） | `packages/sdk/protocol/src/types.ts`、`packages/mcp/mcp-client/README.md` |
| **Strategy 策略** | compaction 策略（thresholdRatio/retainRatio）、checkpoint 策略、`dsh-llm-retry` 重试策略、mcp reconnect 退避策略、session-title 生成器（`ctx.sessionTitle`，唯一 Provider 可替换） | `packages/compaction/`、`packages/session/`、`packages/llm/llm-retry/` |
| **Template Method 模板方法** | **turn flow**（agent loop 的步骤骨架：turn/start → pre-step → step/start → llm/stream → tool/* → step/end → turn/end，事件作为钩子）；工具执行管线（pre-execute → execute → post-execute） | `docs/architecture.md`（Turn flow） |
| **Visitor 访问者** | 未发现直接对应（schema DSL 编译接近但非严格 Visitor） | — |

---

## 四个最典型的模式（重点展开）

### 1. 责任链（waterfall）—— 控制流可插拔的关键

DSH 的事件分两类，语义不同：

```
广播事件（session/event、capability 事件）：观察者——通知所有订阅者，旁路观察
waterfall 事件（agent/*、tools/*、llm/stream）：责任链——请求经过链上节点，
   每个 listener 可改写 / 拦截 / 调用 next() 放行，链末由框架执行
```

v1 自研 harness 里「改 system prompt / 拦截请求」写死在 agent loop 中；DSH 把它们做成可插拔的链节点——**把控制流变成可组合的数据**（`agent/pre-step` 决定模型看到什么，可重写消息或直接拒绝）。

### 2. 中介者（ctx）—— 插件架构的地基

Cordis Context 是典型 Mediator：插件声明 `inject` 依赖，ctx 注入服务；插件之间通过 `ctx.xxx` 服务与事件交互，**从不互相 import**。这带来 HMR 与卸载安全：注册即效果（`ctx.effect()`），卸载即回滚——没有中介者，插件化无法成立。

### 3. 观察者 + 责任链的组合 —— 事件域的划分

DSH 将事件按「职责」划分三域：持久 session 事件（观察者，可重放）、实时 agent 事件（责任链，可干预）、能力事件（策略挂载点）。同一事件机制，因语义不同选用不同模式——这是「模式服务于语义」的教科书案例。

### 4. 适配器/桥接（seam）—— 接口即可替换性

seam 三件套 = Bridge（抽象与实现分离）+ Adapter（具体 Provider 实现）+ Factory（Provider 注册表）。换一家 LLM 厂商 = cordis.yml 换 Provider 名，Consumer（工具）零改动。DSH 的「配置一下就行」不是魔法，是接口在运行时被解析。

---

## 总结：模式之上，还有三条机制

设计模式是词汇表；DSH 真正的架构语法是三条机制，它们决定了模式如何被组织：

1. **一切皆插件**——连 agent loop 本身都是插件（没有特权核心）
2. **注册即效果**——`ctx.effect()` 注册、卸载即回滚（可组合可撤销）
3. **事件与接缝是扩展点**——新行为挂到已文档化的位置，不碰核心

> 对应地，DSH 工程规范（AGENTS.md）要求「每个抽象必须有真实消费者」——**面向接口是手段，可替换性是目的，极简是刹车**。
