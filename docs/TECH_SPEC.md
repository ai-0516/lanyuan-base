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
   - 7.1 微信云托管配置
   - 7.2 部署架构
   - 7.3 部署方案对比
   - 7.4 微信云托管要点
   - 7.5 环境管理
   - 7.6 开发流程
   - 7.7 Dockerfile (参考)
   - 7.8 依赖清单
8. [Session 管理设计](#8-session-管理设计)
   - 8.1 目标与核心原则
   - 8.2 会话生命周期
   - 8.3 压缩旋转 (rotation)
   - 8.4 记忆抽取
   - 8.5 UI 历史消息查询
   - 8.6 Agent 搜索历史 (search_history)
   - 8.7 System Prompt 缓存
   - 8.8 数据模型影响
   - 8.9 待实现确认项

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
| **运维成本** | 低（云托管内置） | 中 | 最低 |
| **全文搜索** | ⚠️ 有限 | ✅ 内置 | ❌ |

**决策：MySQL 8.0** (微信云托管云数据库，已确认)

理由：PM 已确认。选用微信云托管内置的云数据库 MySQL，与容器同集群，内网连接免流量费，无需单独管理 RDS。

### 1.5 AI 模型集成

**决策：直接调用 DeepSeek API（SSE 流式返回）**

| 维度 | 直接调用 DeepSeek API (SSE) | 自建推理服务 (vLLM) |
|------|----------------------|-------------------|
| **部署成本** | 低（按量付费） | 高（GPU 服务器 ¥5k+/月） |
| **响应速度** | 快（100-300ms TTFB） | 中（受 GPU 负载影响） |
| **流式支持** | ✅ DeepSeek 原生 SSE `stream=true` | ✅ vLLM 也支持 SSE |
| **前端体验** | ✅ 逐字显示，用户感知流畅 | ✅ |
| **维护成本** | 零 | 高（模型更新、监控） |
| **隐私** | 数据经过第三方 | 数据不出服务器 |

**决策：直接调用 DeepSeek API**

理由：
- DeepSeek API 原生支持 `stream=true` 参数，后端可转发 SSE 流
- FastAPI `StreamingResponse` + `EventSource` 微信小程序支持 chunked 传输
- 流式相比等待完整响应，用户感知延迟从 `3-5s` 降为 `300ms TTFB + 逐字显示`
- MVP 阶段流量低，按量付费最经济（估计月费 < ¥100）

### 1.6 图片存储

**决策：微信云托管云存储 (CloudBase Storage)**

| 维度 | 微信云托管云存储 | 阿里云 OSS | 本地磁盘 |
|------|----------------|-----------|---------|
| **CDN 加速** | ✅ 内置 | ✅ 内置 | ❌ |
| **成本** | 低（按量计费，有免费额度） | 低（按量计费） | 最低（磁盘） |
| **微信集成** | ✅ 原生，无需额外鉴权 | ⚠️ 需配置跨域和凭证 | ⚠️ 需自建上传接口 |
| **内网访问** | ✅ 与云托管同集群 | ❌ 跨云需出网流量 | — |
| **微信 CDN** | ✅ 内置优化 | ✅ 配合 CDN | ❌ |

**决策：微信云托管云存储**

理由：
- 云托管原生集成，无需额外申请云服务账号
- 内网上传免流量费，自带 CDN 分发
- 微信小程序中访问云存储资源无需额外域名白名单配置
- 节省一个外部依赖，降低运维复杂度

### 1.7 缓存 (MVP 无 Redis)

**决策：MVP 阶段不使用 Redis，采用进程内存 + MySQL 直接查询**

| 场景 | MVP 方案 | Redis 方案（后续） |
|------|---------|-------------------|
| 微信 session_key | 每次请求调用微信 API 获取 | Redis TTL 缓存 |
| AI 对话上下文 | 从 MySQL `ai_conversation` 读最近 20 条 | Redis List 缓存 |
| 通知未读计数 | `SELECT COUNT(*) WHERE is_read=0` | Redis 原子计数 |
| 签名 URL | 每次实时生成 | Redis TTL 缓存 |

**进程内缓存**: 使用 Python `cachetools.TTLCache` 做轻量级热数据缓存（如 token blacklist、配置项）。

**注意**: Uvicorn 使用 1 worker（单进程），避免多进程缓存不一致。后续需扩 worker 时再引入 Redis。

### 1.8 异步任务 (MVP 无 Celery)

**决策：MVP 阶段通知同步写入，去掉 Celery + Redis Broker**

- 点赞、评论等操作产生的通知直接同步 INSERT 到 notification 表
- MVP 用户量低，同步写入对响应时间影响可忽略（< 5ms）
- 后续需处理大量异步任务时，再引入 Celery + Redis

**影响评估**:
- 创建评论接口：原 ~10ms → 现 ~12ms（+1 条通知 INSERT）
- 点赞接口：原 ~5ms → 现 ~7ms（+1 条通知 INSERT）
- 吞吐量：单 worker 可承载 500+ QPS，MVP 足够

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                    微信小程序客户端                       │
│  (原生开发, 7个页面, 底部Tab: AI → 发现 → 我)           │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS (云托管自带域名)
                         ▼
┌─────────────────────────────────────────────────────────┐
│              微信云托管 (WeChat CloudBase)                │
│   HTTPS 终结 + 自动 SSL + 自动扩缩容                     │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │           FastAPI 容器 (Docker)                   │    │
│  │  ┌─────────────────────────────────────────┐    │    │
│  │  │  API Layer (RESTful, 版本前缀 /api/v1)   │    │    │
│  │  │  ┌──────┐ ┌──────────┐ ┌─────────┐ ┌──┐ │    │    │
│  │  │  │ 登录 │ │ 帖子/评论 │ │ 通知    │ │AI│ │    │    │
│  │  │  └──────┘ └──────────┘ └─────────┘ └──┘ │    │    │
│  │  ├─────────────────────────────────────────┤    │    │
│  │  │  Service Layer (业务逻辑)                │    │    │
│  │  │   UserService / PostService /            │    │    │
│  │  │   CommentService / NotificationService   │    │    │
│  │  │   AIService / UploadService              │    │    │
│  │  ├─────────────────────────────────────────┤    │    │
│  │  │  Data Layer (SQLAlchemy async session)   │    │    │
│  │  └─────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────┘
         │                │
         ▼                ▼
┌──────────────────────┐  ┌──────────────────┐
│   MySQL 8.0          │  │ 微信云托管云存储  │
│   (微信云托管云数据库)  │  │  (图片/CDN)       │
└──────────────────────┘  └──────────────────┘
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
│   │   └── conversation.py   # AI 对话会话
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
5. 用户直接进入 App（信息不全不拦截，编辑资料入口在个人中心）
```

**场景 B：AI 对话流程（SSE 流式 + 工具调用）**

**B-0 初始化（进入 AI Tab 时）：**
```
1. 前端调用 POST /api/v1/ai/session
2. 后端查最近一次 active session →
   - 有 → 返回 { session_id, messages: [...] }
   - 无 → 新建 session，返回 { session_id, messages: [] }
3. 前端持有 session_id，渲染历史消息
```

**B-1 简单对话（无需工具）：**
```
1. 用户发送消息 → POST /api/v1/ai/chat { session_id, message }
2. 后端按 session_id 查 MySQL → 获取最近 20 条对话历史
3. 拼接 System Prompt + 历史 → 调 DeepSeek API (stream=true)
4. DeepSeek 直接返回 SSE 文本流
5. FastAPI 用 StreamingResponse 转发 SSE 给前端
   → event: token, data: "暖"
   → event: token, data: "气"
   → event: token, data: "费..."
6. 前端逐字追加到气泡（打字机效果）
7. 流结束后后端将完整回复存入 MySQL
```

**B-2 需要调用工具（如查询暖气费）：**
```
1. 用户："我家暖气费多少？" → POST /api/v1/ai/chat { session_id, message }
2. 后端按 session_id 查历史 + System Prompt（含 tools 定义）
3. 调 DeepSeek API (stream=true)
4. DeepSeek 返回 tool_call（非文本）:
   { role: "assistant", tool_calls: [{ name: "query_heating", args: {...} }] }
5. 后端生成 SSE 状态事件给前端:
   → event: status, data: "正在查询供暖数据..."
6. 前端气泡显示「🔍 正在查询供暖数据...」（带加载动画）
7. 后端执行 tool → 获取结果（如数据库查询）
8. 后端将 tool 结果送回 DeepSeek（第二轮调用 stream=true）
9. DeepSeek 基于结果生成自然语言回复，SSE 流式返回
   → event: token, data: "您家本月暖气费是..."
10. 前端清除状态提示，切换为打字机效果
11. 流结束后后端将完整对话（含 tool_call + result + 回复）存入 MySQL
```

**前端 SSE 事件协议：**
```
event: status     → 中间状态提示（如"正在查询..."），前端显示加载动画
event: token      → 实际 AI 回复文字，前端逐字追加
event: done       → 流结束，前端恢复输入框，后端开始存库
event: error      → 错误提示，前端显示「AI 回复被中断，请重试」
```

**场景 C：通知推送流程 (同步)**
```
1. 用户 B 点赞帖子 A → POST /api/v1/posts/:id/like
2. 创建 like 记录
3. 同步 INSERT notification 记录到数据库
4. 下次用户 A GET /api/v1/notifications → 读取未读通知
```

---

## 3. 数据模型

### 3.1 ER 图 (文本描述)

```
User 1 ──── * Post            # 用户发帖
User 1 ──── * Comment          # 用户评论
User 1 ──── * Like             # 用户点赞
User 1 ──── * Notification     # 用户接收通知
User 1 ──── * Conversation      # 用户 AI 对话会话
Conversation 1 ──── * Message    # 会话下多条消息
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
| read_at | DATETIME | NULLABLE, DEFAULT NULL | 已读时间（null=未读） |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 通知时间 |

索引：`(user_id, is_read, created_at)`（未读通知查询）
索引：`(user_id, post_id)`（标记同一帖子相关通知已读）

#### conversation

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 会话ID |
| user_id | BIGINT UNSIGNED | FK → user.id, NOT NULL | 所属用户 |
| title | VARCHAR(100) | NULLABLE, DEFAULT '' | 会话标题（自动生成或留空） |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | ON UPDATE CURRENT_TIMESTAMP | 最后活动时间 |

索引：`(user_id, updated_at DESC)`（按用户获取会话列表，最近更新的在前）

#### message

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | 消息ID |
| conversation_id | BIGINT UNSIGNED | FK → conversation.id, NOT NULL | 所属会话 |
| role | VARCHAR(20) | NOT NULL | 角色：`user` / `assistant` / `tool`（tool = AI 内部操作结果，如压缩摘要，前端不展示） |
| content | TEXT | NULLABLE | 消息内容（tool_call 消息可为空；tool 消息为操作结果） |
| tool_calls | TEXT | NULLABLE | assistant 消息的 tool_calls JSON（含 tool_call_id / 工具名 / 参数） |
| tool_call_id | VARCHAR(100) | NULLABLE | tool 消息对应的 tool_call_id（与 assistant.tool_calls 配对） |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 发送时间 |

索引：`(conversation_id, created_at)`（按会话获取消息历史）、`(user_id, conversation_id, created_at)`（历史消息跨会话分页查询，见 8.5）

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
| GET | `/posts` | 帖子列表（含评论和点赞，按时间倒序） | `?page=1&size=20` | `{ items: Post[], total, page, size }` |
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
| **POST** | `/ai/session` | 获取会话（后端决定新建或复用） | — | `{ session_id, title, messages: Message[] }` |
| **POST** | `/ai/chat` | 发送消息，SSE 流式返回 | `{ session_id, message }` | SSE 流 |

**SSE 事件协议：**
```
event: status     → 中间状态（工具调用），前端显示加载动画
event: token      → AI 回复文字块，前端逐字追加
event: done       → 流结束，前端恢复输入
event: error      → 错误提示
```

**AI 对话行为说明：**
- 用户进入 AI Tab → 前端调用 `POST /ai/session` 获取当前会话
  - 后端逻辑：查询该用户最近一条 conversation → 有则返回其消息历史，无则新建
  - 返回 `{ session_id, messages: [...] }`，前端渲染历史消息（过滤 `role='tool'`，AI 内部结果不对用户展示）
  - 会话为空（新用户首次进入）→ 前端自动发送 silent 'Hi' 让 AI 打招呼（不显示 Hi 气泡）
- 用户发消息 → 前端调用 `POST /ai/chat { session_id, message }`，SSE 接收回复
- **不存在 `/new` 命令**（2026-08-06 移除）：session 的创建、轮换、结束全部由后端自动管理（见第 8 章），前端无需理解 session 生命周期，只持有 `session_id` 显示消息流
- 消息历史按 `(conversation_id, created_at)` 排序；上下文由后端组装（见 8.3 压缩旋转）

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
| API 响应时间 (无 AI) | < 200ms P95 | 异步查询 + 数据库索引 + 进程缓存 |
| AI 对话 SSE TTFB | < 1s | DeepSeek API `stream=true` + FastAPI `StreamingResponse` 转发 |
| AI 对话完整响应 | 视内容长度 (3-10s) | 逐字显示，用户无等待感 |
| 前端 AI 状态 | 即时反馈 | 发送后气泡显示「AI 正在输入…」光标动画，收到首字后转为打字机效果 |
| 图片加载 | < 1s | 云存储 CDN + WebP 格式 + 缩略图裁剪 |
| 并发 | 支持 100 QPS | FastAPI + Uvicorn (1 worker, MVP) |
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
| 40100 | 未认证（无 token，HTTPBearer 默认文案 "Not authenticated"，#27 统一格式后） |
| 40101-40199 | 认证错误 (token 过期/无效) |
| 40301-40399 | 权限不足 |
| 40401-40499 | 资源不存在 |
| 42901-42999 | 频率限制 |
| 50001-50099 | 服务器内部错误 |

---

## 7. 部署方案

### 7.1 微信云托管配置

采用 **微信云托管 (WeChat CloudBase)** 作为应用运行环境，无需自建反向代理和 HTTPS。

| 组件 | 服务 | 说明 |
|------|------|------|
| **应用运行** | 微信云托管 (Docker 容器) | FastAPI 容器，自动扩缩容 |
| **数据库** | 微信云托管云数据库 MySQL 8.0 | 与容器同集群，免内网流量 |
| **图片存储** | 微信云托管云存储 | 内置 CDN，无需额外域名白名单 |
| **域名** | 云托管自带 `https://xxx.ap-shanghai.app.tcloudbase.com` | 配置到小程序 request 白名单 |

### 7.2 部署架构

```
微信云托管 (Docker)
├── fastapi-app              # Uvicorn 多 workers
│   ├── app/                 # FastAPI 应用代码
│   ├── Dockerfile           # 云托管构建入口
│   └── requirements.txt     # Python 依赖
└── 环境变量 (云托管自动注入):
    ├── MYSQL_URL            # 云数据库连接 (CloudBase 自动注入)
    ├── DEEPSEEK_API_KEY     # DeepSeek API Key
    └── WECHAT_APPID         # 小程序 AppID
```

### 7.3 部署方案对比

MVP 阶段评估了以下部署方案：

| 方案 | 规格 | 月费 (年付) | 优点 | 缺点 |
|------|------|-----------|------|------|
| **🥇 微信云托管 ✅** | 1C1G 容器 + 云数据库 + 云存储 | ~¥30-60 | 所有服务同集群，全托管零运维 | 无 |
| **🥈 腾讯云 Lighthouse** | 2C2G 自建 | ~¥68-100 | 性价比最高，完整控制权 | 需自建 MySQL/Redis/HTTPS |
| **阿里云 ECS + RDS** | 2C2G + RDS | ~¥130-240 | 老牌成熟平台 | 价格偏高，配置繁杂 |
| **现有服务器复用** | 2C2G (仅余225MB) | **¥0** | 零额外成本 | 内存严重不足，无法运行 MySQL+Redis+FastAPI |
| **SCF 云函数** | Serverless | ~¥0-30 | 低流量近免费 | 不适合 AI 对话（调用链长、冷启动） |

**结论**: 微信云托管作为首选方案，平衡了成本、运维便利度和微信集成体验。

### 7.4 微信云托管要点

- **Dockerfile 构建**: 云托管根据项目根目录的 Dockerfile 自动构建镜像
- **HTTPS**: 云托管自动分配域名并配置 SSL，无需手动申请证书
- **环境变量**: 通过云托管控制台配置，敏感信息不写入代码
- **日志**: 云托管内置日志采集，可在控制台查看
- **扩缩容**: 按请求量自动扩缩，MVP 阶段最低 1 实例即可
- **费率**: MVP 约 ¥30-60/月（1C1G 容器 + 云数据库 + 云存储，全托管内）

### 7.5 环境管理

| 环境 | 域名来源 | 用途 |
|------|----------|------|
| **本地开发** | `localhost:8000` | Dev 本地调试 |
| **测试环境** | 云托管测试版本域名 | Dev 自测 + Reviewer + QA |
| **生产环境** | 云托管正式版本域名 | 微信小程序正式环境 |

### 7.6 开发流程

```
1. Dev 本地开发 (本地 MySQL)
2. alembic upgrade head → 数据库迁移
3. uvicorn app.main:app --reload → 开发调试
4. Git commit + push
5. 微信云托管自动/手动构建 Docker 镜像并部署
6. QA 在测试环境验证
7. 发布正式版本 → 上线
```

### 7.7 Dockerfile (参考)

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .

EXPOSE 80
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80
```

### 7.8 依赖清单

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
cachetools>=5.5.0           # 进程内缓存 (TTLCache)
python-jose[cryptography]>=3.3.0  # JWT
python-multipart>=0.0.0
```

**微信小程序**

- 微信开发者工具 (最新稳定版)
- 基础库版本 ≥ 3.0.0
- 无需额外 npm 包（原生开发）

---

## 8. Session 管理设计

> **来源**: [issue #41](https://github.com/ai-0516/lanyuan-base/issues/41)（2026-08-06 调研与讨论，Hermes session lifecycle 借鉴）| **状态**: 已定案，待实现

### 8.1 目标与核心原则

**用户完全对 session 无感**：session 的创建、轮换、结束全部由后端自动管理；前端只持有 `session_id` 渲染连续消息流。

- **压缩是唯一允许「重建一切」的边界**：压缩意味着消息历史完全重写、无前缀缓存命中压力，此时可安全地建新 session、写摘要、抽记忆、刷新 system_prompt
- **消息扁平**：一条消息一个归属（conversation_id），不建 lineage/parent 链（决策 2，2026-08-06）
- **极简**：不预建不存在的规模（搜索 LIKE 起步、缓存无 LRU），规模机制按需升级（#42、分布式缓存）

### 8.2 会话生命周期

| 阶段 | 行为 |
|------|------|
| 获取会话 | `get_or_create`：查用户最近一条 conversation，无则新建 |
| 新用户首次进入 | 新建会话 → messages 为空 → 前端 silent 'Hi' 自动打招呼（不显示 Hi 气泡） |
| 结束会话 | **唯一路径 = 压缩旋转**（8.3）；`/new` 已移除（2026-08-06）【**待实现**：现状 main 仍保留 `/new` 路由（ai_service.py:43-58），实现 rotation 后移除】 |
| 会话复用 | 用户始终落在最新会话；旧会话结束后不再被选中 |

### 8.3 压缩旋转 (rotation)

**触发**：ai_service 每轮检查上下文超限（`SESSION_ROTATION_THRESHOLD`，默认 40K token，用该会话最近一次 LLM 调用的精确 `total_tokens` 判断——PR #49 review：不用字符估算，LLM response 自带精确 usage；total = prompt + completion，本轮生成的 assistant 回复会作为下轮 prompt 的一部分，比 prompt 更贴近「会话内容总量」）→ **仅 llm 层压缩**（真正总结）触发 rotation；snip / tool_result 轻量层原地处理，不建新会话（避免碎片化）。

**流程**（现有流程 = 先 `save_user_message` 到 A 再检查）：

```
1. 用户发 u_k → save_user_message 写入 A
2. 编排层检查超限 → 超限则压缩：
   a. 摘要输入 = A 除 u_k 外全部消息（llm 层总结，现有逻辑不变：
      完整 A 输入、尾部 20 条信息重点保留）→ 压缩产物 = 摘要 + 保留尾部
   b. 创建 B（conversation），u_k 从 A 迁移（UPDATE conversation_id，
      删 A 写 B）→ B 第一条（tail 只有一条 = 触发压缩的那条消息）
   c. assistant(tool_call: compress_context) + tool(摘要) 写入 B
      （tool_call_id 配对，role 交替合法：user → assistant → tool → assistant）
   d. emit SESSION_END(A)（u_k 迁移后 emit，保证 A 消息集 = 被压缩部分）
      → 后台异步抽取记忆（8.4）
   e. _SESSION_PROMPT_CACHE.pop(A_id)（清理死缓存，8.7）
3. LLM 处理 u_k（context = B 全量消息）→ response 写 B
4. 后续消息进 B；A 完整保留（UI 翻页可见、可搜索、原文兜底）
```

**要点**：
- **摘要 = tool 消息**（`role='tool'` + `tool_call_id`）：前端已过滤 tool 消息（`role !== 'tool'`）→ 摘要天然不展示；tool_calls 记录压缩元信息（token 数等），可追溯
- **tail = 1 条**（u_k）：语义 = 它触发了压缩与新 session 的创建；u_k 在 A、B 间只存在一份（迁移而非复制，避免 UI 混排重复渲染）
- **A 完整保留**（除 u_k 迁移）：被压缩内容原文在库，UI 历史翻页可见、搜索可命中、作为摘要的兜底
- **压缩失败安全网**：llm 压缩失败 → 不旋转（A 保持原样、缓存不删），当前轮继续用原上下文
- **LLM 感知**：LLM 会看到 tool_call + 摘要（role 交替合法性要求 + 摘要本就要进上下文）；这是正常的历史模式，无误解风险（压缩确实发生，tool_call 记录的是事实）

### 8.4 记忆抽取

- **SESSION_END 事件 + memory_extract hook 保留**（观察者模式、异步、即发即忘、hook 纯辅助）
- **触发点**：`/new`（已移除）→ **rotation 压缩边界**（8.3 步骤 d）
- 抽取输入 = A 全部消息（u_k 已迁移，天然排除）
- 异步时序：B 的 system_prompt 用抽取完成前的旧记忆，A 关键信息靠摘要（tool 消息）兜底；后台 consumer 毫秒级完成，跨轮次查询时记忆已就绪

### 8.5 UI 历史消息查询

- **方案 B**：默认显示最新消息 + 用户下拉加载历史（用户倾向，2026-08-06）
- **API**：`GET /messages?before_id=X&limit=20` 游标分页（message id 游标，created_at 可能撞秒），跨 conversation 按时间直查 message 表（`WHERE user_id=? AND id<? ORDER BY id DESC`），前端完全不感知 session 边界
- 前端渲染过滤 `role='tool'`；压缩边界处用户看到的是连续消息流（tail 在新会话、被压缩内容在旧会话，按时间衔接）

### 8.6 Agent 搜索历史 (search_history)

**工具签名**（决策 4，2026-08-06；Hermes session_search 简化版，参考 issue #41）：

```
search_history(query, limit=3, window=5, sort=relevance|newest|oldest)
→ top N 命中消息（role 限 user/assistant，tool 不搜）
→ 每条带 ±window 上下文窗口（可含 tool 消息，每条 4000 字符截断）
→ 命中带 snippet + timestamp + messages_before/after
```

- **过滤**：`user_id` 归属过滤 + **排除当前 conversation**（其内容 agent 上下文已有；被压缩的旧 A 天然可搜——搜索的主要目标）
- **多关键词**：空格拆词，**任一命中即返回（OR）**（PR #51 review：AND 容易什么都搜不到）
- **窗口合并**（PR #51 review）：同一会话内相邻命中（id 差 ≤ 2×window）的 ±window 窗口重叠 → 合并为一个连续片段（边界 = [首条 hit − window, 末条 hit + window]），避免同一消息在多个 result 重复；不同会话不合并。合并按会话分组 + 组内 id 排序，**不依赖 hit 输入顺序**（未来 FTS 相关度排序时 hit 无序，合并依然正确）；片段按 anchor 在 hits 中的原始位置排序，保持搜索结果顺序
- **排序**：默认 FTS 相关度（探索式回忆），`sort=newest/oldest` 可选（recency 场景）
- **实现路径**：LIKE `%kw%` 起步（单用户量级可控）→ MySQL FULLTEXT + ngram parser 升级为 [followup issue #42](https://github.com/ai-0516/lanyuan-base/issues/42)
- **注册**：tool_registry 注册，agent 主动调用；与 UI 历史查询（8.5）完全解耦
- **免费红利**：摘要 = tool 消息 → 默认不搜（role 过滤），无需前缀 hack（Hermes 用 `[CONTEXT SUMMARY]` 排除）

### 8.7 System Prompt 缓存

- **session 粒度冻结缓存**：key = session_id，session 内首次组装后冻结（memory_index 变化不使缓存失效）→ system 字节稳定 → 前缀缓存命中 → token 成本（核心设计，2026-08-05 定）
- **普通 dict，无 LRU / 无 maxsize**（2026-08-06 决策）：LRU 128 是多用户下颠簸源头（缓存条目 = 历史 session 总数）；正解 = rotation 时 `pop(A_id)` 精确清理死数据 → 条目 ≈ 活跃 session 数（业务有界）【**待实现**：现状 main 仍是 OrderedDict + LRU 128（context.py:98,134-135），实现 rotation 后改为普通 dict + pop(A_id)】
- **压缩边界自动刷新**：B 是新 session_id → 缓存 miss → 首次组装自动用最新 memory_index（「压缩时更新 system_prompt」的诉求天然满足，无新增机制）

### 8.8 数据模型影响

- **零 schema 改动**：Message 已有 `role='tool'`（VARCHAR）、`tool_calls`、`tool_call_id`；conversation 不加字段（不建 lineage）
- **一个 UPDATE 操作**：tail 迁移（u_k 改 conversation_id），`Message.tool_call_id` 为字符串无外键，迁移安全
- 新增索引：`(user_id, conversation_id, created_at)`（历史消息跨会话分页，见 3.2 message 表）

### 8.9 待实现确认项

- [x] 压缩触发阈值（token 上限 / 上下文占比，实现时定）→ 已定：`SESSION_ROTATION_THRESHOLD=40K`，用 llm_usage 精确 total_tokens 判断（PR #49 review，见 8.3；2026-08-07 由 COMPACT_TOKEN_THRESHOLD 改名）
- [ ] 前端 tool_call 消息（content 为空）渲染跳过逻辑确认（tool 消息已过滤，tool_call 需确认）
- [ ] 搜索 LIKE 查询的上下文窗口实现（命中消息 ±N 条取法）
- [ ] `GET /messages` 分页 API 设计细化（响应结构、limit 上限）
- [ ] **单条巨型消息不触发 rotation**（PR #49 观察 1）：超限判定含 u_k 但 summary_input 排除 u_k——新会话首条超长消息永久留在当前会话（无历史可总结），每轮超限都不旋转。可接受（设计意图），若未来出现单条 >60K 消息需评估
- [ ] **旧 A 永不拒绝**（PR #49 观察 2）：rotation 后旧 A 仍能 verify_ownership 通过（仍属用户），但 get_or_create 永远选最新 B，无实际影响。若需防旧引用无限复用，未来加「已 rotation 会话标记」

---

> **审批**: 以上技术方案请在 kanban 中回复「没有问题」确认，我将创建 Dev 开发任务。
