---
name: lanyuan-pre-push-checks
description: Use before pushing a lanyuan-base branch, force-pushing, marking ready for review, or claiming checks pass — 按改动类型选最小检查集，不盲目全量跑。
---

# lanyuan-base 提交前最小检查

## 0. 路径防错验证（先做）

```bash
git -C <worktree> status --short                      # 改动只落在 worktree
git -C /home/ubuntu/workspace/lanyuan-base status --short   # main 仓库必须为空
```

## 1. 看改动范围

```bash
git diff main...HEAD --stat        # 改动文件与规模
git diff main...HEAD --name-only   # 文件清单
```

## 2. 按改动类型选最小检查

| 改动类型 | 最小检查 |
|---|---|
| Python 代码（backend/） | 相关测试文件：`cd backend && uv run pytest tests/test_xxx.py`；改动文件 `python -m py_compile` |
| 协议/事件变更（SSE 事件表等） | 同步 `docs/` 事件表 + `tests/` 断言 + `scripts/review/` 断言（引用点一处不漏） |
| 数据库迁移（alembic） | `alembic heads` 确认单 head（不要用自写正则校验链） |
| 文档（docs/、daily） | 引用点 grep 一致（旧路径残留、目录树描述同步） |
| 前端（miniprogram/） | 编译验证 + 对照 docs/design/ |

**选择逻辑**：只跑改动面能覆盖的最小测试集。跨层/跨模块改动、或用户明确要求时才全量 `uv run pytest tests/`。

## 3. 自审（新增行）

```bash
git diff | grep "^+" | grep -iE "(api_key|secret|password|token)\s*=\s*['\"][^'\"]{6,}['\"]"  # 硬编码密钥
git diff | grep "^+" | grep -E "print\(|console\.log|debugger"                              # 调试残留
```

- commit message 规范：`type: 描述`（feat/fix/chore/docs...）

## 4. Push

```bash
git push -u origin <branch>
gh pr view N --json files,commits --jq .   # 确认 PR 可见性（内容必须 push 才在 PR 可见）
```

## 原则

- 不重复跑已通过的检查（跑过一次且无新改动，push 前不重跑）
- 全量测试不是默认项；CI 拥有穷尽覆盖
- 检查不是流程仪式——每项检查都要能证明「这次改动没破坏什么」
