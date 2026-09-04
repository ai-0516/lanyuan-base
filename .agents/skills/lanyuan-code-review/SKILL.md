---
name: lanyuan-code-review
description: Use when reviewing a PR in the lanyuan-base repo — 六维度审查 + 实际触发验证 + 报告模板。审查依据与硬阻塞项见正文。
---

# lanyuan-base PR 审查流程

## Sources of truth（审查依据，以这些为准）

- `AGENTS.md`（仓库铁律：merge 信号、worktree、doc 随 PR）
- 关联 Issue / PRD / TECH_SPEC（`docs/`）
- `docs/design/`（前端设计稿）
- 专项文档（`docs/evals.md` 等）
- 不明确处 → **PR 上提 comment**，不私下猜（保持透明）

## Blocking requirements（硬阻塞项，缺一不可）

1. 新增代码必须带对应测试（不审测试质量、不跑 UT——QA 负责，但确认存在且与改动相关）
2. 文档/协议表/验证脚本与代码同步：`docs/`、`scripts/review/` 断言、`tests/` 断言三处引用点一处不漏
3. 业务正确性必须**实际触发验证**（B 维度），不是只读代码
4. 前端 UI 匹配 `docs/design/`，交互状态齐全（E 维度）

## 审查前置：理解上下文

- 读关联 Issue（或 PRD、TECH_SPEC、design），确认需求本身合理
- **多轮 review 上下文延续**（同一 PR 第二次及以后）：
  1. 先读 PR 完整历史（全部评论 / reviews / issue / commit）——GitHub 是共享 context 源
  2. `session_search` 搜「PR #N」找回上次分析的 session（若可访问）
- 对实现方案有疑义，要求 Dev 在 PR 中解释清楚

## 六维度审查（按实际情况跳过无关维度）

| 维度 | 内容 | 方式 |
|------|------|------|
| A 安全 | 硬编码密钥、SQL注入、XSS、认证缺失、路径遍历 | 自动扫描 + 人工确认 |
| B 业务正确 | 代码是否符合 PRD/TECH_SPEC/Issue，**每个错误/分支实际触发验证，记录触发方式与结果** | 手动触发 + 检查日志/行为 |
| C 代码质量 | 命名、防御性取值、magic number、错误日志完整性、函数职责单一 | 对照官方 code-review skill 的 Code Quality checklist 逐条扫描 |
| D 测试 | 新增代码有对应测试（不审质量、不跑 UT） | 人工确认 |
| E 设计稿对齐 | 前端 UI 是否匹配 docs/design/，各交互状态是否处理 | 人工对比 |
| F 性能 | N+1 查询、不必要的重复请求 | 人工检查 |

**关键原则**：B 业务正确性必须实际触发验证。动手触发每个分支，确认行为和日志符合预期。不确定怎么触发的，在 PR 上提 comment 问。

**审查优先级**：一个实质 blocker > 一长串 nit。短 review 带一个可证明的阻塞问题，好过罗列琐碎意见。

## Review 反馈（三种方式结合）

1. **Inline comments** — 具体行级别的问题反馈
2. **Summary comment** — 汇总报告（模板见下）
3. **Review 状态** — GitHub 自带的 Approve / Request Changes

**原则**：一次性提完所有问题，避免零散 review。

## Summary 报告模板

```markdown
## Code Review 报告

### 审查维度
| 维度 | 结果 |
|------|------|
| ✅ A 安全 | 无问题 |
| ❌ B 业务正确 | 见下 |
| ✅ C 代码质量 | 无问题 |
| ⬜ D 测试 | 不涉及（QA 负责） |
| ⬜ E 设计稿对齐 | 后端 PR，不涉及 |
| ✅ F 性能 | 无问题 |

### 业务正确性测试记录
- curl POST /api/xxx → 201，返回体含 xxx 字段 ✅
- curl POST /api/xxx (空参) → 400 ✅
- curl GET /api/xxx/999 (不存在) → 404 ✅

### 严重问题（必须修复）
- [file:line] 问题描述 → 修复建议

### 建议改进
- [file:line] 建议描述

### 正面反馈
- 做得好的地方...
```

## 审计信息（webhook 静默触发时必附）

提交 review 结果时，在 Summary comment 末尾附上本次执行的审计标识（从 terminal 环境读取）：

```
审计: session=<echo $HERMES_SESSION_ID>  chat=<echo $HERMES_SESSION_CHAT_ID>
```

用途：事后 `hermes sessions export <session_id>` 查看该次静默执行的完整过程（每步工具调用、token 用量）。

## 修复循环

- Request Changes → Dev 修完 push + re-request（reviewers 模块）→ 重新看 → 合格前无限轮
- 每轮 review 把当前所有未修复的问题一次性提完
- **worktree 保留到 review 全过程结束**（含修复循环），不每轮清理——避免重复安装 venv 依赖。清理时机：Approve 且 PR merged 之后
- 多个 PR 并行 review 时，每个 PR 一个 worktree（不共用），全部结束后逐一清理
- Dev Lead review 如需查看/执行代码，创建自己的 worktree（`~/.hermes/profiles/dev-lead/workspace/`）

## 提交 review 的操作陷阱（GitHub API 2026-09 实测，PR #101 事故）

1. **设 review 状态用 `gh pr review`（CLI），不要 `gh api` 裸提交**
   - GitHub `PullRequestReviewEvent` 枚举已从 `APPROVED` 改名 `APPROVE`；裸 `gh api -f event=APPROVED` / curl 传旧枚举 → 422 `Variable $event ... was provided invalid value`（REST 内部转 GraphQL 失败）
   - `gh pr review --approve` / `--request-changes`（CLI）已适配新枚举，始终可用
2. **绝不在真实 PR 上做连通性/状态测试提交**（PR #101 事故：调试 422 时在 PR 上留下「连通性测试，将删除」「状态测试-将覆盖」两条 review 残留；其中 COMMENTED 条目 GitHub append-only，**无法删除也无法 dismiss**，永久污染 PR 历史）
   - 需要验证 API 行为：用 GraphQL introspection（`{ __type(name: "PullRequestReviewEvent") { enumValues { name } } }`）查合法枚举，或对一次性测试 PR 验证——绝不用真实 PR
3. **发文件内容 comment 用 `gh api --input file.json` 或 `gh pr comment --body-file file`**
   - `gh api -f body=@file` **不展开 `@` 文件引用**（`@file` 展开是 curl `-d @file` 语法）→ 路径字符串 `@/tmp/xxx.md` 会被当 body 发出去（PR #101 事故：正式报告发成了文件路径）
   - 正确姿势：`python3 -c "import json; json.dump({'body': open(f).read()}, open(o,'w'))"` → `gh api ... --input out.json`；或 `gh pr comment N --body-file report.md`
4. **review 提交前自检**：body 非空、无 `@/tmp` 路径残留、event 用 gh CLI 简写（--approve/--request-changes/--comment）

## 规则

- 只提 PR comment，不修改 Issue 和 PR（如需修改标题/描述/label，通过 comment 提出建议）
- review 结论以 PR comment 为准（Approve / Request Changes 状态 + 报告），不发群通知
- 查评论必须查三个端点：`pulls/{n}/comments`（inline）+ `issues/{n}/comments`（对话）+ `pulls/{n}/reviews`（review body）——只查一个会漏
- merge 信号与执行见 AGENTS.md 铁律 5（reviewDecision + 只 merge 自己的 PR）
