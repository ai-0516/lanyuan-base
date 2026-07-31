# GitHub 多身份改造记录（2026-07-31）

## 背景与问题

所有 Hermes profile 共享同一个 `GITHUB_TOKEN`，导致各 profile 在 GitHub 上创建 issue、提交 PR、写 review comment 时，都显示为同一个人（snxly）。

目标：每个 profile 有独立、可管理的 GitHub 身份。

## 根因

GitHub API 操作（issue / PR / review / comment）的**作者身份由 token 所属账号决定**，API 没有"以某某身份操作"的参数，无法覆盖。

- git config（`user.name` / `user.email`）只影响 **commit 归属**，不影响 API 身份
- 同一个 token = 同一个身份，想分开必须一人一 token

## 方案

**Machine user 模式**（GitHub 官方对自动化账号的推荐做法）：

1. 每个需要独立身份的 profile 注册一个独立 GitHub 账号（bot 账号）
2. 每账号生成自己的 PAT，写入对应 profile 的 `.env`
3. commit 归属用账号的 noreply 邮箱对齐，使 commit 与 API 操作显示为同一身份
4. 账号统一放入 org，权限通过 team 管理

## 最终架构

```
org: ai-0516（owner: snxly，主账号）
└── team: hermes-bots（仓库权限 push）
    ├── dev-hermes-0516     ← dev profile
    └── devlead-hermes-0516 ← dev-lead profile
仓库: ai-0516/lanyuan-base（private，已从 snxly 名下转移）
```

| Profile | GitHub 账号 | 用户 ID | noreply 邮箱（commit 归属） | token 位置 |
|---|---|---|---|---|
| dev | dev-hermes-0516 | 311266063 | 311266063+dev-hermes-0516@users.noreply.github.com | profiles/dev/.env |
| dev-lead | devlead-hermes-0516 | 311272431 | 311272431+devlead-hermes-0516@users.noreply.github.com | profiles/dev-lead/.env |
| 其余 profile | 暂共享 snxly（15939467） | — | — | 各 profile .env |

**身份生效机制**：Hermes profile 的 `.env` 中 `GITHUB_TOKEN` 决定 API 身份（谁建 issue/PR/评论），`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_EMAIL` 决定 commit 归属。两者都指向同一账号即身份完全一致。

## 关键流程（沉淀为操作手册）

### 新增一个 bot 身份

```bash
# 1. 注册账号（Gmail +别名，如 ssnxly+<role>.hermes@gmail.com），命名 <role>-hermes-0516
# 2. 邀请进 org（用主账号 token，需 admin:org scope）
gh api -X POST orgs/ai-0516/invitations -f role=direct_member -F invitee_id=<用户ID>
# 3. bot 登录接受邀请（github.com/orgs/ai-0516/invitations）
# 4. bot 账号下生成 PAT（classic + repo scope，或 fine-grained：Issues/PR R/W + Contents Read）
# 5. 写入对应 profile 的 .env（注意：编辑 ~/workspace/my-hermes/profiles/<name>/.env，勿用 sed -i 改软链接！）
#    GITHUB_TOKEN=<bot token>
#    GIT_AUTHOR_EMAIL=<ID>+<用户名>@users.noreply.github.com
#    GIT_COMMITTER_EMAIL=<ID>+<用户名>@users.noreply.github.com
# 6. 加入 team 获得仓库权限
gh api -X PUT orgs/ai-0516/teams/hermes-bots/memberships/<用户名> -f role=member
# 7. 重启对应 gateway 使新 env 生效
systemctl --user restart hermes-gateway-<profile>
```

### 验证

```bash
# token 归属（服务器上 curl 走代理会假 401，必须用 gh）
GH_TOKEN=<token> gh api user --jq '.login'

# 待处理邀请
gh api orgs/ai-0516/invitations

# org 成员
gh api orgs/ai-0516/members
```

## 踩坑记录（重要）

1. **GitHub API 身份跟随 token，无法伪装** → 独立身份必须独立账号。
2. **批量注册触发风控**：同一 IP + 同前缀 Gmail 别名（ssnxly+...）短时间内注册多个账号，新账号被 flag，表现：无法接受组织邀请（"account has been flagged"），且账号对匿名 API 隐藏（404）。恢复路径：
   - 绑定手机号验证（但 GitHub 申诉表单的国码下拉**无 +86 选项**，国内用户无法走表单）
   - 等待风控自动解除（几天到两周）
   - 申诉表单 support.github.com/contact/account-flags（**support@github.com 拒收直发邮件**）
   - 教训：**分天注册、每账号配手机验证、非必要不批量**
3. **Gmail `+` 别名可注册 GitHub**（投递到主收件箱），但批量同前缀是风控诱因之一。
4. **`sed -i` 会破坏 .env 软链接**：GNU sed -i 写临时文件再 rename，把符号链接替换成实体文件，改动落在 git 仓库之外，`git log` 无记录。必须编辑仓库路径（`~/workspace/my-hermes/profiles/<name>/.env`）或软链接目标。
5. **服务器 curl 走代理访问 GitHub API 返回假 401**，验证一律用 `gh api`。
6. **git-crypt 仓库 commit 显示 `0 insertions`** 是加密 blob 的正常现象，不代表没提交。
7. **组织邀请 7 天过期**；邮箱邀请可被任意持有该邮箱的账号接受——**别用主账号误点 Accept**（会把给 bot 的邀请浪费掉）。
8. **仓库转移到 org 是异步的**：转移后立即挂 team 权限会 404，稍等重试即可。
9. 组织通知中心的 "X invited you to join..." 提醒可能是邮箱邀请的影子，确认邀请列表后无视即可。

## 待办

- [ ] pm-hermes-0516（用户 ID 未公开，被 flag）：等待风控解除 → 重新邀请 → 配 pm profile（如需）
- [ ] 其余 profile（cook / qa-engineer / reviewer / todo-master）按需配独立身份，注意分天注册防风控
- [ ] 仓库迁移：lanyuan-base 已迁至 org；如需统一管理，可把 multi-profile 等协作仓库也转移到 org 下

## 常用命令速查

```bash
# 邀请进 org（需 admin:org）
gh api -X POST orgs/ai-0516/invitations -f role=direct_member -F invitee_id=<ID>
# 邮箱邀请（账号未公开可见时）
gh api -X POST orgs/ai-0516/invitations -f role=direct_member -f email=<邮箱>
# 取消邀请
gh api -X DELETE orgs/ai-0516/invitations/<邀请ID>
# team 加人 / 仓库挂 team
gh api -X PUT orgs/ai-0516/teams/hermes-bots/memberships/<用户名> -f role=member
gh api -X PUT orgs/ai-0516/teams/hermes-bots/repos/ai-0516/<仓库> -f permission=push
# 仓库转移
gh api -X POST repos/<旧owner>/<仓库>/transfer -f new_owner=ai-0516
# 本地 remote 更新
git remote set-url origin git@github.com:ai-0516/<仓库>.git
```
