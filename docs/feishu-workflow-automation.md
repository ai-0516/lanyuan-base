# 飞书群聊实现 Code & Review 流程自动化

> 2026-08-01 · Issue #29 · dev / dev-lead 双 bot 协作

## 目标

用飞书群聊（hermes 群）作为 dev 和 dev-lead 两个 bot 之间的通知通道，实现：

```
dev 提交 PR → 群里 @dev-lead 通知 review → dev-lead review → @dev 通知修复 → 修复循环 → merge → @dev-lead 通知闭环
```

替代原来的「用户人工转发通知」协调模式。

## 技术结论

### 1. 平台侧：飞书推送权限

飞书机器人接收群消息受**两个不同权限**控制：

| 权限 | 效果 |
|------|------|
| `im:message.group_at_msg`（接收群聊中@机器人的消息）| **只推送 @ 机器人的消息** |
| `im:message.group_msg`（获取群组中所有消息）| 推送群里所有消息 |

**必须开通 `group_msg`**，否则非 @ 消息在飞书平台侧就被过滤，根本推不到 bot——此时 Hermes 侧任何配置（如 `FEISHU_REQUIRE_MENTION=false`）都无从生效（巧妇难为无米之炊）。

开通位置：飞书开放平台后台 → 应用（dev / dev-lead 是**两个独立应用**，都要开）→ 权限管理 → 「获取群组中所有消息」→ 发布版本 + 管理员审批。

### 2. Hermes 侧：准入配置语义

| 配置 | 取值 | 语义 |
|------|------|------|
| `FEISHU_ALLOW_ALL_USERS` | `true` | 所有**人类**消息都准入（配合群聊广播） |
| `FEISHU_REQUIRE_MENTION` | `true` | 群聊消息必须 @ 到本 bot 才准入 → **单聊/定向对话**：@ 谁谁响应；@ 两个或 @all 广播 |
| `FEISHU_ALLOW_BOTS` | `mentions` | bot 消息必须被 @ 才准入；`none` 拒绝所有 bot 消息；`all` 全收 |

**推荐组合（2026-08-01 定案）**：`ALLOW_ALL_USERS=true` + `REQUIRE_MENTION=true` + `ALLOW_BOTS=mentions`

- 人类消息：不 @ 全群广播，@ 单个 bot 定向对话（context 保留在群 session）
- bot 消息：必须被 @ 才收，避免 bot 互相对话循环

⚠️ **事故教训**：曾配置 `ALLOW_BOTS=all` + `REQUIRE_MENTION=false` + `group_msg` 权限，导致两个 bot 互相收到对方消息、无限循环对话。约束是必要的——bot 是「点名制」，人类是「广播制」。

### 3. bot 之间发带 @ 的消息

Hermes 的 feishu adapter 出站 `send()` 本身不构造 mention，但飞书 text 类型消息支持 `<at>` 标签透传：

```bash
hermes send --to "feishu:<chat_id>" \
  "<at user_id=\"<对方open_id>\">@对方</at> 消息内容"
```

- 群 chat_id：`oc_7b65699c35e5558fef0a4050191597d9`（hermes 群）
- dev open_id：`ou_8df38659823a6db1be78b258cb8d6fd6`
- dev-lead open_id：`ou_da9becf1e8608ebbab1492dda68ab013`

**⚠️ 硬性约束：消息必须纯文本**，不带任何 markdown 标记（`**`、`#`、`- `、代码块、`[text](url)`）。含 markdown 会走 `post` 类型消息，`<at>` 标签不渲染；纯文本走 `text` 类型，`<at>` 原样透传被飞书解析为真 @。链接用裸 URL。

### 4. 诊断技巧

`feishu_seen_message_ids.json`（profile 根目录）的去重记录在准入检查 `_admit()` **之前**写入——**任何到达 adapter 的消息都会留下 message_id**。查它 + gateway.log 有无 `inbound message`，可区分「消息根本没到 adapter（平台侧过滤）」vs「到达但被拒（准入策略）」。

### 5. 配置管理

`.env` 用**符号链接**指向 my-hermes 真源，避免独立文件漂移：

```bash
~/.hermes/profiles/dev/.env -> ../../../workspace/my-hermes/profiles/dev/.env
~/.hermes/profiles/dev-lead/.env -> /home/ubuntu/workspace/my-hermes/profiles/dev-lead/.env
```

改配置只需改 my-hermes 真源 + 重启 gateway。

## 流程落地

- dev 的 `github-workflow` skill 已加入互通知流程（PR 创建 → @dev-lead review → 修复通知 → merge 闭环；merge 信号来自用户，不是 dev-lead approve）
- 新 skill `feishu-group-mention-send`：群里 @ 对方发消息的模板 + 纯文本约束 + 验证方法
- dev-lead 的 `github-workflow` skill 由 dev-lead 自行更新
