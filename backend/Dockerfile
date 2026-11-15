# syntax=docker/dockerfile:1.7
#
# One image, two process types: the API and the worker differ only by command
# (ADR-001). Multi-stage so the runtime carries no build tooling.

# ---------- builder ----------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies install in their own layer, keyed on the lockfile alone, so a
# source change does not invalidate the dependency cache. --frozen means the
# build fails rather than silently resolving different versions than CI.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime ----------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# curl is used by the compose healthcheck; nothing else is added.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 agentflow

WORKDIR /app
COPY --from=builder --chown=agentflow:agentflow /app /app

# Unprivileged by default: nothing here needs root.
USER agentflow

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
