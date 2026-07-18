# 兰园公共底座 — 技术方案 (TECH_SPEC)

> **版本**: v3.0 | **状态**: 待审批  
> **编写**: Dev Lead | **日期**: 2026-07-18  
> **关联文档**: `docs/PRD.md`, `docs/design/` (v3-\*.html), `docs/design/design-system.html`

---

## 目录

1. [技术选型与对比](#1-技术选型与对比)
2. [系统架构](#2-系统架构)
3. [数据模型](#3-数据模型)
4. [API 设计](#4-api-设计)
5. [前端组件树](#5-前端组件树)
6. [非功能需求](#6-非功能需求)
7. [部署方案](#7-部署方案)

---

## 1. 技术选型与对比

### 1.1 前端 — 微信原生小程序

| 维度 | 微信原生小程序 | Taro (React) | uni-app (Vue) |
|------|---------------|-------------|--------------|
| **最新版本** | SDK 3.7+ | v4.x | v3.x |
| **包体积** | 最小 | 中（含 runtime） | 中（含 runtime） |
| **性能** | 最高（直接调用原生 API） | 中（编译层开销） | 中（编译层开销） |
| **API 兼容性** | 100%（原生支持） | 95%（需适配） | 90%（适配层） |
| **跨平台能力** | ❌ 仅微信 | ✅ 微信+H5+支付宝 | ✅ 微信+H5+App |
| **学习成本** | 低（微信规范） | 中（React 生态） | 低（Vue 生态） |

**决策：微信原生小程序**

理由：
- 本项目仅面向微信生态，无跨平台需求
- 原生 API 保证 100% 兼容微信登录、图片上传、用户信息等核心能力
- 包体积最小（MVP 页面少，原生开发最轻量）
- MVP 共 7 个页面，学习曲线可忽略

### 1.2 后端框架 — FastAPI

| 维度 | FastAPI | Flask | Django REST |
|------|---------|-------|-------------|
| **最新版本** | 0.115.x | 3.x | 5.x |
| **Stars** | 80k+ | 70k+ | 80k+ |
| **异步原生** | ✅ 原生 async | ❌ 需额外配置 | ✅ async (3.0+) |
| **自动文档** | ✅ OpenAPI/Swagger | ❌ 需插件 | ✅ DRF 自带 |
| **性能** | 高（UVicorn） | 中（WSGI） | 中（ASGI） |
| **类型校验** | ✅ Pydantic | ❌ 需手动 | ✅ DRF Serializer |
| **侵入性** | 低 | 极低 | 高（全功能框架） |

**决策：FastAPI**

理由：
- 异步原生，适合 AI 对话场景（非阻塞等待 DeepSeek 响应）
- Pydantic v2 自动做请求/响应校验，减少 boilerplate
- 自动生成 OpenAPI 文档，方便调试
- Python 3.12 支持最佳

### 1.3 ORM

| 维度 | SQLAlchemy 2.0 | Tortoise-ORM | Django ORM |
|------|---------------|-------------|-----------|
| **版本** | 2.0.x | 0.24.x | — |
| **异步** | ✅ async session | ✅ 原生 async | ✅ (via django-ninja) |
| **成熟度** | 极高（15年+） | 中（5年+） | 高 |
| **与 FastAPI 集成** | ✅ 官方推荐 | ✅ 原生支持 | ❌ 需额外适配 |
| **迁移工具** | Alembic | Aerich | 内置 |

**决策：SQLAlchemy 2.0 + Alembic**

理由：
- FastAPI 官方推荐组合，社区资源丰富
- SQLAlchemy 2.0 的 async session 稳定成熟
- Alembic 迁移管理完善

### 1.4 数据库

| 维度 | MySQL 8.0 | PostgreSQL 16 | SQLite |
|------|-----------|---------------|--------|
| **并发性能** | 高 | 高 | 低 |
| **JSON 支持** | ✅ JSON 类型 | ✅ JSONB | ⚠️ 有限 |
| **运维成本** | 低（阿里云 RDS） | 中 | 最低 |
| **全文搜索** | ⚠️ 有限 | ✅ 内置 | ❌ |

**决策：MySQL 8.0** (已确认)

理由：PM 已确认。社区版免费，阿里云 RDS 一键托管，运维成本低。

### 1.5 AI 模型集成

| 维度 | 直接调用 DeepSeek API | 自建推理服务 (vLLM) |
|------|----------------------|-------------------|
| **部署成本** | 低（按量付费） | 高（GPU 服务器 ¥5k+/月） |
| **响应速度** | 快（100-300ms TTFB） | 中（受 GPU 负载影响） |
| **延迟敏感度** | 低（服务端调用，前端无感） | 低 |
| **维护成本** | 零 | 高（模型更新、监控） |
| **隐私** | 数据经过第三方 | 数据不出服务器 |

**决策：直接调用 DeepSeek API**

理由：
- MVP 阶段流量低，按量付费最经济（估计月费 < ¥100）
- 自建推理需要 GPU 服务器，月费和运维成本远高于 API 调用
- AI 对话内容非敏感业务数据（小区物业咨询），隐私风险可接受
- 后续可在后台无缝切换为自有服务

### 1.6 图片存储

| 维度 | 阿里云 OSS | 本地磁盘 | 七牛云 |
|------|-----------|---------|-------|
| **CDN 加速** | ✅ 内置 | ❌ | ✅ 内置 |
| **成本** | 低（按量计费） | 最低（磁盘） | 低 |
| **阿里云 ECS 内网** | ✅ 免流量费 | — | ❌ 出网流量计费 |
| **微信本地缓存** | ✅ 配合 CDN | ❌ | ✅ |

**决策：阿里云 OSS + CDN**

理由：
- 云服务器也在阿里云，内网上传免流量费
- 图片 CDN 分发加速小区用户访问
- 配合微信小程序 `webp` 格式优化传输

### 1.7 缓存

**决策：Redis 7.x** (阿里云 Redis)

用途：
- 微信 session_key 缓存（临时存储）
- DeepSeek 对话上下文缓存（减少重复调用）
- 通知未读计数缓存（高频读取）
- 图片缩略图签名 URL 缓存

### 1.8 Async Task

**决策：Celery + Redis Broker**

用途：
- 异步推送通知（点赞/评论/回复）
- 图片压缩/水印处理
- 长时间 AI 对话处理

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                    微信小程序客户端                       │
│  (原生开发, 7个页面, 底部Tab: AI → 发现 → 我)           │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS (api.lanyuan.com)
                         ▼
┌─────────────────────────────────────────────────────────┐
│                 阿里云 SLB / Caddy                        │
│          HTTPS Terminator + 反向代理                      │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                 FastAPI 应用服务                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  API Layer (RESTful, 版本前缀 /api/v1)           │    │
│  │  ┌──────┐ ┌──────────┐ ┌─────────┐ ┌────────┐ │    │
│  │  │ 登录 │ │ 帖子/评论 │ │ 通知    │ │ AI对话 │ │    │
│  │  └──────┘ └──────────┘ └─────────┘ └────────┘ │    │
│  ├─────────────────────────────────────────────────┤    │
│  │  Service Layer (业务逻辑)                        │    │
│  │   UserService / PostService / CommentService /   │    │
│  │   NotificationService / AIService                │    │
│  ├─────────────────────────────────────────────────┤    │
│  │  Data Layer (SQLAlchemy async session)           │    │
│  └─────────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
         │                │               │
         ▼                ▼               ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   MySQL 8.0  │  │   Redis 7.x   │  │  阿里云 OSS  │
│   (阿里云RDS) │  │   (缓存/任务)  │  │   (图片存储)  │
└──────────────┘  └──────┬───────┘  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │   DeepSeek    │
                  │   API (HTTP)  │
                  └──────────────┘
```

### 2.2 后端目录结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用入口, 生命周期
│   ├── config.py               # 配置管理 (环境变量)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py         # 微信登录相关
│   │   │   ├── posts.py        # 帖子 CRUD
│   │   │   ├── comments.py     # 评论 CRUD
│   │   │   ├── notifications.py# 通知相关
│   │   │   ├── profile.py      # 个人中心/编辑资料
│   │   │   ├── ai.py           # AI 对话
│   │   │   └── upload.py       # 图片上传
│   │   └── deps.py             # 依赖注入 (get_db, get_current_user)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── post.py
│   │   ├── comment.py
│   │   ├── like.py
│   │   ├── notification.py
│   │   └── ai_conversation.py  # AI 对话记录
│   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── post.py
│   │   ├── comment.py
│   │   ├── notification.py
│   │   └── ai.py
│   ├── services/               # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── post_service.py
│   │   ├── comment_service.py
│   │   ├── notification_service.py
│   │   └── ai_service.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py         # JWT 工具
│   │   ├── database.py         # SQLAlchemy 引擎/会话
│   │   └── wechat.py           # 微信 API 客户端
│   └── tasks/
│       ├── __init__.py
│       ├── celery_app.py       # Celery 配置
│       └── notifications.py    # 异步通知推送
├── alembic/                    # 数据库迁移
│   └── versions/
├── alembic.ini
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

### 2.3 前端小程序目录结构

```
miniprogram/
├── app.js                      # 全局入口, 登录态判断
├── app.json                    # 全局配置 (页面注册, TabBar)
├── app.wxss                    # 全局样式 (CSS 变量)
├── project.config.json         # 微信开发者工具配置
├── pages/
│   ├── login/                  # 登录页
│   ├── feed/                   # 发现页 (帖子流)
│   ├── create-post/            # 发布帖子
│   ├── ai-chat/                # AI 对话页
│   ├── profile/                # 个人中心
│   ├── edit-profile/           # 编辑资料
│   └── notifications/          # 消息通知
├── components/
│   ├── tab-bar/                # 底部 Tab (自定义)
│   ├── post-card/              # 帖子卡片组件
│   ├── comment-sheet/          # 评论弹出层
│   ├── like-button/            # 点赞按钮
│   ├── image-grid/             # 图片网格布局
│   ├── chat-bubble/            # 聊天气泡
│   └── lightbox/               # 图片全屏预览
├── utils/
│   ├── request.js              # 封装 wx.request
│   ├── auth.js                 # 登录/Token 管理
│   ├── date.js                 # 时间格式化
│   └── constants.js            # API 地址/颜色常量
└── styles/
    └── variables.wxss          # CSS 变量 (对应设计语言)
```

### 2.4 数据流 — 关键场景

**场景 A：用户登录流程**
```
1. 小程序 wx.login() → 获取 code
2. POST /api/v1/auth/login { code } → 后端调微信 API 换 session_key + openid
3. 后端生成 JWT (含 user_id) → 返回给前端
4. 前端存储 token 到 wx.Storage
5. 检查用户信息: GET /api/v1/user/me → 无信息 → 跳编辑资料
```

**场景 B：AI 对话流程**
```
1. 用户发送消息 → POST /api/v1/ai/chat { message }
2. 后端查 Redis → 获取最近对话历史 (last 20 条)
3. 拼接 System Prompt + 历史 → 调 DeepSeek API
4. DeepSeek 返回 → 后端存储到 MySQL ai_conversations 表
5. 更新 Redis 缓存
6. 返回 AI 回复消息 { role: "assistant", content }
```

**场景 C：通知推送流程 (异步)**
```
1. 用户 B 点赞帖子 A → POST /api/v1/posts/:id/like
2. 后端同步创建 like 记录
3. Celery task: create_notification.delay(like_id)
4. 通知 task 查询 post 作者 → 创建 notification 记录
5. 下次用户 A 刷新通知 → GET /api/v1/notifications → 显示未读
```

---

## 3. 数据模型

### 3.1 ER 图 (文本描述)

```
User 1 ──── * Post            # 用户发帖
User 1 ──── * Comment          # 用户评论
User 1 ──── * Like             # 用户点赞
User 1 ──── * Notification     # 用户接收通知
User 1 ──── * AiConversation   # 用户 AI 对话
Post 1 ──── * Comment          # 帖子下评论
Post 1 ──── * Like             # 帖子被点赞
Comment ──── Comment (self-ref: parent_comment_id)
```

### 3.2 表结构

#### user

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 用户ID |
| openid | VARCHAR(64) | UNIQUE, NOT NULL | 微信 openid |
| unionid | VARCHAR(64) | NULLABLE | 微信 unionid |
| nickname | VARCHAR(32) | NOT NULL | 昵称 |
| avatar | VARCHAR(256) | NOT NULL | 头像 URL |
| community | VARCHAR(64) | NULLABLE | 小区名称 |
| building | VARCHAR(16) | NULLABLE | 楼号 |
| unit | VARCHAR(16) | NULLABLE | 单元 |
| room | VARCHAR(16) | NULLABLE | 房号 |
| bio | VARCHAR(200) | NULLABLE, DEFAULT '' | 个性签名 |
| show_building | TINYINT(1) | DEFAULT 1 | 是否公开楼号 |
| show_room | TINYINT(1) | DEFAULT 0 | 是否公开房号 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 注册时间 |
| updated_at | DATETIME | ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

索引：`(openid)` UNIQUE

#### post

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 帖子ID |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 作者 |
| content | TEXT | NOT NULL | 正文 |
| images | JSON | NOT NULL, DEFAULT '[]' | 图片 URL 数组 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 发布时间 |
| updated_at | DATETIME | ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

索引：`(created_at)` DESC（时间线排序）

#### comment

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 评论ID |
| post_id | BIGINT UNSIGNED | FK → post.id, NOT NULL | 所属帖子 |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 评论者 |
| parent_comment_id | BIGINT UNSIGNED | FK → comment.id, NULLABLE | 被回复的评论 (null=直接评论) |
| content | VARCHAR(500) | NOT NULL | 评论内容 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 评论时间 |

索引：`(post_id, created_at)`（帖子下按时间排序）

#### `like`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 点赞ID |
| post_id | BIGINT UNSIGNED | FK → post.id, NOT NULL | 帖子 |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 点赞者 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 点赞时间 |

索引：`(post_id, user_id)` UNIQUE（保证一个用户对一帖只能点一次）

#### notification

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 通知ID |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 接收者 |
| type | ENUM('like','comment','reply') | NOT NULL | 通知类型 |
| from_user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 触发者 |
| post_id | BIGINT UNSIGNED | FK → post.id, NOT NULL | 关联帖子 |
| comment_id | BIGINT UNSIGNED | FK → comment.id, NULLABLE | 关联评论 (reply 类型时) |
| is_read | TINYINT(1) | DEFAULT 0 | 是否已读 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 通知时间 |

索引：`(user_id, is_read, created_at)`（未读通知查询）
索引：`(user_id, post_id)`（标记同一帖子相关通知已读）

#### ai_conversation

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 消息ID |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 用户 |
| role | ENUM('user','assistant') | NOT NULL | 角色 |
| content | TEXT | NOT NULL | 消息内容 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 发送时间 |

索引：`(user_id, created_at)`（按用户获取对话历史）

---

## 4. API 设计

> **基础路径**: `/api/v1`  
> **认证方式**: JWT Bearer Token (除登录接口外全部需要)  
> **响应格式**: `{ code: 0, data: {...}, message: "ok" }`  
> **错误格式**: `{ code: 40001, message: "错误描述" }`

### 4.1 认证

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/auth/login` | 微信登录 | `{ code: string }` | `{ token, user }` |
| GET | `/auth/check` | 检查 token 是否有效 | — | `{ valid: bool }` |

### 4.2 用户

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/user/me` | 获取当前用户信息 | — | `User` |
| PUT | `/user/me` | 更新个人资料 | `{ nickname?, avatar?, community?, building?, unit?, room?, bio?, show_building?, show_room? }` | `User` |
| GET | `/users/{id}` | 查看用户公开信息 | — | `UserPublic` (隐藏房号) |
| POST | `/user/logout` | 退出登录 | — | `{ success }` |

### 4.3 帖子

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/posts` | 帖子列表 (按时间倒序) | query: `?page=1&size=20` | `{ items: Post[], total, page, size }` |
| GET | `/posts/{id}` | 帖子详情（含评论和点赞） | — | `PostDetail` |
| POST | `/posts` | 发布帖子 | `{ content, images[] }` | `Post` |
| DELETE | `/posts/{id}` | 删除帖子（仅作者） | — | `{ success }` |
| POST | `/posts/{id}/like` | 点赞 / 取消点赞 | — | `{ liked: bool, likeCount: int }` |

**帖子列表响应示例：**
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 1,
        "user": { "id": 1, "nickname": "张三", "avatar": "..." },
        "content": "供暖第一天，屋里终于暖和了！",
        "images": ["https://oss.aliyuncs.com/xxx.jpg"],
        "likeCount": 12,
        "liked": false,
        "commentCount": 3,
        "comments": [
          {
            "id": 1,
            "user": { "id": 2, "nickname": "李四", "avatar": "..." },
            "content": "我家也挺暖和的",
            "createdAt": "2026-07-18T10:00:00Z",
            "replyTo": null
          },
          {
            "id": 2,
            "user": { "id": 1, "nickname": "张三", "avatar": "..." },
            "content": "回复 李四：那你家温度多少？",
            "createdAt": "2026-07-18T10:05:00Z",
            "replyTo": {
              "userId": 2,
              "nickname": "李四"
            }
          }
        ],
        "createdAt": "2026-07-18T08:00:00Z"
      }
    ],
    "total": 1,
    "page": 1,
    "size": 20
  }
}
```

### 4.4 评论

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/posts/{postId}/comments` | 获取帖子评论 | `?page=1&size=50` | `Comment[]` |
| POST | `/posts/{postId}/comments` | 添加评论 | `{ content, parentCommentId? }` | `Comment` |
| DELETE | `/comments/{id}` | 删除评论（仅作者/帖主） | — | `{ success }` |

### 4.5 通知

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/notifications` | 未读通知列表 | — | `Notification[]` |
| GET | `/notifications/count` | 未读通知数量 | — | `{ count: int }` |
| POST | `/notifications/read` | 标记为已读 | `{ postId: number }` | `{ updated: int }` |

### 4.6 AI 对话

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/ai/chat` | 发送消息并获取 AI 回复 | `{ message: string }` | `{ reply: string }` |
| GET | `/ai/history` | 获取当前会话历史 | `?limit=30` | `AiMessage[]` |

### 4.7 图片上传

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/upload/images` | 上传图片 (1-9张) | `multipart: files[]` | `{ urls: string[] }` |

---

## 5. 前端组件树

```
App (app.js)
├── LoginPage (pages/login/)
│   └── <login-btn> [微信一键登录按钮]
│
├── FeedPage (pages/feed/)           ← Tab: 发现
│   ├── <post-card> * N
│   │   ├── <feed-header> [头像 + 昵称 + 时间]
│   │   ├── <feed-body> [正文]
│   │   ├── <image-grid> [1-9张图片, 自适应布局]
│   │   ├── <like-button> [点赞/取消点赞]
│   │   ├── <comment-section> [评论列表, 含回复嵌套]
│   │   │   └── <comment-item> [评论内容 + 回复 @用户名]
│   │   └── <comment-sheet> [底部弹出评论框]
│   └── <fab> [+] 浮动发布按钮
│
├── CreatePostPage (pages/create-post/)
│   ├── <textarea> [正文输入]
│   ├── <image-picker> [图片选择器, 1-9张]
│   ├── <image-grid> [图片预览 + 删除]
│   └── <char-count> [字数统计]
│
├── AiChatPage (pages/ai-chat/)      ← Tab: AI
│   ├── <chat-bubble> (ai) [暖白气泡, 左下圆角4px]
│   ├── <chat-bubble> (user) [陶土渐变气泡, 右下圆角4px]
│   └── <chat-input> [textarea + 发送按钮, 底部固定]
│
├── ProfilePage (pages/profile/)     ← Tab: 我
│   ├── <profile-header> [大头像 + 昵称 + 小区]
│   ├── <notification-card> [消息通知入口 + 未读红点]
│   └── <settings-list> [编辑资料 / 隐私开关 / 退出登录]
│
├── EditProfilePage (pages/edit-profile/)
│   └── <profile-form> [头像 + 昵称 + 小区 + 楼号 + 单元 + 房号 + 个性签名]
│
└── NotificationsPage (pages/notifications/)
    └── <notification-item> * N
        [头像 + 用户名 + 动作 + 帖子标题 + 未读标记]
```

### Tab 结构与导航

```
微信小程序 TabBar (底部固定)
┌──────┬──────┬──────┐
│  AI  │ 发现 │  我  │
│ (○)  │ (💬) │ (◎)  │
└──────┴──────┴──────┘
- AI → /pages/ai-chat/index
- 发现 → /pages/feed/index
- 我 → /pages/profile/index
- 发布: 发现页 FAB → navigateTo /pages/create-post/index
- 通知: 个人中心 → navigateTo /pages/notifications/index
- 编辑资料: 个人中心 → navigateTo /pages/edit-profile/index
- 登录: 未登录 → reLaunch /pages/login/index
```

---

## 6. 非功能需求

### 6.1 性能

| 指标 | 目标 | 实现方式 |
|------|------|----------|
| API 响应时间 (无 AI) | < 200ms P95 | 异步查询 + 数据库索引 + Redis 缓存 |
| AI 对话响应 | < 3s（首次TTFB） | DeepSeek API直调 + 流式SSE（按需看MVP是否支持） |
| 图片加载 | < 1s | OSS CDN + WebP 格式 + 缩略图裁剪 |
| 并发 | 支持 100 QPS | FastAPI + Uvicorn workers (4-8) |
| 数据库连接池 | 最大 20 | SQLAlchemy pool_size=10, max_overflow=10 |

### 6.2 安全

| 维度 | 措施 |
|------|------|
| 认证 | JWT (HS256, 过期时间 7 天) |
| 微信登录 | code 换 session_key 逻辑在后端完成，不暴露给前端 |
| API 鉴权 | JWT middleware 校验，user_id 从 token 解析 |
| CORS | 仅允许小程序域名(可在微信小程序设置request合法域名) |
| XSS | 用户输入 HTML 转义，rich text 限制 |
| 图片风险 | OSS 上传鉴权 (STS 临时凭证) |
| 防刷 | 评论/点赞频率限制 (Redis + 10s/次) |
| 隐私 | 房号默认不公开 (show_room default 0) |

### 6.3 微信小程序限制

| 限制项 | 处理方案 |
|--------|----------|
| request 域名白名单 | 部署后配置到小程序管理后台 |
| 上传域名白名单 | OSS 域名需配置到 uploadFile 合法域名 |
| 包体积 < 2MB | 原生开发，图片用 CDN，代码中不内嵌大文件 |
| wx.login 同一 session 只能用一次 | 每次登录重新获取 code |

### 6.4 错误码约定

| 范围 | 说明 |
|------|------|
| 0 | 成功 |
| 40001-40099 | 请求参数错误 |
| 40101-40199 | 认证错误 (token 过期/无效) |
| 40301-40399 | 权限不足 |
| 40401-40499 | 资源不存在 |
| 42901-42999 | 频率限制 |
| 50001-50099 | 服务器内部错误 |

---

## 7. 部署方案

### 7.1 服务器配置 (MVP)

| 组件 | 规格 | 说明 |
|------|------|------|
| **应用服务器** | 阿里云 ECS 2C4G | 运行 FastAPI + Celery worker |
| **数据库** | 阿里云 RDS MySQL 8.0 (1C2G) | 数据持久化 |
| **缓存** | 阿里云 Redis (256MB) | session/缓存/任务队列 |
| **图片存储** | 阿里云 OSS (按量计费) | 图片上传 + CDN 分发 |
| **域名** | api.lanyuan.com (待申请) | 小程序 request 合法域名 |
| **SSL** | Let's Encrypt 自动续期 | Caddy 自动处理 |

### 7.2 部署架构

```
ECS (阿里云, 2C4G, Ubuntu 22.04)
├── Caddy                  # HTTPS 反向代理 + 自动 SSL
├── Docker
│   ├── fastapi-app        # FastAPI (Uvicorn, 4 workers)
│   ├── celery-worker      # Celery (后台通知推送)
│   └── (Redis 可选本地 or 托管)
├── systemd
│   └── caddy.service
└── OSS CLI / SDK
    └── 图片上传
```

### 7.3 环境管理

| 环境 | 域名 | 用途 |
|------|------|------|
| **本地开发** | `localhost:8000` | Dev 本地调试 |
| **测试环境** | `test-api.lanyuan.com` | Dev 自测 + Reviewer + QA |
| **生产环境** | `api.lanyuan.com` | 微信小程序正式环境 |

### 7.4 开发流程

```
1. Dev clone 仓库 → 本地 docker-compose up (MySQL + Redis)
2. alembic upgrade head → 数据库迁移
3. uvicorn app.main:app --reload → 开发
4. Git commit + push → (Reviewer 门禁)
5. 手动部署到测试 ECS → QA 测试
6. 手动部署到生产 ECS → 上线
```

### 7.5 依赖清单

**Python 包 (requirements.txt)**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlalchemy[asyncio]>=2.0.0
aiomysql>=0.2.0
alembic>=1.13.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
httpx>=0.27.0               # DeepSeek API 客户端
redis>=5.0.0
celery>=5.4.0
python-jose[cryptography]>=3.3.0  # JWT
python-multipart>=0.0.0
aliyun-oss2-sdk>=2.18.0
```

**微信小程序**

- 微信开发者工具 (最新稳定版)
- 基础库版本 ≥ 3.0.0
- 无需额外 npm 包（原生开发）

---

> **审批**: 以上技术方案请在 kanban 中回复「没有问题」确认，我将创建 Dev 开发任务。
