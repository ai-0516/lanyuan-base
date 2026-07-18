# 任务：技术选型 + 架构设计

## 工作区
当前项目路径：`/home/ubuntu/workspace/lanyuan-base`

### Git Worktree 配置要求
各 Profile 使用独立的 Git Worktree 工作目录，不要共用同一个工作区：

| Profile | Worktree 路径 |
|:---|:---|
| PM | `/home/ubuntu/workspace/lanyuan-base`（此为原仓库，已存在） |
| Dev Lead | `~/.hermes/profiles/dev-lead/workspace/lanyuan-base` |
| Dev | `~/.hermes/profiles/dev/workspace/lanyuan-base` |
| QA | `~/.hermes/profiles/qa/workspace/lanyuan-base` |

创建 worktree 的命令示例：
```bash
git worktree add ~/.hermes/profiles/dev-lead/workspace/lanyuan-base main
```

## 参考资料
- **PRD**: `docs/PRD.md` — 完整产品需求、用户故事、验收条件、数据模型
- **设计稿**: `docs/design/` — 全部 v3-*.html 原型页面
- **设计语言**: `docs/design/design-system.html` — CSS 变量、组件样式参考
- **仓库**: `git@github.com:snxly/lanyuan-base.git` (main branch)

## 已确定的技术决策
| 层级 | 技术 |
|:---|:---|
| **前端** | 微信原生小程序 |
| **后端** | FastAPI（Python） |
| **数据库** | MySQL |
| **AI 模型** | DeepSeek |

## 产品定位
这是一个**公共底座**小程序，为「兰园地暖」「兰园停车」等业务小程序提供可复用的登录+社交+AI 能力。业务方可在底座基础上添加新 Tab 扩展功能。

## 你需要做
1. 技术选型（前端框架/后端/数据库/部署方案）
2. 架构设计（系统架构图、组件树、API 设计）
3. 数据模型落地（根据 PRD 数据模型建议设计表结构或 schema）
4. 估算开发工作量，拆分开发任务
