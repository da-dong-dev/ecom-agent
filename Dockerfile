# ============================================================
# ecom-agent 后端镜像
#
# 构建：
#   docker build -t ecom-agent-backend .
#
# 说明：
#   - 用 uv 按 uv.lock 安装依赖，保证可复现；
#   - 不把 .env 打进镜像，运行时由 compose 注入；
#   - data/ 不 COPY，运行时挂卷（SQLite + KB 源文件）；
#   - scripts/ 需要进镜像，初始化 / seed / 灌库要用。
# ============================================================

FROM python:3.12-slim

# 健康检查用 curl
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv：按 lock 文件安装，快且可复现
RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY scripts ./scripts

# 非 root 运行；data 目录由 compose 挂载覆盖
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8091/api/health || exit 1

EXPOSE 8091
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
