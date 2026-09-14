# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Install uv (fast, reproducible dependency management)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first (layer cache) using only project metadata.
# The `webull` extra is included so SANDBOX/REAL modes have the official SDK available.
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-install-project --no-dev --extra webull \
    || uv sync --no-dev --extra webull

# Now copy the source and install the project itself.
COPY README.md ./README.md
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY claude ./claude
RUN uv sync --no-dev --extra webull

# Non-root runtime user
RUN useradd --create-home --uid 10001 apm && chown -R apm:apm /app
USER apm

ENV PATH="/app/.venv/bin:${PATH}"

# Health endpoint
EXPOSE 8080

# Long-lived background process: migrations -> reconcile -> event loop.
ENTRYPOINT ["python", "-m", "apm.orchestrator.main"]
