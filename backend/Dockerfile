# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 nexus
USER nexus

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=2).status == 200 else 1)"

# Apply migrations, then start the API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.asgi:app --host 0.0.0.0 --port 8000 --proxy-headers"]
