FROM python:3.12-slim

WORKDIR /app

# 依赖声明单源 = pyproject.toml + uv.lock（与 CI/开发统一：uv sync）
# --frozen 按锁文件安装（校验 lock 与 pyproject 一致），--no-dev 生产不装测试依赖，
# --no-install-project 跳过项目包（app/ 由 uvicorn 从 cwd 加载，不装进 site-packages）
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 80
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80
