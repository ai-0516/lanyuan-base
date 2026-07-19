# 兰园公共底座

> 微信小程序公共底座 — 为「兰园地暖」「兰园停车」等业务小程序提供开箱即用的登录、社区社交和 AI 能力。

[![Tests](https://img.shields.io/badge/tests-95%20passed-brightgreen)](./backend/tests/)
[![Coverage](https://img.shields.io/badge/coverage-87%25-yellow)](./backend/)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)

---

## 项目概述

**兰园**不是独立的社区 App，而是一个**可扩展的基础框架**：

- 底座提供 3 个 Tab：**AI → 发现 → 我**
- 业务小程序（地暖、停车等）在此基础上**添加 Tab** 扩展功能
- 底座 + 业务 = 一个完整的微信小程序，用户无感知切换

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | 微信原生小程序 | 零额外依赖，包体最小 |
| 后端 | FastAPI + Uvicorn | 原生 async，SSE 流式 |
| ORM | SQLAlchemy 2.0 | async session + Alembic 迁移 |
| 数据库 | MySQL 8.0（生产）/ SQLite（开发） | 微信云托管云数据库 |
| AI | DeepSeek API | SSE 流式 + 工具调用 |
| 认证 | JWT (HS256) | 7 天有效期 |

---

## 项目结构

```
lanyuan-base/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py             # 应用入口
│   │   ├── config.py           # 环境配置
│   │   ├── api/v1/             # REST API 路由（20 个端点）
│   │   │   ├── auth.py         #   微信登录
│   │   │   ├── posts.py        #   帖子 CRUD + 点赞
│   │   │   ├── comments.py     #   评论（两级）
│   │   │   ├── notifications.py#   消息通知
│   │   │   ├── profile.py      #   个人中心
│   │   │   ├── ai.py           #   AI 对话（SSE）
│   │   │   └── upload.py       #   图片上传
│   │   ├── models/             # SQLAlchemy 数据模型（7 表）
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── services/           # 业务逻辑层
│   │   └── core/               # JWT / 数据库 / 微信客户端
│   ├── alembic/                # 数据库迁移
│   ├── tests/                  # 自动化测试（95 条）
│   ├── seed_data.py            # 种子数据脚本
│   ├── Dockerfile              # 微信云托管部署
│   └── requirements.txt        # Python 依赖
├── miniprogram/                # 微信小程序（54 文件）
│   ├── app.js / .json / .wxss  # 全局入口
│   ├── pages/                  # 7 个页面
│   │   ├── login/              #   微信登录
│   │   ├── feed/               #   发现页（帖子流）
│   │   ├── create-post/        #   发布帖子
│   │   ├── ai-chat/            #   AI 对话
│   │   ├── profile/            #   个人中心
│   │   ├── edit-profile/       #   编辑资料
│   │   └── notifications/      #   消息通知
│   ├── components/             # 6 个通用组件
│   │   ├── post-card/          #   帖子卡片
│   │   ├── comment-sheet/      #   评论弹出层
│   │   ├── like-button/        #   点赞按钮
│   │   ├── image-grid/         #   图片网格
│   │   ├── chat-bubble/        #   聊天气泡
│   │   └── lightbox/           #   图片全屏预览
│   └── utils/                  # request / auth / date / constants
├── frontend/                   # Web 原型（开发参考）
│   └── index.html
└── docs/
    ├── PRD.md                  # 产品需求文档
    ├── TECH_SPEC.md            # 技术方案
    └── design/                 # 设计稿（9 个 HTML）
```

---

## 快速开始

### 1. 后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate

# 安装依赖
uv pip install -r requirements.txt

# 配置环境变量
cp .env.example .env  # 编辑 .env 填入 DEEPSEEK_API_KEY 等

# 数据库迁移
alembic upgrade head

# 种子数据（可选）
python3 seed_data.py

# 启动服务
uvicorn app.main:app --reload --port 8000
```

服务启动后访问 http://localhost:8000/docs 查看 Swagger API 文档。

### 2. 微信小程序

1. 安装[微信开发者工具](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)
2. 打开 `miniprogram/` 目录
3. 修改 `utils/constants.js` 中的 `BASE_URL` 指向后端地址
4. 在微信公众平台配置 `request` 合法域名

### 3. 运行测试

```bash
cd backend

# 全部测试 + 覆盖率
.venv/bin/python3 -m pytest tests/ --cov=app --cov-report=html

# 仅 E2E
.venv/bin/python3 -m pytest tests/test_e2e.py -v

# 仅 service 层
.venv/bin/python3 -m pytest tests/test_services.py -v
```

---

## API 概览

| 模块 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 认证 | POST | `/auth/login` | 微信登录 |
| | GET | `/auth/check` | Token 校验 |
| 用户 | GET | `/user/me` | 当前用户信息 |
| | PUT | `/user/me` | 更新资料 |
| | GET | `/users/{id}` | 公开资料 |
| | POST | `/user/logout` | 退出登录 |
| 帖子 | GET | `/posts` | 帖子列表（分页 + 评论 + 点赞） |
| | POST | `/posts` | 发布帖子 |
| | DELETE | `/posts/{id}` | 删除帖子 |
| | POST | `/posts/{id}/like` | 点赞/取消 |
| 评论 | GET | `/posts/{id}/comments` | 帖子评论 |
| | POST | `/posts/{id}/comments` | 添加评论（支持回复） |
| | DELETE | `/comments/{id}` | 删除评论 |
| 通知 | GET | `/notifications` | 未读通知 |
| | GET | `/notifications/count` | 未读数量 |
| | POST | `/notifications/read` | 标记已读 |
| AI | POST | `/ai/session` | 获取/创建会话 |
| | POST | `/ai/chat` | SSE 流式对话 |
| 上传 | POST | `/upload/images` | 图片上传 |

**响应格式**: `{ code: 0, data: {...}, message: "ok" }`

---

## 设计系统

陶土暖色系 — 温暖、自然、社区的视觉感受。

| 变量 | 色值 | 用途 |
|------|------|------|
| `--cream` | `#faf7f2` | 页面背景 |
| `--warm-white` | `#fffaf5` | 卡片背景 |
| `--terracotta` | `#c4673c` | 主色（按钮、高亮） |
| `--clay` | `#b8532e` | 渐变起点 |
| `--ember` | `#d4744b` | 渐变终点 |
| `--bark` | `#3d2b1f` | 文字颜色 |
| `--sand` | `#f2e8dc` | 分割线 |

---

## 测试

| 层级 | 用例数 | 覆盖 |
|------|:---:|:---:|
| E2E（API 集成） | 49 | Auth / Posts / Comments / Likes / Notifications / Profile / AI / Upload |
| Service 单元 | 29 | post_service 100% / comment_service 100% |
| 基础 API | 17 | 核心端点 |
| **总计** | **95** | **87%**（核心业务 100%） |

---

## 部署

支持**微信云托管**（Docker 容器 + 云数据库 MySQL + 云存储）：

```bash
cd backend
docker build -t lanyuan-base .
```

Dockerfile 已内置 `alembic upgrade head`，容器启动自动完成数据库迁移。

---

## 文档

- [PRD — 产品需求文档](./docs/PRD.md)
- [TECH_SPEC — 技术方案](./docs/TECH_SPEC.md)
- [设计稿](./docs/design/) — 9 个 HTML/CSS 原型
- [API 文档](http://localhost:8000/docs) — 启动后端后访问 Swagger
