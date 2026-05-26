# Two-stage uv build. Same pattern as salmon /
# townsfolk / brian / dispatch.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-default-groups

COPY src ./src
RUN uv sync --frozen --no-default-groups


FROM python:3.12-slim AS runtime

# ca-certificates so outbound HTTPS to oembed
# providers + target URLs works. No other system
# deps required; the service is pure-python.
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 embed \
 && useradd --system --uid 1000 --gid 1000 \
        --home /app --shell /sbin/nologin embed

WORKDIR /app
COPY --from=builder --chown=embed:embed /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER embed
EXPOSE 8000

CMD ["uvicorn", "embed.main:app", "--host", "0.0.0.0", "--port", "8000"]
