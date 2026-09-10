FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY mcp_servers /app/mcp_servers
COPY guardrails /app/guardrails
COPY scripts/setup_backend_auth.py /app/scripts/setup_backend_auth.py

EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/python", "-m", "mcp_servers.server"]
