# Single backend image — run as the API (default CMD) or the worker
# (`python -m backend.worker`) or one-shot migrations
# (`alembic -c config/alembic.ini upgrade head`). One image, many containers.
FROM python:3.12-slim

WORKDIR /app

# curl is used by the compose healthcheck against /health.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 1) Dependencies only (cached layer) — project not installed yet.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# 2) Source + config, then install the project itself.
COPY backend/ ./backend/
COPY config/ ./config/
RUN uv sync --no-dev --frozen

# 3) spaCy language model for Presidio PII detection.
RUN uv run python -m spacy download en_core_web_sm

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
