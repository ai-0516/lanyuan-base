FROM python:3.12-slim

WORKDIR /app
# 依赖声明单源 = pyproject.toml（PEP 621），Docker/CI/开发统一
COPY pyproject.toml .
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
RUN pip install --no-cache-dir .

EXPOSE 80
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80
