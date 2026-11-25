# ============================================================
# Production Dockerfile — multi-stage
#   builder : tạo virtualenv và cài dependencies (pip cache, wheel build ở lại stage này)
#   runtime : python slim + virtualenv + code, chạy bằng user non-root
# Build:  docker build -t day14-agent .
# Run:    docker run -p 8000:8000 -e AGENT_API_KEY=... -e REDIS_URL=... day14-agent
# ============================================================

# ── Stage 1: builder ────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# requirements trước → layer cài deps được cache khi chỉ sửa code
COPY requirements.txt .
RUN pip install -r requirements.txt


# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=3)" || exit 1

# `exec` thay shell bằng uvicorn → uvicorn là PID 1, nhận SIGTERM trực tiếp khi `docker stop`.
# PORT do platform (Railway/Render) cấp, mặc định 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8 --no-server-header"]
