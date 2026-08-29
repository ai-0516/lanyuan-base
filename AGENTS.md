# lanyuan-base

微信小程序 + FastAPI 后端项目。开发流程总览：

```
Issue → Branch(worktree) → Implement → Commit → PR → Review 循环 → Merge → Cleanup
```

## 铁律（均为真实事故教训，违反即事故）

### 1. ⚠️ 永远不要直接修改 main 分支

包括 docs 在内**所有**改动一律走分支 + PR——即使只是文档（daily log、协议表等），也必须提交到 feature 分支随 PR 合入。main 只允许：pull、checkout/切分支、review 时读。任何直接 push/force push main 都是错误。

### 2. 新任务开工第一步 = git worktree add

所有分支从 main 切出，worktree 放在 profile workspace 下（不是主 git 目录同级）。**写文件前先 `git worktree list` 确认当前任务 worktree 的确切路径**。

### 3. Worktree 路径防错（write_file/patch 只写 worktree）

main 仓库 `/home/ubuntu/workspace/lanyuan-base` 与 worktree 路径相似（都含 `lanyuan-base`），误写已发生**多次**：

- **write_file/patch 一律用 worktree 绝对路径**（`/home/ubuntu/.hermes/profiles/<profile>/workspace/lanyuan-base-<branch>/...`）；**禁止**写 main 仓库路径
- 写完立即验证：`git -C <worktree> status --short` 应显示改动；同时 `git -C /home/ubuntu/workspace/lanyuan-base status --short` 应为空
- 误写 main 工作区：立即 `cd /home/ubuntu/workspace/lanyuan-base && git checkout -- <file>` 回退，再在 worktree 重写（回退后确认 status 为空）

### 4. 一个 PR 只处理一个 issue

不要合并多个 issue 进一个 PR。

### 5. Merge：reviewDecision 满足即 merge（不特定等某个人）

merge 条件：
1. `gh pr view N --json reviewDecision` == **APPROVED**（main ruleset 2 approve 满足，author 不能自批）

- ⚠️ dev-lead 说「已 Approve / 可以 merge 了」**不算** merge 信号，只是 review 结论（PR #30 事故）
- ⚠️ **不特定等 snxly 的 approve**：snxly 可能先 approve、dev-lead 后 approve——以 reviewDecision 整体条件为准，避免早 merge
- **只 merge 自己的 PR**：merge 前先查 author（PR #31 事故：dev-lead 误 merge dev 的 PR）
- merge 方式统一 **squash**：`gh pr merge N --repo ai-0516/lanyuan-base --squash`

### 6. 文档随 PR 同步更新

- 协议/架构文档随代码演进（如 SSE 事件协议表——新增事件、改语义都要改表）
- daily log 描述最终方案，重构后旧描述必须同步
- 引用点一处不漏：`docs/`、`scripts/review/` 验证脚本断言、`tests/` 断言——三者与新代码不一致都会被 review 实测抓出

### 7. PR 分支上的内容（含 doc）必须 git push 才在 PR 可见

commit 只在本地 worktree 时，reviewer 看不到。push 新 commit 不会破坏以该分支为 base 的 review PR，GitHub 会自动处理。

## 常用命令

```bash
cd backend && uv run pytest tests/    # 测试（push 前先看改动范围，选相关测试；全量仅跨层改动或明确要求）
git -C <worktree> status --short      # 验证改动落点
git diff main...HEAD --stat           # 看改动范围
```

## 审查与提交前检查

- Review：加载 `lanyuan-code-review`（六维度 + 审查依据 + 硬阻塞项）
- Push 前：加载 `lanyuan-pre-push-checks`（按改动类型选最小检查集）

## 项目级技能（按需加载，见 .agents/skills/）

- `lanyuan-code-review` — 审查者六维度流程（Dev-Lead 用）
- `lanyuan-pre-push-checks` — 提交前最小检查（Dev 用）

GitHub 操作手册（gh 命令怎么敲）用官方 skills（github-pr-workflow 等），本项目不重复。
