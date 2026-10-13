# One image for the API and (later) the worker; the container command selects the entrypoint.
# Base images are pinned to exact versions on purpose; upgrade them deliberately.

FROM python:3.13.16-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so this layer is cached until pyproject.toml / uv.lock change.
# README.md is required because pyproject.toml declares it as the project readme.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.13.16-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /app/.venv /app/.venv

# Mount point for the uploads volume (a new named volume inherits this owner).
RUN mkdir -p /data/uploads && chown -R app:app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
WORKDIR /app
EXPOSE 8000

# Default command runs the API. Configuration comes from environment variables at runtime;
# no .env file or secret is ever copied into the image (see .dockerignore).
CMD ["uvicorn", "docusearch.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
