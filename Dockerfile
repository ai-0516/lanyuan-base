# ══════════════════════════════════════════════════════════════════════
# lanyuan v2 生产镜像（TECH_SPEC §11：微信云托管）
#
# 双运行时：Node（DSH runtime 子进程）+ Python（FastAPI + MCP server）
# 镜像内容 = dsh/ 家目录（pnpm 全量 node_modules + 本地插件 tsc 产物）+ backend
#
# 镜像大小预估 ~1.2G 量级（§11.2 实验 5：node_modules ~341M + venv ~328M +
# 基础镜像）；云托管镜像限制实测见 §14 待确认项 1（M4 实测）。
#
# 构建（本机无 Docker 时在云托管侧构建，或 CI）：
#   docker build -t lanyuan-v2 .
# 运行：
#   docker run -p 80:80 \
#     -e DEEPSEEK_API_KEY=... \
#     -e DATABASE_URL=mysql+aiomysql://user:pass@host:3306/lanyuan \
#     lanyuan-v2
# ══════════════════════════════════════════════════════════════════════

# ── 阶段 1：dsh/ 家目录构建（pnpm install 全量 + 本地插件 tsc 编译） ──
FROM node:20-slim AS dsh-builder
WORKDIR /build

# 先 COPY 清单文件（利用 layer 缓存：依赖不变时跳过 install）
COPY dsh/package.json dsh/pnpm-lock.yaml dsh/pnpm-workspace.yaml ./
COPY dsh/spine ./spine
COPY dsh/mcp-client ./mcp-client
COPY dsh/mysql-persistence ./mysql-persistence
COPY dsh/server ./server
COPY dsh/bin ./bin

# corepack 启用 pnpm（node:20-slim 自带 corepack）；--frozen-lockfile 按锁文件
# 安装（与开发/CI 单一真源，lock 必须入库——否则部署必炸）
# file: 本地插件 install 时自动跑 prepare（tsc → lib/），再显式 build 兜底
# （幂等；prepare 行为差异保险）
RUN corepack enable \
    && pnpm install --frozen-lockfile \
    && pnpm run build

# ── 阶段 2：运行时（Python FastAPI + Node DSH runtime） ──
FROM python:3.12-slim AS runtime

# Node 20 二进制（DSH runtime 是 Node 子进程；从 node 官方镜像拷，避免 apt 旧版）
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node:20-slim /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node:20-slim /usr/local/bin/npx /usr/local/bin/npx

WORKDIR /app

# ── backend 依赖（依赖声明单源 = pyproject.toml + uv.lock，同 CI/开发 uv sync） ──
COPY backend/pyproject.toml backend/uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

# ── dsh/ 家目录（pnpm 产物 + 本地插件构建产物；删除即卸载 DSH） ──
COPY --from=dsh-builder /build/ ./dsh/

# ── backend 代码（app/ + alembic migration + MCP server tools/ + 入口） ──
COPY backend/app ./app/
COPY backend/alembic ./alembic/
COPY backend/alembic.ini .
# tools/mcp_server 在 backend/tools/（main.py import tools.mcp_server.main）
COPY backend/tools ./tools/

ENV PATH="/app/.venv/bin:$PATH"

# 微信云托管端口约定（容器默认监听 80，平台转发）
EXPOSE 80

# 启动：alembic 迁移（v2 会话三表 §8.2）→ uvicorn（workers=1 起步，§3.1；
# lifespan 拉起 DSH runtime + MCP server）
# 必需 env（云托管注入）：DEEPSEEK_API_KEY / DATABASE_URL（MySQL）
# 可选 env：LANYUAN_MCP_TOKEN（未配置进程内自动生成同值）、
#   LANYUAN_MCP_URL（默认 http://127.0.0.1:8000/mcp/，与部署端口绑定）、
#   V2_LLM_MODEL（默认 deepseek-v4-flash）
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80 --workers 1
