# DSH Session Surface 与事件溯源：事实源 vs 多投影

> 理解 DSH 会话架构的核心心智模型。参考：`packages/core/session/src/surface.ts`、Agent Note `2026-06-18-session-surface.zh.md`。
> 整理日期：2026-08-22

## 一、surface 是什么

> **Surface layer on top of the session event log: an ordered view of events that produce LLM messages. The append-only log remains the source of truth.**（surface.ts 原文）

**surface = 「模型该看到什么」的派生层**。日志记录一切，surface 只投影出模型上下文需要的部分。

只有 3 类事件「产生模型消息」（`deriveEventMessage` 的投影规则）：

| 事件类型 | 投影 | 说明 |
|---|---|---|
| `user/message` | ✅ user 消息 | 用户输入 |
| `assistant/message` | ✅ assistant 消息 | AI 回复（空 content 跳过——只承载 usage 的不算） |
| `tool/result` | ✅ 工具结果 | 模型要看到工具返回 |
| chunk / turn/* / step/* / request/header / usage | ❌ null | trace/replay 数据，模型不需要 |

## 二、为什么要有 surface（决策动机）

surface 之前，历史操纵（如 context compaction）通过**顺序敏感的监听器改写派生请求**——脆弱、不记录替换用了哪些事件、每种新操纵都要改核心 `deriveMessages()`。

surface 把「历史操纵」从临时改写变成**日志里的一等公民**：`surfaceOp` 字段（`'append'` 或 `{op:'replace', start, end}`）+ `sourceEventSeqs`（引用来源事件）。

**Replace 的典型场景（compaction）**：

```
日志（append-only，事实不变）：
  seq1-4   用户/AI 多轮对话（surfaceOp: append）
  seq50     compaction/start                       ← 日志超限触发压缩
  seq51     user/message "【摘要】…" 
            surfaceOp: {op:'replace', start:1, end:4}   ← 视图上把 seq1-4 换成摘要
  seq52     assistant/message "继续吧"（append）
```

折叠后模型看到 **[摘要, seq52…]**——旧消息从**视图**消失，日志里原封不动。**压缩改的是视图，不是数据**。

### 曾考虑的替代方案（决策记录）

| 方案 | 否决原因 |
|---|---|
| 逐插件 `agent/request` 包装（旧模式） | 监听器排序脆弱、无法持久记录、每种操纵都要改核心 |
| 半开区间 `[start, endExclusive)` | 单条目替换（`start===end`）在闭区间语义下更自然 |
| 链接节点对象 + seq map | 生产代码不读前驱链接，单数组足够；表示越少越好校验 |
| 脏标记后全量重建 | 会话生命周期 O(N²)，每次追加重扫全部历史 |

### 不变式（Session 始终启用的接纳边界校验）

- `sourceEventSeqs` 必须唯一、更早、已知（只有 `assistant/message` 可用空列表）
- replace 端点必须存在于当前 surface；被遮蔽节点必须被 sourceEventSeqs 全覆盖
- `tool/result` 替换只能改写自身且保留 content 外的字段
- 可进入 surface 的事件必须带 `surfaceOp`，否则从派生历史中消失

## 三、心智模型：一个日志，多个投影

**事实源（event store）与视图（projection）分离**——MVC 类比的两个微调：

```
                 ┌─ surface（折叠出模型上下文）──→ 给 LLM 看（模型视图）
事件日志（真相）──┼─ UI 回放 / /messages API ────→ 给用户看（用户视图）
                 └─ telemetry / 审计重放 ────────→ 给开发者看（诊断视图）
```

**微调 1：一个日志，多个投影**。UI 历史不是单独维护的数据，是同一事实源的另一个投影。投影可以按消费方裁剪：surface 只投影消息级事件；UI 投影决定工具调用细节给不给前端；诊断投影保留全部。

**微调 2：事件溯源里没有「存储的 model」**。日志是唯一真相，任何「当前状态」都是投影（压缩后的历史 = 日志 + replace 的折叠结果）。所以 `foldSurface` 要纯函数化、`SurfaceManager` 要增量 O(新事件数)——当前状态不是读出来的，是算出来的。

```
日志 = 事实（发生了什么，永不变）
surface = 模型视图（模型该看到什么，可被压缩/改写）
UI 历史 = 用户视图（用户该看到什么）
```

「日志即真源」的收益（可审计、可重放、可重建任何历史状态）与代价（所有视图都要折叠计算）源于同一个设计——**真相只有一个，看法可以有很多**。

## 四、对 lanyuan v2 的意义

```
v2 如果 SessionEvent 日志落 MySQL：
  ├─ surface 投影 → 模型上下文（DSH 内部已做）
  ├─ UI 投影     → 前端 /messages 历史、search_history 工具（lanyuan 自己做）
  └─ 事实源      → 日志，可重放重建以上任何投影
```

- v1 对照：v1 的「model history 组装」是手写 surface（无 replace 语义）；v1 压缩（L1-L4）改消息内容，DSH 改 surface 节点——**v1 改数据，DSH 改视图**
- 若日志为真源，conversations/messages 从「数据」降级为「投影」——可丢弃重建，但投影器要可靠（这是责任）
- 恢复限制：跨进程会话恢复仍受 JSON-RPC 协议限制（见 `spike.md`「SDK 协议扩展方向」）
